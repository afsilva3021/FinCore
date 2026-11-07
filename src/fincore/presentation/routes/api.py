"""Rotas: API JSON /api/v1."""

import json
import secrets
from datetime import date
from uuid import UUID

from robyn import Request, Response
from tortoise.exceptions import IntegrityError

from fincore.application.categorization import suggest_category
from fincore.application.finance import (
    create_account,
    create_category,
    create_movement,
    create_transfer,
    get_account_balances,
    parse_opening_balance,
    reverse_movement,
    settle_movement,
)
from fincore.application.queries import (
    active_categories_query,
    get_movement,
    movement_by_operation_key,
    transactions_query,
    transfer_leg,
)
from fincore.domain.money import parse_api_amount
from fincore.infrastructure.models import (
    FinancialTransaction,
)
from fincore.presentation.health import database_is_ready
from fincore.presentation.web import (
    COOKIE_CSRF,
    CSRF_TTL_SECONDS,
    CurrentUser,
    _current_user,
    _set_cookie,
    _valid_csrf,
    api_login_required,
    csrf_serializer,
)


def json_response(data: object, status: int = 200) -> Response:
    return Response(
        status_code=status,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        },
        body=json.dumps(data, ensure_ascii=False),
    )


def pagination(request: Request) -> tuple[int, int]:
    try:
        limit = int(request.query_params.get("limit", "50"))
        offset = int(request.query_params.get("offset", "0"))
    except ValueError as exc:
        raise ValueError from exc
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError
    return limit, offset


def json_payload(request: Request) -> dict[str, object]:
    raw = request.body.decode() if isinstance(request.body, bytes) else request.body
    try:
        payload = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Envie um corpo JSON válido.") from exc
    if not isinstance(payload, dict):
        raise ValueError("O corpo JSON deve ser um objeto.")
    return payload


def require_csrf(request: Request) -> bool:
    return _valid_csrf(request, request.headers.get("x-csrf-token") or "")


def error_response(code: str, message: str, status: int) -> Response:
    return json_response({"error": {"code": code, "message": message}}, status)


def movement_json(item: FinancialTransaction) -> dict[str, object]:
    return {
        "id": str(item.id),
        "type": item.transaction_type,
        "description": item.description,
        "amount": str(item.amount),
        "currency": item.currency,
        "status": item.status,
        "competence_date": item.competence_date.isoformat(),
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "settled_at": item.settled_at.isoformat() if item.settled_at else None,
        "settled_by_id": str(item.settled_by_id) if item.settled_by_id else None,
        "account_id": str(item.account_id),
        "category_id": str(item.category_id) if item.category_id else None,
        "transfer_id": str(item.transfer_id) if item.transfer_id else None,
        "reversal_of_id": str(item.reversal_of_id) if item.reversal_of_id else None,
    }


def register_api_routes(app: object) -> None:
    @app.get("/api/v1/health")
    async def api_health(request: Request) -> Response:
        if await database_is_ready():
            return json_response({"status": "ok", "version": "v1"})
        return json_response({"status": "unavailable", "version": "v1"}, 503)

    @app.get("/api/v1/session")
    async def api_session(request: Request) -> Response:
        if await _current_user(request) is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
        token = secrets.token_urlsafe(32)
        response = json_response({"csrf_token": token})
        _set_cookie(response, COOKIE_CSRF, csrf_serializer.dumps(token), CSRF_TTL_SECONDS)
        return response

    @app.get("/api/v1/accounts")
    @api_login_required
    async def api_accounts(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return json_response(
                {"error": {"code": "invalid_pagination", "message": "Paginação inválida."}}, 400
            )
        organization = await current[1].organization
        balances = await get_account_balances(organization)
        items = [
            {
                "id": str(account.id),
                "name": account.name,
                "type": account.account_type,
                "bank_name": account.bank_name,
                "bank_code": account.bank_code,
                "branch_number": account.branch_number,
                "account_number": account.account_number,
                "currency": account.currency,
                "balance": str(balance),
            }
            for account, balance in balances[offset : offset + limit]
        ]
        return json_response(
            {"items": items, "limit": limit, "offset": offset, "total": len(balances)}
        )

    @app.post("/api/v1/accounts")
    @api_login_required
    async def api_create_account(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            name = payload.get("name")
            account_type = payload.get("type")
            opening_balance = payload.get("opening_balance", "0.00")
            if not isinstance(name, str) or not isinstance(account_type, str):
                raise ValueError("Informe nome e tipo da conta.")
            if not isinstance(opening_balance, str):
                raise ValueError("opening_balance deve ser uma string decimal.")
            account_details = {}
            for field_name in ("bank_name", "bank_code", "branch_number", "account_number"):
                value = payload.get(field_name)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"{field_name} deve ser texto.")
                account_details[field_name] = value
            account = await create_account(
                await current[1].organization,
                current[0],
                name.strip(),
                account_type,
                parse_opening_balance(opening_balance),
                **account_details,
            )
        except ValueError as exc:
            return error_response("invalid_account", str(exc), 400)
        except IntegrityError:
            return error_response("account_conflict", "Já existe uma conta com esse nome.", 409)
        return json_response(
            {
                "id": str(account.id),
                "name": account.name,
                "type": account.account_type,
                "bank_name": account.bank_name,
                "bank_code": account.bank_code,
                "branch_number": account.branch_number,
                "account_number": account.account_number,
                "currency": account.currency,
                "opening_balance": str(account.opening_balance),
            },
            201,
        )

    @app.post("/api/v1/categories")
    @api_login_required
    async def api_create_category(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            name = payload.get("name")
            transaction_type = payload.get("transaction_type")
            category_kind = payload.get("category_kind", "analytic")
            parent_value = payload.get("parent_id")
            if not isinstance(name, str) or not isinstance(transaction_type, str):
                raise ValueError("Informe nome e transaction_type.")
            if not isinstance(category_kind, str):
                raise ValueError("category_kind deve ser synthetic ou analytic.")
            category = await create_category(
                await current[1].organization,
                current[0],
                name.strip(),
                transaction_type,
                category_kind,
                UUID(str(parent_value)) if parent_value else None,
            )
        except ValueError as exc:
            return error_response("invalid_category", str(exc), 400)
        except IntegrityError:
            return error_response("category_conflict", "Essa categoria já existe.", 409)
        return json_response(
            {
                "id": str(category.id),
                "name": category.name,
                "transaction_type": category.transaction_type,
                "category_kind": category.category_kind,
                "parent_id": str(category.parent_id) if category.parent_id else None,
            },
            201,
        )

    @app.get("/api/v1/categories/suggestion")
    @api_login_required
    async def api_category_suggestion(request: Request, current: CurrentUser) -> Response:
        """Sugere a categoria pela descrição, aprendendo com os lançamentos da organização."""
        description = (request.query_params.get("description", "") or "")[:240]
        transaction_type = request.query_params.get("type", "") or ""
        result = await suggest_category(
            await current[1].organization, description, transaction_type
        )
        if result is None:
            return json_response({"suggestion": None})
        category, suggestion = result
        return json_response(
            {
                "suggestion": {
                    "category_id": str(category.id),
                    "name": category.name,
                    "confidence": suggestion.confidence,
                    "examples": suggestion.examples,
                }
            }
        )

    @app.get("/api/v1/categories")
    @api_login_required
    async def api_categories(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        organization = await current[1].organization
        query = active_categories_query(organization)
        total = await query.count()
        categories = await query.offset(offset).limit(limit)
        items = [
            {
                "id": str(category.id),
                "name": category.name,
                "transaction_type": category.transaction_type,
                "category_kind": category.category_kind,
                "parent_id": str(category.parent_id) if category.parent_id else None,
                "ledger_account_id": str(category.ledger_account_id)
                if category.ledger_account_id
                else None,
            }
            for category in categories
        ]
        return json_response({"items": items, "limit": limit, "offset": offset, "total": total})

    @app.get("/api/v1/transactions")
    @api_login_required
    async def api_transactions(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return json_response(
                {"error": {"code": "invalid_pagination", "message": "Paginação inválida."}}, 400
            )
        query = transactions_query(
            await current[1].organization,
            status=request.query_params.get("status", None),
            transaction_type=request.query_params.get("type", None),
        )
        total = await query.count()
        movements = await query.offset(offset).limit(limit)
        items = [
            {
                "id": str(item.id),
                "type": item.transaction_type,
                "description": item.description,
                "amount": str(item.amount),
                "currency": item.currency,
                "status": item.status,
                "competence_date": item.competence_date.isoformat(),
                "due_date": item.due_date.isoformat() if item.due_date else None,
                "settled_at": item.settled_at.isoformat() if item.settled_at else None,
                "settled_by_id": str(item.settled_by_id) if item.settled_by_id else None,
                "account_id": str(item.account_id),
                "category_id": str(item.category_id) if item.category_id else None,
                "transfer_id": str(item.transfer_id) if item.transfer_id else None,
            }
            for item in movements
        ]
        return json_response({"items": items, "limit": limit, "offset": offset, "total": total})

    @app.get("/api/v1/transactions/:transaction_id")
    @api_login_required
    async def api_transaction_detail(request: Request, current: CurrentUser) -> Response:
        try:
            transaction_id = UUID(request.path_params["transaction_id"])
        except ValueError, KeyError:
            return error_response("invalid_id", "Identificador inválido.", 400)
        movement = await get_movement(await current[1].organization, transaction_id)
        if movement is None:
            return error_response("not_found", "Movimentação não encontrada.", 404)
        return json_response(movement_json(movement))

    @app.post("/api/v1/transactions")
    @api_login_required
    async def api_create_transaction(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        organization = await current[1].organization
        try:
            payload = json_payload(request)
            operation_key = UUID(str(payload.get("operation_key", "")))
            transaction_type = payload.get("type")
            account_id = UUID(str(payload.get("account_id", "")))
            description = payload.get("description")
            amount_value = payload.get("amount")
            competence_value = payload.get("competence_date")
            if not isinstance(description, str) or not isinstance(amount_value, str):
                raise ValueError("Informe descrição e amount como string decimal.")
            if not isinstance(competence_value, str):
                raise ValueError("Informe competence_date no formato AAAA-MM-DD.")
            amount = parse_api_amount(amount_value)
            competence_date = date.fromisoformat(competence_value)
            if transaction_type == "transfer":
                target_id = UUID(str(payload.get("target_account_id", "")))
                existing = await movement_by_operation_key(operation_key)
                if existing:
                    incoming = await transfer_leg(organization, existing.transfer_id, "transfer_in")
                    if (
                        existing.organization_id != organization.id
                        or existing.transaction_type != "transfer_out"
                        or existing.account_id != account_id
                        or incoming is None
                        or incoming.account_id != target_id
                        or existing.amount != amount
                        or existing.description != description
                        or existing.competence_date != competence_date
                    ):
                        return error_response(
                            "idempotency_conflict",
                            "A chave já foi usada com dados diferentes.",
                            409,
                        )
                    return json_response(movement_json(existing), 200)
                transfer_id = await create_transfer(
                    organization=organization,
                    user=current[0],
                    operation_key=operation_key,
                    source_id=account_id,
                    target_id=target_id,
                    amount=amount,
                    description=description.strip(),
                    competence_date=competence_date,
                )
                movement = await transfer_leg(organization, transfer_id, "transfer_out")
                incoming = await transfer_leg(organization, transfer_id, "transfer_in")
                if (
                    movement.account_id != account_id
                    or incoming is None
                    or incoming.account_id != target_id
                    or movement.amount != amount
                    or movement.description != description.strip()
                    or movement.competence_date != competence_date
                ):
                    return error_response(
                        "idempotency_conflict",
                        "A chave já foi usada com dados diferentes.",
                        409,
                    )
            elif transaction_type in {"income", "expense"}:
                category_value = payload.get("category_id")
                category_id = UUID(str(category_value)) if category_value else None
                due_value = payload.get("due_date")
                if due_value is not None and not isinstance(due_value, str):
                    raise ValueError("due_date deve ser uma data ISO ou null.")
                due_date = (
                    date.fromisoformat(due_value)
                    if isinstance(due_value, str) and due_value
                    else None
                )
                settled = payload.get("settled", False)
                if not isinstance(settled, bool):
                    raise ValueError("settled deve ser booleano.")
                existing = await movement_by_operation_key(operation_key)
                if existing:
                    if (
                        existing.organization_id != organization.id
                        or existing.transaction_type != transaction_type
                        or existing.account_id != account_id
                        or existing.category_id != category_id
                        or existing.amount != amount
                        or existing.description != description.strip()
                        or existing.competence_date != competence_date
                        or existing.due_date != due_date
                        or existing.status != ("settled" if settled else "open")
                    ):
                        return error_response(
                            "idempotency_conflict",
                            "A chave já foi usada com dados diferentes.",
                            409,
                        )
                    return json_response(movement_json(existing), 200)
                movement = await create_movement(
                    organization=organization,
                    user=current[0],
                    operation_key=operation_key,
                    account_id=account_id,
                    category_id=category_id,
                    transaction_type=transaction_type,
                    description=description.strip(),
                    amount=amount,
                    competence_date=competence_date,
                    due_date=due_date,
                    settled=settled,
                )
                if (
                    movement.organization_id != organization.id
                    or movement.transaction_type != transaction_type
                    or movement.account_id != account_id
                    or movement.category_id != category_id
                    or movement.amount != amount
                    or movement.description != description.strip()
                    or movement.competence_date != competence_date
                    or movement.due_date != due_date
                    or movement.status != ("settled" if settled else "open")
                ):
                    return error_response(
                        "idempotency_conflict",
                        "A chave já foi usada com dados diferentes.",
                        409,
                    )
            else:
                raise ValueError("type deve ser income, expense ou transfer.")
        except (ValueError, TypeError) as exc:
            if "chave de operação já foi utilizada" in str(exc).casefold():
                return error_response(
                    "idempotency_conflict",
                    "A chave já foi usada por outra operação.",
                    409,
                )
            return error_response("invalid_transaction", str(exc), 400)
        except IntegrityError:
            return error_response(
                "transaction_conflict", "Conflito ao registrar movimentação.", 409
            )
        return json_response(movement_json(movement), 201)

    @app.post("/api/v1/transactions/:transaction_id/settle")
    @api_login_required
    async def api_settle_transaction(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            transaction_id = UUID(request.path_params["transaction_id"])
        except ValueError, KeyError:
            return error_response("invalid_id", "Identificador inválido.", 400)
        organization = await current[1].organization
        movement = await get_movement(organization, transaction_id)
        if movement is None:
            return error_response("not_found", "Movimentação não encontrada.", 404)
        if movement.status == "open":
            await settle_movement(organization, current[0], movement.id)
            movement = await get_movement(organization, movement.id)
        return json_response(movement_json(movement))

    @app.post("/api/v1/transactions/:transaction_id/reverse")
    @api_login_required
    async def api_reverse_transaction(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            transaction_id = UUID(request.path_params["transaction_id"])
            operation_key = UUID(str(payload.get("operation_key", "")))
            raw_date = payload.get("competence_date", date.today().isoformat())
            if not isinstance(raw_date, str):
                raise ValueError("competence_date deve ser uma data ISO.")
            organization = await current[1].organization
            original = await get_movement(organization, transaction_id)
            if original is None:
                return error_response("not_found", "Movimentação não encontrada.", 404)
            canonical_original = original
            if original.transaction_type == "transfer_in":
                canonical_original = await transfer_leg(
                    organization, original.transfer_id, "transfer_out"
                )
                if canonical_original is None:
                    return error_response("invalid_transfer", "Transferência incompleta.", 409)
            replay = await movement_by_operation_key(operation_key, organization)
            if replay is not None:
                if replay.reversal_of_id != canonical_original.id:
                    return error_response(
                        "idempotency_conflict",
                        "A chave já foi usada para outra operação.",
                        409,
                    )
                return json_response(movement_json(replay), 200)
            movement = await reverse_movement(
                organization,
                current[0],
                transaction_id,
                operation_key,
                date.fromisoformat(raw_date),
            )
        except (ValueError, KeyError, TypeError) as exc:
            if "já possui estorno" in str(exc) or "liquidadas podem ser estornadas" in str(exc):
                return error_response("reversal_conflict", str(exc), 409)
            return error_response("invalid_reversal", str(exc), 400)
        except IntegrityError:
            return error_response("reversal_conflict", "Esta movimentação já foi estornada.", 409)
        return json_response(movement_json(movement), 201)
