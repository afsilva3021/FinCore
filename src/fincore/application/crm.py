"""Casos de uso do CRM: funil, leads, negociações e atividades.

Propostas ficam em `application/crm_proposals.py`. Todas as funções recebem a organização
da sessão e só enxergam registros dela.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from fincore.application.counterparties import save_counterparty
from fincore.domain.crm import (
    DEFAULT_STAGES,
    LEAD_SOURCES,
    MAX_STAGES,
    ensure_deal_can_reopen,
    ensure_deal_open,
    ensure_lead_can_convert,
    ensure_lead_transition,
    qualification_score,
    validate_activity,
    validate_deal_amount,
    validate_lost_reason,
    validate_probability,
)
from fincore.domain.money import MAX_AMOUNT, parse_brl, quantize_money
from fincore.infrastructure.models import (
    Counterparty,
    CrmActivity,
    CrmDeal,
    CrmDealEvent,
    CrmLead,
    CrmPipelineStage,
    CrmProposal,
    Organization,
    User,
)

# Para marcar um lead como qualificado: necessidade confirmada e ao menos dois critérios.
MIN_QUALIFICATION_SCORE = 2


def parse_optional_amount(value: str) -> Decimal:
    """Valor pt-BR opcional de negociação/lead: vazio vale zero."""
    return parse_brl(
        value,
        allow_zero=True,
        empty_as_zero=True,
        invalid_message="Informe um valor monetário válido.",
        range_message="O valor deve ser igual ou superior a zero.",
    )


def _text(value: str, label: str, limit: int, *, required: bool = False) -> str | None:
    cleaned = value.strip()
    if required and not cleaned:
        raise ValueError(f"Informe {label}.")
    if len(cleaned) > limit:
        raise ValueError(f"{label[:1].upper()}{label[1:]} deve ter no máximo {limit} caracteres.")
    return cleaned or None


def _email(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 254 or "@" not in cleaned.strip("@") or " " in cleaned:
        raise ValueError("Informe um e-mail válido.")
    return cleaned


# --- Funil ----------------------------------------------------------------------------


async def ensure_default_stages(organization: Organization) -> None:
    """Cria as etapas padrão na primeira vez que a organização usa o CRM (idempotente)."""
    if await CrmPipelineStage.filter(organization=organization).exists():
        return
    try:
        async with in_transaction():
            for position, (name, probability) in enumerate(DEFAULT_STAGES, start=1):
                await CrmPipelineStage.create(
                    organization=organization,
                    name=name,
                    position=position,
                    win_probability=probability,
                )
    except IntegrityError:
        # Outra requisição criou as etapas ao mesmo tempo.
        return


async def save_stage(
    *,
    organization: Organization,
    stage_id: UUID | None = None,
    name: str,
    win_probability: int,
    position: int | None = None,
) -> CrmPipelineStage:
    cleaned = _text(name, "o nome da etapa", 60, required=True)
    probability = validate_probability(win_probability)
    if position is not None and not 1 <= position <= 999:
        raise ValueError("A ordem da etapa deve ficar entre 1 e 999.")
    try:
        if stage_id is None:
            if await CrmPipelineStage.filter(organization=organization).count() >= MAX_STAGES:
                raise ValueError(f"O funil aceita até {MAX_STAGES} etapas.")
            if position is None:
                last = (
                    await CrmPipelineStage.filter(organization=organization)
                    .order_by("-position")
                    .first()
                )
                position = (last.position if last else 0) + 1
            return await CrmPipelineStage.create(
                organization=organization,
                name=cleaned,
                position=position,
                win_probability=probability,
            )
        stage = await CrmPipelineStage.get_or_none(id=stage_id, organization=organization)
        if stage is None:
            raise ValueError("Etapa não encontrada nesta organização.")
        stage.name = cleaned
        stage.win_probability = probability
        if position is not None:
            stage.position = position
        await stage.save(update_fields=["name", "win_probability", "position"])
        return stage
    except IntegrityError:
        raise ValueError("Já existe uma etapa com este nome.") from None


async def set_stage_active(
    organization: Organization, stage_id: UUID, is_active: bool
) -> CrmPipelineStage:
    async with in_transaction():
        stage = (
            await CrmPipelineStage.filter(id=stage_id, organization=organization)
            .select_for_update()
            .first()
        )
        if stage is None:
            raise ValueError("Etapa não encontrada nesta organização.")
        if not is_active:
            if await CrmDeal.filter(stage=stage, status="open").exists():
                raise ValueError("Mova as negociações em aberto antes de desativar a etapa.")
            others = CrmPipelineStage.filter(organization=organization, is_active=True)
            if not await others.exclude(id=stage.id).exists():
                raise ValueError("O funil precisa de ao menos uma etapa ativa.")
        stage.is_active = is_active
        await stage.save(update_fields=["is_active"])
        return stage


async def _active_stage(organization: Organization, stage_id: UUID | None) -> CrmPipelineStage:
    await ensure_default_stages(organization)
    query = CrmPipelineStage.filter(organization=organization, is_active=True)
    if stage_id is not None:
        stage = await query.filter(id=stage_id).first()
        if stage is None:
            raise ValueError("Selecione uma etapa ativa do funil desta organização.")
        return stage
    stage = await query.order_by("position", "created_at").first()
    if stage is None:
        raise ValueError("Cadastre ao menos uma etapa ativa no funil.")
    return stage


# --- Leads ----------------------------------------------------------------------------


async def create_lead(
    *,
    organization: Organization,
    user: User,
    name: str,
    company_name: str = "",
    email: str = "",
    phone: str = "",
    source: str = "other",
    estimated_value: Decimal | None = None,
    notes: str = "",
    operation_key: UUID | None = None,
) -> CrmLead:
    if operation_key is not None:
        replay = await CrmLead.get_or_none(operation_key=operation_key)
        if replay is not None:
            if replay.organization_id != organization.id:
                raise ValueError("A chave de operação já foi usada.")
            return replay
    if source not in LEAD_SOURCES:
        raise ValueError("Selecione uma origem de lead válida.")
    value = None
    if estimated_value is not None:
        if not estimated_value.is_finite() or not 0 <= estimated_value <= MAX_AMOUNT:
            raise ValueError("O valor estimado deve ser igual ou superior a zero.")
        value = quantize_money(estimated_value)
    values = {
        "name": _text(name, "o nome do lead", 160, required=True),
        "company_name": _text(company_name, "a empresa", 160),
        "email": _email(email),
        "phone": _text(phone, "o telefone", 40),
        "notes": _text(notes, "as observações", 2000),
    }
    try:
        return await CrmLead.create(
            organization=organization,
            created_by=user,
            owner=user,
            operation_key=operation_key,
            source=source,
            estimated_value=value,
            **values,
        )
    except IntegrityError:
        # Dois envios simultâneos com a mesma chave: devolve o que o outro criou.
        replay = await CrmLead.get_or_none(operation_key=operation_key)
        if replay is not None and replay.organization_id == organization.id:
            return replay
        raise ValueError("A chave de operação já foi usada.") from None


async def update_lead(
    *,
    organization: Organization,
    lead_id: UUID,
    status: str,
    has_need: bool,
    has_budget: bool,
    has_authority: bool,
    has_timeline: bool,
    disqualified_reason: str = "",
) -> CrmLead:
    """Atualiza os critérios de qualificação e, se mudou, a situação do lead."""
    async with in_transaction():
        lead = (
            await CrmLead.filter(id=lead_id, organization=organization).select_for_update().first()
        )
        if lead is None:
            raise ValueError("Lead não encontrado nesta organização.")
        if lead.status == "converted":
            raise ValueError("Lead convertido não pode mais ser alterado.")
        if status != lead.status:
            ensure_lead_transition(lead.status, status)
        score = qualification_score(
            need=has_need, budget=has_budget, authority=has_authority, timeline=has_timeline
        )
        if status == "qualified" and (not has_need or score < MIN_QUALIFICATION_SCORE):
            raise ValueError(
                "Para qualificar, confirme a necessidade e ao menos mais um critério "
                "(orçamento, decisor ou prazo)."
            )
        reason = None
        if status == "disqualified":
            reason = _text(disqualified_reason, "o motivo da desqualificação", 240, required=True)
        lead.status = status
        lead.has_need = has_need
        lead.has_budget = has_budget
        lead.has_authority = has_authority
        lead.has_timeline = has_timeline
        lead.disqualified_reason = reason
        await lead.save(
            update_fields=[
                "status",
                "has_need",
                "has_budget",
                "has_authority",
                "has_timeline",
                "disqualified_reason",
            ]
        )
        return lead


async def find_duplicate_contacts(
    organization: Organization, *, names: list[str], email: str | None
) -> list[Counterparty]:
    """Contatos da organização com o mesmo e-mail ou o mesmo nome (sem diferenciar caixa)."""
    conditions = []
    for name in sorted({item.strip() for item in names if item and item.strip()}):
        conditions += [Q(name__iexact=name), Q(trade_name__iexact=name)]
    if email:
        conditions.append(Q(email__iexact=email))
    if not conditions:
        return []
    return await Counterparty.filter(
        Q(organization=organization) & Q(*conditions, join_type="OR")
    ).order_by("name")


async def convert_lead(
    *,
    organization: Organization,
    user: User,
    lead_id: UUID,
    counterparty_id: UUID | None = None,
    person_type: str = "individual",
    tax_id: str = "",
    create_deal: bool = True,
    deal_title: str = "",
    deal_amount: Decimal | None = None,
    stage_id: UUID | None = None,
    expected_close_date: date | None = None,
) -> tuple[CrmLead, Counterparty, CrmDeal | None]:
    """Converte o lead em contato (novo ou existente) e, se pedido, em negociação.

    É atômico: ou ficam gravados o contato, a negociação e o vínculo no lead, ou nada.
    Sem `counterparty_id`, recusa criar um contato que duplique outro pelo nome ou e-mail.
    """
    async with in_transaction():
        lead = (
            await CrmLead.filter(id=lead_id, organization=organization).select_for_update().first()
        )
        if lead is None:
            raise ValueError("Lead não encontrado nesta organização.")
        ensure_lead_can_convert(lead.status)
        if counterparty_id is not None:
            contact = await Counterparty.get_or_none(
                id=counterparty_id, organization=organization, is_active=True
            )
            if contact is None:
                raise ValueError("Selecione um contato ativo desta organização.")
            if not contact.is_customer:
                contact.is_customer = True
                contact.party_type = "other" if contact.is_supplier else "customer"
                try:
                    await contact.save(update_fields=["is_customer", "party_type"])
                except IntegrityError:
                    raise ValueError("Não foi possível marcar o contato como cliente.") from None
        else:
            company = person_type == "company"
            contact_name = (lead.company_name if company and lead.company_name else lead.name)[:150]
            duplicates = await find_duplicate_contacts(
                organization, names=[lead.name, lead.company_name or ""], email=lead.email
            )
            if duplicates:
                raise ValueError(
                    f"Já existe um contato parecido ({duplicates[0].name}). "
                    "Vincule o lead a ele ou ajuste os dados antes de converter."
                )
            contact = await save_counterparty(
                organization=organization,
                person_type=person_type,
                name=contact_name,
                tax_id=tax_id,
                is_customer=True,
                email=lead.email or "",
                phone=lead.phone or "",
                notes=(lead.notes or "")[:1000],
            )
        deal = None
        if create_deal:
            deal = await _create_deal(
                organization=organization,
                user=user,
                counterparty=contact,
                title=deal_title.strip() or f"Negociação com {contact.name}",
                amount=deal_amount
                if deal_amount is not None
                else (lead.estimated_value or Decimal("0.00")),
                stage=await _active_stage(organization, stage_id),
                expected_close_date=expected_close_date,
                notes="",
                operation_key=None,
            )
        lead.status = "converted"
        lead.converted_counterparty = contact
        lead.converted_deal = deal
        lead.converted_by = user
        lead.converted_at = datetime.now(UTC)
        await lead.save(
            update_fields=[
                "status",
                "converted_counterparty_id",
                "converted_deal_id",
                "converted_by_id",
                "converted_at",
            ]
        )
        return lead, contact, deal


# --- Negociações ----------------------------------------------------------------------


async def _create_deal(
    *,
    organization: Organization,
    user: User,
    counterparty: Counterparty,
    title: str,
    amount: Decimal,
    stage: CrmPipelineStage,
    expected_close_date: date | None,
    notes: str,
    operation_key: UUID | None,
) -> CrmDeal:
    deal = await CrmDeal.create(
        organization=organization,
        created_by=user,
        owner=user,
        counterparty=counterparty,
        stage=stage,
        operation_key=operation_key,
        title=_text(title, "o título da negociação", 160, required=True),
        amount=validate_deal_amount(amount),
        expected_close_date=expected_close_date,
        stage_changed_at=datetime.now(UTC),
        notes=_text(notes, "as observações", 2000),
    )
    await CrmDealEvent.create(
        organization=organization, deal=deal, actor=user, action="created", to_stage=stage
    )
    return deal


async def create_deal(
    *,
    organization: Organization,
    user: User,
    counterparty_id: UUID,
    title: str,
    amount: Decimal = Decimal("0.00"),
    stage_id: UUID | None = None,
    expected_close_date: date | None = None,
    notes: str = "",
    operation_key: UUID | None = None,
) -> CrmDeal:
    if operation_key is not None:
        replay = await CrmDeal.get_or_none(operation_key=operation_key)
        if replay is not None:
            if replay.organization_id != organization.id:
                raise ValueError("A chave de operação já foi usada.")
            return replay
    contact = await Counterparty.get_or_none(
        id=counterparty_id, organization=organization, is_active=True
    )
    if contact is None:
        raise ValueError("Selecione um contato ativo desta organização.")
    stage = await _active_stage(organization, stage_id)
    try:
        async with in_transaction():
            return await _create_deal(
                organization=organization,
                user=user,
                counterparty=contact,
                title=title,
                amount=amount,
                stage=stage,
                expected_close_date=expected_close_date,
                notes=notes,
                operation_key=operation_key,
            )
    except IntegrityError:
        replay = await CrmDeal.get_or_none(operation_key=operation_key)
        if replay is not None and replay.organization_id == organization.id:
            return replay
        raise ValueError("A chave de operação já foi usada.") from None


async def _deal_for_update(organization: Organization, deal_id: UUID) -> CrmDeal:
    deal = await CrmDeal.filter(id=deal_id, organization=organization).select_for_update().first()
    if deal is None:
        raise ValueError("Negociação não encontrada nesta organização.")
    return deal


async def update_deal(
    *,
    organization: Organization,
    user: User,
    deal_id: UUID,
    title: str,
    amount: Decimal,
    expected_close_date: date | None,
    notes: str = "",
) -> CrmDeal:
    async with in_transaction():
        deal = await _deal_for_update(organization, deal_id)
        ensure_deal_open(deal.status)
        deal.title = _text(title, "o título da negociação", 160, required=True)
        deal.amount = validate_deal_amount(amount)
        deal.expected_close_date = expected_close_date
        deal.notes = _text(notes, "as observações", 2000)
        await deal.save(update_fields=["title", "amount", "expected_close_date", "notes"])
        await CrmDealEvent.create(
            organization=organization, deal=deal, actor=user, action="updated"
        )
        return deal


async def move_deal(
    *, organization: Organization, user: User, deal_id: UUID, stage_id: UUID
) -> CrmDeal:
    async with in_transaction():
        deal = await _deal_for_update(organization, deal_id)
        ensure_deal_open(deal.status)
        if deal.stage_id == stage_id:
            return deal
        stage = await CrmPipelineStage.get_or_none(
            id=stage_id, organization=organization, is_active=True
        )
        if stage is None:
            raise ValueError("Selecione uma etapa ativa do funil desta organização.")
        await CrmDealEvent.create(
            organization=organization,
            deal=deal,
            actor=user,
            action="stage_changed",
            from_stage_id=deal.stage_id,
            to_stage=stage,
        )
        deal.stage = stage
        deal.stage_changed_at = datetime.now(UTC)
        await deal.save(update_fields=["stage_id", "stage_changed_at"])
        return deal


async def mark_deal_won(deal: CrmDeal, organization: Organization, user: User, detail: str) -> None:
    """Marca como ganha uma negociação já bloqueada na transação em curso."""
    deal.status = "won"
    deal.lost_reason = None
    deal.closed_at = datetime.now(UTC)
    await deal.save(update_fields=["status", "lost_reason", "closed_at"])
    await CrmDealEvent.create(
        organization=organization,
        deal=deal,
        actor=user,
        action="won",
        from_stage_id=deal.stage_id,
        detail=detail[:240] or None,
    )


async def win_deal(*, organization: Organization, user: User, deal_id: UUID) -> CrmDeal:
    async with in_transaction():
        deal = await _deal_for_update(organization, deal_id)
        if deal.status == "won":
            return deal
        ensure_deal_open(deal.status)
        await mark_deal_won(deal, organization, user, "")
        return deal


async def lose_deal(
    *, organization: Organization, user: User, deal_id: UUID, reason: str
) -> CrmDeal:
    async with in_transaction():
        deal = await _deal_for_update(organization, deal_id)
        ensure_deal_open(deal.status)
        deal.lost_reason = validate_lost_reason(reason)
        deal.status = "lost"
        deal.closed_at = datetime.now(UTC)
        await deal.save(update_fields=["status", "lost_reason", "closed_at"])
        await CrmDealEvent.create(
            organization=organization,
            deal=deal,
            actor=user,
            action="lost",
            from_stage_id=deal.stage_id,
            detail=deal.lost_reason,
        )
        return deal


async def reopen_deal(*, organization: Organization, user: User, deal_id: UUID) -> CrmDeal:
    async with in_transaction():
        deal = await _deal_for_update(organization, deal_id)
        ensure_deal_can_reopen(
            deal.status, await CrmProposal.filter(deal=deal, status="accepted").exists()
        )
        stage = await deal.stage
        if not stage.is_active:
            stage = await _active_stage(organization, None)
            deal.stage = stage
        deal.status = "open"
        deal.lost_reason = None
        deal.closed_at = None
        deal.stage_changed_at = datetime.now(UTC)
        await deal.save(
            update_fields=["status", "lost_reason", "closed_at", "stage_id", "stage_changed_at"]
        )
        await CrmDealEvent.create(
            organization=organization, deal=deal, actor=user, action="reopened", to_stage=stage
        )
        return deal


# --- Atividades -----------------------------------------------------------------------


async def create_activity(
    *,
    organization: Organization,
    user: User,
    activity_type: str,
    subject: str,
    due_date: date | None = None,
    notes: str = "",
    lead_id: UUID | None = None,
    deal_id: UUID | None = None,
    counterparty_id: UUID | None = None,
    done: bool = False,
) -> CrmActivity:
    links = [item for item in (lead_id, deal_id, counterparty_id) if item is not None]
    cleaned = validate_activity(activity_type, subject, len(links))
    lead = deal = contact = None
    if lead_id is not None:
        lead = await CrmLead.get_or_none(id=lead_id, organization=organization)
    elif deal_id is not None:
        deal = await CrmDeal.get_or_none(id=deal_id, organization=organization)
    else:
        contact = await Counterparty.get_or_none(id=counterparty_id, organization=organization)
    if lead is None and deal is None and contact is None:
        raise ValueError("O registro ligado à atividade não foi encontrado nesta organização.")
    # Nota é o registro de algo que já aconteceu: nasce concluída.
    finished = done or activity_type == "note"
    return await CrmActivity.create(
        organization=organization,
        created_by=user,
        lead=lead,
        deal=deal,
        counterparty=contact,
        activity_type=activity_type,
        subject=cleaned,
        due_date=due_date,
        notes=_text(notes, "as observações", 2000),
        done_at=datetime.now(UTC) if finished else None,
        done_by=user if finished else None,
    )


async def complete_activity(
    *, organization: Organization, user: User, activity_id: UUID
) -> CrmActivity:
    async with in_transaction():
        activity = (
            await CrmActivity.filter(id=activity_id, organization=organization)
            .select_for_update()
            .first()
        )
        if activity is None:
            raise ValueError("Atividade não encontrada nesta organização.")
        if activity.done_at is None:
            activity.done_at = datetime.now(UTC)
            activity.done_by = user
            await activity.save(update_fields=["done_at", "done_by_id"])
        return activity
