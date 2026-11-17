"""Estoque (ERP, fase 9): posições (`stockbalance`) e movimentos (`stockmovement`).

Aditiva: cria duas tabelas e não altera dados existentes. Além das tabelas:

- `CHECK` constraints de quantidades, valores, sentidos e motivos;
- gatilho que recusa `UPDATE` e `DELETE` em `stockmovement`: movimento efetivado é
  imutável também para quem acessa o banco direto. Correção é por estorno ou ajuste.
"""

from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

FORWARD_SQL = """
ALTER TABLE "stockbalance" ADD CONSTRAINT ck_stock_balance_values CHECK (
    quantity >= 0 AND total_value >= 0 AND movement_count >= 0
    AND (quantity > 0 OR total_value = 0)
);
ALTER TABLE "stockmovement" ADD CONSTRAINT ck_stock_movement_values CHECK (
    quantity > 0 AND unit_cost >= 0 AND total_cost >= 0
    AND balance_quantity >= 0 AND balance_value >= 0 AND sequence > 0
);
ALTER TABLE "stockmovement" ADD CONSTRAINT ck_stock_movement_kind CHECK (
    direction IN ('in', 'out')
    AND reason IN ('opening', 'adjustment', 'transfer', 'reversal', 'purchase', 'sale')
    AND (reason <> 'opening' OR direction = 'in')
    AND ((reason = 'reversal') = (reversal_of_id IS NOT NULL))
    AND (reason <> 'transfer' OR transfer_id IS NOT NULL)
    AND reversal_of_id <> id
);
CREATE INDEX ix_stock_movement_org_created ON "stockmovement" (organization_id, created_at DESC);

CREATE FUNCTION fincore_stock_movement_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Movimento de estoque é imutável; use estorno ou ajuste.'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_stock_movement_immutable
    BEFORE UPDATE OR DELETE ON "stockmovement"
    FOR EACH ROW EXECUTE FUNCTION fincore_stock_movement_immutable();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS trg_stock_movement_immutable ON "stockmovement";
DROP FUNCTION IF EXISTS fincore_stock_movement_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [('models', '0019_product_catalog')]

    initial = False

    operations = [
        ops.CreateModel(
            name='StockBalance',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='stock_balances', on_delete=OnDelete.CASCADE)),
                ('product', fields.ForeignKeyField('models.Product', source_field='product_id', db_constraint=True, to_field='id', related_name='stock_balances', on_delete=OnDelete.RESTRICT)),
                ('warehouse', fields.ForeignKeyField('models.Warehouse', source_field='warehouse_id', db_constraint=True, to_field='id', related_name='stock_balances', on_delete=OnDelete.RESTRICT)),
                ('quantity', fields.DecimalField(default=0, max_digits=15, decimal_places=4)),
                ('total_value', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('movement_count', fields.IntField(default=0)),
                ('updated_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'stockbalance', 'app': 'models', 'unique_together': (('product', 'warehouse'),), 'pk_attr': 'id', 'table_description': 'Posição de um produto em um depósito.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='StockMovement',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='stock_movements', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_stock_movements', on_delete=OnDelete.RESTRICT)),
                ('product', fields.ForeignKeyField('models.Product', source_field='product_id', db_constraint=True, to_field='id', related_name='stock_movements', on_delete=OnDelete.RESTRICT)),
                ('warehouse', fields.ForeignKeyField('models.Warehouse', source_field='warehouse_id', db_constraint=True, to_field='id', related_name='stock_movements', on_delete=OnDelete.RESTRICT)),
                ('sequence', fields.IntField()),
                ('direction', fields.CharField(max_length=3)),
                ('reason', fields.CharField(max_length=16)),
                ('quantity', fields.DecimalField(max_digits=15, decimal_places=4)),
                ('unit_cost', fields.DecimalField(max_digits=15, decimal_places=6)),
                ('total_cost', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('balance_quantity', fields.DecimalField(max_digits=15, decimal_places=4)),
                ('balance_value', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('occurred_on', fields.DateField()),
                ('notes', fields.CharField(null=True, max_length=240)),
                ('operation_key', fields.UUIDField(null=True, unique=True)),
                ('transfer_id', fields.UUIDField(null=True, db_index=True)),
                ('reversal_of', fields.OneToOneField('models.StockMovement', source_field='reversal_of_id', null=True, db_constraint=True, to_field='id', related_name='reversal', on_delete=OnDelete.RESTRICT)),
                ('journal_entry', fields.ForeignKeyField('models.JournalEntry', source_field='journal_entry_id', null=True, db_constraint=True, to_field='id', related_name='stock_movements', on_delete=OnDelete.RESTRICT)),
                ('source_type', fields.CharField(null=True, max_length=24)),
                ('source_id', fields.UUIDField(null=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'stockmovement', 'app': 'models', 'unique_together': (('product', 'warehouse', 'sequence'),), 'pk_attr': 'id', 'table_description': 'Movimento de estoque efetivado. Imutável: correção só por movimento de estorno.'},
            bases=['Model'],
        ),
        ops.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
