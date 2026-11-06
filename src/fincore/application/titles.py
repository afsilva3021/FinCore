"""Cadastro de títulos e baixas, separado do extrato de transações."""

import calendar
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.application.finance import _create_movement_journal
from fincore.application.money import parse_brl
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

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999999.99")
TITLE_TYPES = {"payable", "receivable"}
PARTY_TYPES = {"supplier", "customer", "other"}
PAYMENT_METHODS = {"cash", "bank_transfer", "pix", "boleto", "card", "other"}


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _monthly_date(start: date, offset: int) -> date:
    month_index = start.month - 1 + offset
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _validate_adjustment(value: Decimal, label: str) -> Decimal:
    amount = _quantize(value)
    if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT:
        raise ValueError(f"{label} deve ser um valor igual ou superior a zero.")
    return amount


def parse_adjustment(value: str) -> Decimal:
    return parse_brl(
        value,
        allow_zero=True,
        empty_as_zero=True,
        invalid_message="Informe um ajuste monetário válido.",
        range_message="Ajustes devem ser valores iguais ou superiores a zero.",
    )


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
) -> list[FinancialTitle]:
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
    total_amount = _quantize(amount)
    base_amount = _quantize(total_amount / count)
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
            allocated = Decimal("0.00")
            for installment_no in range(1, count + 1):
                installment_amount = (
                    total_amount - allocated if installment_no == count else base_amount
                )
                allocated += installment_amount
                installment_due = _monthly_date(due_date, installment_no - 1)
                installment_description = description.strip()
                if count > 1:
                    installment_description = (
                        f"{installment_description} ({installment_no}/{count})"
                    )
                created.append(
                    await FinancialTitle.create(
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
        raise ValueError("Não foi possível cadastrar o contato relacionado.") from None
    return created


async def title_balances(titles: list[FinancialTitle]) -> dict[UUID, Decimal]:
    if not titles:
        return {}
    settlements = await TitleSettlement.filter(title_id__in=[title.id for title in titles]).all()
    paid: dict[UUID, Decimal] = {}
    for settlement in settlements:
        paid[settlement.title_id] = paid.get(settlement.title_id, Decimal("0.00")) + (
            settlement.amount_applied
        )
    return {
        title.id: _quantize(title.original_amount - paid.get(title.id, Decimal("0.00")))
        for title in titles
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
        return existing
    applied = _quantize(amount_applied)
    discount = _validate_adjustment(discount, "Desconto")
    interest = _validate_adjustment(interest, "Juros")
    fine = _validate_adjustment(fine, "Multa")
    fees = _validate_adjustment(fees, "Tarifas")
    if applied <= 0 or applied > MAX_AMOUNT:
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
        if title.status == "cancelled":
            raise ValueError("Títulos cancelados não podem ser baixados.")
        if title.status == "settled":
            raise ValueError("Este título já está liquidado.")
        previous = await TitleSettlement.filter(title=title).all()
        balance = _quantize(
            title.original_amount - sum((item.amount_applied for item in previous), Decimal("0.00"))
        )
        if applied > balance:
            raise ValueError("O valor baixado não pode exceder o saldo em aberto.")
        if discount > applied:
            raise ValueError("O desconto não pode exceder o valor aplicado ao título.")
        net_amount = _quantize(
            applied
            - discount
            + interest
            + fine
            + (fees if title.title_type == "payable" else -fees)
        )
        if net_amount <= 0 or net_amount > MAX_AMOUNT:
            raise ValueError("O valor líquido precisa ser maior que zero.")
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
            amount=net_amount,
            competence_date=paid_on,
            due_date=None,
            settled_at=datetime.now(UTC),
            status="settled",
        )
        await _create_movement_journal(organization, user, transaction)
        settlement = await TitleSettlement.create(
            organization=organization,
            title=title,
            created_by=user,
            financial_transaction=transaction,
            account=account,
            operation_key=operation_key,
            payment_method=payment_method,
            amount_applied=applied,
            discount=discount,
            interest=interest,
            fine=fine,
            fees=fees,
            net_amount=net_amount,
            paid_on=paid_on,
            proof_reference=proof_reference.strip() or None,
            notes=notes.strip() or None,
        )
        title.status = "settled" if applied == balance else "partial"
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
        if title.status != "open":
            raise ValueError("Somente títulos em aberto sem baixas podem ser cancelados.")
        if await TitleSettlement.filter(title=title).exists():
            raise ValueError("Títulos com baixas não podem ser cancelados.")
        title.status = "cancelled"
        await title.save(update_fields=["status"])
        return title
