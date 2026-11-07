"""Regras de títulos a pagar/receber: parcelas, saldo em aberto, baixas e cancelamento."""

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fincore.domain.money import MAX_AMOUNT, quantize_money

TITLE_TYPES = frozenset({"payable", "receivable"})
TITLE_STATUSES = frozenset({"open", "partial", "settled", "cancelled"})
PAYMENT_METHODS = frozenset({"cash", "bank_transfer", "pix", "boleto", "card", "other"})
MAX_INSTALLMENTS = 36


def monthly_date(start: date, offset: int) -> date:
    """Mesmo dia `offset` meses depois; em meses mais curtos, o último dia do mês."""
    month_index = start.month - 1 + offset
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def split_installments(total: Decimal, count: int, first_due: date) -> list[tuple[Decimal, date]]:
    """Divide o total em parcelas mensais; a última absorve o arredondamento.

    A soma das parcelas é sempre exatamente igual ao total.
    """
    if count < 1 or count > MAX_INSTALLMENTS:
        raise ValueError("O parcelamento deve ter entre 2 e 36 parcelas.")
    base = quantize_money(total / count)
    installments = []
    allocated = Decimal("0.00")
    for number in range(1, count + 1):
        amount = total - allocated if number == count else base
        allocated += amount
        installments.append((amount, monthly_date(first_due, number - 1)))
    return installments


def open_balance(original_amount: Decimal, applied_amounts: Iterable[Decimal]) -> Decimal:
    """Saldo em aberto derivado: valor original menos o que já foi aplicado em baixas."""
    return quantize_money(original_amount - sum(applied_amounts, Decimal("0.00")))


def validate_adjustment(value: Decimal, label: str) -> Decimal:
    amount = quantize_money(value)
    if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT:
        raise ValueError(f"{label} deve ser um valor igual ou superior a zero.")
    return amount


def ensure_can_settle(status: str) -> None:
    if status == "cancelled":
        raise ValueError("Títulos cancelados não podem ser baixados.")
    if status == "settled":
        raise ValueError("Este título já está liquidado.")


def ensure_can_cancel(status: str, has_settlements: bool) -> None:
    if status != "open":
        raise ValueError("Somente títulos em aberto sem baixas podem ser cancelados.")
    if has_settlements:
        raise ValueError("Títulos com baixas não podem ser cancelados.")


@dataclass(frozen=True, slots=True)
class SettlementAmounts:
    applied: Decimal
    discount: Decimal
    interest: Decimal
    fine: Decimal
    fees: Decimal
    net_amount: Decimal
    new_status: str


def calculate_settlement(
    *,
    title_type: str,
    balance: Decimal,
    applied: Decimal,
    discount: Decimal = Decimal("0.00"),
    interest: Decimal = Decimal("0.00"),
    fine: Decimal = Decimal("0.00"),
    fees: Decimal = Decimal("0.00"),
) -> SettlementAmounts:
    """Calcula o valor líquido de uma baixa e a nova situação do título.

    Valor líquido = aplicado − desconto + juros + multa ± tarifas. Tarifas aumentam o que
    se paga num título a pagar e reduzem o que se recebe num título a receber.
    """
    applied = quantize_money(applied)
    discount = validate_adjustment(discount, "Desconto")
    interest = validate_adjustment(interest, "Juros")
    fine = validate_adjustment(fine, "Multa")
    fees = validate_adjustment(fees, "Tarifas")
    if applied <= 0 or applied > MAX_AMOUNT:
        raise ValueError("O valor baixado deve ser maior que zero.")
    if applied > balance:
        raise ValueError("O valor baixado não pode exceder o saldo em aberto.")
    if discount > applied:
        raise ValueError("O desconto não pode exceder o valor aplicado ao título.")
    signed_fees = fees if title_type == "payable" else -fees
    net_amount = quantize_money(applied - discount + interest + fine + signed_fees)
    if net_amount <= 0 or net_amount > MAX_AMOUNT:
        raise ValueError("O valor líquido precisa ser maior que zero.")
    return SettlementAmounts(
        applied=applied,
        discount=discount,
        interest=interest,
        fine=fine,
        fees=fees,
        net_amount=net_amount,
        new_status="settled" if applied == balance else "partial",
    )
