"""CRM: conversão de lead, funil, propostas e a ponte com contas a receber."""

import asyncio
import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from fincore.app import app
from fincore.application.counterparties import save_counterparty
from fincore.application.crm import (
    complete_activity,
    convert_lead,
    create_activity,
    create_deal,
    create_lead,
    ensure_default_stages,
    lose_deal,
    move_deal,
    reopen_deal,
    save_stage,
    set_stage_active,
    update_lead,
    win_deal,
)
from fincore.application.crm_proposals import (
    accept_proposal,
    create_proposal,
    reject_proposal,
    send_proposal,
)
from fincore.application.finance import create_account
from fincore.application.identity import register_user
from fincore.application.members import add_member
from fincore.application.queries import crm_stages, crm_summary
from fincore.application.titles import settle_title, title_balances
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    Counterparty,
    CrmActivity,
    CrmDeal,
    CrmDealEvent,
    CrmLead,
    CrmPipelineStage,
    CrmProposal,
    FinancialTitle,
    FinancialTransaction,
    JournalEntry,
    Membership,
    User,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
TODAY = date.today()
ITEMS = [("Consultoria", Decimal("10"), Decimal("100.00"))]


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _setup():
    user = await register_user("Pessoa CRM", f"crm-{uuid4().hex}@example.test", PASSWORD, "Org CRM")
    organization = await (await Membership.get(user=user)).organization
    account = await create_account(organization, user, "Banco", "bank", Decimal("0.00"))
    return organization, user, account


async def _deal(organization, user, **extra) -> CrmDeal:
    contact = await save_counterparty(
        organization=organization,
        person_type="company",
        name=f"Cliente {uuid4().hex[:8]}",
        is_customer=True,
    )
    return await create_deal(
        organization=organization,
        user=user,
        counterparty_id=contact.id,
        title="Contrato anual",
        amount=Decimal("1000.00"),
        **extra,
    )


async def _sent_proposal(organization, user, deal, items=ITEMS, **extra) -> CrmProposal:
    proposal = await create_proposal(
        organization=organization,
        user=user,
        deal_id=deal.id,
        items=items,
        valid_until=TODAY + timedelta(days=10),
        **extra,
    )
    return await send_proposal(organization=organization, proposal_id=proposal.id)


@pytest.mark.asyncio
async def test_default_stages_are_created_once() -> None:
    organization, _, _ = await _setup()
    await asyncio.gather(*(ensure_default_stages(organization) for _ in range(4)))
    stages = await crm_stages(organization)
    assert [stage.name for stage in stages] == [
        "Prospecção",
        "Qualificação",
        "Proposta",
        "Negociação",
    ]
    assert [stage.win_probability for stage in stages] == [10, 25, 50, 75]


@pytest.mark.asyncio
async def test_lead_qualification_requires_need_and_another_criterion() -> None:
    organization, user, _ = await _setup()
    lead = await create_lead(organization=organization, user=user, name="Maria")
    flags = {"has_need": True, "has_budget": False, "has_authority": False, "has_timeline": False}
    with pytest.raises(ValueError, match="confirme a necessidade"):
        await update_lead(organization=organization, lead_id=lead.id, status="qualified", **flags)
    lead = await update_lead(
        organization=organization,
        lead_id=lead.id,
        status="qualified",
        **{**flags, "has_budget": True},
    )
    assert lead.status == "qualified"
    with pytest.raises(ValueError, match="motivo da desqualificação"):
        await update_lead(
            organization=organization, lead_id=lead.id, status="disqualified", **flags
        )


@pytest.mark.asyncio
async def test_lead_conversion_creates_contact_and_deal_atomically() -> None:
    organization, user, _ = await _setup()
    lead = await create_lead(
        organization=organization,
        user=user,
        name="Maria Silva",
        company_name="Padaria Sol",
        email="maria@sol.test",
        estimated_value=Decimal("1500.00"),
    )
    lead, contact, deal = await convert_lead(
        organization=organization, user=user, lead_id=lead.id, person_type="company"
    )
    assert lead.status == "converted" and lead.converted_by_id == user.id
    assert contact.name == "Padaria Sol" and contact.is_customer and contact.email == lead.email
    assert deal is not None and deal.amount == Decimal("1500.00")
    assert deal.counterparty_id == contact.id
    assert (await deal.stage).name == "Prospecção"
    assert await CrmDealEvent.filter(deal=deal, action="created").count() == 1
    # Converter não cria título nem movimenta caixa.
    assert not await FinancialTitle.filter(organization=organization).exists()
    assert not await FinancialTransaction.filter(organization=organization).exists()
    with pytest.raises(ValueError, match="já foi convertido"):
        await convert_lead(organization=organization, user=user, lead_id=lead.id)
    assert await CrmDeal.filter(organization=organization).count() == 1


@pytest.mark.asyncio
async def test_conversion_refuses_duplicate_contact_and_links_existing_one() -> None:
    organization, user, _ = await _setup()
    existing = await save_counterparty(
        organization=organization,
        person_type="company",
        name="Padaria Sol",
        is_supplier=True,
        email="contato@sol.test",
    )
    lead = await create_lead(
        organization=organization, user=user, name="Maria", email="CONTATO@sol.test"
    )
    with pytest.raises(ValueError, match="Já existe um contato parecido"):
        await convert_lead(organization=organization, user=user, lead_id=lead.id)
    # A recusa não deixa nada pela metade.
    assert (await CrmLead.get(id=lead.id)).status == "new"
    assert await Counterparty.filter(organization=organization).count() == 1

    _, contact, deal = await convert_lead(
        organization=organization,
        user=user,
        lead_id=lead.id,
        counterparty_id=existing.id,
        create_deal=False,
    )
    assert contact.id == existing.id and deal is None
    refreshed = await Counterparty.get(id=existing.id)
    assert refreshed.is_customer and refreshed.is_supplier and refreshed.party_type == "other"


@pytest.mark.asyncio
async def test_failed_conversion_rolls_back_the_new_contact() -> None:
    organization, user, _ = await _setup()
    lead = await create_lead(organization=organization, user=user, name="Lead sem etapa")
    with pytest.raises(ValueError, match="etapa ativa"):
        await convert_lead(organization=organization, user=user, lead_id=lead.id, stage_id=uuid4())
    assert not await Counterparty.filter(organization=organization).exists()
    assert (await CrmLead.get(id=lead.id)).status == "new"


@pytest.mark.asyncio
async def test_deal_moves_through_the_funnel_with_an_audit_trail() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)
    stages = await crm_stages(organization)
    deal = await move_deal(
        organization=organization, user=user, deal_id=deal.id, stage_id=stages[2].id
    )
    assert deal.stage_id == stages[2].id
    event = await CrmDealEvent.get(deal=deal, action="stage_changed")
    assert (event.from_stage_id, event.to_stage_id) == (stages[0].id, stages[2].id)
    assert event.actor_id == user.id

    with pytest.raises(ValueError, match="motivo da perda"):
        await lose_deal(organization=organization, user=user, deal_id=deal.id, reason=" ")
    deal = await lose_deal(organization=organization, user=user, deal_id=deal.id, reason="Preço")
    assert deal.status == "lost" and deal.closed_at is not None
    with pytest.raises(ValueError, match="em aberto"):
        await move_deal(
            organization=organization, user=user, deal_id=deal.id, stage_id=stages[1].id
        )
    with pytest.raises(ValueError, match="em aberto"):
        await win_deal(organization=organization, user=user, deal_id=deal.id)
    deal = await reopen_deal(organization=organization, user=user, deal_id=deal.id)
    assert deal.status == "open" and deal.closed_at is None and deal.lost_reason is None
    actions = (
        await CrmDealEvent.filter(deal=deal)
        .order_by("occurred_at")
        .values_list("action", flat=True)
    )
    assert actions == ["created", "stage_changed", "lost", "reopened"]


@pytest.mark.asyncio
async def test_stage_with_open_deals_cannot_be_deactivated() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)
    with pytest.raises(ValueError, match="Mova as negociações"):
        await set_stage_active(organization, deal.stage_id, False)
    with pytest.raises(ValueError, match="Já existe uma etapa"):
        await save_stage(organization=organization, name="Proposta", win_probability=40)
    with pytest.raises(ValueError, match="entre 0 e 100"):
        await save_stage(organization=organization, name="Nova", win_probability=120)
    other = await _setup()
    with pytest.raises(ValueError, match="Etapa não encontrada"):
        await set_stage_active(other[0], deal.stage_id, False)


@pytest.mark.asyncio
async def test_database_rejects_invalid_crm_rows() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)
    with pytest.raises(IntegrityError):
        await CrmDeal.filter(id=deal.id).update(status="lost")  # perdida sem motivo/data
    with pytest.raises(IntegrityError):
        await CrmPipelineStage.filter(id=deal.stage_id).update(win_probability=150)
    with pytest.raises(IntegrityError):
        await CrmActivity.create(
            organization=organization, created_by=user, activity_type="call", subject="Sem vínculo"
        )


@pytest.mark.asyncio
async def test_activities_are_linked_and_completed_once() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)
    other_org, _, _ = await _setup()
    with pytest.raises(ValueError, match="não foi encontrado"):
        await create_activity(
            organization=other_org,
            user=user,
            activity_type="call",
            subject="Invasão",
            deal_id=deal.id,
        )
    note = await create_activity(
        organization=organization,
        user=user,
        activity_type="note",
        subject="Cliente pediu desconto",
        deal_id=deal.id,
    )
    assert note.done_at is not None
    task = await create_activity(
        organization=organization,
        user=user,
        activity_type="task",
        subject="Enviar proposta",
        due_date=TODAY - timedelta(days=1),
        deal_id=deal.id,
    )
    done = await complete_activity(organization=organization, user=user, activity_id=task.id)
    again = await complete_activity(organization=organization, user=user, activity_id=task.id)
    assert done.done_at == again.done_at and done.done_by_id == user.id


@pytest.mark.asyncio
async def test_accepting_a_proposal_creates_receivables_without_moving_cash() -> None:
    organization, user, account = await _setup()
    deal = await _deal(organization, user)
    proposal = await create_proposal(
        organization=organization,
        user=user,
        deal_id=deal.id,
        items=[
            ("Consultoria", Decimal("1.5"), Decimal("333.33")),
            ("Entrega", Decimal("1"), Decimal("50.05")),
        ],
        discount=Decimal("50.05"),
        valid_until=TODAY + timedelta(days=10),
    )
    assert proposal.number == 1 and proposal.status == "draft"
    accept = {
        "organization": organization,
        "user": user,
        "proposal_id": proposal.id,
        "account_id": account.id,
        "payment_method": "pix",
        "first_due_date": TODAY + timedelta(days=30),
        "installment_count": 3,
    }
    with pytest.raises(ValueError, match="Envie a proposta"):
        await accept_proposal(**accept)
    await send_proposal(organization=organization, proposal_id=proposal.id)

    proposal, titles = await accept_proposal(**accept)
    assert proposal.status == "accepted" and proposal.decided_by_id == user.id
    assert [title.original_amount for title in titles] == [
        Decimal("166.67"),
        Decimal("166.67"),
        Decimal("166.66"),
    ]
    assert sum(title.original_amount for title in titles) == Decimal("500.00")
    assert {title.title_type for title in titles} == {"receivable"}
    assert {title.counterparty_id for title in titles} == {deal.counterparty_id}
    assert proposal.first_title_id == titles[0].id
    deal = await CrmDeal.get(id=deal.id)
    assert deal.status == "won" and deal.amount == Decimal("500.00")
    # Título não é transação: nada no extrato nem no diário até haver baixa.
    assert not await FinancialTransaction.filter(organization=organization).exists()
    assert not await JournalEntry.filter(organization=organization).exists()

    # Reenvio do aceite devolve os mesmos títulos.
    _, replay = await accept_proposal(**accept)
    assert [title.id for title in replay] == [title.id for title in titles]
    assert await FinancialTitle.filter(organization=organization).count() == 3
    with pytest.raises(ValueError, match="proposta aceita"):
        await reopen_deal(organization=organization, user=user, deal_id=deal.id)

    # A baixa do título segue o fluxo financeiro normal e só então movimenta caixa.
    await settle_title(
        organization=organization,
        user=user,
        title_id=titles[0].id,
        operation_key=uuid4(),
        amount_applied=Decimal("166.67"),
        paid_on=TODAY,
        account_id=account.id,
        payment_method="pix",
    )
    assert await FinancialTransaction.filter(organization=organization).count() == 1
    balances = await title_balances(titles)
    assert balances[titles[0].id] == Decimal("0.00")


@pytest.mark.asyncio
async def test_concurrent_accepts_generate_the_receivables_once() -> None:
    organization, user, account = await _setup()
    proposal = await _sent_proposal(organization, user, await _deal(organization, user))

    async def accept():
        return await accept_proposal(
            organization=organization,
            user=user,
            proposal_id=proposal.id,
            account_id=account.id,
            payment_method="boleto",
            first_due_date=TODAY + timedelta(days=5),
            installment_count=2,
        )

    results = await asyncio.gather(*(accept() for _ in range(5)))
    assert len({tuple(title.id for title in titles) for _, titles in results}) == 1
    assert await FinancialTitle.filter(organization=organization).count() == 2
    assert await CrmDealEvent.filter(organization=organization, action="won").count() == 1


@pytest.mark.asyncio
async def test_failed_accept_leaves_proposal_and_deal_untouched() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)
    proposal = await _sent_proposal(organization, user, deal)
    _, _, foreign_account = await _setup()
    with pytest.raises(ValueError, match="conta financeira"):
        await accept_proposal(
            organization=organization,
            user=user,
            proposal_id=proposal.id,
            account_id=foreign_account.id,
            payment_method="pix",
            first_due_date=TODAY,
        )
    assert (await CrmProposal.get(id=proposal.id)).status == "sent"
    assert (await CrmDeal.get(id=deal.id)).status == "open"
    assert not await FinancialTitle.filter(organization=organization).exists()


@pytest.mark.asyncio
async def test_rejected_or_expired_proposals_do_not_generate_titles() -> None:
    organization, user, account = await _setup()
    deal = await _deal(organization, user)
    rejected = await _sent_proposal(organization, user, deal)
    await reject_proposal(
        organization=organization, user=user, proposal_id=rejected.id, reason="Sem verba"
    )
    expired = await _sent_proposal(organization, user, deal)
    assert (rejected.number, expired.number) == (1, 2)
    common = {
        "organization": organization,
        "user": user,
        "account_id": account.id,
        "payment_method": "pix",
        "first_due_date": TODAY,
    }
    with pytest.raises(ValueError, match="recusada"):
        await accept_proposal(proposal_id=rejected.id, **common)
    with pytest.raises(ValueError, match="validade"):
        await accept_proposal(proposal_id=expired.id, today=TODAY + timedelta(days=11), **common)
    with pytest.raises(ValueError, match="passado"):
        await create_proposal(
            organization=organization,
            user=user,
            deal_id=deal.id,
            items=ITEMS,
            valid_until=TODAY - timedelta(days=1),
        )
    assert not await FinancialTitle.filter(organization=organization).exists()


@pytest.mark.asyncio
async def test_proposal_numbers_are_sequential_under_concurrency() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)

    async def create():
        return await create_proposal(
            organization=organization,
            user=user,
            deal_id=deal.id,
            items=ITEMS,
            valid_until=TODAY + timedelta(days=5),
        )

    proposals = await asyncio.gather(*(create() for _ in range(6)))
    assert sorted(item.number for item in proposals) == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_summary_matches_reference_values() -> None:
    organization, user, account = await _setup()
    open_deal = await _deal(organization, user)
    stages = await crm_stages(organization)
    await move_deal(
        organization=organization, user=user, deal_id=open_deal.id, stage_id=stages[2].id
    )  # Proposta, 50%
    await _deal(organization, user)  # Prospecção, 10%
    lost = await _deal(organization, user)
    await lose_deal(organization=organization, user=user, deal_id=lost.id, reason="Prazo")
    won = await _deal(organization, user)
    await accept_proposal(
        organization=organization,
        user=user,
        proposal_id=(await _sent_proposal(organization, user, won)).id,
        account_id=account.id,
        payment_method="pix",
        first_due_date=TODAY,
    )
    stalled = await _deal(organization, user)
    await CrmDeal.filter(id=stalled.id).update(
        stage_changed_at=stalled.stage_changed_at - timedelta(days=20)
    )
    lead = await create_lead(organization=organization, user=user, name="Lead A")
    await create_lead(organization=organization, user=user, name="Lead B")
    await convert_lead(organization=organization, user=user, lead_id=lead.id, create_deal=False)
    await create_activity(
        organization=organization,
        user=user,
        activity_type="task",
        subject="Atrasada",
        due_date=TODAY - timedelta(days=2),
        deal_id=open_deal.id,
    )

    summary = await crm_summary(organization)
    assert summary.open_count == 3 and summary.open_total == Decimal("3000.00")
    # 1000 × 50% + 2 × 1000 × 10%
    assert summary.forecast == Decimal("700.00")
    assert (summary.won_count, summary.lost_count) == (1, 1)
    assert summary.win_rate == Decimal("50.0")
    assert summary.won_total == Decimal("1000.00")
    assert summary.average_cycle_days == Decimal("0.0")
    assert summary.stalled_count == 1 and summary.stalled[0].id == stalled.id
    assert summary.leads_total == 2 and summary.lead_conversion == Decimal("50.0")
    assert (summary.pending_activities, summary.overdue_activities) == (1, 1)
    # Outra organização não entra nos indicadores.
    other, _, _ = await _setup()
    assert (await crm_summary(other)).open_count == 0


async def _client(user: User, organization):
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(organization.id), "sv": user.session_version}
    )
    headers = {
        "cookie": f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}",
        "content-type": "application/x-www-form-urlencoded",
    }
    client = RobynTestClient(app)

    async def get(path: str, **query: str):
        return await asyncio.to_thread(client.get, path, headers=headers, query_params=query)

    async def post(path: str, body: str = ""):
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    return get, post


@pytest.mark.asyncio
async def test_crm_pages_render_and_run_the_full_sales_flow() -> None:
    organization, user, account = await _setup()
    get, post = await _client(user, organization)
    for path in ("/crm", "/crm/leads", "/crm/deals", "/crm/activities", "/crm/stages"):
        assert (await get(path)).status_code == 200, path

    response = await post("/crm/leads", "name=&source=referral")
    assert response.status_code == 400 and "Informe o nome do lead." in response.text
    response = await post("/crm/leads", "name=Maria+Souza&source=referral&estimated_value=1.500,00")
    assert response.status_code == 303
    lead = await CrmLead.get(organization=organization)
    assert lead.estimated_value == Decimal("1500.00")

    response = await post(f"/crm/leads/{lead.id}/convert", "create_deal=true&deal_amount=2.000,00")
    assert response.status_code == 303
    deal = await CrmDeal.get(organization=organization)
    assert deal.amount == Decimal("2000.00")
    assert response.headers.get("location").startswith(f"/crm/deals/{deal.id}")

    valid_until = (TODAY + timedelta(days=7)).isoformat()
    response = await post(
        f"/crm/deals/{deal.id}/proposals",
        f"item_description_1=Servi%C3%A7o&item_quantity_1=2&item_price_1=1.000,00"
        f"&discount=100,00&valid_until={valid_until}",
    )
    assert response.status_code == 303
    proposal = await CrmProposal.get(organization=organization)
    assert (await post(f"/crm/proposals/{proposal.id}/send")).status_code == 303
    response = await post(
        f"/crm/proposals/{proposal.id}/accept",
        f"account_id={account.id}&payment_method=boleto&installment_count=2"
        f"&first_due_date={valid_until}",
    )
    assert response.status_code == 303
    titles = await FinancialTitle.filter(organization=organization).order_by("installment_number")
    assert [title.original_amount for title in titles] == [Decimal("950.00"), Decimal("950.00")]

    page = await get(f"/crm/proposals/{proposal.id}")
    assert "Títulos a receber gerados" in page.text
    board = await get("/crm/deals", status="won")
    assert "Maria Souza" in board.text
    assert "Proposta nº 1" in (await get("/receivables")).text


@pytest.mark.asyncio
async def test_page_shows_the_rule_that_blocked_the_action() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)
    get, post = await _client(user, organization)
    response = await post(f"/crm/deals/{deal.id}/lose", "lost_reason=")
    assert response.status_code == 400
    assert "Informe o motivo da perda" in response.text
    assert (await CrmDeal.get(id=deal.id)).status == "open"
    assert (await get(f"/crm/deals/{uuid4()}")).status_code == 404
    assert (await get("/crm/deals/nao-e-uuid")).status_code == 404


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_change_the_crm() -> None:
    organization, user, _ = await _setup()
    deal = await _deal(organization, user)
    email = f"leitura-crm-{uuid4().hex}@example.test"
    await add_member(organization, "Leitura CRM", email, PASSWORD, "viewer")
    viewer = await User.get(email=email)
    get, post = await _client(viewer, organization)
    assert (await get("/crm/deals")).status_code == 200
    assert (await get(f"/crm/deals/{deal.id}")).status_code == 200
    assert (await post("/crm/leads", "name=Bloqueado")).status_code == 403
    assert (await post(f"/crm/deals/{deal.id}/win")).status_code == 403
    assert not await CrmLead.filter(organization=organization).exists()
    assert (await CrmDeal.get(id=deal.id)).status == "open"


@pytest.mark.asyncio
async def test_crm_pages_require_login() -> None:
    client = RobynTestClient(app)
    response = await asyncio.to_thread(client.get, "/crm/deals")
    assert response.status_code == 303 and response.headers.get("location") == "/login"
    response = await asyncio.to_thread(client.get, "/api/v1/crm/deals")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_demo_seed_builds_a_consistent_crm_funnel() -> None:
    from fincore.application.demo_seed import _seed_crm
    from fincore.application.finance import create_category

    organization, user, account = await _setup()
    category = await create_category(organization, user, "Encomendas", "income", "analytic")
    await _seed_crm(organization, user, account, category, TODAY)

    summary = await crm_summary(organization)
    assert summary.leads_by_status == {"new": 1, "qualified": 1, "converted": 1}
    assert (summary.open_count, summary.won_count, summary.lost_count) == (2, 1, 1)
    titles = await FinancialTitle.filter(organization=organization).order_by("installment_number")
    # 4 × 720,00 + 4 × 45,00 − 60,00 = 3.000,00 em três parcelas.
    assert [title.original_amount for title in titles] == [Decimal("1000.00")] * 3
    assert not await FinancialTransaction.filter(organization=organization).exists()


@pytest.mark.asyncio
async def test_a_deal_cannot_be_billed_twice_by_two_accepted_proposals() -> None:
    organization, user, account = await _setup()
    deal = await _deal(organization, user)
    first = await _sent_proposal(organization, user, deal)
    second = await _sent_proposal(organization, user, deal)
    common = {
        "organization": organization,
        "user": user,
        "account_id": account.id,
        "payment_method": "pix",
        "first_due_date": TODAY,
    }
    await accept_proposal(proposal_id=first.id, **common)
    with pytest.raises(ValueError, match="já tem uma proposta aceita"):
        await accept_proposal(proposal_id=second.id, **common)
    assert (await CrmProposal.get(id=second.id)).status == "sent"
    assert await FinancialTitle.filter(organization=organization).count() == 1


@pytest.mark.asyncio
async def test_success_message_cannot_be_forged_through_the_url() -> None:
    organization, user, _ = await _setup()
    get, post = await _client(user, organization)
    forged = "Sessão expirada: acesse evil.test"
    for path in ("/crm/leads", "/crm/deals", "/crm/activities", "/crm/stages", "/contacts"):
        page = await get(path, message=forged)
        assert page.status_code == 200 and "evil.test" not in page.text, path
    assert "Lead cadastrado." in (await get("/crm/leads", message="lead_created")).text
    assert "Contato cadastrado." in (await get("/contacts", message="Contato cadastrado.")).text
    # NUL em formulário é descartado em vez de derrubar a requisição com erro 500.
    response = await post("/crm/leads", "name=Maria%00+Nula")
    assert response.status_code == 303
    assert (await CrmLead.get(organization=organization)).name == "Maria Nula"
