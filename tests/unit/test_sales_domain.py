from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from fincore.domain.sales import (
    CANCELLED,
    CONFIRMED,
    INVOICED,
    QUOTE,
    ensure_can_cancel,
    ensure_can_confirm,
    ensure_can_invoice,
    line_total,
    margin,
    margin_percent,
    order_total,
    validate_terms,
)

TODAY = date(2026, 11, 17)


def test_line_and_order_totals() -> None:
    assert line_total(D("3"), D("19.90")) == D("59.70")
    assert line_total(D("1.250"), D("18.90"), D("3.62")) == D("20.01")  # 23,625 -> 23,63 - 3,62
    assert order_total([(D("3"), D("19.90"), D("0")), (D("2"), D("5.00"), D("1.00"))]) == D("68.70")
    for quantity, price, discount in (
        (D("0"), D("1"), D("0")),
        (D("1"), D("-1"), D("0")),
        (D("1"), D("1.999"), D("0")),
        (D("1"), D("10"), D("-1")),
        (D("1"), D("10"), D("10.01")),
    ):
        with pytest.raises(ValueError):
            line_total(quantity, price, discount)


def test_order_state_transitions() -> None:
    ensure_can_confirm(QUOTE, None, TODAY)
    ensure_can_confirm(QUOTE, TODAY, TODAY)
    with pytest.raises(ValueError, match="venceu"):
        ensure_can_confirm(QUOTE, TODAY - timedelta(days=1), TODAY)
    for status in (CONFIRMED, INVOICED, CANCELLED):
        with pytest.raises(ValueError):
            ensure_can_confirm(status, None, TODAY)
    ensure_can_invoice(CONFIRMED)
    for status in (QUOTE, INVOICED, CANCELLED):
        with pytest.raises(ValueError):
            ensure_can_invoice(status)
    ensure_can_cancel(QUOTE)
    ensure_can_cancel(CONFIRMED)
    for status in (INVOICED, CANCELLED):
        with pytest.raises(ValueError):
            ensure_can_cancel(status)


def test_margin() -> None:
    assert margin(D("100.00"), D("62.50")) == D("37.50")
    assert margin_percent(D("100.00"), D("62.50")) == D("37.5")
    assert margin_percent(D("50.00"), D("60.00")) == D("-20.0")
    assert margin_percent(D("0.00"), D("0.00")) is None


def test_terms() -> None:
    validate_terms(1, 0)
    for count, days in ((0, 0), (37, 0), (1, 366)):
        with pytest.raises(ValueError):
            validate_terms(count, days)
