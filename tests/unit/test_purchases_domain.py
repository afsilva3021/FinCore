from decimal import Decimal as D

import pytest

from fincore.domain.purchases import (
    APPROVED,
    CANCELLED,
    DRAFT,
    PARTIALLY_RECEIVED,
    RECEIVED,
    ensure_can_approve,
    ensure_can_cancel,
    ensure_can_receive,
    ensure_within_remaining,
    line_total,
    order_total,
    status_after_receipts,
    validate_terms,
)


def test_totals_round_each_line_like_the_stock_entry() -> None:
    assert line_total(D("5"), D("3.333333")) == D("16.67")
    assert order_total([(D("10"), D("4")), (D("5"), D("3.333333"))]) == D("56.67")
    assert order_total([]) == D("0.00")
    with pytest.raises(ValueError, match="limite"):
        order_total([(D("99999999999"), D("999999"))])


def test_order_state_transitions() -> None:
    ensure_can_approve(DRAFT)
    for status in (APPROVED, PARTIALLY_RECEIVED, RECEIVED, CANCELLED):
        with pytest.raises(ValueError):
            ensure_can_approve(status)
    ensure_can_receive(APPROVED)
    ensure_can_receive(PARTIALLY_RECEIVED)
    for status in (DRAFT, RECEIVED, CANCELLED):
        with pytest.raises(ValueError):
            ensure_can_receive(status)
    ensure_can_cancel(DRAFT, False)
    ensure_can_cancel(APPROVED, False)
    for status, received in ((APPROVED, True), (PARTIALLY_RECEIVED, True), (RECEIVED, False)):
        with pytest.raises(ValueError):
            ensure_can_cancel(status, received)


def test_receipt_cannot_exceed_what_is_left() -> None:
    ensure_within_remaining(D("6"), D("10"), D("4"))
    for quantity in (D("6.0001"), D("0"), D("-1")):
        with pytest.raises(ValueError):
            ensure_within_remaining(quantity, D("10"), D("4"))


def test_status_follows_received_quantities() -> None:
    assert status_after_receipts([(D("10"), D("0")), (D("5"), D("0"))]) == APPROVED
    assert status_after_receipts([(D("10"), D("4")), (D("5"), D("0"))]) == PARTIALLY_RECEIVED
    assert status_after_receipts([(D("10"), D("10")), (D("5"), D("5"))]) == RECEIVED


def test_payment_terms_limits() -> None:
    validate_terms(1, 0)
    validate_terms(36, 365)
    for count, days in ((0, 30), (37, 30), (1, -1), (1, 366)):
        with pytest.raises(ValueError):
            validate_terms(count, days)
