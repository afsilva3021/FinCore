"""Rotas: páginas do CRM (painel, leads, negociações, atividades, funil e propostas)."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from uuid import UUID

from robyn import Request, Response

from fincore.application.crm import (
    complete_activity,
    convert_lead,
    create_activity,
    create_deal,
    create_lead,
    ensure_default_stages,
    find_duplicate_contacts,
    lose_deal,
    move_deal,
    parse_optional_amount,
    reopen_deal,
    save_stage,
    set_stage_active,
    update_deal,
    update_lead,
    win_deal,
)
from fincore.application.crm_proposals import (
    accept_proposal,
    create_proposal,
    proposal_titles,
    reject_proposal,
    send_proposal,
)
from fincore.application.finance import parse_money
from fincore.application.queries import (
    KANBAN_LIMIT,
    active_accounts,
    active_customers,
    activities_query,
    analytic_categories,
    crm_stages,
    crm_summary,
    deal_events,
    deals_query,
    get_deal,
    get_lead,
    get_proposal,
    leads_query,
    open_deals_for_kanban,
    proposal_items,
    proposal_totals,
    proposals_query,
    selectable_deals,
)
from fincore.domain.crm import (
    STALLED_AFTER_DAYS,
    activity_is_overdue,
    is_stalled,
    proposal_is_expired,
    qualification_score,
)
from fincore.domain.money import normalize_brl
from fincore.infrastructure.models import CrmDeal, CrmLead, CrmProposal, Organization
from fincore.presentation.web import (
    CurrentUser,
    _csrf_from_auth,
    _form_data,
    _form_date,
    _form_uuid,
    _page,
    _paginate,
    _redirect,
    _response,
    _valid_csrf,
    login_required,
)

LEAD_STATUS_LABELS = {
    "new": "Novo",
    "contacted": "Contatado",
    "qualified": "Qualificado",
    "disqualified": "Desqualificado",
    "converted": "Convertido",
}
LEAD_SOURCE_LABELS = {
    "website": "Site",
    "referral": "Indicação",
    "cold_call": "Prospecção ativa",
    "event": "Evento",
    "social": "Redes sociais",
    "other": "Outra",
}
DEAL_STATUS_LABELS = {"open": "Em aberto", "won": "Ganha", "lost": "Perdida"}
ACTIVITY_LABELS = {
    "task": "Tarefa",
    "call": "Ligação",
    "meeting": "Reunião",
    "email": "E-mail",
    "note": "Nota",
}
PROPOSAL_STATUS_LABELS = {
    "draft": "Rascunho",
    "sent": "Enviada",
    "accepted": "Aceita",
    "rejected": "Recusada",
    "expired": "Vencida",
}
EVENT_LABELS = {
    "created": "Negociação criada",
    "updated": "Dados alterados",
    "stage_changed": "Mudança de etapa",
    "won": "Marcada como ganha",
    "lost": "Marcada como perdida",
    "reopened": "Reaberta",
}
PAYMENT_METHODS = [
    ("pix", "Pix"),
    ("boleto", "Boleto"),
    ("bank_transfer", "Transferência"),
    ("card", "Cartão"),
    ("cash", "Dinheiro"),
    ("other", "Outro"),
]
# Mensagens de sucesso exibidas após um redirect. A URL leva só o código: texto livre
# vindo da query permitiria forjar avisos dentro da aplicação (content spoofing).
MESSAGES = {
    "lead_created": "Lead cadastrado.",
    "lead_updated": "Lead atualizado.",
    "lead_converted_deal": "Lead convertido em contato e negociação.",
    "lead_converted": "Lead convertido em contato.",
    "deal_created": "Negociação criada.",
    "deal_updated": "Negociação atualizada.",
    "deal_moved": "Negociação movida de etapa.",
    "deal_won": "Negociação marcada como ganha.",
    "deal_lost": "Negociação marcada como perdida.",
    "deal_reopened": "Negociação reaberta.",
    "activity_created": "Atividade registrada.",
    "activity_done": "Atividade concluída.",
    "proposal_created": "Proposta criada em rascunho.",
    "proposal_sent": "Proposta marcada como enviada.",
    "proposal_rejected": "Proposta marcada como recusada.",
    "proposal_accepted": "Proposta aceita: títulos a receber gerados.",
    "stage_created": "Etapa adicionada.",
    "stage_updated": "Etapa atualizada.",
}
PROPOSAL_ITEM_ROWS = 6
CHECKED = {"true", "on", "1"}


def _path_uuid(request: Request, key: str) -> UUID | None:
    try:
        return UUID(request.path_params[key])
    except KeyError, ValueError:
        return None


def _optional_date(form: dict[str, str], key: str, message: str) -> date | None:
    return _form_date(form, key, message) if form.get(key) else None


def _optional_uuid(form: dict[str, str], key: str, message: str) -> UUID | None:
    return _form_uuid(form, key, message) if form.get(key) else None


def _quantity(value: str) -> Decimal:
    try:
        return Decimal(normalize_brl(value))
    except ValueError, InvalidOperation:
        raise ValueError("Informe a quantidade de cada item como número.") from None


def proposal_items_from_form(form: dict[str, str]) -> list[tuple[str, Decimal, Decimal]]:
    items = []
    for index in range(1, PROPOSAL_ITEM_ROWS + 1):
        description = form.get(f"item_description_{index}", "").strip()
        quantity = form.get(f"item_quantity_{index}", "").strip()
        price = form.get(f"item_price_{index}", "").strip()
        if not (description or price):
            continue
        if not description or not price:
            raise ValueError("Preencha a descrição e o preço unitário de cada item.")
        items.append((description, _quantity(quantity or "1"), parse_money(price)))
    return items


def _back(path: str, code: str) -> Response:
    if code not in MESSAGES:
        raise KeyError(code)
    return _redirect(f"{path}?{urlencode({'message': code})}")


def register_crm_routes(app: object) -> None:
    def context(request: Request, current: CurrentUser, organization: Organization, **extra):
        return {
            "name": current[0].full_name,
            "organization": organization,
            "csrf": _csrf_from_auth(request),
            "today": date.today(),
            "message": MESSAGES.get(request.query_params.get("message", ""), ""),
            **extra,
        }

    # --- Painel -----------------------------------------------------------------------

    @app.get("/crm")
    @login_required
    async def crm_dashboard_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        await ensure_default_stages(organization)
        return _response(
            _page(
                "crm/dashboard.html",
                **context(request, current, organization, active="crm", section="Painel comercial"),
                summary=await crm_summary(organization),
                lead_status_labels=LEAD_STATUS_LABELS,
                stalled_days=STALLED_AFTER_DAYS,
            )
        )

    # --- Leads ------------------------------------------------------------------------

    async def render_leads(
        request: Request,
        current: CurrentUser,
        organization: Organization,
        *,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        status = request.query_params.get("status", "active")
        if status not in {"active", "all", *LEAD_STATUS_LABELS}:
            status = "active"
        search = request.query_params.get("q", "").strip()[:120]
        leads, pagination = await _paginate(
            leads_query(organization, status=status, search=search),
            request,
            keep=("status", "q"),
        )
        return _response(
            _page(
                "crm/leads.html",
                **context(request, current, organization, active="crm-leads", section="Leads"),
                leads=leads,
                pagination=pagination,
                status=status,
                search=search,
                status_labels=LEAD_STATUS_LABELS,
                source_labels=LEAD_SOURCE_LABELS,
                scores={
                    lead.id: qualification_score(
                        need=lead.has_need,
                        budget=lead.has_budget,
                        authority=lead.has_authority,
                        timeline=lead.has_timeline,
                    )
                    for lead in leads
                },
                error=error,
                form=form or {},
                open_drawer=form is not None,
            ),
            400 if error else 200,
        )

    @app.get("/crm/leads")
    @login_required
    async def crm_leads_page(request: Request, current: CurrentUser) -> Response:
        return await render_leads(request, current, await current[1].organization)

    @app.post("/crm/leads")
    @login_required
    async def crm_create_lead_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/leads")
        organization = await current[1].organization
        try:
            await create_lead(
                organization=organization,
                user=current[0],
                name=form.get("name", ""),
                company_name=form.get("company_name", ""),
                email=form.get("email", ""),
                phone=form.get("phone", ""),
                source=form.get("source", "other"),
                estimated_value=parse_optional_amount(form.get("estimated_value", ""))
                if form.get("estimated_value", "").strip()
                else None,
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            return await render_leads(request, current, organization, error=str(exc), form=form)
        return _back("/crm/leads", "lead_created")

    @app.post("/crm/leads/:lead_id/update")
    @login_required
    async def crm_update_lead_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/leads")
        organization = await current[1].organization
        lead_id = _path_uuid(request, "lead_id")
        try:
            if lead_id is None:
                raise ValueError("Lead não encontrado nesta organização.")
            await update_lead(
                organization=organization,
                lead_id=lead_id,
                status=form.get("status", ""),
                has_need=form.get("has_need", "").lower() in CHECKED,
                has_budget=form.get("has_budget", "").lower() in CHECKED,
                has_authority=form.get("has_authority", "").lower() in CHECKED,
                has_timeline=form.get("has_timeline", "").lower() in CHECKED,
                disqualified_reason=form.get("disqualified_reason", ""),
            )
        except ValueError as exc:
            return await render_leads(request, current, organization, error=str(exc))
        return _back("/crm/leads", "lead_updated")

    async def render_convert(
        request: Request,
        current: CurrentUser,
        organization: Organization,
        lead: CrmLead,
        *,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        await ensure_default_stages(organization)
        duplicates = await find_duplicate_contacts(
            organization, names=[lead.name, lead.company_name or ""], email=lead.email
        )
        duplicate_ids = {item.id for item in duplicates}
        return _response(
            _page(
                "crm/lead_convert.html",
                **context(request, current, organization, active="crm-leads", section="Leads"),
                lead=lead,
                duplicates=[item for item in duplicates if item.is_active],
                contacts=[
                    item
                    for item in await active_customers(organization)
                    if item.id not in duplicate_ids
                ],
                stages=await crm_stages(organization, active_only=True),
                error=error,
                form=form or {},
            ),
            400 if error else 200,
        )

    @app.get("/crm/leads/:lead_id/convert")
    @login_required
    async def crm_convert_lead_form(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        lead_id = _path_uuid(request, "lead_id")
        lead = await get_lead(organization, lead_id) if lead_id else None
        if lead is None:
            return _response("Lead não encontrado.", 404)
        if lead.status == "converted":
            return _redirect("/crm/leads?status=converted")
        return await render_convert(request, current, organization, lead)

    @app.post("/crm/leads/:lead_id/convert")
    @login_required
    async def crm_convert_lead_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/leads")
        organization = await current[1].organization
        lead_id = _path_uuid(request, "lead_id")
        lead = await get_lead(organization, lead_id) if lead_id else None
        if lead is None:
            return _response("Lead não encontrado.", 404)
        try:
            amount_text = form.get("deal_amount", "").strip()
            _, _, deal = await convert_lead(
                organization=organization,
                user=current[0],
                lead_id=lead.id,
                counterparty_id=_optional_uuid(form, "counterparty_id", "Contato inválido."),
                person_type=form.get("person_type", "individual"),
                tax_id=form.get("tax_id", ""),
                create_deal=form.get("create_deal", "").lower() in CHECKED,
                deal_title=form.get("deal_title", ""),
                deal_amount=parse_optional_amount(amount_text) if amount_text else None,
                stage_id=_optional_uuid(form, "stage_id", "Etapa inválida."),
                expected_close_date=_optional_date(
                    form, "expected_close_date", "Informe uma previsão de fechamento válida."
                ),
            )
        except ValueError as exc:
            return await render_convert(
                request, current, organization, lead, error=str(exc), form=form
            )
        if deal is not None:
            return _back(f"/crm/deals/{deal.id}", "lead_converted_deal")
        return _back("/crm/leads", "lead_converted")

    # --- Negociações ------------------------------------------------------------------

    async def render_deals(
        request: Request,
        current: CurrentUser,
        organization: Organization,
        *,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        await ensure_default_stages(organization)
        status = request.query_params.get("status", "open")
        if status not in DEAL_STATUS_LABELS:
            status = "open"
        today = date.today()
        stages = await crm_stages(organization)
        columns: list[tuple[object, list[CrmDeal], Decimal]] = []
        closed: list[CrmDeal] = []
        pagination = None
        truncated = False
        stalled: set[UUID] = set()
        if status == "open":
            deals = await open_deals_for_kanban(organization)
            truncated = len(deals) > KANBAN_LIMIT
            deals = deals[:KANBAN_LIMIT]
            stalled = {deal.id for deal in deals if is_stalled(deal.stage_changed_at.date(), today)}
            for stage in stages:
                items = [deal for deal in deals if deal.stage_id == stage.id]
                if stage.is_active or items:
                    columns.append(
                        (stage, items, sum((deal.amount for deal in items), Decimal("0.00")))
                    )
        else:
            closed, pagination = await _paginate(
                deals_query(organization, status=status), request, keep=("status",)
            )
        return _response(
            _page(
                "crm/deals.html",
                **context(
                    request, current, organization, active="crm-deals", section="Negociações"
                ),
                status=status,
                status_labels=DEAL_STATUS_LABELS,
                columns=columns,
                active_stages=[stage for stage in stages if stage.is_active],
                closed=closed,
                pagination=pagination,
                truncated=truncated,
                kanban_limit=KANBAN_LIMIT,
                stalled=stalled,
                stalled_days=STALLED_AFTER_DAYS,
                contacts=await active_customers(organization),
                error=error,
                form=form or {},
                open_drawer=form is not None,
            ),
            400 if error else 200,
        )

    @app.get("/crm/deals")
    @login_required
    async def crm_deals_page(request: Request, current: CurrentUser) -> Response:
        return await render_deals(request, current, await current[1].organization)

    @app.post("/crm/deals")
    @login_required
    async def crm_create_deal_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/deals")
        organization = await current[1].organization
        try:
            deal = await create_deal(
                organization=organization,
                user=current[0],
                counterparty_id=_form_uuid(form, "counterparty_id", "Selecione um contato."),
                title=form.get("title", ""),
                amount=parse_optional_amount(form.get("amount", "")),
                stage_id=_optional_uuid(form, "stage_id", "Etapa inválida."),
                expected_close_date=_optional_date(
                    form, "expected_close_date", "Informe uma previsão de fechamento válida."
                ),
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            return await render_deals(request, current, organization, error=str(exc), form=form)
        return _back(f"/crm/deals/{deal.id}", "deal_created")

    async def render_deal(
        request: Request,
        current: CurrentUser,
        organization: Organization,
        deal: CrmDeal,
        *,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        today = date.today()
        proposals = await proposals_query(organization, deal=deal)
        activities = await activities_query(organization, status="all", deal=deal).limit(100)
        return _response(
            _page(
                "crm/deal_detail.html",
                **context(
                    request, current, organization, active="crm-deals", section="Negociações"
                ),
                deal=deal,
                stages=await crm_stages(organization, active_only=True),
                status_labels=DEAL_STATUS_LABELS,
                events=await deal_events(deal),
                event_labels=EVENT_LABELS,
                activities=activities,
                activity_labels=ACTIVITY_LABELS,
                overdue={
                    item.id
                    for item in activities
                    if activity_is_overdue(item.due_date, item.done_at is not None, today)
                },
                proposals=proposals,
                proposal_totals=await proposal_totals(proposals),
                proposal_states={
                    item.id: "expired"
                    if proposal_is_expired(item.status, item.valid_until, today)
                    else item.status
                    for item in proposals
                },
                proposal_labels=PROPOSAL_STATUS_LABELS,
                item_rows=range(1, PROPOSAL_ITEM_ROWS + 1),
                default_valid_until=(today + timedelta(days=15)).isoformat(),
                stalled=deal.status == "open" and is_stalled(deal.stage_changed_at.date(), today),
                stalled_days=STALLED_AFTER_DAYS,
                error=error,
                form=form or {},
                open_drawer=form is not None,
            ),
            400 if error else 200,
        )

    async def owned_deal(request: Request, organization: Organization) -> CrmDeal | None:
        deal_id = _path_uuid(request, "deal_id")
        return await get_deal(organization, deal_id) if deal_id else None

    @app.get("/crm/deals/:deal_id")
    @login_required
    async def crm_deal_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        deal = await owned_deal(request, organization)
        if deal is None:
            return _response("Negociação não encontrada.", 404)
        return await render_deal(request, current, organization, deal)

    async def deal_action(request: Request, current: CurrentUser, action, success: str) -> Response:
        """Executa uma ação sobre a negociação da URL e volta para a página dela."""
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/deals")
        organization = await current[1].organization
        deal = await owned_deal(request, organization)
        if deal is None:
            return _response("Negociação não encontrada.", 404)
        try:
            await action(organization, deal, form)
        except ValueError as exc:
            refreshed = await get_deal(organization, deal.id)
            return await render_deal(request, current, organization, refreshed, error=str(exc))
        target = "/crm/deals" if form.get("return_to") == "board" else f"/crm/deals/{deal.id}"
        return _back(target, success)

    @app.post("/crm/deals/:deal_id/update")
    @login_required
    async def crm_update_deal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, form):
            await update_deal(
                organization=organization,
                user=current[0],
                deal_id=deal.id,
                title=form.get("title", ""),
                amount=parse_optional_amount(form.get("amount", "")),
                expected_close_date=_optional_date(
                    form, "expected_close_date", "Informe uma previsão de fechamento válida."
                ),
                notes=form.get("notes", ""),
            )

        return await deal_action(request, current, action, "deal_updated")

    @app.post("/crm/deals/:deal_id/stage")
    @login_required
    async def crm_move_deal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, form):
            await move_deal(
                organization=organization,
                user=current[0],
                deal_id=deal.id,
                stage_id=_form_uuid(form, "stage_id", "Selecione uma etapa do funil."),
            )

        return await deal_action(request, current, action, "deal_moved")

    @app.post("/crm/deals/:deal_id/win")
    @login_required
    async def crm_win_deal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, form):
            await win_deal(organization=organization, user=current[0], deal_id=deal.id)

        return await deal_action(request, current, action, "deal_won")

    @app.post("/crm/deals/:deal_id/lose")
    @login_required
    async def crm_lose_deal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, form):
            await lose_deal(
                organization=organization,
                user=current[0],
                deal_id=deal.id,
                reason=form.get("lost_reason", ""),
            )

        return await deal_action(request, current, action, "deal_lost")

    @app.post("/crm/deals/:deal_id/reopen")
    @login_required
    async def crm_reopen_deal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, form):
            await reopen_deal(organization=organization, user=current[0], deal_id=deal.id)

        return await deal_action(request, current, action, "deal_reopened")

    @app.post("/crm/deals/:deal_id/activities")
    @login_required
    async def crm_deal_activity_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, deal, form):
            await create_activity(
                organization=organization,
                user=current[0],
                activity_type=form.get("activity_type", ""),
                subject=form.get("subject", ""),
                due_date=_optional_date(form, "due_date", "Informe uma data válida."),
                notes=form.get("notes", ""),
                deal_id=deal.id,
                done=form.get("done", "").lower() in CHECKED,
            )

        return await deal_action(request, current, action, "activity_created")

    @app.post("/crm/deals/:deal_id/proposals")
    @login_required
    async def crm_create_proposal_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/deals")
        organization = await current[1].organization
        deal = await owned_deal(request, organization)
        if deal is None:
            return _response("Negociação não encontrada.", 404)
        try:
            proposal = await create_proposal(
                organization=organization,
                user=current[0],
                deal_id=deal.id,
                items=proposal_items_from_form(form),
                valid_until=_form_date(form, "valid_until", "Informe a validade da proposta."),
                discount=parse_optional_amount(form.get("discount", "")),
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            return await render_deal(
                request, current, organization, deal, error=str(exc), form=form
            )
        return _back(f"/crm/proposals/{proposal.id}", "proposal_created")

    # --- Atividades -------------------------------------------------------------------

    async def render_activities(
        request: Request,
        current: CurrentUser,
        organization: Organization,
        *,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        status = request.query_params.get("status", "pending")
        if status not in {"pending", "done", "all"}:
            status = "pending"
        today = date.today()
        activities, pagination = await _paginate(
            activities_query(organization, status=status), request, keep=("status",)
        )
        return _response(
            _page(
                "crm/activities.html",
                **context(
                    request, current, organization, active="crm-activities", section="Atividades"
                ),
                activities=activities,
                pagination=pagination,
                status=status,
                activity_labels=ACTIVITY_LABELS,
                overdue={
                    item.id
                    for item in activities
                    if activity_is_overdue(item.due_date, item.done_at is not None, today)
                },
                deals=await selectable_deals(organization),
                leads=await leads_query(organization, status="active").limit(KANBAN_LIMIT),
                contacts=await active_customers(organization),
                error=error,
                form=form or {},
                open_drawer=form is not None,
            ),
            400 if error else 200,
        )

    @app.get("/crm/activities")
    @login_required
    async def crm_activities_page(request: Request, current: CurrentUser) -> Response:
        return await render_activities(request, current, await current[1].organization)

    @app.post("/crm/activities")
    @login_required
    async def crm_create_activity_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/activities")
        organization = await current[1].organization
        try:
            await create_activity(
                organization=organization,
                user=current[0],
                activity_type=form.get("activity_type", ""),
                subject=form.get("subject", ""),
                due_date=_optional_date(form, "due_date", "Informe uma data válida."),
                notes=form.get("notes", ""),
                lead_id=_optional_uuid(form, "lead_id", "Lead inválido."),
                deal_id=_optional_uuid(form, "deal_id", "Negociação inválida."),
                counterparty_id=_optional_uuid(form, "counterparty_id", "Contato inválido."),
                done=form.get("done", "").lower() in CHECKED,
            )
        except ValueError as exc:
            return await render_activities(
                request, current, organization, error=str(exc), form=form
            )
        return _back("/crm/activities", "activity_created")

    @app.post("/crm/activities/:activity_id/complete")
    @login_required
    async def crm_complete_activity_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/activities")
        organization = await current[1].organization
        activity_id = _path_uuid(request, "activity_id")
        try:
            if activity_id is None:
                raise ValueError("Atividade não encontrada nesta organização.")
            activity = await complete_activity(
                organization=organization, user=current[0], activity_id=activity_id
            )
        except ValueError as exc:
            return await render_activities(request, current, organization, error=str(exc))
        if form.get("return_to") == "deal" and activity.deal_id:
            return _back(f"/crm/deals/{activity.deal_id}", "activity_done")
        return _back("/crm/activities", "activity_done")

    # --- Funil ------------------------------------------------------------------------

    async def render_stages(
        request: Request,
        current: CurrentUser,
        organization: Organization,
        *,
        error: str | None = None,
    ) -> Response:
        await ensure_default_stages(organization)
        return _response(
            _page(
                "crm/stages.html",
                **context(
                    request, current, organization, active="crm-deals", section="Etapas do funil"
                ),
                stages=await crm_stages(organization),
                error=error,
            ),
            400 if error else 200,
        )

    @app.get("/crm/stages")
    @login_required
    async def crm_stages_page(request: Request, current: CurrentUser) -> Response:
        return await render_stages(request, current, await current[1].organization)

    def stage_values(form: dict[str, str]) -> tuple[int, int | None]:
        try:
            probability = int(form.get("win_probability", ""))
            position = int(form["position"]) if form.get("position") else None
        except ValueError:
            raise ValueError("Informe probabilidade e ordem como números inteiros.") from None
        return probability, position

    @app.post("/crm/stages")
    @login_required
    async def crm_create_stage_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/stages")
        organization = await current[1].organization
        try:
            probability, position = stage_values(form)
            await save_stage(
                organization=organization,
                name=form.get("name", ""),
                win_probability=probability,
                position=position,
            )
        except ValueError as exc:
            return await render_stages(request, current, organization, error=str(exc))
        return _back("/crm/stages", "stage_created")

    @app.post("/crm/stages/:stage_id/update")
    @login_required
    async def crm_update_stage_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/stages")
        organization = await current[1].organization
        stage_id = _path_uuid(request, "stage_id")
        try:
            if stage_id is None:
                raise ValueError("Etapa não encontrada nesta organização.")
            if form.get("action") in {"activate", "deactivate"}:
                await set_stage_active(organization, stage_id, form["action"] == "activate")
            else:
                probability, position = stage_values(form)
                await save_stage(
                    organization=organization,
                    stage_id=stage_id,
                    name=form.get("name", ""),
                    win_probability=probability,
                    position=position,
                )
        except ValueError as exc:
            return await render_stages(request, current, organization, error=str(exc))
        return _back("/crm/stages", "stage_updated")

    # --- Propostas --------------------------------------------------------------------

    @app.get("/crm/proposals")
    @login_required
    async def crm_proposals_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        status = request.query_params.get("status", "all")
        if status not in {"all", "draft", "sent", "accepted", "rejected"}:
            status = "all"
        today = date.today()
        proposals, pagination = await _paginate(
            proposals_query(organization, status=status), request, keep=("status",)
        )
        return _response(
            _page(
                "crm/proposals.html",
                **context(
                    request, current, organization, active="crm-proposals", section="Propostas"
                ),
                proposals=proposals,
                pagination=pagination,
                status=status,
                totals=await proposal_totals(proposals),
                states={
                    item.id: "expired"
                    if proposal_is_expired(item.status, item.valid_until, today)
                    else item.status
                    for item in proposals
                },
                labels=PROPOSAL_STATUS_LABELS,
            )
        )

    async def render_proposal(
        request: Request,
        current: CurrentUser,
        organization: Organization,
        proposal: CrmProposal,
        *,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        today = date.today()
        items = await proposal_items(proposal)
        subtotal = sum((item.total for item in items), Decimal("0.00"))
        return _response(
            _page(
                "crm/proposal_detail.html",
                **context(
                    request, current, organization, active="crm-proposals", section="Propostas"
                ),
                proposal=proposal,
                deal=proposal.deal,
                items=items,
                subtotal=subtotal,
                total=subtotal - proposal.discount,
                state="expired"
                if proposal_is_expired(proposal.status, proposal.valid_until, today)
                else proposal.status,
                labels=PROPOSAL_STATUS_LABELS,
                titles=await proposal_titles(proposal),
                accounts=await active_accounts(organization),
                categories=await analytic_categories(organization, "income"),
                payment_methods=PAYMENT_METHODS,
                default_due=(today + timedelta(days=30)).isoformat(),
                error=error,
                form=form or {},
            ),
            400 if error else 200,
        )

    async def owned_proposal(request: Request, organization: Organization) -> CrmProposal | None:
        proposal_id = _path_uuid(request, "proposal_id")
        return await get_proposal(organization, proposal_id) if proposal_id else None

    @app.get("/crm/proposals/:proposal_id")
    @login_required
    async def crm_proposal_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        proposal = await owned_proposal(request, organization)
        if proposal is None:
            return _response("Proposta não encontrada.", 404)
        return await render_proposal(request, current, organization, proposal)

    async def proposal_action(
        request: Request, current: CurrentUser, action, success: str
    ) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/proposals")
        organization = await current[1].organization
        proposal = await owned_proposal(request, organization)
        if proposal is None:
            return _response("Proposta não encontrada.", 404)
        try:
            await action(organization, proposal, form)
        except ValueError as exc:
            refreshed = await get_proposal(organization, proposal.id)
            return await render_proposal(
                request, current, organization, refreshed, error=str(exc), form=form
            )
        return _back(f"/crm/proposals/{proposal.id}", success)

    @app.post("/crm/proposals/:proposal_id/send")
    @login_required
    async def crm_send_proposal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, proposal, form):
            await send_proposal(organization=organization, proposal_id=proposal.id)

        return await proposal_action(request, current, action, "proposal_sent")

    @app.post("/crm/proposals/:proposal_id/reject")
    @login_required
    async def crm_reject_proposal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, proposal, form):
            await reject_proposal(
                organization=organization,
                user=current[0],
                proposal_id=proposal.id,
                reason=form.get("rejection_reason", ""),
            )

        return await proposal_action(request, current, action, "proposal_rejected")

    @app.post("/crm/proposals/:proposal_id/accept")
    @login_required
    async def crm_accept_proposal_page(request: Request, current: CurrentUser) -> Response:
        async def action(organization, proposal, form):
            try:
                installments = int(form.get("installment_count", "1") or "1")
            except ValueError:
                raise ValueError("Informe o número de parcelas como inteiro.") from None
            await accept_proposal(
                organization=organization,
                user=current[0],
                proposal_id=proposal.id,
                account_id=_form_uuid(form, "account_id", "Selecione a conta de recebimento."),
                payment_method=form.get("payment_method", ""),
                first_due_date=_form_date(form, "first_due_date", "Informe o primeiro vencimento."),
                installment_count=installments,
                category_id=_optional_uuid(form, "category_id", "Categoria inválida."),
                operation_key=_optional_uuid(form, "operation_key", "Chave de operação inválida."),
            )

        return await proposal_action(request, current, action, "proposal_accepted")
