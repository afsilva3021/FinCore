"""Rotas: Clientes e fornecedores."""

import json
from urllib.parse import urlencode
from uuid import UUID

from robyn import Request, Response

from fincore.application.counterparties import (
    get_counterparty,
    save_counterparty,
    set_counterparty_active,
)
from fincore.infrastructure.cep_lookup import CepLookupError, lookup_cep
from fincore.infrastructure.models import (
    Organization,
    User,
)
from fincore.presentation.web import (
    CurrentUser,
    _current_user,
    _form_data,
    _redirect,
    _render_counterparties,
    _response,
    _valid_csrf,
    login_required,
)


def register_counterparty_routes(app: object) -> None:
    async def render(
        request: Request,
        user: User,
        organization: Organization,
        *,
        role: str = "all",
        status: str = "all",
        form: dict[str, str] | None = None,
        error: str | None = None,
        message: str | None = None,
        editing: bool = False,
    ) -> Response:
        return _response(
            await _render_counterparties(
                request,
                user,
                organization,
                role=role,
                status=status,
                form=form,
                error=error,
                message=message,
                editing=editing,
            ),
            400 if error else 200,
        )

    async def index(request: Request, role_override: str | None = None) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        role = role_override or request.query_params.get("role", "all")
        status = request.query_params.get("status", "all")
        message = request.query_params.get("message", "")
        return await render(
            request,
            current[0],
            await current[1].organization,
            role=role,
            status=status,
            message=message,
        )

    @app.get("/contacts")
    async def contacts_page(request: Request) -> Response:
        return await index(request)

    @app.get("/customers")
    async def customers_page(request: Request) -> Response:
        return await index(request, "customer")

    @app.get("/suppliers")
    async def suppliers_page(request: Request) -> Response:
        return await index(request, "supplier")

    @app.get("/contacts/postal-code")
    async def postal_code_lookup(request: Request) -> Response:
        if await _current_user(request) is None:
            return _redirect("/login")
        try:
            result = await lookup_cep(request.query_params.get("cep", ""))
            return Response(
                status_code=200,
                headers={"Content-Type": "application/json; charset=utf-8"},
                body=json.dumps(result, ensure_ascii=False),
            )
        except CepLookupError as exc:
            return Response(
                status_code=exc.status_code,
                headers={"Content-Type": "application/json; charset=utf-8"},
                body=json.dumps({"message": str(exc)}, ensure_ascii=False),
            )

    @app.get("/contacts/:contact_id/edit")
    @login_required
    async def edit_contact_page(request: Request, current: CurrentUser) -> Response:
        try:
            contact = await get_counterparty(
                await current[1].organization, UUID(request.path_params["contact_id"])
            )
        except ValueError, KeyError:
            contact = None
        if contact is None:
            return _response("Contato não encontrado.", 404)
        form = {
            "id": str(contact.id),
            "person_type": contact.person_type,
            "name": contact.name,
            "trade_name": contact.trade_name or "",
            "tax_id": contact.tax_id or "",
            "is_customer": "true" if contact.is_customer else "",
            "is_supplier": "true" if contact.is_supplier else "",
            "email": contact.email or "",
            "phone": contact.phone or "",
            "postal_code": contact.postal_code or "",
            "street": contact.street or "",
            "address_number": contact.address_number or "",
            "complement": contact.complement or "",
            "district": contact.district or "",
            "city": contact.city or "",
            "state": contact.state or "",
            "notes": contact.notes or "",
        }
        return await render(
            request,
            current[0],
            await current[1].organization,
            role=request.query_params.get("role", "all"),
            status=request.query_params.get("status", "all"),
            form=form,
            editing=True,
        )

    async def save_page(request: Request, contact_id: UUID | None = None) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        role = form.get("role", "all")
        status = form.get("status", "all")
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/contacts")
        try:
            await save_counterparty(
                organization=organization,
                counterparty_id=contact_id,
                person_type=form.get("person_type", ""),
                name=form.get("name", ""),
                trade_name=form.get("trade_name", ""),
                tax_id=form.get("tax_id", ""),
                is_customer=form.get("is_customer", "").lower() in {"true", "on", "1"},
                is_supplier=form.get("is_supplier", "").lower() in {"true", "on", "1"},
                email=form.get("email", ""),
                phone=form.get("phone", ""),
                postal_code=form.get("postal_code", ""),
                street=form.get("street", ""),
                address_number=form.get("address_number", ""),
                complement=form.get("complement", ""),
                district=form.get("district", ""),
                city=form.get("city", ""),
                state=form.get("state", ""),
                notes=form.get("notes", ""),
            )
        except ValueError as exc:
            form["id"] = str(contact_id) if contact_id else ""
            return await render(
                request,
                current[0],
                organization,
                role=role,
                status=status,
                form=form,
                error=str(exc),
                editing=contact_id is not None,
            )
        role = role if role in {"all", "customer", "supplier"} else "all"
        status = status if status in {"all", "active", "inactive"} else "all"
        message = "Contato atualizado." if contact_id else "Contato cadastrado."
        query = urlencode({"role": role, "status": status, "message": message})
        return _redirect(f"/contacts?{query}")

    @app.post("/contacts")
    async def create_contact_page(request: Request) -> Response:
        return await save_page(request)

    @app.post("/contacts/:contact_id/update")
    async def update_contact_page(request: Request) -> Response:
        try:
            contact_id = UUID(request.path_params["contact_id"])
        except ValueError, KeyError:
            return _response("Contato não encontrado.", 404)
        return await save_page(request, contact_id)

    @app.post("/contacts/:contact_id/active")
    @login_required
    async def set_contact_active_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/contacts")
        try:
            contact_id = UUID(request.path_params["contact_id"])
            is_active = form.get("is_active", "").lower() in {"true", "1", "on"}
            await set_counterparty_active(await current[1].organization, contact_id, is_active)
        except (ValueError, KeyError) as exc:
            role = form.get("role", "all")
            status = form.get("status", "all")
            return await render(
                request,
                current[0],
                await current[1].organization,
                role=role,
                status=status,
                error=str(exc),
            )
        message = "Contato reativado." if is_active else "Contato inativado."
        role = form.get("role", "all")
        status = form.get("status", "all")
        role = role if role in {"all", "customer", "supplier"} else "all"
        status = status if status in {"all", "active", "inactive"} else "all"
        return _redirect(
            f"/contacts?{urlencode({'role': role, 'status': status, 'message': message})}"
        )
