import pytest

from fincore.application.counterparties import normalize_tax_id, validate_counterparty


@pytest.mark.parametrize(
    ("value", "person_type", "expected"),
    [
        ("123.456.789-01", "individual", "12345678901"),
        ("12.345.678/0001-90", "company", "12345678000190"),
        ("", "individual", None),
    ],
)
def test_tax_id_is_normalized_without_punctuation(value, person_type, expected):
    assert normalize_tax_id(value, person_type) == expected


@pytest.mark.parametrize(
    ("person_type", "tax_id"),
    [("individual", "1234"), ("company", "12345678901"), ("unknown", "")],
)
def test_invalid_person_type_or_tax_id_is_rejected(person_type, tax_id):
    with pytest.raises(ValueError):
        validate_counterparty(
            person_type=person_type,
            name="Contato válido",
            tax_id=tax_id,
            is_customer=True,
            is_supplier=False,
        )


def test_contact_must_have_at_least_one_role_and_can_be_both():
    with pytest.raises(ValueError, match="Selecione cliente"):
        validate_counterparty(
            person_type="individual",
            name="Contato",
            tax_id="",
            is_customer=False,
            is_supplier=False,
        )

    assert validate_counterparty(
        person_type="company",
        name="Empresa",
        tax_id="",
        is_customer=True,
        is_supplier=True,
    ) == ("Empresa", None, "other")
