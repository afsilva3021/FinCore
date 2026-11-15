"""Cadastro de títulos e baixas, separado do extrato de transações."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.application.finance import create_settlement_journal, reverse_movement
from fincore.domain.accounting import settlement_journal_lines
from fincore.domain.money import MAX_AMOUNT, parse_brl, quantize_money
from fincore.domain.titles import (
    PAYMENT_METHODS,
    TITLE_TYPES,
    calculate_settlement,
    ensure_can_cancel,
    ensure_can_reverse_settlement,
    ensure_can_settle,
    open_balance,
    split_installments,
    status_after_settlement_reversal,
)
from fincore.infrastructure.models import (
    Category,
    Counterparty,
    FinancialAccount,
    FinancialTitle,
    FinancialTransaction,
    Organization,
    TitleSettlement,
    User,
)

PARTY_TYPES = {"supplier", "customer", "other"}


def parse_adjustment(value: str) -> Decimal:
    return parse_brl(
        value,
        allow_zero=True,
        empty_as_zero=True,
        invalid_message="Informe um ajuste monetário válido.",
        range_message="Ajustes devem ser valores iguais ou superiores a zero.",
    )


async def _titles_for_operation_key(
    organization: Organization, operation_key: UUID
) -> list[FinancialTitle] | None:
    """Reenvio com a mesma chave devolve os títulos já criados, sem duplicar."""
    first = await FinancialTitle.get_or_none(operation_key=operation_key)
    if first is None:
        return None
    if first.organization_id != organization.id:
        raise ValueError("A chave de operação já foi usada.")
    if first.installment_group is None:
        return [first]
    return await FinancialTitle.filter(
        organization=organization, installment_group=first.installment_group
    ).order_by("installment_number")


async def create_titles(
    *,
    organization: Organization,
    user: User,
    title_type: str,
    description: str,
    counterparty_name: str,
    party_type: str,
    account_id: UUID,
    category_id: UUID | None,
    amount: Decimal,
    issue_date: date,
    due_date: date,
    payment_method: str,
    payment_terms: str,
    installment_count: int,
    document_number: str = "",
    document_type: str = "",
    document_series: str = "",
    cost_center: str = "",
    project: str = "",
    expected_receipt_date: date | None = None,
    collection_sent_date: date | None = None,
    barcode: str = "",
    beneficiary: str = "",
    approver_reference: str = "",
    payment_details: str = "",
    related_reference: str = "",
    charge_reference: str = "",
    attachment_reference: str = "",
    notes: str = "",
    operation_key: UUID | None = None,
    counterparty_id: UUID | None = None,
) -> list[FinancialTitle]:
    """Cadastra um título ou suas parcelas, sem movimentar caixa.

    Com `counterparty_id`, o título usa exatamente esse contato da organização (caso das
    propostas do CRM) e `counterparty_name`/`party_type` são ignorados.
    """
    if operation_key is not None:
        replay = await _titles_for_operation_key(organization, operation_key)
        if replay is not None:
            return replay
    if title_type not in TITLE_TYPES:
        raise ValueError("Tipo de título inválido.")
    if not description.strip() or len(description) > 240:
        raise ValueError("Informe uma descrição de até 240 caracteres.")
    if counterparty_id is None:
        if not counterparty_name.strip() or len(counterparty_name) > 160:
            raise ValueError("Informe o fornecedor, cliente ou contato relacionado.")
        if party_type not in PARTY_TYPES:
            raise ValueError("Selecione fornecedor, cliente ou outro contato.")
    if amount <= 0 or amount > MAX_AMOUNT:
        raise ValueError("O valor original deve ser maior que zero.")
    if due_date < issue_date:
        raise ValueError("O vencimento não pode ser anterior à emissão.")
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Selecione uma forma de pagamento ou recebimento válida.")
    if payment_terms not in {"cash", "installment"}:
        raise ValueError("Selecione à vista ou parcelado.")
    count = installment_count if payment_terms == "installment" else 1
    if count < 1 or count > 36 or (payment_terms == "installment" and count < 2):
        raise ValueError("O parcelamento deve ter entre 2 e 36 parcelas.")
    account = await FinancialAccount.get_or_none(
        id=account_id, organization=organization, is_active=True
    )
    if account is None:
        raise ValueError("Selecione uma conta financeira desta organização.")
    category = None
    transaction_type = "expense" if title_type == "payable" else "income"
    if category_id:
        category = await Category.get_or_none(
            id=category_id,
            organization=organization,
            transaction_type=transaction_type,
            category_kind="analytic",
            is_active=True,
        )
        if category is None:
            raise ValueError("Selecione uma categoria analítica válida para este título.")

    name = counterparty_name.strip()
    total_amount = quantize_money(amount)
    installments = split_installments(total_amount, count, due_date)
    group_id = uuid4() if count > 1 else None
    created: list[FinancialTitle] = []
    try:
        async with in_transaction():
            if counterparty_id is not None:
                counterparty = await Counterparty.get_or_none(
                    id=counterparty_id, organization=organization
                )
                if counterparty is None:
                    raise ValueError("Contato não encontrado nesta organização.")
            else:
                counterparty = await Counterparty.filter(
                    organization=organization,
                    name=name,
                    party_type=party_type,
                ).first()
            if (
                counterparty is None
                and counterparty_id is None
                and party_type in {"customer", "supplier"}
            ):
                role_field = "is_customer" if party_type == "customer" else "is_supplier"
                counterparty = await Counterparty.filter(
                    organization=organization,
                    name=name,
                    party_type="other",
                    is_active=True,
                    **{role_field: True},
                ).first()
            if counterparty is not None and not counterparty.is_active:
                raise ValueError("O contato selecionado está inativo.")
            if counterparty is None:
                counterparty = await Counterparty.create(
                    organization=organization,
                    name=name,
                    party_type=party_type,
                    is_customer=party_type == "customer",
                    is_supplier=party_type == "supplier",
                )
            for installment_no, (installment_amount, installment_due) in enumerate(
                installments, start=1
            ):
                installment_description = description.strip()
                if count > 1:
                    installment_description = (
                        f"{installment_description} ({installment_no}/{count})"
                    )
                created.append(
                    await FinancialTitle.create(
                        operation_key=operation_key if installment_no == 1 else None,
                        organization=organization,
                        created_by=user,
                        counterparty=counterparty,
                        account=account,
                        category=category,
                        installment_group=group_id,
                        title_type=title_type,
                        description=installment_description[:240],
                        document_number=document_number.strip() or None,
                        document_type=document_type.strip() or None,
                        document_series=document_series.strip() or None,
                        cost_center=cost_center.strip() or None,
                        project=project.strip() or None,
                        original_amount=installment_amount,
                        issue_date=issue_date,
                        due_date=installment_due,
                        expected_receipt_date=expected_receipt_date,
                        collection_sent_date=collection_sent_date,
                        payment_method=payment_method,
                        payment_terms=payment_terms,
                        installment_number=installment_no,
                        installment_count=count,
                        barcode=barcode.strip() or None,
                        beneficiary=beneficiary.strip() or None,
                        approver_reference=approver_reference.strip() or None,
                        payment_details=payment_details.strip() or None,
                        related_reference=related_reference.strip() or None,
                        charge_reference=charge_reference.strip() or None,
                        attachment_reference=attachment_reference.strip() or None,
                        notes=notes.strip() or None,
                    )
                )
    except IntegrityError:
        # Dois envios simultâneos com a mesma chave: o perdedor devolve o que o outro criou.
        if operation_key is not None:
            replay = await _titles_for_operation_key(organization, operation_key)
            if replay is not None:
                return replay
        raise ValueError("Não foi possível cadastrar o contato relacionado.") from None
    return created


async def title_balances(titles: list[FinancialTitle]) -> dict[UUID, Decimal]:
    if not titles:
        return {}
    # Baixas estornadas ficam no histórico, mas não abatem o saldo.
    settlements = await TitleSettlement.filter(
        title_id__in=[title.id for title in titles], reversed_at=None
    ).all()
    applied: dict[UUID, list[Decimal]] = {}
    for settlement in settlements:
        applied.setdefault(settlement.title_id, []).append(settlement.amount_applied)
    return {
        title.id: open_balance(title.original_amount, applied.get(title.id, [])) for title in titles
    }


async def settle_title(
    *,
    organization: Organization,
    user: User,
    title_id: UUID,
    operation_key: UUID,
    amount_applied: Decimal,
    paid_on: date,
    account_id: UUID,
    payment_method: str,
    discount: Decimal = Decimal("0.00"),
    interest: Decimal = Decimal("0.00"),
    fine: Decimal = Decimal("0.00"),
    fees: Decimal = Decimal("0.00"),
    proof_reference: str = "",
    notes: str = "",
) -> TitleSettlement:
    existing = await TitleSettlement.get_or_none(operation_key=operation_key)
    if existing:
        # Reenvio só devolve a baixa original se for do mesmo título e organização;
        # uma chave reaproveitada em outro contexto é conflito, nunca um vazamento.
        if existing.organization_id != organization.id or existing.title_id != title_id:
            raise ValueError("A chave de operação já foi usada.")
        return existing
    if quantize_money(amount_applied) <= 0:
        raise ValueError("O valor baixado deve ser maior que zero.")
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Selecione uma forma de pagamento válida.")

    async with in_transaction():
        title = (
            await FinancialTitle.filter(id=title_id, organization=organization)
            .select_for_update()
            .first()
        )
        if title is None:
            raise ValueError("Título não encontrado nesta organização.")
        ensure_can_settle(title.status)
        previous = await TitleSettlement.filter(title=title, reversed_at=None).values_list(
            "amount_applied", flat=True
        )
        amounts = calculate_settlement(
            title_type=title.title_type,
            balance=open_balance(title.original_amount, previous),
            applied=amount_applied,
            discount=discount,
            interest=interest,
            fine=fine,
            fees=fees,
        )
        account = await FinancialAccount.get_or_none(
            id=account_id, organization=organization, is_active=True
        )
        if account is None:
            raise ValueError("Selecione uma conta financeira válida para a baixa.")
        category = await title.category if title.category_id else None
        transaction = await FinancialTransaction.create(
            operation_key=uuid4(),
            organization=organization,
            account=account,
            category=category,
            created_by=user,
            settled_by=user,
            transaction_type="expense" if title.title_type == "payable" else "income",
            description=f"Baixa de título: {title.description}"[:240],
            amount=amounts.net_amount,
            competence_date=paid_on,
            due_date=None,
            settled_at=datetime.now(UTC),
            status="settled",
        )
        await create_settlement_journal(
            organization,
            user,
            transaction,
            settlement_journal_lines(
                title_type=title.title_type,
                applied=amounts.applied,
                discount=amounts.discount,
                interest=amounts.interest,
                fine=amounts.fine,
                fees=amounts.fees,
                net_amount=amounts.net_amount,
            ),
        )
        settlement = await TitleSettlement.create(
            organization=organization,
            title=title,
            created_by=user,
            financial_transaction=transaction,
            account=account,
            operation_key=operation_key,
            payment_method=payment_method,
            amount_applied=amounts.applied,
            discount=amounts.discount,
            interest=amounts.interest,
            fine=amounts.fine,
            fees=amounts.fees,
            net_amount=amounts.net_amount,
            paid_on=paid_on,
            proof_reference=proof_reference.strip() or None,
            notes=notes.strip() or None,
        )
        title.status = amounts.new_status
        await title.save(update_fields=["status"])
        return settlement


async def cancel_title(*, organization: Organization, title_id: UUID) -> FinancialTitle:
    async with in_transaction():
        title = (
            await FinancialTitle.filter(id=title_id, organization=organization)
            .select_for_update()
            .first()
        )
        if title is None:
            raise ValueError("Título não encontrado nesta organização.")
        ensure_can_cancel(
            title.status, await TitleSettlement.filter(title=title, reversed_at=None).exists()
        )
        title.status = "cancelled"
        await title.save(update_fields=["status"])
        return title


async def reverse_settlement(
    *,
    organization: Organization,
    user: User,
    title_id: UUID,
    settlement_id: UUID,
    operation_key: UUID,
    reversed_on: date,
) -> TitleSettlement:
    """Estorna uma baixa: devolve o saldo ao título e compensa o caixa e o razão.

    Numa única transação: cria a movimentação compensatória (com lançamento contábil
    espelhado), marca a baixa como estornada — ela continua no histórico — e recalcula a
    situação do título. Repetir o estorno de uma baixa já estornada não faz nada.
    """
    async with in_transaction():
        title = (
            await FinancialTitle.filter(id=title_id, organization=organization)
            .select_for_update()
            .first()
        )
        if title is None:
            raise ValueError("Título não encontrado nesta organização.")
        settlement = (
            await TitleSettlement.filter(id=settlement_id, title=title, organization=organization)
            .select_for_update()
            .first()
        )
        if settlement is None:
            raise ValueError("Baixa não encontrada neste título.")
        if settlement.reversed_at is not None:
            return settlement
        ensure_can_reverse_settlement(title.status)
        movement = await reverse_movement(
            organization,
            user,
            settlement.financial_transaction_id,
            operation_key,
            reversed_on,
            from_settlement=True,
        )
        # Chave reaproveitada de outra operação devolveria um movimento que não é o
        # estorno desta baixa.
        if movement.reversal_of_id != settlement.financial_transaction_id:
            raise ValueError("A chave de operação já foi usada.")
        settlement.reversed_at = datetime.now(UTC)
        settlement.reversed_by = user
        settlement.reversal_transaction = movement
        await settlement.save(
            update_fields=["reversed_at", "reversed_by_id", "reversal_transaction_id"]
        )
        title.status = status_after_settlement_reversal(
            await TitleSettlement.filter(title=title, reversed_at=None).exists()
        )
        await title.save(update_fields=["status"])
        return settlement
