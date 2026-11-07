"""Dados de demonstração — SOMENTE para desenvolvimento, screenshots e demo pública.

Cria uma organização fictícia, claramente identificada no nome, com contas, categorias,
movimentos de três meses, títulos a pagar/receber e uma baixa. Nunca roda sozinho: o
comando exige FINCORE_ALLOW_DEMO_SEED=1 e recusa se o e-mail de demonstração já existir.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from fincore.application.counterparties import save_counterparty
from fincore.application.finance import (
    create_account,
    create_category,
    create_movement,
    create_transfer,
)
from fincore.application.identity import register_user
from fincore.application.members import add_member
from fincore.application.titles import create_titles, settle_title
from fincore.infrastructure.models import Membership, User

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
    return DEMO_EMAIL
