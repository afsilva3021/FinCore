"""Consultas de relatórios financeiros para renderização e exportação."""

from datetime import date
from decimal import Decimal

from fincore.infrastructure.models import FinancialTransaction, Organization


async def transaction_report(
    organization: Organization, start: date, end: date
) -> tuple[list[FinancialTransaction], Decimal, Decimal]:
    if start > end:
        raise ValueError("A data inicial deve ser anterior à data final.")
    movements = (
        await FinancialTransaction.filter(
            organization=organization, competence_date__gte=start, competence_date__lte=end
        )
        .select_related("account", "category")
        .order_by("competence_date", "created_at")
    )
    income = sum(
        (
            item.amount
            for item in movements
            if item.transaction_type == "income" and item.status == "settled"
        ),
        Decimal("0.00"),
    )
    expenses = sum(
        (
            item.amount
            for item in movements
            if item.transaction_type == "expense" and item.status == "settled"
        ),
        Decimal("0.00"),
    )
    return movements, income, expenses
