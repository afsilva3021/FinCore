"""Rotas: vendas (orçamentos, pedidos, faturamento e devolução)."""

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from robyn import Request, Response

from fincore.application.queries import (
    active_accounts,
    active_customers,
    document_titles,
    get_sales_invoice,
    get_sales_order,
    sales_invoices,
    sales_order_items,
    sales_orders_query,
    sellable_products,
    stock_on_hand,
    warehouses,
)
from fincore.application.sales import (
    cancel_sales_order,
    confirm_sales_order,
    create_sales_order,
    invoice_sales_order,
    reverse_sales_invoice,
)
from fincore.domain.money import parse_brl
from fincore.domain.products import parse_quantity
from fincore.domain.sales import (
    STATUS_LABELS,
    STATUSES,
    line_total,
    margin,
    margin_percent,
    order_total,
)
from fincore.infrastructure.models import Organization, SalesOrder, User
from fincore.presentation.routes.api import error_response, json_response, pagination
from fincore.presentation.routes.purchases import PAYMENT_LABELS
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

MAX_FORM_ITEMS = 12
STATUS_BADGES = {
    "quote": "text-bg-secondary",
    "confirmed": "text-bg-primary",
    "invoiced": "text-bg-success",
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


def _money(value: str, label: str) -> Decimal:
    return parse_brl(
        value,
        allow_zero=True,
        empty_as_zero=True,
        invalid_message=f"Informe {label} em formato válido, por exemplo 19,90.",
        range_message=f"Confira {label}: deve ser zero ou maior.",
    )


def _items_from_form(form: dict[str, str]) -> list[tuple[UUID, Decimal, Decimal | None, Decimal]]:
    """Linhas preenchidas (product_N, quantity_N, unit_price_N, discount_N).

    Preço em branco usa o preço de venda do cadastro do produto.
    """
    items = []
    for index in range(1, MAX_FORM_ITEMS + 1):
        product = form.get(f"product_{index}", "")
        if not product:
            continue
        try:
            product_id = UUID(product)
        except ValueError:
            raise ValueError("Selecione um produto válido em cada linha.") from None
        price = form.get(f"unit_price_{index}", "").strip()
        items.append(
            (
                product_id,
                parse_quantity(form.get(f"quantity_{index}", ""), label="a quantidade"),
                _money(price, "o preço unitário") if price else None,
                _money(form.get(f"discount_{index}", ""), "o desconto"),
            )
        )
    return items


def _order_number(order: SalesOrder) -> str:
    return f"PV-{order.number:05d}"


def _total(items: list) -> Decimal:
    return order_total((item.quantity, item.unit_price, item.discount) for item in items)


def order_json(order: SalesOrder, items: list) -> dict[str, object]:
    return {
        "id": str(order.id),
        "number": _order_number(order),
        "status": order.status,
        "customer": {"id": str(order.customer_id), "name": order.customer.name},
        "warehouse_id": str(order.warehouse_id),
        "order_date": order.order_date.isoformat(),
        "valid_until": order.valid_until.isoformat() if order.valid_until else None,
        "payment_method": order.payment_method,
        "installment_count": order.installment_count,
        "first_due_days": order.first_due_days,
        "total": str(_total(items)),
        "items": [
            {
                "id": str(item.id),
                "product_id": str(item.product_id),
                "sku": item.product.sku,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "discount": str(item.discount),
                "total": str(line_total(item.quantity, item.unit_price, item.discount)),
            }
            for item in items
        ],
    }


def register_sales_routes(app: object) -> None:
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
            sales_orders_query(organization, status=status, search=search),
            request,
            keep=("status", "q"),
        )
        items = await sales_order_items(orders)
        active_warehouses = [item for item in await warehouses(organization) if item.is_active]
        default = next((item for item in active_warehouses if item.is_default), None)
        if form is None:
            form = {
                "operation_key": str(uuid4()),
                "order_date": date.today().isoformat(),
                "warehouse_id": str(default.id) if default else "",
                "payment_method": "pix",
                "installment_count": "1",
                "first_due_days": "0",
                "customer_id": query.get("customer", "") or "",
            }
        filled = [index for index in range(1, MAX_FORM_ITEMS + 1) if form.get(f"product_{index}")]
        body = _page(
            "sales/orders.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            rows=[
                {
                    "order": order,
                    "number": _order_number(order),
                    "total": _total(items[order.id]),
                    "count": len(items[order.id]),
                }
                for order in orders
            ],
            pagination=page_info,
            status=status,
            search=search,
            status_labels=STATUS_LABELS,
            status_badges=STATUS_BADGES,
            payment_labels=PAYMENT_LABELS,
            customers=await active_customers(organization),
            warehouses=active_warehouses,
            accounts=await active_accounts(organization),
            products=await sellable_products(organization),
            form=form,
            item_rows=range(1, max(filled[-1] if filled else 1, 1) + 1),
            open_drawer=error is not None or query.get("new", "") == "1",
            error=error,
            message=query.get("message", ""),
            today=date.today().isoformat(),
            active="sales",
            section="Vendas",
        )
        return _response(body, 400 if error else 200)

    @app.get("/sales")
    @login_required
    async def sales_page(request: Request, current: CurrentUser) -> Response:
        return await render_orders(request, current[0], await current[1].organization)

    @app.post("/sales")
    @login_required
    async def create_sales_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/sales")
        organization = await current[1].organization
        try:
            valid_until = form.get("valid_until", "")
            order = await create_sales_order(
                organization=organization,
                user=current[0],
                operation_key=_form_uuid(
                    form, "operation_key", "Recarregue a página e tente novamente."
                ),
                customer_id=_form_uuid(form, "customer_id", "Selecione o cliente."),
                warehouse_id=_form_uuid(form, "warehouse_id", "Selecione o depósito."),
                account_id=_form_uuid(form, "account_id", "Selecione a conta financeira."),
                order_date=_form_date(form, "order_date", "Informe a data do orçamento."),
                valid_until=(
                    _form_date(form, "valid_until", "Informe uma validade válida.")
                    if valid_until
                    else None
                ),
                payment_method=form.get("payment_method", ""),
                installment_count=_form_int(
                    form, "installment_count", 1, "Informe o número de parcelas."
                ),
                first_due_days=_form_int(
                    form, "first_due_days", 0, "Informe o prazo da primeira parcela em dias."
                ),
                items=_items_from_form(form),
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            return await render_orders(request, current[0], organization, form=form, error=str(exc))
        return _redirect(f"/sales/{order.id}?{urlencode({'message': 'Orçamento criado.'})}")

    async def render_order(
        request: Request,
        user: User,
        organization: Organization,
        order: SalesOrder,
        *,
        error: str | None = None,
        invoice_key: str | None = None,
    ) -> Response:
        items = (await sales_order_items([order]))[order.id]
        invoices = await sales_invoices(order)
        titles = await document_titles(
            organization, "sales_invoice", [invoice.id for invoice in invoices]
        )
        on_hand = await stock_on_hand(organization, [item.product_id for item in items])
        body = _page(
            "sales/order.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            order=order,
            number=_order_number(order),
            lines=[
                {
                    "item": item,
                    "total": line_total(item.quantity, item.unit_price, item.discount),
                    "on_hand": on_hand.get(item.product_id, Decimal("0")),
                }
                for item in items
            ],
            total=_total(items),
            invoices=[
                {
                    "invoice": invoice,
                    "margin": margin(invoice.total, invoice.cost_total),
                    "margin_percent": margin_percent(invoice.total, invoice.cost_total),
                }
                for invoice in invoices
            ],
            titles=titles,
            invoice_key=invoice_key or str(uuid4()),
            status_labels=STATUS_LABELS,
            status_badges=STATUS_BADGES,
            payment_labels=PAYMENT_LABELS,
            expired=order.status == "quote"
            and order.valid_until is not None
            and order.valid_until < date.today(),
            error=error,
            message=_query(request).get("message", ""),
            today=date.today().isoformat(),
            active="sales",
            section=f"Vendas / {_order_number(order)}",
        )
        return _response(body, 400 if error else 200)

    async def order_from_path(request: Request, organization: Organization):
        order_id = _path_uuid(request, "order_id")
        return await get_sales_order(organization, order_id) if order_id else None

    @app.get("/sales/:order_id")
    @login_required
    async def sales_order_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        order = await order_from_path(request, organization)
        if order is None:
            return _response("Pedido de venda não encontrado.", 404)
        return await render_order(request, current[0], organization, order)

    def order_action(path: str, success: str):
        """POST sobre um pedido: valida CSRF e organização, executa e volta ao pedido."""

        def decorator(handler):
            @login_required
            async def route(request: Request, current: CurrentUser) -> Response:
                form = _form_data(request)
                if not _valid_csrf(request, form.get("csrf", "")):
                    return _redirect("/sales")
                organization = await current[1].organization
                order = await order_from_path(request, organization)
                if order is None:
                    return _response("Pedido de venda não encontrado.", 404)
                try:
                    await handler(organization, current[0], order, form)
                except ValueError as exc:
                    refreshed = await get_sales_order(organization, order.id)
                    return await render_order(
                        request,
                        current[0],
                        organization,
                        refreshed,
                        error=str(exc),
                        invoice_key=form.get("operation_key") or None,
                    )
                return _redirect(f"/sales/{order.id}?{urlencode({'message': success})}")

            route.__name__ = handler.__name__
            route.__qualname__ = handler.__qualname__
            app.post(path)(route)
            return handler

        return decorator

    @order_action("/sales/:order_id/confirm", "Pedido confirmado.")
    async def confirm_order(organization, _user, order, _form):
        await confirm_sales_order(organization, order.id)

    @order_action("/sales/:order_id/cancel", "Pedido cancelado.")
    async def cancel_order(organization, _user, order, _form):
        await cancel_sales_order(organization, order.id)

    @order_action("/sales/:order_id/invoices", "Pedido faturado.")
    async def invoice_order(organization, user, order, form):
        await invoice_sales_order(
            organization=organization,
            user=user,
            order_id=order.id,
            operation_key=_form_uuid(
                form, "operation_key", "Recarregue a página e tente novamente."
            ),
            issued_on=_form_date(form, "issued_on", "Informe a data do faturamento."),
        )

    @app.post("/sales/invoices/:invoice_id/reverse")
    @login_required
    async def reverse_invoice_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/sales")
        organization = await current[1].organization
        invoice_id = _path_uuid(request, "invoice_id")
        invoice = await get_sales_invoice(organization, invoice_id) if invoice_id else None
        if invoice is None:
            return _response("Faturamento não encontrado.", 404)
        order = await get_sales_order(organization, invoice.order_id)
        try:
            await reverse_sales_invoice(
                organization=organization, user=current[0], invoice_id=invoice.id
            )
        except ValueError as exc:
            return await render_order(request, current[0], organization, order, error=str(exc))
        message = urlencode({"message": "Faturamento estornado; a mercadoria voltou ao estoque."})
        return _redirect(f"/sales/{order.id}?{message}")

    # --- API JSON (consulta) ----------------------------------------------------------

    @app.get("/api/v1/sales-orders")
    @api_login_required
    async def api_sales_orders(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        status = _query(request).get("status", "") or ""
        if status and status not in STATUSES:
            return error_response("invalid_filter", "status inválido.", 400)
        query = sales_orders_query(await current[1].organization, status=status)
        total = await query.count()
        orders = await query.offset(offset).limit(limit)
        items = await sales_order_items(orders)
        return json_response(
            {
                "items": [order_json(order, items[order.id]) for order in orders],
                "limit": limit,
                "offset": offset,
                "total": total,
            }
        )

    @app.get("/api/v1/sales-orders/:order_id")
    @api_login_required
    async def api_sales_order(request: Request, current: CurrentUser) -> Response:
        order = await order_from_path(request, await current[1].organization)
        if order is None:
            return error_response("not_found", "Pedido de venda não encontrado.", 404)
        items = (await sales_order_items([order]))[order.id]
        return json_response(order_json(order, items))
