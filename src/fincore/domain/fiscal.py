"""Fiscal simulado: validação de formato dos dados tributários e cálculo ilustrativo.

Nada aqui tem valor fiscal. Os códigos e alíquotas são informados pelo usuário e só o
formato é conferido; o cálculo é uma multiplicação simples, sem regras de base reduzida,
substituição tributária, DIFAL ou qualquer outra regra da legislação.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from fincore.domain.money import quantize_money

SIMULATION_NOTICE = "SIMULAÇÃO — SEM VALOR FISCAL"
MAX_RATE = Decimal("100")
DEFAULT_PROFILE = {
    "name": "Venda de mercadoria (simulação)",
    "cfop": "5102",
    "icms_cst": "102",
    "icms_rate": Decimal("0"),
    "pis_rate": Decimal("0"),
    "cofins_rate": Decimal("0"),
}


def validate_cfop(value: str) -> str:
    """CFOP de saída: 4 dígitos começando por 5, 6 ou 7."""
    cfop = re.sub(r"\D", "", value)
    if not re.fullmatch(r"[567]\d{3}", cfop):
        raise ValueError("CFOP de saída deve ter 4 dígitos e começar por 5, 6 ou 7.")
    return cfop


def validate_icms_cst(value: str) -> str:
    """CST (2 dígitos) ou CSOSN (3 dígitos); só o formato é conferido."""
    code = value.strip()
    if not re.fullmatch(r"\d{2,3}", code):
        raise ValueError("Informe o CST (2 dígitos) ou o CSOSN (3 dígitos) do ICMS.")
    return code


def validate_rate(value: Decimal, label: str) -> Decimal:
    if not value.is_finite() or value < 0 or value > MAX_RATE:
        raise ValueError(f"A alíquota de {label} deve ficar entre 0 e 100%.")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError(f"A alíquota de {label} aceita no máximo duas casas decimais.")
    return value.quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class ItemTaxes:
    icms: Decimal
    pis: Decimal
    cofins: Decimal


def item_taxes(
    total: Decimal, icms_rate: Decimal, pis_rate: Decimal, cofins_rate: Decimal
) -> ItemTaxes:
    """Imposto ilustrativo de um item: valor do item x alíquota, arredondado em centavos.

    Os impostos são "por dentro": ficam destacados, mas não aumentam o total do documento.
    """
    return ItemTaxes(
        icms=quantize_money(total * icms_rate / 100),
        pis=quantize_money(total * pis_rate / 100),
        cofins=quantize_money(total * cofins_rate / 100),
    )


def ensure_can_cancel(status: str, reason: str | None) -> None:
    if status != "issued":
        raise ValueError("Este documento já foi cancelado.")
    if not reason or len(reason) < 15:
        raise ValueError("Informe o motivo do cancelamento com pelo menos 15 caracteres.")
