"""CRM: leads, oportunidades no funil, atividades e ligação com clientes e orçamentos."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from tortoise.transactions import in_transaction

from fincore.application.counterparties import normalize_tax_id, save_counterparty
from fincore.domain.crm import (
    ACTIVITY_KINDS,
    DEFAULT_STAGES,
    OPEN,
    clean_text,
    ensure_lead_open,
    ensure_stage_move,
)
from fincore.domain.money import MAX_AMOUNT, quantize_money
from fincore.infrastructure.models import (
    Counterparty,
    CrmActivity,
    Lead,
    Opportunity,
    OpportunityStageChange,
    Organization,
    PipelineStage,
    SalesOrder,
    User,
)


async def create_default_pipeline(organization: Organization) -> None:
    """Etapas padrão do funil de uma organização. Idempotente."""
    existing = set(
        await PipelineStage.filter(organization=organization).values_list("name", flat=True)
    )
    await PipelineStage.bulk_create(
        [
            PipelineStage(organization=organization, name=name, position=position, kind=kind)
            for name, position, kind in DEFAULT_STAGES
            if name not in existing
        ]
    )


# --- Leads ----------------------------------------------------------------------------


async def save_lead(
    *,
    organization: Organization,
    user: User,
    name: str,
    company: str = "",
    email: str = "",
    phone: str = "",
    source: str = "",
    notes: str = "",
    lead_id: UUID | None = None,
) -> Lead:
    values = {
        "name": clean_text(name, "O nome do lead", 160),
        "company": clean_text(company, "A empresa", 160, required=False),
        "email": clean_text(email, "O e-mail", 254, required=False),
        "phone": clean_text(phone, "O telefone", 40, required=False),
        "source": clean_text(source, "A origem", 60, required=False),
        "notes": notes.strip() or None,
    }
    if values["notes"] and len(values["notes"]) > 2000:
        raise ValueError("Observações devem ter no máximo 2000 caracteres.")
    if values["email"] and "@" not in values["email"]:
        raise ValueError("Informe um e-mail válido.")
    if lead_id is None:
        return await Lead.create(organization=organization, created_by=user, **values)
    lead = await Lead.get_or_none(id=lead_id, organization=organization)
    if lead is None:
        raise ValueError("Lead não encontrado nesta organização.")
    for key, value in values.items():
        setattr(lead, key, value)
    await lead.save()
    return lead


async def discard_lead(organization: Organization, lead_id: UUID) -> Lead:
    async with in_transaction():
        lead = await Lead.filter(id=lead_id, organization=organization).select_for_update().first()
        if lead is None:
            raise ValueError("Lead não encontrado nesta organização.")
        ensure_lead_open(lead.status)
        lead.status = "discarded"
        await lead.save(update_fields=["status"])
        return lead


async def convert_lead(
    *,
    organization: Organization,
    lead_id: UUID,
    person_type: str,
    tax_id: str = "",
) -> tuple[Lead, Counterparty, bool]:
    """Converte o lead em cliente. Devolve o lead, o cliente e se o cliente foi criado agora.

    Se já existe um contato com o mesmo CPF/CNPJ, ele é reaproveitado (e marcado como
    cliente) em vez de criar outro. As oportunidades do lead passam a apontar para o cliente.
    """
    if person_type not in {"individual", "company"}:
        raise ValueError("Selecione pessoa física ou jurídica.")
    normalized = normalize_tax_id(tax_id, person_type)
    async with in_transaction():
        lead = await Lead.filter(id=lead_id, organization=organization).select_for_update().first()
        if lead is None:
            raise ValueError("Lead não encontrado nesta organização.")
        ensure_lead_open(lead.status)
        existing = None
        if normalized:
            existing = await Counterparty.get_or_none(organization=organization, tax_id=normalized)
        if existing is not None:
            if not existing.is_active:
                raise ValueError("Já existe um contato inativo com este CPF/CNPJ; reative-o.")
            if not existing.is_customer:
                existing.is_customer = True
                existing.party_type = "other" if existing.is_supplier else "customer"
                await existing.save(update_fields=["is_customer", "party_type"])
            customer, created = existing, False
        else:
            customer = await save_counterparty(
                organization=organization,
                person_type=person_type,
                # Para pessoa jurídica, a empresa do lead é a razão social.
                name=(lead.company if person_type == "company" and lead.company else lead.name),
                tax_id=tax_id,
                is_customer=True,
                email=lead.email or "",
                phone=lead.phone or "",
                notes=f"Convertido do lead {lead.name}." if lead.company else "",
            )
            created = True
        lead.status = "converted"
        lead.counterparty = customer
        await lead.save(update_fields=["status", "counterparty_id"])
        await Opportunity.filter(lead=lead, counterparty_id=None).update(
            counterparty_id=customer.id
        )
        return lead, customer, created


# --- Oportunidades --------------------------------------------------------------------


async def create_opportunity(
    *,
    organization: Organization,
    user: User,
    title: str,
    lead_id: UUID | None = None,
    counterparty_id: UUID | None = None,
    expected_value: Decimal = Decimal("0"),
    expected_close: date | None = None,
) -> Opportunity:
    """Nova oportunidade na primeira etapa aberta do funil, para um lead ou um cliente."""
    cleaned = clean_text(title, "O título da oportunidade", 160)
    if (lead_id is None) == (counterparty_id is None):
        raise ValueError("Selecione um lead ou um cliente (só um dos dois).")
    if not expected_value.is_finite() or expected_value < 0 or expected_value > MAX_AMOUNT:
        raise ValueError("O valor estimado deve ser zero ou maior e respeitar o limite permitido.")
    lead = customer = None
    if lead_id is not None:
        lead = await Lead.get_or_none(id=lead_id, organization=organization)
        if lead is None or lead.status == "discarded":
            raise ValueError("Selecione um lead em aberto ou convertido desta organização.")
        if lead.counterparty_id is not None:
            customer = await Counterparty.get(id=lead.counterparty_id)
    else:
        customer = await Counterparty.get_or_none(
            id=counterparty_id, organization=organization, is_active=True, is_customer=True
        )
        if customer is None:
            raise ValueError("Selecione um cliente ativo desta organização.")
    await create_default_pipeline(organization)
    stage = (
        await PipelineStage.filter(organization=organization, kind=OPEN, is_active=True)
        .order_by("position")
        .first()
    )
    async with in_transaction():
        opportunity = await Opportunity.create(
            organization=organization,
            created_by=user,
            owner=user,
            title=cleaned,
            lead=lead,
            counterparty=customer,
            stage=stage,
            expected_value=quantize_money(expected_value),
            expected_close=expected_close,
        )
        await OpportunityStageChange.create(
            opportunity=opportunity, from_stage=None, to_stage=stage, changed_by=user
        )
    return opportunity


async def move_opportunity(
    *,
    organization: Organization,
    user: User,
    opportunity_id: UUID,
    stage_id: UUID,
    lost_reason: str = "",
) -> Opportunity:
    """Muda a etapa e registra no histórico. Perda exige motivo; ganho exige cliente."""
    reason = clean_text(lost_reason, "O motivo da perda", 240, required=False)
    async with in_transaction():
        opportunity = (
            await Opportunity.filter(id=opportunity_id, organization=organization)
            .select_for_update()
            .first()
        )
        if opportunity is None:
            raise ValueError("Oportunidade não encontrada nesta organização.")
        stage = await PipelineStage.get_or_none(
            id=stage_id, organization=organization, is_active=True
        )
        if stage is None:
            raise ValueError("Selecione uma etapa do funil desta organização.")
        ensure_stage_move(
            to_kind=stage.kind,
            has_customer=opportunity.counterparty_id is not None,
            lost_reason=reason,
            same_stage=stage.id == opportunity.stage_id,
        )
        await OpportunityStageChange.create(
            opportunity=opportunity,
            from_stage_id=opportunity.stage_id,
            to_stage=stage,
            changed_by=user,
            note=reason if stage.kind == "lost" else None,
        )
        opportunity.stage = stage
        opportunity.lost_reason = reason if stage.kind == "lost" else None
        opportunity.closed_at = None if stage.kind == OPEN else datetime.now(UTC)
        await opportunity.save(update_fields=["stage_id", "lost_reason", "closed_at"])
        return opportunity


async def link_opportunity_order(
    organization: Organization, opportunity_id: UUID, order: SalesOrder
) -> bool:
    """Liga o orçamento criado à oportunidade (mesma organização e mesmo cliente)."""
    updated = await Opportunity.filter(
        id=opportunity_id,
        organization=organization,
        counterparty_id=order.customer_id,
        sales_order_id=None,
    ).update(sales_order_id=order.id)
    return updated == 1


# --- Atividades -----------------------------------------------------------------------


async def add_activity(
    *,
    organization: Organization,
    user: User,
    kind: str,
    subject: str,
    due_on: date | None = None,
    lead_id: UUID | None = None,
    opportunity_id: UUID | None = None,
) -> CrmActivity:
    if kind not in ACTIVITY_KINDS:
        raise ValueError("Selecione um tipo de atividade válido.")
    cleaned = clean_text(subject, "O assunto da atividade", 200)
    if (lead_id is None) == (opportunity_id is None):
        raise ValueError("A atividade deve estar ligada a um lead ou a uma oportunidade.")
    if lead_id is not None:
        if not await Lead.filter(id=lead_id, organization=organization).exists():
            raise ValueError("Lead não encontrado nesta organização.")
    elif not await Opportunity.filter(id=opportunity_id, organization=organization).exists():
        raise ValueError("Oportunidade não encontrada nesta organização.")
    return await CrmActivity.create(
        organization=organization,
        created_by=user,
        kind=kind,
        subject=cleaned,
        due_on=due_on,
        # Uma nota é só um registro: já nasce concluída.
        done_at=datetime.now(UTC) if kind == "note" else None,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
    )


async def complete_activity(organization: Organization, activity_id: UUID) -> CrmActivity:
    activity = await CrmActivity.get_or_none(id=activity_id, organization=organization)
    if activity is None:
        raise ValueError("Atividade não encontrada nesta organização.")
    if activity.done_at is None:
        activity.done_at = datetime.now(UTC)
        await activity.save(update_fields=["done_at"])
    return activity
