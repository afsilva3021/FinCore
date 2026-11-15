"""Demonstrativos contábeis derivados do razão: balancete, DRE e balanço patrimonial.

Tudo é calculado a partir das linhas de lançamentos efetivados da organização; não há
saldo gravado. A montagem (pura) fica em `domain/accounting.py`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from tortoise.functions import Sum

from fincore.domain.accounting import (
    BalanceSheet,
    IncomeStatement,
    TrialBalance,
    build_balance_sheet,
    build_income_statement,
    build_trial_balance,
)
from fincore.infrastructure.models import JournalEntryLine, LedgerAccount, Organization

ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class FinancialStatements:
    start: date
    end: date
    trial_balance: TrialBalance[LedgerAccount]
    income_statement: IncomeStatement[LedgerAccount]
    balance_sheet: BalanceSheet[LedgerAccount]


async def _sums(organization: Organization, **date_filter: date) -> dict[UUID, tuple]:
    """Débitos e créditos efetivados por conta, numa agregação no banco."""
    rows = (
        await JournalEntryLine.filter(
            journal_entry__organization=organization,
            journal_entry__status="posted",
            **{f"journal_entry__entry_date__{key}": value for key, value in date_filter.items()},
        )
        .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
        .group_by("account_id")
        .values("account_id", "total_debit", "total_credit")
    )
    return {
        row["account_id"]: (row["total_debit"] or ZERO, row["total_credit"] or ZERO) for row in rows
    }


async def financial_statements(
    organization: Organization, start: date, end: date
) -> FinancialStatements:
    """Balancete e DRE do período [start, end] e balanço patrimonial na data final."""
    if start > end:
        raise ValueError("A data inicial deve ser anterior à data final.")
    accounts = await LedgerAccount.filter(organization=organization, is_synthetic=False).order_by(
        "code"
    )
    before = await _sums(organization, lt=start)
    period = await _sums(organization, gte=start, lte=end)
    none = (ZERO, ZERO)
    trial_balance = build_trial_balance(
        (
            account,
            account.account_type,
            *before.get(account.id, none),
            *period.get(account.id, none),
        )
        for account in accounts
    )
    balance_sheet = build_balance_sheet(
        (
            account,
            account.account_type,
            before.get(account.id, none)[0] + period.get(account.id, none)[0],
            before.get(account.id, none)[1] + period.get(account.id, none)[1],
        )
        for account in accounts
    )
    return FinancialStatements(
        start, end, trial_balance, build_income_statement(trial_balance), balance_sheet
    )
