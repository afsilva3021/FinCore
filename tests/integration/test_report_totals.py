import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from tortoise import Tortoise

from fincore.application.finance import (
    create_account,
    create_movement,
    create_transfer,
    reverse_movement,
)
from fincore.application.identity import register_user
from fincore.application.reports import realized_totals, transaction_report
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import FinancialTransaction, Membership

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_reversals_net_out_of_income_and_expense_totals() -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Relatório", f"relatorio-{suffix}@example.test", "senha-relatorio-123", "Org Relatório"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None
    organization = await membership.organization
    today = date.today()
    cash = await create_account(organization, user, "Caixa", "cash", Decimal("0.00"))
    reserve = await create_account(organization, user, "Reserva", "bank", Decimal("0.00"))

    async def movement(kind: str, amount: str) -> FinancialTransaction:
        return await create_movement(
            organization=organization,
            user=user,
            operation_key=uuid4(),
            account_id=cash.id,
            category_id=None,
            transaction_type=kind,
            description=f"{kind} {amount}",
            amount=Decimal(amount),
            competence_date=today,
            due_date=None,
            settled=True,
        )

    income = await movement("income", "1000.00")
    await movement("income", "250.00")
    expense = await movement("expense", "300.00")
    await movement("expense", "40.00")
    await reverse_movement(organization, user, income.id, uuid4(), today)
    await reverse_movement(organization, user, expense.id, uuid4(), today)
    transfer_id = await create_transfer(
        organization=organization,
        user=user,
        operation_key=uuid4(),
        source_id=cash.id,
        target_id=reserve.id,
        amount=Decimal("70.00"),
        description="Transferência",
        competence_date=today,
    )
    transfer_out = await FinancialTransaction.get(
        transfer_id=transfer_id, transaction_type="transfer_out"
    )
    await reverse_movement(organization, user, transfer_out.id, uuid4(), today)

    # Sem a correção: receitas 1250 + 300 (estorno da despesa) e despesas 340 + 1000.
    assert await realized_totals(organization) == (Decimal("250.00"), Decimal("40.00"))
    _, report_income, report_expenses = await transaction_report(organization, today, today)
    assert (report_income, report_expenses) == (Decimal("250.00"), Decimal("40.00"))
