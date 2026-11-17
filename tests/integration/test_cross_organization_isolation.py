"""Isolamento entre organizações em todas as rotas que recebem um ID na URL.

Uma organização B autenticada tenta ler e alterar registros da organização A usando os
IDs reais de A. Nenhuma tentativa pode revelar dados de A nem alterar o estado de A.
"""

import asyncio
import json
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise

from fincore.app import app
from fincore.application.accounting import create_journal_entry
from fincore.application.counterparties import save_counterparty
from fincore.application.crm import add_activity, create_opportunity, save_lead
from fincore.application.finance import create_account, create_movement
from fincore.application.fiscal import issue_fiscal_document, save_tax_profile
from fincore.application.identity import register_user
from fincore.application.inventory import register_stock_movement
from fincore.application.products import save_product, save_product_group, save_warehouse
from fincore.application.purchases import (
    approve_purchase_order,
    create_purchase_order,
    receive_purchase_order,
)
from fincore.application.sales import confirm_sales_order, create_sales_order, invoice_sales_order
from fincore.application.titles import create_titles
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    Counterparty,
    CrmActivity,
    FinancialAccount,
    FinancialTitle,
    FinancialTransaction,
    FiscalDocument,
    ImportedStatementTransaction,
    JournalEntry,
    JournalEntryLine,
    Lead,
    LedgerAccount,
    Membership,
    Opportunity,
    PipelineStage,
    Product,
    ProductGroup,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    ReconciliationMatch,
    SalesInvoice,
    SalesOrder,
    StockBalance,
    StockMovement,
    TaxProfile,
    UnitOfMeasure,
    Warehouse,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
SECRET_NAME = f"Segredo-{uuid4().hex[:8]}"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _organization(label: str):
    user = await register_user(
        f"Pessoa {label}",
        f"isolamento-{label.replace(' ', '').lower()}-{uuid4().hex}@example.test",
        PASSWORD,
        label,
    )
    membership = await Membership.get(user=user)
    return user, membership, await membership.organization


async def _seed_organization_a():
    user, _, organization = await _organization("Org A")
    account = await create_account(organization, user, SECRET_NAME, "bank", Decimal("100.00"))

    async def movement(settled: bool) -> FinancialTransaction:
        return await create_movement(
            organization=organization,
            user=user,
            operation_key=uuid4(),
            account_id=account.id,
            category_id=None,
            transaction_type="income",
            description=f"{SECRET_NAME} movimento",
            amount=Decimal("50.00"),
            competence_date=date.today(),
            due_date=None,
            settled=settled,
        )

    open_movement = await movement(False)
    settled_movement = await movement(True)
    (title,) = await create_titles(
        organization=organization,
        user=user,
        title_type="receivable",
        description=f"{SECRET_NAME} título",
        counterparty_name="Cliente A",
        party_type="customer",
        account_id=account.id,
        category_id=None,
        amount=Decimal("80.00"),
        issue_date=date.today(),
        due_date=date.today(),
        payment_method="pix",
        payment_terms="cash",
        installment_count=1,
    )
    contact = await save_counterparty(
        organization=organization,
        person_type="company",
        name=f"{SECRET_NAME} contato",
        is_customer=True,
    )
    analytic = await LedgerAccount.filter(organization=organization, is_synthetic=False).limit(2)
    draft = await create_journal_entry(
        organization=organization,
        user=user,
        idempotency_key=uuid4(),
        entry_date=date.today(),
        description=f"{SECRET_NAME} rascunho",
        lines=[
            (analytic[0].id, Decimal("10.00"), Decimal("0.00")),
            (analytic[1].id, Decimal("0.00"), Decimal("10.00")),
        ],
    )
    statement = await ImportedStatementTransaction.create(
        organization=organization,
        account=account,
        external_id=f"FIT-{uuid4().hex}",
        posted_at=date.today(),
        description=f"{SECRET_NAME} extrato",
        amount=Decimal("50.00"),
    )
    unit = await UnitOfMeasure.get(organization=organization, code="UN")
    group = await save_product_group(
        organization=organization, user=user, name=f"{SECRET_NAME} grupo"
    )
    warehouse = await save_warehouse(
        organization=organization, user=user, code="SEGREDO", name=f"{SECRET_NAME} depósito"
    )
    product = await save_product(
        organization=organization,
        user=user,
        sku=f"SEG-{uuid4().hex[:8]}",
        name=f"{SECRET_NAME} produto",
        unit_id=unit.id,
        group_id=group.id,
    )
    stock_movement, _ = await register_stock_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        direction="in",
        reason="opening",
        product_id=product.id,
        warehouse_id=warehouse.id,
        quantity=Decimal("7"),
        unit_cost=Decimal("3.00"),
        occurred_on=date.today(),
        notes=f"{SECRET_NAME} movimento de estoque",
    )
    supplier = await save_counterparty(
        organization=organization,
        person_type="company",
        name=f"{SECRET_NAME} fornecedor",
        is_supplier=True,
    )
    purchase_order = await create_purchase_order(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        supplier_id=supplier.id,
        warehouse_id=warehouse.id,
        account_id=account.id,
        order_date=date.today(),
        items=[(product.id, Decimal("10"), Decimal("2.00"))],
        notes=f"{SECRET_NAME} pedido",
    )
    await approve_purchase_order(organization, purchase_order.id)
    purchase_item = await PurchaseOrderItem.get(order=purchase_order)
    purchase_receipt, _ = await receive_purchase_order(
        organization=organization,
        user=user,
        order_id=purchase_order.id,
        operation_key=uuid4(),
        received_on=date.today(),
        quantities={purchase_item.id: Decimal("4")},
    )
    customer = await save_counterparty(
        organization=organization,
        person_type="individual",
        name=f"{SECRET_NAME} cliente",
        is_customer=True,
    )

    async def sale(quantity: str):
        order = await create_sales_order(
            organization=organization,
            user=user,
            operation_key=uuid4(),
            customer_id=customer.id,
            warehouse_id=warehouse.id,
            account_id=account.id,
            order_date=date.today(),
            items=[(product.id, Decimal(quantity), Decimal("9.00"), Decimal("0"))],
            notes=f"{SECRET_NAME} venda",
        )
        return await confirm_sales_order(organization, order.id)

    sales_order = await sale("1")
    invoiced_order = await sale("2")
    sales_invoice, _ = await invoice_sales_order(
        organization=organization,
        user=user,
        order_id=invoiced_order.id,
        operation_key=uuid4(),
        issued_on=date.today(),
    )
    tax_profile = await save_tax_profile(
        organization=organization, name=f"{SECRET_NAME} perfil", cfop="5102", icms_cst="00"
    )
    fiscal_document, _ = await issue_fiscal_document(
        organization=organization,
        user=user,
        sales_invoice_id=sales_invoice.id,
        operation_key=uuid4(),
        tax_profile_id=tax_profile.id,
    )
    lead = await save_lead(organization=organization, user=user, name=f"{SECRET_NAME} lead")
    opportunity = await create_opportunity(
        organization=organization, user=user, title=f"{SECRET_NAME} oportunidade", lead_id=lead.id
    )
    activity = await add_activity(
        organization=organization,
        user=user,
        kind="task",
        subject=f"{SECRET_NAME} atividade",
        opportunity_id=opportunity.id,
    )
    stage = await PipelineStage.get(organization=organization, name="Perdido")
    return {
        "organization": organization,
        "tax_profile": tax_profile,
        "fiscal_document": fiscal_document,
        "lead": lead,
        "opportunity": opportunity,
        "activity": activity,
        "stage": stage,
        "sales_order": sales_order,
        "sales_invoice": sales_invoice,
        "purchase_order": purchase_order,
        "purchase_item": purchase_item,
        "purchase_receipt": purchase_receipt,
        "stock_movement": stock_movement,
        "unit": unit,
        "group": group,
        "warehouse": warehouse,
        "product": product,
        "account": account,
        "open_movement": open_movement,
        "settled_movement": settled_movement,
        "title": title,
        "contact": contact,
        "draft": draft,
        "statement": statement,
    }


async def _snapshot(seed: dict) -> dict:
    organization = seed["organization"]
    return {
        "accounts": await FinancialAccount.filter(organization=organization).values(
            "id", "name", "is_active", "opening_balance"
        ),
        "transactions": await FinancialTransaction.filter(organization=organization)
        .order_by("id")
        .values("id", "status", "amount", "description"),
        "titles": await FinancialTitle.filter(organization=organization).values("id", "status"),
        "contacts": await Counterparty.filter(organization=organization).values(
            "id", "name", "is_active"
        ),
        "journals": await JournalEntry.filter(organization=organization)
        .order_by("id")
        .values("id", "status", "description"),
        "journal_lines": await JournalEntryLine.filter(
            journal_entry__organization=organization
        ).count(),
        "products": await Product.filter(organization=organization).values(
            "id", "sku", "name", "is_active", "unit_id", "group_id"
        ),
        "groups": await ProductGroup.filter(organization=organization).values(
            "id", "name", "is_active", "parent_id"
        ),
        "units": await UnitOfMeasure.filter(organization=organization)
        .order_by("code")
        .values("id", "code", "name", "is_active"),
        "warehouses": await Warehouse.filter(organization=organization)
        .order_by("code")
        .values("id", "code", "name", "is_active", "is_default"),
        "stock_balances": await StockBalance.filter(organization=organization).values(
            "id", "quantity", "total_value", "movement_count"
        ),
        "stock_movements": await StockMovement.filter(organization=organization).count(),
        "fiscal_documents": await FiscalDocument.filter(organization=organization).values(
            "id", "status", "number"
        ),
        "tax_profiles": await TaxProfile.filter(organization=organization).values(
            "id", "is_active"
        ),
        "leads": await Lead.filter(organization=organization).values("id", "status"),
        "opportunities": await Opportunity.filter(organization=organization).values(
            "id", "stage_id", "lost_reason"
        ),
        "activities": await CrmActivity.filter(organization=organization).values("id", "done_at"),
        "activity_count": await CrmActivity.filter(organization=organization).count(),
        "sales_orders": await SalesOrder.filter(organization=organization)
        .order_by("number")
        .values("id", "status"),
        "sales_invoices": await SalesInvoice.filter(organization=organization).values(
            "id", "status"
        ),
        "purchase_orders": await PurchaseOrder.filter(organization=organization).values(
            "id", "status"
        ),
        "purchase_items": await PurchaseOrderItem.filter(order__organization=organization).values(
            "id", "received_quantity"
        ),
        "purchase_receipts": await PurchaseReceipt.filter(organization=organization).values(
            "id", "status"
        ),
        "matches": await ReconciliationMatch.filter(
            statement_transaction__organization=organization
        ).count(),
    }


@pytest.mark.asyncio
async def test_another_organization_cannot_read_or_change_any_record_by_id() -> None:
    seed = await _seed_organization_a()
    intruder, intruder_membership, intruder_org = await _organization("Org B")
    intruder_account = await create_account(
        intruder_org, intruder, "Conta B", "bank", Decimal("0.00")
    )
    before = await _snapshot(seed)

    token = uuid4().hex
    session = serializer.dumps(
        {
            "uid": str(intruder.id),
            "org": str(intruder_membership.organization_id),
            "sv": intruder.session_version,
        }
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    form = {"cookie": cookie, "content-type": "application/x-www-form-urlencoded"}
    api = {"cookie": cookie, "content-type": "application/json", "x-csrf-token": token}
    today = date.today().isoformat()
    a = {key: str(value.id) for key, value in seed.items() if key != "organization"}

    attempts = [
        ("get", f"/accounts/{a['account']}/edit", form, None),
        ("post", f"/accounts/{a['account']}/update", form, "name=Invadida&account_type=cash"),
        ("post", f"/accounts/{a['account']}/active", form, "active=false"),
        (
            "post",
            f"/titles/{a['title']}/settlements",
            form,
            f"operation_key={uuid4()}&amount_applied=80,00&paid_on={today}"
            f"&account_id={intruder_account.id}&payment_method=pix",
        ),
        ("post", f"/titles/{a['title']}/cancel", form, ""),
        ("post", f"/transactions/{a['open_movement']}/settle", form, f"settled_at={today}"),
        (
            "post",
            f"/transactions/{a['settled_movement']}/reverse",
            form,
            f"operation_key={uuid4()}&reversed_at={today}",
        ),
        (
            "post",
            f"/accounting/journals/{a['draft']}/update",
            form,
            f"entry_date={today}&description=Invadido&action=draft",
        ),
        ("post", f"/accounting/journals/{a['draft']}/post", form, ""),
        ("post", f"/accounting/journals/{a['draft']}/reverse", form, f"entry_date={today}"),
        (
            "post",
            f"/reconciliation/{a['statement']}/match",
            form,
            f"transaction_id={a['settled_movement']}",
        ),
        ("post", f"/reconciliation/{a['statement']}/unmatch", form, ""),
        ("get", f"/contacts/{a['contact']}/edit", form, None),
        ("post", f"/contacts/{a['contact']}/update", form, "person_type=company&name=Invadido"),
        ("post", f"/contacts/{a['contact']}/active", form, "active=false"),
        ("get", f"/products/{a['product']}/edit", form, None),
        (
            "post",
            f"/products/{a['product']}/update",
            form,
            f"sku=INVADIDO&name=Invadido&unit_id={a['unit']}",
        ),
        ("post", f"/products/{a['product']}/active", form, "is_active=false"),
        ("post", "/products", form, f"sku=INTRUSO-A&name=Intruso&unit_id={a['unit']}"),
        ("post", "/products", form, f"sku=INTRUSO-B&name=Intruso&group_id={a['group']}"),
        ("post", f"/product-settings/groups/{a['group']}/update", form, "name=Invadido"),
        ("post", f"/product-settings/groups/{a['group']}/active", form, "is_active=false"),
        ("post", "/product-settings/groups", form, f"name=Filho&parent_id={a['group']}"),
        (
            "post",
            f"/product-settings/units/{a['unit']}/update",
            form,
            "code=INV&name=Invadida&decimal_places=0",
        ),
        ("post", f"/product-settings/units/{a['unit']}/active", form, "is_active=false"),
        (
            "post",
            f"/product-settings/warehouses/{a['warehouse']}/update",
            form,
            "code=INV&name=Invadido",
        ),
        ("post", f"/product-settings/warehouses/{a['warehouse']}/active", form, "is_active=false"),
        ("post", f"/product-settings/warehouses/{a['warehouse']}/default", form, ""),
        ("get", f"/api/v1/products/{a['product']}", api, None),
        (
            "post",
            "/api/v1/products",
            api,
            json.dumps({"sku": "INTRUSO-C", "name": "Intruso", "unit_id": a["unit"]}),
        ),
        ("get", f"/fiscal/{a['fiscal_document']}", form, None),
        (
            "post",
            f"/fiscal/{a['fiscal_document']}/cancel",
            form,
            "reason=Tentativa+de+outra+organização",
        ),
        (
            "post",
            "/fiscal/documents",
            form,
            f"sales_invoice_id={a['sales_invoice']}&operation_key={uuid4()}",
        ),
        ("post", f"/fiscal/tax-profiles/{a['tax_profile']}/active", form, "is_active=false"),
        ("get", f"/crm/opportunities/{a['opportunity']}", form, None),
        (
            "post",
            f"/crm/opportunities/{a['opportunity']}/stage",
            form,
            f"stage_id={a['stage']}&lost_reason=Invadido",
        ),
        (
            "post",
            f"/crm/opportunities/{a['opportunity']}/activities",
            form,
            "kind=note&subject=Invadido",
        ),
        ("post", f"/crm/activities/{a['activity']}/done", form, ""),
        ("post", f"/crm/leads/{a['lead']}/convert", form, "person_type=individual"),
        ("post", f"/crm/leads/{a['lead']}/discard", form, ""),
        ("post", "/crm/opportunities", form, f"title=Intruso&lead_id={a['lead']}"),
        ("get", f"/sales/{a['sales_order']}", form, None),
        ("post", f"/sales/{a['sales_order']}/cancel", form, ""),
        ("post", f"/sales/{a['sales_order']}/confirm", form, ""),
        (
            "post",
            f"/sales/{a['sales_order']}/invoices",
            form,
            f"operation_key={uuid4()}&issued_on={today}",
        ),
        ("post", f"/sales/invoices/{a['sales_invoice']}/reverse", form, ""),
        ("get", f"/api/v1/sales-orders/{a['sales_order']}", api, None),
        ("get", f"/purchases/{a['purchase_order']}", form, None),
        ("post", f"/purchases/{a['purchase_order']}/cancel", form, ""),
        ("post", f"/purchases/{a['purchase_order']}/approve", form, ""),
        (
            "post",
            f"/purchases/{a['purchase_order']}/receipts",
            form,
            f"operation_key={uuid4()}&received_on={today}&qty_{a['purchase_item']}=1",
        ),
        ("post", f"/purchases/receipts/{a['purchase_receipt']}/reverse", form, ""),
        ("get", f"/api/v1/purchase-orders/{a['purchase_order']}", api, None),
        ("post", f"/stock/movements/{a['stock_movement']}/reverse", form, ""),
        (
            "post",
            "/stock/movements",
            form,
            f"operation_key={uuid4()}&kind=exit&product_id={a['product']}"
            f"&warehouse_id={a['warehouse']}&quantity=1&occurred_on={today}",
        ),
        (
            "post",
            "/stock/movements",
            form,
            f"operation_key={seed['stock_movement'].operation_key}&kind=entry"
            f"&product_id={a['product']}&warehouse_id={a['warehouse']}&quantity=1"
            f"&unit_cost=1&occurred_on={today}",
        ),
        ("post", f"/api/v1/stock/movements/{a['stock_movement']}/reverse", api, "{}"),
        (
            "post",
            "/api/v1/stock/movements",
            api,
            json.dumps(
                {
                    "type": "exit",
                    "operation_key": str(uuid4()),
                    "product_id": a["product"],
                    "warehouse_id": a["warehouse"],
                    "quantity": "1",
                    "occurred_on": today,
                }
            ),
        ),
        ("get", f"/api/v1/transactions/{a['open_movement']}", api, None),
        (
            "post",
            f"/api/v1/transactions/{a['open_movement']}/settle",
            api,
            json.dumps({"settled_at": today}),
        ),
        (
            "post",
            f"/api/v1/transactions/{a['settled_movement']}/reverse",
            api,
            json.dumps({"operation_key": str(uuid4()), "reversed_at": today}),
        ),
    ]
    client = RobynTestClient(app)
    for method, path, headers, body in attempts:
        if method == "get":
            response = await asyncio.to_thread(client.get, path, headers=headers)
        else:
            payload = body if headers is api else f"csrf={token}&{body}"
            response = await asyncio.to_thread(client.post, path, headers=headers, body=payload)
        assert SECRET_NAME not in response.text, f"{path} revelou dados da organização A"
        assert response.status_code != 500, f"{path} quebrou com 500"

    assert await _snapshot(seed) == before
    # Nada foi criado na organização B apontando para unidade ou grupo da organização A.
    assert not await Product.filter(organization=intruder_org).exists()
    assert not await ProductGroup.filter(organization=intruder_org).exists()
    assert not await StockMovement.filter(organization=intruder_org).exists()
    assert not await Opportunity.filter(organization=intruder_org).exists()
    assert not await FiscalDocument.filter(organization=intruder_org).exists()

    # Listagens da organização B não mostram nada da organização A.
    for path in (
        "/stock",
        "/stock/movements",
        "/products",
        "/api/v1/stock/balances",
        "/api/v1/stock/movements",
        "/purchases",
        "/api/v1/purchase-orders",
        "/sales",
        "/api/v1/sales-orders",
        "/crm",
        "/crm/leads",
        "/api/v1/crm/opportunities",
        "/fiscal",
        "/api/v1/fiscal/documents",
    ):
        response = await asyncio.to_thread(client.get, path, headers=form)
        assert response.status_code == 200, path
        assert SECRET_NAME not in response.text, f"{path} revelou dados da organização A"
    filtered = await asyncio.to_thread(
        client.get,
        "/api/v1/stock/movements",
        headers=form,
        query_params={"product_id": a["product"]},
    )
    assert json.loads(filtered.text)["total"] == 0
