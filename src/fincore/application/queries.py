"""Consultas de leitura usadas pelas telas e pela API.

As rotas não acessam o ORM diretamente: pedem os dados por estas funções, que sempre
recebem a organização e filtram por ela. Funções `*_query` devolvem a consulta ainda não
executada para que a apresentação possa paginá-la.
"""

from collections.abc import Iterable
from decimal import Decimal
from uuid import UUID

from tortoise.expressions import Q
from tortoise.functions import Count, Sum
from tortoise.queryset import QuerySet

from fincore.infrastructure.models import (
    Category,
    Counterparty,
    FinancialAccount,
    FinancialTitle,
    FinancialTransaction,
    ImportedStatementTransaction,
    JournalEntry,
    JournalEntryLine,
    Membership,
    Organization,
    Product,
    ProductGroup,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    ReconciliationMatch,
    StockBalance,
    StockMovement,
    TitleSettlement,
    UnitOfMeasure,
    User,
    Warehouse,
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


# --- Cadastros de produto -------------------------------------------------------------


def products_query(
    organization: Organization,
    *,
    search: str = "",
    status: str = "all",
    group_id: UUID | None = None,
) -> QuerySet[Product]:
    """Produtos da organização por código, descrição ou código de barras."""
    query = Product.filter(organization=organization)
    term = search.strip()
    if term:
        query = query.filter(
            Q(sku__icontains=term) | Q(name__icontains=term) | Q(barcode__icontains=term)
        )
    if status == "active":
        query = query.filter(is_active=True)
    elif status == "inactive":
        query = query.filter(is_active=False)
    if group_id is not None:
        query = query.filter(group_id=group_id)
    return query.select_related("unit", "group").order_by("name", "sku")


async def get_product(organization: Organization, product_id: UUID) -> Product | None:
    return (
        await Product.filter(id=product_id, organization=organization)
        .select_related("unit", "group")
        .first()
    )


async def product_groups(
    organization: Organization, *, only_active: bool = False
) -> list[ProductGroup]:
    query = ProductGroup.filter(organization=organization)
    if only_active:
        query = query.filter(is_active=True)
    return await query.select_related("parent").order_by("name")


async def units_of_measure(
    organization: Organization, *, only_active: bool = False
) -> list[UnitOfMeasure]:
    query = UnitOfMeasure.filter(organization=organization)
    if only_active:
        query = query.filter(is_active=True)
    return await query.order_by("code")


async def warehouses(organization: Organization, *, only_active: bool = False) -> list[Warehouse]:
    query = Warehouse.filter(organization=organization)
    if only_active:
        query = query.filter(is_active=True)
    return await query.order_by("-is_default", "name")


# --- Estoque --------------------------------------------------------------------------


def stock_balances_query(
    organization: Organization,
    *,
    search: str = "",
    warehouse_id: UUID | None = None,
    product_id: UUID | None = None,
    only_in_stock: bool = False,
) -> QuerySet[StockBalance]:
    """Posições de estoque (produto x depósito) que já tiveram movimento."""
    query = StockBalance.filter(organization=organization, movement_count__gt=0)
    term = search.strip()
    if term:
        query = query.filter(
            Q(product__sku__icontains=term)
            | Q(product__name__icontains=term)
            | Q(product__barcode__icontains=term)
        )
    if warehouse_id is not None:
        query = query.filter(warehouse_id=warehouse_id)
    if product_id is not None:
        query = query.filter(product_id=product_id)
    if only_in_stock:
        query = query.filter(quantity__gt=0)
    return query.select_related("product__unit", "warehouse").order_by(
        "product__name", "product__sku", "warehouse__name"
    )


async def stock_totals(organization: Organization) -> tuple[Decimal, int]:
    """Valor total do estoque ao custo médio e quantas posições têm saldo."""
    rows = (
        await StockBalance.filter(organization=organization, quantity__gt=0)
        .annotate(value_sum=Sum("total_value"), positions=Count("id"))
        .values("value_sum", "positions")
    )
    if not rows:
        return Decimal("0.00"), 0
    return rows[0]["value_sum"] or Decimal("0.00"), rows[0]["positions"] or 0


async def stock_on_hand(
    organization: Organization, product_ids: Iterable[UUID] | None = None
) -> dict[UUID, Decimal]:
    """Quantidade em estoque por produto, somando todos os depósitos (uma consulta)."""
    query = StockBalance.filter(organization=organization)
    if product_ids is not None:
        ids = list(product_ids)
        if not ids:
            return {}
        query = query.filter(product_id__in=ids)
    rows = (
        await query.group_by("product_id")
        .annotate(on_hand=Sum("quantity"))
        .values("product_id", "on_hand")
    )
    return {row["product_id"]: row["on_hand"] or Decimal("0") for row in rows}


async def low_stock_products(organization: Organization) -> list[tuple[Product, Decimal]]:
    """Produtos ativos com estoque mínimo definido e saldo total abaixo dele."""
    products = (
        await Product.filter(
            organization=organization, is_active=True, tracks_stock=True, min_stock__gt=0
        )
        .select_related("unit")
        .order_by("name")
    )
    on_hand = await stock_on_hand(organization, [product.id for product in products])
    return [
        (product, on_hand.get(product.id, Decimal("0")))
        for product in products
        if on_hand.get(product.id, Decimal("0")) < product.min_stock
    ]


async def stock_products(organization: Organization) -> list[Product]:
    """Produtos que podem receber movimentação manual."""
    return (
        await Product.filter(organization=organization, is_active=True, tracks_stock=True)
        .select_related("unit")
        .order_by("name", "sku")
    )


def stock_movements_query(
    organization: Organization,
    *,
    product_id: UUID | None = None,
    warehouse_id: UUID | None = None,
) -> QuerySet[StockMovement]:
    query = StockMovement.filter(organization=organization)
    if product_id is not None:
        query = query.filter(product_id=product_id)
    if warehouse_id is not None:
        query = query.filter(warehouse_id=warehouse_id)
    return query.select_related("product__unit", "warehouse", "created_by").order_by(
        "-created_at", "-sequence"
    )


async def get_stock_movement(organization: Organization, movement_id: UUID) -> StockMovement | None:
    return (
        await StockMovement.filter(id=movement_id, organization=organization)
        .select_related("product__unit", "warehouse")
        .first()
    )


async def stock_movement_flags(
    organization: Organization, movements: Iterable[StockMovement]
) -> tuple[set[UUID], set[UUID]]:
    """IDs já estornados e IDs que ainda podem ser estornados, entre os movimentos dados.

    Pode ser estornado o último movimento da posição que não é estorno nem foi estornado;
    numa transferência, as duas pernas precisam ser as últimas das suas posições.
    """
    items = list(movements)
    if not items:
        return set(), set()
    reversed_ids = set(
        await StockMovement.filter(reversal_of_id__in=[item.id for item in items]).values_list(
            "reversal_of_id", flat=True
        )
    )
    balances = await StockBalance.filter(
        organization=organization,
        product_id__in={item.product_id for item in items},
        warehouse_id__in={item.warehouse_id for item in items},
    )
    last = {(row.product_id, row.warehouse_id): row.movement_count for row in balances}

    def is_last(item: StockMovement) -> bool:
        return last.get((item.product_id, item.warehouse_id)) == item.sequence

    transfer_ids = {item.transfer_id for item in items if item.transfer_id and is_last(item)}
    legs = (
        await StockMovement.filter(organization=organization, transfer_id__in=transfer_ids)
        if transfer_ids
        else []
    )
    if legs:
        leg_balances = await StockBalance.filter(
            organization=organization,
            product_id__in={leg.product_id for leg in legs},
            warehouse_id__in={leg.warehouse_id for leg in legs},
        )
        last.update(
            {(row.product_id, row.warehouse_id): row.movement_count for row in leg_balances}
        )
    blocked_transfers = {leg.transfer_id for leg in legs if not is_last(leg)}
    reversible = {
        item.id
        for item in items
        if item.reason != "reversal"
        and item.source_type is None
        and item.id not in reversed_ids
        and is_last(item)
        and item.transfer_id not in blocked_transfers
    }
    return reversed_ids, reversible


# --- Compras --------------------------------------------------------------------------


async def active_suppliers(organization: Organization) -> list[Counterparty]:
    return await Counterparty.filter(
        organization=organization, is_active=True, is_supplier=True
    ).order_by("name")


def purchase_orders_query(
    organization: Organization, *, status: str = "", search: str = ""
) -> QuerySet[PurchaseOrder]:
    query = PurchaseOrder.filter(organization=organization)
    if status:
        query = query.filter(status=status)
    term = search.strip()
    if term:
        condition = Q(supplier__name__icontains=term) | Q(supplier__trade_name__icontains=term)
        digits = term.upper().removeprefix("PC-").lstrip("0")
        if digits.isdigit():
            condition |= Q(number=int(digits))
        query = query.filter(condition)
    return query.select_related("supplier", "warehouse").order_by("-number")


async def get_purchase_order(organization: Organization, order_id: UUID) -> PurchaseOrder | None:
    return (
        await PurchaseOrder.filter(id=order_id, organization=organization)
        .select_related("supplier", "warehouse", "account", "created_by")
        .first()
    )


async def purchase_order_items(orders: Iterable[PurchaseOrder]) -> dict[UUID, list]:
    """Itens (com produto e unidade) de vários pedidos numa consulta só, por pedido."""
    ids = [order.id for order in orders]
    grouped: dict[UUID, list] = {order_id: [] for order_id in ids}
    if ids:
        rows = (
            await PurchaseOrderItem.filter(order_id__in=ids)
            .select_related("product__unit")
            .order_by("position")
        )
        for row in rows:
            grouped[row.order_id].append(row)
    return grouped


async def purchase_receipts(order: PurchaseOrder) -> list[PurchaseReceipt]:
    return await PurchaseReceipt.filter(order=order).select_related("created_by").order_by("number")


async def get_purchase_receipt(
    organization: Organization, receipt_id: UUID
) -> PurchaseReceipt | None:
    return await PurchaseReceipt.get_or_none(id=receipt_id, organization=organization)


async def document_titles(
    organization: Organization, source_type: str, source_ids: Iterable[UUID]
) -> dict[UUID, list[FinancialTitle]]:
    """Títulos gerados por documentos (recebimentos de compra, faturamentos), por documento."""
    ids = list(source_ids)
    grouped: dict[UUID, list[FinancialTitle]] = {source_id: [] for source_id in ids}
    if ids:
        rows = await FinancialTitle.filter(
            organization=organization, source_type=source_type, source_id__in=ids
        ).order_by("installment_number")
        for row in rows:
            grouped[row.source_id].append(row)
    return grouped
