"""Rotas: API JSON do CRM (/api/v1/crm)."""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from robyn import Request, Response

from fincore.application.crm import (
    complete_activity,
    convert_lead,
    create_activity,
    create_deal,
    create_lead,
    ensure_default_stages,
    lose_deal,
    move_deal,
    win_deal,
)
from fincore.application.crm_proposals import (
    accept_proposal,
    create_proposal,
    proposal_titles,
    reject_proposal,
    send_proposal,
)
from fincore.application.queries import (
    activities_query,
    crm_stages,
    crm_summary,
    deals_query,
    get_deal,
    get_lead,
    get_proposal,
    leads_query,
    proposal_items,
    proposal_totals,
    proposals_query,
)
from fincore.application.titles import title_balances
from fincore.domain.crm import proposal_is_expired
from fincore.domain.money import MAX_AMOUNT, parse_api_amount
from fincore.infrastructure.models import (
    CrmActivity,
    CrmDeal,
    CrmLead,
    CrmPipelineStage,
    CrmProposal,
)
from fincore.presentation.routes.api import (
    error_response,
    json_payload,
    json_response,
    pagination,
    require_csrf,
)
from fincore.presentation.routes.api_titles import title_json
from fincore.presentation.web import CurrentUser, api_login_required

_DECIMAL = re.compile(r"^\d+(\.\d+)?$")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _id(value) -> str | None:
    return str(value) if value else None


def stage_json(stage: CrmPipelineStage) -> dict[str, object]:
    return {
        "id": str(stage.id),
        "name": stage.name,
        "position": stage.position,
        "win_probability": stage.win_probability,
        "is_active": stage.is_active,
    }


def lead_json(lead: CrmLead) -> dict[str, object]:
    return {
        "id": str(lead.id),
        "name": lead.name,
        "company_name": lead.company_name,
        "email": lead.email,
        "phone": lead.phone,
        "source": lead.source,
        "status": lead.status,
        "estimated_value": str(lead.estimated_value) if lead.estimated_value is not None else None,
        "currency": lead.currency,
        "qualification": {
            "need": lead.has_need,
            "budget": lead.has_budget,
            "authority": lead.has_authority,
            "timeline": lead.has_timeline,
        },
        "converted_counterparty_id": _id(lead.converted_counterparty_id),
        "converted_deal_id": _id(lead.converted_deal_id),
        "converted_at": _iso(lead.converted_at),
        "created_at": _iso(lead.created_at),
    }


def deal_json(deal: CrmDeal) -> dict[str, object]:
    return {
        "id": str(deal.id),
        "title": deal.title,
        "status": deal.status,
        "amount": str(deal.amount),
        "currency": deal.currency,
        "counterparty_id": str(deal.counterparty_id),
        "stage_id": str(deal.stage_id),
        "expected_close_date": _iso(deal.expected_close_date),
        "lost_reason": deal.lost_reason,
        "stage_changed_at": _iso(deal.stage_changed_at),
        "closed_at": _iso(deal.closed_at),
        "created_at": _iso(deal.created_at),
    }


def activity_json(item: CrmActivity) -> dict[str, object]:
    return {
        "id": str(item.id),
        "type": item.activity_type,
        "subject": item.subject,
        "due_date": _iso(item.due_date),
        "done_at": _iso(item.done_at),
        "lead_id": _id(item.lead_id),
        "deal_id": _id(item.deal_id),
        "counterparty_id": _id(item.counterparty_id),
        "notes": item.notes,
    }


def proposal_json(proposal: CrmProposal, total: Decimal) -> dict[str, object]:
    return {
        "id": str(proposal.id),
        "number": proposal.number,
        "deal_id": str(proposal.deal_id),
        "status": proposal.status,
        "expired": proposal_is_expired(proposal.status, proposal.valid_until, date.today()),
        "issue_date": proposal.issue_date.isoformat(),
        "valid_until": proposal.valid_until.isoformat(),
        "discount": str(proposal.discount),
        "total": str(total),
        "currency": proposal.currency,
        "first_title_id": _id(proposal.first_title_id),
        "decided_at": _iso(proposal.decided_at),
    }


def _text(payload: dict[str, object], key: str, required: bool = True) -> str:
    value = payload.get(key, "" if not required else None)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"Informe {key} como texto.")
    return value


def _uuid(payload: dict[str, object], key: str, required: bool = True) -> UUID | None:
    if not required and not payload.get(key):
        return None
    try:
        return UUID(str(payload.get(key, "")))
    except ValueError:
        raise ValueError(f"{key} deve ser um UUID válido.") from None


def _date(payload: dict[str, object], key: str, required: bool = True) -> date | None:
    if not required and not payload.get(key):
        return None
    try:
        return date.fromisoformat(_text(payload, key))
    except ValueError:
        raise ValueError(f"{key} deve ser uma data AAAA-MM-DD.") from None


def _amount(payload: dict[str, object], key: str) -> Decimal | None:
    """Valor opcional, zero permitido, como string decimal canônica."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _DECIMAL.match(value.strip()):
        raise ValueError(f'{key} deve ser string decimal, por exemplo "250.00".')
    # Compara antes de arredondar: quantize() de um número enorme lança InvalidOperation.
    amount = Decimal(value.strip())
    if amount > MAX_AMOUNT:
        raise ValueError(f"{key} está fora do limite permitido.")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _bool(payload: dict[str, object], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} deve ser true ou false.")
    return value


def _items(payload: dict[str, object]) -> list[tuple[str, Decimal, Decimal]]:
    raw = payload.get("items")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Informe items como lista com ao menos um item.")
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Cada item deve ser um objeto.")
        quantity = entry.get("quantity", "1")
        if not isinstance(quantity, str) or not _DECIMAL.match(quantity.strip()):
            raise ValueError('quantity deve ser string decimal, por exemplo "2" ou "1.5".')
        items.append(
            (
                _text(entry, "description"),
                Decimal(quantity.strip()),
                parse_api_amount(_text(entry, "unit_price")),
            )
        )
    return items


def _error(exc: ValueError) -> Response:
    message = str(exc)
    if "não encontrad" in message:
        return error_response("not_found", message, 404)
    if "já foi usada" in message:
        return error_response("idempotency_conflict", message, 409)
    return error_response("invalid_crm", message, 400)


def _path_id(request: Request, key: str) -> UUID | None:
    try:
        return UUID(request.path_params[key])
    except KeyError, ValueError:
        return None


def register_crm_api_routes(app: object) -> None:
    def page(request: Request) -> tuple[int, int] | None:
        try:
            return pagination(request)
        except ValueError:
            return None

    async def listing(request: Request, query, serialize) -> Response:
        bounds = page(request)
        if bounds is None:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        limit, offset = bounds
        total = await query.count()
        rows = list(await query.offset(offset).limit(limit))
        return json_response(
            {"items": await serialize(rows), "limit": limit, "offset": offset, "total": total}
        )

    def plain(serializer):
        async def serialize(rows):
            return [serializer(row) for row in rows]

        return serialize

    @app.get("/api/v1/crm/summary")
    @api_login_required
    async def api_crm_summary(request: Request, current: CurrentUser) -> Response:
        summary = await crm_summary(await current[1].organization)

        def number(value: Decimal | None) -> str | None:
            return str(value) if value is not None else None

        return json_response(
            {
                "leads": {
                    "total": summary.leads_total,
                    "by_status": summary.leads_by_status,
                    "conversion_rate": number(summary.lead_conversion),
                },
                "deals": {
                    "open_count": summary.open_count,
                    "open_total": str(summary.open_total),
                    "weighted_forecast": str(summary.forecast),
                    "won_count": summary.won_count,
                    "won_total": str(summary.won_total),
                    "lost_count": summary.lost_count,
                    "win_rate": number(summary.win_rate),
                    "average_cycle_days": number(summary.average_cycle_days),
                    "stalled_count": summary.stalled_count,
                },
                "stages": [
                    {
                        **stage_json(item.stage),
                        "open_count": item.count,
                        "open_total": str(item.total),
                        "weighted": str(item.weighted),
                    }
                    for item in summary.stages
                ],
                "activities": {
                    "pending": summary.pending_activities,
                    "overdue": summary.overdue_activities,
                },
                "proposals_waiting": summary.proposals_waiting,
                "currency": "BRL",
            }
        )

    @app.get("/api/v1/crm/stages")
    @api_login_required
    async def api_crm_stages(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        await ensure_default_stages(organization)
        return json_response(
            {"items": [stage_json(item) for item in await crm_stages(organization)]}
        )

    # --- Leads ------------------------------------------------------------------------

    @app.get("/api/v1/crm/leads")
    @api_login_required
    async def api_crm_leads(request: Request, current: CurrentUser) -> Response:
        query = leads_query(
            await current[1].organization, status=request.query_params.get("status", None)
        )
        return await listing(request, query, plain(lead_json))

    @app.post("/api/v1/crm/leads")
    @api_login_required
    async def api_crm_create_lead(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            lead = await create_lead(
                organization=await current[1].organization,
                user=current[0],
                operation_key=_uuid(payload, "operation_key"),
                name=_text(payload, "name"),
                company_name=_text(payload, "company_name", required=False),
                email=_text(payload, "email", required=False),
                phone=_text(payload, "phone", required=False),
                source=_text(payload, "source", required=False) or "other",
                estimated_value=_amount(payload, "estimated_value"),
                notes=_text(payload, "notes", required=False),
            )
        except ValueError as exc:
            return _error(exc)
        return json_response(lead_json(lead), 201)

    @app.get("/api/v1/crm/leads/:lead_id")
    @api_login_required
    async def api_crm_lead(request: Request, current: CurrentUser) -> Response:
        lead_id = _path_id(request, "lead_id")
        lead = await get_lead(await current[1].organization, lead_id) if lead_id else None
        if lead is None:
            return error_response("not_found", "Lead não encontrado.", 404)
        return json_response(lead_json(lead))

    @app.post("/api/v1/crm/leads/:lead_id/convert")
    @api_login_required
    async def api_crm_convert_lead(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        organization = await current[1].organization
        lead_id = _path_id(request, "lead_id")
        lead = await get_lead(organization, lead_id) if lead_id else None
        if lead is None:
            return error_response("not_found", "Lead não encontrado.", 404)
        try:
            payload = json_payload(request)
            lead, contact, deal = await convert_lead(
                organization=organization,
                user=current[0],
                lead_id=lead.id,
                counterparty_id=_uuid(payload, "counterparty_id", required=False),
                person_type=_text(payload, "person_type", required=False) or "individual",
                tax_id=_text(payload, "tax_id", required=False),
                create_deal=_bool(payload, "create_deal", True),
                deal_title=_text(payload, "deal_title", required=False),
                deal_amount=_amount(payload, "deal_amount"),
                stage_id=_uuid(payload, "stage_id", required=False),
                expected_close_date=_date(payload, "expected_close_date", required=False),
            )
        except ValueError as exc:
            if "já foi convertido" in str(exc) or "parecido" in str(exc):
                return error_response("conflict", str(exc), 409)
            return _error(exc)
        return json_response(
            {
                "lead": lead_json(lead),
                "counterparty_id": str(contact.id),
                "deal": deal_json(deal) if deal else None,
            },
            201,
        )

    # --- Negociações ------------------------------------------------------------------

    @app.get("/api/v1/crm/deals")
    @api_login_required
    async def api_crm_deals(request: Request, current: CurrentUser) -> Response:
        query = deals_query(
            await current[1].organization, status=request.query_params.get("status", None)
        )
        return await listing(request, query, plain(deal_json))

    @app.post("/api/v1/crm/deals")
    @api_login_required
    async def api_crm_create_deal(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            deal = await create_deal(
                organization=await current[1].organization,
                user=current[0],
                operation_key=_uuid(payload, "operation_key"),
                counterparty_id=_uuid(payload, "counterparty_id"),
                title=_text(payload, "title"),
                amount=_amount(payload, "amount") or Decimal("0.00"),
                stage_id=_uuid(payload, "stage_id", required=False),
                expected_close_date=_date(payload, "expected_close_date", required=False),
                notes=_text(payload, "notes", required=False),
            )
        except ValueError as exc:
            return _error(exc)
        return json_response(deal_json(deal), 201)

    async def owned_deal(request: Request, current: CurrentUser) -> CrmDeal | None:
        deal_id = _path_id(request, "deal_id")
        return await get_deal(await current[1].organization, deal_id) if deal_id else None

    @app.get("/api/v1/crm/deals/:deal_id")
    @api_login_required
    async def api_crm_deal(request: Request, current: CurrentUser) -> Response:
        deal = await owned_deal(request, current)
        if deal is None:
            return error_response("not_found", "Negociação não encontrada.", 404)
        return json_response(deal_json(deal))

    async def deal_write(request: Request, current: CurrentUser, action) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        deal = await owned_deal(request, current)
        if deal is None:
            return error_response("not_found", "Negociação não encontrada.", 404)
        try:
            deal = await action(await current[1].organization, deal, json_payload(request))
        except ValueError as exc:
            if "em aberto" in str(exc):
                return error_response("invalid_state", str(exc), 409)
            return _error(exc)
        return json_response(deal_json(deal))

    @app.post("/api/v1/crm/deals/:deal_id/stage")
    @api_login_required
    async def api_crm_move_deal(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, payload):
            return await move_deal(
                organization=organization,
                user=current[0],
                deal_id=deal.id,
                stage_id=_uuid(payload, "stage_id"),
            )

        return await deal_write(request, current, action)

    @app.post("/api/v1/crm/deals/:deal_id/win")
    @api_login_required
    async def api_crm_win_deal(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, payload):
            return await win_deal(organization=organization, user=current[0], deal_id=deal.id)

        return await deal_write(request, current, action)

    @app.post("/api/v1/crm/deals/:deal_id/lose")
    @api_login_required
    async def api_crm_lose_deal(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, payload):
            return await lose_deal(
                organization=organization,
                user=current[0],
                deal_id=deal.id,
                reason=_text(payload, "lost_reason"),
            )

        return await deal_write(request, current, action)

    # --- Atividades -------------------------------------------------------------------

    @app.get("/api/v1/crm/activities")
    @api_login_required
    async def api_crm_activities(request: Request, current: CurrentUser) -> Response:
        status = request.query_params.get("status", "pending")
        query = activities_query(
            await current[1].organization,
            status=status if status in {"pending", "done", "all"} else "pending",
        )
        return await listing(request, query, plain(activity_json))

    @app.post("/api/v1/crm/activities")
    @api_login_required
    async def api_crm_create_activity(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            activity = await create_activity(
                organization=await current[1].organization,
                user=current[0],
                activity_type=_text(payload, "type"),
                subject=_text(payload, "subject"),
                due_date=_date(payload, "due_date", required=False),
                notes=_text(payload, "notes", required=False),
                lead_id=_uuid(payload, "lead_id", required=False),
                deal_id=_uuid(payload, "deal_id", required=False),
                counterparty_id=_uuid(payload, "counterparty_id", required=False),
                done=_bool(payload, "done", False),
            )
        except ValueError as exc:
            return _error(exc)
        return json_response(activity_json(activity), 201)

    @app.post("/api/v1/crm/activities/:activity_id/complete")
    @api_login_required
    async def api_crm_complete_activity(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        activity_id = _path_id(request, "activity_id")
        try:
            if activity_id is None:
                raise ValueError("Atividade não encontrada nesta organização.")
            activity = await complete_activity(
                organization=await current[1].organization,
                user=current[0],
                activity_id=activity_id,
            )
        except ValueError as exc:
            return _error(exc)
        return json_response(activity_json(activity))

    # --- Propostas --------------------------------------------------------------------

    async def proposals_serializer(rows: list[CrmProposal]) -> list[dict[str, object]]:
        totals = await proposal_totals(rows)
        return [proposal_json(row, totals[row.id]) for row in rows]

    async def proposal_detail(proposal: CrmProposal) -> dict[str, object]:
        totals = await proposal_totals([proposal])
        data = proposal_json(proposal, totals[proposal.id])
        data["items"] = [
            {
                "description": item.description,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "total": str(item.total),
            }
            for item in await proposal_items(proposal)
        ]
        titles = await proposal_titles(proposal)
        balances = await title_balances(titles)
        data["titles"] = [title_json(title, balances[title.id]) for title in titles]
        return data

    @app.get("/api/v1/crm/proposals")
    @api_login_required
    async def api_crm_proposals(request: Request, current: CurrentUser) -> Response:
        query = proposals_query(
            await current[1].organization, status=request.query_params.get("status", None)
        )
        return await listing(request, query, proposals_serializer)

    @app.post("/api/v1/crm/deals/:deal_id/proposals")
    @api_login_required
    async def api_crm_create_proposal(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        deal = await owned_deal(request, current)
        if deal is None:
            return error_response("not_found", "Negociação não encontrada.", 404)
        try:
            payload = json_payload(request)
            proposal = await create_proposal(
                organization=await current[1].organization,
                user=current[0],
                operation_key=_uuid(payload, "operation_key"),
                deal_id=deal.id,
                items=_items(payload),
                valid_until=_date(payload, "valid_until"),
                discount=_amount(payload, "discount") or Decimal("0.00"),
                notes=_text(payload, "notes", required=False),
            )
        except ValueError as exc:
            return _error(exc)
        return json_response(await proposal_detail(proposal), 201)

    async def owned_proposal(request: Request, current: CurrentUser) -> CrmProposal | None:
        proposal_id = _path_id(request, "proposal_id")
        return (
            await get_proposal(await current[1].organization, proposal_id) if proposal_id else None
        )

    @app.get("/api/v1/crm/proposals/:proposal_id")
    @api_login_required
    async def api_crm_proposal(request: Request, current: CurrentUser) -> Response:
        proposal = await owned_proposal(request, current)
        if proposal is None:
            return error_response("not_found", "Proposta não encontrada.", 404)
        return json_response(await proposal_detail(proposal))

    async def proposal_write(
        request: Request, current: CurrentUser, action, status: int = 200
    ) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        proposal = await owned_proposal(request, current)
        if proposal is None:
            return error_response("not_found", "Proposta não encontrada.", 404)
        try:
            proposal = await action(await current[1].organization, proposal, json_payload(request))
        except ValueError as exc:
            message = str(exc)
            if "já foi usada" not in message and (
                "roposta" in message or "negociação perdida" in message
            ):
                return error_response("invalid_state", message, 409)
            return _error(exc)
        return json_response(await proposal_detail(proposal), status)

    @app.post("/api/v1/crm/proposals/:proposal_id/send")
    @api_login_required
    async def api_crm_send_proposal(request: Request, current: CurrentUser) -> Response:
        async def action(organization, proposal, payload):
            return await send_proposal(organization=organization, proposal_id=proposal.id)

        return await proposal_write(request, current, action)

    @app.post("/api/v1/crm/proposals/:proposal_id/reject")
    @api_login_required
    async def api_crm_reject_proposal(request: Request, current: CurrentUser) -> Response:
        async def action(organization, proposal, payload):
            return await reject_proposal(
                organization=organization,
                user=current[0],
                proposal_id=proposal.id,
                reason=_text(payload, "reason", required=False),
            )

        return await proposal_write(request, current, action)

    @app.post("/api/v1/crm/proposals/:proposal_id/accept")
    @api_login_required
    async def api_crm_accept_proposal(request: Request, current: CurrentUser) -> Response:
        async def action(organization, proposal, payload):
            installments = payload.get("installments", 1)
            if not isinstance(installments, int) or isinstance(installments, bool):
                raise ValueError("installments deve ser um número inteiro.")
            accepted, _ = await accept_proposal(
                organization=organization,
                user=current[0],
                proposal_id=proposal.id,
                operation_key=_uuid(payload, "operation_key"),
                account_id=_uuid(payload, "account_id"),
                payment_method=_text(payload, "payment_method"),
                first_due_date=_date(payload, "first_due_date"),
                installment_count=installments,
                category_id=_uuid(payload, "category_id", required=False),
            )
            return accepted

        return await proposal_write(request, current, action, 201)
