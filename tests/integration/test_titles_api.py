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

from fincore.app import app
from fincore.application.finance import create_account
from fincore.application.identity import register_user
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    FinancialTitle,
    FinancialTransaction,
    Membership,
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


async def _api_client():
    user = await register_user(
        "Pessoa API", f"api-titulos-{uuid4().hex}@example.test", "Cavalo-Bateria-42", "Org API"
    )
    membership = await Membership.get(user=user)
    organization = await membership.organization
    account = await create_account(organization, user, "Banco", "bank", Decimal("0.00"))
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(organization.id), "sv": user.session_version}
    )
    headers = {
        "cookie": f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}",
        "content-type": "application/json",
        "x-csrf-token": token,
    }
    client = RobynTestClient(app)

    async def post(path: str, payload: dict):
        response = await asyncio.to_thread(
            client.post, path, headers=headers, body=json.dumps(payload)
        )
        return response.status_code, json.loads(response.text)

    async def get(path: str, **query: str):
        response = await asyncio.to_thread(client.get, path, headers=headers, query_params=query)
        return response.status_code, json.loads(response.text)

    return organization, account, post, get


def _title_payload(account_id, **extra) -> dict:
    return {
        "operation_key": str(uuid4()),
        "type": "payable",
        "description": "Aluguel",
        "counterparty_name": "Imobiliária",
        "account_id": str(account_id),
        "amount": "100.00",
        "issue_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=10)).isoformat(),
        "payment_method": "boleto",
        **extra,
    }


@pytest.mark.asyncio
async def test_create_installments_is_idempotent_and_exact() -> None:
    organization, account, post, get = await _api_client()
    payload = _title_payload(account.id, installments=3)

    status, body = await post("/api/v1/titles", payload)
    assert status == 201
    assert [item["original_amount"] for item in body["items"]] == ["33.33", "33.33", "33.34"]
    replay_status, replay = await post("/api/v1/titles", payload)
    assert replay_status == 201
    assert [item["id"] for item in replay["items"]] == [item["id"] for item in body["items"]]
    assert await FinancialTitle.filter(organization=organization).count() == 3
    # Cadastrar título não movimenta caixa.
    assert not await FinancialTransaction.filter(organization=organization).exists()

    status, listing = await get("/api/v1/titles", type="payable", status="open")
    assert status == 200 and listing["total"] == 3


@pytest.mark.asyncio
async def test_settlement_through_api_updates_balance_once() -> None:
    _, account, post, get = await _api_client()
    _, created = await post("/api/v1/titles", _title_payload(account.id))
    title_id = created["items"][0]["id"]
    settlement = {
        "operation_key": str(uuid4()),
        "amount_applied": "40.00",
        "paid_on": date.today().isoformat(),
        "account_id": str(account.id),
        "payment_method": "pix",
        "fees": "1.50",
    }

    status, body = await post(f"/api/v1/titles/{title_id}/settlements", settlement)
    assert status == 201 and body["net_amount"] == "41.50"
    await post(f"/api/v1/titles/{title_id}/settlements", settlement)
    assert await TitleSettlement.filter(title_id=title_id).count() == 1

    status, detail = await get(f"/api/v1/titles/{title_id}")
    assert detail["status"] == "partial" and detail["open_balance"] == "60.00"
    assert len(detail["settlements"]) == 1

    too_much = {**settlement, "operation_key": str(uuid4()), "amount_applied": "61.00"}
    status, error = await post(f"/api/v1/titles/{title_id}/settlements", too_much)
    assert status == 400 and "saldo em aberto" in error["error"]["message"]

    status, error = await post(f"/api/v1/titles/{title_id}/cancel", {})
    assert status == 409


@pytest.mark.asyncio
async def test_operation_keys_cannot_reach_another_organization() -> None:
    _, account_a, post_a, _ = await _api_client()
    _, account_b, post_b, get_b = await _api_client()
    payload = _title_payload(account_a.id)
    _, created = await post_a("/api/v1/titles", payload)
    title_a = created["items"][0]["id"]

    status, _ = await get_b(f"/api/v1/titles/{title_a}")
    assert status == 404
    status, body = await post_b("/api/v1/titles", {**payload, "account_id": str(account_b.id)})
    assert status == 409
    settlement = {
        "operation_key": str(uuid4()),
        "amount_applied": "10.00",
        "paid_on": date.today().isoformat(),
        "account_id": str(account_a.id),
        "payment_method": "pix",
    }
    await post_a(f"/api/v1/titles/{title_a}/settlements", settlement)
    _, other = await post_b("/api/v1/titles", _title_payload(account_b.id))
    status, body = await post_b(
        f"/api/v1/titles/{other['items'][0]['id']}/settlements",
        {**settlement, "account_id": str(account_b.id)},
    )
    assert status == 409
    assert "amount_applied" not in body
