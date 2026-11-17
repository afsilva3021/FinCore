"""Compras: pedido, aprovação, recebimento (estoque + contas a pagar) e estorno.

O recebimento grava, numa única transação com o pedido bloqueado: a entrada em estoque
de cada item, os títulos a pagar e o lançamento contábil por competência
(D Estoque / C Fornecedores). Ou tudo é gravado, ou nada.
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
from fincore.domain.inventory import IN, validate_unit_cost
from fincore.domain.products import fit_quantity_to_unit
from fincore.domain.purchases import (
    APPROVED,
    CANCELLED,
    MAX_ITEMS,
    ensure_can_approve,
    ensure_can_cancel,
    ensure_can_receive,
    ensure_within_remaining,
    order_total,
    status_after_receipts,
    validate_terms,
)
from fincore.domain.titles import PAYMENT_METHODS
from fincore.infrastructure.models import (
    Counterparty,
    FinancialAccount,
    JournalEntry,
    Organization,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    StockMovement,
    User,
    Warehouse,
)

SUPPLIERS_ACCOUNT = ("SYS-SUPPLIERS", "Fornecedores", "liability")
RECEIPT_SOURCE = "purchase_receipt"


async def create_purchase_order(
    *,
    organization: Organization,
    user: User,
    operation_key: UUID,
    supplier_id: UUID,
    warehouse_id: UUID,
    account_id: UUID,
    order_date: date,
    items: list[tuple[UUID, Decimal, Decimal]],
    payment_method: str = "boleto",
    installment_count: int = 1,
    first_due_days: int = 30,
    expected_date: date | None = None,
    notes: str = "",
) -> PurchaseOrder:
    """Cria um pedido de compra em rascunho. Itens: (produto, quantidade, custo unitário)."""
    existing = await PurchaseOrder.get_or_none(operation_key=operation_key)
    if existing is not None:
        if existing.organization_id != organization.id:
            raise ValueError("Chave de operação já utilizada.")
        return existing
    supplier = await Counterparty.get_or_none(
        id=supplier_id, organization=organization, is_active=True, is_supplier=True
    )
    if supplier is None:
        raise ValueError("Selecione um fornecedor ativo desta organização.")
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
        raise ValueError("Selecione uma forma de pagamento válida.")
    validate_terms(installment_count, first_due_days)
    if expected_date is not None and expected_date < order_date:
        raise ValueError("A previsão de entrega não pode ser anterior à data do pedido.")
    if len(notes.strip()) > 2000:
        raise ValueError("Observações devem ter no máximo 2000 caracteres.")
    if not items:
        raise ValueError("Inclua pelo menos um item no pedido.")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"Um pedido aceita no máximo {MAX_ITEMS} itens.")

    products = {
        product.id: product
        for product in await Product.filter(
            organization=organization, id__in=[product_id for product_id, _, _ in items]
        ).select_related("unit")
    }
    lines = []
    for product_id, quantity, unit_cost in items:
        product = products.get(product_id)
        if product is None or not product.is_active:
            raise ValueError("Selecione produtos ativos desta organização.")
        if not product.tracks_stock:
            raise ValueError(
                f"{product.name} não controla estoque; lance essa despesa em Contas a pagar."
            )
        if quantity <= 0:
            raise ValueError("A quantidade de cada item deve ser maior que zero.")
        lines.append(
            (
                product,
                fit_quantity_to_unit(quantity, product.unit.decimal_places),
                validate_unit_cost(unit_cost),
            )
        )
    if len({product.id for product, _, _ in lines}) != len(lines):
        raise ValueError("Cada produto deve aparecer uma única vez no pedido.")
    if order_total((quantity, cost) for _, quantity, cost in lines) <= 0:
        raise ValueError("O total do pedido deve ser maior que zero.")

    try:
        async with in_transaction():
            order = await PurchaseOrder.create(
                organization=organization,
                created_by=user,
                number=await next_document_number(organization, "purchase_order"),
                supplier=supplier,
                warehouse=warehouse,
                account=account,
                order_date=order_date,
                expected_date=expected_date,
                payment_method=payment_method,
                installment_count=installment_count,
                first_due_days=first_due_days,
                notes=notes.strip() or None,
                operation_key=operation_key,
            )
            await PurchaseOrderItem.bulk_create(
                [
                    PurchaseOrderItem(
                        order=order,
                        product=product,
                        position=position,
                        quantity=quantity,
                        unit_cost=unit_cost,
                    )
                    for position, (product, quantity, unit_cost) in enumerate(lines, start=1)
                ]
            )
    except IntegrityError:
        existing = await PurchaseOrder.get_or_none(
            operation_key=operation_key, organization=organization
        )
        if existing is None:
            raise
        return existing
    return order


async def _locked_order(organization: Organization, order_id: UUID) -> PurchaseOrder:
    order = (
        await PurchaseOrder.filter(id=order_id, organization=organization)
        .select_for_update()
        .first()
    )
    if order is None:
        raise ValueError("Pedido de compra não encontrado nesta organização.")
    return order


async def approve_purchase_order(organization: Organization, order_id: UUID) -> PurchaseOrder:
    async with in_transaction():
        order = await _locked_order(organization, order_id)
        ensure_can_approve(order.status)
        order.status = APPROVED
        await order.save(update_fields=["status"])
        return order


async def cancel_purchase_order(organization: Organization, order_id: UUID) -> PurchaseOrder:
    async with in_transaction():
        order = await _locked_order(organization, order_id)
        has_received = await PurchaseOrderItem.filter(order=order, received_quantity__gt=0).exists()
        ensure_can_cancel(order.status, has_received)
        order.status = CANCELLED
        await order.save(update_fields=["status"])
        return order


async def receive_purchase_order(
    *,
    organization: Organization,
    user: User,
    order_id: UUID,
    operation_key: UUID,
    received_on: date,
    quantities: dict[UUID, Decimal],
    document_number: str = "",
) -> tuple[PurchaseReceipt, bool]:
    """Recebe o pedido, total ou parcialmente. `quantities`: item do pedido -> quantidade.

    Devolve o recebimento e se foi criado agora (False = reenvio com a mesma chave).
    """
    existing = await PurchaseReceipt.get_or_none(operation_key=operation_key)
    if existing is not None:
        if existing.organization_id != organization.id or existing.order_id != order_id:
            raise ValueError("Chave de operação já utilizada.")
        return existing, False
    if received_on > date.today():
        raise ValueError("A data do recebimento não pode estar no futuro.")
    document = " ".join(document_number.split())
    if len(document) > 60:
        raise ValueError("O número do documento deve ter no máximo 60 caracteres.")
    wanted = {item_id: quantity for item_id, quantity in quantities.items() if quantity > 0}
    if not wanted:
        raise ValueError("Informe a quantidade recebida de pelo menos um item.")
    # Contas de sistema são garantidas fora da transação (criação concorrente é tratada).
    stock_account = await ensure_document_ledger_account(organization, user, STOCK_ACCOUNT)
    suppliers_account = await ensure_document_ledger_account(organization, user, SUPPLIERS_ACCOUNT)

    try:
        async with in_transaction():
            order = await _locked_order(organization, order_id)
            ensure_can_receive(order.status)
            if received_on < order.order_date:
                raise ValueError("O recebimento não pode ser anterior à data do pedido.")
            items = await PurchaseOrderItem.filter(order=order).order_by("position")
            by_id = {item.id: item for item in items}
            if not set(wanted) <= set(by_id):
                raise ValueError("Item de recebimento não pertence a este pedido.")
            products = {
                product.id: product
                for product in await Product.filter(
                    id__in=[item.product_id for item in items]
                ).select_related("unit")
            }
            receiving = [item for item in items if item.id in wanted]
            for item in receiving:
                ensure_within_remaining(wanted[item.id], item.quantity, item.received_quantity)
            receipt = PurchaseReceipt(
                organization=organization,
                order=order,
                created_by=user,
                number=await next_document_number(organization, "purchase_receipt"),
                received_on=received_on,
                document_number=document or None,
                total=Decimal("0.00"),
                operation_key=operation_key,
            )
            movements = await post_document_movements(
                organization=organization,
                user=user,
                direction=IN,
                reason="purchase",
                lines=[
                    (products[item.product_id], order.warehouse_id, wanted[item.id], item.unit_cost)
                    for item in receiving
                ],
                occurred_on=received_on,
                source_type=RECEIPT_SOURCE,
                source_id=receipt.id,
            )
            receipt.total = sum((movement.total_cost for movement in movements), Decimal("0.00"))
            label = f"Compra PC-{order.number:05d}, recebimento {receipt.number}"
            receipt.journal_entry = await post_document_journal(
                organization=organization,
                user=user,
                entry_date=received_on,
                description=label,
                debit_account_id=stock_account.id,
                credit_account_id=suppliers_account.id,
                amount=receipt.total,
            )
            await receipt.save()
            for item, movement in zip(receiving, movements, strict=True):
                await PurchaseReceiptItem.create(
                    receipt=receipt,
                    order_item=item,
                    quantity=movement.quantity,
                    unit_cost=item.unit_cost,
                    total_cost=movement.total_cost,
                    stock_movement=movement,
                )
                item.received_quantity += movement.quantity
                await item.save(update_fields=["received_quantity"])
            if receipt.total > 0:
                await create_document_titles(
                    organization=organization,
                    user=user,
                    title_type="payable",
                    counterparty=await Counterparty.get(id=order.supplier_id),
                    account=await FinancialAccount.get(id=order.account_id),
                    amount=receipt.total,
                    issue_date=received_on,
                    first_due_date=received_on + timedelta(days=order.first_due_days),
                    payment_method=order.payment_method,
                    installment_count=order.installment_count,
                    description=label,
                    origin="purchase",
                    accrual_account=suppliers_account,
                    source_type=RECEIPT_SOURCE,
                    source_id=receipt.id,
                    document_number=document or None,
                    related_reference=f"PC-{order.number:05d}",
                )
            order.status = status_after_receipts(
                (item.quantity, item.received_quantity) for item in items
            )
            await order.save(update_fields=["status"])
    except IntegrityError:
        existing = await PurchaseReceipt.get_or_none(
            operation_key=operation_key, organization=organization, order_id=order_id
        )
        if existing is None:
            raise
        return existing, False
    return receipt, True


async def reverse_purchase_receipt(
    *, organization: Organization, user: User, receipt_id: UUID
) -> PurchaseReceipt:
    """Estorna um recebimento: tira a mercadoria do estoque, cancela os títulos e inverte o diário.

    Recusa (sem alterar nada) se algum título já foi pago ou se a mercadoria já saiu.
    """
    found = await PurchaseReceipt.get_or_none(id=receipt_id, organization=organization)
    if found is None:
        raise ValueError("Recebimento não encontrado nesta organização.")
    async with in_transaction():
        order = await _locked_order(organization, found.order_id)
        receipt = await PurchaseReceipt.filter(id=receipt_id).select_for_update().get()
        if receipt.status != "posted":
            raise ValueError("Este recebimento já foi estornado.")
        today = date.today()
        try:
            await cancel_document_titles(RECEIPT_SOURCE, receipt.id)
        except ValueError:
            raise ValueError(
                "Há título deste recebimento com baixa; o recebimento não pode ser estornado."
            ) from None
        receipt_items = await PurchaseReceiptItem.filter(receipt=receipt)
        movements = await StockMovement.filter(
            id__in=[item.stock_movement_id for item in receipt_items]
        )
        await revert_document_movements(
            organization=organization, user=user, movements=movements, occurred_on=today
        )
        if receipt.journal_entry_id is not None:
            original = await JournalEntry.get(id=receipt.journal_entry_id)
            stock_account = await ensure_document_ledger_account(organization, user, STOCK_ACCOUNT)
            suppliers = await ensure_document_ledger_account(organization, user, SUPPLIERS_ACCOUNT)
            await post_document_journal(
                organization=organization,
                user=user,
                entry_date=today,
                description=f"Estorno: {original.description}",
                debit_account_id=suppliers.id,
                credit_account_id=stock_account.id,
                amount=receipt.total,
                reversal_of=original,
            )
        items = await PurchaseOrderItem.filter(order=order)
        by_id = {item.id: item for item in items}
        for receipt_item in receipt_items:
            item = by_id[receipt_item.order_item_id]
            item.received_quantity -= receipt_item.quantity
            await item.save(update_fields=["received_quantity"])
        receipt.status = "reversed"
        receipt.reversed_at = datetime.now(UTC)
        receipt.reversed_by = user
        await receipt.save(update_fields=["status", "reversed_at", "reversed_by_id"])
        order.status = status_after_receipts(
            (item.quantity, item.received_quantity) for item in items
        )
        await order.save(update_fields=["status"])
        return receipt
