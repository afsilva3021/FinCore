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
from fincore.application.finance import create_account, create_movement
from fincore.application.identity import register_user
from fincore.application.titles import create_titles
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
    ReconciliationMatch,
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
    return {
        "organization": organization,
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
