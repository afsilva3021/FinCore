"""Rotas: Contas, categorias, transações e títulos a pagar/receber."""

from datetime import date
from uuid import UUID

from robyn import Request, Response
from tortoise.exceptions import IntegrityError

from fincore.application.finance import (
    create_account,
    create_category,
    create_movement,
    create_transfer,
    parse_money,
    parse_opening_balance,
    reverse_movement,
    set_account_active,
    settle_movement,
    update_account,
)
from fincore.application.queries import (
    get_account,
)
from fincore.application.titles import (
    cancel_title,
    create_titles,
    parse_adjustment,
    settle_title,
)
from fincore.presentation.web import (
    CurrentUser,
    _current_user,
    _form_data,
    _form_date,
    _form_uuid,
    _redirect,
    _render_accounts,
    _render_categories,
    _render_title_register,
    _render_transactions,
    _response,
    _title_from_path,
    _valid_csrf,
    login_required,
)


def register_finance_routes(app: object) -> None:
    @app.get("/accounts")
    @login_required
    async def accounts_page(request: Request, current: CurrentUser) -> Response:
        return _response(await _render_accounts(request, current[0], await current[1].organization))

    @app.post("/accounts")
    @login_required
    async def create_account_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounts")
        try:
            opening_balance = parse_opening_balance(form.get("opening_balance", ""))
            account_type = form.get("account_type", "")
            bank_code = form.get("bank_code", "").strip()
            branch_number = form.get("branch_number", "").strip()
            account_number = form.get("account_number", "").strip()
            if account_type == "bank" and not all((bank_code, branch_number, account_number)):
                raise ValueError("Informe código do banco, agência com dígito e número da conta.")
            await create_account(
                organization,
                current[0],
                form.get("name", "").strip(),
                account_type,
                opening_balance,
                form.get("bank_name", ""),
                bank_code,
                branch_number,
                account_number,
            )
        except ValueError as exc:
            return _response(
                await _render_accounts(request, current[0], organization, str(exc)), 400
            )
        except IntegrityError:
            return _response(
                await _render_accounts(
                    request, current[0], organization, "Já existe uma conta com esse nome."
                ),
                409,
            )
        return _redirect("/accounts")

    @app.get("/accounts/:account_id/edit")
    @login_required
    async def edit_account_page(request: Request, current: CurrentUser) -> Response:
        try:
            account_id = UUID(request.path_params["account_id"])
        except ValueError, KeyError:
            return _redirect("/accounts")
        organization = await current[1].organization
        account = await get_account(organization, account_id)
        if account is None:
            return _redirect("/accounts")
        return _response(
            await _render_accounts(request, current[0], organization, editing_account=account)
        )

    @app.post("/accounts/:account_id/update")
    @login_required
    async def update_account_page(request: Request, current: CurrentUser) -> Response:
        try:
            account_id = UUID(request.path_params["account_id"])
        except ValueError, KeyError:
            return _redirect("/accounts")
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounts")
        organization = await current[1].organization
        account = await get_account(organization, account_id)
        if account is None:
            return _redirect("/accounts")
        try:
            await update_account(
                organization,
                account_id,
                form.get("name", "").strip(),
                form.get("account_type", ""),
                form.get("bank_name", ""),
                form.get("bank_code", ""),
                form.get("branch_number", ""),
                form.get("account_number", ""),
            )
        except ValueError as exc:
            return _response(
                await _render_accounts(request, current[0], organization, str(exc), account, form),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_accounts(
                    request,
                    current[0],
                    organization,
                    "Já existe uma conta com esse nome.",
                    account,
                    form,
                ),
                409,
            )
        return _redirect("/accounts")

    @app.post("/accounts/:account_id/active")
    @login_required
    async def set_account_active_page(request: Request, current: CurrentUser) -> Response:
        try:
            account_id = UUID(request.path_params["account_id"])
        except ValueError, KeyError:
            return _redirect("/accounts")
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounts")
        if form.get("is_active") not in {"true", "false"}:
            return _redirect("/accounts")
        try:
            await set_account_active(
                await current[1].organization,
                account_id,
                form["is_active"] == "true",
            )
        except ValueError:
            pass
        return _redirect("/accounts")

    @app.get("/categories")
    @login_required
    async def categories_page(request: Request, current: CurrentUser) -> Response:
        return _response(
            await _render_categories(request, current[0], await current[1].organization)
        )

    @app.post("/categories")
    @login_required
    async def create_category_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/categories")
        try:
            await create_category(
                organization,
                current[0],
                form.get("name", "").strip(),
                form.get("transaction_type", ""),
                form.get("category_kind", "analytic"),
                UUID(form["parent_id"]) if form.get("parent_id") else None,
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_categories(
                    request,
                    current[0],
                    organization,
                    str(exc) or "Selecione uma categoria superior válida.",
                    form,
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_categories(
                    request,
                    current[0],
                    organization,
                    "Essa categoria já existe para esse tipo.",
                    form,
                ),
                409,
            )
        return _redirect("/categories")

    @app.get("/transactions")
    @login_required
    async def transactions_page(request: Request, current: CurrentUser) -> Response:
        default_type = request.query_params.get("type", "income")
        if default_type not in {"income", "expense", "transfer"}:
            default_type = "income"
        return _response(
            await _render_transactions(
                request,
                current[0],
                await current[1].organization,
                default_type=default_type,
            )
        )

    @app.get("/payables")
    @login_required
    async def payables_page(request: Request, current: CurrentUser) -> Response:
        return _response(
            await _render_title_register(
                request, current[0], await current[1].organization, "payable"
            )
        )

    @app.get("/receivables")
    @login_required
    async def receivables_page(request: Request, current: CurrentUser) -> Response:
        return _response(
            await _render_title_register(
                request, current[0], await current[1].organization, "receivable"
            )
        )

    async def _submit_title_form(request: Request, title_type: str) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        target = "/payables" if title_type == "payable" else "/receivables"
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect(target)
        try:
            installment_count = int(form.get("installment_count", "1"))
            category_id = UUID(form["category_id"]) if form.get("category_id") else None
            await create_titles(
                organization=organization,
                user=current[0],
                title_type=title_type,
                description=form.get("description", ""),
                counterparty_name=form.get("counterparty_name", ""),
                party_type=form.get(
                    "party_type", "supplier" if title_type == "payable" else "customer"
                ),
                account_id=UUID(form.get("account_id", "")),
                category_id=category_id,
                amount=parse_money(form.get("amount", "")),
                issue_date=date.fromisoformat(form.get("issue_date", "")),
                due_date=date.fromisoformat(form.get("due_date", "")),
                payment_method=form.get("payment_method", "other"),
                payment_terms=form.get("payment_terms", "cash"),
                installment_count=installment_count,
                document_number=form.get("document_number", ""),
                document_type=form.get("document_type", ""),
                document_series=form.get("document_series", ""),
                cost_center=form.get("cost_center", ""),
                project=form.get("project", ""),
                expected_receipt_date=(
                    date.fromisoformat(form["expected_receipt_date"])
                    if form.get("expected_receipt_date")
                    else None
                ),
                collection_sent_date=(
                    date.fromisoformat(form["collection_sent_date"])
                    if form.get("collection_sent_date")
                    else None
                ),
                barcode=form.get("barcode", ""),
                beneficiary=form.get("beneficiary", ""),
                approver_reference=form.get("approver_reference", ""),
                payment_details=form.get("payment_details", ""),
                related_reference=form.get("related_reference", ""),
                charge_reference=form.get("charge_reference", ""),
                attachment_reference=form.get("attachment_reference", ""),
                notes=form.get("notes", ""),
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_title_register(
                    request,
                    current[0],
                    organization,
                    title_type,
                    str(exc) or "Confira os dados do título.",
                    form,
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_title_register(
                    request,
                    current[0],
                    organization,
                    title_type,
                    "Não foi possível salvar o título; confira os dados e tente novamente.",
                    form,
                ),
                409,
            )
        return _redirect(target)

    @app.post("/payables")
    async def create_payable_title(request: Request) -> Response:
        return await _submit_title_form(request, "payable")

    @app.post("/receivables")
    async def create_receivable_title(request: Request) -> Response:
        return await _submit_title_form(request, "receivable")

    @app.post("/titles/:title_id/settlements")
    @login_required
    async def title_settlement_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        title = await _title_from_path(request, organization)
        title_type = title.title_type if title else "payable"
        target = "/payables" if title_type == "payable" else "/receivables"
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect(target)
        try:
            if title is None:
                raise ValueError("Título não encontrado nesta organização.")
            await settle_title(
                organization=organization,
                user=current[0],
                title_id=title.id,
                operation_key=_form_uuid(
                    form, "operation_key", "Atualize a página e tente a baixa novamente."
                ),
                amount_applied=parse_money(form.get("amount_applied", "")),
                paid_on=_form_date(form, "paid_on", "Informe uma data de pagamento válida."),
                account_id=_form_uuid(
                    form, "account_id", "Selecione uma conta financeira válida para a baixa."
                ),
                payment_method=form.get("payment_method", "other"),
                discount=parse_adjustment(form.get("discount", "0")),
                interest=parse_adjustment(form.get("interest", "0")),
                fine=parse_adjustment(form.get("fine", "0")),
                fees=parse_adjustment(form.get("fees", "0")),
                proof_reference=form.get("proof_reference", ""),
                notes=form.get("notes", ""),
            )
            return _redirect(target)
        except ValueError as exc:
            return _response(
                await _render_title_register(
                    request, current[0], organization, title_type, str(exc)
                ),
                400,
            )

    @app.post("/titles/:title_id/cancel")
    @login_required
    async def cancel_title_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        title = await _title_from_path(request, organization)
        title_type = title.title_type if title else "payable"
        target = "/payables" if title_type == "payable" else "/receivables"
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect(target)
        try:
            if title is None:
                raise ValueError("Título não encontrado nesta organização.")
            await cancel_title(organization=organization, title_id=title.id)
            return _redirect(target)
        except ValueError as exc:
            return _response(
                await _render_title_register(
                    request, current[0], organization, title_type, str(exc)
                ),
                400,
            )

    @app.post("/transactions")
    @login_required
    async def create_transaction_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/transactions")
        try:
            operation_key = UUID(form.get("operation_key", ""))
            account_id = UUID(form.get("account_id", ""))
            competence_date = date.fromisoformat(form.get("competence_date", ""))
            amount = parse_money(form.get("amount", ""))
            transaction_type = form.get("transaction_type", "")
            if transaction_type == "transfer":
                await create_transfer(
                    organization=organization,
                    user=current[0],
                    operation_key=operation_key,
                    source_id=account_id,
                    target_id=UUID(form.get("target_account_id", "")),
                    amount=amount,
                    description=form.get("description", "").strip(),
                    competence_date=competence_date,
                )
            else:
                category_id = UUID(form["category_id"]) if form.get("category_id") else None
                due_date = date.fromisoformat(form["due_date"]) if form.get("due_date") else None
                await create_movement(
                    organization=organization,
                    user=current[0],
                    operation_key=operation_key,
                    account_id=account_id,
                    category_id=category_id,
                    transaction_type=transaction_type,
                    description=form.get("description", "").strip(),
                    amount=amount,
                    competence_date=competence_date,
                    due_date=due_date,
                    settled=form.get("status") == "settled",
                )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_transactions(request, current[0], organization, str(exc), form),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_transactions(
                    request,
                    current[0],
                    organization,
                    "Não foi possível salvar; confira se a movimentação já foi enviada.",
                    form,
                ),
                409,
            )
        return _redirect("/transactions")

    @app.post("/transactions/:transaction_id/settle")
    @login_required
    async def settle_transaction_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/transactions")
        try:
            movement_id = UUID(request.path_params["transaction_id"])
        except ValueError, KeyError:
            return _redirect("/transactions")
        await settle_movement(await current[1].organization, current[0], movement_id)
        return_to = form.get("return_to", "")
        if return_to not in {"/payables", "/receivables"}:
            return_to = "/transactions"
        return _redirect(return_to)

    @app.post("/transactions/:transaction_id/reverse")
    @login_required
    async def reverse_transaction_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/transactions")
        try:
            await reverse_movement(
                organization,
                current[0],
                UUID(request.path_params["transaction_id"]),
                UUID(form.get("operation_key", "")),
                date.today(),
            )
        except (ValueError, KeyError, IntegrityError) as exc:
            return _response(
                await _render_transactions(
                    request,
                    current[0],
                    organization,
                    str(exc) or "Não foi possível estornar esta movimentação.",
                ),
                400,
            )
        return _redirect("/transactions")
