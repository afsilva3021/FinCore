"""Rotas: Login, cadastro, logout e visão geral."""

import secrets
from decimal import Decimal

from itsdangerous import BadSignature, SignatureExpired
from robyn import Request, Response
from tortoise.exceptions import IntegrityError

from fincore.application.finance import (
    get_account_balances,
)
from fincore.application.identity import (
    PASSWORD_RULES_TEXT,
    login_with_throttle,
    register_user,
    revoke_sessions,
)
from fincore.application.queries import (
    first_active_membership,
    open_transaction_count,
    recent_transactions,
)
from fincore.application.reports import realized_totals
from fincore.presentation.web import (
    COOKIE_AUTH,
    COOKIE_CSRF,
    CSRF_TTL_SECONDS,
    SESSION_TTL_SECONDS,
    CurrentUser,
    _client_ip,
    _cookie_value,
    _csrf_cookie,
    _csrf_from_auth,
    _current_user,
    _form_data,
    _page,
    _redirect,
    _response,
    _session_token,
    _set_cookie,
    _valid_csrf,
    csrf_serializer,
    login_required,
)


def register_web_routes(app: object) -> None:
    @app.get("/")
    async def home(request: Request) -> Response:
        if await _current_user(request):
            return _redirect("/dashboard", 302)
        return _redirect("/login", 302)

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        csrf = secrets.token_urlsafe(24)
        response = _response(_page("auth/login.html", csrf=csrf, error=None))
        _set_cookie(response, COOKIE_CSRF, csrf_serializer.dumps(csrf), CSRF_TTL_SECONDS)
        return response

    @app.post("/login")
    async def login(request: Request) -> Response:
        form = _form_data(request)
        csrf_cookie = _cookie_value(request, COOKIE_CSRF)
        try:
            expected_csrf = csrf_serializer.loads(csrf_cookie or "", max_age=CSRF_TTL_SECONDS)
        except BadSignature, SignatureExpired:
            expected_csrf = None
        if expected_csrf is None or expected_csrf != form.get("csrf"):
            return _response(
                _page(
                    "auth/login.html",
                    csrf="",
                    error="Atualize a página e tente novamente.",
                ),
                400,
            )
        result = await login_with_throttle(
            form.get("email", ""), form.get("password", ""), _client_ip(request)
        )
        if result.session is None:
            if result.locked_minutes:
                message = (
                    "Muitas tentativas de login. Tente novamente em "
                    f"{result.locked_minutes} minuto(s)."
                )
                status = 429
            else:
                message, status = "E-mail ou senha inválidos.", 401
            response = _response(
                _page("auth/login.html", csrf=expected_csrf, error=message), status
            )
            _set_cookie(response, COOKIE_CSRF, csrf_cookie, CSRF_TTL_SECONDS)
            return response
        user, membership = result.session
        token = _session_token(user, membership)
        response = _redirect("/dashboard")
        _set_cookie(response, COOKIE_AUTH, token, SESSION_TTL_SECONDS)
        _csrf_cookie(response, secrets.token_urlsafe(24))
        return response

    @app.get("/register")
    async def register_page(request: Request) -> Response:
        csrf = secrets.token_urlsafe(24)
        response = _response(
            _page(
                "auth/register.html",
                csrf=csrf,
                error=None,
                form={},
                password_rules=PASSWORD_RULES_TEXT,
            )
        )
        _set_cookie(response, COOKIE_CSRF, csrf_serializer.dumps(csrf), CSRF_TTL_SECONDS)
        return response

    @app.post("/register")
    async def register(request: Request) -> Response:
        form = _form_data(request)
        csrf_cookie = _cookie_value(request, COOKIE_CSRF)
        try:
            expected_csrf = csrf_serializer.loads(csrf_cookie or "", max_age=CSRF_TTL_SECONDS)
        except BadSignature, SignatureExpired:
            expected_csrf = None
        if expected_csrf is None or expected_csrf != form.get("csrf"):
            return _response(
                _page(
                    "auth/register.html",
                    csrf="",
                    error="Atualize a página e tente novamente.",
                    form={},
                    password_rules=PASSWORD_RULES_TEXT,
                ),
                400,
            )
        full_name = form.get("full_name", "").strip()
        email = form.get("email", "").strip().lower()
        organization = form.get("organization", "").strip()
        password = form.get("password", "")
        password_confirm = form.get("password_confirm", "")
        # Os campos preenchidos voltam ao formulário em caso de erro; as senhas, nunca.
        filled = {"full_name": full_name, "email": email, "organization": organization}

        def register_error(message: str, status: int) -> Response:
            response = _response(
                _page(
                    "auth/register.html",
                    csrf=expected_csrf,
                    error=message,
                    form=filled,
                    password_rules=PASSWORD_RULES_TEXT,
                ),
                status,
            )
            _set_cookie(response, COOKIE_CSRF, csrf_cookie, CSRF_TTL_SECONDS)
            return response

        if not all((full_name, email, organization)):
            return register_error("Preencha nome, organização e e-mail.", 400)
        if not secrets.compare_digest(password.encode(), password_confirm.encode()):
            return register_error("A confirmação de senha não confere com a senha.", 400)
        try:
            user = await register_user(full_name, email, password, organization)
        except ValueError as exc:
            return register_error(str(exc), 400)
        except IntegrityError:
            return register_error("Este e-mail já está cadastrado.", 409)
        membership = await first_active_membership(user)
        response = _redirect("/dashboard")
        session = _session_token(user, membership)
        _set_cookie(response, COOKIE_AUTH, session, SESSION_TTL_SECONDS)
        _csrf_cookie(response, secrets.token_urlsafe(24))
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/login")
        current = await _current_user(request)
        if current is not None:
            await revoke_sessions(current[0])
        response = _redirect("/login")
        _set_cookie(response, COOKIE_AUTH, "", 0)
        return response

    @app.get("/dashboard")
    @login_required
    async def dashboard(request: Request, current: CurrentUser) -> Response:
        user, membership = current
        organization = await membership.organization
        balances = await get_account_balances(organization)
        income, expenses = await realized_totals(organization)
        open_count = await open_transaction_count(organization)
        return _response(
            _page(
                "dashboard.html",
                name=user.full_name,
                organization=organization,
                csrf=_csrf_from_auth(request),
                total_balance=sum((balance for _, balance in balances), Decimal("0.00")),
                balances=balances,
                income=income,
                expenses=expenses,
                open_count=open_count,
                recent=await recent_transactions(organization, 5),
            )
        )
