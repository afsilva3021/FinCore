"""Consultas de relatórios financeiros para renderização e exportação."""

from datetime import date
from decimal import Decimal

from tortoise.functions import Sum

from fincore.infrastructure.models import FinancialTransaction, Organization

ZERO = Decimal("0.00")


async def realized_totals(
    organization: Organization,
    start: date | None = None,
    end: date | None = None,
) -> tuple[Decimal, Decimal]:
    """Retorna (receitas, despesas) realizadas, líquidas de estornos.

    O estorno de uma receita é gravado como movimento de despesa com `reversal_of`
    preenchido (e vice-versa). Somar apenas por `transaction_type` contaria o estorno
    como despesa nova; aqui ele é abatido da natureza do movimento original.
    Transferências não entram nos totais de receita e despesa.
    """
    query = FinancialTransaction.filter(
        organization=organization,
        status="settled",
        transaction_type__in=["income", "expense"],
    )
    if start is not None:
        query = query.filter(competence_date__gte=start)
    if end is not None:
        query = query.filter(competence_date__lte=end)

    async def totals_by_type(reversals: bool) -> dict[str, Decimal]:
        scoped = (
            query.exclude(reversal_of_id=None) if reversals else query.filter(reversal_of_id=None)
        )
        rows = (
            await scoped.annotate(total=Sum("amount"))
            .group_by("transaction_type")
            .values("transaction_type", "total")
        )
        return {row["transaction_type"]: Decimal(row["total"] or ZERO) for row in rows}

    regular = await totals_by_type(reversals=False)
    reversed_ = await totals_by_type(reversals=True)
    income = regular.get("income", ZERO) - reversed_.get("expense", ZERO)
    expenses = regular.get("expense", ZERO) - reversed_.get("income", ZERO)
    return income.quantize(ZERO), expenses.quantize(ZERO)


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
    income, expenses = await realized_totals(organization, start, end)
    return movements, income, expenses
