import asyncio
import os
from datetime import date
from decimal import Decimal
from html import escape
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise

from fincore.app import app
from fincore.application.finance import create_account
from fincore.application.identity import register_user
from fincore.application.titles import create_titles, settle_title
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import FinancialTitle, Membership
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

FORM = "application/x-www-form-urlencoded"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _receivable_setup():
    suffix = uuid4().hex
    user = await register_user(
        "Títulos Web", f"titulos-web-{suffix}@example.test", "senha-titulos-web-1", "Org Títulos"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    account = await create_account(organization, user, "Conta", "bank", Decimal("0"))
    (title,) = await create_titles(
        organization=organization,
        user=user,
        title_type="receivable",
        description="Venda única",
        counterparty_name="Cliente",
        party_type="customer",
        account_id=account.id,
        category_id=None,
        amount=Decimal("100.00"),
        issue_date=date.today(),
        due_date=date.today(),
        payment_method="pix",
        payment_terms="cash",
        installment_count=1,
    )
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(organization.id), "sv": user.session_version}
    )
    token = uuid4().hex
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    return organization, user, account, title, {"cookie": cookie, "content-type": FORM}, token


@pytest.mark.asyncio
async def test_settlement_page_shows_the_business_rule_that_failed() -> None:
    _, _, account, title, headers, token = await _receivable_setup()
    body = (
        f"csrf={token}&operation_key={uuid4()}&amount_applied=150,00"
        f"&paid_on={date.today().isoformat()}&account_id={account.id}&payment_method=pix"
    )
    client = RobynTestClient(app)
    response = await asyncio.to_thread(
        client.post, f"/titles/{title.id}/settlements", headers=headers, body=body
    )

    assert response.status_code == 400
    assert escape("O valor baixado não pode exceder o saldo em aberto.") in response.text
    assert "Contas a receber" in response.text


@pytest.mark.asyncio
async def test_cancel_refusal_is_reported_on_the_receivables_page() -> None:
    organization, user, account, title, headers, token = await _receivable_setup()
    await settle_title(
        organization=organization,
        user=user,
        title_id=title.id,
        operation_key=uuid4(),
        amount_applied=Decimal("10.00"),
        paid_on=date.today(),
        account_id=account.id,
        payment_method="pix",
    )
    client = RobynTestClient(app)
    response = await asyncio.to_thread(
        client.post, f"/titles/{title.id}/cancel", headers=headers, body=f"csrf={token}"
    )

    assert response.status_code == 400
    assert "Somente títulos em aberto sem baixas podem ser cancelados." in response.text
    assert "Contas a receber" in response.text
    assert (await FinancialTitle.get(id=title.id)).status != "cancelled"


@pytest.mark.asyncio
async def test_successful_cancel_redirects_to_the_title_register_of_its_type() -> None:
    _, _, _, title, headers, token = await _receivable_setup()
    client = RobynTestClient(app)
    response = await asyncio.to_thread(
        client.post, f"/titles/{title.id}/cancel", headers=headers, body=f"csrf={token}"
    )

    assert response.status_code == 303
    assert response.headers.get("location") == "/receivables"
