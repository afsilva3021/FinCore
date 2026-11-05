from decimal import Decimal

import pytest

from fincore.application.simulated_integrations import simulate_adapter


@pytest.mark.parametrize("adapter", ["pix", "boleto", "card", "webhook"])
def test_payment_adapters_return_local_simulation(adapter: str) -> None:
    result = simulate_adapter(adapter, Decimal("12.34"))
    assert result.status == "simulated"
    assert result.amount == Decimal("12.34")
    assert result.reference.startswith("DEMO-")
    assert "Nenhum pagamento" in result.message


def test_open_finance_simulates_consent_without_amount() -> None:
    assert simulate_adapter("open_finance").status == "simulated"


def test_unknown_adapter_or_invalid_amount_is_rejected() -> None:
    with pytest.raises(ValueError):
        simulate_adapter("production")
    with pytest.raises(ValueError):
        simulate_adapter("pix", Decimal("0"))
