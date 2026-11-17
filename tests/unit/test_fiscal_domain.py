from decimal import Decimal as D

import pytest

from fincore.domain.fiscal import (
    SIMULATION_NOTICE,
    ensure_can_cancel,
    item_taxes,
    validate_cfop,
    validate_icms_cst,
    validate_rate,
)


def test_cfop_and_cst_formats() -> None:
    assert validate_cfop("5.102") == "5102"
    assert validate_cfop("6108") == "6108"
    for invalid in ("1102", "510", "51020", "abcd", ""):
        with pytest.raises(ValueError):
            validate_cfop(invalid)
    assert validate_icms_cst("00") == "00" and validate_icms_cst(" 102 ") == "102"
    for invalid in ("1", "1020", "A0", ""):
        with pytest.raises(ValueError):
            validate_icms_cst(invalid)


def test_rate_limits() -> None:
    assert validate_rate(D("18"), "ICMS") == D("18.00")
    assert validate_rate(D("1.65"), "PIS") == D("1.65")
    for invalid in (D("-0.01"), D("100.01"), D("1.655"), D("NaN")):
        with pytest.raises(ValueError):
            validate_rate(invalid, "ICMS")


def test_item_taxes_are_simple_percentages_rounded_to_cents() -> None:
    taxes = item_taxes(D("76.00"), D("18"), D("1.65"), D("7.6"))
    assert (taxes.icms, taxes.pis, taxes.cofins) == (D("13.68"), D("1.25"), D("5.78"))
    zero = item_taxes(D("76.00"), D("0"), D("0"), D("0"))
    assert (zero.icms, zero.pis, zero.cofins) == (D("0.00"), D("0.00"), D("0.00"))


def test_cancel_needs_an_issued_document_and_a_real_reason() -> None:
    ensure_can_cancel("issued", "Cliente desistiu da compra")
    with pytest.raises(ValueError, match="já foi cancelado"):
        ensure_can_cancel("cancelled", "Cliente desistiu da compra")
    for reason in (None, "", "curto"):
        with pytest.raises(ValueError, match="15 caracteres"):
            ensure_can_cancel("issued", reason)


def test_notice_says_it_is_a_simulation() -> None:
    assert "SIMULAÇÃO" in SIMULATION_NOTICE and "SEM VALOR FISCAL" in SIMULATION_NOTICE
