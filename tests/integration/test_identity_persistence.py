import asyncio
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import urlencode
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise

from fincore.app import app
from fincore.application.accounting import (
    create_journal_entry,
    create_ledger_account,
    get_ledger_balances,
    post_journal_entry,
    reverse_journal_entry,
    update_draft_journal_entry,
)
from fincore.application.counterparties import get_counterparty
from fincore.application.finance import (
    backfill_settled_movement_journals,
    create_account,
    create_category,
    create_movement,
    create_transfer,
    get_account_balances,
    parse_money,
    reverse_movement,
    settle_movement,
)
from fincore.application.identity import authenticate, register_user
from fincore.application.reconciliation import (
    import_ofx,
    match_statement_transaction,
    unmatch_statement_transaction,
)
from fincore.application.titles import create_titles, settle_title, title_balances
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    Counterparty,
    FinancialAccount,
    FinancialTitle,
    FinancialTransaction,
    ImportedStatementTransaction,
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    Membership,
    ReconciliationEvent,
    ReconciliationMatch,
    TitleSettlement,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_registration_persists_identity_hash_and_isolated_organization() -> None:
    suffix = uuid4().hex
    first = await register_user(
        "Pessoa Um", f"um-{suffix}@example.test", "senha-local-segura-123", "Organização Um"
    )
    second = await register_user(
        "Pessoa Dois", f"dois-{suffix}@example.test", "senha-local-segura-456", "Organização Dois"
    )

    assert first.password_hash != "senha-local-segura-123"
    authenticated = await authenticate(first.email, "senha-local-segura-123")
    assert authenticated is not None
    _, first_membership = authenticated
    second_membership = await Membership.filter(user=second).first()
    assert second_membership is not None
    assert first_membership.organization_id != second_membership.organization_id
    other_org_membership_count = await Membership.filter(
        user=first, organization_id=second_membership.organization_id
    ).count()
    assert other_org_membership_count == 0


@pytest.mark.asyncio
async def test_invalid_password_does_not_authenticate() -> None:
    suffix = uuid4().hex
    await register_user("Pessoa", f"user-{suffix}@example.test", "senha-correta-123", "Organização")

    assert await authenticate(f"user-{suffix}@example.test", "senha-incorreta-123") is None


@pytest.mark.asyncio
async def test_synthetic_categories_group_analytic_subcategories_and_balances() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Plano", f"plano-{suffix}@example.test", "senha-plano-123456", "Org Plano"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization

    group = await create_category(
        organization, user, "Receitas operacionais", "income", "synthetic"
    )
    sales = await create_category(
        organization, user, "Vendas de serviços", "income", "analytic", group.id
    )
    assert sales.parent_id == group.id
    assert group.ledger_account_id is not None
    assert sales.ledger_account_id is not None
    group_account = await LedgerAccount.get(id=group.ledger_account_id)
    leaf_account = await LedgerAccount.get(id=sales.ledger_account_id)
    assert group_account.is_synthetic
    assert leaf_account.is_synthetic is False
    assert leaf_account.parent_id == group_account.id
    with pytest.raises(ValueError, match="contas analíticas"):
        await create_journal_entry(
            organization=organization,
            user=user,
            idempotency_key=uuid4(),
            entry_date=date.today(),
            description="Grupo não pode receber lançamento",
            lines=[
                (group_account.id, Decimal("1.00"), Decimal("0.00")),
                (leaf_account.id, Decimal("0.00"), Decimal("1.00")),
            ],
        )

    cash = await create_account(organization, user, "Conta hierarquia", "bank", Decimal("0.00"))
    await create_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        account_id=cash.id,
        category_id=sales.id,
        transaction_type="income",
        description="Receita analítica",
        amount=Decimal("35.00"),
        competence_date=date.today(),
        due_date=None,
        settled=True,
    )
    balances = {account.id: balance for account, balance in await get_ledger_balances(organization)}
    assert balances[group_account.id] == Decimal("35.00")
    assert balances[leaf_account.id] == Decimal("35.00")

    session = serializer.dumps({"uid": str(user.id), "org": str(organization.id)})
    csrf_cookie = csrf_serializer.dumps("category-tree-test")
    headers = {"cookie": f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_cookie}"}
    client = RobynTestClient(app)
    categories_page = await asyncio.to_thread(client.get, "/categories", headers=headers)
    accounting_page = await asyncio.to_thread(client.get, "/accounting", headers=headers)
    reconciliation_page = await asyncio.to_thread(client.get, "/reconciliation", headers=headers)
    transactions_page = await asyncio.to_thread(client.get, "/transactions", headers=headers)
    assert categories_page.status_code == 200
    assert "Receitas operacionais" in categories_page.text
    assert "Vendas de serviços" in categories_page.text
    assert "Sintética" in categories_page.text
    assert 'aria-controls="category-drawer"' in categories_page.text
    assert (
        'id="category-drawer"' in categories_page.text
        and 'id="category-drawer" data-movement-drawer' in categories_page.text
    )
    assert accounting_page.status_code == 200
    assert "Conta contábil analítica" in accounting_page.text
    assert "Saldo normal" in accounting_page.text
    assert 'aria-controls="ledger-account-drawer"' in accounting_page.text
    assert 'aria-controls="journal-entry-drawer"' in accounting_page.text
    assert 'id="ledger-account-drawer" data-movement-drawer' in accounting_page.text
    assert 'id="journal-entry-drawer" data-movement-drawer' in accounting_page.text
    assert reconciliation_page.status_code == 200
    assert 'aria-controls="ofx-import-drawer"' in reconciliation_page.text
    assert 'id="ofx-import-drawer" data-movement-drawer' in reconciliation_page.text
    assert transactions_page.status_code == 200
    assert "Receitas operacionais / Vendas de serviços" in transactions_page.text

    with pytest.raises(ValueError, match="categoria analítica"):
        await create_movement(
            organization=organization,
            user=user,
            operation_key=uuid4(),
            account_id=cash.id,
            category_id=group.id,
            transaction_type="income",
            description="Categoria de grupo não pode receber lançamento",
            amount=Decimal("1.00"),
            competence_date=date.today(),
            due_date=None,
            settled=False,
        )


@pytest.mark.asyncio
async def test_financial_movements_settlement_transfer_and_idempotency() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Financeiro", f"fin-{suffix}@example.test", "senha-financeira-123", "Org Financeira"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    cash = await create_account(
        organization, user, "Conta movimento", "bank", parse_money("1000,00")
    )
    reserve = await create_account(organization, user, "Reserva", "cash", parse_money("200,00"))
    category = await create_category(organization, user, "Vendas", "income")
    operation_key = uuid4()
    income = await create_movement(
        organization=organization,
        user=user,
        operation_key=operation_key,
        account_id=cash.id,
        category_id=category.id,
        transaction_type="income",
        description="Recebimento futuro",
        amount=parse_money("100,00"),
        competence_date=date.today(),
        due_date=None,
        settled=False,
    )
    repeated_income = await create_movement(
        organization=organization,
        user=user,
        operation_key=operation_key,
        account_id=cash.id,
        category_id=category.id,
        transaction_type="income",
        description="Recebimento futuro",
        amount=parse_money("100,00"),
        competence_date=date.today(),
        due_date=None,
        settled=False,
    )
    assert repeated_income.id == income.id
    initial_balances = {
        account.name: balance for account, balance in await get_account_balances(organization)
    }
    assert initial_balances["Conta movimento"] == Decimal("1000.00")
    assert await settle_movement(organization, user, income.id)
    assert not await settle_movement(organization, user, income.id)
    settled_income = await FinancialTransaction.get(id=income.id)
    assert settled_income.settled_by_id == user.id
    transfer_id = await create_transfer(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        source_id=cash.id,
        target_id=reserve.id,
        amount=parse_money("50,00"),
        description="Reserva mensal",
        competence_date=date.today(),
    )
    balances = {
        account.name: balance for account, balance in await get_account_balances(organization)
    }
    assert balances == {"Conta movimento": Decimal("1050.00"), "Reserva": Decimal("250.00")}
    assert await FinancialTransaction.filter(transfer_id=transfer_id).count() == 2
    transfer_source = await FinancialTransaction.get(
        transfer_id=transfer_id, transaction_type="transfer_out"
    )
    automatic_entries = await JournalEntry.filter(
        source_transaction_id__in=[income.id, transfer_source.id], status="posted"
    ).all()
    assert len(automatic_entries) == 2
    for automatic_entry in automatic_entries:
        entry_lines = await JournalEntryLine.filter(journal_entry=automatic_entry).all()
        assert sum((line.debit for line in entry_lines), Decimal("0.00")) == sum(
            (line.credit for line in entry_lines), Decimal("0.00")
        )


@pytest.mark.asyncio
async def test_concurrent_transfer_retries_create_only_one_pair() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Concorrência", f"race-{suffix}@example.test", "senha-concorrente-123", "Org Concorrente"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    source = await create_account(organization, user, "Origem", "bank", Decimal("0.00"))
    target = await create_account(organization, user, "Destino", "bank", Decimal("0.00"))
    key = uuid4()

    async def submit_transfer():
        return await create_transfer(
            organization=organization,
            user=user,
            operation_key=key,
            source_id=source.id,
            target_id=target.id,
            amount=Decimal("15.00"),
            description="Reenvio concorrente",
            competence_date=date.today(),
        )

    transfer_ids = await asyncio.gather(submit_transfer(), submit_transfer())

    assert transfer_ids[0] == transfer_ids[1]
    assert await FinancialTransaction.filter(transfer_id=transfer_ids[0]).count() == 2


@pytest.mark.asyncio
async def test_backfill_creates_missing_historical_journal_once() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Histórico", f"backfill-{suffix}@example.test", "senha-backfill-123", "Org Histórico"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    legacy_account = await FinancialAccount.create(
        organization=organization,
        created_by=user,
        name="Conta antiga",
        account_type="bank",
        opening_balance=Decimal("60.00"),
    )
    movement = await FinancialTransaction.create(
        organization=organization,
        account=legacy_account,
        created_by=user,
        operation_key=uuid4(),
        transaction_type="income",
        description="Recebimento legado",
        amount=Decimal("10.00"),
        competence_date=date.today(),
        settled_at=datetime.now(UTC),
        status="settled",
    )

    created = await backfill_settled_movement_journals()

    assert created >= 1
    assert await JournalEntry.filter(source_transaction=movement).count() == 1
    assert await backfill_settled_movement_journals() == 0
    refreshed_account = await FinancialAccount.get(id=legacy_account.id)
    assert refreshed_account.ledger_account_id is not None
    matching_ledger = await LedgerAccount.get(id=refreshed_account.ledger_account_id)
    balances = {account.id: balance for account, balance in await get_ledger_balances(organization)}
    assert balances[matching_ledger.id] == Decimal("70.00")


@pytest.mark.asyncio
async def test_ofx_import_is_idempotent_and_manual_match_is_audited() -> None:
    from pathlib import Path

    suffix = uuid4().hex
    user = await register_user(
        "Conciliação", f"conc-{suffix}@example.test", "senha-conciliacao-123", "Org Conciliação"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    account = await create_account(organization, user, "Conta OFX", "bank", Decimal("0.00"))
    category = await create_category(organization, user, "Vendas", "income")
    movement = await create_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        account_id=account.id,
        category_id=category.id,
        transaction_type="income",
        description="Recebimento cliente",
        amount=Decimal("1250.75"),
        competence_date=date(2026, 10, 15),
        due_date=None,
        settled=True,
    )
    content = (Path(__file__).parents[1] / "fixtures" / "extrato-demo.ofx").read_text()
    assert await import_ofx(organization, account.id, content) == (2, 0)
    assert await import_ofx(organization, account.id, content) == (0, 2)
    statement = await ImportedStatementTransaction.get(
        organization=organization, external_id="demo-001"
    )
    await match_statement_transaction(organization, user, statement.id, movement.id)
    await unmatch_statement_transaction(organization, user, statement.id)

    assert await ReconciliationMatch.filter(statement_transaction=statement).count() == 0
    assert await ReconciliationEvent.filter(statement_transaction=statement).count() == 2


@pytest.mark.asyncio
async def test_financial_reversal_preserves_original_and_compensates_transfer() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Estornos", f"reversal-{suffix}@example.test", "senha-estorno-segura-123", "Org Estornos"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    first = await create_account(organization, user, "Conta Estorno A", "bank", Decimal("0"))
    second = await create_account(organization, user, "Conta Estorno B", "bank", Decimal("0"))
    income = await create_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        account_id=first.id,
        category_id=None,
        transaction_type="income",
        description="Receita original",
        amount=Decimal("80.00"),
        competence_date=date.today(),
        due_date=None,
        settled=True,
    )
    reversal = await reverse_movement(organization, user, income.id, uuid4(), date.today())
    assert reversal.transaction_type == "expense"
    assert reversal.reversal_of_id == income.id
    assert (await FinancialTransaction.get(id=income.id)).status == "settled"
    with pytest.raises(ValueError, match="já possui estorno"):
        await reverse_movement(organization, user, income.id, uuid4(), date.today())
    repeated = await reverse_movement(
        organization, user, income.id, reversal.operation_key, date.today()
    )
    assert repeated.id == reversal.id

    transfer_id = await create_transfer(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        source_id=first.id,
        target_id=second.id,
        amount=Decimal("25.00"),
        description="Transferência original",
        competence_date=date.today(),
    )
    transfer_out = await FinancialTransaction.get(
        organization=organization, transfer_id=transfer_id, transaction_type="transfer_out"
    )
    transfer_reversal = await reverse_movement(
        organization, user, transfer_out.id, uuid4(), date.today()
    )
    assert transfer_reversal.reversal_of_id == transfer_out.id
    balances = {
        account.name: amount for account, amount in await get_account_balances(organization)
    }
    assert balances["Conta Estorno A"] == Decimal("0.00")
    assert balances["Conta Estorno B"] == Decimal("0.00")


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Tortoise connection .*different event loop")
async def test_authenticated_json_api_creates_and_settles_financial_movement() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "API", f"api-integration-{suffix}@example.test", "senha-api-integracao-123", "Org API"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    token = "csrf-api-integration-token"
    session = serializer.dumps({"uid": str(user.id), "org": str(organization.id)})
    cookies = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    headers = {"cookie": cookies, "x-csrf-token": token}
    client = RobynTestClient(app)

    async def request(method: str, path: str, payload: dict | None = None):
        call = client.get if method == "GET" else client.post
        kwargs = {"headers": headers}
        if payload is not None:
            kwargs["json_data"] = payload
        return await asyncio.to_thread(call, path, **kwargs)

    account = await request(
        "POST",
        "/api/v1/accounts",
        {
            "name": "Conta API persistente",
            "type": "bank",
            "bank_name": "Banco de Teste",
            "bank_code": "001",
            "branch_number": "0123-4",
            "account_number": "000123-5",
        },
    )
    assert account.status_code == 201
    assert account.json()["bank_code"] == "001"
    assert account.json()["branch_number"] == "0123-4"
    assert account.json()["account_number"] == "000123-5"
    invalid_bank_account = await request(
        "POST",
        "/api/v1/accounts",
        {"name": "Conta API inválida", "type": "bank", "branch_number": "12345"},
    )
    assert invalid_bank_account.status_code == 400
    category = await request(
        "POST",
        "/api/v1/categories",
        {"name": "Vendas API", "transaction_type": "income"},
    )
    assert category.status_code == 201
    operation_key = str(uuid4())
    payload = {
        "operation_key": operation_key,
        "type": "income",
        "account_id": account.json()["id"],
        "category_id": category.json()["id"],
        "description": "Recebimento via API",
        "amount": "25.00",
        "competence_date": date.today().isoformat(),
        "settled": False,
    }
    created = await request("POST", "/api/v1/transactions", payload)
    replay = await request("POST", "/api/v1/transactions", payload)
    assert created.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    settled = await request("POST", f"/api/v1/transactions/{created.json()['id']}/settle")
    assert settled.status_code == 200
    assert settled.json()["settled_by_id"] == str(user.id)


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Tortoise connection .*different event loop")
async def test_contact_crud_roles_isolation_and_reactivation() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Cadastro de contatos",
        f"contatos-{suffix}@example.test",
        "senha-contatos-integracao-123",
        "Organização de contatos",
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    token = "csrf-contact-integration"
    session = serializer.dumps({"uid": str(user.id), "org": str(organization.id)})
    headers = {
        "cookie": f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    client = RobynTestClient(app)

    async def post(path: str, payload: dict[str, str]):
        return await asyncio.to_thread(
            client.post, path, body=urlencode({"csrf": token, **payload}), headers=headers
        )

    contacts_page = await asyncio.to_thread(client.get, "/contacts", headers=headers)
    assert contacts_page.status_code == 200
    assert 'aria-controls="contact-drawer"' in contacts_page.text
    assert "data-movement-drawer" in contacts_page.text
    assert "data-drawer-initially-open" not in contacts_page.text

    created = await post(
        "/contacts",
        {
            "role": "all",
            "status": "all",
            "person_type": "company",
            "name": "Empresa de teste",
            "trade_name": "Empresa Teste",
            "tax_id": "12.345.678/0001-90",
            "is_customer": "true",
            "is_supplier": "true",
            "email": "empresa@example.test",
            "postal_code": "01001-000",
            "street": "Praça da Sé",
            "district": "Sé",
            "city": "São Paulo",
            "state": "sp",
        },
    )
    assert created.status_code == 303
    contact = await Counterparty.get(organization=organization, tax_id="12345678000190")
    assert contact.is_customer and contact.is_supplier
    assert contact.party_type == "other"
    assert contact.state == "SP"
    assert contact.postal_code == "01001000"

    other_org_user = await register_user(
        "Outra pessoa",
        f"outra-org-{suffix}@example.test",
        "senha-outra-org-123",
        "Outra organização",
    )
    other_membership = await Membership.filter(user=other_org_user).first()
    assert other_membership is not None
    other_org = await other_membership.organization
    assert await get_counterparty(other_org, contact.id) is None

    inactivated = await post(
        f"/contacts/{contact.id}/active",
        {"role": "all", "status": "all", "is_active": "false"},
    )
    assert inactivated.status_code == 303
    await contact.refresh_from_db()
    assert contact.is_active is False
    inactive_page = await asyncio.to_thread(
        client.get, "/contacts", headers=headers, query_params={"status": "inactive"}
    )
    assert "Empresa Teste" in inactive_page.text
    assert 'value="true"' in inactive_page.text

    reactivated = await post(
        f"/contacts/{contact.id}/active",
        {"role": "all", "status": "all", "is_active": "true"},
    )
    assert reactivated.status_code == 303
    await contact.refresh_from_db()
    assert contact.is_active is True
    customer_page = await asyncio.to_thread(client.get, "/customers", headers=headers)
    supplier_page = await asyncio.to_thread(client.get, "/suppliers", headers=headers)
    assert "Empresa Teste" in customer_page.text
    assert "Empresa Teste" in supplier_page.text
    assert "Reativar" not in customer_page.text


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Tortoise connection .*different event loop")
async def test_cep_lookup_route_requires_a_session_and_returns_address(monkeypatch) -> None:
    user = await register_user(
        "Consulta CEP",
        f"cep-route-{uuid4().hex}@example.test",
        "senha-cep-route-123",
        "Organização CEP",
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    session = serializer.dumps({"uid": str(user.id), "org": str(organization.id)})
    headers = {"cookie": f"{COOKIE_AUTH}={session}"}

    async def fake_lookup(value: str) -> dict[str, str]:
        assert value == "01001000"
        return {
            "postal_code": "01001-000",
            "street": "Praça da Sé",
            "district": "Sé",
            "city": "São Paulo",
            "state": "SP",
        }

    monkeypatch.setattr("fincore.presentation.web.lookup_cep", fake_lookup)
    client = RobynTestClient(app)
    response = await asyncio.to_thread(
        client.get, "/contacts/postal-code", headers=headers, query_params={"cep": "01001000"}
    )
    assert response.status_code == 200
    assert response.json()["city"] == "São Paulo"
    assert response.json()["state"] == "SP"

    anonymous = await asyncio.to_thread(
        client.get, "/contacts/postal-code", query_params={"cep": "01001000"}
    )
    assert anonymous.status_code in {302, 303}


@pytest.mark.asyncio
async def test_payables_and_receivables_pages_filter_titles_by_type_and_status() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Consulta Financeira",
        f"titulos-{suffix}@example.test",
        "senha-titulos-integracao-123",
        "Organização de Títulos",
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    account = await create_account(organization, user, f"Conta {suffix[:6]}", "bank", Decimal("0"))
    await create_titles(
        organization=organization,
        user=user,
        title_type="payable",
        description="Fornecedor vencido",
        counterparty_name="Fornecedor Teste",
        party_type="supplier",
        account_id=account.id,
        category_id=None,
        amount=Decimal("125.00"),
        issue_date=date.fromordinal(date.today().toordinal() - 2),
        due_date=date.fromordinal(date.today().toordinal() - 1),
        payment_method="boleto",
        payment_terms="cash",
        installment_count=1,
    )
    await create_titles(
        organization=organization,
        user=user,
        title_type="receivable",
        description="Cliente futuro",
        counterparty_name="Cliente Teste",
        party_type="customer",
        account_id=account.id,
        category_id=None,
        amount=Decimal("250.00"),
        issue_date=date.today(),
        due_date=date.fromordinal(date.today().toordinal() + 5),
        payment_method="pix",
        payment_terms="cash",
        installment_count=1,
    )
    csrf_token = "csrf-title-register-test"
    session = serializer.dumps({"uid": str(user.id), "org": str(organization.id)})
    headers = {
        "cookie": f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(csrf_token)}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    client = RobynTestClient(app)
    payables = await asyncio.to_thread(
        client.get, "/payables", headers=headers, query_params={"status": "overdue"}
    )
    receivables = await asyncio.to_thread(
        client.get, "/receivables", headers=headers, query_params={"status": "all"}
    )
    dashboard = await asyncio.to_thread(client.get, "/dashboard", headers=headers)
    accounts_page = await asyncio.to_thread(client.get, "/accounts", headers=headers)
    account_form = await asyncio.to_thread(
        client.post,
        "/accounts",
        body=urlencode(
            {
                "csrf": csrf_token,
                "name": f"Conta formulário {suffix[:6]}",
                "account_type": "bank",
                "bank_name": "Banco de Teste",
                "bank_code": "001",
                "branch_number": "0123-4",
                "account_number": "000123-5",
                "opening_balance": "0.00",
            }
        ),
        headers=headers,
    )
    invalid_account_form = await asyncio.to_thread(
        client.post,
        "/accounts",
        body=urlencode(
            {
                "csrf": csrf_token,
                "name": f"Conta incompleta {suffix[:6]}",
                "account_type": "bank",
                "opening_balance": "0.00",
            }
        ),
        headers=headers,
    )
    title_transactions = await asyncio.to_thread(client.get, "/transactions", headers=headers)
    new_payable = await asyncio.to_thread(client.get, "/payables", headers=headers)

    assert payables.status_code == 200
    assert "Contas a pagar" in payables.text
    assert "Fornecedor vencido" in payables.text
    assert "Vencido" in payables.text
    assert "Cliente futuro" not in payables.text
    assert receivables.status_code == 200
    assert "Cliente futuro" in receivables.text
    assert "Fornecedor vencido" not in receivables.text
    assert "Visão geral" in dashboard.text
    assert 'href="/customers"' in dashboard.text
    assert 'href="/suppliers"' in dashboard.text
    assert "Indicadores realizados" in dashboard.text
    assert "Fornecedor vencido" not in dashboard.text
    assert "Cliente futuro" not in dashboard.text
    assert title_transactions.status_code == 200
    assert "Fornecedor vencido" not in title_transactions.text
    assert "Cliente futuro" not in title_transactions.text
    assert "Microsoft 365" not in dashboard.text
    assert "dados fictícios para demonstração visual" not in dashboard.text
    assert accounts_page.status_code == 200
    assert account_form.status_code == 303
    assert invalid_account_form.status_code == 400
    form_account = await FinancialAccount.get(
        organization=organization, name=f"Conta formulário {suffix[:6]}"
    )
    assert form_account.bank_code == "001"
    assert form_account.branch_number == "0123-4"
    assert form_account.account_number == "000123-5"
    assert "Contas cadastradas" in accounts_page.text
    assert 'name="bank_name"' in accounts_page.text
    assert 'name="bank_code"' in accounts_page.text
    assert 'name="branch_number"' in accounts_page.text
    assert 'name="account_number"' in accounts_page.text
    assert "data-branch-mask" in accounts_page.text
    assert "data-drawer-open" in accounts_page.text
    assert "data-drawer-close>Cancelar</button>" in accounts_page.text
    assert "＋ Novo título" in new_payable.text
    assert "data-drawer-open" in new_payable.text
    assert "Data de emissão" in new_payable.text
    assert "Data de vencimento" in new_payable.text
    assert "data-drawer-close>Cancelar</button>" in new_payable.text
    assert 'role="dialog" aria-modal="true"' in new_payable.text
    assert "data-drawer-backdrop" in new_payable.text


@pytest.mark.asyncio
async def test_title_installments_and_partial_settlement_create_linked_movement() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Baixas de Títulos",
        f"baixas-{suffix}@example.test",
        "senha-baixas-integracao-123",
        "Organização de Baixas",
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    account = await create_account(organization, user, f"Conta {suffix[:6]}", "bank", Decimal("0"))
    before_movements = await FinancialTransaction.filter(organization=organization).count()

    titles = await create_titles(
        organization=organization,
        user=user,
        title_type="payable",
        description="Serviço parcelado",
        counterparty_name="Fornecedor Parcelado",
        party_type="supplier",
        account_id=account.id,
        category_id=None,
        amount=Decimal("100.00"),
        issue_date=date.today(),
        due_date=date.today(),
        payment_method="bank_transfer",
        payment_terms="installment",
        installment_count=3,
    )
    assert [title.original_amount for title in titles] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert await FinancialTransaction.filter(organization=organization).count() == before_movements

    first = titles[0]
    partial = await settle_title(
        organization=organization,
        user=user,
        title_id=first.id,
        operation_key=uuid4(),
        amount_applied=Decimal("10.00"),
        paid_on=date.today(),
        account_id=account.id,
        payment_method="pix",
        discount=Decimal("1.00"),
        fees=Decimal("0.50"),
        proof_reference="comprovante-001",
    )
    assert partial.net_amount == Decimal("9.50")
    persisted_title = await FinancialTitle.get(id=first.id)
    assert persisted_title.status == "partial"
    assert (await title_balances([first]))[first.id] == Decimal("23.33")
    assert partial.financial_transaction_id is not None
    assert (
        await FinancialTransaction.filter(organization=organization).count() == before_movements + 1
    )
    assert await TitleSettlement.filter(title=first).count() == 1


def test_money_parser_uses_decimal_and_rejects_zero_or_negative_movement() -> None:
    assert parse_money("1.234,56") == Decimal("1234.56")
    with pytest.raises(ValueError):
        parse_money("0,00")
    with pytest.raises(ValueError):
        parse_money("-5,00")


@pytest.mark.asyncio
async def test_journal_requires_balance_and_reversal_preserves_history() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Contábil", f"ledger-{suffix}@example.test", "senha-contabil-123", "Org Contábil"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    asset = await create_ledger_account(organization, user, "1.1.01", "Bancos", "asset")
    revenue = await create_ledger_account(organization, user, "4.1.01", "Receita", "revenue")
    unbalanced = await create_journal_entry(
        organization=organization,
        user=user,
        idempotency_key=uuid4(),
        entry_date=date.today(),
        description="Lançamento desbalanceado",
        lines=[
            (asset.id, Decimal("50.00"), Decimal("0.00")),
            (revenue.id, Decimal("0.00"), Decimal("40.00")),
        ],
    )
    with pytest.raises(ValueError, match="débitos iguais aos créditos"):
        await post_journal_entry(organization, unbalanced.id)
    still_draft = await JournalEntry.get(id=unbalanced.id)
    assert still_draft.status == "draft"
    await update_draft_journal_entry(
        organization=organization,
        entry_id=unbalanced.id,
        entry_date=date.today(),
        description="Rascunho corrigido",
        lines=[
            (asset.id, Decimal("50.00"), Decimal("0.00")),
            (revenue.id, Decimal("0.00"), Decimal("50.00")),
        ],
    )
    await post_journal_entry(organization, unbalanced.id)
    await post_journal_entry(organization, unbalanced.id)
    corrected = await JournalEntry.get(id=unbalanced.id)
    assert corrected.status == "posted"
    await reverse_journal_entry(organization, user, unbalanced.id)

    entry = await create_journal_entry(
        organization=organization,
        user=user,
        idempotency_key=uuid4(),
        entry_date=date.today(),
        description="Venda recebida",
        lines=[
            (asset.id, Decimal("75.00"), Decimal("0.00")),
            (revenue.id, Decimal("0.00"), Decimal("75.00")),
        ],
    )
    await post_journal_entry(organization, entry.id)
    balances = {
        account.code: balance for account, balance in await get_ledger_balances(organization)
    }
    assert balances["1.1.01"] == Decimal("75.00")
    assert balances["4.1.01"] == Decimal("75.00")
    reversal = await reverse_journal_entry(organization, user, entry.id)
    assert reversal.reversal_of_id == entry.id
    balances = {
        account.code: balance for account, balance in await get_ledger_balances(organization)
    }
    assert balances["1.1.01"] == Decimal("0.00")
    assert balances["4.1.01"] == Decimal("0.00")
    with pytest.raises(ValueError, match="já possui estorno"):
        await reverse_journal_entry(organization, user, entry.id)
