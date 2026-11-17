"""Rotas: estoque (posição, movimentações, transferências e estornos)."""

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from robyn import Request, Response

from fincore.application.inventory import (
    OperationKeyConflict,
    register_stock_movement,
    reverse_stock_movement,
    transfer_stock,
)
from fincore.application.queries import (
    get_stock_movement,
    low_stock_products,
    stock_balances_query,
    stock_movement_flags,
    stock_movements_query,
    stock_products,
    stock_totals,
    warehouses,
)
from fincore.domain.inventory import (
    ADJUSTMENT,
    IN,
    OPENING,
    OUT,
    Position,
    average_cost,
    reason_label,
)
from fincore.domain.products import parse_api_quantity, parse_quantity
from fincore.infrastructure.models import Organization, StockBalance, StockMovement, User
from fincore.presentation.routes.api import (
    error_response,
    json_payload,
    json_response,
    pagination,
    require_csrf,
)
from fincore.presentation.web import (
    CurrentUser,
    _csrf_from_auth,
    _form_data,
    _form_date,
    _form_uuid,
    _page,
    _paginate,
    _query,
    _redirect,
    _response,
    _valid_csrf,
    api_login_required,
    login_required,
)

# Tipo escolhido no formulário/API -> (sentido, motivo). Transferência é tratada à parte.
KINDS = {
    "opening": (IN, OPENING),
    "entry": (IN, ADJUSTMENT),
    "exit": (OUT, ADJUSTMENT),
}
KIND_LABELS = {
    "opening": "Saldo inicial",
    "entry": "Entrada (ajuste ou sobra)",
    "exit": "Saída (perda, quebra ou consumo)",
    "transfer": "Transferência entre depósitos",
}
SUCCESS = {
    "opening": "Saldo inicial registrado.",
    "entry": "Entrada registrada.",
    "exit": "Saída registrada.",
    "transfer": "Transferência registrada.",
}


def _query_uuid(request: Request, name: str) -> UUID | None:
    try:
        return UUID(_query(request).get(name, "") or "")
    except ValueError:
        return None


def _path_uuid(request: Request, name: str) -> UUID | None:
    try:
        return UUID(request.path_params[name])
    except ValueError, KeyError:
        return None


def movement_json(item: StockMovement) -> dict[str, object]:
    return {
        "id": str(item.id),
        "product_id": str(item.product_id),
        "warehouse_id": str(item.warehouse_id),
        "sequence": item.sequence,
        "direction": item.direction,
        "reason": item.reason,
        "quantity": str(item.quantity),
        "unit_cost": str(item.unit_cost),
        "total_cost": str(item.total_cost),
        "balance_quantity": str(item.balance_quantity),
        "balance_value": str(item.balance_value),
        "occurred_on": item.occurred_on.isoformat(),
        "notes": item.notes,
        "transfer_id": str(item.transfer_id) if item.transfer_id else None,
        "reversal_of_id": str(item.reversal_of_id) if item.reversal_of_id else None,
        "journal_entry_id": str(item.journal_entry_id) if item.journal_entry_id else None,
    }


def balance_json(item: StockBalance) -> dict[str, object]:
    return {
        "product_id": str(item.product_id),
        "sku": item.product.sku,
        "product_name": item.product.name,
        "unit": item.product.unit.code,
        "warehouse_id": str(item.warehouse_id),
        "warehouse_code": item.warehouse.code,
        "quantity": str(item.quantity),
        "average_cost": str(average_cost(Position(item.quantity, item.total_value))),
        "total_value": str(item.total_value),
    }


async def _submit_movement(
    *,
    organization: Organization,
    user: User,
    kind: str,
    operation_key: UUID,
    product_id: UUID,
    warehouse_id: UUID,
    destination_warehouse_id: UUID | None,
    quantity: Decimal,
    unit_cost: Decimal,
    occurred_on: date,
    notes: str,
) -> tuple[list[StockMovement], bool]:
    """Executa a movimentação pedida pela página ou pela API."""
    if kind == "transfer":
        if destination_warehouse_id is None:
            raise ValueError("Selecione o depósito de destino.")
        out_leg, in_leg, created = await transfer_stock(
            organization=organization,
            user=user,
            operation_key=operation_key,
            product_id=product_id,
            source_warehouse_id=warehouse_id,
            destination_warehouse_id=destination_warehouse_id,
            quantity=quantity,
            occurred_on=occurred_on,
            notes=notes,
        )
        return [out_leg, in_leg], created
    if kind not in KINDS:
        raise ValueError("Selecione um tipo de movimentação válido.")
    direction, reason = KINDS[kind]
    movement, created = await register_stock_movement(
        organization=organization,
        user=user,
        operation_key=operation_key,
        direction=direction,
        reason=reason,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=quantity,
        unit_cost=unit_cost if direction == IN else Decimal("0"),
        occurred_on=occurred_on,
        notes=notes,
    )
    return [movement], created


def register_inventory_routes(app: object) -> None:
    # --- Posição de estoque -----------------------------------------------------------

    async def render_stock(
        request: Request,
        user: User,
        organization: Organization,
        *,
        form: dict[str, str] | None = None,
        error: str | None = None,
    ) -> Response:
        search = (_query(request).get("q", "") or "").strip()
        warehouse_id = _query_uuid(request, "warehouse")
        only_in_stock = _query(request).get("in_stock", "") == "1"
        balances, page_info = await _paginate(
            stock_balances_query(
                organization,
                search=search,
                warehouse_id=warehouse_id,
                only_in_stock=only_in_stock,
            ),
            request,
            keep=("q", "warehouse", "in_stock"),
        )
        total_value, positions = await stock_totals(organization)
        all_warehouses = await warehouses(organization)
        active_warehouses = [item for item in all_warehouses if item.is_active]
        default = next((item for item in active_warehouses if item.is_default), None)
        if form is None:
            form = {
                "kind": "entry",
                "operation_key": str(uuid4()),
                "warehouse_id": str(default.id) if default else "",
                "occurred_on": date.today().isoformat(),
            }
        body = _page(
            "inventory/stock.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            rows=[
                (item, average_cost(Position(item.quantity, item.total_value))) for item in balances
            ],
            pagination=page_info,
            total_value=total_value,
            positions=positions,
            low_stock=await low_stock_products(organization),
            warehouses=all_warehouses,
            active_warehouses=active_warehouses,
            products=await stock_products(organization),
            kinds=KIND_LABELS,
            search=search,
            warehouse_filter=str(warehouse_id) if warehouse_id else "",
            only_in_stock=only_in_stock,
            form=form,
            open_drawer=error is not None,
            error=error,
            message=_query(request).get("message", ""),
            today=date.today().isoformat(),
            active="stock",
            section="Estoque",
        )
        return _response(body, 400 if error else 200)

    @app.get("/stock")
    @login_required
    async def stock_page(request: Request, current: CurrentUser) -> Response:
        return await render_stock(request, current[0], await current[1].organization)

    @app.post("/stock/movements")
    @login_required
    async def create_stock_movement_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/stock")
        organization = await current[1].organization
        kind = form.get("kind", "")
        try:
            destination = form.get("destination_warehouse_id", "")
            await _submit_movement(
                organization=organization,
                user=current[0],
                kind=kind,
                operation_key=_form_uuid(
                    form, "operation_key", "Recarregue a página e tente novamente."
                ),
                product_id=_form_uuid(form, "product_id", "Selecione o produto."),
                warehouse_id=_form_uuid(form, "warehouse_id", "Selecione o depósito."),
                destination_warehouse_id=(
                    _form_uuid(form, "destination_warehouse_id", "Selecione o depósito de destino.")
                    if destination
                    else None
                ),
                quantity=parse_quantity(form.get("quantity", ""), label="a quantidade"),
                unit_cost=parse_quantity(form.get("unit_cost", ""), label="o custo unitário"),
                occurred_on=_form_date(form, "occurred_on", "Informe a data do movimento."),
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            return await render_stock(request, current[0], organization, form=form, error=str(exc))
        return _redirect(f"/stock?{urlencode({'message': SUCCESS[kind]})}")

    # --- Movimentações (kardex) -------------------------------------------------------

    async def render_movements(
        request: Request,
        user: User,
        organization: Organization,
        *,
        error: str | None = None,
    ) -> Response:
        product_id = _query_uuid(request, "product")
        warehouse_id = _query_uuid(request, "warehouse")
        movements, page_info = await _paginate(
            stock_movements_query(organization, product_id=product_id, warehouse_id=warehouse_id),
            request,
            keep=("product", "warehouse"),
        )
        reversed_ids, reversible_ids = await stock_movement_flags(organization, movements)
        body = _page(
            "inventory/movements.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            movements=movements,
            pagination=page_info,
            reversed_ids=reversed_ids,
            reversible_ids=reversible_ids,
            reason_label=reason_label,
            products=await stock_products(organization),
            warehouses=await warehouses(organization),
            product_filter=str(product_id) if product_id else "",
            warehouse_filter=str(warehouse_id) if warehouse_id else "",
            error=error,
            message=_query(request).get("message", ""),
            active="stock-movements",
            section="Movimentações de estoque",
        )
        return _response(body, 400 if error else 200)

    @app.get("/stock/movements")
    @login_required
    async def stock_movements_page(request: Request, current: CurrentUser) -> Response:
        return await render_movements(request, current[0], await current[1].organization)

    @app.post("/stock/movements/:movement_id/reverse")
    @login_required
    async def reverse_stock_movement_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/stock/movements")
        organization = await current[1].organization
        movement_id = _path_uuid(request, "movement_id")
        if movement_id is None or await get_stock_movement(organization, movement_id) is None:
            return _response("Movimento não encontrado.", 404)
        try:
            await reverse_stock_movement(
                organization=organization, user=current[0], movement_id=movement_id
            )
        except ValueError as exc:
            return await render_movements(request, current[0], organization, error=str(exc))
        return _redirect(f"/stock/movements?{urlencode({'message': 'Movimento estornado.'})}")

    # --- API JSON ---------------------------------------------------------------------

    @app.get("/api/v1/stock/balances")
    @api_login_required
    async def api_stock_balances(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        query = stock_balances_query(
            await current[1].organization,
            search=_query(request).get("q", "") or "",
            product_id=_query_uuid(request, "product_id"),
            warehouse_id=_query_uuid(request, "warehouse_id"),
        )
        total = await query.count()
        items = [balance_json(item) for item in await query.offset(offset).limit(limit)]
        return json_response({"items": items, "limit": limit, "offset": offset, "total": total})

    @app.get("/api/v1/stock/movements")
    @api_login_required
    async def api_stock_movements(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        query = stock_movements_query(
            await current[1].organization,
            product_id=_query_uuid(request, "product_id"),
            warehouse_id=_query_uuid(request, "warehouse_id"),
        )
        total = await query.count()
        items = [movement_json(item) for item in await query.offset(offset).limit(limit)]
        return json_response({"items": items, "limit": limit, "offset": offset, "total": total})

    @app.post("/api/v1/stock/movements")
    @api_login_required
    async def api_create_stock_movement(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            payload = json_payload(request)
            text = {}
            for field in (
                "type",
                "operation_key",
                "product_id",
                "warehouse_id",
                "destination_warehouse_id",
                "quantity",
                "unit_cost",
                "occurred_on",
                "notes",
            ):
                value = payload.get(field)
                if value is None:
                    value = ""
                if not isinstance(value, str):
                    raise ValueError(f"{field} deve ser texto.")
                text[field] = value

            def uuid_field(name: str) -> UUID:
                try:
                    return UUID(text[name])
                except ValueError:
                    raise ValueError(f"Informe {name} como UUID.") from None

            try:
                occurred_on = date.fromisoformat(text["occurred_on"])
            except ValueError:
                raise ValueError("Informe occurred_on no formato AAAA-MM-DD.") from None
            movements, created = await _submit_movement(
                organization=await current[1].organization,
                user=current[0],
                kind=text["type"],
                operation_key=uuid_field("operation_key"),
                product_id=uuid_field("product_id"),
                warehouse_id=uuid_field("warehouse_id"),
                destination_warehouse_id=(
                    uuid_field("destination_warehouse_id")
                    if text["destination_warehouse_id"]
                    else None
                ),
                quantity=parse_api_quantity(text["quantity"], label="quantity"),
                unit_cost=parse_api_quantity(text["unit_cost"] or "0", label="unit_cost"),
                occurred_on=occurred_on,
                notes=text["notes"],
            )
        except OperationKeyConflict as exc:
            return error_response("conflict", str(exc), 409)
        except ValueError as exc:
            return error_response("invalid_stock_movement", str(exc), 400)
        return json_response(
            {"items": [movement_json(item) for item in movements]}, 201 if created else 200
        )

    @app.post("/api/v1/stock/movements/:movement_id/reverse")
    @api_login_required
    async def api_reverse_stock_movement(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        organization = await current[1].organization
        movement_id = _path_uuid(request, "movement_id")
        if movement_id is None or await get_stock_movement(organization, movement_id) is None:
            return error_response("not_found", "Movimento não encontrado.", 404)
        try:
            reversals = await reverse_stock_movement(
                organization=organization, user=current[0], movement_id=movement_id
            )
        except ValueError as exc:
            return error_response("invalid_state", str(exc), 409)
        return json_response({"items": [movement_json(item) for item in reversals]}, 201)
