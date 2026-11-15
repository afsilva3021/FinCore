"""Propostas comerciais do CRM e a ponte com o financeiro.

O aceite de uma proposta gera títulos a receber (`FinancialTitle`) para o contato da
negociação. Como em qualquer título, isso não movimenta caixa nem aparece no extrato: o
dinheiro só entra quando o título recebe baixa em Contas a receber.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.application.crm import mark_deal_won
from fincore.application.titles import create_titles
from fincore.domain.crm import (
    calculate_proposal,
    ensure_proposal_can_accept,
    ensure_proposal_can_reject,
    ensure_proposal_can_send,
)
from fincore.domain.money import quantize_money
from fincore.infrastructure.models import (
    CrmDeal,
    CrmProposal,
    CrmProposalItem,
    FinancialTitle,
    Organization,
    User,
)

ProposalItemInput = tuple[str, Decimal, Decimal]


async def create_proposal(
    *,
    organization: Organization,
    user: User,
    deal_id: UUID,
    items: list[ProposalItemInput],
    valid_until: date,
    discount: Decimal = Decimal("0.00"),
    notes: str = "",
    operation_key: UUID | None = None,
    today: date | None = None,
) -> CrmProposal:
    """Cria uma proposta em rascunho com numeração sequencial por organização."""
    if operation_key is not None:
        replay = await CrmProposal.get_or_none(operation_key=operation_key)
        if replay is not None:
            if replay.organization_id != organization.id:
                raise ValueError("A chave de operação já foi usada.")
            return replay
    today = today or date.today()
    totals = calculate_proposal(items, discount)
    if valid_until < today:
        raise ValueError("A validade da proposta não pode estar no passado.")
    cleaned_notes = notes.strip()
    if len(cleaned_notes) > 2000:
        raise ValueError("As observações devem ter no máximo 2000 caracteres.")
    try:
        async with in_transaction():
            # Bloqueia a organização para a numeração sequencial não repetir em concorrência.
            await Organization.filter(id=organization.id).select_for_update().first()
            deal = await CrmDeal.get_or_none(id=deal_id, organization=organization)
            if deal is None:
                raise ValueError("Negociação não encontrada nesta organização.")
            if deal.status == "lost":
                raise ValueError("Reabra a negociação perdida antes de criar uma proposta.")
            last = await CrmProposal.filter(organization=organization).order_by("-number").first()
            proposal = await CrmProposal.create(
                organization=organization,
                created_by=user,
                deal=deal,
                operation_key=operation_key,
                number=(last.number if last else 0) + 1,
                issue_date=today,
                valid_until=valid_until,
                discount=totals.discount,
                notes=cleaned_notes or None,
            )
            for position, line in enumerate(totals.lines, start=1):
                await CrmProposalItem.create(
                    proposal=proposal,
                    position=position,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    total=line.total,
                )
            return proposal
    except IntegrityError:
        replay = await CrmProposal.get_or_none(operation_key=operation_key)
        if replay is not None and replay.organization_id == organization.id:
            return replay
        raise ValueError("A chave de operação já foi usada.") from None


async def _proposal_for_update(organization: Organization, proposal_id: UUID) -> CrmProposal:
    proposal = (
        await CrmProposal.filter(id=proposal_id, organization=organization)
        .select_for_update()
        .first()
    )
    if proposal is None:
        raise ValueError("Proposta não encontrada nesta organização.")
    return proposal


async def proposal_total(proposal: CrmProposal) -> Decimal:
    """Total derivado dos itens menos o desconto; não existe campo de total editável."""
    totals = await CrmProposalItem.filter(proposal=proposal).values_list("total", flat=True)
    return quantize_money(sum(totals, Decimal("0.00")) - proposal.discount)


async def proposal_titles(proposal: CrmProposal) -> list[FinancialTitle]:
    """Títulos a receber gerados pelo aceite (todas as parcelas do grupo)."""
    if proposal.first_title_id is None:
        return []
    first = await FinancialTitle.get(id=proposal.first_title_id)
    if first.installment_group is None:
        return [first]
    return await FinancialTitle.filter(
        organization_id=proposal.organization_id, installment_group=first.installment_group
    ).order_by("installment_number")


async def send_proposal(
    *, organization: Organization, proposal_id: UUID, today: date | None = None
) -> CrmProposal:
    """Registra que a proposta foi apresentada ao cliente. Não envia e-mail nem mensagem."""
    async with in_transaction():
        proposal = await _proposal_for_update(organization, proposal_id)
        if proposal.status == "sent":
            return proposal
        ensure_proposal_can_send(proposal.status, proposal.valid_until, today or date.today())
        proposal.status = "sent"
        proposal.sent_at = datetime.now(UTC)
        await proposal.save(update_fields=["status", "sent_at"])
        return proposal


async def reject_proposal(
    *, organization: Organization, user: User, proposal_id: UUID, reason: str = ""
) -> CrmProposal:
    cleaned = reason.strip()
    if len(cleaned) > 240:
        raise ValueError("O motivo da recusa deve ter no máximo 240 caracteres.")
    async with in_transaction():
        proposal = await _proposal_for_update(organization, proposal_id)
        if proposal.status == "rejected":
            return proposal
        ensure_proposal_can_reject(proposal.status)
        proposal.status = "rejected"
        proposal.rejection_reason = cleaned or None
        proposal.decided_at = datetime.now(UTC)
        proposal.decided_by = user
        await proposal.save(
            update_fields=["status", "rejection_reason", "decided_at", "decided_by_id"]
        )
        return proposal


async def accept_proposal(
    *,
    organization: Organization,
    user: User,
    proposal_id: UUID,
    account_id: UUID,
    payment_method: str,
    first_due_date: date,
    installment_count: int = 1,
    category_id: UUID | None = None,
    operation_key: UUID | None = None,
    today: date | None = None,
) -> tuple[CrmProposal, list[FinancialTitle]]:
    """Registra o aceite, gera os títulos a receber e marca a negociação como ganha.

    Tudo numa única transação PostgreSQL. Reenviar o aceite de uma proposta já aceita
    devolve os mesmos títulos, sem duplicar.
    """
    today = today or date.today()
    async with in_transaction():
        proposal = await _proposal_for_update(organization, proposal_id)
        if proposal.status == "accepted":
            return proposal, await proposal_titles(proposal)
        ensure_proposal_can_accept(proposal.status, proposal.valid_until, today)
        deal = await CrmDeal.filter(id=proposal.deal_id).select_for_update().first()
        if deal.status == "lost":
            raise ValueError("Reabra a negociação perdida antes de aceitar a proposta.")
        if await CrmProposal.filter(deal=deal, status="accepted").exists():
            raise ValueError(
                "Esta negociação já tem uma proposta aceita; os títulos já foram gerados."
            )
        key = operation_key or uuid4()
        # A chave vira a chave de criação dos títulos; se já pertence a outros títulos,
        # reaproveitá-la ligaria a proposta a recebíveis que não são dela.
        if await FinancialTitle.filter(operation_key=key).exists():
            raise ValueError("A chave de operação já foi usada.")
        total = await proposal_total(proposal)
        titles = await create_titles(
            organization=organization,
            user=user,
            operation_key=key,
            counterparty_id=deal.counterparty_id,
            counterparty_name="",
            party_type="customer",
            title_type="receivable",
            description=f"Proposta nº {proposal.number}: {deal.title}"[:220],
            account_id=account_id,
            category_id=category_id,
            amount=total,
            issue_date=today,
            due_date=first_due_date,
            payment_method=payment_method,
            payment_terms="installment" if installment_count > 1 else "cash",
            installment_count=installment_count,
            document_number=str(proposal.number),
            document_type="Proposta",
            related_reference=f"Proposta nº {proposal.number} · negociação {deal.id}",
        )
        proposal.status = "accepted"
        proposal.decided_at = datetime.now(UTC)
        proposal.decided_by = user
        proposal.first_title = titles[0]
        await proposal.save(
            update_fields=["status", "decided_at", "decided_by_id", "first_title_id"]
        )
        # O valor fechado da negociação passa a ser o total aceito.
        deal.amount = total
        await deal.save(update_fields=["amount"])
        if deal.status == "open":
            await mark_deal_won(deal, organization, user, f"Proposta nº {proposal.number} aceita")
        return proposal, titles
