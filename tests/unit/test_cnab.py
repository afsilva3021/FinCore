import pytest

from fincore.application.cnab import CNABProfile, parse_cnab


def test_cnab_requires_explicit_bank_layout_and_version() -> None:
    profile = CNABProfile(bank_code="000", layout="240", version="não-configurado")
    with pytest.raises(ValueError, match="banco, padrão 240/400 e versão"):
        parse_cnab("", profile)
