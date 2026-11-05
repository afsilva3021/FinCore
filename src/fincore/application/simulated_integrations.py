"""Contratos locais de demonstração; nenhum adapter acessa terceiros."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

SUPPORTED_ADAPTERS = {"pix", "boleto", "card", "webhook", "open_finance"}


@dataclass(frozen=True)
class SimulationResult:
    adapter: str
    reference: str
    status: str
    message: str
    amount: Decimal | None


def simulate_adapter(adapter: str, amount: Decimal | None = None) -> SimulationResult:
    """Produces a fake result for UI/contracts without network or payment side effects."""
    if adapter not in SUPPORTED_ADAPTERS:
        raise ValueError("Selecione um adapter de demonstração disponível.")
    if adapter != "open_finance" and (
        amount is None
        or not amount.is_finite()
        or amount <= 0
        or amount > Decimal("9999999999999.99")
    ):
        raise ValueError("Informe um valor positivo para simular esta operação.")
    names = {
        "pix": "Pix simulado",
        "boleto": "Boleto simulado",
        "card": "Autorização de cartão simulada",
        "webhook": "Evento de webhook fictício",
        "open_finance": "Consentimento de Open Finance fictício",
    }
    return SimulationResult(
        adapter=adapter,
        reference=f"DEMO-{uuid4().hex[:12].upper()}",
        status="simulated",
        message=(
            f"{names[adapter]} gerado localmente. Nenhum pagamento ou compartilhamento ocorreu."
        ),
        amount=amount,
    )
