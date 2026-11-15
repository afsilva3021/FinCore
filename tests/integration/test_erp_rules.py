"""Regras de ERP no financeiro/contábil: estorno espelhado, estorno de baixa, juros e
descontos em contas próprias, trava de período e demonstrativos."""

import asyncio
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise
from tortoise.functions import Sum

from fincore.app import app
from fincore.application.accounting import (
    create_journal_entry,
    create_ledger_account,
    post_journal_entry,
    reverse_journal_entry,
    set_books_closed_until,
)
from fincore.application.finance import (
    create_account,
    create_category,
    create_movement,
    create_transfer,
    get_account_balances,
    reverse_movement,
    settle_movement,
)
from fincore.application.identity import register_user
from fincore.application.members import add_member
from fincore.application.statements import financial_statements
from fincore.application.titles import (
    cancel_title,
    create_titles,
    reverse_settlement,
    settle_title,
    title_balances,
)
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    FinancialTitle,
    FinancialTransaction,
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    Membership,
    Organization,
    TitleSettlement,
    User,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
TODAY = date.today()
D = Decimal


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _setup(opening: str = "0.00"):
    user = await register_user("Pessoa ERP", f"erp-{uuid4().hex}@example.test", PASSWORD, "Org ERP")
    organization = await (await Membership.get(user=user)).organization
    account = await create_account(organization, user, "Banco", "bank", D(opening))
    return organization, user, account


async def _ledger(organization) -> dict[str, Decimal]:
    """Débitos − créditos efetivados por código de conta (prefixo sem o sufixo aleatório)."""
    rows = (
        await JournalEntryLine.filter(
            journal_entry__organization=organization, journal_entry__status="posted"
        )
        .annotate(debit_sum=Sum("debit"), credit_sum=Sum("credit"))
        .group_by("account__code")
        .values("account__code", "debit_sum", "credit_sum")
    )
    result: dict[str, Decimal] = {}
    for row in rows:
        code = row["account__code"]
        key = code if code.startswith("SYS-") else code.rsplit("-", 1)[0]
        result[key] = result.get(key, D("0.00")) + row["debit_sum"] - row["credit_sum"]
    return result


async def _title(organization, user, account, title_type="receivable", category=None):
    (title,) = await create_titles(
        organization=organization,
        user=user,
        title_type=title_type,
        description="Título ERP",
        counterparty_name="Contato ERP",
        party_type="customer" if title_type == "receivable" else "supplier",
        account_id=account.id,
        category_id=category.id if category else None,
        amount=D("100.00"),
        issue_date=TODAY,
        due_date=TODAY,
        payment_method="pix",
        payment_terms="cash",
        installment_count=1,
    )
    return title


async def _settle(organization, user, account, title, amount="100.00", **extra):
    return await settle_title(
        organization=organization,
        user=user,
        title_id=title.id,
        operation_key=uuid4(),
        amount_applied=D(amount),
        paid_on=extra.pop("paid_on", TODAY),
        account_id=account.id,
        payment_method="pix",
        **extra,
    )


@pytest.mark.asyncio
async def test_settlement_posts_interest_discount_and_fees_to_their_own_accounts() -> None:
    organization, user, account = await _setup()
    category = await create_category(organization, user, "Vendas", "income", "analytic")
    title = await _title(organization, user, account, category=category)
    settlement = await _settle(
        organization,
        user,
        account,
        title,
        discount=D("5.00"),
        interest=D("3.00"),
        fine=D("2.00"),
        fees=D("1.00"),
    )
    assert settlement.net_amount == D("99.00")
    assert await _ledger(organization) == {
        "FIN": D("99.00"),  # banco recebe o líquido
        "SYS-DISC-GIVEN": D("5.00"),
        "SYS-BANK-FEES": D("1.00"),
        "CAT-REV": D("-100.00"),  # a receita da categoria é só o principal
        "SYS-FIN-REV": D("-5.00"),  # juros + multa
    }
    # O razão do banco continua igual ao saldo financeiro derivado das transações.
    ((_, balance),) = await get_account_balances(organization)
    assert balance == D("99.00")

    payable = await _title(organization, user, account, "payable")
    await _settle(
        organization,
        user,
        account,
        payable,
        discount=D("10.00"),
        interest=D("4.00"),
        fees=D("2.00"),
    )
    ledger = await _ledger(organization)
    assert ledger["SYS-EXP"] == D("100.00")
    assert ledger["SYS-FIN-EXP"] == D("4.00")
    assert ledger["SYS-DISC-RECV"] == D("-10.00")
    assert ledger["SYS-BANK-FEES"] == D("3.00")
    assert ledger["FIN"] == D("99.00") - D("96.00")
    assert sum(ledger.values()) == D("0.00")


@pytest.mark.asyncio
async def test_reversing_a_settlement_reopens_the_title_and_zeroes_the_ledger() -> None:
    organization, user, account = await _setup()
    title = await _title(organization, user, account)
    first = await _settle(organization, user, account, title, "40.00", interest=D("2.00"))
    second = await _settle(organization, user, account, title, "60.00")
    assert (await FinancialTitle.get(id=title.id)).status == "settled"

    # A movimentação da baixa não pode ser estornada pelo extrato (deixaria o título pago).
    with pytest.raises(ValueError, match="baixa de um título"):
        await reverse_movement(organization, user, second.financial_transaction_id, uuid4(), TODAY)

    key = uuid4()
    reversed_ = await reverse_settlement(
        organization=organization,
        user=user,
        title_id=title.id,
        settlement_id=second.id,
        operation_key=key,
        reversed_on=TODAY,
    )
    assert reversed_.reversed_at is not None and reversed_.reversed_by_id == user.id
    title = await FinancialTitle.get(id=title.id)
    assert title.status == "partial"
    assert (await title_balances([title]))[title.id] == D("60.00")
    # Repetir o estorno não cria outro movimento.
    again = await reverse_settlement(
        organization=organization,
        user=user,
        title_id=title.id,
        settlement_id=second.id,
        operation_key=uuid4(),
        reversed_on=TODAY,
    )
    assert again.reversal_transaction_id == reversed_.reversal_transaction_id
    assert await FinancialTransaction.filter(organization=organization).count() == 3
    # O estorno não pode ser estornado.
    with pytest.raises(ValueError, match="não pode ser estornado"):
        await reverse_movement(
            organization, user, reversed_.reversal_transaction_id, uuid4(), TODAY
        )

    await reverse_settlement(
        organization=organization,
        user=user,
        title_id=title.id,
        settlement_id=first.id,
        operation_key=uuid4(),
        reversed_on=TODAY,
    )
    title = await FinancialTitle.get(id=title.id)
    assert title.status == "open"
    assert (await title_balances([title]))[title.id] == D("100.00")
    # Histórico preservado, caixa e razão zerados conta a conta (inclusive os juros).
    assert await TitleSettlement.filter(title=title).count() == 2
    ((_, balance),) = await get_account_balances(organization)
    assert balance == D("0.00")
    assert set((await _ledger(organization)).values()) == {D("0.00")}
    # O título reaberto pode receber nova baixa ou ser cancelado.
    await _settle(organization, user, account, title, "100.00")
    assert (await FinancialTitle.get(id=title.id)).status == "settled"


@pytest.mark.asyncio
async def test_title_with_only_reversed_settlements_can_be_cancelled() -> None:
    organization, user, account = await _setup()
    title = await _title(organization, user, account)
    settlement = await _settle(organization, user, account, title, "30.00")
    with pytest.raises(ValueError, match="sem baixas"):
        await cancel_title(organization=organization, title_id=title.id)
    other, _, _ = await _setup()
    with pytest.raises(ValueError, match="Título não encontrado"):
        await reverse_settlement(
            organization=other,
            user=user,
            title_id=title.id,
            settlement_id=settlement.id,
            operation_key=uuid4(),
            reversed_on=TODAY,
        )
    await reverse_settlement(
        organization=organization,
        user=user,
        title_id=title.id,
        settlement_id=settlement.id,
        operation_key=uuid4(),
        reversed_on=TODAY,
    )
    assert (await cancel_title(organization=organization, title_id=title.id)).status == "cancelled"
    with pytest.raises(ValueError, match="cancelados"):
        await _settle(organization, user, account, title, "10.00")


@pytest.mark.asyncio
async def test_movement_reversal_mirrors_the_original_accounts() -> None:
    organization, user, account = await _setup()
    category = await create_category(organization, user, "Serviços", "income", "analytic")
    movement = await create_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        account_id=account.id,
        category_id=category.id,
        transaction_type="income",
        description="Serviço",
        amount=D("250.00"),
        competence_date=TODAY,
        due_date=None,
        settled=True,
    )
    reversal = await reverse_movement(organization, user, movement.id, uuid4(), TODAY)
    ledger = await _ledger(organization)
    # Antes, o estorno debitava "Despesas sem categoria" e a receita ficava inflada.
    assert ledger == {"FIN": D("0.00"), "CAT-REV": D("0.00")}
    original_entry = await JournalEntry.get(source_transaction=movement)
    reversal_entry = await JournalEntry.get(source_transaction=reversal)
    assert reversal_entry.reversal_of_id == original_entry.id

    # Lançamento gerado por movimentação só é corrigido pela origem.
    with pytest.raises(ValueError, match="movimentação financeira"):
        await reverse_journal_entry(organization, user, original_entry.id)

    target = await create_account(organization, user, "Caixa", "cash", D("0.00"))
    await create_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        account_id=account.id,
        category_id=None,
        transaction_type="income",
        description="Aporte",
        amount=D("80.00"),
        competence_date=TODAY,
        due_date=None,
        settled=True,
    )
    await create_transfer(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        source_id=account.id,
        target_id=target.id,
        amount=D("80.00"),
        description="Troco",
        competence_date=TODAY,
    )
    out_leg = await FinancialTransaction.get(
        organization=organization, transaction_type="transfer_out"
    )
    await reverse_movement(organization, user, out_leg.id, uuid4(), TODAY)
    balances = {item.name: value for item, value in await get_account_balances(organization)}
    assert balances == {"Banco": D("80.00"), "Caixa": D("0.00")}
    assert sum((await _ledger(organization)).values()) == D("0.00")


@pytest.mark.asyncio
async def test_closed_period_blocks_every_posting_path() -> None:
    organization, user, account = await _setup()
    yesterday = TODAY - timedelta(days=1)
    open_movement = await create_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        account_id=account.id,
        category_id=None,
        transaction_type="income",
        description="Em aberto antiga",
        amount=D("10.00"),
        competence_date=yesterday,
        due_date=None,
        settled=False,
    )
    title = await _title(organization, user, account)
    ledger_a = await create_ledger_account(organization, user, "1.9", "Estoque", "asset")
    ledger_b = await create_ledger_account(organization, user, "3.9", "Capital", "equity")
    draft = await create_journal_entry(
        organization=organization,
        user=user,
        idempotency_key=uuid4(),
        entry_date=yesterday,
        description="Rascunho retroativo",
        lines=[(ledger_a.id, D("5.00"), D("0.00")), (ledger_b.id, D("0.00"), D("5.00"))],
    )

    with pytest.raises(ValueError, match="futuro"):
        await set_books_closed_until(organization, user, TODAY + timedelta(days=1))
    await set_books_closed_until(organization, user, yesterday)
    stored = await Organization.get(id=organization.id)
    assert stored.books_closed_until == yesterday and stored.books_closed_by_id == user.id

    with pytest.raises(ValueError, match="período contábil está fechado"):
        await create_movement(
            organization=organization,
            user=user,
            operation_key=uuid4(),
            account_id=account.id,
            category_id=None,
            transaction_type="expense",
            description="Retroativa",
            amount=D("1.00"),
            competence_date=yesterday,
            due_date=None,
            settled=True,
        )
    with pytest.raises(ValueError, match="fechado"):
        await _settle(organization, user, account, title, paid_on=yesterday)
    with pytest.raises(ValueError, match="fechado"):
        await post_journal_entry(organization, draft.id)
    # Nada ficou pela metade.
    assert await FinancialTransaction.filter(organization=organization).count() == 1
    assert (await FinancialTitle.get(id=title.id)).status == "open"
    assert not await JournalEntry.filter(organization=organization, status="posted").exists()

    # Liquidar hoje um movimento de competência fechada lança na data da liquidação.
    assert await settle_movement(organization, user, open_movement.id)
    entry = await JournalEntry.get(source_transaction=open_movement)
    assert entry.entry_date == TODAY
    # Datas depois do fechamento seguem normais; reabrir libera o período.
    await _settle(organization, user, account, title, paid_on=TODAY)
    await set_books_closed_until(organization, user, None)
    assert (await post_journal_entry(organization, draft.id)).status == "posted"


@pytest.mark.asyncio
async def test_statements_match_reference_values() -> None:
    organization, user, account = await _setup("1000.00")
    sales = await create_category(organization, user, "Vendas", "income", "analytic")
    rent = await create_category(organization, user, "Aluguel", "expense", "analytic")

    async def movement(kind, category, amount, day):
        return await create_movement(
            organization=organization,
            user=user,
            operation_key=uuid4(),
            account_id=account.id,
            category_id=category.id,
            transaction_type=kind,
            description=f"{kind} {amount}",
            amount=D(amount),
            competence_date=day,
            due_date=None,
            settled=True,
        )

    await movement("income", sales, "500.00", TODAY)
    refunded = await movement("income", sales, "50.00", TODAY)
    await reverse_movement(organization, user, refunded.id, uuid4(), TODAY)
    await movement("expense", rent, "150.00", TODAY)
    title = await _title(organization, user, account, category=sales)
    await _settle(organization, user, account, title, interest=D("10.00"), fees=D("4.00"))

    statements = await financial_statements(organization, TODAY, TODAY)
    trial, income, sheet = (
        statements.trial_balance,
        statements.income_statement,
        statements.balance_sheet,
    )
    assert trial.is_balanced
    revenues = {item.name: amount for item, amount in income.revenues}
    expenses = {item.name: amount for item, amount in income.expenses}
    assert revenues == {
        "Vendas": D("600.00"),  # 500 + 100 do título; os 50 estornados se anulam
        "Receitas financeiras (juros e multas)": D("10.00"),
    }
    assert expenses == {"Aluguel": D("150.00"), "Tarifas bancárias": D("4.00")}
    assert income.result == D("456.00")
    assert sheet.total_assets == D("1456.00")
    assert sheet.total_equity == D("1000.00")
    assert sheet.accumulated_result == D("456.00")
    assert sheet.is_balanced
    # O ativo do balanço é o saldo financeiro derivado das transações.
    ((_, balance),) = await get_account_balances(organization)
    assert balance == sheet.total_assets

    # Período anterior: sem movimento; o saldo de abertura (lançado hoje) não aparece.
    earlier = await financial_statements(
        organization, TODAY - timedelta(days=30), TODAY - timedelta(days=1)
    )
    assert earlier.trial_balance.rows == [] and earlier.income_statement.result == D("0.00")
    with pytest.raises(ValueError, match="data inicial"):
        await financial_statements(organization, TODAY, TODAY - timedelta(days=1))
    other, _, _ = await _setup()
    assert (await financial_statements(other, TODAY, TODAY)).balance_sheet.total_assets == D("0")


async def _client(user: User, organization):
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(organization.id), "sv": user.session_version}
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    client = RobynTestClient(app)

    async def get(path: str, **query: str):
        return await asyncio.to_thread(
            client.get, path, headers={"cookie": cookie}, query_params=query
        )

    async def post(path: str, body: str = ""):
        headers = {"cookie": cookie, "content-type": "application/x-www-form-urlencoded"}
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    async def api(path: str, payload: dict):
        headers = {"cookie": cookie, "content-type": "application/json", "x-csrf-token": token}
        response = await asyncio.to_thread(
            client.post, path, headers=headers, body=json.dumps(payload)
        )
        return response.status_code, json.loads(response.text)

    return get, post, api


@pytest.mark.asyncio
async def test_pages_and_api_for_reversal_statements_and_period_lock() -> None:
    organization, user, account = await _setup()
    title = await _title(organization, user, account)
    settlement = await _settle(organization, user, account, title, "40.00")
    get, post, api = await _client(user, organization)

    page = await get("/receivables", status="partial")
    assert "Estornar baixa" in page.text
    response = await post(
        f"/titles/{title.id}/settlements/{settlement.id}/reverse", f"operation_key={uuid4()}"
    )
    assert response.status_code == 303 and response.headers.get("location") == "/receivables"
    assert (await FinancialTitle.get(id=title.id)).status == "open"

    second = await _settle(organization, user, account, title, "25.00")
    status, body = await api(
        f"/api/v1/titles/{title.id}/settlements/{second.id}/reverse",
        {"operation_key": str(uuid4())},
    )
    assert status == 200 and body["reversed_at"] and body["reversal_transaction_id"]
    status, _ = await api(
        f"/api/v1/titles/{title.id}/settlements/{uuid4()}/reverse", {"operation_key": str(uuid4())}
    )
    assert status == 404
    # Estorno pelo extrato da movimentação de uma baixa é recusado com o motivo.
    third = await _settle(organization, user, account, title, "10.00")
    response = await post(
        f"/transactions/{third.financial_transaction_id}/reverse", f"operation_key={uuid4()}"
    )
    assert response.status_code == 400 and "baixa de um título" in response.text

    page = await get("/accounting/statements")
    assert page.status_code == 200 and "Balancete de verificação" in page.text
    assert (await get("/accounting/statements", start="2026-13-01")).status_code == 400

    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert (await post("/accounting/period-lock", f"closed_until={yesterday}")).status_code == 303
    assert "Período fechado até" in (await get("/accounting")).text
    response = await post(
        f"/titles/{title.id}/settlements",
        f"operation_key={uuid4()}&amount_applied=5,00&paid_on={yesterday}"
        f"&account_id={account.id}&payment_method=pix",
    )
    assert response.status_code == 400 and "período contábil está fechado" in response.text

    # Perfil financeiro lança, mas não fecha nem reabre período.
    email = f"financeiro-{uuid4().hex}@example.test"
    await add_member(organization, "Financeiro", email, PASSWORD, "finance")
    _, finance_post, _ = await _client(await User.get(email=email), organization)
    assert (await finance_post("/accounting/period-lock", "closed_until=")).status_code == 403
    assert (await Organization.get(id=organization.id)).books_closed_until is not None


@pytest.mark.asyncio
async def test_manual_journal_reversal_still_works_and_respects_the_lock() -> None:
    organization, user, _ = await _setup()
    first = await create_ledger_account(organization, user, "1.1", "Estoque", "asset")
    second = await create_ledger_account(organization, user, "3.1", "Capital", "equity")
    entry = await create_journal_entry(
        organization=organization,
        user=user,
        idempotency_key=uuid4(),
        entry_date=TODAY,
        description="Aporte em estoque",
        lines=[(first.id, D("70.00"), D("0.00")), (second.id, D("0.00"), D("70.00"))],
    )
    await post_journal_entry(organization, entry.id)
    await set_books_closed_until(organization, user, TODAY)
    with pytest.raises(ValueError, match="fechado"):
        await reverse_journal_entry(organization, user, entry.id)
    await set_books_closed_until(organization, user, None)
    reversal = await reverse_journal_entry(organization, user, entry.id)
    with pytest.raises(ValueError, match="não pode ser estornado"):
        await reverse_journal_entry(organization, user, reversal.id)
    balances = await LedgerAccount.filter(organization=organization, code__in=["1.1", "3.1"])
    assert len(balances) == 2
    assert set((await _ledger(organization)).values()) == {D("0.00")}
