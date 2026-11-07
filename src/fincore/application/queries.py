"""Consultas de leitura usadas pelas telas e pela API.

As rotas não acessam o ORM diretamente: pedem os dados por estas funções, que sempre
recebem a organização e filtram por ela. Funções `*_query` devolvem a consulta ainda não
executada para que a apresentação possa paginá-la.
"""

from collections.abc import Iterable
from uuid import UUID

from tortoise.queryset import QuerySet

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

# --- Identidade -----------------------------------------------------------------------


async def session_membership(
    user_id: object, organization_id: object, session_version: object
) -> tuple[User, Membership] | None:
    """Usuário e vínculo ativos de um token de sessão, ou None se o token não vale mais."""
    user = await User.get_or_none(id=user_id, is_active=True)
    if user is None or session_version != user.session_version:
        return None
    membership = await Membership.filter(
        user=user, organization_id=organization_id, is_active=True, organization__is_active=True
    ).first()
    if membership is None:
        return None
    return user, membership


async def first_active_membership(user: User) -> Membership | None:
    return await Membership.filter(user=user, is_active=True).first()


# --- Contas e categorias --------------------------------------------------------------


async def get_account(organization: Organization, account_id: UUID) -> FinancialAccount | None:
    return await FinancialAccount.get_or_none(id=account_id, organization=organization)


async def active_accounts(organization: Organization) -> list[FinancialAccount]:
    return await FinancialAccount.filter(organization=organization, is_active=True).order_by("name")


def active_categories_query(organization: Organization) -> QuerySet[Category]:
    return Category.filter(organization=organization, is_active=True).order_by(
        "transaction_type", "name"
    )


async def active_categories(organization: Organization) -> list[Category]:
    return (
        await Category.filter(organization=organization, is_active=True)
        .select_related("parent")
        .order_by("transaction_type", "name")
    )


async def synthetic_root_categories(organization: Organization) -> list[Category]:
    return await Category.filter(
        organization=organization, is_active=True, category_kind="synthetic", parent_id=None
    ).order_by("transaction_type", "name")


async def analytic_categories(
    organization: Organization, transaction_type: str | None = None
) -> list[Category]:
    query = Category.filter(organization=organization, is_active=True, category_kind="analytic")
    if transaction_type is not None:
        query = query.filter(transaction_type=transaction_type)
    return await query.select_related("parent").order_by("name")


# --- Movimentações --------------------------------------------------------------------


TRANSACTION_TYPES = frozenset({"income", "expense", "transfer_in", "transfer_out"})


def transactions_query(
    organization: Organization, *, status: str | None = None, transaction_type: str | None = None
) -> QuerySet[FinancialTransaction]:
    """Movimentos da organização, mais recentes primeiro; filtros inválidos são ignorados."""
    query = FinancialTransaction.filter(organization=organization)
    if status in {"open", "settled"}:
        query = query.filter(status=status)
    if transaction_type in TRANSACTION_TYPES:
        query = query.filter(transaction_type=transaction_type)
    return query.select_related("account", "category").order_by("-competence_date", "-created_at")


async def recent_transactions(organization: Organization, limit: int) -> list[FinancialTransaction]:
    return (
        await FinancialTransaction.filter(organization=organization)
        .select_related("account")
        .order_by("-created_at")
        .limit(limit)
    )


async def open_transaction_count(organization: Organization) -> int:
    return await FinancialTransaction.filter(organization=organization, status="open").count()


async def get_movement(
    organization: Organization, transaction_id: UUID
) -> FinancialTransaction | None:
    return await FinancialTransaction.get_or_none(id=transaction_id, organization=organization)


async def movement_by_operation_key(
    operation_key: UUID, organization: Organization | None = None
) -> FinancialTransaction | None:
    """Busca pela chave de idempotência.

    Sem organização, procura em todas: a chave é única no banco e um reenvio com a chave de
    outra organização precisa ser detectado como conflito, não como chave nova.
    """
    query = FinancialTransaction.filter(operation_key=operation_key)
    if organization is not None:
        query = query.filter(organization=organization)
    return await query.first()


async def transfer_leg(
    organization: Organization, transfer_id: UUID | None, leg: str
) -> FinancialTransaction | None:
    return await FinancialTransaction.get_or_none(
        organization=organization, transfer_id=transfer_id, transaction_type=leg
    )


# --- Títulos --------------------------------------------------------------------------


async def get_title(organization: Organization, title_id: UUID) -> FinancialTitle | None:
    return await FinancialTitle.get_or_none(id=title_id, organization=organization)


def titles_query(organization: Organization, title_type: str) -> QuerySet[FinancialTitle]:
    return FinancialTitle.filter(organization=organization, title_type=title_type)


async def unsettled_titles(organization: Organization, title_type: str) -> list[FinancialTitle]:
    return await titles_query(organization, title_type).filter(status__in=["open", "partial"])


async def title_ids_with_settlements(title_ids: Iterable[UUID]) -> set[UUID]:
    return set(
        await TitleSettlement.filter(title_id__in=list(title_ids)).values_list(
            "title_id", flat=True
        )
    )


# --- Contabilidade --------------------------------------------------------------------


def journal_entries_query(organization: Organization) -> QuerySet[JournalEntry]:
    return JournalEntry.filter(organization=organization).order_by("-entry_date", "-created_at")


async def journal_lines_by_entry(entry_ids: Iterable[UUID]) -> dict[UUID, list[JournalEntryLine]]:
    lines: dict[UUID, list[JournalEntryLine]] = {}
    for line in await JournalEntryLine.filter(journal_entry_id__in=list(entry_ids)).select_related(
        "account"
    ):
        lines.setdefault(line.journal_entry_id, []).append(line)
    return lines


async def reversed_entry_ids(organization: Organization) -> set[UUID]:
    return set(
        await JournalEntry.filter(organization=organization)
        .exclude(reversal_of_id=None)
        .values_list("reversal_of_id", flat=True)
    )


# --- Conciliação ----------------------------------------------------------------------


def statement_query(organization: Organization) -> QuerySet[ImportedStatementTransaction]:
    return (
        ImportedStatementTransaction.filter(organization=organization)
        .select_related("account")
        .order_by("-posted_at", "-imported_at")
    )


async def matches_by_statement(statement_ids: Iterable[UUID]) -> dict[UUID, ReconciliationMatch]:
    return {
        match.statement_transaction_id: match
        for match in await ReconciliationMatch.filter(
            statement_transaction_id__in=list(statement_ids)
        )
    }


async def reconciliation_candidates(
    organization: Organization, statements: Iterable[ImportedStatementTransaction]
) -> dict[tuple[UUID, object], list[FinancialTransaction]]:
    """Movimentos liquidados na mesma conta e data de cada linha do extrato, numa só query."""
    items = list(statements)
    if not items:
        return {}
    candidates: dict[tuple[UUID, object], list[FinancialTransaction]] = {}
    for movement in await FinancialTransaction.filter(
        organization=organization,
        status="settled",
        account_id__in={item.account_id for item in items},
        competence_date__in={item.posted_at for item in items},
    ).order_by("description"):
        candidates.setdefault((movement.account_id, movement.competence_date), []).append(movement)
    return candidates


def titles_api_query(
    organization: Organization, *, title_type: str | None = None, status: str | None = None
) -> QuerySet[FinancialTitle]:
    query = FinancialTitle.filter(organization=organization)
    if title_type in {"payable", "receivable"}:
        query = query.filter(title_type=title_type)
    if status in {"open", "partial", "settled", "cancelled"}:
        query = query.filter(status=status)
    return query.order_by("due_date", "installment_number", "created_at")


async def title_settlements(title: FinancialTitle) -> list[TitleSettlement]:
    return await TitleSettlement.filter(title=title).order_by("paid_on", "created_at")
