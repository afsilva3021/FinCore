"""API JSON do CRM: contrato, idempotência e isolamento."""

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
from fincore.application.counterparties import save_counterparty
from fincore.application.finance import create_account
from fincore.application.identity import register_user
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    CrmDeal,
    CrmLead,
    CrmProposal,
    FinancialTitle,
    FinancialTransaction,
    Membership,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

TODAY = date.today()


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _api_client():
    user = await register_user(
        "Pessoa API CRM", f"api-crm-{uuid4().hex}@example.test", "Cavalo-Bateria-42", "Org API CRM"
    )
    organization = await (await Membership.get(user=user)).organization
    account = await create_account(organization, user, "Banco", "bank", Decimal("0.00"))
    contact = await save_counterparty(
        organization=organization, person_type="company", name="Cliente API", is_customer=True
    )
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

    async def post(path: str, payload: dict | None = None, csrf: bool = True):
        sent = headers if csrf else {k: v for k, v in headers.items() if k != "x-csrf-token"}
        response = await asyncio.to_thread(
            client.post, path, headers=sent, body=json.dumps(payload or {})
        )
        return response.status_code, json.loads(response.text)

    async def get(path: str, **query: str):
        response = await asyncio.to_thread(client.get, path, headers=headers, query_params=query)
        return response.status_code, json.loads(response.text)

    return organization, account, contact, post, get


def _proposal_payload(**extra) -> dict:
    return {
        "operation_key": str(uuid4()),
        "items": [
            {"description": "Consultoria", "quantity": "1.5", "unit_price": "333.33"},
            {"description": "Entrega", "unit_price": "50.05"},
        ],
        "discount": "50.05",
        "valid_until": (TODAY + timedelta(days=10)).isoformat(),
        **extra,
    }


@pytest.mark.asyncio
async def test_lead_creation_is_idempotent_and_conversion_is_single_use() -> None:
    organization, _, _, post, get = await _api_client()
    payload = {
        "operation_key": str(uuid4()),
        "name": "Maria",
        "company_name": "Padaria Sol",
        "email": "maria@sol.test",
        "source": "referral",
        "estimated_value": "1500.00",
    }
    status, lead = await post("/api/v1/crm/leads", payload)
    assert status == 201 and lead["status"] == "new" and lead["estimated_value"] == "1500.00"
    status, replay = await post("/api/v1/crm/leads", payload)
    assert status == 201 and replay["id"] == lead["id"]
    assert await CrmLead.filter(organization=organization).count() == 1

    status, error = await post(
        "/api/v1/crm/leads", {**payload, "operation_key": str(uuid4())}, csrf=False
    )
    assert status == 403 and error["error"]["code"] == "csrf_failed"
    status, error = await post(
        "/api/v1/crm/leads", {"operation_key": str(uuid4()), "name": "X", "source": "fax"}
    )
    assert status == 400 and "origem" in error["error"]["message"]
    status, error = await post(
        "/api/v1/crm/leads",
        {"operation_key": str(uuid4()), "name": "X", "estimated_value": 1500.0},
    )
    assert status == 400 and "string decimal" in error["error"]["message"]

    status, converted = await post(
        f"/api/v1/crm/leads/{lead['id']}/convert", {"person_type": "company"}
    )
    assert status == 201 and converted["lead"]["status"] == "converted"
    assert converted["deal"]["amount"] == "1500.00" and converted["deal"]["status"] == "open"
    status, error = await post(f"/api/v1/crm/leads/{lead['id']}/convert", {})
    assert status == 409
    assert await CrmDeal.filter(organization=organization).count() == 1

    status, listing = await get("/api/v1/crm/leads", status="converted")
    assert status == 200 and listing["total"] == 1
    status, _ = await get("/api/v1/crm/leads", limit="0")
    assert status == 400


@pytest.mark.asyncio
async def test_deal_and_proposal_flow_generates_receivables_once() -> None:
    organization, account, contact, post, get = await _api_client()
    status, stages = await get("/api/v1/crm/stages")
    assert status == 200 and [item["win_probability"] for item in stages["items"]] == [
        10,
        25,
        50,
        75,
    ]
    deal_payload = {
        "operation_key": str(uuid4()),
        "counterparty_id": str(contact.id),
        "title": "Contrato anual",
        "amount": "1000.00",
    }
    status, deal = await post("/api/v1/crm/deals", deal_payload)
    assert status == 201 and deal["stage_id"] == stages["items"][0]["id"]
    _, replay = await post("/api/v1/crm/deals", deal_payload)
    assert replay["id"] == deal["id"]

    status, moved = await post(
        f"/api/v1/crm/deals/{deal['id']}/stage", {"stage_id": stages["items"][2]["id"]}
    )
    assert status == 200 and moved["stage_id"] == stages["items"][2]["id"]
    status, summary = await get("/api/v1/crm/summary")
    assert summary["deals"]["open_total"] == "1000.00"
    assert summary["deals"]["weighted_forecast"] == "500.00"

    proposal_payload = _proposal_payload()
    status, proposal = await post(f"/api/v1/crm/deals/{deal['id']}/proposals", proposal_payload)
    assert status == 201 and proposal["total"] == "500.00" and proposal["number"] == 1
    assert [item["total"] for item in proposal["items"]] == ["500.00", "50.05"]
    await post(f"/api/v1/crm/deals/{deal['id']}/proposals", proposal_payload)
    assert await CrmProposal.filter(organization=organization).count() == 1

    accept = {
        "operation_key": str(uuid4()),
        "account_id": str(account.id),
        "payment_method": "boleto",
        "first_due_date": (TODAY + timedelta(days=30)).isoformat(),
        "installments": 3,
    }
    status, error = await post(f"/api/v1/crm/proposals/{proposal['id']}/accept", accept)
    assert status == 409 and "Envie a proposta" in error["error"]["message"]
    status, sent = await post(f"/api/v1/crm/proposals/{proposal['id']}/send")
    assert status == 200 and sent["status"] == "sent"

    status, accepted = await post(f"/api/v1/crm/proposals/{proposal['id']}/accept", accept)
    assert status == 201 and accepted["status"] == "accepted"
    assert [title["original_amount"] for title in accepted["titles"]] == [
        "166.67",
        "166.67",
        "166.66",
    ]
    assert {title["type"] for title in accepted["titles"]} == {"receivable"}
    status, again = await post(f"/api/v1/crm/proposals/{proposal['id']}/accept", accept)
    assert [title["id"] for title in again["titles"]] == [
        title["id"] for title in accepted["titles"]
    ]
    assert await FinancialTitle.filter(organization=organization).count() == 3
    assert not await FinancialTransaction.filter(organization=organization).exists()

    status, detail = await get(f"/api/v1/crm/deals/{deal['id']}")
    assert detail["status"] == "won" and detail["amount"] == "500.00"
    status, error = await post(
        f"/api/v1/crm/deals/{deal['id']}/stage", {"stage_id": stages["items"][0]["id"]}
    )
    assert status == 409
    # Os títulos gerados aparecem na API financeira de títulos.
    status, titles = await get("/api/v1/titles", type="receivable")
    assert titles["total"] == 3


@pytest.mark.asyncio
async def test_accept_key_cannot_hijack_existing_titles() -> None:
    _, account, contact, post, _ = await _api_client()
    title_key = str(uuid4())
    status, _ = await post(
        "/api/v1/titles",
        {
            "operation_key": title_key,
            "type": "receivable",
            "description": "Título avulso",
            "counterparty_name": "Outro cliente",
            "account_id": str(account.id),
            "amount": "10.00",
            "issue_date": TODAY.isoformat(),
            "due_date": TODAY.isoformat(),
            "payment_method": "pix",
        },
    )
    assert status == 201
    _, deal = await post(
        "/api/v1/crm/deals",
        {"operation_key": str(uuid4()), "counterparty_id": str(contact.id), "title": "Venda"},
    )
    _, proposal = await post(f"/api/v1/crm/deals/{deal['id']}/proposals", _proposal_payload())
    await post(f"/api/v1/crm/proposals/{proposal['id']}/send")
    status, error = await post(
        f"/api/v1/crm/proposals/{proposal['id']}/accept",
        {
            "operation_key": title_key,
            "account_id": str(account.id),
            "payment_method": "pix",
            "first_due_date": TODAY.isoformat(),
        },
    )
    assert status == 409 and error["error"]["code"] == "idempotency_conflict"
    assert (await CrmProposal.get(id=proposal["id"])).status == "sent"


@pytest.mark.asyncio
async def test_crm_api_does_not_cross_organizations() -> None:
    _, _, contact_a, post_a, _ = await _api_client()
    _, account_b, contact_b, post_b, get_b = await _api_client()
    lead_payload = {"operation_key": str(uuid4()), "name": "Lead A"}
    _, lead_a = await post_a("/api/v1/crm/leads", lead_payload)
    deal_payload = {
        "operation_key": str(uuid4()),
        "counterparty_id": str(contact_a.id),
        "title": "Negociação A",
    }
    _, deal_a = await post_a("/api/v1/crm/deals", deal_payload)
    _, proposal_a = await post_a(f"/api/v1/crm/deals/{deal_a['id']}/proposals", _proposal_payload())

    assert (await get_b(f"/api/v1/crm/leads/{lead_a['id']}"))[0] == 404
    assert (await get_b(f"/api/v1/crm/deals/{deal_a['id']}"))[0] == 404
    assert (await get_b(f"/api/v1/crm/proposals/{proposal_a['id']}"))[0] == 404
    assert (await post_b(f"/api/v1/crm/deals/{deal_a['id']}/win"))[0] == 404
    # Chave de idempotência de outra organização é conflito, nunca vazamento.
    status, body = await post_b("/api/v1/crm/leads", lead_payload)
    assert status == 409 and "name" not in body
    status, body = await post_b(
        "/api/v1/crm/deals", {**deal_payload, "counterparty_id": str(contact_b.id)}
    )
    assert status == 409 and "title" not in body
    # Contato de A não pode ser usado em negociação de B.
    status, _ = await post_b(
        "/api/v1/crm/deals",
        {"operation_key": str(uuid4()), "counterparty_id": str(contact_a.id), "title": "X"},
    )
    assert status == 400
    assert (await get_b("/api/v1/crm/deals"))[1]["total"] == 0
    assert (await CrmDeal.get(id=deal_a["id"])).status == "open"
    assert account_b is not None


@pytest.mark.asyncio
async def test_activities_api() -> None:
    _, _, contact, post, get = await _api_client()
    status, error = await post("/api/v1/crm/activities", {"type": "call", "subject": "Sem vínculo"})
    assert status == 400 and "ligada" in error["error"]["message"]
    status, activity = await post(
        "/api/v1/crm/activities",
        {
            "type": "call",
            "subject": "Ligar",
            "counterparty_id": str(contact.id),
            "due_date": (TODAY - timedelta(days=1)).isoformat(),
        },
    )
    assert status == 201 and activity["done_at"] is None
    status, summary = await get("/api/v1/crm/summary")
    assert summary["activities"] == {"pending": 1, "overdue": 1}
    status, done = await post(f"/api/v1/crm/activities/{activity['id']}/complete")
    assert status == 200 and done["done_at"] is not None
    status, listing = await get("/api/v1/crm/activities", status="pending")
    assert listing["total"] == 0
    assert (await post(f"/api/v1/crm/activities/{uuid4()}/complete"))[0] == 404


@pytest.mark.asyncio
async def test_hostile_input_gets_a_controlled_error_instead_of_500() -> None:
    organization, _, contact, post, _ = await _api_client()
    deal = {"operation_key": str(uuid4()), "counterparty_id": str(contact.id), "title": "X"}
    # Decimal enorme: quantize() lançava InvalidOperation e a rota respondia 500.
    status, error = await post("/api/v1/crm/deals", {**deal, "amount": "9" * 40})
    assert status == 400 and "limite" in error["error"]["message"]
    status, _ = await post(
        "/api/v1/crm/leads",
        {"operation_key": str(uuid4()), "name": "X", "estimated_value": "9" * 400},
    )
    assert status == 400
    # NUL em texto é recusado pelo PostgreSQL; precisa parar antes do banco.
    status, error = await post(
        "/api/v1/crm/leads", {"operation_key": str(uuid4()), "name": "a\u0000b"}
    )
    assert status == 400 and "caracteres inválidos" in error["error"]["message"]
    status, created = await post("/api/v1/crm/deals", deal)
    assert status == 201
    status, _ = await post(
        f"/api/v1/crm/deals/{created['id']}/proposals",
        _proposal_payload(items=[{"description": "X", "quantity": "9" * 60, "unit_price": "1.00"}]),
    )
    assert status == 400
    status, _ = await post(
        f"/api/v1/crm/deals/{created['id']}/proposals", _proposal_payload(discount="9" * 40)
    )
    assert status == 400
    assert not await CrmLead.filter(organization=organization).exists()
    assert not await CrmProposal.filter(organization=organization).exists()
