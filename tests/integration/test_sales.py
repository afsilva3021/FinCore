"""Vendas: orçamento, pedido, faturamento (estoque + receber + diário), devolução e perfis."""

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
from fincore.application.finance import create_account
from fincore.application.identity import register_user
from fincore.application.inventory import register_stock_movement
from fincore.application.members import add_member
from fincore.application.products import save_product
from fincore.application.sales import (
    cancel_sales_order,
    confirm_sales_order,
    create_sales_order,
    invoice_sales_order,
    reverse_sales_invoice,
)
from fincore.application.titles import cancel_title, settle_title
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    FinancialTitle,
    Membership,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
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


class Seller:
    """Organização pronta para vender: cliente, conta, e produtos com e sem estoque."""

    @classmethod
    async def create(cls):
        self = cls()
        self.user = await register_user(
            "Dona Vendas", f"vendas-{uuid4().hex}@example.test", PASSWORD, "Org Vendas"
        )
        self.organization = await (await Membership.get(user=self.user)).organization
        self.bank = await create_account(self.organization, self.user, "Banco", "bank", D("0"))
        self.customer = await save_counterparty(
            organization=self.organization,
            person_type="individual",
            name=f"Cliente {uuid4().hex[:6]}",
            is_customer=True,
        )
        self.warehouse = await Warehouse.get(organization=self.organization, is_default=True)
        self.unit = await UnitOfMeasure.get(organization=self.organization, code="UN")
        # Bolo: 10 em estoque a R$ 14,00, vendido a R$ 38,00. Café: sem estoque, R$ 5,00.
        self.cake = await self.product("Bolo", "38.00")
        self.coffee = await self.product("Café", "5.00", tracks_stock=False)
        await self.stock_in(self.cake, "10", "14.00")
        return self

    async def product(self, name, price, **extra):
        return await save_product(
            organization=self.organization,
            user=self.user,
            sku=f"VND-{uuid4().hex[:8]}",
            name=name,
            unit_id=self.unit.id,
            sale_price=D(price),
            **extra,
        )

    async def stock_in(self, product, quantity, cost):
        await register_stock_movement(
            organization=self.organization,
            user=self.user,
            operation_key=uuid4(),
            direction="in",
            reason="opening",
            product_id=product.id,
            warehouse_id=self.warehouse.id,
            quantity=D(quantity),
            unit_cost=D(cost),
            occurred_on=TODAY,
        )

    async def order(self, items=None, *, confirm=True, **extra) -> SalesOrder:
        values = {
            "organization": self.organization,
            "user": self.user,
            "operation_key": uuid4(),
            "customer_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "account_id": self.bank.id,
            "order_date": TODAY,
            "items": [(self.cake.id, D("2"), None, D("0")), (self.coffee.id, D("3"), None, D("1"))]
            if items is None
            else items,
            **extra,
        }
        order = await create_sales_order(**values)
        if confirm:
            order = await confirm_sales_order(self.organization, order.id)
        return order

    async def invoice(self, order, **extra) -> SalesInvoice:
        invoice, _ = await invoice_sales_order(
            **{
                "organization": self.organization,
                "user": self.user,
                "order_id": order.id,
                "operation_key": uuid4(),
                "issued_on": TODAY,
                **extra,
            }
        )
        return invoice

    async def stock(self, product=None) -> tuple[D, D]:
        row = await StockBalance.get_or_none(product=product or self.cake, warehouse=self.warehouse)
        return (row.quantity, row.total_value) if row else (D("0"), D("0"))

    async def ledger(self) -> dict[str, D]:
        rows = await get_ledger_balances(self.organization)
        return {account.code: balance for account, balance in rows if balance}

    async def titles(self, invoice) -> list[FinancialTitle]:
        return await FinancialTitle.filter(
            source_type="sales_invoice", source_id=invoice.id
        ).order_by("installment_number")

    async def status(self, order) -> str:
        return (await SalesOrder.get(id=order.id)).status


async def test_invoice_posts_stock_receivables_revenue_and_cost_together() -> None:
    seller = await Seller.create()
    order = await seller.order(installment_count=2, first_due_days=30)
    assert (order.number, order.status) == (1, "confirmed")

    invoice = await seller.invoice(order)
    # 2 bolos x 38,00 + 3 cafés x 5,00 - 1,00 de desconto = 90,00; custo 2 x 14,00 = 28,00
    assert (invoice.number, invoice.total, invoice.cost_total) == (1, D("90.00"), D("28.00"))
    assert await seller.stock() == (D("8"), D("112.00"))
    assert await seller.status(order) == "invoiced"

    titles = await seller.titles(invoice)
    assert [title.original_amount for title in titles] == [D("45.00"), D("45.00")]
    first = titles[0]
    assert (first.title_type, first.origin, first.status) == ("receivable", "sale", "open")
    assert first.due_date == TODAY + timedelta(days=30)
    assert first.counterparty_id == seller.customer.id and first.related_reference == "PV-00001"

    ledger = await seller.ledger()
    assert ledger["SYS-CUSTOMERS"] == D("90.00") and ledger["SYS-SALES"] == D("90.00")
    assert ledger["SYS-COGS"] == D("28.00") and ledger["SYS-STOCK"] == D("112.00")
    items = await SalesInvoiceItem.filter(invoice=invoice).order_by("order_item__position")
    assert [(item.total, item.cost) for item in items] == [
        (D("76.00"), D("28.00")),
        (D("14.00"), D("0.00")),
    ]
    assert items[0].stock_movement_id is not None and items[1].stock_movement_id is None
    movement = await StockMovement.get(id=items[0].stock_movement_id)
    assert (movement.direction, movement.reason, movement.source_id) == ("out", "sale", invoice.id)


async def test_invoice_without_enough_stock_writes_nothing() -> None:
    seller = await Seller.create()
    order = await seller.order([(seller.cake.id, D("11"), None, D("0"))])
    before = await seller.ledger()

    with pytest.raises(ValueError, match="Estoque insuficiente: há 10 UN de Bolo"):
        await seller.invoice(order)
    assert await seller.stock() == (D("10"), D("140.00"))
    assert await seller.status(order) == "confirmed"
    assert await SalesInvoice.filter(organization=seller.organization).count() == 0
    assert await FinancialTitle.filter(organization=seller.organization).count() == 0
    assert await seller.ledger() == before

    # Chegando mercadoria, o mesmo pedido fatura; a tentativa recusada não gastou número.
    await seller.stock_in(seller.cake, "1", "14.00")
    assert (await seller.invoice(order)).number == 1


async def test_two_orders_competing_for_the_last_units_only_one_invoices() -> None:
    seller = await Seller.create()
    orders = [await seller.order([(seller.cake.id, D("6"), None, D("0"))]) for _ in range(3)]

    results = await asyncio.gather(
        *(seller.invoice(order) for order in orders), return_exceptions=True
    )
    invoiced = [item for item in results if isinstance(item, SalesInvoice)]
    assert len(invoiced) == 1 and sum(isinstance(item, ValueError) for item in results) == 2
    assert await seller.stock() == (D("4"), D("56.00"))
    assert sorted([await seller.status(order) for order in orders]) == [
        "confirmed",
        "confirmed",
        "invoiced",
    ]


async def test_invoicing_twice_or_resending_does_not_duplicate() -> None:
    seller = await Seller.create()
    order = await seller.order()
    key = uuid4()
    first = await seller.invoice(order, operation_key=key)
    assert (await seller.invoice(order, operation_key=key)).id == first.id
    with pytest.raises(ValueError, match="confirmado e ainda não faturado"):
        await seller.invoice(order)

    burst_order = await seller.order()
    burst = await asyncio.gather(
        *(seller.invoice(burst_order) for _ in range(4)), return_exceptions=True
    )
    assert sum(isinstance(item, SalesInvoice) for item in burst) == 1
    assert await SalesInvoice.filter(order=burst_order).count() == 1
    assert await seller.stock() == (D("6"), D("84.00"))

    other = await Seller.create()
    with pytest.raises(ValueError, match="Chave de operação"):
        await other.invoice(await other.order(), operation_key=key)


async def test_receiving_a_sale_title_clears_the_customer_account() -> None:
    seller = await Seller.create()
    invoice = await seller.invoice(await seller.order())
    (title,) = await seller.titles(invoice)

    settlement = await settle_title(
        organization=seller.organization,
        user=seller.user,
        title_id=title.id,
        operation_key=uuid4(),
        amount_applied=D("90.00"),
        paid_on=TODAY,
        account_id=seller.bank.id,
        payment_method="pix",
        discount=D("4.00"),
        interest=D("1.50"),
        fees=D("2.00"),
    )
    assert settlement.net_amount == D("85.50")
    ledger = await seller.ledger()
    assert "SYS-CUSTOMERS" not in ledger  # quitado
    assert ledger["SYS-SALES"] == D("90.00")  # a receita não é lançada de novo na baixa
    assert ledger["SYS-FIN-EXP"] == D("6.00") and ledger["SYS-FIN-REV"] == D("1.50")
    assert "SYS-REV" not in ledger
    assert (await FinancialTitle.get(id=title.id)).status == "settled"

    with pytest.raises(ValueError, match="título deste faturamento com baixa"):
        await reverse_sales_invoice(
            organization=seller.organization, user=seller.user, invoice_id=invoice.id
        )
    assert await seller.stock() == (D("8"), D("112.00"))


async def test_return_restores_stock_at_original_cost_and_reverses_ledger() -> None:
    seller = await Seller.create()
    order = await seller.order()
    invoice = await seller.invoice(order)
    # Depois da venda entra mercadoria mais cara: o custo médio muda (8 x 14 + 8 x 20).
    await seller.stock_in(seller.cake, "8", "20.00")
    assert await seller.stock() == (D("16"), D("272.00"))
    (title,) = await seller.titles(invoice)
    with pytest.raises(ValueError, match="estorne o documento de origem"):
        await cancel_title(organization=seller.organization, title_id=title.id)

    returned = await reverse_sales_invoice(
        organization=seller.organization, user=seller.user, invoice_id=invoice.id
    )
    assert returned.status == "reversed" and returned.reversed_at is not None
    # Os 2 bolos voltam pelos R$ 28,00 com que saíram, não pelo custo médio de agora.
    assert await seller.stock() == (D("18"), D("300.00"))
    assert (await FinancialTitle.get(id=title.id)).status == "cancelled"
    assert await seller.status(order) == "confirmed"
    ledger = await seller.ledger()
    for code in ("SYS-CUSTOMERS", "SYS-SALES", "SYS-COGS"):
        assert code not in ledger
    assert ledger["SYS-STOCK"] == D("300.00")

    with pytest.raises(ValueError, match="já foi estornado"):
        await reverse_sales_invoice(
            organization=seller.organization, user=seller.user, invoice_id=invoice.id
        )
    # O pedido pode ser faturado de novo (agora ao custo médio atual) ou cancelado.
    again = await seller.invoice(order)
    assert again.number == 2 and again.cost_total == D("33.33")  # 2 x 16,666667
    assert await SalesInvoice.filter(order=order, status="posted").count() == 1


async def test_quote_lifecycle_and_validations() -> None:
    seller = await Seller.create()
    other = await Seller.create()
    quote = await seller.order(confirm=False, valid_until=TODAY + timedelta(days=7))
    assert quote.status == "quote"
    with pytest.raises(ValueError, match="confirmado"):
        await seller.invoice(quote)
    await confirm_sales_order(seller.organization, quote.id)
    with pytest.raises(ValueError, match="orçamento"):
        await confirm_sales_order(seller.organization, quote.id)
    assert (await cancel_sales_order(seller.organization, quote.id)).status == "cancelled"

    expired = await seller.order(
        confirm=False, order_date=TODAY - timedelta(days=10), valid_until=TODAY - timedelta(days=1)
    )
    with pytest.raises(ValueError, match="venceu"):
        await confirm_sales_order(seller.organization, expired.id)

    invoiced = await seller.order()
    await seller.invoice(invoiced)
    with pytest.raises(ValueError, match="faturado não pode ser cancelado"):
        await cancel_sales_order(seller.organization, invoiced.id)
    with pytest.raises(ValueError, match="não encontrado"):
        await confirm_sales_order(other.organization, quote.id)

    # Preço vazio usa o do cadastro; preço informado vale para o pedido.
    custom = await seller.order([(seller.cake.id, D("1"), D("30.00"), D("0"))], confirm=False)
    item = await custom.items.all().first()
    assert item.unit_price == D("30.00")

    line = [(seller.cake.id, D("1"), None, D("0"))]
    for invalid, message in (
        ({"items": []}, "pelo menos um item"),
        ({"items": line + line}, "uma única vez"),
        ({"items": [(seller.cake.id, D("1.5"), None, D("0"))]}, "inteiros"),
        ({"items": [(seller.cake.id, D("1"), None, D("38.01"))]}, "desconto"),
        ({"items": [(seller.cake.id, D("1"), D("0"), D("0"))]}, "total do pedido"),
        ({"items": [(other.cake.id, D("1"), None, D("0"))]}, "produtos ativos"),
        ({"customer_id": other.customer.id}, "cliente"),
        ({"warehouse_id": other.warehouse.id}, "depósito"),
        ({"account_id": other.bank.id}, "conta financeira"),
        ({"installment_count": 0}, "parcelas"),
        ({"payment_method": "cheque"}, "forma de recebimento"),
        ({"valid_until": TODAY - timedelta(days=1)}, "validade"),
    ):
        with pytest.raises(ValueError, match=message):
            await seller.order(**invalid)
    with pytest.raises(ValueError, match="futuro"):
        await seller.invoice(await seller.order(), issued_on=TODAY + timedelta(days=1))


async def test_database_guards_sales_rules() -> None:
    seller = await Seller.create()
    order = await seller.order()
    invoice = await seller.invoice(order)
    connection = Tortoise.get_connection("default")
    with pytest.raises(IntegrityError):
        await connection.execute_query(
            "UPDATE salesorder SET status = 'inventado' WHERE id = $1", [order.id]
        )
    with pytest.raises(IntegrityError):
        await connection.execute_query(
            "UPDATE salesinvoice SET total = 0 WHERE id = $1", [invoice.id]
        )
    # Dois faturamentos efetivados para o mesmo pedido não cabem no banco.
    with pytest.raises(IntegrityError):
        await SalesInvoice.create(
            organization=seller.organization,
            order=order,
            created_by=seller.user,
            number=999,
            issued_on=TODAY,
            total=D("1.00"),
            cost_total=D("0.00"),
            operation_key=uuid4(),
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


async def test_sales_pages_quote_confirm_invoice_and_return() -> None:
    seller = await Seller.create()
    headers, token = await _headers(seller.user)
    client = RobynTestClient(app)

    async def post(path: str, body: str = ""):
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    async def get(path: str, **params):
        return await asyncio.to_thread(client.get, path, headers=headers, query_params=params)

    empty = await get("/sales")
    assert empty.status_code == 200 and "Nenhum orçamento ou pedido" in empty.text

    base = (
        f"operation_key={uuid4()}&customer_id={seller.customer.id}"
        f"&warehouse_id={seller.warehouse.id}&account_id={seller.bank.id}&order_date={TODAY}"
        "&payment_method=pix&installment_count=1&first_due_days=0"
    )
    invalid = await post(
        "/sales", f"{base}&product_1={seller.cake.id}&quantity_1=1&discount_1=99,00"
    )
    assert invalid.status_code == 400 and "desconto" in invalid.text.lower()
    assert 'value="99,00"' in invalid.text and "data-drawer-initially-open" in invalid.text

    body = (
        f"{base}&product_1={seller.cake.id}&quantity_1=2&unit_price_1=&discount_1="
        f"&product_2={seller.coffee.id}&quantity_2=3&unit_price_2=4,50&discount_2=1,50"
    )
    assert (await post("/sales", body)).status_code == 303
    assert (await post("/sales", body)).status_code == 303
    order = await SalesOrder.get(organization=seller.organization)
    assert order.status == "quote"

    page = await get(f"/sales/{order.id}")
    # 2 x 38,00 + 3 x 4,50 - 1,50 = 88,00
    assert "PV-00001" in page.text and "R$ 88,00" in page.text and "Orçamento" in page.text
    assert "Confirmar pedido" in page.text and "Faturar pedido" not in page.text
    assert (await post(f"/sales/{order.id}/confirm")).status_code == 303
    page = await get(f"/sales/{order.id}")
    assert "Faturar pedido" in page.text and "Confirmar pedido" not in page.text

    key = uuid4()
    invoice_body = f"operation_key={key}&issued_on={TODAY}"
    assert (await post(f"/sales/{order.id}/invoices", invoice_body)).status_code == 303
    assert (await post(f"/sales/{order.id}/invoices", invoice_body)).status_code == 303
    assert await seller.stock() == (D("8"), D("112.00"))
    page = await get(f"/sales/{order.id}")
    assert "Faturado" in page.text and "R$ 28,00" in page.text and "68,2%" in page.text
    listing = await get("/sales", q="pv-1")
    assert "PV-00001" in listing.text
    assert "PV-00001" not in (await get("/sales", status="quote")).text

    refused = await post(f"/sales/{order.id}/cancel")
    assert refused.status_code == 400 and "faturado não pode ser cancelado" in refused.text
    invoice = await SalesInvoice.get(order=order)
    assert (await post(f"/sales/invoices/{invoice.id}/reverse")).status_code == 303
    assert await seller.stock() == (D("10"), D("140.00"))
    again = await post(f"/sales/invoices/{invoice.id}/reverse")
    assert again.status_code == 400 and "já foi estornado" in again.text
    assert (await post(f"/sales/{order.id}/cancel")).status_code == 303

    # Falta de estoque aparece na página do pedido e nada é faturado.
    big = await seller.order([(seller.cake.id, D("50"), None, D("0"))])
    short = await post(f"/sales/{big.id}/invoices", f"operation_key={uuid4()}&issued_on={TODAY}")
    assert short.status_code == 400 and "Estoque insuficiente: há 10 UN de Bolo" in short.text
    assert await seller.status(big) == "confirmed"

    assert (await get(f"/sales/{uuid4()}")).status_code == 404
    assert (await post(f"/sales/invoices/{uuid4()}/reverse")).status_code == 404
    no_csrf = await asyncio.to_thread(
        client.post, f"/sales/{big.id}/cancel", headers=headers, body="csrf=errado"
    )
    assert no_csrf.status_code == 303 and await seller.status(big) == "confirmed"

    api = await get("/api/v1/sales-orders")
    data = json.loads(api.text)
    assert data["total"] == 2
    numbers = {item["number"]: item for item in data["items"]}
    assert numbers["PV-00001"]["total"] == "88.00" and numbers["PV-00001"]["status"] == "cancelled"
    detail = await get(f"/api/v1/sales-orders/{big.id}")
    assert json.loads(detail.text)["items"][0]["quantity"] == "50.0000"
    assert (await asyncio.to_thread(client.get, "/api/v1/sales-orders")).status_code == 401


async def test_sales_profile_sells_and_registers_customers_only() -> None:
    seller = await Seller.create()
    client = RobynTestClient(app)

    for role, can_sell, can_contacts in (
        ("sales", True, True),
        ("purchasing", False, True),
        ("finance", False, True),
        ("stock", False, False),
        ("viewer", False, False),
    ):
        membership = await add_member(
            seller.organization, role, f"{role}-{uuid4().hex}@example.test", PASSWORD, role
        )
        headers, token = await _headers(await membership.user)
        quote = await seller.order(confirm=False)
        confirm = await asyncio.to_thread(
            client.post, f"/sales/{quote.id}/confirm", headers=headers, body=f"csrf={token}"
        )
        assert (confirm.status_code != 403) == can_sell, role
        assert (await seller.status(quote) == "confirmed") == can_sell, role
        contact = await asyncio.to_thread(
            client.post,
            "/contacts",
            headers=headers,
            body=f"csrf={token}&person_type=individual&name=Cli+{uuid4().hex[:6]}&is_customer=true",
        )
        assert (contact.status_code != 403) == can_contacts, role
        purchase = await asyncio.to_thread(
            client.post, f"/purchases/{uuid4()}/approve", headers=headers, body=f"csrf={token}"
        )
        assert (purchase.status_code != 403) == (role == "purchasing"), role
        page = await asyncio.to_thread(client.get, f"/sales/{quote.id}", headers=headers)
        assert page.status_code == 200 and ("is-readonly" in page.text) != can_sell, role
