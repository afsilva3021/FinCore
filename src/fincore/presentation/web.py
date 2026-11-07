"""Páginas HTML renderizadas no servidor e fluxo seguro de identidade."""

import json
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from uuid import UUID, uuid4

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from jinja2 import Environment, FileSystemLoader, select_autoescape
from robyn import Request, Response

from fincore.application.accounting import (
    get_ledger_balances,
    parse_line_amount,
)
from fincore.application.counterparties import (
    list_counterparties,
)
from fincore.application.finance import (
    get_account_balances,
)
from fincore.application.identity import (
    PASSWORD_RULES_TEXT,
)
from fincore.application.members import (
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    can_manage_members,
    can_write,
    list_members,
    role_label,
)
from fincore.application.queries import (
    active_accounts,
    active_categories,
    analytic_categories,
    get_title,
    journal_entries_query,
    journal_lines_by_entry,
    reversed_entry_ids,
    session_membership,
    synthetic_root_categories,
    title_ids_with_settlements,
    titles_query,
    transactions_query,
    unsettled_titles,
)
from fincore.application.titles import (
    title_balances,
)
from fincore.config.settings import settings
from fincore.infrastructure.models import (
    FinancialAccount,
    FinancialTitle,
    Membership,
    Organization,
    User,
)

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
    current = await session_membership(payload.get("uid"), payload.get("org"), payload.get("sv"))
    if current is None:
        return None
    _current_role.set(current[1].role)
    return current


CurrentUser = tuple[User, Membership]


def _as_route(handler, wrapper):
    # Sem functools.wraps de propósito: o Robyn inspeciona a assinatura do handler e deve
    # enxergar só `request`; __wrapped__ faria o inspect expor também `current`.
    wrapper.__name__ = handler.__name__
    wrapper.__qualname__ = handler.__qualname__
    wrapper.__doc__ = handler.__doc__
    return wrapper


def login_required(handler):
    """Rotas HTML: sem sessão válida, redireciona para /login; senão injeta `current`."""

    async def route(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return _redirect("/login")
        return await handler(request, current)

    return _as_route(handler, route)


def api_login_required(handler):
    """Rotas da API: sem sessão válida, responde 401 JSON; senão injeta `current`."""

    async def route(request: Request) -> Response:
        current = await _current_user(request)
        if current is None:
            return Response(
                status_code=401,
                headers={"Content-Type": "application/json; charset=utf-8"},
                description=json.dumps(
                    {"error": {"code": "unauthenticated", "message": "Sessão necessária."}},
                    ensure_ascii=False,
                ),
            )
        return await handler(request, current)

    return _as_route(handler, route)


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
    return await get_title(organization, title_id)


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
        categories=await active_categories(organization),
        parent_categories=await synthetic_root_categories(organization),
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
    transactions, pagination = await _paginate(transactions_query(organization), request)
    return _page(
        "finance/transactions.html",
        name=user.full_name,
        organization=organization,
        csrf=_csrf_from_auth(request),
        operation_key=str(uuid4()),
        today=date.today().isoformat(),
        accounts=await active_accounts(organization),
        categories=await analytic_categories(organization),
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
    base_query = titles_query(organization, title_type)
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
    all_titles = await unsettled_titles(organization, title_type)
    all_balances = await title_balances(all_titles)
    overdue_titles = [item for item in all_titles if item.due_date < today]
    balances = await title_balances(titles)
    settlement_ids = await title_ids_with_settlements(item.id for item in titles)
    accounts = await active_accounts(organization)
    categories = await analytic_categories(organization, "expense" if is_payable else "income")
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


async def _render_accounting(
    request: Request,
    user: User,
    organization: Organization,
    error: str | None = None,
    open_drawer: str | None = None,
) -> str:
    entries, pagination = await _paginate(journal_entries_query(organization), request)
    # Todas as linhas dos lançamentos exibidos numa só query (antes: uma por lançamento).
    lines_by_entry = await journal_lines_by_entry(entry.id for entry in entries)
    entry_rows = []
    for entry in entries:
        lines = lines_by_entry.get(entry.id, [])
        debit = sum((line.debit for line in lines), Decimal("0.00"))
        credit = sum((line.credit for line in lines), Decimal("0.00"))
        entry_rows.append((entry, lines, debit, credit))
    reversals = await reversed_entry_ids(organization)
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
