"""Regras de partidas dobradas."""

from collections.abc import Hashable, Iterable
from decimal import Decimal

from fincore.domain.money import MAX_AMOUNT, quantize_money

ACCOUNT_TYPES = frozenset({"asset", "liability", "equity", "revenue", "expense"})
# Ativo e despesa crescem a débito; passivo, patrimônio líquido e receita, a crédito.
DEBIT_NATURE = frozenset({"asset", "expense"})
JOURNAL_STATUSES = frozenset({"draft", "posted"})


def normalize_journal_lines[AccountKey: Hashable](
    lines: Iterable[tuple[AccountKey, Decimal, Decimal]],
) -> list[tuple[AccountKey, Decimal, Decimal]]:
    """Arredonda (meio para cima, como no resto do sistema) e descarta linhas vazias.

    Exige ao menos duas linhas e, em cada uma, débito OU crédito positivo.
    """
    clean = [
        (account, quantize_money(Decimal(debit)), quantize_money(Decimal(credit)))
        for account, debit, credit in lines
        if debit or credit
    ]
    if len(clean) < 2:
        raise ValueError("Um lançamento precisa de pelo menos duas linhas.")
    for _, debit, credit in clean:
        if (
            debit < 0
            or credit < 0
            or debit > MAX_AMOUNT
            or credit > MAX_AMOUNT
            or (debit > 0 and credit > 0)
        ):
            raise ValueError("Cada linha deve ter um valor positivo em débito ou crédito.")
    return clean


def ensure_balanced(amounts: Iterable[tuple[Decimal, Decimal]], currencies: set[str]) -> None:
    """Efetivação: mesma moeda, ao menos duas linhas e soma de débitos = soma de créditos."""
    pairs = list(amounts)
    if len(pairs) < 2:
        raise ValueError("Um lançamento precisa de pelo menos duas linhas.")
    if len(currencies) != 1:
        raise ValueError("Débitos e créditos precisam estar na mesma moeda.")
    debit_total = sum((debit for debit, _ in pairs), Decimal("0.00"))
    credit_total = sum((credit for _, credit in pairs), Decimal("0.00"))
    if debit_total == 0 or debit_total != credit_total:
        raise ValueError("O lançamento precisa ter débitos iguais aos créditos.")


def ensure_can_post(status: str) -> None:
    if status != "draft":
        raise ValueError("O lançamento não pode ser efetivado neste estado.")


def normal_balance(account_type: str, debit: Decimal, credit: Decimal) -> Decimal:
    """Saldo no sentido natural da conta (positivo quando a conta "cresce")."""
    return debit - credit if account_type in DEBIT_NATURE else credit - debit
