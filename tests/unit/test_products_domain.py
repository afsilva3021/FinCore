from decimal import Decimal

import pytest

from fincore.application.members import AREA_CATALOG, AREA_FINANCE, can_write
from fincore.domain.products import (
    fit_quantity_to_unit,
    gtin_check_digit,
    normalize_barcode,
    normalize_ncm,
    normalize_sku,
    normalize_unit_code,
    normalize_warehouse_code,
    parse_api_quantity,
    parse_quantity,
    required_name,
    validate_group_parent,
    validate_unit_decimals,
)
from fincore.presentation.web import _format_brl, _format_quantity, write_area


def test_sku_is_uppercased_and_restricted_to_safe_characters() -> None:
    assert normalize_sku("  cam-001/a ") == "CAM-001/A"
    for invalid in ("", "-ABC", "COM ESPAÇO", "X" * 41, "ação"):
        with pytest.raises(ValueError):
            normalize_sku(invalid)


def test_unit_and_warehouse_codes() -> None:
    assert normalize_unit_code(" kg ") == "KG"
    assert normalize_warehouse_code("loja-01") == "LOJA-01"
    for invalid in ("", "1KG", "QUILOGR", "K G"):
        with pytest.raises(ValueError):
            normalize_unit_code(invalid)
    with pytest.raises(ValueError):
        normalize_warehouse_code("LOJA 01")
    assert validate_unit_decimals(4) == 4
    for invalid in (-1, 5):
        with pytest.raises(ValueError):
            validate_unit_decimals(invalid)


def test_required_name_collapses_spaces_and_enforces_limit() -> None:
    assert required_name("  Camiseta   básica ", "um nome", 160) == "Camiseta básica"
    for invalid in ("   ", "x" * 161):
        with pytest.raises(ValueError):
            required_name(invalid, "um nome", 160)


@pytest.mark.parametrize(
    "code",
    ["7891000100103", "96385074", "036000291452", "17891000100100"],
    ids=["ean13", "ean8", "upc-a", "gtin14"],
)
def test_valid_barcodes_are_accepted(code: str) -> None:
    assert gtin_check_digit(code[:-1]) == int(code[-1])
    assert normalize_barcode(code) == code


def test_barcode_rejects_wrong_check_digit_length_and_letters() -> None:
    assert normalize_barcode("") is None
    assert normalize_barcode("789 1000-100103") == "7891000100103"
    for invalid in ("7891000100104", "12345", "78910001001AB", "1234567890"):
        with pytest.raises(ValueError):
            normalize_barcode(invalid)


def test_ncm_keeps_eight_digits() -> None:
    assert normalize_ncm("6109.10.00") == "61091000"
    assert normalize_ncm(" ") is None
    for invalid in ("6109", "6109100A", "610910000"):
        with pytest.raises(ValueError):
            normalize_ncm(invalid)


def test_quantity_parsing_follows_ptbr_and_never_rounds_silently() -> None:
    assert parse_quantity("1.500") == Decimal("1500.0000")
    assert parse_quantity("2,5") == Decimal("2.5000")
    assert parse_quantity("") == Decimal("0.0000")
    assert parse_quantity("0,1234") == Decimal("0.1234")
    for invalid in ("-1", "abc", "1,000.5", "0,12345", "100000000000"):
        with pytest.raises(ValueError):
            parse_quantity(invalid)


def test_api_quantity_uses_canonical_decimal_notation() -> None:
    assert parse_api_quantity("2.5") == Decimal("2.5000")
    assert parse_api_quantity("1.500") == Decimal("1.5000")
    for invalid in ("2,5", "-1", "1e3", "", "0.00001"):
        with pytest.raises(ValueError):
            parse_api_quantity(invalid)


def test_quantity_must_fit_the_unit_decimal_places() -> None:
    assert fit_quantity_to_unit(Decimal("3"), 0) == Decimal("3.0000")
    assert fit_quantity_to_unit(Decimal("2.125"), 3) == Decimal("2.1250")
    with pytest.raises(ValueError, match="inteiros"):
        fit_quantity_to_unit(Decimal("2.5"), 0)
    with pytest.raises(ValueError, match="2 casas"):
        fit_quantity_to_unit(Decimal("2.125"), 2)


def test_group_cannot_be_its_own_ancestor() -> None:
    validate_group_parent(None, ["a", "b"])
    validate_group_parent("c", ["a", "b"])
    with pytest.raises(ValueError):
        validate_group_parent("b", ["a", "b"])


def test_formatting_filters() -> None:
    assert _format_brl(Decimal("1234.5")) == "1.234,50"
    assert _format_brl(Decimal("0")) == "0,00"
    assert _format_quantity(Decimal("2.5000")) == "2,5"
    assert _format_quantity(Decimal("1500.0000")) == "1.500"
    assert _format_quantity(Decimal("0.0000")) == "0"


def test_each_profile_writes_only_its_own_area() -> None:
    assert write_area("/products") == AREA_CATALOG
    assert write_area("/products/123/update") == AREA_CATALOG
    assert write_area("/product-settings/units") == AREA_CATALOG
    assert write_area("/api/v1/products") == AREA_CATALOG
    # Prefixo parecido não entra na área por engano.
    assert write_area("/productsX") == AREA_FINANCE
    assert write_area("/accounts") == AREA_FINANCE

    assert can_write("owner", AREA_FINANCE) and can_write("owner", AREA_CATALOG)
    assert can_write("finance", AREA_FINANCE) and not can_write("finance", AREA_CATALOG)
    assert can_write("stock", AREA_CATALOG) and not can_write("stock", AREA_FINANCE)
    assert not can_write("viewer", AREA_FINANCE) and not can_write("viewer", AREA_CATALOG)
    assert not can_write("desconhecido", AREA_CATALOG)
