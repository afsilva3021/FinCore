"""Compras: pedido, recebimento (estoque + contas a pagar + diário), estorno e perfis."""

import asyncio
import json
import os
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from fincore.app import app
from fincore.application.accounting import get_ledger_balances
from fincore.application.counterparties import save_counterparty
from fincore.application.finance import create_account, reverse_movement
from fincore.application.identity import register_user
from fincore.application.inventory import register_stock_movement, reverse_stock_movement
from fincore.application.members import add_member
from fincore.application.products import save_product
from fincore.application.purchases import (
    approve_purchase_order,
    cancel_purchase_order,
    create_purchase_order,
    receive_purchase_order,
    reverse_purchase_receipt,
)
from fincore.application.titles import cancel_title, settle_title
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    FinancialTitle,
    JournalEntryLine,
    Membership,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    StockBalance,
    StockMovement,
    UnitOfMeasure,
    Warehouse,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
TODAY = date.today()
FORM = "application/x-www-form-urlencoded"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


class Buyer:
    """Organização pronta para comprar: fornecedor, conta, depósito e dois produtos."""

    @classmethod
    async def create(cls):
        self = cls()
        self.user = await register_user(
            "Dona Compras", f"compras-{uuid4().hex}@example.test", PASSWORD, "Org Compras"
        )
        self.organization = await (await Membership.get(user=self.user)).organization
        self.bank = await create_account(self.organization, self.user, "Banco", "bank", D("5000"))
        self.supplier = await save_counterparty(
            organization=self.organization,
            person_type="company",
            name=f"Fornecedor {uuid4().hex[:6]}",
            is_supplier=True,
        )
        self.warehouse = await Warehouse.get(organization=self.organization, is_default=True)
        unit = await UnitOfMeasure.get(organization=self.organization, code="UN")
        self.flour, self.sugar = [
            await save_product(
                organization=self.organization,
                user=self.user,
                sku=f"CMP-{uuid4().hex[:8]}",
                name=name,
                unit_id=unit.id,
            )
            for name in ("Farinha", "Açúcar")
        ]
        return self

    async def order(self, items=None, *, approve=True, **extra) -> PurchaseOrder:
        values = {
            "organization": self.organization,
            "user": self.user,
            "operation_key": uuid4(),
            "supplier_id": self.supplier.id,
            "warehouse_id": self.warehouse.id,
            "account_id": self.bank.id,
            "order_date": TODAY,
            "items": [(self.flour.id, D("10"), D("4.00")), (self.sugar.id, D("5"), D("3.333333"))]
            if items is None
            else items,
            **extra,
        }
        order = await create_purchase_order(**values)
        if approve:
            order = await approve_purchase_order(self.organization, order.id)
        return order

    async def items(self, order) -> dict:
        rows = await PurchaseOrderItem.filter(order=order).order_by("position")
        return {row.product_id: row for row in rows}

    async def receive(self, order, quantities=None, **extra) -> PurchaseReceipt:
        items = await self.items(order)
        if quantities is None:
            wanted = {row.id: row.quantity - row.received_quantity for row in items.values()}
        else:
            wanted = {items[product.id].id: D(amount) for product, amount in quantities.items()}
        receipt, _ = await receive_purchase_order(
            **{
                "organization": self.organization,
                "user": self.user,
                "order_id": order.id,
                "operation_key": uuid4(),
                "received_on": TODAY,
                "quantities": wanted,
                **extra,
            }
        )
        return receipt

    async def stock(self, product) -> tuple[D, D]:
        row = await StockBalance.get_or_none(product=product, warehouse=self.warehouse)
        return (row.quantity, row.total_value) if row else (D("0"), D("0"))

    async def ledger(self) -> dict[str, D]:
        rows = await get_ledger_balances(self.organization)
        return {account.code: balance for account, balance in rows if balance}

    async def titles(self, receipt) -> list[FinancialTitle]:
        return await FinancialTitle.filter(
            source_type="purchase_receipt", source_id=receipt.id
        ).order_by("installment_number")

    async def status(self, order) -> str:
        return (await PurchaseOrder.get(id=order.id)).status


async def test_full_receipt_posts_stock_payables_and_journal_together() -> None:
    buyer = await Buyer.create()
    order = await buyer.order(installment_count=3, first_due_days=28)
    assert (order.number, order.status) == (1, "approved")

    receipt = await buyer.receive(order, document_number="NF 1234")
    # 10 x 4,00 + 5 x 3,333333 (= 16,67) = 56,67
    assert (receipt.number, receipt.total, receipt.status) == (1, D("56.67"), "posted")
    assert await buyer.stock(buyer.flour) == (D("10"), D("40.00"))
    assert await buyer.stock(buyer.sugar) == (D("5"), D("16.67"))
    assert await buyer.status(order) == "received"

    titles = await buyer.titles(receipt)
    assert [title.original_amount for title in titles] == [D("18.89"), D("18.89"), D("18.89")]
    assert sum(title.original_amount for title in titles) == receipt.total
    first = titles[0]
    assert (first.title_type, first.origin, first.status) == ("payable", "purchase", "open")
    assert first.due_date == TODAY + timedelta(days=28)
    assert first.counterparty_id == buyer.supplier.id and first.document_number == "NF 1234"
    assert first.related_reference == "PC-00001" and first.accrual_account_id is not None

    # Por competência: a mercadoria entra no ativo e a dívida no passivo, sem tocar no banco.
    ledger = await buyer.ledger()
    assert ledger["SYS-STOCK"] == D("56.67") and ledger["SYS-SUPPLIERS"] == D("56.67")
    assert "SYS-EXP" not in ledger
    movements = await StockMovement.filter(source_id=receipt.id).order_by("created_at")
    assert {movement.reason for movement in movements} == {"purchase"}
    assert all(movement.journal_entry_id is None for movement in movements)


async def test_partial_receipts_track_what_is_left_and_refuse_excess() -> None:
    buyer = await Buyer.create()
    order = await buyer.order()

    first = await buyer.receive(order, {buyer.flour: "4"})
    assert first.total == D("16.00") and await buyer.status(order) == "partially_received"
    items = await buyer.items(order)
    assert items[buyer.flour.id].received_quantity == D("4")
    assert items[buyer.sugar.id].received_quantity == D("0")

    with pytest.raises(ValueError, match="não pode passar do que falta"):
        await buyer.receive(order, {buyer.flour: "6.5"})
    with pytest.raises(ValueError, match="pelo menos um item"):
        await buyer.receive(order, {buyer.flour: "0"})
    with pytest.raises(ValueError, match="futuro"):
        await buyer.receive(order, {buyer.flour: "1"}, received_on=TODAY + timedelta(days=1))
    assert await buyer.stock(buyer.flour) == (D("4"), D("16.00"))
    assert await PurchaseReceipt.filter(order=order).count() == 1

    second = await buyer.receive(order)  # o restante
    assert second.number == 2 and second.total == D("40.67")
    assert await buyer.status(order) == "received"
    with pytest.raises(ValueError, match="aceitam recebimento"):
        await buyer.receive(order, {buyer.flour: "1"})
    assert await buyer.stock(buyer.flour) == (D("10"), D("40.00"))


async def test_receipt_updates_average_cost_with_existing_stock() -> None:
    buyer = await Buyer.create()
    await register_stock_movement(
        organization=buyer.organization,
        user=buyer.user,
        operation_key=uuid4(),
        direction="in",
        reason="opening",
        product_id=buyer.flour.id,
        warehouse_id=buyer.warehouse.id,
        quantity=D("10"),
        unit_cost=D("2.00"),
        occurred_on=TODAY,
    )
    order = await buyer.order([(buyer.flour.id, D("10"), D("4.00"))])
    await buyer.receive(order)
    assert await buyer.stock(buyer.flour) == (D("20"), D("60.00"))  # custo médio 3,00


async def test_simultaneous_receipts_cannot_receive_more_than_ordered() -> None:
    buyer = await Buyer.create()
    order = await buyer.order([(buyer.flour.id, D("10"), D("1.00"))])

    results = await asyncio.gather(
        *(buyer.receive(order, {buyer.flour: "4"}) for _ in range(5)), return_exceptions=True
    )
    received = [item for item in results if isinstance(item, PurchaseReceipt)]
    assert len(received) == 2 and all(
        isinstance(item, (PurchaseReceipt, ValueError)) for item in results
    )
    assert sorted(item.number for item in received) == [1, 2]
    assert await buyer.stock(buyer.flour) == (D("8"), D("8.00"))
    assert (await buyer.items(order))[buyer.flour.id].received_quantity == D("8")


async def test_resending_a_receipt_or_an_order_does_not_duplicate() -> None:
    buyer = await Buyer.create()
    key = uuid4()
    order = await buyer.order(operation_key=key)
    assert (await buyer.order(operation_key=key, approve=False)).id == order.id
    assert await PurchaseOrder.filter(organization=buyer.organization).count() == 1

    receipt_key = uuid4()
    first = await buyer.receive(order, {buyer.flour: "3"}, operation_key=receipt_key)
    again = await buyer.receive(order, {buyer.flour: "3"}, operation_key=receipt_key)
    assert again.id == first.id
    assert await buyer.stock(buyer.flour) == (D("3"), D("12.00"))
    assert len(await buyer.titles(first)) == 1

    other = await Buyer.create()
    with pytest.raises(ValueError, match="Chave de operação"):
        await other.order(operation_key=key)
    other_order = await other.order()
    with pytest.raises(ValueError, match="Chave de operação"):
        await other.receive(other_order, operation_key=receipt_key)


async def test_paying_a_purchase_title_clears_the_supplier_not_an_expense() -> None:
    buyer = await Buyer.create()
    order = await buyer.order([(buyer.flour.id, D("10"), D("10.00"))])
    receipt = await buyer.receive(order)
    (title,) = await buyer.titles(receipt)

    settlement = await settle_title(
        organization=buyer.organization,
        user=buyer.user,
        title_id=title.id,
        operation_key=uuid4(),
        amount_applied=D("60.00"),
        paid_on=TODAY,
        account_id=buyer.bank.id,
        payment_method="pix",
        discount=D("5.00"),
        interest=D("2.00"),
        fees=D("1.00"),
    )
    assert settlement.net_amount == D("58.00")
    ledger = await buyer.ledger()
    assert ledger["SYS-SUPPLIERS"] == D("40.00")  # 100 - 60 quitados
    assert ledger["SYS-FIN-EXP"] == D("3.00") and ledger["SYS-FIN-REV"] == D("5.00")
    assert ledger["SYS-STOCK"] == D("100.00") and "SYS-EXP" not in ledger
    lines = await JournalEntryLine.filter(
        journal_entry__source_transaction_id=settlement.financial_transaction_id
    )
    assert sum(line.debit for line in lines) == sum(line.credit for line in lines) == D("63.00")
    assert (await FinancialTitle.get(id=title.id)).status == "partial"

    # Título de compra não é cancelado nem tem a baixa estornada por fora do documento.
    with pytest.raises(ValueError, match="estorne o documento de origem"):
        await cancel_title(organization=buyer.organization, title_id=title.id)
    with pytest.raises(ValueError, match="baixa de um título de compra"):
        await reverse_movement(
            buyer.organization, buyer.user, settlement.financial_transaction_id, uuid4(), TODAY
        )
    with pytest.raises(ValueError, match="título deste recebimento com baixa"):
        await reverse_purchase_receipt(
            organization=buyer.organization, user=buyer.user, receipt_id=receipt.id
        )
    assert await buyer.stock(buyer.flour) == (D("10"), D("100.00"))
    assert (await PurchaseReceipt.get(id=receipt.id)).status == "posted"


async def test_reversing_a_receipt_undoes_stock_titles_and_ledger() -> None:
    buyer = await Buyer.create()
    order = await buyer.order(installment_count=2)
    first = await buyer.receive(order, {buyer.flour: "4"})
    second = await buyer.receive(order)

    reversed_receipt = await reverse_purchase_receipt(
        organization=buyer.organization, user=buyer.user, receipt_id=second.id
    )
    assert reversed_receipt.status == "reversed" and reversed_receipt.reversed_at is not None
    assert await buyer.stock(buyer.flour) == (D("4"), D("16.00"))
    assert await buyer.stock(buyer.sugar) == (D("0"), D("0.00"))
    assert {title.status for title in await buyer.titles(second)} == {"cancelled"}
    assert {title.status for title in await buyer.titles(first)} == {"open"}
    assert await buyer.status(order) == "partially_received"
    ledger = await buyer.ledger()
    assert ledger["SYS-STOCK"] == D("16.00") and ledger["SYS-SUPPLIERS"] == D("16.00")
    # O histórico fica: os movimentos originais continuam lá, com os estornos ao lado.
    assert await StockMovement.filter(source_id=second.id, reason="purchase").count() == 2
    assert await StockMovement.filter(source_id=second.id, reason="reversal").count() == 2

    with pytest.raises(ValueError, match="já foi estornado"):
        await reverse_purchase_receipt(
            organization=buyer.organization, user=buyer.user, receipt_id=second.id
        )
    # O saldo do pedido volta a poder ser recebido.
    third = await buyer.receive(order)
    assert third.total == second.total and await buyer.status(order) == "received"


async def test_receipt_reversal_is_refused_when_goods_already_left() -> None:
    buyer = await Buyer.create()
    order = await buyer.order([(buyer.flour.id, D("10"), D("4.00"))])
    receipt = await buyer.receive(order)
    await register_stock_movement(
        organization=buyer.organization,
        user=buyer.user,
        operation_key=uuid4(),
        direction="out",
        reason="adjustment",
        product_id=buyer.flour.id,
        warehouse_id=buyer.warehouse.id,
        quantity=D("3"),
        occurred_on=TODAY,
    )
    before = await buyer.ledger()

    with pytest.raises(ValueError, match="já saiu do estoque"):
        await reverse_purchase_receipt(
            organization=buyer.organization, user=buyer.user, receipt_id=receipt.id
        )
    # Nada mudou: nem título, nem estoque, nem diário, nem o recebimento.
    assert {title.status for title in await buyer.titles(receipt)} == {"open"}
    assert await buyer.stock(buyer.flour) == (D("7"), D("28.00"))
    assert await buyer.ledger() == before
    assert (await PurchaseReceipt.get(id=receipt.id)).status == "posted"

    # Movimento de compra só é estornado pelo recebimento, nunca pela tela de estoque.
    movement = await StockMovement.get(source_id=receipt.id, reason="purchase")
    with pytest.raises(ValueError, match="documento de origem"):
        await reverse_stock_movement(
            organization=buyer.organization, user=buyer.user, movement_id=movement.id
        )


async def test_order_lifecycle_and_validations() -> None:
    buyer = await Buyer.create()
    other = await Buyer.create()
    draft = await buyer.order(approve=False)
    assert draft.status == "draft"
    with pytest.raises(ValueError, match="aceitam recebimento"):
        await buyer.receive(draft)
    await approve_purchase_order(buyer.organization, draft.id)
    with pytest.raises(ValueError, match="rascunho"):
        await approve_purchase_order(buyer.organization, draft.id)
    assert (await cancel_purchase_order(buyer.organization, draft.id)).status == "cancelled"
    with pytest.raises(ValueError, match="aceitam recebimento"):
        await buyer.receive(draft)

    received = await buyer.order()
    await buyer.receive(received, {buyer.flour: "1"})
    with pytest.raises(ValueError, match="sem mercadoria recebida"):
        await cancel_purchase_order(buyer.organization, received.id)
    with pytest.raises(ValueError, match="não encontrado"):
        await approve_purchase_order(other.organization, received.id)

    item = [(buyer.flour.id, D("1"), D("1.00"))]
    for invalid, message in (
        ({"items": []}, "pelo menos um item"),
        ({"items": item + item}, "uma única vez"),
        ({"items": [(buyer.flour.id, D("1.5"), D("1.00"))]}, "inteiros"),
        ({"items": [(buyer.flour.id, D("0"), D("1.00"))]}, "maior que zero"),
        ({"items": [(buyer.flour.id, D("1"), D("0"))]}, "total do pedido"),
        ({"items": [(other.flour.id, D("1"), D("1.00"))]}, "produtos ativos"),
        ({"supplier_id": other.supplier.id}, "fornecedor"),
        ({"warehouse_id": other.warehouse.id}, "depósito"),
        ({"account_id": other.bank.id}, "conta financeira"),
        ({"installment_count": 37}, "parcelas"),
        ({"first_due_days": 400}, "prazo"),
        ({"payment_method": "cheque"}, "forma de pagamento"),
        ({"expected_date": TODAY - timedelta(days=1)}, "previsão"),
    ):
        with pytest.raises(ValueError, match=message):
            await buyer.order(**invalid)
    untracked = await save_product(
        organization=buyer.organization,
        user=buyer.user,
        sku=f"SRV-{uuid4().hex[:6]}",
        name="Serviço",
        unit_id=buyer.flour.unit_id,
        tracks_stock=False,
    )
    with pytest.raises(ValueError, match="não controla estoque"):
        await buyer.order(items=[(untracked.id, D("1"), D("1.00"))])
    assert await PurchaseOrder.filter(organization=buyer.organization).count() == 2
    numbers = await PurchaseOrder.filter(organization=buyer.organization).values_list(
        "number", flat=True
    )
    assert sorted(numbers) == [1, 2]  # tentativas recusadas não gastam número


async def test_database_guards_purchase_rules() -> None:
    buyer = await Buyer.create()
    order = await buyer.order()
    item = (await buyer.items(order))[buyer.flour.id]
    connection = Tortoise.get_connection("default")
    for statement in (
        "UPDATE purchaseorderitem SET received_quantity = quantity + 1 WHERE id = $1",
        "UPDATE purchaseorderitem SET quantity = 0 WHERE id = $1",
    ):
        with pytest.raises(IntegrityError):
            await connection.execute_query(statement, [item.id])
    with pytest.raises(IntegrityError):
        await connection.execute_query(
            "UPDATE purchaseorder SET status = 'inventado' WHERE id = $1", [order.id]
        )
    receipt = await buyer.receive(order)
    (title, *_) = await buyer.titles(receipt)
    with pytest.raises(IntegrityError):
        await connection.execute_query(
            "UPDATE financialtitle SET accrual_account_id = NULL WHERE id = $1", [title.id]
        )


# --- Páginas, API e perfis ------------------------------------------------------------


async def _headers(user) -> tuple[dict[str, str], str]:
    membership = await Membership.get(user=user)
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(membership.organization_id), "sv": user.session_version}
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    return {"cookie": cookie, "content-type": FORM}, token


async def test_purchase_pages_create_approve_receive_and_reverse() -> None:
    buyer = await Buyer.create()
    headers, token = await _headers(buyer.user)
    client = RobynTestClient(app)

    async def post(path: str, body: str = ""):
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    async def get(path: str, **params):
        return await asyncio.to_thread(client.get, path, headers=headers, query_params=params)

    empty = await get("/purchases")
    assert empty.status_code == 200 and "Nenhum pedido de compra" in empty.text

    base = (
        f"operation_key={uuid4()}&supplier_id={buyer.supplier.id}"
        f"&warehouse_id={buyer.warehouse.id}&account_id={buyer.bank.id}&order_date={TODAY}"
        "&payment_method=boleto&installment_count=2&first_due_days=30"
    )
    invalid = await post(
        "/purchases", f"{base}&product_1={buyer.flour.id}&quantity_1=1,5&unit_cost_1=4"
    )
    assert invalid.status_code == 400 and "inteiros" in invalid.text
    assert 'value="1,5"' in invalid.text and "data-drawer-initially-open" in invalid.text
    assert await PurchaseOrder.filter(organization=buyer.organization).count() == 0

    body = (
        f"{base}&product_1={buyer.flour.id}&quantity_1=10&unit_cost_1=4,00"
        f"&product_2={buyer.sugar.id}&quantity_2=5&unit_cost_2=2,50&product_3="
    )
    created = await post("/purchases", body)
    assert created.status_code == 303
    assert (await post("/purchases", body)).status_code == 303  # reenvio não duplica
    order = await PurchaseOrder.get(organization=buyer.organization)
    assert order.status == "draft"

    page = await get(f"/purchases/{order.id}")
    assert "PC-00001" in page.text and "R$ 52,50" in page.text and "Aprovar pedido" in page.text
    assert "Registrar recebimento" not in page.text
    assert (await post(f"/purchases/{order.id}/approve")).status_code == 303
    page = await get(f"/purchases/{order.id}")
    assert "Registrar recebimento" in page.text and "Aprovar pedido" not in page.text

    items = await buyer.items(order)
    flour, sugar = items[buyer.flour.id], items[buyer.sugar.id]
    receipt_key = uuid4()
    excess = await post(
        f"/purchases/{order.id}/receipts",
        f"operation_key={receipt_key}&received_on={TODAY}&qty_{flour.id}=11&qty_{sugar.id}=",
    )
    assert excess.status_code == 400 and "não pode passar do que falta" in excess.text
    assert f'value="{receipt_key}"' in excess.text and 'value="11"' in excess.text
    partial = (
        f"operation_key={receipt_key}&received_on={TODAY}&document_number=NF+77"
        f"&qty_{flour.id}=4&qty_{sugar.id}="
    )
    assert (await post(f"/purchases/{order.id}/receipts", partial)).status_code == 303
    assert (await post(f"/purchases/{order.id}/receipts", partial)).status_code == 303
    assert await buyer.stock(buyer.flour) == (D("4"), D("16.00"))

    page = await get(f"/purchases/{order.id}")
    assert "Recebido em parte" in page.text and "NF 77" in page.text and "R$ 8,00" in page.text
    listing = await get("/purchases", q="pc-1")
    assert "PC-00001" in listing.text and "26%" in listing.text  # 4 de 15 unidades
    assert "PC-00001" not in (await get("/purchases", status="received")).text

    receipt = await PurchaseReceipt.get(order=order)
    assert (await post(f"/purchases/receipts/{receipt.id}/reverse")).status_code == 303
    assert await buyer.stock(buyer.flour) == (D("0"), D("0.00"))
    again = await post(f"/purchases/receipts/{receipt.id}/reverse")
    assert again.status_code == 400 and "já foi estornado" in again.text
    assert (await post(f"/purchases/{order.id}/cancel")).status_code == 303
    assert await buyer.status(order) == "cancelled"

    assert (await get(f"/purchases/{uuid4()}")).status_code == 404
    assert (await post(f"/purchases/receipts/{uuid4()}/reverse")).status_code == 404
    no_csrf = await asyncio.to_thread(
        client.post, f"/purchases/{order.id}/approve", headers=headers, body="csrf=errado"
    )
    assert no_csrf.status_code == 303 and await buyer.status(order) == "cancelled"

    api = await get("/api/v1/purchase-orders")
    data = json.loads(api.text)
    assert data["total"] == 1 and data["items"][0]["number"] == "PC-00001"
    assert data["items"][0]["total"] == "52.50" and len(data["items"][0]["items"]) == 2
    detail = await get(f"/api/v1/purchase-orders/{order.id}")
    assert json.loads(detail.text)["status"] == "cancelled"
    assert (await asyncio.to_thread(client.get, "/api/v1/purchase-orders")).status_code == 401


async def test_purchasing_profile_buys_and_registers_suppliers_only() -> None:
    buyer = await Buyer.create()
    order = await buyer.order(approve=False)
    client = RobynTestClient(app)

    for role, can_buy, can_contacts, can_finance in (
        ("purchasing", True, True, False),
        ("finance", False, True, True),
        ("stock", False, False, False),
        ("viewer", False, False, False),
    ):
        membership = await add_member(
            buyer.organization, role, f"{role}-{uuid4().hex}@example.test", PASSWORD, role
        )
        headers, token = await _headers(await membership.user)

        async def post(path: str, body: str = "", headers=headers, token=token):
            response = await asyncio.to_thread(
                client.post, path, headers=headers, body=f"csrf={token}&{body}"
            )
            return response.status_code != 403

        fresh = await buyer.order(approve=False)
        assert await post(f"/purchases/{fresh.id}/approve") == can_buy, role
        assert (await buyer.status(fresh) == "approved") == can_buy, role
        contact = f"person_type=company&name=Fornecedor+{uuid4().hex[:6]}&is_supplier=true"
        assert await post("/contacts", contact) == can_contacts, role
        account = f"name=Conta+{uuid4().hex[:6]}&account_type=cash&opening_balance=0"
        assert await post("/accounts", account) == can_finance, role
        page = await asyncio.to_thread(client.get, f"/purchases/{order.id}", headers=headers)
        assert page.status_code == 200 and ("is-readonly" in page.text) != can_buy, role
