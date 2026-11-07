"""Caso de uso: sugerir a categoria de um lançamento a partir do histórico da organização."""

from fincore.domain.categorization import Suggestion, suggest, train
from fincore.infrastructure.models import Category, FinancialTransaction, Organization

HISTORY_LIMIT = 2000


async def suggest_category(
    organization: Organization, description: str, transaction_type: str
) -> tuple[Category, Suggestion] | None:
    if transaction_type not in {"income", "expense"} or not description.strip():
        return None
    history = (
        await FinancialTransaction.filter(
            organization=organization,
            transaction_type=transaction_type,
            reversal_of_id=None,
            category_id__not_isnull=True,
            category__is_active=True,
        )
        .order_by("-created_at")
        .limit(HISTORY_LIMIT)
        .values_list("description", "category_id")
    )
    result = suggest(train(history), description)
    if result is None:
        return None
    category = await Category.get_or_none(
        id=result.label, organization=organization, is_active=True
    )
    return (category, result) if category else None
