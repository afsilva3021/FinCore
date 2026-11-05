from decimal import Decimal
from pathlib import Path

import pytest

from fincore.application.reconciliation import parse_ofx

FIXTURE = Path(__file__).parents[1] / "fixtures" / "extrato-demo.ofx"


def test_parse_ofx_sgml_transactions_and_signed_amounts() -> None:
    parsed = parse_ofx(FIXTURE.read_text())
    assert len(parsed) == 2
    assert parsed[0].external_id == "demo-001"
    assert parsed[0].amount == Decimal("1250.75")
    assert parsed[1].amount == Decimal("-89.90")
    assert parsed[0].description == "Recebimento cliente"


@pytest.mark.parametrize(
    "content",
    ["", "<OFX></OFX>", "<OFX><STMTTRN><TRNAMT>1</STMTTRN></OFX>"],
)
def test_parse_ofx_rejects_empty_or_malformed_statements(content: str) -> None:
    with pytest.raises(ValueError):
        parse_ofx(content)


def test_parse_ofx_rejects_repeated_fitid() -> None:
    entry = "<STMTTRN><DTPOSTED>20260101<TRNAMT>1<FITID>x<NAME>Teste"
    with pytest.raises(ValueError, match="FITID repetido"):
        parse_ofx(f"<OFX><BANKTRANLIST>{entry}{entry}</BANKTRANLIST></OFX>")
