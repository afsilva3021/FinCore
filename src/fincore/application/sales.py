"""Vendas: orçamento, pedido, faturamento (estoque + contas a receber + diário) e devolução.

O faturamento grava, numa única transação com o pedido bloqueado: a saída de estoque ao
custo médio, os títulos a receber e os lançamentos por competência (D Clientes / C Receita
de vendas e D CMV / C Estoque). Sem estoque suficiente, nada é gravado.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.application.inventory import (
    STOCK_ACCOUNT,
    ensure_document_ledger_account,
    post_document_journal,
    post_document_movements,
    revert_document_movements,
)
from fincore.application.numbering import next_document_number
from fincore.application.titles import cancel_document_titles, create_document_titles
from fincore.domain.inventory import OUT
from fincore.domain.products import fit_quantity_to_unit
from fincore.domain.sales import (
    CANCELLED,
    CONFIRMED,
    INVOICED,
    MAX_ITEMS,
    ensure_can_cancel,
    ensure_can_confirm,
    ensure_can_invoice,
    line_total,
    order_total,
    validate_terms,
)
from fincore.domain.titles import PAYMENT_METHODS
from fincore.infrastructure.models import (
    Counterparty,
    FinancialAccount,
    FiscalDocument,
    JournalEntry,
    Organization,
    Product,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
    StockMovement,
    User,
    Warehouse,
)

CUSTOMERS_ACCOUNT = ("SYS-CUSTOMERS", "Clientes", "asset")
SALES_ACCOUNT = ("SYS-SALES", "Receita de vendas", "revenue")
COGS_ACCOUNT = ("SYS-COGS", "Custo das mercadorias vendidas", "expense")
INVOICE_SOURCE = "sales_invoice"


async def create_sales_order(
    *,
    organization: Organization,
    user: User,
    operation_key: UUID,
    customer_id: UUID,
    warehouse_id: UUID,
    account_id: UUID,
    order_date: date,
    items: list[tuple[UUID, Decimal, Decimal | None, Decimal]],
    payment_method: str = "pix",
    installment_count: int = 1,
    first_due_days: int = 0,
    valid_until: date | None = None,
    notes: str = "",
) -> SalesOrder:
    """Cria um orçamento. Itens: (produto, quantidade, preço unitário ou None, desconto).

    Preço `None` usa o preço de venda do cadastro do produto.
    """
    existing = await SalesOrder.get_or_none(operation_key=operation_key)
    if existing is not None:
        if existing.organization_id != organization.id:
            raise ValueError("Chave de operação já utilizada.")
        return existing
    customer = await Counterparty.get_or_none(
        id=customer_id, organization=organization, is_active=True, is_customer=True
    )
    if customer is None:
        raise ValueError("Selecione um cliente ativo desta organização.")
    warehouse = await Warehouse.get_or_none(
        id=warehouse_id, organization=organization, is_active=True
    )
    if warehouse is None:
        raise ValueError("Selecione um depósito ativo desta organização.")
    account = await FinancialAccount.get_or_none(
        id=account_id, organization=organization, is_active=True
    )
    if account is None:
        raise ValueError("Selecione uma conta financeira ativa desta organização.")
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Selecione uma forma de recebimento válida.")
    validate_terms(installment_count, first_due_days)
    if valid_until is not None and valid_until < order_date:
        raise ValueError("A validade do orçamento não pode ser anterior à data dele.")
    if len(notes.strip()) > 2000:
        raise ValueError("Observações devem ter no máximo 2000 caracteres.")
    if not items:
        raise ValueError("Inclua pelo menos um item.")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"Um pedido aceita no máximo {MAX_ITEMS} itens.")

    products = {
        product.id: product
        for product in await Product.filter(
            organization=organization, id__in=[item[0] for item in items]
        ).select_related("unit")
    }
    lines = []
    for product_id, quantity, unit_price, discount in items:
        product = products.get(product_id)
        if product is None or not product.is_active:
            raise ValueError("Selecione produtos ativos desta organização.")
        price = product.sale_price if unit_price is None else unit_price
        line_total(quantity, price, discount)
        lines.append(
            (product, fit_quantity_to_unit(quantity, product.unit.decimal_places), price, discount)
        )
    if len({line[0].id for line in lines}) != len(lines):
        raise ValueError("Cada produto deve aparecer uma única vez no pedido.")
    if order_total(line[1:] for line in lines) <= 0:
        raise ValueError("O total do pedido deve ser maior que zero.")

    try:
        async with in_transaction():
            order = await SalesOrder.create(
                organization=organization,
                created_by=user,
                number=await next_document_number(organization, "sales_order"),
                customer=customer,
                warehouse=warehouse,
                account=account,
                order_date=order_date,
                valid_until=valid_until,
                payment_method=payment_method,
                installment_count=installment_count,
                first_due_days=first_due_days,
                notes=notes.strip() or None,
                operation_key=operation_key,
            )
            await SalesOrderItem.bulk_create(
                [
                    SalesOrderItem(
                        order=order,
                        product=product,
                        position=position,
                        quantity=quantity,
                        unit_price=price,
                        discount=discount,
                    )
                    for position, (product, quantity, price, discount) in enumerate(lines, start=1)
                ]
            )
    except IntegrityError:
        existing = await SalesOrder.get_or_none(
            operation_key=operation_key, organization=organization
        )
        if existing is None:
            raise
        return existing
    return order


async def _locked_order(organization: Organization, order_id: UUID) -> SalesOrder:
    order = (
        await SalesOrder.filter(id=order_id, organization=organization).select_for_update().first()
    )
    if order is None:
        raise ValueError("Pedido de venda não encontrado nesta organização.")
    return order


async def confirm_sales_order(organization: Organization, order_id: UUID) -> SalesOrder:
    """Orçamento aceito pelo cliente vira pedido confirmado."""
    async with in_transaction():
        order = await _locked_order(organization, order_id)
        ensure_can_confirm(order.status, order.valid_until, date.today())
        order.status = CONFIRMED
        await order.save(update_fields=["status"])
        return order


async def cancel_sales_order(organization: Organization, order_id: UUID) -> SalesOrder:
    async with in_transaction():
        order = await _locked_order(organization, order_id)
        ensure_can_cancel(order.status)
        order.status = CANCELLED
        await order.save(update_fields=["status"])
        return order


async def invoice_sales_order(
    *,
    organization: Organization,
    user: User,
    order_id: UUID,
    operation_key: UUID,
    issued_on: date,
) -> tuple[SalesInvoice, bool]:
    """Fatura o pedido inteiro. Devolve o faturamento e se foi criado agora."""
    existing = await SalesInvoice.get_or_none(operation_key=operation_key)
    if existing is not None:
        if existing.organization_id != organization.id or existing.order_id != order_id:
            raise ValueError("Chave de operação já utilizada.")
        return existing, False
    if issued_on > date.today():
        raise ValueError("A data do faturamento não pode estar no futuro.")
    customers = await ensure_document_ledger_account(organization, user, CUSTOMERS_ACCOUNT)
    sales = await ensure_document_ledger_account(organization, user, SALES_ACCOUNT)
    cogs = await ensure_document_ledger_account(organization, user, COGS_ACCOUNT)
    stock = await ensure_document_ledger_account(organization, user, STOCK_ACCOUNT)

    try:
        async with in_transaction():
            order = await _locked_order(organization, order_id)
            ensure_can_invoice(order.status)
            if issued_on < order.order_date:
                raise ValueError("O faturamento não pode ser anterior à data do pedido.")
            items = await SalesOrderItem.filter(order=order).order_by("position")
            products = {
                product.id: product
                for product in await Product.filter(
                    id__in=[item.product_id for item in items]
                ).select_related("unit")
            }
            invoice = SalesInvoice(
                organization=organization,
                order=order,
                created_by=user,
                number=await next_document_number(organization, "sales_invoice"),
                issued_on=issued_on,
                total=order_total(
                    (item.quantity, item.unit_price, item.discount) for item in items
                ),
                cost_total=Decimal("0.00"),
                operation_key=operation_key,
            )
            stocked = [item for item in items if products[item.product_id].tracks_stock]
            movements = await post_document_movements(
                organization=organization,
                user=user,
                direction=OUT,
                reason="sale",
                lines=[
                    (products[item.product_id], order.warehouse_id, item.quantity, Decimal("0"))
                    for item in stocked
                ],
                occurred_on=issued_on,
                source_type=INVOICE_SOURCE,
                source_id=invoice.id,
            )
            by_item = {item.id: movement for item, movement in zip(stocked, movements, strict=True)}
            invoice.cost_total = sum(
                (movement.total_cost for movement in movements), Decimal("0.00")
            )
            label = f"Venda PV-{order.number:05d}, faturamento {invoice.number}"
            invoice.revenue_entry = await post_document_journal(
                organization=organization,
                user=user,
                entry_date=issued_on,
                description=label,
                debit_account_id=customers.id,
                credit_account_id=sales.id,
                amount=invoice.total,
            )
            invoice.cost_entry = await post_document_journal(
                organization=organization,
                user=user,
                entry_date=issued_on,
                description=f"Custo da {label[0].lower()}{label[1:]}",
                debit_account_id=cogs.id,
                credit_account_id=stock.id,
                amount=invoice.cost_total,
            )
            await invoice.save()
            await SalesInvoiceItem.bulk_create(
                [
                    SalesInvoiceItem(
                        invoice=invoice,
                        order_item=item,
                        quantity=item.quantity,
                        total=line_total(item.quantity, item.unit_price, item.discount),
                        cost=by_item[item.id].total_cost if item.id in by_item else Decimal("0.00"),
                        stock_movement=by_item.get(item.id),
                    )
                    for item in items
                ]
            )
            await create_document_titles(
                organization=organization,
                user=user,
                title_type="receivable",
                counterparty=await Counterparty.get(id=order.customer_id),
                account=await FinancialAccount.get(id=order.account_id),
                amount=invoice.total,
                issue_date=issued_on,
                first_due_date=issued_on + timedelta(days=order.first_due_days),
                payment_method=order.payment_method,
                installment_count=order.installment_count,
                description=label,
                origin="sale",
                accrual_account=customers,
                source_type=INVOICE_SOURCE,
                source_id=invoice.id,
                related_reference=f"PV-{order.number:05d}",
            )
            order.status = INVOICED
            await order.save(update_fields=["status"])
    except IntegrityError:
        existing = await SalesInvoice.get_or_none(
            operation_key=operation_key, organization=organization, order_id=order_id
        )
        if existing is None:
            raise
        return existing, False
    return invoice, True


async def reverse_sales_invoice(
    *, organization: Organization, user: User, invoice_id: UUID
) -> SalesInvoice:
    """Devolução total: a mercadoria volta ao estoque pelo custo com que saiu, os títulos
    são cancelados e receita e CMV são invertidos. O pedido volta a "confirmado".

    Recusa, sem alterar nada, se algum título já foi recebido.
    """
    found = await SalesInvoice.get_or_none(id=invoice_id, organization=organization)
    if found is None:
        raise ValueError("Faturamento não encontrado nesta organização.")
    async with in_transaction():
        order = await _locked_order(organization, found.order_id)
        invoice = await SalesInvoice.filter(id=invoice_id).select_for_update().get()
        if invoice.status != "posted":
            raise ValueError("Este faturamento já foi estornado.")
        if await FiscalDocument.filter(sales_invoice=invoice, status="issued").exists():
            raise ValueError(
                "Cancele o documento fiscal simulado deste faturamento antes de estorná-lo."
            )
        today = date.today()
        try:
            await cancel_document_titles(INVOICE_SOURCE, invoice.id)
        except ValueError:
            raise ValueError(
                "Há título deste faturamento com baixa; o faturamento não pode ser estornado."
            ) from None
        movement_ids = [
            item.stock_movement_id
            for item in await SalesInvoiceItem.filter(invoice=invoice)
            if item.stock_movement_id is not None
        ]
        await revert_document_movements(
            organization=organization,
            user=user,
            movements=await StockMovement.filter(id__in=movement_ids),
            occurred_on=today,
        )
        for entry_id, amount in (
            (invoice.revenue_entry_id, invoice.total),
            (invoice.cost_entry_id, invoice.cost_total),
        ):
            if entry_id is None:
                continue
            original = await JournalEntry.get(id=entry_id)
            lines = await original.lines.all()
            debit = next(line for line in lines if line.debit > 0)
            credit = next(line for line in lines if line.credit > 0)
            await post_document_journal(
                organization=organization,
                user=user,
                entry_date=today,
                description=f"Estorno: {original.description}",
                debit_account_id=credit.account_id,
                credit_account_id=debit.account_id,
                amount=amount,
                reversal_of=original,
            )
        invoice.status = "reversed"
        invoice.reversed_at = datetime.now(UTC)
        invoice.reversed_by = user
        await invoice.save(update_fields=["status", "reversed_at", "reversed_by_id"])
        order.status = CONFIRMED
        await order.save(update_fields=["status"])
        return invoice
