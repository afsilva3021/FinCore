"""Páginas HTML renderizadas no servidor e fluxo seguro de identidade."""

import csv
import json
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from http.cookies import SimpleCookie
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from uuid import UUID, uuid4
from xml.sax.saxutils import escape

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from robyn import Request, Response
from tortoise.exceptions import IntegrityError

from fincore.application.accounting import (
    create_journal_entry,
    create_ledger_account,
    get_ledger_balances,
    parse_line_amount,
    post_journal_entry,
    reverse_journal_entry,
    update_draft_journal_entry,
)
from fincore.application.counterparties import (
    get_counterparty,
    list_counterparties,
    save_counterparty,
    set_counterparty_active,
)
from fincore.application.finance import (
    create_account,
    create_category,
    create_movement,
    create_transfer,
    get_account_balances,
    parse_money,
    parse_opening_balance,
    reverse_movement,
    set_account_active,
    settle_movement,
    update_account,
)
from fincore.application.identity import (
    PASSWORD_RULES_TEXT,
    login_with_throttle,
    register_user,
    revoke_sessions,
)
from fincore.application.members import (
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    add_member,
    can_manage_members,
    can_write,
    change_member_role,
    list_members,
    role_label,
    set_member_active,
)
from fincore.application.money import parse_api_amount
from fincore.application.reconciliation import (
    import_ofx,
    match_statement_transaction,
    unmatch_statement_transaction,
)
from fincore.application.reports import realized_totals, transaction_report
from fincore.application.simulated_integrations import simulate_adapter
from fincore.application.titles import (
    cancel_title,
    create_titles,
    parse_adjustment,
    settle_title,
    title_balances,
)
from fincore.config.settings import settings
from fincore.infrastructure.cep_lookup import CepLookupError, lookup_cep
from fincore.infrastructure.models import (
    Category,
    FinancialAccount,
    FinancialTitle,
    FinancialTransaction,
    ImportedStatementTransaction,
    JournalEntry,
    JournalEntryLine,
    Membership,
    Organization,
    ReconciliationMatch,
    TitleSettlement,
    User,
)
from fincore.presentation.health import database_is_ready

COOKIE_AUTH = "fincore_session"
COOKIE_CSRF = "fincore_csrf"
SESSION_TTL_SECONDS = 60 * 60 * 12
# O token CSRF precisa durar tanto quanto a sessão; senão os formulários passam a
# falhar silenciosamente depois que o cookie CSRF expira com a sessão ainda válida.
CSRF_TTL_SECONDS = SESSION_TTL_SECONDS
serializer = URLSafeTimedSerializer(settings.auth_secret)
csrf_serializer = URLSafeTimedSerializer(settings.auth_secret, salt="fincore-csrf")
template_env = Environment(
    loader=FileSystemLoader(Path(__file__).parents[3] / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)
# Perfil do membro da requisição atual, definido em _current_user e lido pelo layout para
# mostrar o perfil, o menu de membros e o aviso de somente leitura.
_current_role: ContextVar[str] = ContextVar("fincore_current_role", default="")
template_env.globals["current_role"] = lambda: _current_role.get()
template_env.globals["role_label"] = role_label
template_env.globals["can_write"] = lambda: can_write(_current_role.get())
template_env.globals["can_manage_members"] = lambda: can_manage_members(_current_role.get())

# Escritas que não exigem perfil com permissão de alteração.
WRITE_EXEMPT_PATHS = frozenset({"/login", "/register", "/logout"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def enforce_write_permission(request: Request) -> Request | Response:
    """Middleware global: perfis sem permissão de escrita não alteram nada.

    Fica antes de todas as rotas, então uma rota nova já nasce protegida (negar por
    padrão). Requisições sem sessão seguem para a rota, que responde 401/redirect.
    """
    path = request.url.path
    if request.method.upper() not in WRITE_METHODS or path in WRITE_EXEMPT_PATHS:
        return request
    current = await _current_user(request)
    if current is None or can_write(current[1].role):
        return request
    message = "Seu perfil é somente leitura; peça a um proprietário para alterar o acesso."
    if path.startswith("/api/"):
        return Response(
            status_code=403,
            headers={"Content-Type": "application/json; charset=utf-8"},
            description=json.dumps({"error": {"code": "forbidden", "message": message}}),
        )
    return Response(
        status_code=403,
        headers={"Content-Type": "text/html; charset=utf-8"},
        description=_page("forbidden.html", message=message),
    )


def _format_tax_id(value: str | None) -> str:
    if not value:
        return "—"
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return value


template_env.filters["format_tax_id"] = _format_tax_id


def _response(body: str, status: int = 200) -> Response:
    return Response(
        status_code=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=body,
    )


def _redirect(location: str, status: int = 303) -> Response:
    return Response(status_code=status, headers={"Location": location}, body="")


def _cookie_value(request: Request, name: str) -> str | None:
    cookie = SimpleCookie()
    cookie.load(request.headers.get("cookie") or "")
    morsel = cookie.get(name)
    return morsel.value if morsel else None


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        key=name,
        value=value,
        path="/",
        max_age=max_age,
        secure=settings.secure_cookies,
        http_only=name == COOKIE_AUTH,
        same_site="Lax",
    )


def _csrf_cookie(response: Response, token: str) -> None:
    _set_cookie(response, COOKIE_CSRF, csrf_serializer.dumps(token), CSRF_TTL_SECONDS)


def _valid_csrf(request: Request, submitted: str) -> bool:
    cookie_value = _cookie_value(request, COOKIE_CSRF)
    if not cookie_value or not submitted:
        return False
    try:
        cookie_token = csrf_serializer.loads(cookie_value, max_age=CSRF_TTL_SECONDS)
    except BadSignature, SignatureExpired:
        return False
    return secrets.compare_digest(str(cookie_token).encode(), submitted.encode())


def _session_token(user: User, membership: Membership) -> str:
    return serializer.dumps(
        {"uid": str(user.id), "org": str(membership.organization_id), "sv": user.session_version}
    )


def _client_ip(request: Request) -> str | None:
    """IP do cliente; o X-Forwarded-For só é aceito quando vem de um proxy confiável."""
    peer = request.ip_addr
    if peer and peer in settings.trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for") or ""
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        for hop in reversed(hops):
            if hop not in settings.trusted_proxies:
                return hop
    return peer


async def _current_user(request: Request) -> tuple[User, Membership] | None:
    token = _cookie_value(request, COOKIE_AUTH)
    if not token:
        return None
    try:
        payload = serializer.loads(token, max_age=SESSION_TTL_SECONDS)
    except BadSignature, SignatureExpired:
        return None
    user = await User.get_or_none(id=payload.get("uid"), is_active=True)
    if user is None or payload.get("sv") != user.session_version:
        return None
    membership = await Membership.filter(
        user=user, organization_id=payload.get("org"), is_active=True, organization__is_active=True
    ).first()
    if membership is None:
        return None
    _current_role.set(membership.role)
    return user, membership


PAGE_SIZE = 50
MAX_PAGE = 10_000


@dataclass(frozen=True, slots=True)
class PageInfo:
    number: int
    has_previous: bool
    has_next: bool
    params: dict[str, str]

    def url(self, number: int) -> str:
        return "?" + urlencode({**self.params, "page": number})


def _page_number(request: Request) -> int:
    try:
        number = int(request.query_params.get("page", "1") or "1")
    except ValueError:
        return 1
    return min(max(number, 1), MAX_PAGE)


async def _paginate(query, request: Request, *, keep: tuple[str, ...] = (), size: int = PAGE_SIZE):
    """Busca uma página (size + 1 linhas para saber se há próxima) e os links de navegação."""
    number = _page_number(request)
    rows = list(await query.offset((number - 1) * size).limit(size + 1))
    params = {key: value for key in keep if (value := request.query_params.get(key, ""))}
    info = PageInfo(number, number > 1, len(rows) > size, params)
    return rows[:size], info


def _form_data(request: Request) -> dict[str, str]:
    body = request.body.decode() if isinstance(request.body, bytes) else request.body
    return {key: values[-1] for key, values in parse_qs(body or "", keep_blank_values=True).items()}


def _page(template: str, **context: object) -> str:
    return template_env.get_template(template).render(**context)


def _form_uuid(form: dict[str, str], key: str, message: str) -> UUID:
    """Lê um UUID do formulário trocando a mensagem técnica por uma em pt-BR."""
    try:
        return UUID(form.get(key, ""))
    except ValueError:
        raise ValueError(message) from None


def _form_date(form: dict[str, str], key: str, message: str) -> date:
    try:
        return date.fromisoformat(form.get(key, ""))
    except ValueError:
        raise ValueError(message) from None


async def _title_from_path(request: Request, organization: Organization) -> FinancialTitle | None:
    try:
        title_id = UUID(request.path_params["title_id"])
    except KeyError, ValueError:
        return None
    return await FinancialTitle.get_or_none(id=title_id, organization=organization)


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
        membership = await Membership.filter(user=user, is_active=True).first()
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
    async def dashboard(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        user, membership = current
        organization = await membership.organization
        balances = await get_account_balances(organization)
        income, expenses = await realized_totals(organization)
        open_count = await FinancialTransaction.filter(
            organization=organization, status="open"
        ).count()
        return _response(
            _page(
                "dashboard.html",
                name=user.full_name,
                organization=organization,
                csrf=_csrf_from_auth(request),
                total_balance=sum((balance for _, balance in balances), Decimal("0.00")),
                income=income,
                expenses=expenses,
                open_count=open_count,
                recent=await FinancialTransaction.filter(organization=organization)
                .select_related("account")
                .order_by("-created_at")
                .limit(5),
            )
        )


def _csrf_from_auth(request: Request) -> str:
    cookie_value = _cookie_value(request, COOKIE_CSRF)
    if not cookie_value:
        return ""
    try:
        return csrf_serializer.loads(cookie_value, max_age=CSRF_TTL_SECONDS)
    except BadSignature, SignatureExpired:
        return ""


async def _render_accounts(
    request: Request,
    user: User,
    organization: Organization,
    error: str | None = None,
    editing_account: FinancialAccount | None = None,
    form: dict[str, str] | None = None,
) -> str:
    return _page(
        "finance/accounts.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        accounts=await get_account_balances(organization, include_inactive=True),
        active="accounts",
        section="Bancos e contas",
        error=error,
        editing_account=editing_account,
        form=form or {},
    )


async def _render_categories(
    request: Request,
    user: User,
    organization: Organization,
    error: str | None = None,
    form: dict[str, str] | None = None,
) -> str:
    return _page(
        "finance/categories.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        categories=await Category.filter(organization=organization, is_active=True)
        .select_related("parent")
        .order_by("transaction_type", "name"),
        parent_categories=await Category.filter(
            organization=organization,
            is_active=True,
            category_kind="synthetic",
            parent_id=None,
        ).order_by("transaction_type", "name"),
        active="categories",
        section="Categorias",
        error=error,
        form=form or {},
    )


async def _render_counterparties(
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
) -> str:
    if role not in {"all", "customer", "supplier"}:
        role = "all"
    if status not in {"all", "active", "inactive"}:
        status = "all"
    if form is None:
        form = {
            "person_type": "individual",
            "is_customer": "true" if role == "customer" else "",
            "is_supplier": "true" if role == "supplier" else "",
        }
    number = _page_number(request)
    rows = await list_counterparties(
        organization,
        role=role,
        status=status,
        limit=PAGE_SIZE + 1,
        offset=(number - 1) * PAGE_SIZE,
    )
    contacts = rows[:PAGE_SIZE]
    params = {
        key: value for key in ("role", "status") if (value := request.query_params.get(key, ""))
    }
    pagination = PageInfo(number, number > 1, len(rows) > PAGE_SIZE, params)
    return _page(
        "finance/contacts.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        contacts=contacts,
        pagination=pagination,
        role=role,
        status=status,
        form=form,
        editing=editing,
        open_drawer=editing or error is not None,
        error=error,
        message=message,
        page_title={"customer": "Clientes", "supplier": "Fornecedores"}.get(
            role, "Clientes e fornecedores"
        ),
        form_title={"customer": "cliente", "supplier": "fornecedor"}.get(role, "contato"),
        active="contacts" if role == "all" else role + "s",
        section={"customer": "Clientes", "supplier": "Fornecedores"}.get(
            role, "Clientes e fornecedores"
        ),
    )


async def _render_transactions(
    request: Request,
    user: User,
    organization: Organization,
    error: str | None = None,
    form: dict[str, str] | None = None,
    default_type: str = "income",
) -> str:
    transactions, pagination = await _paginate(
        FinancialTransaction.filter(organization=organization)
        .select_related("account", "category")
        .order_by("-competence_date", "-created_at"),
        request,
    )
    return _page(
        "finance/transactions.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        operation_key=str(uuid4()),
        today=date.today().isoformat(),
        accounts=await FinancialAccount.filter(organization=organization, is_active=True).order_by(
            "name"
        ),
        categories=await Category.filter(
            organization=organization, is_active=True, category_kind="analytic"
        )
        .select_related("parent")
        .order_by("name"),
        transactions=transactions,
        pagination=pagination,
        reversal_keys={str(item.id): str(uuid4()) for item in transactions},
        error=error,
        form=form or {"transaction_type": default_type},
    )


async def _render_title_register(
    request: Request,
    user: User,
    organization: Organization,
    title_type: str,
    error: str | None = None,
    form: dict[str, str] | None = None,
) -> str:
    """Renderiza títulos próprios, sem consultar o extrato de transações."""
    query_params = request.query_params
    search = query_params.get("q", "").strip()[:120]
    status = query_params.get("status", "open")
    if status not in {"open", "partial", "settled", "overdue", "cancelled", "all"}:
        status = "open"
    start_raw = query_params.get("due_from", "")
    end_raw = query_params.get("due_to", "")
    try:
        due_from = date.fromisoformat(start_raw) if start_raw else None
        due_to = date.fromisoformat(end_raw) if end_raw else None
    except ValueError:
        due_from = due_to = None
    today = date.today()
    base_query = FinancialTitle.filter(organization=organization, title_type=title_type)
    if search:
        from tortoise.expressions import Q

        base_query = base_query.filter(
            Q(description__icontains=search) | Q(counterparty__name__icontains=search)
        )
    if due_from:
        base_query = base_query.filter(due_date__gte=due_from)
    if due_to:
        base_query = base_query.filter(due_date__lte=due_to)
    if status == "open":
        base_query = base_query.filter(status="open")
    elif status == "partial":
        base_query = base_query.filter(status="partial")
    elif status == "settled":
        base_query = base_query.filter(status="settled")
    elif status == "overdue":
        base_query = base_query.filter(status__in=["open", "partial"], due_date__lt=today)
    elif status == "cancelled":
        base_query = base_query.filter(status="cancelled")
    titles, pagination = await _paginate(
        base_query.select_related("account", "category", "counterparty").order_by(
            "due_date", "-created_at"
        ),
        request,
        keep=("status",),
    )
    is_payable = title_type == "payable"
    all_titles = await FinancialTitle.filter(
        organization=organization, title_type=title_type, status__in=["open", "partial"]
    ).all()
    all_balances = await title_balances(all_titles)
    overdue_titles = [item for item in all_titles if item.due_date < today]
    balances = await title_balances(titles)
    settlement_ids = set(
        await TitleSettlement.filter(title_id__in=[item.id for item in titles]).values_list(
            "title_id", flat=True
        )
    )
    accounts = await FinancialAccount.filter(organization=organization, is_active=True).order_by(
        "name"
    )
    categories = (
        await Category.filter(
            organization=organization,
            is_active=True,
            transaction_type="expense" if is_payable else "income",
            category_kind="analytic",
        )
        .select_related("parent")
        .order_by("name")
    )
    return _page(
        "finance/title_register.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        active="payables" if is_payable else "receivables",
        section="Contas a pagar" if is_payable else "Contas a receber",
        title_type=title_type,
        accounts=accounts,
        categories=categories,
        counterparties=await list_counterparties(
            organization,
            role="supplier" if is_payable else "customer",
            status="active",
        ),
        payment_methods=[
            ("bank_transfer", "Transferência"),
            ("pix", "Pix"),
            ("boleto", "Boleto"),
            ("card", "Cartão"),
            ("cash", "Dinheiro"),
            ("other", "Outro"),
        ],
        titles=titles,
        pagination=pagination,
        title_states={
            str(item.id): "overdue"
            if item.due_date < today and item.status in {"open", "partial"}
            else item.status
            for item in titles
        },
        balances=balances,
        settlement_ids=settlement_ids,
        operation_keys={str(item.id): str(uuid4()) for item in titles},
        title_count=len(titles),
        open_count=len(all_titles),
        open_total=sum(all_balances.values(), Decimal("0.00")),
        overdue_count=len(overdue_titles),
        overdue_total=sum((all_balances[item.id] for item in overdue_titles), Decimal("0.00")),
        today=today.isoformat(),
        search=search,
        status=status,
        due_from=due_from.isoformat() if due_from else start_raw,
        due_to=due_to.isoformat() if due_to else end_raw,
        issue_today=today.isoformat(),
        form=form or {},
        error=error,
    )


def register_finance_routes(app: object) -> None:
    @app.get("/accounts")
    async def accounts_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return _response(await _render_accounts(request, current[0], await current[1].organization))

    @app.post("/accounts")
    async def create_account_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounts")
        try:
            opening_balance = parse_opening_balance(form.get("opening_balance", ""))
            account_type = form.get("account_type", "")
            bank_code = form.get("bank_code", "").strip()
            branch_number = form.get("branch_number", "").strip()
            account_number = form.get("account_number", "").strip()
            if account_type == "bank" and not all((bank_code, branch_number, account_number)):
                raise ValueError("Informe código do banco, agência com dígito e número da conta.")
            await create_account(
                organization,
                current[0],
                form.get("name", "").strip(),
                account_type,
                opening_balance,
                form.get("bank_name", ""),
                bank_code,
                branch_number,
                account_number,
            )
        except ValueError as exc:
            return _response(
                await _render_accounts(request, current[0], organization, str(exc)), 400
            )
        except IntegrityError:
            return _response(
                await _render_accounts(
                    request, current[0], organization, "Já existe uma conta com esse nome."
                ),
                409,
            )
        return _redirect("/accounts")

    @app.get("/accounts/:account_id/edit")
    async def edit_account_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        try:
            account_id = UUID(request.path_params["account_id"])
        except ValueError, KeyError:
            return _redirect("/accounts")
        organization = await current[1].organization
        account = await FinancialAccount.get_or_none(id=account_id, organization=organization)
        if account is None:
            return _redirect("/accounts")
        return _response(
            await _render_accounts(request, current[0], organization, editing_account=account)
        )

    @app.post("/accounts/:account_id/update")
    async def update_account_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        try:
            account_id = UUID(request.path_params["account_id"])
        except ValueError, KeyError:
            return _redirect("/accounts")
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounts")
        organization = await current[1].organization
        account = await FinancialAccount.get_or_none(id=account_id, organization=organization)
        if account is None:
            return _redirect("/accounts")
        try:
            await update_account(
                organization,
                account_id,
                form.get("name", "").strip(),
                form.get("account_type", ""),
                form.get("bank_name", ""),
                form.get("bank_code", ""),
                form.get("branch_number", ""),
                form.get("account_number", ""),
            )
        except ValueError as exc:
            return _response(
                await _render_accounts(request, current[0], organization, str(exc), account, form),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_accounts(
                    request,
                    current[0],
                    organization,
                    "Já existe uma conta com esse nome.",
                    account,
                    form,
                ),
                409,
            )
        return _redirect("/accounts")

    @app.post("/accounts/:account_id/active")
    async def set_account_active_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        try:
            account_id = UUID(request.path_params["account_id"])
        except ValueError, KeyError:
            return _redirect("/accounts")
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounts")
        if form.get("is_active") not in {"true", "false"}:
            return _redirect("/accounts")
        try:
            await set_account_active(
                await current[1].organization,
                account_id,
                form["is_active"] == "true",
            )
        except ValueError:
            pass
        return _redirect("/accounts")

    @app.get("/categories")
    async def categories_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return _response(
            await _render_categories(request, current[0], await current[1].organization)
        )

    @app.post("/categories")
    async def create_category_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/categories")
        try:
            await create_category(
                organization,
                current[0],
                form.get("name", "").strip(),
                form.get("transaction_type", ""),
                form.get("category_kind", "analytic"),
                UUID(form["parent_id"]) if form.get("parent_id") else None,
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_categories(
                    request,
                    current[0],
                    organization,
                    str(exc) or "Selecione uma categoria superior válida.",
                    form,
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_categories(
                    request,
                    current[0],
                    organization,
                    "Essa categoria já existe para esse tipo.",
                    form,
                ),
                409,
            )
        return _redirect("/categories")

    @app.get("/transactions")
    async def transactions_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        default_type = request.query_params.get("type", "income")
        if default_type not in {"income", "expense", "transfer"}:
            default_type = "income"
        return _response(
            await _render_transactions(
                request,
                current[0],
                await current[1].organization,
                default_type=default_type,
            )
        )

    @app.get("/payables")
    async def payables_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return _response(
            await _render_title_register(
                request, current[0], await current[1].organization, "payable"
            )
        )

    @app.get("/receivables")
    async def receivables_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return _response(
            await _render_title_register(
                request, current[0], await current[1].organization, "receivable"
            )
        )

    async def _submit_title_form(request: Request, title_type: str) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        target = "/payables" if title_type == "payable" else "/receivables"
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect(target)
        try:
            installment_count = int(form.get("installment_count", "1"))
            category_id = UUID(form["category_id"]) if form.get("category_id") else None
            await create_titles(
                organization=organization,
                user=current[0],
                title_type=title_type,
                description=form.get("description", ""),
                counterparty_name=form.get("counterparty_name", ""),
                party_type=form.get(
                    "party_type", "supplier" if title_type == "payable" else "customer"
                ),
                account_id=UUID(form.get("account_id", "")),
                category_id=category_id,
                amount=parse_money(form.get("amount", "")),
                issue_date=date.fromisoformat(form.get("issue_date", "")),
                due_date=date.fromisoformat(form.get("due_date", "")),
                payment_method=form.get("payment_method", "other"),
                payment_terms=form.get("payment_terms", "cash"),
                installment_count=installment_count,
                document_number=form.get("document_number", ""),
                document_type=form.get("document_type", ""),
                document_series=form.get("document_series", ""),
                cost_center=form.get("cost_center", ""),
                project=form.get("project", ""),
                expected_receipt_date=(
                    date.fromisoformat(form["expected_receipt_date"])
                    if form.get("expected_receipt_date")
                    else None
                ),
                collection_sent_date=(
                    date.fromisoformat(form["collection_sent_date"])
                    if form.get("collection_sent_date")
                    else None
                ),
                barcode=form.get("barcode", ""),
                beneficiary=form.get("beneficiary", ""),
                approver_reference=form.get("approver_reference", ""),
                payment_details=form.get("payment_details", ""),
                related_reference=form.get("related_reference", ""),
                charge_reference=form.get("charge_reference", ""),
                attachment_reference=form.get("attachment_reference", ""),
                notes=form.get("notes", ""),
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_title_register(
                    request,
                    current[0],
                    organization,
                    title_type,
                    str(exc) or "Confira os dados do título.",
                    form,
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_title_register(
                    request,
                    current[0],
                    organization,
                    title_type,
                    "Não foi possível salvar o título; confira os dados e tente novamente.",
                    form,
                ),
                409,
            )
        return _redirect(target)

    @app.post("/payables")
    async def create_payable_title(request: Request) -> Response:
        return await _submit_title_form(request, "payable")

    @app.post("/receivables")
    async def create_receivable_title(request: Request) -> Response:
        return await _submit_title_form(request, "receivable")

    @app.post("/titles/:title_id/settlements")
    async def title_settlement_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        title = await _title_from_path(request, organization)
        title_type = title.title_type if title else "payable"
        target = "/payables" if title_type == "payable" else "/receivables"
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect(target)
        try:
            if title is None:
                raise ValueError("Título não encontrado nesta organização.")
            await settle_title(
                organization=organization,
                user=current[0],
                title_id=title.id,
                operation_key=_form_uuid(
                    form, "operation_key", "Atualize a página e tente a baixa novamente."
                ),
                amount_applied=parse_money(form.get("amount_applied", "")),
                paid_on=_form_date(form, "paid_on", "Informe uma data de pagamento válida."),
                account_id=_form_uuid(
                    form, "account_id", "Selecione uma conta financeira válida para a baixa."
                ),
                payment_method=form.get("payment_method", "other"),
                discount=parse_adjustment(form.get("discount", "0")),
                interest=parse_adjustment(form.get("interest", "0")),
                fine=parse_adjustment(form.get("fine", "0")),
                fees=parse_adjustment(form.get("fees", "0")),
                proof_reference=form.get("proof_reference", ""),
                notes=form.get("notes", ""),
            )
            return _redirect(target)
        except ValueError as exc:
            return _response(
                await _render_title_register(
                    request, current[0], organization, title_type, str(exc)
                ),
                400,
            )

    @app.post("/titles/:title_id/cancel")
    async def cancel_title_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        title = await _title_from_path(request, organization)
        title_type = title.title_type if title else "payable"
        target = "/payables" if title_type == "payable" else "/receivables"
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect(target)
        try:
            if title is None:
                raise ValueError("Título não encontrado nesta organização.")
            await cancel_title(organization=organization, title_id=title.id)
            return _redirect(target)
        except ValueError as exc:
            return _response(
                await _render_title_register(
                    request, current[0], organization, title_type, str(exc)
                ),
                400,
            )

    @app.post("/transactions")
    async def create_transaction_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/transactions")
        try:
            operation_key = UUID(form.get("operation_key", ""))
            account_id = UUID(form.get("account_id", ""))
            competence_date = date.fromisoformat(form.get("competence_date", ""))
            amount = parse_money(form.get("amount", ""))
            transaction_type = form.get("transaction_type", "")
            if transaction_type == "transfer":
                await create_transfer(
                    organization=organization,
                    user=current[0],
                    operation_key=operation_key,
                    source_id=account_id,
                    target_id=UUID(form.get("target_account_id", "")),
                    amount=amount,
                    description=form.get("description", "").strip(),
                    competence_date=competence_date,
                )
            else:
                category_id = UUID(form["category_id"]) if form.get("category_id") else None
                due_date = date.fromisoformat(form["due_date"]) if form.get("due_date") else None
                await create_movement(
                    organization=organization,
                    user=current[0],
                    operation_key=operation_key,
                    account_id=account_id,
                    category_id=category_id,
                    transaction_type=transaction_type,
                    description=form.get("description", "").strip(),
                    amount=amount,
                    competence_date=competence_date,
                    due_date=due_date,
                    settled=form.get("status") == "settled",
                )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_transactions(request, current[0], organization, str(exc), form),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_transactions(
                    request,
                    current[0],
                    organization,
                    "Não foi possível salvar; confira se a movimentação já foi enviada.",
                    form,
                ),
                409,
            )
        return _redirect("/transactions")

    @app.post("/transactions/:transaction_id/settle")
    async def settle_transaction_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/transactions")
        try:
            movement_id = UUID(request.path_params["transaction_id"])
        except ValueError, KeyError:
            return _redirect("/transactions")
        await settle_movement(await current[1].organization, current[0], movement_id)
        return_to = form.get("return_to", "")
        if return_to not in {"/payables", "/receivables"}:
            return_to = "/transactions"
        return _redirect(return_to)

    @app.post("/transactions/:transaction_id/reverse")
    async def reverse_transaction_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/transactions")
        try:
            await reverse_movement(
                organization,
                current[0],
                UUID(request.path_params["transaction_id"]),
                UUID(form.get("operation_key", "")),
                date.today(),
            )
        except (ValueError, KeyError, IntegrityError) as exc:
            return _response(
                await _render_transactions(
                    request,
                    current[0],
                    organization,
                    str(exc) or "Não foi possível estornar esta movimentação.",
                ),
                400,
            )
        return _redirect("/transactions")


async def _render_accounting(
    request: Request,
    user: User,
    organization: Organization,
    error: str | None = None,
    open_drawer: str | None = None,
) -> str:
    entries, pagination = await _paginate(
        JournalEntry.filter(organization=organization).order_by("-entry_date", "-created_at"),
        request,
    )
    # Todas as linhas dos lançamentos exibidos numa só query (antes: uma por lançamento).
    lines_by_entry: dict[UUID, list[JournalEntryLine]] = {}
    for line in await JournalEntryLine.filter(
        journal_entry_id__in=[entry.id for entry in entries]
    ).select_related("account"):
        lines_by_entry.setdefault(line.journal_entry_id, []).append(line)
    entry_rows = []
    for entry in entries:
        lines = lines_by_entry.get(entry.id, [])
        debit = sum((line.debit for line in lines), Decimal("0.00"))
        credit = sum((line.credit for line in lines), Decimal("0.00"))
        entry_rows.append((entry, lines, debit, credit))
    reversals = set(
        await JournalEntry.filter(organization=organization)
        .exclude(reversal_of_id=None)
        .values_list("reversal_of_id", flat=True)
    )
    accounts = await get_ledger_balances(organization)
    return _page(
        "accounting/index.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        idempotency_key=str(uuid4()),
        today=date.today().isoformat(),
        accounts=accounts,
        posting_accounts=[
            (account, balance) for account, balance in accounts if not account.is_synthetic
        ],
        parent_accounts=[
            (account, balance) for account, balance in accounts if account.is_synthetic
        ],
        entries=entry_rows,
        pagination=pagination,
        reversed_ids=reversals,
        error=error,
        open_drawer=open_drawer,
    )


def register_accounting_routes(app: object) -> None:
    @app.get("/accounting")
    async def accounting_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return _response(
            await _render_accounting(request, current[0], await current[1].organization)
        )

    @app.post("/accounting/accounts")
    async def create_ledger_account_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            await create_ledger_account(
                organization,
                current[0],
                form.get("code", "").strip(),
                form.get("name", "").strip(),
                form.get("account_type", ""),
                UUID(form["parent_id"]) if form.get("parent_id") else None,
                form.get("account_kind", "analytic") == "synthetic",
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(
                    request,
                    current[0],
                    organization,
                    str(exc) or "Selecione uma conta superior válida.",
                    open_drawer="account",
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_accounting(
                    request,
                    current[0],
                    organization,
                    "Este código contábil já está em uso.",
                    open_drawer="account",
                ),
                409,
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals")
    async def create_journal_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            entry = await create_journal_entry(
                organization=organization,
                user=current[0],
                idempotency_key=UUID(form.get("idempotency_key", "")),
                entry_date=date.fromisoformat(form.get("entry_date", "")),
                description=form.get("description", "").strip(),
                lines=_journal_lines_from_form(form),
            )
            if form.get("action") == "post":
                await post_journal_entry(organization, entry.id)
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(
                    request, current[0], organization, str(exc), open_drawer="entry"
                ),
                400,
            )
        except IntegrityError:
            return _response(
                await _render_accounting(
                    request,
                    current[0],
                    organization,
                    "Não foi possível salvar o lançamento.",
                    open_drawer="entry",
                ),
                409,
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals/:entry_id/update")
    async def update_draft_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            entry = await update_draft_journal_entry(
                organization=organization,
                entry_id=UUID(request.path_params["entry_id"]),
                entry_date=date.fromisoformat(form.get("entry_date", "")),
                description=form.get("description", ""),
                lines=_journal_lines_from_form(form),
            )
            if form.get("action") == "post":
                await post_journal_entry(organization, entry.id)
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(request, current[0], organization, str(exc)), 400
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals/:entry_id/post")
    async def post_journal_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            await post_journal_entry(organization, UUID(request.path_params["entry_id"]))
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(request, current[0], organization, str(exc)), 400
            )
        return _redirect("/accounting")

    @app.post("/accounting/journals/:entry_id/reverse")
    async def reverse_journal_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/accounting")
        try:
            await reverse_journal_entry(
                organization, current[0], UUID(request.path_params["entry_id"])
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await _render_accounting(request, current[0], organization, str(exc)), 400
            )
        return _redirect("/accounting")


def register_reconciliation_routes(app: object) -> None:
    async def render_reconciliation(
        request: Request,
        user: User,
        organization: Organization,
        message: str | None = None,
        error: str | None = None,
        form: dict[str, str] | None = None,
    ) -> str:
        imported, pagination = await _paginate(
            ImportedStatementTransaction.filter(organization=organization)
            .select_related("account")
            .order_by("-posted_at", "-imported_at"),
            request,
        )
        # Vínculos da página numa só query (antes: uma por linha do extrato).
        matches = {
            match.statement_transaction_id: match
            for match in await ReconciliationMatch.filter(
                statement_transaction_id__in=[item.id for item in imported]
            )
        }
        rows = []
        for item in imported:
            match = matches.get(item.id)
            candidates = (
                await FinancialTransaction.filter(
                    organization=organization,
                    account_id=item.account_id,
                    competence_date=item.posted_at,
                    status="settled",
                )
                .order_by("description")
                .all()
            )
            rows.append((item, match, candidates))
        return _page(
            "finance/reconciliation.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            accounts=await FinancialAccount.filter(
                organization=organization, is_active=True
            ).order_by("name"),
            rows=rows,
            pagination=pagination,
            message=message,
            error=error,
            form=form or {},
        )

    @app.get("/reconciliation")
    async def reconciliation_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return _response(
            await render_reconciliation(request, current[0], await current[1].organization)
        )

    @app.post("/reconciliation/import")
    async def import_statement_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/reconciliation")
        try:
            created, duplicates = await import_ofx(
                organization, UUID(form.get("account_id", "")), form.get("ofx_content", "")
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await render_reconciliation(
                    request, current[0], organization, error=str(exc), form=form
                ),
                400,
            )
        return _response(
            await render_reconciliation(
                request,
                current[0],
                organization,
                message=(
                    f"{created} lançamento(s) importado(s); {duplicates} duplicado(s) ignorado(s)."
                ),
            )
        )

    @app.post("/reconciliation/:statement_id/match")
    async def match_statement_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/reconciliation")
        try:
            await match_statement_transaction(
                organization,
                current[0],
                UUID(request.path_params["statement_id"]),
                UUID(form.get("financial_transaction_id", "")),
            )
        except (ValueError, KeyError, IntegrityError) as exc:
            return _response(
                await render_reconciliation(
                    request, current[0], organization, error=str(exc) or "Vínculo já utilizado."
                ),
                400,
            )
        return _redirect("/reconciliation")

    @app.post("/reconciliation/:statement_id/unmatch")
    async def unmatch_statement_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        form = _form_data(request)
        organization = await current[1].organization
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/reconciliation")
        try:
            await unmatch_statement_transaction(
                organization, current[0], UUID(request.path_params["statement_id"])
            )
        except (ValueError, KeyError) as exc:
            return _response(
                await render_reconciliation(request, current[0], organization, error=str(exc)), 400
            )
        return _redirect("/reconciliation")


def register_report_routes(app: object) -> None:
    def report_dates(request: Request) -> tuple[date, date]:
        query = request.query_params
        today = date.today()
        default_start = today.replace(day=1)
        try:
            start = date.fromisoformat(query.get("start", default_start.isoformat()))
            end = date.fromisoformat(query.get("end", today.isoformat()))
        except ValueError as exc:
            raise ValueError("Informe um período válido.") from exc
        return start, end

    @app.get("/reports")
    async def reports_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        user, membership = current
        organization = await membership.organization
        try:
            start, end = report_dates(request)
            movements, income, expenses = await transaction_report(organization, start, end)
        except ValueError as exc:
            return _response(
                _page(
                    "finance/reports.html",
                    name=user.full_name,
                    organization=organization,
                    csrf=_csrf_from_auth(request),
                    error=str(exc),
                    start=date.today().replace(day=1).isoformat(),
                    end=date.today().isoformat(),
                    movements=[],
                    income=Decimal("0"),
                    expenses=Decimal("0"),
                )
            )
        return _response(
            _page(
                "finance/reports.html",
                name=user.full_name,
                organization=organization,
                csrf=_csrf_from_auth(request),
                error=None,
                start=start.isoformat(),
                end=end.isoformat(),
                movements=movements,
                income=income,
                expenses=expenses,
            )
        )

    @app.get("/reports.csv")
    async def reports_csv(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        try:
            start, end = report_dates(request)
            organization = await current[1].organization
            movements, _, _ = await transaction_report(organization, start, end)
        except ValueError as exc:
            return _response(str(exc), 400)
        output = StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(
            [
                "Data competência",
                "Movimentação",
                "Descrição",
                "Conta",
                "Categoria",
                "Status",
                "Valor BRL",
            ]
        )
        for item in movements:
            signed = (
                item.amount if item.transaction_type in {"income", "transfer_in"} else -item.amount
            )
            writer.writerow(
                [
                    item.competence_date.isoformat(),
                    item.transaction_type,
                    item.description,
                    item.account.name,
                    item.category.name if item.category else "",
                    item.status,
                    f"{signed:.2f}",
                ]
            )
        return Response(
            status_code=200,
            headers={
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="fincore-{start}-{end}.csv"',
            },
            body="\ufeff" + output.getvalue(),
        )

    @app.get("/reports.pdf")
    async def reports_pdf(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        try:
            start, end = report_dates(request)
            organization = await current[1].organization
            movements, income, expenses = await transaction_report(organization, start, end)
        except ValueError as exc:
            return _response(str(exc), 400)
        buffer = BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=landscape(A4), title="Relatório FinCore")
        styles = getSampleStyleSheet()
        summary = (
            f"Receitas realizadas: R$ {income:.2f} · Despesas realizadas: R$ {expenses:.2f} "
            f"· Saldo do período: R$ {income - expenses:.2f}"
        )
        story = [
            Paragraph("FinCore · Relatório financeiro", styles["Title"]),
            Paragraph(
                f"{escape(organization.name)} · {start:%d/%m/%Y} a {end:%d/%m/%Y}",
                styles["Normal"],
            ),
            Spacer(1, 12),
            Paragraph(summary, styles["Normal"]),
            Spacer(1, 12),
        ]
        rows = [["Data", "Tipo", "Descrição", "Conta", "Categoria", "Estado", "Valor BRL"]]
        for item in movements:
            signed = (
                item.amount if item.transaction_type in {"income", "transfer_in"} else -item.amount
            )
            rows.append(
                [
                    item.competence_date.strftime("%d/%m/%Y"),
                    item.transaction_type,
                    item.description[:50],
                    item.account.name[:24],
                    item.category.name[:24] if item.category else "—",
                    item.status,
                    f"{signed:.2f}",
                ]
            )
        table = Table(rows, repeatRows=1, colWidths=[65, 65, 185, 110, 110, 55, 75])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102a43")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(table)
        document.build(story)
        return Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f'attachment; filename="fincore-{start}-{end}.pdf"',
            },
            body=buffer.getvalue(),
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
    async def integrations_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return _response(await render_demo(request, current[0], await current[1].organization))

    @app.post("/integrations/simulate")
    async def simulate_integration_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
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


def register_api_routes(app: object) -> None:
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
    async def api_accounts(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return json_response(
                {"error": {"code": "unauthenticated", "message": "Sessão necessária."}}, 401
            )
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
    async def api_create_account(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
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
    async def api_create_category(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
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

    @app.get("/api/v1/categories")
    async def api_categories(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        organization = await current[1].organization
        query = Category.filter(organization=organization, is_active=True)
        total = await query.count()
        categories = await query.order_by("transaction_type", "name").offset(offset).limit(limit)
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
    async def api_transactions(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return json_response(
                {"error": {"code": "unauthenticated", "message": "Sessão necessária."}}, 401
            )
        try:
            limit, offset = pagination(request)
        except ValueError:
            return json_response(
                {"error": {"code": "invalid_pagination", "message": "Paginação inválida."}}, 400
            )
        query = FinancialTransaction.filter(organization=await current[1].organization)
        if request.query_params.get("status", None) in {"open", "settled"}:
            query = query.filter(status=request.query_params["status"])
        if request.query_params.get("type", None) in {
            "income",
            "expense",
            "transfer_in",
            "transfer_out",
        }:
            query = query.filter(transaction_type=request.query_params["type"])
        total = await query.count()
        movements = (
            await query.select_related("account", "category")
            .order_by("-competence_date", "-created_at")
            .offset(offset)
            .limit(limit)
        )
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
    async def api_transaction_detail(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
        try:
            transaction_id = UUID(request.path_params["transaction_id"])
        except ValueError, KeyError:
            return error_response("invalid_id", "Identificador inválido.", 400)
        movement = await FinancialTransaction.get_or_none(
            id=transaction_id, organization=await current[1].organization
        )
        if movement is None:
            return error_response("not_found", "Movimentação não encontrada.", 404)
        return json_response(movement_json(movement))

    @app.post("/api/v1/transactions")
    async def api_create_transaction(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
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
                existing = await FinancialTransaction.get_or_none(operation_key=operation_key)
                if existing:
                    incoming = await FinancialTransaction.get_or_none(
                        organization=organization,
                        transfer_id=existing.transfer_id,
                        transaction_type="transfer_in",
                    )
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
                movement = await FinancialTransaction.get(
                    organization=organization,
                    transfer_id=transfer_id,
                    transaction_type="transfer_out",
                )
                incoming = await FinancialTransaction.get_or_none(
                    organization=organization,
                    transfer_id=transfer_id,
                    transaction_type="transfer_in",
                )
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
                existing = await FinancialTransaction.get_or_none(operation_key=operation_key)
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
    async def api_settle_transaction(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
        if not require_csrf(request):
            return error_response("csrf_failed", "Token CSRF ausente ou inválido.", 403)
        try:
            transaction_id = UUID(request.path_params["transaction_id"])
        except ValueError, KeyError:
            return error_response("invalid_id", "Identificador inválido.", 400)
        organization = await current[1].organization
        movement = await FinancialTransaction.get_or_none(
            id=transaction_id, organization=organization
        )
        if movement is None:
            return error_response("not_found", "Movimentação não encontrada.", 404)
        if movement.status == "open":
            await settle_movement(organization, current[0], movement.id)
            movement = await FinancialTransaction.get(id=movement.id)
        return json_response(movement_json(movement))

    @app.post("/api/v1/transactions/:transaction_id/reverse")
    async def api_reverse_transaction(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return error_response("unauthenticated", "Sessão necessária.", 401)
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
            original = await FinancialTransaction.get_or_none(
                id=transaction_id, organization=organization
            )
            if original is None:
                return error_response("not_found", "Movimentação não encontrada.", 404)
            canonical_original = original
            if original.transaction_type == "transfer_in":
                canonical_original = await FinancialTransaction.get_or_none(
                    organization=organization,
                    transfer_id=original.transfer_id,
                    transaction_type="transfer_out",
                )
                if canonical_original is None:
                    return error_response("invalid_transfer", "Transferência incompleta.", 409)
            replay = await FinancialTransaction.get_or_none(
                operation_key=operation_key, organization=organization
            )
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
    async def edit_contact_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
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
    async def set_contact_active_page(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
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


def _journal_lines_from_form(form: dict[str, str]) -> list[tuple[UUID, Decimal, Decimal]]:
    line_inputs = []
    for index in range(1, 13):
        account_text = form.get(f"account_id_{index}", "")
        debit = parse_line_amount(form.get(f"debit_{index}", ""))
        credit = parse_line_amount(form.get(f"credit_{index}", ""))
        if account_text or debit or credit:
            if not account_text:
                raise ValueError("Selecione uma conta em cada linha preenchida.")
            line_inputs.append((UUID(account_text), debit, credit))
    return line_inputs


async def _render_members(
    request: Request,
    user: User,
    membership: Membership,
    organization: Organization,
    *,
    error: str | None = None,
    message: str | None = None,
    form: dict[str, str] | None = None,
) -> str:
    return _page(
        "finance/members.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        members=await list_members(organization),
        me=membership,
        roles=ROLE_LABELS,
        role_descriptions=ROLE_DESCRIPTIONS,
        password_rules=PASSWORD_RULES_TEXT,
        active="members",
        section="Membros",
        error=error,
        message=message,
        form=form or {},
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
