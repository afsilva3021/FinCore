"""Rotas: cadastros de produto (produtos, grupos, unidades de medida e depósitos)."""

import re
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID

from robyn import Request, Response

from fincore.application.products import (
    save_product,
    save_product_group,
    save_unit,
    save_warehouse,
    set_default_warehouse,
    set_product_active,
    set_product_group_active,
    set_unit_active,
    set_warehouse_active,
)
from fincore.application.queries import (
    get_product,
    product_groups,
    products_query,
    stock_on_hand,
    units_of_measure,
    warehouses,
)
from fincore.domain.money import parse_brl
from fincore.domain.products import parse_api_quantity, parse_quantity
from fincore.infrastructure.models import Organization, Product, User
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
    _format_brl,
    _format_quantity,
    _page,
    _paginate,
    _query,
    _redirect,
    _response,
    _valid_csrf,
    api_login_required,
    login_required,
)

STATUSES = {"all", "active", "inactive"}
TRUTHY = {"true", "on", "1"}


def _optional_uuid(value: str, message: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(message) from None


def _required_uuid(value: str, message: str) -> UUID:
    result = _optional_uuid(value, message)
    if result is None:
        raise ValueError(message)
    return result


def _api_price(value: str) -> Decimal:
    """Preço na notação canônica da API ("19.90"); recusa mais de duas casas."""
    if not re.fullmatch(r"\d+(\.\d{1,2})?", value.strip()):
        raise ValueError('Informe sale_price como string decimal, por exemplo "19.90".')
    return Decimal(value.strip())


def _path_uuid(request: Request, name: str) -> UUID | None:
    try:
        return UUID(request.path_params[name])
    except ValueError, KeyError:
        return None


def _price_from_form(value: str) -> Decimal:
    return parse_brl(
        value,
        allow_zero=True,
        empty_as_zero=True,
        invalid_message="Informe o preço de venda em formato válido, por exemplo 19,90.",
        range_message="Preço de venda deve ser zero ou maior e respeitar o limite permitido.",
    )


def product_json(item: Product) -> dict[str, object]:
    return {
        "id": str(item.id),
        "sku": item.sku,
        "name": item.name,
        "description": item.description,
        "barcode": item.barcode,
        "ncm": item.ncm,
        "unit": {"id": str(item.unit_id), "code": item.unit.code},
        "group": {"id": str(item.group_id), "name": item.group.name} if item.group_id else None,
        "sale_price": str(item.sale_price),
        "currency": item.currency,
        "min_stock": str(item.min_stock),
        "tracks_stock": item.tracks_stock,
        "is_active": item.is_active,
    }


def register_product_routes(app: object) -> None:
    # --- Produtos: páginas ------------------------------------------------------------

    async def render_products(
        request: Request,
        user: User,
        organization: Organization,
        *,
        form: dict[str, str] | None = None,
        error: str | None = None,
        editing: bool = False,
        filters: dict[str, str] | None = None,
    ) -> Response:
        source = filters if filters is not None else _query(request)
        search = (source.get("q", "") or "").strip()
        status = source.get("status", "all") or "all"
        status = status if status in STATUSES else "all"
        try:
            group_id = _optional_uuid(source.get("group", "") or "", "grupo")
        except ValueError:
            group_id = None
        products, page_info = await _paginate(
            products_query(organization, search=search, status=status, group_id=group_id),
            request,
            keep=("q", "status", "group"),
        )
        units = await units_of_measure(organization, only_active=True)
        groups = await product_groups(organization, only_active=True)
        if form is None:
            default_unit = next((unit for unit in units if unit.code == "UN"), None)
            form = {
                "unit_id": str(default_unit.id) if default_unit else "",
                "tracks_stock": "true",
            }
        body = _page(
            "inventory/products.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            products=products,
            on_hand=await stock_on_hand(organization, [item.id for item in products]),
            pagination=page_info,
            units=units,
            groups=groups,
            search=search,
            status=status,
            group_filter=str(group_id) if group_id else "",
            form=form,
            editing=editing,
            open_drawer=editing or error is not None,
            error=error,
            message=_query(request).get("message", ""),
            active="products",
            section="Produtos",
        )
        return _response(body, 400 if error else 200)

    @app.get("/products")
    @login_required
    async def products_page(request: Request, current: CurrentUser) -> Response:
        return await render_products(request, current[0], await current[1].organization)

    @app.get("/products/:product_id/edit")
    @login_required
    async def edit_product_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        product_id = _path_uuid(request, "product_id")
        product = await get_product(organization, product_id) if product_id else None
        if product is None:
            return _response("Produto não encontrado.", 404)
        form = {
            "id": str(product.id),
            "sku": product.sku,
            "name": product.name,
            "description": product.description or "",
            "unit_id": str(product.unit_id),
            "group_id": str(product.group_id) if product.group_id else "",
            "barcode": product.barcode or "",
            "ncm": product.ncm or "",
            "sale_price": _format_brl(product.sale_price),
            "min_stock": _format_quantity(product.min_stock),
            "tracks_stock": "true" if product.tracks_stock else "",
        }
        return await render_products(request, current[0], organization, form=form, editing=True)

    async def save_product_page(
        request: Request, current: CurrentUser, product_id: UUID | None
    ) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/products")
        organization = await current[1].organization
        filters = {key: form.get("filter_" + key, "") for key in ("q", "status", "group")}
        try:
            await save_product(
                organization=organization,
                user=current[0],
                product_id=product_id,
                sku=form.get("sku", ""),
                name=form.get("name", ""),
                description=form.get("description", ""),
                unit_id=_required_uuid(form.get("unit_id", ""), "Selecione a unidade de medida."),
                group_id=_optional_uuid(form.get("group_id", ""), "Selecione um grupo válido."),
                barcode=form.get("barcode", ""),
                ncm=form.get("ncm", ""),
                sale_price=_price_from_form(form.get("sale_price", "")),
                min_stock=parse_quantity(form.get("min_stock", ""), label="o estoque mínimo"),
                tracks_stock=form.get("tracks_stock", "").lower() in TRUTHY,
            )
        except ValueError as exc:
            form["id"] = str(product_id) if product_id else ""
            return await render_products(
                request,
                current[0],
                organization,
                form=form,
                error=str(exc),
                editing=product_id is not None,
                filters=filters,
            )
        message = "Produto atualizado." if product_id else "Produto cadastrado."
        query = {key: value for key, value in filters.items() if value}
        return _redirect(f"/products?{urlencode({**query, 'message': message})}")

    @app.post("/products")
    @login_required
    async def create_product_page(request: Request, current: CurrentUser) -> Response:
        return await save_product_page(request, current, None)

    @app.post("/products/:product_id/update")
    @login_required
    async def update_product_page(request: Request, current: CurrentUser) -> Response:
        product_id = _path_uuid(request, "product_id")
        if product_id is None:
            return _response("Produto não encontrado.", 404)
        return await save_product_page(request, current, product_id)

    @app.post("/products/:product_id/active")
    @login_required
    async def set_product_active_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/products")
        product_id = _path_uuid(request, "product_id")
        is_active = form.get("is_active", "").lower() in TRUTHY
        try:
            if product_id is None:
                raise ValueError("Produto não encontrado nesta organização.")
            await set_product_active(await current[1].organization, product_id, is_active)
        except ValueError:
            return _response("Produto não encontrado.", 404)
        filters = {key: form.get("filter_" + key, "") for key in ("q", "status", "group")}
        query = {key: value for key, value in filters.items() if value}
        message = "Produto reativado." if is_active else "Produto inativado."
        return _redirect(f"/products?{urlencode({**query, 'message': message})}")

    # --- Grupos, unidades e depósitos: uma página -------------------------------------

    async def render_settings(
        request: Request,
        user: User,
        organization: Organization,
        *,
        error: str | None = None,
        failed: str = "",
        form: dict[str, str] | None = None,
    ) -> Response:
        body = _page(
            "inventory/settings.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            groups=await product_groups(organization),
            units=await units_of_measure(organization),
            warehouses=await warehouses(organization),
            error=error,
            failed=failed,
            form=form or {},
            message=_query(request).get("message", ""),
            active="product-settings",
            section="Grupos, unidades e depósitos",
        )
        return _response(body, 400 if error else 200)

    @app.get("/product-settings")
    @login_required
    async def product_settings_page(request: Request, current: CurrentUser) -> Response:
        return await render_settings(request, current[0], await current[1].organization)

    def settings_route(path: str, kind: str, success: str):
        """Registra um POST da página de cadastros auxiliares.

        O handler recebe (organização, usuário, formulário, id da URL) e levanta
        ValueError com a mensagem que deve aparecer na página.
        """

        def decorator(handler):
            @login_required
            async def route(request: Request, current: CurrentUser) -> Response:
                form = _form_data(request)
                if not _valid_csrf(request, form.get("csrf", "")):
                    return _redirect("/product-settings")
                organization = await current[1].organization
                item_id = None
                if ":item_id" in path:
                    item_id = _path_uuid(request, "item_id")
                    if item_id is None:
                        return _response("Registro não encontrado.", 404)
                try:
                    message = await handler(organization, current[0], form, item_id) or success
                except ValueError as exc:
                    return await render_settings(
                        request,
                        current[0],
                        organization,
                        error=str(exc),
                        failed=kind if item_id is None else "",
                        form=form,
                    )
                return _redirect(f"/product-settings?{urlencode({'message': message})}")

            route.__name__ = handler.__name__
            route.__qualname__ = handler.__qualname__
            app.post(path)(route)
            return handler

        return decorator

    def _active_message(form: dict[str, str], label: str) -> tuple[bool, str]:
        is_active = form.get("is_active", "").lower() in TRUTHY
        return is_active, f"{label} {'reativado' if is_active else 'inativado'}."

    @settings_route("/product-settings/groups", "group", "Grupo cadastrado.")
    async def create_group(organization, user, form, _item_id):
        await save_product_group(
            organization=organization,
            user=user,
            name=form.get("name", ""),
            parent_id=_optional_uuid(form.get("parent_id", ""), "Selecione um grupo válido."),
        )

    @settings_route("/product-settings/groups/:item_id/update", "group", "Grupo atualizado.")
    async def update_group(organization, user, form, item_id):
        await save_product_group(
            organization=organization,
            user=user,
            group_id=item_id,
            name=form.get("name", ""),
            parent_id=_optional_uuid(form.get("parent_id", ""), "Selecione um grupo válido."),
        )

    @settings_route("/product-settings/groups/:item_id/active", "group", "")
    async def set_group_active(organization, _user, form, item_id):
        is_active, message = _active_message(form, "Grupo")
        await set_product_group_active(organization, item_id, is_active)
        return message

    def _unit_decimals(form: dict[str, str]) -> int:
        try:
            return int(form.get("decimal_places", "0") or "0")
        except ValueError:
            raise ValueError("Casas decimais da unidade devem ser um número de 0 a 4.") from None

    @settings_route("/product-settings/units", "unit", "Unidade cadastrada.")
    async def create_unit(organization, _user, form, _item_id):
        await save_unit(
            organization=organization,
            code=form.get("code", ""),
            name=form.get("name", ""),
            decimal_places=_unit_decimals(form),
        )

    @settings_route("/product-settings/units/:item_id/update", "unit", "Unidade atualizada.")
    async def update_unit(organization, _user, form, item_id):
        await save_unit(
            organization=organization,
            unit_id=item_id,
            code=form.get("code", ""),
            name=form.get("name", ""),
            decimal_places=_unit_decimals(form),
        )

    @settings_route("/product-settings/units/:item_id/active", "unit", "")
    async def set_unit_active_route(organization, _user, form, item_id):
        is_active, message = _active_message(form, "Unidade")
        await set_unit_active(organization, item_id, is_active)
        return message.replace("ado.", "ada.")

    @settings_route("/product-settings/warehouses", "warehouse", "Depósito cadastrado.")
    async def create_warehouse(organization, user, form, _item_id):
        await save_warehouse(
            organization=organization,
            user=user,
            code=form.get("code", ""),
            name=form.get("name", ""),
        )

    @settings_route(
        "/product-settings/warehouses/:item_id/update", "warehouse", "Depósito atualizado."
    )
    async def update_warehouse(organization, user, form, item_id):
        await save_warehouse(
            organization=organization,
            user=user,
            warehouse_id=item_id,
            code=form.get("code", ""),
            name=form.get("name", ""),
        )

    @settings_route("/product-settings/warehouses/:item_id/active", "warehouse", "")
    async def set_warehouse_active_route(organization, _user, form, item_id):
        is_active, message = _active_message(form, "Depósito")
        await set_warehouse_active(organization, item_id, is_active)
        return message

    @settings_route(
        "/product-settings/warehouses/:item_id/default", "warehouse", "Depósito padrão alterado."
    )
    async def set_default_warehouse_route(organization, _user, _form, item_id):
        await set_default_warehouse(organization, item_id)

    # --- API JSON ---------------------------------------------------------------------

    @app.get("/api/v1/products")
    @api_login_required
    async def api_products(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        status = _query(request).get("status", "all") or "all"
        if status not in STATUSES:
            return error_response("invalid_filter", "status deve ser all, active ou inactive.", 400)
        query = products_query(
            await current[1].organization,
            search=_query(request).get("q", "") or "",
            status=status,
        )
        total = await query.count()
        items = [product_json(item) for item in await query.offset(offset).limit(limit)]
        return json_response({"items": items, "limit": limit, "offset": offset, "total": total})

    @app.get("/api/v1/products/:product_id")
    @api_login_required
    async def api_product(request: Request, current: CurrentUser) -> Response:
        product_id = _path_uuid(request, "product_id")
        product = (
            await get_product(await current[1].organization, product_id) if product_id else None
        )
        if product is None:
            return error_response("not_found", "Produto não encontrado.", 404)
        return json_response(product_json(product))

    @app.post("/api/v1/products")
    @api_login_required
    async def api_create_product(request: Request, current: CurrentUser) -> Response:
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        organization = await current[1].organization
        try:
            payload = json_payload(request)
            text = {}
            for field in ("sku", "name", "unit_id", "group_id", "description", "barcode", "ncm"):
                value = payload.get(field, "")
                if value is None:
                    value = ""
                if not isinstance(value, str):
                    raise ValueError(f"{field} deve ser texto.")
                text[field] = value
            sale_price = payload.get("sale_price", "0.00")
            min_stock = payload.get("min_stock", "0")
            tracks_stock = payload.get("tracks_stock", True)
            if not isinstance(sale_price, str) or not isinstance(min_stock, str):
                raise ValueError("sale_price e min_stock devem ser strings decimais.")
            if not isinstance(tracks_stock, bool):
                raise ValueError("tracks_stock deve ser true ou false.")
            product = await save_product(
                organization=organization,
                user=current[0],
                sku=text["sku"],
                name=text["name"],
                description=text["description"],
                unit_id=_required_uuid(text["unit_id"], "Informe unit_id como UUID."),
                group_id=_optional_uuid(text["group_id"], "group_id deve ser um UUID."),
                barcode=text["barcode"],
                ncm=text["ncm"],
                sale_price=_api_price(sale_price),
                min_stock=parse_api_quantity(min_stock, label="min_stock"),
                tracks_stock=tracks_stock,
            )
        except ValueError as exc:
            message = str(exc)
            if message.startswith("Já existe"):
                return error_response("conflict", message, 409)
            return error_response("invalid_product", message, 400)
        created = await get_product(organization, product.id)
        return json_response(product_json(created), 201)
