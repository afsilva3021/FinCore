import asyncio
import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError, OperationalError

from fincore.app import app
from fincore.application.finance import create_account, create_movement
from fincore.application.identity import register_user
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    FinancialAccount,
    FinancialTransaction,
    JournalEntryLine,
    Membership,
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


async def _setup():
    user = await register_user(
        "Pessoa Integridade",
        f"integridade-{uuid4().hex}@example.test",
        "Cavalo-Bateria-42",
        "Org Integridade",
    )
    membership = await Membership.get(user=user)
    organization = await membership.organization
    account = await create_account(organization, user, "Conta", "bank", Decimal("0.00"))
    return user, membership, organization, account


async def _movement(organization, user, account, amount: str, days_ago: int = 0):
    return await create_movement(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        account_id=account.id,
        category_id=None,
        transaction_type="income",
        description=f"Movimento {days_ago:03d}",
        amount=Decimal(amount),
        competence_date=date.today() - timedelta(days=days_ago),
        due_date=None,
        settled=True,
    )


@pytest.mark.asyncio
async def test_database_rejects_values_that_bypass_the_application() -> None:
    user, _, organization, account = await _setup()
    movement = await _movement(organization, user, account, "10.00")
    invalid_writes = [
        FinancialTransaction.filter(id=movement.id).update(amount=Decimal("-1.00")),
        FinancialTransaction.filter(id=movement.id).update(status="paid"),
        FinancialTransaction.filter(id=movement.id).update(transaction_type="gift"),
        FinancialAccount.filter(id=account.id).update(opening_balance=Decimal("-5.00")),
        Membership.filter(organization=organization).update(role="admin"),
    ]
    for write in invalid_writes:
        with pytest.raises((IntegrityError, OperationalError)):
            await write
    line = await JournalEntryLine.filter(journal_entry__organization=organization).first()
    assert line is not None
    with pytest.raises((IntegrityError, OperationalError)):
        await JournalEntryLine.filter(id=line.id).update(debit=Decimal("5.00"), credit=Decimal("5"))


@pytest.mark.asyncio
async def test_transactions_page_paginates_instead_of_hiding_older_records() -> None:
    user, membership, organization, account = await _setup()
    for days_ago in range(55):
        await _movement(organization, user, account, "1.00", days_ago)
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(organization.id), "sv": user.session_version}
    )
    headers = {"cookie": f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"}
    client = RobynTestClient(app)

    first = await asyncio.to_thread(client.get, "/transactions", headers=headers)
    assert "Movimento 000" in first.text and "Movimento 049" in first.text
    assert "Movimento 050" not in first.text
    assert 'href="?page=2"' in first.text

    second = await asyncio.to_thread(
        client.get, "/transactions", headers=headers, query_params={"page": "2"}
    )
    assert "Movimento 054" in second.text and "Movimento 050" in second.text
    assert "Movimento 049" not in second.text
    assert 'href="?page=1"' in second.text and 'href="?page=3"' not in second.text

    weird = await asyncio.to_thread(
        client.get, "/transactions", headers=headers, query_params={"page": "abc"}
    )
    assert weird.status_code == 200
