"""Compras: estados do pedido, totais e regras de recebimento."""

from collections.abc import Iterable
from decimal import Decimal

from fincore.domain.money import MAX_AMOUNT, quantize_money

DRAFT = "draft"
APPROVED = "approved"
PARTIALLY_RECEIVED = "partially_received"
RECEIVED = "received"
CANCELLED = "cancelled"
STATUSES = frozenset({DRAFT, APPROVED, PARTIALLY_RECEIVED, RECEIVED, CANCELLED})
STATUS_LABELS = {
    DRAFT: "Rascunho",
    APPROVED: "Aprovado",
    PARTIALLY_RECEIVED: "Recebido em parte",
    RECEIVED: "Recebido",
    CANCELLED: "Cancelado",
}
MAX_ITEMS = 50
MAX_INSTALLMENTS = 36
MAX_DUE_DAYS = 365


def line_total(quantity: Decimal, unit_cost: Decimal) -> Decimal:
    return quantize_money(quantity * unit_cost)


def order_total(lines: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """Soma dos itens, cada um arredondado para centavos (como entra no estoque)."""
    total = sum((line_total(quantity, cost) for quantity, cost in lines), Decimal("0.00"))
    if total > MAX_AMOUNT:
        raise ValueError("O total do pedido ultrapassa o limite permitido.")
    return total


def validate_terms(installment_count: int, first_due_days: int) -> None:
    if not 1 <= installment_count <= MAX_INSTALLMENTS:
        raise ValueError(f"O número de parcelas deve ficar entre 1 e {MAX_INSTALLMENTS}.")
    if not 0 <= first_due_days <= MAX_DUE_DAYS:
        raise ValueError(f"O prazo da primeira parcela deve ficar entre 0 e {MAX_DUE_DAYS} dias.")


def ensure_can_approve(status: str) -> None:
    if status != DRAFT:
        raise ValueError("Só um pedido em rascunho pode ser aprovado.")


def ensure_can_receive(status: str) -> None:
    if status not in {APPROVED, PARTIALLY_RECEIVED}:
        raise ValueError(
            "Só pedidos aprovados e ainda não recebidos por completo aceitam recebimento."
        )


def ensure_can_cancel(status: str, has_received: bool) -> None:
    if status not in {DRAFT, APPROVED} or has_received:
        raise ValueError(
            "Só pedidos sem mercadoria recebida podem ser cancelados; "
            "estorne os recebimentos antes."
        )


def remaining(ordered: Decimal, received: Decimal) -> Decimal:
    return ordered - received


def ensure_within_remaining(quantity: Decimal, ordered: Decimal, received: Decimal) -> None:
    if quantity <= 0:
        raise ValueError("A quantidade recebida deve ser maior que zero.")
    if quantity > remaining(ordered, received):
        raise ValueError("A quantidade recebida não pode passar do que falta receber do pedido.")


def status_after_receipts(items: Iterable[tuple[Decimal, Decimal]]) -> str:
    """Situação do pedido aprovado conforme (pedido, recebido) de cada item."""
    pairs = list(items)
    if all(received >= ordered for ordered, received in pairs):
        return RECEIVED
    if any(received > 0 for _, received in pairs):
        return PARTIALLY_RECEIVED
    return APPROVED
