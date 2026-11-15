"""Regras contábeis puras: trava de período, partidas da baixa e demonstrativos."""

from datetime import date
from decimal import Decimal

import pytest

from fincore.domain.accounting import (
    build_balance_sheet,
    build_income_statement,
    build_trial_balance,
    ensure_period_open,
    settlement_journal_lines,
    validate_closing_date,
)
from fincore.domain.titles import calculate_settlement, status_after_settlement_reversal

D = Decimal


def test_closed_period_rejects_dates_up_to_the_closing_day() -> None:
    closed = date(2026, 10, 31)
    ensure_period_open(date(2026, 11, 1), closed)
    ensure_period_open(date(2020, 1, 1), None)
    with pytest.raises(ValueError, match="fechado até 31/10/2026"):
        ensure_period_open(closed, closed)
    with pytest.raises(ValueError, match="fechado"):
        ensure_period_open(date(2026, 1, 1), closed)
    with pytest.raises(ValueError, match="futuro"):
        validate_closing_date(date(2026, 11, 16), date(2026, 11, 15))
    assert validate_closing_date(None, date(2026, 11, 15)) is None


@pytest.mark.parametrize("title_type", ["receivable", "payable"])
def test_settlement_lines_are_balanced_and_split_by_nature(title_type: str) -> None:
    amounts = calculate_settlement(
        title_type=title_type,
        balance=D("100.00"),
        applied=D("100.00"),
        discount=D("5.00"),
        interest=D("3.00"),
        fine=D("2.00"),
        fees=D("1.00"),
    )
    lines = settlement_journal_lines(
        title_type=title_type,
        applied=amounts.applied,
        discount=amounts.discount,
        interest=amounts.interest,
        fine=amounts.fine,
        fees=amounts.fees,
        net_amount=amounts.net_amount,
    )
    by_role = {role: (debit, credit) for role, debit, credit in lines}
    assert sum(debit for _, debit, _ in lines) == sum(credit for _, _, credit in lines)
    if title_type == "receivable":
        assert amounts.net_amount == D("99.00")
        assert by_role == {
            "bank": (D("99.00"), D("0.00")),
            "discount_given": (D("5.00"), D("0.00")),
            "bank_fees": (D("1.00"), D("0.00")),
            "category": (D("0.00"), D("100.00")),
            "financial_income": (D("0.00"), D("5.00")),
        }
    else:
        assert amounts.net_amount == D("101.00")
        assert by_role == {
            "category": (D("100.00"), D("0.00")),
            "financial_expense": (D("5.00"), D("0.00")),
            "bank_fees": (D("1.00"), D("0.00")),
            "bank": (D("0.00"), D("101.00")),
            "discount_received": (D("0.00"), D("5.00")),
        }


def test_plain_settlement_has_only_bank_and_category() -> None:
    lines = settlement_journal_lines(
        title_type="receivable",
        applied=D("40.00"),
        discount=D("0.00"),
        interest=D("0.00"),
        fine=D("0.00"),
        fees=D("0.00"),
        net_amount=D("40.00"),
    )
    assert lines == [("bank", D("40.00"), D("0.00")), ("category", D("0.00"), D("40.00"))]
    with pytest.raises(ValueError):
        settlement_journal_lines(
            title_type="receivable",
            applied=D("40.00"),
            discount=D("0.00"),
            interest=D("0.00"),
            fine=D("0.00"),
            fees=D("0.00"),
            net_amount=D("39.00"),  # desequilibrado
        )


def test_title_status_after_settlement_reversal() -> None:
    assert status_after_settlement_reversal(False) == "open"
    assert status_after_settlement_reversal(True) == "partial"


def _statements():
    # (conta, natureza, débitos anteriores, créditos anteriores, débitos, créditos)
    rows = [
        ("banco", "asset", D("1000.00"), D("0.00"), D("500.00"), D("200.00")),
        ("capital", "equity", D("0.00"), D("1000.00"), D("0.00"), D("0.00")),
        ("vendas", "revenue", D("0.00"), D("0.00"), D("50.00"), D("500.00")),
        ("aluguel", "expense", D("0.00"), D("0.00"), D("150.00"), D("0.00")),
        ("emprestimo", "liability", D("0.00"), D("0.00"), D("0.00"), D("0.00")),
    ]
    return rows, build_trial_balance(rows)


def test_trial_balance_and_income_statement() -> None:
    _, trial = _statements()
    assert trial.is_balanced and trial.total_debit == D("700.00")
    assert [row.account for row in trial.rows] == ["banco", "capital", "vendas", "aluguel"]
    bank = trial.rows[0]
    assert (bank.opening, bank.closing) == (D("1000.00"), D("1300.00"))
    income = build_income_statement(trial)
    # O estorno a débito da receita (50) abate a própria receita.
    assert income.revenues == [("vendas", D("450.00"))]
    assert income.expenses == [("aluguel", D("150.00"))]
    assert income.result == D("300.00")


def test_balance_sheet_closes_with_the_accumulated_result() -> None:
    rows, _ = _statements()
    sheet = build_balance_sheet(
        (account, kind, before_debit + debit, before_credit + credit)
        for account, kind, before_debit, before_credit, debit, credit in rows
    )
    assert sheet.total_assets == D("1300.00")
    assert sheet.total_equity == D("1000.00") and sheet.total_liabilities == D("0.00")
    assert sheet.accumulated_result == D("300.00")
    assert sheet.is_balanced
