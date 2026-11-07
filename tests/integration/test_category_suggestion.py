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
from fincore.application.finance import create_account, create_category, create_movement
from fincore.application.identity import register_user
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import Membership
from fincore.presentation.web import COOKIE_AUTH, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _organization_with_history():
    user = await register_user(
        "Pessoa IA", f"ia-{uuid4().hex}@example.test", "Cavalo-Bateria-42", "Org IA"
    )
    membership = await Membership.get(user=user)
    organization = await membership.organization
    account = await create_account(organization, user, "Banco", "bank", Decimal("0.00"))
    energy = await create_category(organization, user, "Energia", "expense", "analytic", None)
    for text in ("Conta de luz Enel", "Enel energia elétrica", "Luz escritório Enel"):
        await create_movement(
            organization=organization,
            user=user,
            operation_key=uuid4(),
            account_id=account.id,
            category_id=energy.id,
            transaction_type="expense",
            description=text,
            amount=Decimal("120.00"),
            competence_date=date.today(),
            due_date=None,
            settled=True,
        )
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(organization.id), "sv": user.session_version}
    )
    return energy, {"cookie": f"{COOKIE_AUTH}={session}"}


async def _ask(headers: dict, **query: str) -> dict:
    response = await asyncio.to_thread(
        RobynTestClient(app).get,
        "/api/v1/categories/suggestion",
        headers=headers,
        query_params=query,
    )
    assert response.status_code == 200
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_suggestion_learns_from_the_organization_history() -> None:
    energy, headers = await _organization_with_history()
    body = await _ask(headers, description="ENEL luz de abril", type="expense")
    assert body["suggestion"]["category_id"] == str(energy.id)
    assert body["suggestion"]["examples"] == 3
    assert (await _ask(headers, description="ENEL luz", type="income"))["suggestion"] is None


@pytest.mark.asyncio
async def test_another_organization_does_not_learn_from_this_history() -> None:
    first_energy, _ = await _organization_with_history()
    other_energy, other_headers = await _organization_with_history()
    suggestion = (await _ask(other_headers, description="Enel luz", type="expense"))["suggestion"]
    # Só os 3 lançamentos e a categoria da própria organização contam.
    assert suggestion["category_id"] == str(other_energy.id) != str(first_energy.id)
    assert suggestion["examples"] == 3
