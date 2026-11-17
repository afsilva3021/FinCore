"""Cadastros de produto: regras puras de código, código de barras, NCM e quantidades."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fincore.domain.money import normalize_brl

QUANTITY_STEP = Decimal("0.0001")
# Limite de NUMERIC(15, 4) no banco.
MAX_QUANTITY = Decimal("99999999999.9999")
MAX_UNIT_DECIMALS = 4
GTIN_LENGTHS = frozenset({8, 12, 13, 14})

_SKU = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,39}$")
_UNIT_CODE = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")
_WAREHOUSE_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,19}$")

# Unidades criadas com a organização: (sigla, nome, casas decimais).
DEFAULT_UNITS: tuple[tuple[str, str, int], ...] = (
    ("UN", "Unidade", 0),
    ("CX", "Caixa", 0),
    ("KG", "Quilograma", 3),
    ("L", "Litro", 3),
    ("M", "Metro", 2),
)
DEFAULT_WAREHOUSE = ("PRINCIPAL", "Depósito principal")


def normalize_sku(value: str) -> str:
    """Código do produto em maiúsculas; letras, números e . _ / - (até 40 caracteres)."""
    sku = value.strip().upper()
    if not _SKU.match(sku):
        raise ValueError(
            "Informe um código (SKU) de até 40 caracteres com letras, números, ponto, "
            "hífen, sublinhado ou barra, começando por letra ou número."
        )
    return sku


def normalize_unit_code(value: str) -> str:
    code = value.strip().upper()
    if not _UNIT_CODE.match(code):
        raise ValueError("Informe uma sigla de unidade de 1 a 6 letras ou números, como UN ou KG.")
    return code


def normalize_warehouse_code(value: str) -> str:
    code = value.strip().upper()
    if not _WAREHOUSE_CODE.match(code):
        raise ValueError(
            "Informe um código de depósito de até 20 caracteres com letras, números, "
            "ponto, hífen ou sublinhado."
        )
    return code


def validate_unit_decimals(value: int) -> int:
    if not 0 <= value <= MAX_UNIT_DECIMALS:
        raise ValueError(f"Casas decimais da unidade devem ficar entre 0 e {MAX_UNIT_DECIMALS}.")
    return value


def required_name(value: str, label: str, limit: int) -> str:
    name = " ".join(value.split())
    if not name or len(name) > limit:
        raise ValueError(f"Informe {label} de até {limit} caracteres.")
    return name


def gtin_check_digit(body: str) -> int:
    """Dígito verificador GTIN (módulo 10, pesos 3 e 1 a partir da direita)."""
    total = sum(
        int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body))
    )
    return (10 - total % 10) % 10


def normalize_barcode(value: str) -> str | None:
    """Valida GTIN-8/12/13/14 (EAN/UPC) pelo dígito verificador; vazio vira None."""
    cleaned = re.sub(r"[\s-]", "", value)
    if not cleaned:
        return None
    if not cleaned.isdigit() or len(cleaned) not in GTIN_LENGTHS:
        raise ValueError("Código de barras deve ser um GTIN/EAN de 8, 12, 13 ou 14 dígitos.")
    if gtin_check_digit(cleaned[:-1]) != int(cleaned[-1]):
        raise ValueError("Código de barras inválido: o dígito verificador não confere.")
    return cleaned


def normalize_ncm(value: str) -> str | None:
    """NCM com 8 dígitos (aceita pontos); só o formato é conferido."""
    cleaned = re.sub(r"[\s.]", "", value)
    if not cleaned:
        return None
    if not cleaned.isdigit() or len(cleaned) != 8:
        raise ValueError("NCM deve conter 8 dígitos.")
    return cleaned


def _checked_quantity(quantity: Decimal, label: str) -> Decimal:
    if not quantity.is_finite() or quantity < 0 or quantity > MAX_QUANTITY:
        raise ValueError(f"Confira {label}: deve ser zero ou maior e respeitar o limite permitido.")
    if quantity != quantity.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP):
        raise ValueError(f"Confira {label}: use no máximo {MAX_UNIT_DECIMALS} casas decimais.")
    return quantity.quantize(QUANTITY_STEP)


def parse_quantity(value: str, *, label: str = "a quantidade") -> Decimal:
    """Converte quantidade pt-BR ("1.500" = mil e quinhentos, "2,5") em `Decimal`.

    Vazio vale zero. Não aceita negativos e recusa mais de 4 casas decimais em vez de
    arredondar em silêncio.
    """
    try:
        quantity = Decimal(normalize_brl(value) or "0")
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(f"Informe {label} em formato válido, por exemplo 10 ou 2,5.") from exc
    return _checked_quantity(quantity, label)


def parse_api_quantity(value: str, *, label: str = "a quantidade") -> Decimal:
    """Converte a string decimal canônica da API ("2.5"), sem separador de milhar."""
    normalized = value.strip()
    if not re.fullmatch(r"\d+(\.\d+)?", normalized):
        raise ValueError(f'Informe {label} como string decimal, por exemplo "2.5".')
    return _checked_quantity(Decimal(normalized), label)


def fit_quantity_to_unit(
    quantity: Decimal, decimals: int, *, label: str = "a quantidade"
) -> Decimal:
    """Garante que a quantidade respeita as casas decimais da unidade de medida."""
    step = Decimal(1).scaleb(-decimals)
    if quantity != quantity.quantize(step, rounding=ROUND_HALF_UP):
        if decimals == 0:
            raise ValueError(f"Confira {label}: esta unidade só aceita números inteiros.")
        raise ValueError(
            f"Confira {label}: esta unidade aceita no máximo {decimals} casas decimais."
        )
    return quantity.quantize(QUANTITY_STEP)


def validate_group_parent(group_id: object, ancestors_of_parent: list[object]) -> None:
    """Um grupo não pode ficar abaixo de si mesmo nem de um descendente."""
    if group_id is not None and group_id in ancestors_of_parent:
        raise ValueError("Um grupo não pode ser subgrupo dele mesmo.")
