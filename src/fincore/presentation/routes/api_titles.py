"""Rotas: API JSON de títulos a pagar/receber (/api/v1/titles)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from robyn import Request, Response

from fincore.application.queries import get_title, title_settlements, titles_api_query
from fincore.application.titles import cancel_title, create_titles, settle_title, title_balances
from fincore.domain.money import parse_api_amount
from fincore.infrastructure.models import FinancialTitle, TitleSettlement
from fincore.presentation.routes.api import (
    error_response,
    json_payload,
    json_response,
    pagination,
    require_csrf,
)
from fincore.presentation.web import CurrentUser, api_login_required


def title_json(title: FinancialTitle, balance: Decimal) -> dict[str, object]:
    return {
        "id": str(title.id),
        "type": title.title_type,
        "description": title.description,
        "status": title.status,
        "overdue": title.status in {"open", "partial"} and title.due_date < date.today(),
        "original_amount": str(title.original_amount),
        "open_balance": str(balance),
        "currency": title.currency,
        "issue_date": title.issue_date.isoformat(),
        "due_date": title.due_date.isoformat(),
        "payment_method": title.payment_method,
        "installment_number": title.installment_number,
        "installment_count": title.installment_count,
        "installment_group": str(title.installment_group) if title.installment_group else None,
        "counterparty_id": str(title.counterparty_id) if title.counterparty_id else None,
        "account_id": str(title.account_id),
        "category_id": str(title.category_id) if title.category_id else None,
        "document_number": title.document_number,
    }


def settlement_json(item: TitleSettlement) -> dict[str, object]:
    return {
        "id": str(item.id),
        "operation_key": str(item.operation_key),
        "paid_on": item.paid_on.isoformat(),
        "amount_applied": str(item.amount_applied),
        "discount": str(item.discount),
        "interest": str(item.interest),
        "fine": str(item.fine),
        "fees": str(item.fees),
        "net_amount": str(item.net_amount),
        "payment_method": item.payment_method,
        "account_id": str(item.account_id),
        "financial_transaction_id": str(item.financial_transaction_id),
    }


def _text(payload: dict[str, object], key: str, required: bool = True) -> str:
    value = payload.get(key, "" if not required else None)
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"Informe {key} como texto.")
    return value


def _uuid(payload: dict[str, object], key: str) -> UUID:
    try:
        return UUID(str(payload.get(key, "")))
    except ValueError:
        raise ValueError(f"{key} deve ser um UUID válido.") from None


def _date(payload: dict[str, object], key: str) -> date:
    try:
        return date.fromisoformat(_text(payload, key))
    except ValueError:
        raise ValueError(f"{key} deve ser uma data AAAA-MM-DD.") from None


def _optional_amount(payload: dict[str, object], key: str) -> Decimal:
    value = payload.get(key)
    if value is None:
        return Decimal("0.00")
    if not isinstance(value, str):
        raise ValueError(f"{key} deve ser string decimal.")
    if value.strip() in {"0", "0.0", "0.00"}:
        return Decimal("0.00")
    return parse_api_amount(value)


def _error(exc: ValueError) -> Response:
    message = str(exc)
    if "não encontrado" in message:
        return error_response("not_found", message, 404)
    if "já foi usada" in message:
        return error_response("idempotency_conflict", message, 409)
    return error_response("invalid_title", message, 400)


async def _owned_title(request: Request, current: CurrentUser) -> FinancialTitle | None:
    try:
        title_id = UUID(request.path_params["title_id"])
    except KeyError, ValueError:
        return None
    return await get_title(await current[1].organization, title_id)


def register_title_api_routes(app: object) -> None:
    @app.get("/api/v1/titles")
    @api_login_required
    async def api_titles(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        query = titles_api_query(
            await current[1].organization,
            title_type=request.query_params.get("type", None),
            status=request.query_params.get("status", None),
        )
        total = await query.count()
        titles = list(await query.offset(offset).limit(limit))
        balances = await title_balances(titles)
        items = [title_json(title, balances[title.id]) for title in titles]
        return json_response({"items": items, "limit": limit, "offset": offset, "total": total})

    @app.get("/api/v1/titles/:title_id")
    @api_login_required
    async def api_title_detail(request: Request, current: CurrentUser) -> Response:
        title = await _owned_title(request, current)
        if title is None:
            return error_response("not_found", "Título não encontrado.", 404)
        balances = await title_balances([title])
        data = title_json(title, balances[title.id])
        data["settlements"] = [settlement_json(item) for item in await title_settlements(title)]
        return json_response(data)

    @app.post("/api/v1/titles")
    @api_login_required
    async def api_create_titles(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            title_type = _text(payload, "type")
            installments = payload.get("installments", 1)
            if not isinstance(installments, int) or isinstance(installments, bool):
                raise ValueError("installments deve ser um número inteiro.")
            category = payload.get("category_id")
            titles = await create_titles(
                organization=await current[1].organization,
                user=current[0],
                operation_key=_uuid(payload, "operation_key"),
                title_type=title_type,
                description=_text(payload, "description"),
                counterparty_name=_text(payload, "counterparty_name"),
                party_type="supplier" if title_type == "payable" else "customer",
                account_id=_uuid(payload, "account_id"),
                category_id=UUID(str(category)) if category else None,
                amount=parse_api_amount(_text(payload, "amount")),
                issue_date=_date(payload, "issue_date"),
                due_date=_date(payload, "due_date"),
                payment_method=_text(payload, "payment_method"),
                payment_terms="installment" if installments > 1 else "cash",
                installment_count=installments,
                document_number=_text(payload, "document_number", required=False),
                notes=_text(payload, "notes", required=False),
            )
        except ValueError as exc:
            return _error(exc)
        balances = await title_balances(titles)
        return json_response(
            {"items": [title_json(title, balances[title.id]) for title in titles]}, 201
        )

    @app.post("/api/v1/titles/:title_id/settlements")
    @api_login_required
    async def api_settle_title(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        title = await _owned_title(request, current)
        if title is None:
            return error_response("not_found", "Título não encontrado.", 404)
        try:
            payload = json_payload(request)
            settlement = await settle_title(
                organization=await current[1].organization,
                user=current[0],
                title_id=title.id,
                operation_key=_uuid(payload, "operation_key"),
                amount_applied=parse_api_amount(_text(payload, "amount_applied")),
                paid_on=_date(payload, "paid_on"),
                account_id=_uuid(payload, "account_id"),
                payment_method=_text(payload, "payment_method"),
                discount=_optional_amount(payload, "discount"),
                interest=_optional_amount(payload, "interest"),
                fine=_optional_amount(payload, "fine"),
                fees=_optional_amount(payload, "fees"),
                proof_reference=_text(payload, "proof_reference", required=False),
                notes=_text(payload, "notes", required=False),
            )
        except ValueError as exc:
            return _error(exc)
        if settlement.title_id != title.id:
            return error_response(
                "idempotency_conflict", "A chave já foi usada para outra baixa.", 409
            )
        return json_response(settlement_json(settlement), 201)

    @app.post("/api/v1/titles/:title_id/cancel")
    @api_login_required
    async def api_cancel_title(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        title = await _owned_title(request, current)
        if title is None:
            return error_response("not_found", "Título não encontrado.", 404)
        try:
            title = await cancel_title(
                organization=await current[1].organization, title_id=title.id
            )
        except ValueError as exc:
            return error_response("invalid_state", str(exc), 409)
        return json_response(title_json(title, (await title_balances([title]))[title.id]))
