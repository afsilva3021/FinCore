"""Vendas: estados do pedido, totais e margem."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from fincore.domain.money import MAX_AMOUNT, quantize_money

QUOTE = "quote"
CONFIRMED = "confirmed"
INVOICED = "invoiced"
CANCELLED = "cancelled"
STATUSES = frozenset({QUOTE, CONFIRMED, INVOICED, CANCELLED})
STATUS_LABELS = {
    QUOTE: "Orçamento",
    CONFIRMED: "Pedido confirmado",
    INVOICED: "Faturado",
    CANCELLED: "Cancelado",
}
MAX_ITEMS = 50
MAX_INSTALLMENTS = 36
MAX_DUE_DAYS = 365


def line_total(quantity: Decimal, unit_price: Decimal, discount: Decimal = Decimal("0")) -> Decimal:
    """Quantidade x preço, arredondado, menos o desconto da linha."""
    if quantity <= 0:
        raise ValueError("A quantidade de cada item deve ser maior que zero.")
    if unit_price < 0 or unit_price != quantize_money(unit_price):
        raise ValueError("O preço unitário deve ser zero ou maior, com até duas casas decimais.")
    if discount < 0 or discount != quantize_money(discount):
        raise ValueError("O desconto deve ser zero ou maior, com até duas casas decimais.")
    gross = quantize_money(quantity * unit_price)
    if discount > gross:
        raise ValueError("O desconto não pode ser maior que o valor do item.")
    return gross - discount


def order_total(lines: Iterable[tuple[Decimal, Decimal, Decimal]]) -> Decimal:
    total = sum((line_total(*line) for line in lines), Decimal("0.00"))
    if total > MAX_AMOUNT:
        raise ValueError("O total do pedido ultrapassa o limite permitido.")
    return total


def validate_terms(installment_count: int, first_due_days: int) -> None:
    if not 1 <= installment_count <= MAX_INSTALLMENTS:
        raise ValueError(f"O número de parcelas deve ficar entre 1 e {MAX_INSTALLMENTS}.")
    if not 0 <= first_due_days <= MAX_DUE_DAYS:
        raise ValueError(f"O prazo da primeira parcela deve ficar entre 0 e {MAX_DUE_DAYS} dias.")


def ensure_can_confirm(status: str, valid_until: date | None, today: date) -> None:
    if status != QUOTE:
        raise ValueError("Só um orçamento pode ser confirmado como pedido.")
    if valid_until is not None and today > valid_until:
        raise ValueError("Este orçamento venceu; crie um novo com os preços atuais.")


def ensure_can_invoice(status: str) -> None:
    if status != CONFIRMED:
        raise ValueError("Só um pedido confirmado e ainda não faturado pode ser faturado.")


def ensure_can_cancel(status: str) -> None:
    if status not in {QUOTE, CONFIRMED}:
        raise ValueError(
            "Pedido faturado não pode ser cancelado; estorne o faturamento (devolução) antes."
        )


def margin(total: Decimal, cost: Decimal) -> Decimal:
    """Margem bruta em reais (pode ser negativa quando se vende abaixo do custo)."""
    return total - cost


def margin_percent(total: Decimal, cost: Decimal) -> Decimal | None:
    """Margem bruta sobre a venda, em % com uma casa; None quando a venda é zero."""
    if total <= 0:
        return None
    return (margin(total, cost) * 100 / total).quantize(Decimal("0.1"))
