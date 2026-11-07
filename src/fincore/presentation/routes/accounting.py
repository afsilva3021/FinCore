"""Rotas: Plano de contas e diário contábil."""

from datetime import date
from uuid import UUID

from robyn import Request, Response
from tortoise.exceptions import IntegrityError

from fincore.application.accounting import (
    create_journal_entry,
    create_ledger_account,
    post_journal_entry,
    reverse_journal_entry,
    update_draft_journal_entry,
)
from fincore.presentation.web import (
    CurrentUser,
    _form_data,
    _journal_lines_from_form,
    _redirect,
    _render_accounting,
    _response,
    _valid_csrf,
    login_required,
)


def register_accounting_routes(app: object) -> None:
    @app.get("/accounting")
    @login_required
    async def accounting_page(request: Request, current: CurrentUser) -> Response:
        return _response(
            await _render_accounting(request, current[0], await current[1].organization)
        )

    @app.post("/accounting/accounts")
    @login_required
    async def create_ledger_account_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            await create_ledger_account(
                organization,
                current[0],
                form.get("code", "").strip(),
                form.get("name", "").strip(),
                form.get("account_type", ""),
                UUID(form["parent_id"]) if form.get("parent_id") else None,
                form.get("account_kind", "analytic") == "synthetic",
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(
                    request,
                    current[0],
                    organization,
                    str(exc) or "Selecione uma conta superior válida.",
                    open_drawer="account",
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_accounting(
                    request,
                    current[0],
                    organization,
                    "Este código contábil já está em uso.",
                    open_drawer="account",
                ),
                409,
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals")
    @login_required
    async def create_journal_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            entry = await create_journal_entry(
                organization=organization,
                user=current[0],
                idempotency_key=UUID(form.get("idempotency_key", "")),
                entry_date=date.fromisoformat(form.get("entry_date", "")),
                description=form.get("description", "").strip(),
                lines=_journal_lines_from_form(form),
            )
            if form.get("action") == "post":
                await post_journal_entry(organization, entry.id)
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(
                    request, current[0], organization, str(exc), open_drawer="entry"
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_accounting(
                    request,
                    current[0],
                    organization,
                    "Não foi possível salvar o lançamento.",
                    open_drawer="entry",
                ),
                409,
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals/:entry_id/update")
    @login_required
    async def update_draft_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            entry = await update_draft_journal_entry(
                organization=organization,
                entry_id=UUID(request.path_params["entry_id"]),
                entry_date=date.fromisoformat(form.get("entry_date", "")),
                description=form.get("description", ""),
                lines=_journal_lines_from_form(form),
            )
            if form.get("action") == "post":
                await post_journal_entry(organization, entry.id)
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(request, current[0], organization, str(exc)), 400
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals/:entry_id/post")
    @login_required
    async def post_journal_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            await post_journal_entry(organization, UUID(request.path_params["entry_id"]))
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(request, current[0], organization, str(exc)), 400
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals/:entry_id/reverse")
    @login_required
    async def reverse_journal_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            await reverse_journal_entry(
                organization, current[0], UUID(request.path_params["entry_id"])
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(request, current[0], organization, str(exc)), 400
            )
        return _redirect("/accounting")
