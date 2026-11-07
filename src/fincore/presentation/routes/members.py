"""Rotas: Membros e perfis de acesso."""

from urllib.parse import urlencode

from robyn import Request, Response

from fincore.application.members import (
    add_member,
    can_manage_members,
    change_member_role,
    set_member_active,
)
from fincore.infrastructure.models import (
    Membership,
    User,
)
from fincore.presentation.web import (
    _current_user,
    _form_data,
    _form_uuid,
    _page,
    _redirect,
    _render_members,
    _response,
    _valid_csrf,
)


def register_member_routes(app: object) -> None:
    async def _owner(request: Request) -> tuple[User, Membership] | Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        if not can_manage_members(current[1].role):
            return _response(
                _page("forbidden.html", message="Só proprietários gerenciam membros."), 403
            )
        return current

    @app.get("/members")
    async def members_page(request: Request) -> Response:
        current = await _owner(request)
        if isinstance(current, Response):
            return current
        user, membership = current
        organization = await membership.organization
        return _response(
            await _render_members(
                request,
                user,
                membership,
                organization,
                message=request.query_params.get("message", None),
            )
        )

    @app.post("/members")
    async def create_member_page(request: Request) -> Response:
        current = await _owner(request)
        if isinstance(current, Response):
            return current
        user, membership = current
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/members")
        organization = await membership.organization
        try:
            await add_member(
                organization,
                form.get("full_name", ""),
                form.get("email", ""),
                form.get("password", ""),
                form.get("role", ""),
            )
        except ValueError as exc:
            filled = {key: form.get(key, "") for key in ("full_name", "email", "role")}
            return _response(
                await _render_members(
                    request, user, membership, organization, error=str(exc), form=filled
                ),
                400,
            )
        return _redirect(f"/members?{urlencode({'message': 'Membro adicionado.'})}")

    @app.post("/members/:membership_id")
    async def update_member_page(request: Request) -> Response:
        current = await _owner(request)
        if isinstance(current, Response):
            return current
        user, membership = current
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/members")
        organization = await membership.organization
        try:
            target = _form_uuid(
                {"id": request.path_params.get("membership_id", "")},
                "id",
                "Membro não encontrado nesta organização.",
            )
            action = form.get("action", "")
            if action == "role":
                await change_member_role(organization, target, form.get("role", ""))
                message = "Perfil atualizado."
            elif action in {"deactivate", "activate"}:
                await set_member_active(organization, target, action == "activate")
                message = "Acesso desativado." if action == "deactivate" else "Acesso reativado."
            else:
                raise ValueError("Ação inválida.")
        except ValueError as exc:
            return _response(
                await _render_members(request, user, membership, organization, error=str(exc)),
                400,
            )
        return _redirect(f"/members?{urlencode({'message': message})}")
