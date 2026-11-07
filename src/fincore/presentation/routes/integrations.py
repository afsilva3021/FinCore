"""Rotas: Simuladores de integrações (sem chamadas externas)."""

from robyn import Request, Response

from fincore.application.finance import (
    parse_money,
)
from fincore.application.simulated_integrations import simulate_adapter
from fincore.infrastructure.models import (
    Organization,
    User,
)
from fincore.presentation.web import (
    CurrentUser,
    _csrf_from_auth,
    _form_data,
    _page,
    _redirect,
    _response,
    _valid_csrf,
    login_required,
)


def register_demo_integration_routes(app: object) -> None:
    async def render_demo(
        request: Request,
        user: User,
        organization: Organization,
        result: object | None = None,
        error: str | None = None,
    ) -> str:
        return _page(
            "finance/integrations.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            result=result,
            error=error,
        )

    @app.get("/integrations")
    @login_required
    async def integrations_page(request: Request, current: CurrentUser) -> Response:
        return _response(await render_demo(request, current[0], await current[1].organization))

    @app.post("/integrations/simulate")
    @login_required
    async def simulate_integration_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/integrations")
        try:
            adapter = form.get("adapter", "")
            amount = parse_money(form["amount"]) if form.get("amount") else None
            result = simulate_adapter(adapter, amount)
        except (ValueError, KeyError) as exc:
            return _response(
                await render_demo(request, current[0], organization, error=str(exc)), 400
            )
        return _response(await render_demo(request, current[0], organization, result=result))
