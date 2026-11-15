"""Regras de partidas dobradas."""

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from datetime import date
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


# --- Trava de período -----------------------------------------------------------------


def ensure_period_open(entry_date: date, closed_until: date | None) -> None:
    """Período fechado não recebe lançamentos: a data precisa ser posterior ao fechamento."""
    if closed_until is not None and entry_date <= closed_until:
        raise ValueError(
            f"O período contábil está fechado até {closed_until.strftime('%d/%m/%Y')}; "
            "use uma data posterior ou reabra o período."
        )


def validate_closing_date(closed_until: date | None, today: date) -> date | None:
    if closed_until is not None and closed_until > today:
        raise ValueError("A data de fechamento do período não pode estar no futuro.")
    return closed_until


# --- Baixa de título: partidas por natureza -------------------------------------------

# Papéis das contas numa baixa; a aplicação resolve cada papel para uma conta do razão.
ROLE_BANK = "bank"
ROLE_CATEGORY = "category"
ROLE_FINANCIAL_INCOME = "financial_income"
ROLE_FINANCIAL_EXPENSE = "financial_expense"
ROLE_DISCOUNT_GIVEN = "discount_given"
ROLE_DISCOUNT_RECEIVED = "discount_received"
ROLE_BANK_FEES = "bank_fees"


def settlement_journal_lines(
    *,
    title_type: str,
    applied: Decimal,
    discount: Decimal,
    interest: Decimal,
    fine: Decimal,
    fees: Decimal,
    net_amount: Decimal,
) -> list[tuple[str, Decimal, Decimal]]:
    """Partidas (papel, débito, crédito) da baixa de um título no regime de caixa.

    O principal aplicado vai para a conta da categoria; juros e multa, desconto e tarifa
    bancária vão para contas próprias, em vez de inflar ou reduzir a receita/despesa da
    categoria. O banco recebe (ou paga) o valor líquido. Linhas zeradas são omitidas.

    A receber:  D banco (líquido) + D descontos concedidos + D tarifas
                = C categoria (aplicado) + C receitas financeiras (juros + multa)
    A pagar:    D categoria (aplicado) + D despesas financeiras (juros + multa) + D tarifas
                = C banco (líquido) + C descontos obtidos
    """
    zero = Decimal("0.00")
    charges = quantize_money(interest + fine)
    if title_type == "receivable":
        lines = [
            (ROLE_BANK, net_amount, zero),
            (ROLE_DISCOUNT_GIVEN, discount, zero),
            (ROLE_BANK_FEES, fees, zero),
            (ROLE_CATEGORY, zero, applied),
            (ROLE_FINANCIAL_INCOME, zero, charges),
        ]
    elif title_type == "payable":
        lines = [
            (ROLE_CATEGORY, applied, zero),
            (ROLE_FINANCIAL_EXPENSE, charges, zero),
            (ROLE_BANK_FEES, fees, zero),
            (ROLE_BANK, zero, net_amount),
            (ROLE_DISCOUNT_RECEIVED, zero, discount),
        ]
    else:
        raise ValueError("Tipo de título inválido.")
    lines = [line for line in lines if line[1] or line[2]]
    ensure_balanced([(debit, credit) for _, debit, credit in lines], {"BRL"})
    return lines


# --- Demonstrativos -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrialBalanceRow[AccountKey]:
    account: AccountKey
    account_type: str
    opening: Decimal  # saldo anterior, no sentido natural da conta
    debit: Decimal
    credit: Decimal
    closing: Decimal


@dataclass(frozen=True, slots=True)
class TrialBalance[AccountKey]:
    rows: list[TrialBalanceRow[AccountKey]]
    total_debit: Decimal
    total_credit: Decimal

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit


def build_trial_balance[AccountKey](
    movements: Iterable[tuple[AccountKey, str, Decimal, Decimal, Decimal, Decimal]],
) -> TrialBalance[AccountKey]:
    """Balancete de verificação.

    Cada item: (conta, natureza, débitos anteriores, créditos anteriores, débitos do
    período, créditos do período). Contas sem saldo nem movimento são omitidas.
    """
    rows = []
    total_debit = total_credit = Decimal("0.00")
    for account, account_type, debit_before, credit_before, debit, credit in movements:
        opening = normal_balance(account_type, debit_before, credit_before)
        closing = opening + normal_balance(account_type, debit, credit)
        if not (opening or debit or credit):
            continue
        rows.append(TrialBalanceRow(account, account_type, opening, debit, credit, closing))
        total_debit += debit
        total_credit += credit
    return TrialBalance(rows, total_debit, total_credit)


@dataclass(frozen=True, slots=True)
class IncomeStatement[AccountKey]:
    revenues: list[tuple[AccountKey, Decimal]]
    expenses: list[tuple[AccountKey, Decimal]]
    total_revenue: Decimal
    total_expense: Decimal

    @property
    def result(self) -> Decimal:
        return self.total_revenue - self.total_expense


def build_income_statement[AccountKey](
    trial_balance: TrialBalance[AccountKey],
) -> IncomeStatement[AccountKey]:
    """DRE do período: movimento das contas de receita menos o das contas de despesa."""
    revenues = [
        (row.account, row.credit - row.debit)
        for row in trial_balance.rows
        if row.account_type == "revenue" and row.credit != row.debit
    ]
    expenses = [
        (row.account, row.debit - row.credit)
        for row in trial_balance.rows
        if row.account_type == "expense" and row.credit != row.debit
    ]
    return IncomeStatement(
        revenues,
        expenses,
        sum((amount for _, amount in revenues), Decimal("0.00")),
        sum((amount for _, amount in expenses), Decimal("0.00")),
    )


@dataclass(frozen=True, slots=True)
class BalanceSheet[AccountKey]:
    assets: list[tuple[AccountKey, Decimal]]
    liabilities: list[tuple[AccountKey, Decimal]]
    equity: list[tuple[AccountKey, Decimal]]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    # Receitas − despesas acumuladas até a data, ainda não transferidas ao patrimônio.
    accumulated_result: Decimal

    @property
    def total_liabilities_and_equity(self) -> Decimal:
        return self.total_liabilities + self.total_equity + self.accumulated_result

    @property
    def is_balanced(self) -> bool:
        return self.total_assets == self.total_liabilities_and_equity


def build_balance_sheet[AccountKey](
    balances: Iterable[tuple[AccountKey, str, Decimal, Decimal]],
) -> BalanceSheet[AccountKey]:
    """Balanço patrimonial na data: (conta, natureza, débitos e créditos acumulados).

    O resultado acumulado (receitas − despesas) entra como linha própria do patrimônio,
    sem exigir lançamento de encerramento; assim Ativo = Passivo + PL + Resultado.
    """
    groups: dict[str, list[tuple[AccountKey, Decimal]]] = {
        "asset": [],
        "liability": [],
        "equity": [],
    }
    result = Decimal("0.00")
    for account, account_type, debit, credit in balances:
        balance = normal_balance(account_type, debit, credit)
        if account_type == "revenue":
            result += balance
        elif account_type == "expense":
            result -= balance
        elif balance:
            groups[account_type].append((account, balance))

    def total(key: str) -> Decimal:
        return sum((amount for _, amount in groups[key]), Decimal("0.00"))

    return BalanceSheet(
        groups["asset"],
        groups["liability"],
        groups["equity"],
        total("asset"),
        total("liability"),
        total("equity"),
        result,
    )
