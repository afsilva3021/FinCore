"""Rotas: CRM (funil de oportunidades, leads e atividades)."""

from datetime import date
from urllib.parse import urlencode
from uuid import UUID

from robyn import Request, Response

from fincore.application.crm import (
    add_activity,
    complete_activity,
    convert_lead,
    create_opportunity,
    discard_lead,
    move_opportunity,
    save_lead,
)
from fincore.application.queries import (
    active_customers,
    board_opportunities,
    crm_totals,
    get_activity,
    get_lead,
    get_opportunity,
    leads_query,
    open_leads,
    opportunity_activities,
    opportunity_history,
    pending_activities,
    pipeline_stages,
)
from fincore.domain.crm import (
    ACTIVITY_KINDS,
    LEAD_STATUS_LABELS,
    LEAD_STATUSES,
    conversion_rate,
)
from fincore.domain.money import parse_brl
from fincore.infrastructure.models import Opportunity, Organization, User
from fincore.presentation.routes.api import error_response, json_response, pagination
from fincore.presentation.web import (
    CurrentUser,
    _csrf_from_auth,
    _form_data,
    _form_date,
    _page,
    _paginate,
    _query,
    _redirect,
    _response,
    _valid_csrf,
    api_login_required,
    login_required,
)

STAGE_BADGES = {"open": "text-bg-primary", "won": "text-bg-success", "lost": "text-bg-secondary"}


def _path_uuid(request: Request, name: str) -> UUID | None:
    try:
        return UUID(request.path_params[name])
    except ValueError, KeyError:
        return None


def _optional_uuid(value: str, message: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(message) from None


def _optional_date(form: dict[str, str], key: str, message: str) -> date | None:
    return _form_date(form, key, message) if form.get(key, "") else None


def _party_name(opportunity: Opportunity) -> str:
    if opportunity.counterparty is not None:
        return opportunity.counterparty.trade_name or opportunity.counterparty.name
    return opportunity.lead.name if opportunity.lead is not None else ""


def opportunity_json(item: Opportunity) -> dict[str, object]:
    return {
        "id": str(item.id),
        "title": item.title,
        "stage": {"id": str(item.stage_id), "name": item.stage.name, "kind": item.stage.kind},
        "party": _party_name(item),
        "lead_id": str(item.lead_id) if item.lead_id else None,
        "counterparty_id": str(item.counterparty_id) if item.counterparty_id else None,
        "expected_value": str(item.expected_value),
        "expected_close": item.expected_close.isoformat() if item.expected_close else None,
        "lost_reason": item.lost_reason,
        "sales_order_id": str(item.sales_order_id) if item.sales_order_id else None,
    }


def register_crm_routes(app: object) -> None:
    # --- Funil ------------------------------------------------------------------------

    async def render_board(
        request: Request,
        user: User,
        organization: Organization,
        *,
        form: dict[str, str] | None = None,
        error: str | None = None,
    ) -> Response:
        query = _query(request)
        stages = await pipeline_stages(organization)
        opportunities = await board_opportunities(organization)
        columns = [
            {
                "stage": stage,
                "cards": [
                    {"item": item, "party": _party_name(item)}
                    for item in opportunities
                    if item.stage_id == stage.id
                ],
            }
            for stage in stages
        ]
        totals = await crm_totals(organization)
        if form is None:
            form = {"lead_id": query.get("lead", "") or ""}
        body = _page(
            "crm/board.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            columns=columns,
            totals=totals,
            rate=conversion_rate(totals["won"][0], totals["lost"][0]),
            activities=await pending_activities(organization),
            activity_kinds=ACTIVITY_KINDS,
            leads=await open_leads(organization),
            customers=await active_customers(organization),
            form=form,
            open_drawer=error is not None or query.get("new", "") == "1",
            error=error,
            message=query.get("message", ""),
            today=date.today(),
            active="crm",
            section="CRM",
        )
        return _response(body, 400 if error else 200)

    @app.get("/crm")
    @login_required
    async def crm_page(request: Request, current: CurrentUser) -> Response:
        return await render_board(request, current[0], await current[1].organization)

    @app.post("/crm/opportunities")
    @login_required
    async def create_opportunity_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm")
        organization = await current[1].organization
        try:
            opportunity = await create_opportunity(
                organization=organization,
                user=current[0],
                title=form.get("title", ""),
                lead_id=_optional_uuid(form.get("lead_id", ""), "Selecione um lead válido."),
                counterparty_id=_optional_uuid(
                    form.get("counterparty_id", ""), "Selecione um cliente válido."
                ),
                expected_value=parse_brl(
                    form.get("expected_value", ""),
                    allow_zero=True,
                    empty_as_zero=True,
                    invalid_message="Informe o valor estimado em formato válido, como 1.500,00.",
                    range_message="O valor estimado deve ser zero ou maior.",
                ),
                expected_close=_optional_date(
                    form, "expected_close", "Informe uma previsão de fechamento válida."
                ),
            )
        except ValueError as exc:
            return await render_board(request, current[0], organization, form=form, error=str(exc))
        message = urlencode({"message": "Oportunidade criada."})
        return _redirect(f"/crm/opportunities/{opportunity.id}?{message}")

    # --- Oportunidade -----------------------------------------------------------------

    async def render_opportunity(
        request: Request,
        user: User,
        organization: Organization,
        opportunity: Opportunity,
        *,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        body = _page(
            "crm/opportunity.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            opportunity=opportunity,
            party=_party_name(opportunity),
            stages=await pipeline_stages(organization),
            stage_badges=STAGE_BADGES,
            history=await opportunity_history(opportunity),
            activities=await opportunity_activities(opportunity),
            activity_kinds=ACTIVITY_KINDS,
            form=form or {},
            error=error,
            message=_query(request).get("message", ""),
            today=date.today(),
            active="crm",
            section="CRM / Oportunidade",
        )
        return _response(body, 400 if error else 200)

    async def opportunity_from_path(request: Request, organization: Organization):
        opportunity_id = _path_uuid(request, "opportunity_id")
        return await get_opportunity(organization, opportunity_id) if opportunity_id else None

    @app.get("/crm/opportunities/:opportunity_id")
    @login_required
    async def opportunity_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        opportunity = await opportunity_from_path(request, organization)
        if opportunity is None:
            return _response("Oportunidade não encontrada.", 404)
        return await render_opportunity(request, current[0], organization, opportunity)

    def opportunity_action(path: str, success: str):
        def decorator(handler):
            @login_required
            async def route(request: Request, current: CurrentUser) -> Response:
                form = _form_data(request)
                if not _valid_csrf(request, form.get("csrf", "")):
                    return _redirect("/crm")
                organization = await current[1].organization
                opportunity = await opportunity_from_path(request, organization)
                if opportunity is None:
                    return _response("Oportunidade não encontrada.", 404)
                try:
                    await handler(organization, current[0], opportunity, form)
                except ValueError as exc:
                    return await render_opportunity(
                        request, current[0], organization, opportunity, error=str(exc), form=form
                    )
                message = urlencode({"message": success})
                return _redirect(f"/crm/opportunities/{opportunity.id}?{message}")

            route.__name__ = handler.__name__
            route.__qualname__ = handler.__qualname__
            app.post(path)(route)
            return handler

        return decorator

    @opportunity_action("/crm/opportunities/:opportunity_id/stage", "Etapa atualizada.")
    async def change_stage(organization, user, opportunity, form):
        stage_id = _optional_uuid(form.get("stage_id", ""), "Selecione uma etapa válida.")
        if stage_id is None:
            raise ValueError("Selecione a nova etapa.")
        await move_opportunity(
            organization=organization,
            user=user,
            opportunity_id=opportunity.id,
            stage_id=stage_id,
            lost_reason=form.get("lost_reason", ""),
        )

    @opportunity_action("/crm/opportunities/:opportunity_id/activities", "Atividade registrada.")
    async def create_activity(organization, user, opportunity, form):
        await add_activity(
            organization=organization,
            user=user,
            kind=form.get("kind", ""),
            subject=form.get("subject", ""),
            due_on=_optional_date(form, "due_on", "Informe uma data válida para a atividade."),
            opportunity_id=opportunity.id,
        )

    @app.post("/crm/activities/:activity_id/done")
    @login_required
    async def complete_activity_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm")
        organization = await current[1].organization
        activity_id = _path_uuid(request, "activity_id")
        activity = await get_activity(organization, activity_id) if activity_id else None
        if activity is None:
            return _response("Atividade não encontrada.", 404)
        await complete_activity(organization, activity.id)
        message = urlencode({"message": "Atividade concluída."})
        if activity.opportunity_id is not None and form.get("back", "") != "board":
            return _redirect(f"/crm/opportunities/{activity.opportunity_id}?{message}")
        return _redirect(f"/crm?{message}")

    # --- Leads ------------------------------------------------------------------------

    async def render_leads(
        request: Request,
        user: User,
        organization: Organization,
        *,
        form: dict[str, str] | None = None,
        error: str | None = None,
        drawer: bool = False,
    ) -> Response:
        query = _query(request)
        status = query.get("status", "") or ""
        status = status if status in LEAD_STATUSES else ""
        search = (query.get("q", "") or "").strip()
        leads, page_info = await _paginate(
            leads_query(organization, status=status, search=search), request, keep=("status", "q")
        )
        body = _page(
            "crm/leads.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            leads=leads,
            pagination=page_info,
            status=status,
            search=search,
            status_labels=LEAD_STATUS_LABELS,
            form=form or {},
            open_drawer=drawer,
            error=error,
            message=query.get("message", ""),
            active="crm-leads",
            section="CRM / Leads",
        )
        return _response(body, 400 if error else 200)

    @app.get("/crm/leads")
    @login_required
    async def leads_page(request: Request, current: CurrentUser) -> Response:
        return await render_leads(request, current[0], await current[1].organization)

    @app.post("/crm/leads")
    @login_required
    async def create_lead_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/crm/leads")
        organization = await current[1].organization
        try:
            await save_lead(
                organization=organization,
                user=current[0],
                name=form.get("name", ""),
                company=form.get("company", ""),
                email=form.get("email", ""),
                phone=form.get("phone", ""),
                source=form.get("source", ""),
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            return await render_leads(
                request, current[0], organization, form=form, error=str(exc), drawer=True
            )
        return _redirect(f"/crm/leads?{urlencode({'message': 'Lead cadastrado.'})}")

    def lead_action(path: str):
        def decorator(handler):
            @login_required
            async def route(request: Request, current: CurrentUser) -> Response:
                form = _form_data(request)
                if not _valid_csrf(request, form.get("csrf", "")):
                    return _redirect("/crm/leads")
                organization = await current[1].organization
                lead_id = _path_uuid(request, "lead_id")
                lead = await get_lead(organization, lead_id) if lead_id else None
                if lead is None:
                    return _response("Lead não encontrado.", 404)
                try:
                    message = await handler(organization, lead, form)
                except ValueError as exc:
                    return await render_leads(request, current[0], organization, error=str(exc))
                return _redirect(f"/crm/leads?{urlencode({'message': message})}")

            route.__name__ = handler.__name__
            route.__qualname__ = handler.__qualname__
            app.post(path)(route)
            return handler

        return decorator

    @lead_action("/crm/leads/:lead_id/convert")
    async def convert_lead_route(organization, lead, form):
        _, customer, created = await convert_lead(
            organization=organization,
            lead_id=lead.id,
            person_type=form.get("person_type", ""),
            tax_id=form.get("tax_id", ""),
        )
        if created:
            return f"Lead convertido: cliente {customer.name} cadastrado."
        return f"Lead convertido e vinculado ao contato {customer.name}, que já existia."

    @lead_action("/crm/leads/:lead_id/discard")
    async def discard_lead_route(organization, lead, _form):
        await discard_lead(organization, lead.id)
        return "Lead descartado."

    # --- API JSON (consulta) ----------------------------------------------------------

    @app.get("/api/v1/crm/opportunities")
    @api_login_required
    async def api_opportunities(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        items = await board_opportunities(await current[1].organization, closed_days=3650)
        return json_response(
            {
                "items": [opportunity_json(item) for item in items[offset : offset + limit]],
                "limit": limit,
                "offset": offset,
                "total": len(items),
            }
        )
