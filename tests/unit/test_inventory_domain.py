from decimal import Decimal as D

import pytest

from fincore.domain.inventory import (
    IN,
    OUT,
    InsufficientStock,
    Position,
    apply_entry,
    apply_entry_at_value,
    apply_exit,
    average_cost,
    ensure_manual_reason,
    ensure_reversible,
    reason_label,
    undo_entry,
    undo_exit,
    validate_unit_cost,
)

EMPTY = Position(D("0"), D("0"))


def test_entries_build_a_weighted_average_cost() -> None:
    first = apply_entry(EMPTY, D("10"), D("5"))
    assert (first.total_cost, first.position) == (D("50.00"), Position(D("10.0000"), D("50.00")))
    second = apply_entry(first.position, D("10"), D("7"))
    assert second.position == Position(D("20.0000"), D("120.00"))
    assert average_cost(second.position) == D("6.000000")
    assert average_cost(EMPTY) == D("0.000000")


def test_exit_uses_current_average_and_keeps_it_unchanged() -> None:
    position = Position(D("20"), D("120.00"))
    exit_ = apply_exit(position, D("5"))
    assert (exit_.unit_cost, exit_.total_cost) == (D("6.000000"), D("30.00"))
    assert exit_.position == Position(D("15"), D("90.00"))
    assert average_cost(exit_.position) == D("6.000000")


def test_exits_never_lose_or_create_cents() -> None:
    # 3 unidades por R$ 10,00: custo médio 3,333333. Saindo uma a uma, a soma é 10,00.
    position = apply_entry(EMPTY, D("3"), D("3.333333")).position
    assert position.total_value == D("10.00")
    costs = []
    for _ in range(3):
        step = apply_exit(position, D("1"))
        costs.append(step.total_cost)
        position = step.position
    assert sum(costs) == D("10.00")
    assert position == Position(D("0"), D("0.00"))


def test_exit_larger_than_the_balance_is_refused() -> None:
    with pytest.raises(InsufficientStock) as error:
        apply_exit(Position(D("2"), D("10.00")), D("2.0001"))
    assert error.value.available == D("2")


@pytest.mark.parametrize("quantity", [D("0"), D("-1"), D("0.00001"), D("NaN")])
def test_movement_quantity_must_be_positive_with_four_places(quantity: D) -> None:
    with pytest.raises(ValueError):
        apply_entry(EMPTY, quantity, D("1"))
    with pytest.raises(ValueError):
        apply_exit(Position(D("10"), D("10.00")), quantity)


def test_unit_cost_limits() -> None:
    assert validate_unit_cost(D("0")) == D("0.000000")
    assert apply_entry(EMPTY, D("4"), D("0")).total_cost == D("0.00")
    for invalid in (D("-0.01"), D("0.0000001"), D("1000000000"), D("Infinity")):
        with pytest.raises(ValueError):
            validate_unit_cost(invalid)
    with pytest.raises(ValueError, match="limite"):
        apply_entry(Position(D("99999999999"), D("0")), D("1"), D("1"))


def test_transfer_entry_carries_the_exact_value() -> None:
    received = apply_entry_at_value(Position(D("1"), D("2.00")), D("3"), D("10.00"))
    assert received.position == Position(D("4.0000"), D("12.00"))
    assert received.total_cost == D("10.00") and received.unit_cost == D("3.333333")


def test_undo_restores_the_previous_position_exactly() -> None:
    before = Position(D("7.5000"), D("31.37"))
    entry = apply_entry(before, D("2.5"), D("4.123456"))
    assert undo_entry(entry.position, D("2.5"), entry.total_cost).position == before
    exit_ = apply_exit(before, D("3.3333"))
    assert undo_exit(exit_.position, D("3.3333"), exit_.total_cost).position == before
    with pytest.raises(ValueError):
        undo_entry(Position(D("1"), D("1.00")), D("2"), D("1.00"))


def test_only_the_last_non_reversal_movement_can_be_reversed_once() -> None:
    ok = {"reason": "adjustment", "already_reversed": False, "sequence": 4, "last_sequence": 4}
    ensure_reversible(**ok)
    with pytest.raises(ValueError, match="último"):
        ensure_reversible(**{**ok, "sequence": 3})
    with pytest.raises(ValueError, match="já foi estornado"):
        ensure_reversible(**{**ok, "already_reversed": True})
    with pytest.raises(ValueError, match="não pode ser estornado"):
        ensure_reversible(**{**ok, "reason": "reversal"})


def test_manual_reasons_and_labels() -> None:
    ensure_manual_reason(IN, "opening")
    ensure_manual_reason(IN, "adjustment")
    ensure_manual_reason(OUT, "adjustment")
    for direction, reason in (
        (OUT, "opening"),
        (IN, "transfer"),
        (IN, "reversal"),
        ("x", "adjustment"),
    ):
        with pytest.raises(ValueError):
            ensure_manual_reason(direction, reason)
    assert reason_label(IN, "opening") == "Saldo inicial"
    assert reason_label(OUT, "transfer") == "Transferência enviada"
