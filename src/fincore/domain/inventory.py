"""Estoque: custo médio ponderado móvel e regras dos movimentos.

A posição de um produto em um depósito é o par (quantidade, valor total). O custo médio
é valor / quantidade. Guardar o valor total, e não o custo médio arredondado, mantém a
soma dos movimentos sempre igual ao valor da posição, centavo a centavo.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from fincore.domain.money import MAX_AMOUNT, quantize_money

QUANTITY_STEP = Decimal("0.0001")
UNIT_COST_STEP = Decimal("0.000001")
MAX_UNIT_COST = Decimal("999999999.999999")
ZERO_QUANTITY = Decimal("0.0000")
ZERO_VALUE = Decimal("0.00")

IN = "in"
OUT = "out"
DIRECTIONS = frozenset({IN, OUT})

# Motivos aceitos em cada sentido. `reversal` só nasce do estorno de outro movimento;
# `purchase` e `sale` ficam reservados para os documentos das próximas fases.
OPENING = "opening"
ADJUSTMENT = "adjustment"
TRANSFER = "transfer"
REVERSAL = "reversal"
MANUAL_REASONS = {IN: frozenset({OPENING, ADJUSTMENT}), OUT: frozenset({ADJUSTMENT})}
REASONS = frozenset({OPENING, ADJUSTMENT, TRANSFER, REVERSAL, "purchase", "sale"})
REASON_LABELS = {
    (IN, OPENING): "Saldo inicial",
    (IN, ADJUSTMENT): "Ajuste de entrada",
    (OUT, ADJUSTMENT): "Ajuste de saída",
    (IN, TRANSFER): "Transferência recebida",
    (OUT, TRANSFER): "Transferência enviada",
    (IN, REVERSAL): "Estorno de saída",
    (OUT, REVERSAL): "Estorno de entrada",
}


@dataclass(frozen=True, slots=True)
class Position:
    quantity: Decimal
    total_value: Decimal


@dataclass(frozen=True, slots=True)
class Applied:
    """Resultado de um movimento: o que ele vale e como a posição fica."""

    unit_cost: Decimal
    total_cost: Decimal
    position: Position


def average_cost(position: Position) -> Decimal:
    """Custo médio unitário (6 casas); zero quando não há saldo."""
    if position.quantity <= 0:
        return Decimal("0.000000")
    return (position.total_value / position.quantity).quantize(
        UNIT_COST_STEP, rounding=ROUND_HALF_UP
    )


def _positive_quantity(quantity: Decimal) -> Decimal:
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("A quantidade do movimento deve ser maior que zero.")
    if quantity != quantity.quantize(QUANTITY_STEP):
        raise ValueError("A quantidade aceita no máximo 4 casas decimais.")
    return quantity.quantize(QUANTITY_STEP)


def validate_unit_cost(unit_cost: Decimal) -> Decimal:
    if not unit_cost.is_finite() or unit_cost < 0 or unit_cost > MAX_UNIT_COST:
        raise ValueError("O custo unitário deve ser zero ou maior e respeitar o limite permitido.")
    if unit_cost != unit_cost.quantize(UNIT_COST_STEP):
        raise ValueError("O custo unitário aceita no máximo 6 casas decimais.")
    return unit_cost.quantize(UNIT_COST_STEP)


def apply_entry(position: Position, quantity: Decimal, unit_cost: Decimal) -> Applied:
    """Entrada: soma quantidade e valor; o custo médio passa a ser a média ponderada."""
    quantity = _positive_quantity(quantity)
    unit_cost = validate_unit_cost(unit_cost)
    total_cost = quantize_money(quantity * unit_cost)
    return _entry_with_value(position, quantity, unit_cost, total_cost)


def apply_entry_at_value(position: Position, quantity: Decimal, total_cost: Decimal) -> Applied:
    """Entrada por um valor total já conhecido (perna de destino de uma transferência)."""
    quantity = _positive_quantity(quantity)
    unit_cost = (total_cost / quantity).quantize(UNIT_COST_STEP, rounding=ROUND_HALF_UP)
    return _entry_with_value(position, quantity, unit_cost, total_cost)


def _entry_with_value(
    position: Position, quantity: Decimal, unit_cost: Decimal, total_cost: Decimal
) -> Applied:
    after = Position(position.quantity + quantity, position.total_value + total_cost)
    if after.quantity > Decimal("99999999999.9999") or after.total_value > MAX_AMOUNT:
        raise ValueError("O saldo em estoque ultrapassaria o limite permitido.")
    return Applied(unit_cost, total_cost, after)


def apply_exit(position: Position, quantity: Decimal) -> Applied:
    """Saída ao custo médio vigente. Recusa saída maior que o saldo.

    Quando a saída zera a quantidade, leva todo o valor restante, para não sobrar
    centavo de arredondamento em uma posição sem mercadoria.
    """
    quantity = _positive_quantity(quantity)
    if quantity > position.quantity:
        raise InsufficientStock(position.quantity)
    unit_cost = average_cost(position)
    if quantity == position.quantity:
        total_cost = position.total_value
    else:
        total_cost = min(quantize_money(quantity * unit_cost), position.total_value)
    return Applied(
        unit_cost,
        total_cost,
        Position(position.quantity - quantity, position.total_value - total_cost),
    )


def undo_entry(position: Position, quantity: Decimal, total_cost: Decimal) -> Applied:
    """Desfaz exatamente uma entrada que foi o último movimento da posição."""
    if quantity > position.quantity or total_cost > position.total_value:
        raise ValueError("O saldo atual não permite desfazer esta entrada.")
    unit_cost = (total_cost / quantity).quantize(UNIT_COST_STEP, rounding=ROUND_HALF_UP)
    return Applied(
        unit_cost,
        total_cost,
        Position(position.quantity - quantity, position.total_value - total_cost),
    )


def undo_exit(position: Position, quantity: Decimal, total_cost: Decimal) -> Applied:
    """Desfaz exatamente uma saída que foi o último movimento da posição."""
    unit_cost = (total_cost / quantity).quantize(UNIT_COST_STEP, rounding=ROUND_HALF_UP)
    return _entry_with_value(position, quantity, unit_cost, total_cost)


class InsufficientStock(ValueError):
    def __init__(self, available: Decimal) -> None:
        self.available = available
        super().__init__("Estoque insuficiente para esta saída.")


def ensure_manual_reason(direction: str, reason: str) -> None:
    if direction not in DIRECTIONS or reason not in MANUAL_REASONS[direction]:
        raise ValueError("Selecione um tipo de movimentação válido.")


def ensure_reversible(
    *, reason: str, already_reversed: bool, sequence: int, last_sequence: int
) -> None:
    """Só o último movimento da posição pode ser estornado, e uma única vez.

    Estornar um movimento antigo mudaria o custo médio de tudo o que veio depois; para
    corrigir o passado, lança-se um ajuste.
    """
    if reason == REVERSAL:
        raise ValueError("Um estorno não pode ser estornado; lance um novo movimento.")
    if already_reversed:
        raise ValueError("Este movimento já foi estornado.")
    if sequence != last_sequence:
        raise ValueError(
            "Só o último movimento do produto no depósito pode ser estornado. "
            "Para corrigir um movimento antigo, lance um ajuste."
        )


def reason_label(direction: str, reason: str) -> str:
    return REASON_LABELS.get((direction, reason), reason)
