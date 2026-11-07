"""Dinheiro: precisão, limites e conversão de valores pt-BR e da API para `Decimal`."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
# Limite de NUMERIC(15, 2) no banco.
MAX_AMOUNT = Decimal("9999999999999.99")


def quantize_money(value: Decimal) -> Decimal:
    """Arredonda para centavos (meio para cima), a regra usada em todo o sistema."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# "1.234,56", "1234,56", "1.234" (milhar), "1234.56" (ponto decimal), "1234".
_BR_WITH_COMMA = re.compile(r"^(\d{1,3}(\.\d{3})+|\d+),\d+$")
_BR_THOUSANDS_ONLY = re.compile(r"^\d{1,3}(\.\d{3})+$")
_PLAIN_DECIMAL = re.compile(r"^\d+(\.\d+)?$")


def normalize_brl(value: str) -> str:
    """Normaliza um valor pt-BR para a notação decimal com ponto.

    O ponto é separador de milhar quando separa grupos de exatamente três dígitos
    ("1.500" = mil e quinhentos). Fora disso, um único ponto é tratado como decimal
    ("1.5", "1234.56"), o que mantém compatível quem digita no padrão internacional.
    Formatos mistos ou ambíguos ("1,000.50", "1.23.4") são rejeitados.
    """
    normalized = value.strip().replace("R$", "").replace(" ", "").replace(" ", "")
    if not normalized:
        return ""
    if "," in normalized:
        if not _BR_WITH_COMMA.match(normalized):
            raise ValueError("Formato monetário inválido.")
        return normalized.replace(".", "").replace(",", ".")
    if _BR_THOUSANDS_ONLY.match(normalized):
        return normalized.replace(".", "")
    if _PLAIN_DECIMAL.match(normalized):
        return normalized
    raise ValueError("Formato monetário inválido.")


def _to_decimal(normalized: str, message: str) -> Decimal:
    try:
        amount = Decimal(normalized).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(message) from exc
    if not amount.is_finite():
        raise ValueError(message)
    return amount


def parse_brl(
    value: str,
    *,
    allow_zero: bool,
    empty_as_zero: bool,
    invalid_message: str,
    range_message: str,
) -> Decimal:
    """Converte texto pt-BR em `Decimal` com duas casas, sem aceitar negativos."""
    try:
        normalized = normalize_brl(value)
    except ValueError as exc:
        raise ValueError(invalid_message) from exc
    if not normalized:
        if not empty_as_zero:
            raise ValueError(invalid_message)
        normalized = "0"
    amount = _to_decimal(normalized, invalid_message)
    minimum_ok = amount >= 0 if allow_zero else amount > 0
    if not minimum_ok or amount > MAX_AMOUNT:
        raise ValueError(range_message)
    return amount


def parse_api_amount(value: str) -> Decimal:
    """Converte a string decimal canônica da API ("1500.00"), sem separador de milhar."""
    normalized = value.strip()
    if not _PLAIN_DECIMAL.match(normalized):
        raise ValueError('Informe amount como string decimal, por exemplo "250.00".')
    amount = _to_decimal(normalized, "Informe um valor monetário válido.")
    if amount <= 0 or amount > MAX_AMOUNT:
        raise ValueError("O valor deve ser maior que zero e respeitar o limite permitido.")
    return amount
