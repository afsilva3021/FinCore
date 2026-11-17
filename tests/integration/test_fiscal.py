"""Fiscal simulado: documento gerado do faturamento, numeração, cancelamento e avisos."""

import asyncio
import json
import os
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from test_sales import Seller
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from fincore.app import app
from fincore.application.accounting import get_ledger_balances
from fincore.application.fiscal import (
    cancel_fiscal_document,
    issue_fiscal_document,
    save_tax_profile,
    set_tax_profile_active,
)
from fincore.application.members import add_member
from fincore.application.sales import reverse_sales_invoice
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    FiscalDocument,
    FiscalDocumentItem,
    Membership,
    StockBalance,
    TaxProfile,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
TODAY = date.today()
FORM = "application/x-www-form-urlencoded"
REASON = "Cliente pediu correção dos dados"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _invoice(seller: Seller, **order):
    return await seller.invoice(await seller.order(**order))


async def _issue(seller: Seller, invoice, **extra) -> FiscalDocument:
    document, _ = await issue_fiscal_document(
        **{
            "organization": seller.organization,
            "user": seller.user,
            "sales_invoice_id": invoice.id,
            "operation_key": uuid4(),
            **extra,
        }
    )
    return document


async def test_document_copies_the_invoice_and_highlights_taxes() -> None:
    seller = await Seller.create()
    invoice = await _invoice(seller)  # 2 bolos (76,00) + 3 cafés com desconto (14,00) = 90,00
    profile = await save_tax_profile(
        organization=seller.organization,
        name="Venda no estado",
        cfop="5.102",
        icms_cst="00",
        icms_rate=D("18"),
        pis_rate=D("1.65"),
        cofins_rate=D("7.6"),
    )
    stock_before = await StockBalance.filter(organization=seller.organization).values(
        "quantity", "total_value"
    )
    ledger_before = await get_ledger_balances(seller.organization)

    document = await _issue(seller, invoice, tax_profile_id=profile.id)
    assert (document.number, document.series, document.status) == (1, "1", "issued")
    assert document.total_products == invoice.total == D("90.00")
    assert document.customer_id == seller.customer.id and document.issued_on == TODAY
    items = await FiscalDocumentItem.filter(document=document).order_by("position")
    assert [(item.description, item.cfop, item.unit, item.total) for item in items] == [
        ("Bolo", "5102", "UN", D("76.00")),
        ("Café", "5102", "UN", D("14.00")),
    ]
    assert [(item.icms_amount, item.pis_amount, item.cofins_amount) for item in items] == [
        (D("13.68"), D("1.25"), D("5.78")),
        (D("2.52"), D("0.23"), D("1.06")),
    ]
    assert (document.total_icms, document.total_pis, document.total_cofins) == (
        D("16.20"),
        D("1.48"),
        D("6.84"),
    )
    # É só um documento: estoque e contabilidade não mudam.
    assert (
        await StockBalance.filter(organization=seller.organization).values(
            "quantity", "total_value"
        )
        == stock_before
    )
    assert [(a.code, b) for a, b in await get_ledger_balances(seller.organization)] == [
        (a.code, b) for a, b in ledger_before
    ]


async def test_default_profile_is_created_when_the_organization_has_none() -> None:
    seller = await Seller.create()
    document = await _issue(seller, await _invoice(seller))
    profile = await TaxProfile.get(organization=seller.organization)
    assert (profile.cfop, profile.icms_cst, profile.icms_rate) == ("5102", "102", D("0.00"))
    assert document.tax_profile_id == profile.id and document.total_icms == D("0.00")
    await _issue(seller, await _invoice(seller))
    assert await TaxProfile.filter(organization=seller.organization).count() == 1


async def test_numbering_has_no_gaps_or_duplicates_under_concurrency() -> None:
    seller = await Seller.create()
    await seller.stock_in(seller.cake, "40", "14.00")
    invoices = [await _invoice(seller) for _ in range(8)]

    documents = await asyncio.gather(*(_issue(seller, invoice) for invoice in invoices))
    assert sorted(document.number for document in documents) == list(range(1, 9))

    # Uma emissão recusada não gasta número; um cancelamento também não libera o dele.
    with pytest.raises(ValueError, match="já tem um documento fiscal emitido"):
        await _issue(seller, invoices[0])
    await cancel_fiscal_document(
        organization=seller.organization, document_id=documents[0].id, reason=REASON
    )
    again = await _issue(seller, invoices[0])
    assert again.number == 9
    numbers = await FiscalDocument.filter(organization=seller.organization).values_list(
        "number", flat=True
    )
    assert sorted(numbers) == list(range(1, 10))


async def test_one_issued_document_per_invoice_and_idempotent_resend() -> None:
    seller = await Seller.create()
    invoice = await _invoice(seller)
    key = uuid4()
    first = await _issue(seller, invoice, operation_key=key)
    assert (await _issue(seller, invoice, operation_key=key)).id == first.id

    burst_invoice = await _invoice(seller)
    burst = await asyncio.gather(
        *(_issue(seller, burst_invoice) for _ in range(5)), return_exceptions=True
    )
    assert sum(isinstance(item, FiscalDocument) for item in burst) == 1
    assert await FiscalDocument.filter(sales_invoice=burst_invoice).count() == 1

    other = await Seller.create()
    with pytest.raises(ValueError, match="Chave de operação"):
        await _issue(other, await _invoice(other), operation_key=key)
    with pytest.raises(ValueError, match="não encontrado"):
        await _issue(other, invoice)
    with pytest.raises(IntegrityError):
        await FiscalDocument.create(
            organization=seller.organization,
            created_by=seller.user,
            number=99,
            sales_invoice=invoice,
            tax_profile_id=first.tax_profile_id,
            customer=seller.customer,
            issued_on=TODAY,
            total_products=D("1.00"),
            operation_key=uuid4(),
        )


async def test_cancel_rules_and_link_with_the_invoice_reversal() -> None:
    seller = await Seller.create()
    invoice = await _invoice(seller)
    document = await _issue(seller, invoice)

    with pytest.raises(ValueError, match="Cancele o documento fiscal simulado"):
        await reverse_sales_invoice(
            organization=seller.organization, user=seller.user, invoice_id=invoice.id
        )
    with pytest.raises(ValueError, match="15 caracteres"):
        await cancel_fiscal_document(
            organization=seller.organization, document_id=document.id, reason="erro"
        )
    cancelled = await cancel_fiscal_document(
        organization=seller.organization, document_id=document.id, reason=REASON
    )
    assert cancelled.status == "cancelled" and cancelled.cancel_reason == REASON
    assert cancelled.cancelled_at is not None
    with pytest.raises(ValueError, match="já foi cancelado"):
        await cancel_fiscal_document(
            organization=seller.organization, document_id=document.id, reason=REASON
        )

    await reverse_sales_invoice(
        organization=seller.organization, user=seller.user, invoice_id=invoice.id
    )
    with pytest.raises(ValueError, match="faturamento efetivado"):
        await _issue(seller, invoice)


async def test_tax_profile_validations() -> None:
    seller = await Seller.create()
    other = await Seller.create()
    base = {"organization": seller.organization, "name": "Perfil", "cfop": "5102", "icms_cst": "00"}
    profile = await save_tax_profile(**base)
    for invalid, message in (
        ({}, "Já existe"),
        ({"name": "Outro", "cfop": "1102"}, "CFOP"),
        ({"name": "Outro", "icms_cst": "X"}, "CST"),
        ({"name": "Outro", "icms_rate": D("101")}, "ICMS"),
        ({"name": "Outro", "pis_rate": D("1.655")}, "PIS"),
        ({"name": " "}, "nome"),
    ):
        with pytest.raises(ValueError, match=message):
            await save_tax_profile(**{**base, **invalid})
    await set_tax_profile_active(seller.organization, profile.id, False)
    with pytest.raises(ValueError, match="perfil tributário ativo"):
        await _issue(seller, await _invoice(seller), tax_profile_id=profile.id)
    with pytest.raises(ValueError, match="não encontrado"):
        await set_tax_profile_active(other.organization, profile.id, True)
    foreign = await save_tax_profile(**{**base, "organization": other.organization})
    with pytest.raises(ValueError, match="perfil tributário ativo"):
        await _issue(seller, await _invoice(seller), tax_profile_id=foreign.id)


async def _headers(user) -> tuple[dict[str, str], str]:
    membership = await Membership.get(user=user)
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(membership.organization_id), "sv": user.session_version}
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    return {"cookie": cookie, "content-type": FORM}, token


async def test_fiscal_pages_always_say_simulation() -> None:
    seller = await Seller.create()
    invoice = await _invoice(seller)
    headers, token = await _headers(seller.user)
    client = RobynTestClient(app)

    async def post(path: str, body: str = ""):
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    async def get(path: str, **params):
        return await asyncio.to_thread(client.get, path, headers=headers, query_params=params)

    listing = await get("/fiscal")
    assert listing.status_code == 200 and "SIMULAÇÃO — SEM VALOR FISCAL" in listing.text
    assert "Nenhum documento simulado" in listing.text

    invalid = await post("/fiscal/tax-profiles", "name=Interestadual&cfop=1102&icms_cst=00")
    assert invalid.status_code == 400 and "CFOP" in invalid.text
    assert 'value="Interestadual"' in invalid.text
    profile = (
        "name=Venda+no+estado&cfop=5102&icms_cst=00&icms_rate=18&pis_rate=1,65&cofins_rate=7,6"
    )
    assert (await post("/fiscal/tax-profiles", profile)).status_code == 303
    assert (await TaxProfile.get(organization=seller.organization)).pis_rate == D("1.65")

    order_page = await get(f"/sales/{invoice.order_id}")
    assert "Gerar documento" in order_page.text and "Fiscal (simulação)" in order_page.text
    key = uuid4()
    body = f"sales_invoice_id={invoice.id}&operation_key={key}"
    assert (await post("/fiscal/documents", body)).status_code == 303
    assert (await post("/fiscal/documents", body)).status_code == 303
    document = await FiscalDocument.get(organization=seller.organization)

    page = await get(f"/fiscal/{document.id}")
    assert page.status_code == 200 and "SIMULAÇÃO — SEM VALOR FISCAL" in page.text
    assert "1/000001" in page.text and "R$ 90,00" in page.text and "R$ 16,20" in page.text
    assert "Emitido (simulado)" in page.text and "Não é nota fiscal" in page.text
    order_page = await get(f"/sales/{invoice.order_id}")
    assert "Doc. 1/000001" in order_page.text and "Gerar documento" not in order_page.text

    duplicate = await post(
        "/fiscal/documents", f"sales_invoice_id={invoice.id}&operation_key={uuid4()}"
    )
    assert duplicate.status_code == 400 and "já tem um documento fiscal emitido" in duplicate.text
    blocked = await post(f"/sales/invoices/{invoice.id}/reverse")
    assert blocked.status_code == 400 and "Cancele o documento fiscal simulado" in blocked.text
    short = await post(f"/fiscal/{document.id}/cancel", "reason=erro")
    assert (
        short.status_code == 400 and "15 caracteres" in short.text and 'value="erro"' in short.text
    )
    assert (
        await post(f"/fiscal/{document.id}/cancel", "reason=Cliente+pediu+correção")
    ).status_code == 303
    page = await get(f"/fiscal/{document.id}")
    assert "Cancelado" in page.text and "Cliente pediu correção" in page.text
    assert "Documento fiscal simulado cancelado" not in (await get("/fiscal", status="issued")).text

    assert (await get(f"/fiscal/{uuid4()}")).status_code == 404
    assert (await post(f"/fiscal/{uuid4()}/cancel", "reason=" + "x" * 20)).status_code == 404
    api = await get("/api/v1/fiscal/documents")
    data = json.loads(api.text)
    assert data["simulated"] is True and data["total"] == 1
    assert data["items"][0]["simulated"] is True and data["items"][0]["status"] == "cancelled"
    assert data["items"][0]["number"] == "1/000001" and "SIMULAÇÃO" in data["notice"]
    assert (await asyncio.to_thread(client.get, "/api/v1/fiscal/documents")).status_code == 401


async def test_fiscal_write_profiles() -> None:
    seller = await Seller.create()
    client = RobynTestClient(app)
    for role, allowed in (("sales", True), ("finance", True), ("stock", False), ("viewer", False)):
        membership = await add_member(
            seller.organization, role, f"{role}-{uuid4().hex}@example.test", PASSWORD, role
        )
        headers, token = await _headers(await membership.user)
        body = f"csrf={token}&name=Perfil+{role}&cfop=5102&icms_cst=00"
        response = await asyncio.to_thread(
            client.post, "/fiscal/tax-profiles", headers=headers, body=body
        )
        assert (response.status_code == 303) == allowed, role
        assert (response.status_code == 403) != allowed, role
    assert await TaxProfile.filter(organization=seller.organization).count() == 2
