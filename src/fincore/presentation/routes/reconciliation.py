"""Rotas: Importação OFX e conciliação bancária."""

from uuid import UUID

from robyn import Request, Response
from tortoise.exceptions import IntegrityError

from fincore.application.queries import (
    active_accounts,
    matches_by_statement,
    reconciliation_candidates,
    statement_query,
)
from fincore.application.reconciliation import (
    import_ofx,
    match_statement_transaction,
    unmatch_statement_transaction,
)
from fincore.infrastructure.models import (
    Organization,
    User,
)
from fincore.presentation.web import (
    CurrentUser,
    _csrf_from_auth,
    _form_data,
    _page,
    _paginate,
    _redirect,
    _response,
    _valid_csrf,
    login_required,
)


def register_reconciliation_routes(app: object) -> None:
    async def render_reconciliation(
        request: Request,
        user: User,
        organization: Organization,
        message: str | None = None,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> str:
        imported, pagination = await _paginate(statement_query(organization), request)
        # Vínculos e candidatos da página em duas queries (antes: duas por linha do extrato).
        matches = await matches_by_statement(item.id for item in imported)
        candidates = await reconciliation_candidates(organization, imported)
        rows = [
            (
                item,
                matches.get(item.id),
                candidates.get((item.account_id, item.posted_at), []),
            )
            for item in imported
        ]
        return _page(
            "finance/reconciliation.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            accounts=await active_accounts(organization),
            rows=rows,
            pagination=pagination,
            message=message,
            error=error,
            form=form or {},
        )

    @app.get("/reconciliation")
    @login_required
    async def reconciliation_page(request: Request, current: CurrentUser) -> Response:
        return _response(
            await render_reconciliation(request, current[0], await current[1].organization)
        )

    @app.post("/reconciliation/import")
    @login_required
    async def import_statement_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/reconciliation")
        try:
            created, duplicates = await import_ofx(
                organization, UUID(form.get("account_id", "")), form.get("ofx_content", "")
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await render_reconciliation(
                    request, current[0], organization, error=str(exc), form=form
                ),
                400,
            )
        return _response(
            await render_reconciliation(
                request,
                current[0],
                organization,
                message=(
                    f"{created} lançamento(s) importado(s); {duplicates} duplicado(s) ignorado(s)."
                ),
            )
        )

    @app.post("/reconciliation/:statement_id/match")
    @login_required
    async def match_statement_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/reconciliation")
        try:
            await match_statement_transaction(
                organization,
                current[0],
                UUID(request.path_params["statement_id"]),
                UUID(form.get("financial_transaction_id", "")),
            )
        except (ValueError, KeyError, IntegrityError) as exc:
            return _response(
                await render_reconciliation(
                    request, current[0], organization, error=str(exc) or "Vínculo já utilizado."
                ),
                400,
            )
        return _redirect("/reconciliation")

    @app.post("/reconciliation/:statement_id/unmatch")
    @login_required
    async def unmatch_statement_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/reconciliation")
        try:
            await unmatch_statement_transaction(
                organization, current[0], UUID(request.path_params["statement_id"])
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await render_reconciliation(request, current[0], organization, error=str(exc)), 400
            )
        return _redirect("/reconciliation")
