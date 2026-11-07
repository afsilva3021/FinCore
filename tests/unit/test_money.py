from decimal import Decimal

import pytest

from fincore.application.accounting import parse_line_amount
from fincore.application.finance import parse_money, parse_opening_balance
from fincore.application.titles import parse_adjustment
from fincore.domain.money import parse_api_amount


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.500", "1500.00"),
        ("1.500,00", "1500.00"),
        ("12.345.678", "12345678.00"),
        ("12.345.678,9", "12345678.90"),
        ("1500", "1500.00"),
        ("1500,5", "1500.50"),
        ("R$ 1.234,56", "1234.56"),
        ("1234.56", "1234.56"),
        ("1.5", "1.50"),
        ("0,015", "0.02"),
    ],
)
def test_parse_money_treats_dot_groups_as_thousands(raw: str, expected: str) -> None:
    assert parse_money(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["1,000.50", "1.23.4", "12,34,56", "abc", "", "-5", "1e3"])
def test_parse_money_rejects_ambiguous_or_invalid_formats(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_money(raw)


def test_optional_amount_parsers_share_thousands_rule() -> None:
    assert parse_opening_balance("2.000") == Decimal("2000.00")
    assert parse_opening_balance("") == Decimal("0.00")
    assert parse_adjustment("1.000,10") == Decimal("1000.10")
    assert parse_line_amount("3.000") == Decimal("3000.00")


def test_api_amount_uses_canonical_decimal_notation() -> None:
    assert parse_api_amount("1.500") == Decimal("1.50")
    assert parse_api_amount("250.00") == Decimal("250.00")
    for invalid in ("1.500,00", "1,5", "0", "-1.00"):
        with pytest.raises(ValueError):
            parse_api_amount(invalid)
