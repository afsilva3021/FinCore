"""Dados de demonstração — SOMENTE para desenvolvimento, screenshots e demo pública.

Cria uma organização fictícia, claramente identificada no nome, com contas, categorias,
movimentos de três meses, títulos a pagar/receber e uma baixa. Nunca roda sozinho: o
comando exige FINCORE_ALLOW_DEMO_SEED=1 e recusa se o e-mail de demonstração já existir.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from fincore.application.counterparties import save_counterparty
from fincore.application.crm import (
    add_activity,
    convert_lead,
    create_opportunity,
    move_opportunity,
    save_lead,
)
from fincore.application.finance import (
    create_account,
    create_category,
    create_movement,
    create_transfer,
)
from fincore.application.identity import register_user
from fincore.application.inventory import register_stock_movement, transfer_stock
from fincore.application.members import add_member
from fincore.application.products import save_product, save_product_group, save_warehouse
from fincore.application.purchases import (
    approve_purchase_order,
    create_purchase_order,
    receive_purchase_order,
)
from fincore.application.sales import confirm_sales_order, create_sales_order, invoice_sales_order
from fincore.application.titles import create_titles, settle_title
from fincore.infrastructure.models import (
    Membership,
    PipelineStage,
    PurchaseOrderItem,
    UnitOfMeasure,
    User,
    Warehouse,
)

DEMO_ORGANIZATION = "Padaria Exemplo (dados fictícios)"
DEMO_EMAIL = "demo@fincore.example"
DEMO_PASSWORD = "Paes-Quentinhos-2026"
VIEWER_EMAIL = "leitura@fincore.example"

EXPENSES = [
    ("Farinha de trigo Moinho Exemplo", "Insumos", "1850.00"),
    ("Fermento e açúcar distribuidora", "Insumos", "420.00"),
    ("Conta de luz Enel", "Energia", "980.00"),
    ("Energia elétrica Enel", "Energia", "1020.00"),
    ("Aluguel do salão", "Aluguel", "3500.00"),
    ("Gás GLP fornos", "Gás", "640.00"),
    ("Manutenção forno industrial", "Manutenção", "750.00"),
]
INCOME = [
    ("Vendas balcão", "Vendas balcão", "4200.00"),
    ("Encomenda bolo de casamento", "Encomendas", "1600.00"),
    ("Vendas cartão maquininha", "Vendas balcão", "5300.00"),
    ("Encomenda coffee break empresa", "Encomendas", "2100.00"),
]
# (código, descrição, grupo, unidade, preço de venda, estoque mínimo, saldo inicial, custo)
# Sem saldo inicial = produto sem controle de estoque (feito na hora).
PRODUCTS = [
    ("PAO-FRANCES", "Pão francês", "Pães", "KG", "18.90", "5", "12.5", "7.20"),
    ("PAO-QUEIJO", "Pão de queijo", "Pães", "KG", "49.90", "2.5", "4", "21.50"),
    (
        "BOLO-CENOURA",
        "Bolo de cenoura com chocolate",
        "Confeitaria",
        "UN",
        "38.00",
        "2",
        "6",
        "14.00",
    ),
    ("SONHO-CREME", "Sonho de creme", "Confeitaria", "UN", "7.50", "12", "8", "2.80"),
    ("CAFE-COADO", "Café coado 200 ml", "Bebidas", "UN", "5.00", "0", None, None),
    ("SUCO-LARANJA", "Suco de laranja 1 L", "Bebidas", "L", "16.00", "3", "10", "6.50"),
]


async def seed_demo(today: date | None = None) -> str:
    today = today or date.today()
    if await User.exists(email=DEMO_EMAIL):
        raise ValueError("Os dados de demonstração já existem neste banco.")
    owner = await register_user("Ana Demonstração", DEMO_EMAIL, DEMO_PASSWORD, DEMO_ORGANIZATION)
    organization = await (await Membership.get(user=owner)).organization
    await add_member(organization, "Bruno Leitura", VIEWER_EMAIL, DEMO_PASSWORD, "viewer")

    bank = await create_account(organization, owner, "Banco Exemplo", "bank", Decimal("15000.00"))
    cash = await create_account(organization, owner, "Caixa da loja", "cash", Decimal("800.00"))
    categories = {}
    for kind, names in (
        ("expense", {name for _, name, _ in EXPENSES}),
        ("income", {name for _, name, _ in INCOME}),
    ):
        for name in sorted(names):
            categories[name] = await create_category(organization, owner, name, kind, "analytic")

    async def movement(kind: str, description: str, category: str, amount: str, day: date):
        await create_movement(
            organization=organization,
            user=owner,
            operation_key=uuid4(),
            account_id=bank.id,
            category_id=categories[category].id,
            transaction_type=kind,
            description=description,
            amount=Decimal(amount),
            competence_date=day,
            due_date=None,
            settled=day <= today,
        )

    for month in range(3):
        base = today - timedelta(days=30 * month)
        for offset, (text, category, amount) in enumerate(EXPENSES):
            await movement("expense", text, category, amount, base - timedelta(days=offset))
        for offset, (text, category, amount) in enumerate(INCOME):
            await movement("income", text, category, amount, base - timedelta(days=offset * 2))
    await create_transfer(
        organization=organization,
        user=owner,
        operation_key=uuid4(),
        source_id=bank.id,
        target_id=cash.id,
        amount=Decimal("500.00"),
        description="Troco para o caixa",
        competence_date=today,
    )

    await save_counterparty(
        organization=organization,
        person_type="company",
        name="Moinho Exemplo Ltda",
        is_supplier=True,
        city="São Paulo",
        state="SP",
    )
    supplier_titles = await create_titles(
        organization=organization,
        user=owner,
        title_type="payable",
        description="Forno novo",
        counterparty_name="Equipamentos Exemplo",
        party_type="supplier",
        account_id=bank.id,
        category_id=categories["Manutenção"].id,
        amount=Decimal("9000.00"),
        issue_date=today - timedelta(days=20),
        due_date=today - timedelta(days=5),
        payment_method="boleto",
        payment_terms="installment",
        installment_count=3,
    )
    await settle_title(
        organization=organization,
        user=owner,
        title_id=supplier_titles[0].id,
        operation_key=uuid4(),
        amount_applied=Decimal("3000.00"),
        paid_on=today - timedelta(days=5),
        account_id=bank.id,
        payment_method="boleto",
    )
    await create_titles(
        organization=organization,
        user=owner,
        title_type="receivable",
        description="Coffee break mensal",
        counterparty_name="Escritório Exemplo",
        party_type="customer",
        account_id=bank.id,
        category_id=categories["Encomendas"].id,
        amount=Decimal("2400.00"),
        issue_date=today,
        due_date=today + timedelta(days=15),
        payment_method="pix",
        payment_terms="cash",
        installment_count=1,
    )
    showcase = await save_warehouse(
        organization=organization, user=owner, code="VITRINE", name="Vitrine"
    )
    main_warehouse = await Warehouse.get(organization=organization, is_default=True)
    units = {unit.code: unit for unit in await UnitOfMeasure.filter(organization=organization)}
    groups = {}
    for name in sorted({item[2] for item in PRODUCTS}):
        groups[name] = await save_product_group(organization=organization, user=owner, name=name)
    stock_day = today - timedelta(days=3)
    created_products = {}
    for sku, name, group, unit, price, min_stock, opening, cost in PRODUCTS:
        product = created_products[sku] = await save_product(
            organization=organization,
            user=owner,
            sku=sku,
            name=name,
            group_id=groups[group].id,
            unit_id=units[unit].id,
            sale_price=Decimal(price),
            min_stock=Decimal(min_stock),
            tracks_stock=opening is not None,
        )
        if opening is None:
            continue
        await register_stock_movement(
            organization=organization,
            user=owner,
            operation_key=uuid4(),
            direction="in",
            reason="opening",
            product_id=product.id,
            warehouse_id=main_warehouse.id,
            quantity=Decimal(opening),
            unit_cost=Decimal(cost),
            occurred_on=stock_day,
        )
        if sku == "BOLO-CENOURA":
            await transfer_stock(
                organization=organization,
                user=owner,
                operation_key=uuid4(),
                product_id=product.id,
                source_warehouse_id=main_warehouse.id,
                destination_warehouse_id=showcase.id,
                quantity=Decimal("2"),
                occurred_on=today,
                notes="Reposição da vitrine",
            )
        if sku == "PAO-FRANCES":
            await register_stock_movement(
                organization=organization,
                user=owner,
                operation_key=uuid4(),
                direction="out",
                reason="adjustment",
                product_id=product.id,
                warehouse_id=main_warehouse.id,
                quantity=Decimal("1.5"),
                occurred_on=today,
                notes="Sobras do dia descartadas",
            )
    # Compra recebida em parte: gera estoque, contas a pagar e lançamento por competência.
    supplier = await save_counterparty(
        organization=organization,
        person_type="company",
        name="Laticínios Exemplo Ltda",
        trade_name="Laticínios Exemplo",
        is_supplier=True,
    )
    purchase = await create_purchase_order(
        organization=organization,
        user=owner,
        operation_key=uuid4(),
        supplier_id=supplier.id,
        warehouse_id=main_warehouse.id,
        account_id=bank.id,
        order_date=today - timedelta(days=2),
        expected_date=today + timedelta(days=5),
        payment_method="boleto",
        installment_count=2,
        first_due_days=28,
        items=[
            (created_products["PAO-QUEIJO"].id, Decimal("10"), Decimal("22.00")),
            (created_products["SONHO-CREME"].id, Decimal("24"), Decimal("2.90")),
        ],
        notes="Reposição quinzenal",
    )
    await approve_purchase_order(organization, purchase.id)
    purchase_items = {
        item.product_id: item for item in await PurchaseOrderItem.filter(order=purchase)
    }
    await receive_purchase_order(
        organization=organization,
        user=owner,
        order_id=purchase.id,
        operation_key=uuid4(),
        received_on=today,
        document_number="NF 4521",
        quantities={purchase_items[created_products["PAO-QUEIJO"].id].id: Decimal("10")},
    )
    # Vendas: uma faturada (baixa estoque, gera contas a receber, receita e custo), um
    # pedido confirmado aguardando faturamento e um orçamento em aberto.
    customer = await save_counterparty(
        organization=organization,
        person_type="company",
        name="Escritório Exemplo Ltda",
        trade_name="Escritório Exemplo",
        is_customer=True,
    )

    async def demo_sale(items, **extra):
        return await create_sales_order(
            organization=organization,
            user=owner,
            operation_key=uuid4(),
            customer_id=customer.id,
            warehouse_id=main_warehouse.id,
            account_id=bank.id,
            order_date=today,
            items=[
                (created_products[sku].id, Decimal(quantity), None, Decimal(discount))
                for sku, quantity, discount in items
            ],
            **extra,
        )

    invoiced = await demo_sale(
        [("BOLO-CENOURA", "2", "6.00"), ("CAFE-COADO", "10", "0")],
        payment_method="boleto",
        first_due_days=15,
        notes="Coffee break da reunião mensal",
    )
    await confirm_sales_order(organization, invoiced.id)
    await invoice_sales_order(
        organization=organization,
        user=owner,
        order_id=invoiced.id,
        operation_key=uuid4(),
        issued_on=today,
    )
    waiting = await demo_sale([("PAO-QUEIJO", "3", "0"), ("SUCO-LARANJA", "4", "0")])
    await confirm_sales_order(organization, waiting.id)
    await demo_sale([("SONHO-CREME", "30", "15.00")], valid_until=today + timedelta(days=7))
    # CRM: leads e oportunidades em etapas diferentes do funil.
    stages = {stage.name: stage for stage in await PipelineStage.filter(organization=organization)}

    async def demo_opportunity(title, value, stage, days, **party):
        opportunity = await create_opportunity(
            organization=organization,
            user=owner,
            title=title,
            expected_value=Decimal(value),
            expected_close=today + timedelta(days=days),
            **party,
        )
        if stage != "Novo":
            await move_opportunity(
                organization=organization,
                user=owner,
                opportunity_id=opportunity.id,
                stage_id=stages[stage].id,
                lost_reason="Fechou com outro fornecedor" if stage == "Perdido" else "",
            )
        return opportunity

    school = await save_lead(
        organization=organization,
        user=owner,
        name="Paula Mendes",
        company="Colégio Exemplo",
        email="paula@colegio.example",
        source="Indicação",
    )
    cafe = await save_lead(
        organization=organization,
        user=owner,
        name="Rui Castro",
        company="Cafeteria Exemplo",
        phone="(11) 5555-0100",
        source="Instagram",
    )
    await save_lead(organization=organization, user=owner, name="Hotel Exemplo", source="Site")
    lunch = await demo_opportunity(
        "Lanche da cantina", "3200.00", "Proposta", 12, lead_id=school.id
    )
    await demo_opportunity("Pães para o café da manhã", "1800.00", "Novo", 20, lead_id=cafe.id)
    await demo_opportunity(
        "Coffee break mensal", "2400.00", "Negociação", 6, counterparty_id=customer.id
    )
    await demo_opportunity("Festa de fim de ano", "950.00", "Ganho", 1, counterparty_id=customer.id)
    await convert_lead(organization=organization, lead_id=cafe.id, person_type="company")
    await demo_opportunity("Bolos para vitrine", "600.00", "Perdido", 3, lead_id=cafe.id)
    await add_activity(
        organization=organization,
        user=owner,
        kind="meeting",
        subject="Degustação com a coordenação",
        due_on=today + timedelta(days=2),
        opportunity_id=lunch.id,
    )
    await add_activity(
        organization=organization,
        user=owner,
        kind="call",
        subject="Confirmar quantidade semanal",
        due_on=today - timedelta(days=1),
        opportunity_id=lunch.id,
    )
    return DEMO_EMAIL
