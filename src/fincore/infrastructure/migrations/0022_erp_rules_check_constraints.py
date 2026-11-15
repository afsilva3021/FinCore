"""CHECK constraint do estorno de baixa: data e movimentação compensatória andam juntas."""

from tortoise import migrations
from tortoise.migrations import operations as ops

NAME = "ck_settlement_reversal"
CONDITION = "(reversed_at IS NULL) = (reversal_transaction_id IS NULL)"


class Migration(migrations.Migration):
    dependencies = [("models", "0021_erp_rules")]

    initial = False

    operations = [
        ops.RunSQL(
            # As colunas acabaram de nascer vazias (0021), então a regra já vale para tudo.
            sql=f'ALTER TABLE "titlesettlement" ADD CONSTRAINT {NAME} CHECK ({CONDITION});',
            reverse_sql=f'ALTER TABLE "titlesettlement" DROP CONSTRAINT IF EXISTS {NAME};',
        )
    ]
