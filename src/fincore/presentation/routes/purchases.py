"""Rotas: compras (pedidos de compra, recebimentos e estorno de recebimento)."""

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from robyn import Request, Response

from fincore.application.purchases import (
    approve_purchase_order,
    cancel_purchase_order,
    create_purchase_order,
    receive_purchase_order,
    reverse_purchase_receipt,
)
from fincore.application.queries import (
    active_accounts,
    active_suppliers,
    document_titles,
    get_purchase_order,
    get_purchase_receipt,
    purchase_order_items,
    purchase_orders_query,
    purchase_receipts,
    stock_products,
    warehouses,
)
from fincore.domain.products import parse_quantity
from fincore.domain.purchases import STATUS_LABELS, STATUSES, line_total, order_total, remaining
from fincore.infrastructure.models import Organization, PurchaseOrder, User
from fincore.presentation.routes.api import error_response, json_response, pagination
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

PAYMENT_LABELS = {
    "boleto": "Boleto",
    "pix": "Pix",
    "bank_transfer": "Transferência bancária",
    "card": "Cartão",
    "cash": "Dinheiro",
    "other": "Outro",
}
MAX_FORM_ITEMS = 12
STATUS_BADGES = {
    "draft": "text-bg-secondary",
    "approved": "text-bg-primary",
    "partially_received": "text-bg-warning",
    "received": "text-bg-success",
    "cancelled": "text-bg-dark",
}


def _path_uuid(request: Request, name: str) -> UUID | None:
    try:
        return UUID(request.path_params[name])
    except ValueError, KeyError:
        return None


def _form_int(form: dict[str, str], key: str, default: int, message: str) -> int:
    try:
        return int(form.get(key, "") or default)
    except ValueError:
        raise ValueError(message) from None


def _items_from_form(form: dict[str, str]) -> list[tuple[UUID, Decimal, Decimal]]:
    """Linhas preenchidas do formulário (product_N, quantity_N, unit_cost_N)."""
    items = []
    for index in range(1, MAX_FORM_ITEMS + 1):
        product = form.get(f"product_{index}", "")
        if not product:
            continue
        try:
            product_id = UUID(product)
        except ValueError:
            raise ValueError("Selecione um produto válido em cada linha.") from None
        items.append(
            (
                product_id,
                parse_quantity(form.get(f"quantity_{index}", ""), label="a quantidade"),
                parse_quantity(form.get(f"unit_cost_{index}", ""), label="o custo unitário"),
            )
        )
    return items


def _order_number(order: PurchaseOrder) -> str:
    return f"PC-{order.number:05d}"


def order_json(order: PurchaseOrder, items: list) -> dict[str, object]:
    return {
        "id": str(order.id),
        "number": _order_number(order),
        "status": order.status,
        "supplier": {"id": str(order.supplier_id), "name": order.supplier.name},
        "warehouse_id": str(order.warehouse_id),
        "order_date": order.order_date.isoformat(),
        "expected_date": order.expected_date.isoformat() if order.expected_date else None,
        "payment_method": order.payment_method,
        "installment_count": order.installment_count,
        "first_due_days": order.first_due_days,
        "total": str(order_total((item.quantity, item.unit_cost) for item in items)),
        "items": [
            {
                "id": str(item.id),
                "product_id": str(item.product_id),
                "sku": item.product.sku,
                "quantity": str(item.quantity),
                "unit_cost": str(item.unit_cost),
                "received_quantity": str(item.received_quantity),
            }
            for item in items
        ],
    }


def register_purchase_routes(app: object) -> None:
    async def render_orders(
        request: Request,
        user: User,
        organization: Organization,
        *,
        form: dict[str, str] | None = None,
        error: str | None = None,
    ) -> Response:
        query = _query(request)
        status = query.get("status", "") or ""
        status = status if status in STATUSES else ""
        search = (query.get("q", "") or "").strip()
        orders, page_info = await _paginate(
            purchase_orders_query(organization, status=status, search=search),
            request,
            keep=("status", "q"),
        )
        items = await purchase_order_items(orders)
        rows = []
        for order in orders:
            lines = items[order.id]
            ordered = sum((item.quantity for item in lines), Decimal("0"))
            received = sum((item.received_quantity for item in lines), Decimal("0"))
            rows.append(
                {
                    "order": order,
                    "number": _order_number(order),
                    "total": order_total((item.quantity, item.unit_cost) for item in lines),
                    "items": len(lines),
                    "progress": int(received * 100 / ordered) if ordered else 0,
                }
            )
        all_warehouses = [item for item in await warehouses(organization) if item.is_active]
        default = next((item for item in all_warehouses if item.is_default), None)
        if form is None:
            form = {
                "operation_key": str(uuid4()),
                "order_date": date.today().isoformat(),
                "warehouse_id": str(default.id) if default else "",
                "payment_method": "boleto",
                "installment_count": "1",
                "first_due_days": "30",
            }
        filled = [index for index in range(1, MAX_FORM_ITEMS + 1) if form.get(f"product_{index}")]
        body = _page(
            "purchasing/orders.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            rows=rows,
            pagination=page_info,
            status=status,
            search=search,
            status_labels=STATUS_LABELS,
            status_badges=STATUS_BADGES,
            payment_labels=PAYMENT_LABELS,
            suppliers=await active_suppliers(organization),
            warehouses=all_warehouses,
            accounts=await active_accounts(organization),
            products=await stock_products(organization),
            form=form,
            item_rows=range(1, max(filled[-1] if filled else 1, 1) + 1),
            open_drawer=error is not None,
            error=error,
            message=query.get("message", ""),
            today=date.today().isoformat(),
            active="purchases",
            section="Compras",
        )
        return _response(body, 400 if error else 200)

    @app.get("/purchases")
    @login_required
    async def purchases_page(request: Request, current: CurrentUser) -> Response:
        return await render_orders(request, current[0], await current[1].organization)

    @app.post("/purchases")
    @login_required
    async def create_purchase_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/purchases")
        organization = await current[1].organization
        try:
            expected = form.get("expected_date", "")
            order = await create_purchase_order(
                organization=organization,
                user=current[0],
                operation_key=_form_uuid(
                    form, "operation_key", "Recarregue a página e tente novamente."
                ),
                supplier_id=_form_uuid(form, "supplier_id", "Selecione o fornecedor."),
                warehouse_id=_form_uuid(form, "warehouse_id", "Selecione o depósito."),
                account_id=_form_uuid(form, "account_id", "Selecione a conta financeira."),
                order_date=_form_date(form, "order_date", "Informe a data do pedido."),
                expected_date=(
                    _form_date(form, "expected_date", "Informe uma previsão de entrega válida.")
                    if expected
                    else None
                ),
                payment_method=form.get("payment_method", ""),
                installment_count=_form_int(
                    form, "installment_count", 1, "Informe o número de parcelas."
                ),
                first_due_days=_form_int(
                    form, "first_due_days", 30, "Informe o prazo da primeira parcela em dias."
                ),
                items=_items_from_form(form),
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            return await render_orders(request, current[0], organization, form=form, error=str(exc))
        message = urlencode({"message": "Pedido de compra criado em rascunho."})
        return _redirect(f"/purchases/{order.id}?{message}")

    async def render_order(
        request: Request,
        user: User,
        organization: Organization,
        order: PurchaseOrder,
        *,
        error: str | None = None,
        receipt_form: dict[str, str] | None = None,
    ) -> Response:
        items = (await purchase_order_items([order]))[order.id]
        receipts = await purchase_receipts(order)
        titles = await document_titles(
            organization, "purchase_receipt", [receipt.id for receipt in receipts]
        )
        if receipt_form is None:
            receipt_form = {
                "operation_key": str(uuid4()),
                "received_on": date.today().isoformat(),
            }
        body = _page(
            "purchasing/order.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            order=order,
            number=_order_number(order),
            lines=[
                {
                    "item": item,
                    "total": line_total(item.quantity, item.unit_cost),
                    "remaining": remaining(item.quantity, item.received_quantity),
                }
                for item in items
            ],
            total=order_total((item.quantity, item.unit_cost) for item in items),
            receipts=receipts,
            titles=titles,
            receipt_form=receipt_form,
            status_labels=STATUS_LABELS,
            status_badges=STATUS_BADGES,
            payment_labels=PAYMENT_LABELS,
            can_receive=order.status in {"approved", "partially_received"},
            error=error,
            message=_query(request).get("message", ""),
            today=date.today().isoformat(),
            active="purchases",
            section=f"Compras / {_order_number(order)}",
        )
        return _response(body, 400 if error else 200)

    async def order_from_path(request: Request, organization: Organization):
        order_id = _path_uuid(request, "order_id")
        return await get_purchase_order(organization, order_id) if order_id else None

    @app.get("/purchases/:order_id")
    @login_required
    async def purchase_order_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        order = await order_from_path(request, organization)
        if order is None:
            return _response("Pedido de compra não encontrado.", 404)
        return await render_order(request, current[0], organization, order)

    def order_action(path: str, success: str):
        """POST sobre um pedido: valida CSRF e organização, executa e volta ao pedido."""

        def decorator(handler):
            @login_required
            async def route(request: Request, current: CurrentUser) -> Response:
                form = _form_data(request)
                if not _valid_csrf(request, form.get("csrf", "")):
                    return _redirect("/purchases")
                organization = await current[1].organization
                order = await order_from_path(request, organization)
                if order is None:
                    return _response("Pedido de compra não encontrado.", 404)
                try:
                    await handler(organization, current[0], order, form)
                except ValueError as exc:
                    refreshed = await get_purchase_order(organization, order.id)
                    return await render_order(
                        request,
                        current[0],
                        organization,
                        refreshed,
                        error=str(exc),
                        receipt_form=form if "received_on" in form else None,
                    )
                return _redirect(f"/purchases/{order.id}?{urlencode({'message': success})}")

            route.__name__ = handler.__name__
            route.__qualname__ = handler.__qualname__
            app.post(path)(route)
            return handler

        return decorator

    @order_action("/purchases/:order_id/approve", "Pedido aprovado.")
    async def approve_order(organization, _user, order, _form):
        await approve_purchase_order(organization, order.id)

    @order_action("/purchases/:order_id/cancel", "Pedido cancelado.")
    async def cancel_order(organization, _user, order, _form):
        await cancel_purchase_order(organization, order.id)

    @order_action("/purchases/:order_id/receipts", "Recebimento registrado.")
    async def receive_order(organization, user, order, form):
        quantities = {}
        for key, value in form.items():
            if not key.startswith("qty_") or not value.strip():
                continue
            try:
                item_id = UUID(key.removeprefix("qty_"))
            except ValueError:
                raise ValueError("Item de recebimento inválido.") from None
            quantities[item_id] = parse_quantity(value, label="a quantidade recebida")
        await receive_purchase_order(
            organization=organization,
            user=user,
            order_id=order.id,
            operation_key=_form_uuid(
                form, "operation_key", "Recarregue a página e tente novamente."
            ),
            received_on=_form_date(form, "received_on", "Informe a data do recebimento."),
            quantities=quantities,
            document_number=form.get("document_number", ""),
        )

    @app.post("/purchases/receipts/:receipt_id/reverse")
    @login_required
    async def reverse_receipt_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/purchases")
        organization = await current[1].organization
        receipt_id = _path_uuid(request, "receipt_id")
        receipt = await get_purchase_receipt(organization, receipt_id) if receipt_id else None
        if receipt is None:
            return _response("Recebimento não encontrado.", 404)
        order = await get_purchase_order(organization, receipt.order_id)
        try:
            await reverse_purchase_receipt(
                organization=organization, user=current[0], receipt_id=receipt.id
            )
        except ValueError as exc:
            return await render_order(request, current[0], organization, order, error=str(exc))
        message = urlencode({"message": "Recebimento estornado."})
        return _redirect(f"/purchases/{order.id}?{message}")

    # --- API JSON (consulta) ----------------------------------------------------------

    @app.get("/api/v1/purchase-orders")
    @api_login_required
    async def api_purchase_orders(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        status = _query(request).get("status", "") or ""
        if status and status not in STATUSES:
            return error_response("invalid_filter", "status inválido.", 400)
        query = purchase_orders_query(await current[1].organization, status=status)
        total = await query.count()
        orders = await query.offset(offset).limit(limit)
        items = await purchase_order_items(orders)
        return json_response(
            {
                "items": [order_json(order, items[order.id]) for order in orders],
                "limit": limit,
                "offset": offset,
                "total": total,
            }
        )

    @app.get("/api/v1/purchase-orders/:order_id")
    @api_login_required
    async def api_purchase_order(request: Request, current: CurrentUser) -> Response:
        order = await order_from_path(request, await current[1].organization)
        if order is None:
            return error_response("not_found", "Pedido de compra não encontrado.", 404)
        items = (await purchase_order_items([order]))[order.id]
        return json_response(order_json(order, items))
