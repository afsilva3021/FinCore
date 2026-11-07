"""Regras de domínio puras: sem banco, sem framework, executam em milissegundos."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from fincore.domain.accounting import (
    ensure_balanced,
    ensure_can_post,
    normal_balance,
    normalize_journal_lines,
)
from fincore.domain.titles import (
    calculate_settlement,
    ensure_can_cancel,
    ensure_can_settle,
    monthly_date,
    open_balance,
    split_installments,
)

D = Decimal


def test_installments_always_add_up_to_the_exact_total() -> None:
    parts = split_installments(D("100.00"), 3, date(2026, 1, 31))
    assert [amount for amount, _ in parts] == [D("33.33"), D("33.33"), D("33.34")]
    assert sum(amount for amount, _ in parts) == D("100.00")
    # 31/jan -> último dia de fevereiro -> 31/mar
    assert [due for _, due in parts] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]


def test_monthly_date_crosses_the_year() -> None:
    assert monthly_date(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_open_balance_is_derived_from_settlements() -> None:
    assert open_balance(D("100.00"), [D("30.00"), D("20.50")]) == D("49.50")


def test_partial_and_full_settlement_status() -> None:
    partial = calculate_settlement(title_type="receivable", balance=D("100"), applied=D("40"))
    assert (partial.net_amount, partial.new_status) == (D("40.00"), "partial")
    full = calculate_settlement(title_type="receivable", balance=D("60"), applied=D("60"))
    assert full.new_status == "settled"


def test_fees_increase_payables_and_reduce_receivables() -> None:
    common = {"balance": D("100"), "applied": D("100"), "discount": D("5"), "fees": D("2")}
    common["interest"], common["fine"] = D("1"), D("3")
    assert calculate_settlement(title_type="payable", **common).net_amount == D("101.00")
    assert calculate_settlement(title_type="receivable", **common).net_amount == D("97.00")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"applied": D("150")}, "exceder o saldo"),
        ({"applied": D("0")}, "maior que zero"),
        ({"applied": D("10"), "discount": D("11")}, "desconto não pode exceder"),
        ({"applied": D("10"), "fine": D("-1")}, "Multa"),
        ({"applied": D("10"), "discount": D("10")}, "valor líquido"),
    ],
)
def test_invalid_settlements_are_refused(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_settlement(title_type="receivable", balance=D("100"), **kwargs)


def test_title_state_transitions() -> None:
    ensure_can_settle("open")
    ensure_can_settle("partial")
    for status in ("cancelled", "settled"):
        with pytest.raises(ValueError):
            ensure_can_settle(status)
    ensure_can_cancel("open", has_settlements=False)
    with pytest.raises(ValueError):
        ensure_can_cancel("partial", has_settlements=True)
    with pytest.raises(ValueError):
        ensure_can_cancel("open", has_settlements=True)


def test_journal_lines_need_two_one_sided_lines() -> None:
    a, b = uuid4(), uuid4()
    clean = normalize_journal_lines([(a, D("10.005"), D("0")), (b, D("0"), D("10.01")), (a, 0, 0)])
    assert clean == [(a, D("10.01"), D("0.00")), (b, D("0.00"), D("10.01"))]
    with pytest.raises(ValueError, match="duas linhas"):
        normalize_journal_lines([(a, D("10"), D("0"))])
    with pytest.raises(ValueError, match="débito ou crédito"):
        normalize_journal_lines([(a, D("10"), D("10")), (b, D("0"), D("10"))])


def test_posting_requires_balanced_entry_in_one_currency() -> None:
    ensure_balanced([(D("10"), D("0")), (D("0"), D("10"))], {"BRL"})
    with pytest.raises(ValueError, match="débitos iguais"):
        ensure_balanced([(D("10"), D("0")), (D("0"), D("9"))], {"BRL"})
    with pytest.raises(ValueError, match="mesma moeda"):
        ensure_balanced([(D("10"), D("0")), (D("0"), D("10"))], {"BRL", "USD"})
    with pytest.raises(ValueError):
        ensure_can_post("posted")


def test_normal_balance_follows_account_nature() -> None:
    assert normal_balance("asset", D("100"), D("30")) == D("70")
    assert normal_balance("revenue", D("30"), D("100")) == D("70")
