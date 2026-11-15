"""Consultas de leitura usadas pelas telas e pela API.

As rotas não acessam o ORM diretamente: pedem os dados por estas funções, que sempre
recebem a organização e filtram por ela. Funções `*_query` devolvem a consulta ainda não
executada para que a apresentação possa paginá-la.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from tortoise.expressions import Q
from tortoise.functions import Count, Sum
from tortoise.queryset import QuerySet

from fincore.domain.crm import (
    STALLED_AFTER_DAYS,
    average_cycle_days,
    percentage,
    weighted_value,
    win_rate,
)
from fincore.infrastructure.models import (
    Category,
    Counterparty,
    CrmActivity,
    CrmDeal,
    CrmDealEvent,
    CrmLead,
    CrmPipelineStage,
    CrmProposal,
    CrmProposalItem,
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


# --- CRM ------------------------------------------------------------------------------

KANBAN_LIMIT = 300


async def crm_stages(
    organization: Organization, *, active_only: bool = False
) -> list[CrmPipelineStage]:
    query = CrmPipelineStage.filter(organization=organization)
    if active_only:
        query = query.filter(is_active=True)
    return await query.order_by("position", "created_at")


async def get_stage(organization: Organization, stage_id: UUID) -> CrmPipelineStage | None:
    return await CrmPipelineStage.get_or_none(id=stage_id, organization=organization)


async def active_customers(organization: Organization) -> list[Counterparty]:
    return await Counterparty.filter(organization=organization, is_active=True).order_by("name")


async def get_lead(organization: Organization, lead_id: UUID) -> CrmLead | None:
    return await CrmLead.get_or_none(id=lead_id, organization=organization)


def leads_query(
    organization: Organization, *, status: str | None = None, search: str = ""
) -> QuerySet[CrmLead]:
    query = CrmLead.filter(organization=organization)
    if status in {"new", "contacted", "qualified", "disqualified", "converted"}:
        query = query.filter(status=status)
    elif status == "active":
        query = query.filter(status__in=["new", "contacted", "qualified"])
    if search:
        query = query.filter(
            Q(name__icontains=search)
            | Q(company_name__icontains=search)
            | Q(email__icontains=search)
        )
    return query.order_by("-created_at")


async def get_deal(organization: Organization, deal_id: UUID) -> CrmDeal | None:
    return (
        await CrmDeal.filter(id=deal_id, organization=organization)
        .select_related("counterparty", "stage")
        .first()
    )


def deals_query(organization: Organization, *, status: str | None = None) -> QuerySet[CrmDeal]:
    query = CrmDeal.filter(organization=organization)
    if status in {"open", "won", "lost"}:
        query = query.filter(status=status)
    return query.select_related("counterparty", "stage").order_by("-created_at")


async def open_deals_for_kanban(organization: Organization) -> list[CrmDeal]:
    """Negociações em aberto para o quadro; uma linha a mais indica que houve corte."""
    return (
        await CrmDeal.filter(organization=organization, status="open")
        .select_related("counterparty", "stage")
        .order_by("stage_changed_at")
        .limit(KANBAN_LIMIT + 1)
    )


async def selectable_deals(organization: Organization) -> list[CrmDeal]:
    return (
        await CrmDeal.filter(organization=organization, status="open")
        .select_related("counterparty")
        .order_by("title")
        .limit(KANBAN_LIMIT)
    )


async def deal_events(deal: CrmDeal) -> list[CrmDealEvent]:
    return (
        await CrmDealEvent.filter(deal=deal)
        .select_related("actor", "from_stage", "to_stage")
        .order_by("-occurred_at")
    )


def activities_query(
    organization: Organization,
    *,
    status: str = "pending",
    deal: CrmDeal | None = None,
    lead: CrmLead | None = None,
) -> QuerySet[CrmActivity]:
    query = CrmActivity.filter(organization=organization)
    if deal is not None:
        query = query.filter(deal=deal)
    if lead is not None:
        query = query.filter(lead=lead)
    if status == "pending":
        query = query.filter(done_at=None)
    elif status == "done":
        query = query.exclude(done_at=None)
    return query.select_related("deal", "lead", "counterparty").order_by("due_date", "-created_at")


async def get_activity(organization: Organization, activity_id: UUID) -> CrmActivity | None:
    return await CrmActivity.get_or_none(id=activity_id, organization=organization)


async def get_proposal(organization: Organization, proposal_id: UUID) -> CrmProposal | None:
    return (
        await CrmProposal.filter(id=proposal_id, organization=organization)
        .select_related("deal", "deal__counterparty")
        .first()
    )


def proposals_query(
    organization: Organization, *, status: str | None = None, deal: CrmDeal | None = None
) -> QuerySet[CrmProposal]:
    query = CrmProposal.filter(organization=organization)
    if deal is not None:
        query = query.filter(deal=deal)
    if status in {"draft", "sent", "accepted", "rejected"}:
        query = query.filter(status=status)
    return query.select_related("deal", "deal__counterparty").order_by("-number")


async def proposal_items(proposal: CrmProposal) -> list[CrmProposalItem]:
    return await CrmProposalItem.filter(proposal=proposal).order_by("position")


async def proposal_totals(proposals: Iterable[CrmProposal]) -> dict[UUID, Decimal]:
    """Total (itens − desconto) de cada proposta, numa só consulta agregada."""
    items = list(proposals)
    if not items:
        return {}
    rows = (
        await CrmProposalItem.filter(proposal_id__in=[item.id for item in items])
        .annotate(subtotal=Sum("total"))
        .group_by("proposal_id")
        .values("proposal_id", "subtotal")
    )
    subtotals = {row["proposal_id"]: row["subtotal"] or Decimal("0.00") for row in rows}
    return {
        item.id: (subtotals.get(item.id, Decimal("0.00")) - item.discount).quantize(Decimal("0.01"))
        for item in items
    }


@dataclass(frozen=True, slots=True)
class StageSummary:
    stage: CrmPipelineStage
    count: int
    total: Decimal
    weighted: Decimal


@dataclass(frozen=True, slots=True)
class CrmSummary:
    leads_total: int
    leads_by_status: dict[str, int]
    lead_conversion: Decimal | None
    open_count: int
    open_total: Decimal
    forecast: Decimal
    won_count: int
    won_total: Decimal
    lost_count: int
    win_rate: Decimal | None
    average_cycle_days: Decimal | None
    stages: list[StageSummary]
    stalled_count: int
    stalled: list[CrmDeal]
    pending_activities: int
    overdue_activities: int
    proposals_waiting: int


async def crm_summary(organization: Organization, today: date | None = None) -> CrmSummary:
    """Indicadores do CRM calculados no banco a partir dos registros da organização."""
    today = today or date.today()
    zero = Decimal("0.00")
    lead_rows = (
        await CrmLead.filter(organization=organization)
        .annotate(n=Count("id"))
        .group_by("status")
        .values("status", "n")
    )
    leads_by_status = {row["status"]: row["n"] for row in lead_rows}
    leads_total = sum(leads_by_status.values())

    stage_rows = (
        await CrmDeal.filter(organization=organization, status="open")
        .annotate(n=Count("id"), total=Sum("amount"))
        .group_by("stage_id")
        .values("stage_id", "n", "total")
    )
    by_stage = {row["stage_id"]: (row["n"], row["total"] or zero) for row in stage_rows}
    stages = []
    for stage in await crm_stages(organization):
        count, total = by_stage.get(stage.id, (0, zero))
        if not stage.is_active and not count:
            continue
        stages.append(
            StageSummary(stage, count, total, weighted_value(total, stage.win_probability))
        )

    closed_rows = (
        await CrmDeal.filter(organization=organization, status__in=["won", "lost"])
        .annotate(n=Count("id"), total=Sum("amount"))
        .group_by("status")
        .values("status", "n", "total")
    )
    closed = {row["status"]: (row["n"], row["total"] or zero) for row in closed_rows}
    won_count, won_total = closed.get("won", (0, zero))
    lost_count = closed.get("lost", (0, zero))[0]
    cycles = [
        max((closed_at - created_at).days, 0)
        for created_at, closed_at in await CrmDeal.filter(organization=organization, status="won")
        .exclude(closed_at=None)
        .order_by("-closed_at")
        .limit(500)
        .values_list("created_at", "closed_at")
    ]

    stalled_before = datetime.combine(today, time.min, tzinfo=UTC) - timedelta(
        days=STALLED_AFTER_DAYS
    )
    stalled_query = CrmDeal.filter(
        organization=organization, status="open", stage_changed_at__lt=stalled_before
    )
    pending = CrmActivity.filter(organization=organization, done_at=None)
    return CrmSummary(
        leads_total=leads_total,
        leads_by_status=leads_by_status,
        lead_conversion=percentage(leads_by_status.get("converted", 0), leads_total),
        open_count=sum(item.count for item in stages),
        open_total=sum((item.total for item in stages), zero),
        forecast=sum((item.weighted for item in stages), zero),
        won_count=won_count,
        won_total=won_total,
        lost_count=lost_count,
        win_rate=win_rate(won_count, lost_count),
        average_cycle_days=average_cycle_days(cycles),
        stages=stages,
        stalled_count=await stalled_query.count(),
        stalled=await stalled_query.select_related("counterparty", "stage")
        .order_by("stage_changed_at")
        .limit(10),
        pending_activities=await pending.count(),
        overdue_activities=await pending.filter(due_date__lt=today).count(),
        proposals_waiting=await CrmProposal.filter(
            organization=organization, status="sent", valid_until__gte=today
        ).count(),
    )
