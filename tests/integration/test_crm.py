"""CRM: leads, conversão sem duplicar contato, funil, histórico, atividades e perfis."""

import asyncio
import json
import os
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from fincore.app import app
from fincore.application.counterparties import save_counterparty
from fincore.application.crm import (
    add_activity,
    complete_activity,
    convert_lead,
    create_default_pipeline,
    create_opportunity,
    discard_lead,
    move_opportunity,
    save_lead,
)
from fincore.application.finance import create_account
from fincore.application.identity import register_user
from fincore.application.members import add_member
from fincore.application.products import save_product
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    Counterparty,
    CrmActivity,
    Lead,
    Membership,
    Opportunity,
    OpportunityStageChange,
    PipelineStage,
    SalesOrder,
    UnitOfMeasure,
    Warehouse,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
TODAY = date.today()
FORM = "application/x-www-form-urlencoded"
CNPJ = "11.222.333/0001-81"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


class Crm:
    @classmethod
    async def create(cls):
        self = cls()
        self.user = await register_user(
            "Dona CRM", f"crm-{uuid4().hex}@example.test", PASSWORD, "Org CRM"
        )
        self.organization = await (await Membership.get(user=self.user)).organization
        self.stages = {
            stage.name: stage
            for stage in await PipelineStage.filter(organization=self.organization)
        }
        return self

    async def lead(self, **extra) -> Lead:
        values = {"name": f"Lead {uuid4().hex[:6]}", "company": "Empresa Lead", **extra}
        return await save_lead(organization=self.organization, user=self.user, **values)

    async def opportunity(self, **extra) -> Opportunity:
        values = {"title": "Negócio", "expected_value": D("1500"), **extra}
        return await create_opportunity(organization=self.organization, user=self.user, **values)

    async def move(self, opportunity, stage: str, **extra) -> Opportunity:
        return await move_opportunity(
            organization=self.organization,
            user=self.user,
            opportunity_id=opportunity.id,
            stage_id=self.stages[stage].id,
            **extra,
        )


async def test_new_organization_gets_the_default_pipeline_once() -> None:
    crm = await Crm.create()
    assert sorted(crm.stages) == [
        "Ganho",
        "Negociação",
        "Novo",
        "Perdido",
        "Proposta",
        "Qualificado",
    ]
    assert (crm.stages["Ganho"].kind, crm.stages["Perdido"].kind) == ("won", "lost")
    await create_default_pipeline(crm.organization)
    assert await PipelineStage.filter(organization=crm.organization).count() == 6


async def test_converting_a_lead_creates_a_customer_and_carries_its_opportunities() -> None:
    crm = await Crm.create()
    lead = await crm.lead(name="Marina Souza", email="marina@example.test", phone="11 99999-0000")
    opportunity = await crm.opportunity(lead_id=lead.id)
    assert opportunity.counterparty_id is None

    converted, customer, created = await convert_lead(
        organization=crm.organization, lead_id=lead.id, person_type="company", tax_id=CNPJ
    )
    assert created and converted.status == "converted" and converted.counterparty_id == customer.id
    assert (customer.name, customer.is_customer, customer.tax_id) == (
        "Empresa Lead",
        True,
        "11222333000181",
    )
    assert customer.email == "marina@example.test"
    assert (await Opportunity.get(id=opportunity.id)).counterparty_id == customer.id
    with pytest.raises(ValueError, match="já foi convertido"):
        await convert_lead(
            organization=crm.organization, lead_id=lead.id, person_type="company", tax_id=CNPJ
        )
    with pytest.raises(ValueError, match="já foi convertido"):
        await discard_lead(crm.organization, lead.id)


async def test_conversion_reuses_an_existing_contact_with_the_same_tax_id() -> None:
    crm = await Crm.create()
    supplier = await save_counterparty(
        organization=crm.organization,
        person_type="company",
        name="Já Cadastrada Ltda",
        tax_id=CNPJ,
        is_supplier=True,
    )
    lead = await crm.lead()

    _, customer, created = await convert_lead(
        organization=crm.organization, lead_id=lead.id, person_type="company", tax_id=CNPJ
    )
    assert not created and customer.id == supplier.id
    stored = await Counterparty.get(id=supplier.id)
    assert stored.is_customer and stored.is_supplier and stored.party_type == "other"
    assert await Counterparty.filter(organization=crm.organization).count() == 1

    # Sem CPF/CNPJ não há como deduplicar: cria um cliente novo com o nome do lead.
    person = await crm.lead(name="Pessoa Física", company="")
    _, individual, created = await convert_lead(
        organization=crm.organization, lead_id=person.id, person_type="individual"
    )
    assert created and individual.name == "Pessoa Física"
    with pytest.raises(ValueError, match="CNPJ"):
        await convert_lead(
            organization=crm.organization,
            lead_id=(await crm.lead()).id,
            person_type="company",
            tax_id="123",
        )


async def test_pipeline_moves_are_recorded_and_follow_the_rules() -> None:
    crm = await Crm.create()
    lead = await crm.lead()
    opportunity = await crm.opportunity(lead_id=lead.id)
    assert opportunity.stage_id == crm.stages["Novo"].id

    await crm.move(opportunity, "Proposta")
    with pytest.raises(ValueError, match="já está nesta etapa"):
        await crm.move(opportunity, "Proposta")
    with pytest.raises(ValueError, match="Converta o lead"):
        await crm.move(opportunity, "Ganho")
    with pytest.raises(ValueError, match="motivo da perda"):
        await crm.move(opportunity, "Perdido")

    lost = await crm.move(opportunity, "Perdido", lost_reason="Preço acima do orçamento")
    assert lost.lost_reason == "Preço acima do orçamento" and lost.closed_at is not None
    reopened = await crm.move(opportunity, "Negociação")
    assert reopened.lost_reason is None and reopened.closed_at is None

    await convert_lead(organization=crm.organization, lead_id=lead.id, person_type="individual")
    won = await crm.move(opportunity, "Ganho")
    assert won.closed_at is not None

    history = await OpportunityStageChange.filter(opportunity=opportunity).order_by("changed_at")
    names = {stage.id: name for name, stage in crm.stages.items()}
    assert [names[change.to_stage_id] for change in history] == [
        "Novo",
        "Proposta",
        "Perdido",
        "Negociação",
        "Ganho",
    ]
    assert history[0].from_stage_id is None and history[2].note == "Preço acima do orçamento"
    assert all(change.changed_by_id == crm.user.id for change in history)


async def test_opportunity_and_lead_validations() -> None:
    crm = await Crm.create()
    other = await Crm.create()
    lead = await crm.lead()
    foreign_lead = await other.lead()
    customer = await save_counterparty(
        organization=crm.organization, person_type="individual", name="Cliente", is_customer=True
    )

    with pytest.raises(ValueError, match="só um dos dois"):
        await crm.opportunity()
    with pytest.raises(ValueError, match="só um dos dois"):
        await crm.opportunity(lead_id=lead.id, counterparty_id=customer.id)
    with pytest.raises(ValueError, match="lead em aberto"):
        await crm.opportunity(lead_id=foreign_lead.id)
    with pytest.raises(ValueError, match="cliente ativo"):
        await crm.opportunity(counterparty_id=uuid4())
    with pytest.raises(ValueError, match="valor estimado"):
        await crm.opportunity(lead_id=lead.id, expected_value=D("-1"))
    with pytest.raises(ValueError, match="título"):
        await crm.opportunity(lead_id=lead.id, title=" ")
    assert await Opportunity.filter(organization=crm.organization).count() == 0

    direct = await crm.opportunity(counterparty_id=customer.id)
    assert direct.counterparty_id == customer.id and direct.lead_id is None
    with pytest.raises(ValueError, match="etapa do funil"):
        await move_opportunity(
            organization=crm.organization,
            user=crm.user,
            opportunity_id=direct.id,
            stage_id=other.stages["Proposta"].id,
        )
    with pytest.raises(ValueError, match="não encontrada"):
        await other.move(direct, "Proposta")

    with pytest.raises(ValueError, match="nome"):
        await crm.lead(name=" ")
    with pytest.raises(ValueError, match="e-mail"):
        await crm.lead(email="sem-arroba")
    discarded = await discard_lead(crm.organization, lead.id)
    assert discarded.status == "discarded"
    with pytest.raises(ValueError, match="lead em aberto"):
        await crm.opportunity(lead_id=lead.id)
    with pytest.raises(ValueError, match="não encontrado"):
        await discard_lead(other.organization, lead.id)


async def test_activities_belong_to_a_lead_or_an_opportunity() -> None:
    crm = await Crm.create()
    other = await Crm.create()
    lead = await crm.lead()
    opportunity = await crm.opportunity(lead_id=lead.id)
    base = {"organization": crm.organization, "user": crm.user}

    task = await add_activity(
        **base,
        kind="call",
        subject="Ligar para confirmar",
        due_on=TODAY + timedelta(days=2),
        opportunity_id=opportunity.id,
    )
    note = await add_activity(
        **base, kind="note", subject="Prefere contato à tarde", lead_id=lead.id
    )
    assert task.done_at is None and note.done_at is not None
    done = await complete_activity(crm.organization, task.id)
    assert done.done_at is not None
    assert (await complete_activity(crm.organization, task.id)).done_at == done.done_at

    for invalid, message in (
        ({"kind": "email", "subject": "x", "lead_id": lead.id}, "tipo de atividade"),
        ({"kind": "task", "subject": " ", "lead_id": lead.id}, "assunto"),
        ({"kind": "task", "subject": "x"}, "lead ou a uma oportunidade"),
        (
            {"kind": "task", "subject": "x", "lead_id": lead.id, "opportunity_id": opportunity.id},
            "lead ou a uma oportunidade",
        ),
        ({"kind": "task", "subject": "x", "opportunity_id": uuid4()}, "não encontrada"),
    ):
        with pytest.raises(ValueError, match=message):
            await add_activity(**base, **invalid)
    with pytest.raises(ValueError, match="não encontrada"):
        await complete_activity(other.organization, task.id)
    assert await CrmActivity.filter(organization=crm.organization).count() == 2

    connection = Tortoise.get_connection("default")
    with pytest.raises(IntegrityError):
        await connection.execute_query(
            "UPDATE crmactivity SET lead_id = NULL WHERE id = $1", [note.id]
        )
    with pytest.raises(IntegrityError):
        await connection.execute_query(
            "UPDATE lead SET status = 'converted' WHERE id = $1", [lead.id]
        )


# --- Páginas, API e perfis ------------------------------------------------------------


async def _headers(user) -> tuple[dict[str, str], str]:
    membership = await Membership.get(user=user)
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(membership.organization_id), "sv": user.session_version}
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    return {"cookie": cookie, "content-type": FORM}, token


async def test_crm_pages_from_lead_to_won_opportunity_with_quote() -> None:
    crm = await Crm.create()
    headers, token = await _headers(crm.user)
    client = RobynTestClient(app)

    async def post(path: str, body: str = ""):
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    async def get(path: str, **params):
        return await asyncio.to_thread(client.get, path, headers=headers, query_params=params)

    board = await get("/crm")
    assert board.status_code == 200
    for stage in ("Novo", "Qualificado", "Proposta", "Negociação", "Ganho", "Perdido"):
        assert stage in board.text
    assert "Nenhuma oportunidade" in board.text

    invalid = await post("/crm/leads", "name=&company=Padaria+Vizinha")
    assert invalid.status_code == 400 and "nome" in invalid.text
    assert (
        'value="Padaria Vizinha"' in invalid.text and "data-drawer-initially-open" in invalid.text
    )
    created = await post(
        "/crm/leads",
        "name=Carla+Dias&company=Padaria+Vizinha&email=carla@example.test&source=Indicação",
    )
    assert created.status_code == 303
    lead = await Lead.get(organization=crm.organization)
    leads = await get("/crm/leads", q="carla")
    assert "Carla Dias" in leads.text and "Converter" in leads.text

    refused = await post("/crm/opportunities", f"title=&lead_id={lead.id}")
    assert refused.status_code == 400 and "título" in refused.text
    made = await post(
        "/crm/opportunities",
        f"title=Fornecimento+semanal&lead_id={lead.id}&expected_value=2.400,00"
        f"&expected_close={TODAY + timedelta(days=20)}",
    )
    assert made.status_code == 303
    opportunity = await Opportunity.get(organization=crm.organization)
    assert opportunity.expected_value == D("2400.00")

    page = await get(f"/crm/opportunities/{opportunity.id}")
    assert "Fornecimento semanal" in page.text and "R$ 2.400,00" in page.text
    assert "lead ainda não convertido" in page.text and "Gerar orçamento" not in page.text
    won_stage = crm.stages["Ganho"].id
    blocked = await post(f"/crm/opportunities/{opportunity.id}/stage", f"stage_id={won_stage}")
    assert blocked.status_code == 400 and "Converta o lead" in blocked.text
    stage = f"stage_id={crm.stages['Proposta'].id}"
    assert (await post(f"/crm/opportunities/{opportunity.id}/stage", stage)).status_code == 303
    activity = f"kind=meeting&subject=Visita+à+loja&due_on={TODAY + timedelta(days=3)}"
    assert (
        await post(f"/crm/opportunities/{opportunity.id}/activities", activity)
    ).status_code == 303
    board = await get("/crm")
    assert "Fornecimento semanal" in board.text and "Visita à loja" in board.text
    task = await CrmActivity.get(organization=crm.organization)
    assert (await post(f"/crm/activities/{task.id}/done", "back=board")).status_code == 303
    assert "Visita à loja" not in (await get("/crm")).text

    converted = await post(f"/crm/leads/{lead.id}/convert", "person_type=company&tax_id=")
    assert converted.status_code == 303
    customer = await Counterparty.get(organization=crm.organization)
    page = await get(f"/crm/opportunities/{opportunity.id}")
    assert "Gerar orçamento" in page.text
    assert f"customer={customer.id}" in page.text and f"opportunity={opportunity.id}" in page.text

    # Orçamento criado a partir da oportunidade fica ligado a ela.
    bank = await create_account(crm.organization, crm.user, "Banco", "bank", D("0"))
    unit = await UnitOfMeasure.get(organization=crm.organization, code="UN")
    product = await save_product(
        organization=crm.organization,
        user=crm.user,
        sku=f"CRM-{uuid4().hex[:6]}",
        name="Pão",
        unit_id=unit.id,
        sale_price=D("10.00"),
    )
    warehouse = await Warehouse.get(organization=crm.organization, is_default=True)
    form_page = await get(
        "/sales", new="1", customer=str(customer.id), opportunity=str(opportunity.id)
    )
    assert f'name="opportunity_id" value="{opportunity.id}"' in form_page.text
    assert "data-drawer-initially-open" in form_page.text
    quote = await post(
        "/sales",
        f"operation_key={uuid4()}&opportunity_id={opportunity.id}&customer_id={customer.id}"
        f"&warehouse_id={warehouse.id}&account_id={bank.id}&order_date={TODAY}"
        f"&payment_method=pix&product_1={product.id}&quantity_1=5",
    )
    assert quote.status_code == 303
    order = await SalesOrder.get(organization=crm.organization)
    assert (await Opportunity.get(id=opportunity.id)).sales_order_id == order.id
    assert "Ver orçamento PV-00001" in (await get(f"/crm/opportunities/{opportunity.id}")).text

    assert (
        await post(f"/crm/opportunities/{opportunity.id}/stage", f"stage_id={won_stage}")
    ).status_code == 303
    board = await get("/crm")
    assert "100,0%" in board.text  # 1 ganha, 0 perdidas

    assert (await get(f"/crm/opportunities/{uuid4()}")).status_code == 404
    assert (await post(f"/crm/leads/{uuid4()}/discard")).status_code == 404
    api = await get("/api/v1/crm/opportunities")
    data = json.loads(api.text)
    assert data["total"] == 1 and data["items"][0]["stage"]["kind"] == "won"
    assert data["items"][0]["sales_order_id"] == str(order.id)
    assert (await asyncio.to_thread(client.get, "/api/v1/crm/opportunities")).status_code == 401


async def test_only_owner_and_sales_profiles_write_the_crm() -> None:
    crm = await Crm.create()
    client = RobynTestClient(app)
    for role, allowed in (
        ("sales", True),
        ("finance", False),
        ("purchasing", False),
        ("viewer", False),
    ):
        membership = await add_member(
            crm.organization, role, f"{role}-{uuid4().hex}@example.test", PASSWORD, role
        )
        headers, token = await _headers(await membership.user)
        name = f"Lead+{role}"
        response = await asyncio.to_thread(
            client.post, "/crm/leads", headers=headers, body=f"csrf={token}&name={name}"
        )
        assert (response.status_code == 303) == allowed, role
        assert (response.status_code == 403) != allowed, role
        page = await asyncio.to_thread(client.get, "/crm", headers=headers)
        assert page.status_code == 200 and ("is-readonly" in page.text) != allowed, role
    assert await Lead.filter(organization=crm.organization).count() == 1
