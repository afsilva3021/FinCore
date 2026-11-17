"""Cadastro de títulos e baixas, separado do extrato de transações."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.application.finance import (
    _create_journal,
    _create_movement_journal,
    _ensure_financial_ledger_account,
    _system_account,
)
from fincore.domain.money import MAX_AMOUNT, parse_brl, quantize_money
from fincore.domain.titles import (
    PAYMENT_METHODS,
    TITLE_TYPES,
    calculate_settlement,
    ensure_can_cancel,
    ensure_can_settle,
    open_balance,
    split_installments,
)
from fincore.infrastructure.models import (
    Category,
    Counterparty,
    FinancialAccount,
    FinancialTitle,
    FinancialTransaction,
    LedgerAccount,
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
) -> list[FinancialTitle]:
    if operation_key is not None:
        replay = await _titles_for_operation_key(organization, operation_key)
        if replay is not None:
            return replay
    if title_type not in TITLE_TYPES:
        raise ValueError("Tipo de título inválido.")
    if not description.strip() or len(description) > 240:
        raise ValueError("Informe uma descrição de até 240 caracteres.")
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
            counterparty = await Counterparty.filter(
                organization=organization,
                name=name,
                party_type=party_type,
            ).first()
            if counterparty is None and party_type in {"customer", "supplier"}:
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


async def create_document_titles(
    *,
    organization: Organization,
    user: User,
    title_type: str,
    counterparty: Counterparty,
    account: FinancialAccount,
    amount: Decimal,
    issue_date: date,
    first_due_date: date,
    payment_method: str,
    installment_count: int,
    description: str,
    origin: str,
    accrual_account: LedgerAccount,
    source_type: str,
    source_id: UUID,
    document_number: str | None = None,
    related_reference: str | None = None,
) -> list[FinancialTitle]:
    """Títulos gerados por uma compra ou venda. **Chame dentro da transação do documento.**

    O documento já lançou o valor por competência contra `accrual_account` (Fornecedores
    ou Clientes); a baixa destes títulos só quita essa conta.
    """
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Selecione uma forma de pagamento ou recebimento válida.")
    total = quantize_money(amount)
    if total <= 0 or total > MAX_AMOUNT:
        raise ValueError("O valor do título deve ser maior que zero.")
    installments = split_installments(total, installment_count, first_due_date)
    group_id = uuid4() if installment_count > 1 else None
    titles = []
    for number, (installment_amount, due_date) in enumerate(installments, start=1):
        label = (
            f"{description} ({number}/{installment_count})"
            if installment_count > 1
            else description
        )
        titles.append(
            await FinancialTitle.create(
                organization=organization,
                created_by=user,
                counterparty=counterparty,
                account=account,
                installment_group=group_id,
                title_type=title_type,
                description=label[:240],
                document_number=document_number,
                original_amount=installment_amount,
                issue_date=issue_date,
                due_date=due_date,
                payment_method=payment_method,
                payment_terms="installment" if installment_count > 1 else "cash",
                installment_number=number,
                installment_count=installment_count,
                related_reference=related_reference,
                origin=origin,
                accrual_account=accrual_account,
                source_type=source_type,
                source_id=source_id,
            )
        )
    return titles


async def cancel_document_titles(source_type: str, source_id: UUID) -> None:
    """Cancela os títulos de um documento estornado. **Chame dentro da transação.**

    Recusa se algum título já recebeu baixa: primeiro é preciso tratar o pagamento.
    """
    titles = await FinancialTitle.filter(
        source_type=source_type, source_id=source_id
    ).select_for_update()
    for title in titles:
        if title.status == "cancelled":
            continue
        ensure_can_cancel(title.status, await TitleSettlement.filter(title=title).exists())
        title.status = "cancelled"
        await title.save(update_fields=["status"])


async def _create_accrual_settlement_journal(
    organization: Organization,
    user: User,
    title: FinancialTitle,
    transaction: FinancialTransaction,
    account: FinancialAccount,
    amounts,
) -> None:
    """Baixa de título de compra/venda: quita Fornecedores ou Clientes, não vira despesa.

    Juros, multa e tarifas (e o desconto concedido a clientes) vão para despesas
    financeiras; desconto obtido de fornecedor e juros/multa recebidos, para receitas
    financeiras. O lançamento continua com débitos iguais aos créditos.
    """
    bank = await _ensure_financial_ledger_account(organization, user, account)
    accrual = await LedgerAccount.get(id=title.accrual_account_id)
    zero = Decimal("0.00")
    if title.title_type == "payable":
        financial_expense = amounts.interest + amounts.fine + amounts.fees
        financial_revenue = amounts.discount
        lines = [(accrual, amounts.applied, zero), (bank, zero, amounts.net_amount)]
    else:
        financial_expense = amounts.discount + amounts.fees
        financial_revenue = amounts.interest + amounts.fine
        lines = [(bank, amounts.net_amount, zero), (accrual, zero, amounts.applied)]
    if financial_expense > 0:
        expense = await _system_account(
            organization, user, "SYS-FIN-EXP", "Despesas financeiras", "expense"
        )
        lines.append((expense, financial_expense, zero))
    if financial_revenue > 0:
        revenue = await _system_account(
            organization, user, "SYS-FIN-REV", "Receitas financeiras", "revenue"
        )
        lines.append((revenue, zero, financial_revenue))
    await _create_journal(
        organization=organization,
        user=user,
        idempotency_key=uuid4(),
        entry_date=transaction.competence_date,
        description=transaction.description,
        lines=lines,
        source_transaction=transaction,
    )


async def title_balances(titles: list[FinancialTitle]) -> dict[UUID, Decimal]:
    if not titles:
        return {}
    settlements = await TitleSettlement.filter(title_id__in=[title.id for title in titles]).all()
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
        previous = await TitleSettlement.filter(title=title).values_list(
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
        if title.origin == "manual":
            await _create_movement_journal(organization, user, transaction)
        else:
            await _create_accrual_settlement_journal(
                organization, user, title, transaction, account, amounts
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
        if title.origin != "manual":
            raise ValueError(
                "Este título foi gerado por uma compra ou venda; estorne o documento de origem."
            )
        ensure_can_cancel(title.status, await TitleSettlement.filter(title=title).exists())
        title.status = "cancelled"
        await title.save(update_fields=["status"])
        return title
