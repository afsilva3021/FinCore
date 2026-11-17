"""Compras (ERP, fase 10): pedidos, recebimentos, numeração e origem dos títulos.

Aditiva. Cria `documentsequence`, `purchaseorder`, `purchaseorderitem`, `purchasereceipt`
e `purchasereceiptitem`, e acrescenta a `financialtitle` a origem do título (`manual`,
`purchase` ou `sale`), a conta de contrapartida por competência e o documento de origem.
Títulos existentes ficam como `manual` e continuam lançando só na baixa.

`ck_membership_role` passa a aceitar os perfis `purchasing` (Compras) e `sales` (Vendas).
"""

from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

ROLES_BEFORE = "role IN ('owner', 'finance', 'stock', 'viewer')"
ROLES_AFTER = "role IN ('owner', 'finance', 'stock', 'purchasing', 'sales', 'viewer')"

FORWARD_SQL = f"""
ALTER TABLE "financialtitle" ADD CONSTRAINT ck_title_origin CHECK (
    origin IN ('manual', 'purchase', 'sale')
    AND ((origin = 'manual') = (accrual_account_id IS NULL))
    AND (origin = 'manual' OR (source_type IS NOT NULL AND source_id IS NOT NULL))
);
ALTER TABLE "documentsequence" ADD CONSTRAINT ck_document_sequence CHECK (last_number >= 0);
ALTER TABLE "purchaseorder" ADD CONSTRAINT ck_purchase_order CHECK (
    status IN ('draft', 'approved', 'partially_received', 'received', 'cancelled')
    AND number > 0 AND installment_count BETWEEN 1 AND 36
    AND first_due_days BETWEEN 0 AND 365
    AND (expected_date IS NULL OR expected_date >= order_date)
);
ALTER TABLE "purchaseorderitem" ADD CONSTRAINT ck_purchase_order_item CHECK (
    quantity > 0 AND unit_cost >= 0 AND received_quantity >= 0
    AND received_quantity <= quantity AND position > 0
);
ALTER TABLE "purchasereceipt" ADD CONSTRAINT ck_purchase_receipt CHECK (
    status IN ('posted', 'reversed') AND total >= 0 AND number > 0
    AND ((status = 'reversed') = (reversed_at IS NOT NULL))
);
ALTER TABLE "purchasereceiptitem" ADD CONSTRAINT ck_purchase_receipt_item CHECK (
    quantity > 0 AND unit_cost >= 0 AND total_cost >= 0
);
CREATE INDEX ix_purchase_order_org_status ON "purchaseorder" (organization_id, status);

ALTER TABLE "membership" DROP CONSTRAINT IF EXISTS ck_membership_role;
ALTER TABLE "membership" ADD CONSTRAINT ck_membership_role CHECK ({ROLES_AFTER});
"""

# O retorno falha de propósito se houver membros com os perfis novos.
REVERSE_SQL = f"""
ALTER TABLE "membership" DROP CONSTRAINT IF EXISTS ck_membership_role;
ALTER TABLE "membership" ADD CONSTRAINT ck_membership_role CHECK ({ROLES_BEFORE});
"""


class Migration(migrations.Migration):
    dependencies = [('models', '0020_stock')]

    initial = False

    operations = [
        ops.CreateModel(
            name='DocumentSequence',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='document_sequences', on_delete=OnDelete.CASCADE)),
                ('kind', fields.CharField(max_length=24)),
                ('last_number', fields.IntField(default=0)),
            ],
            options={'table': 'documentsequence', 'app': 'models', 'unique_together': (('organization', 'kind'),), 'pk_attr': 'id', 'table_description': 'Último número usado por tipo de documento em cada organização.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='PurchaseOrder',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='purchase_orders', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_purchase_orders', on_delete=OnDelete.RESTRICT)),
                ('number', fields.IntField()),
                ('supplier', fields.ForeignKeyField('models.Counterparty', source_field='supplier_id', db_constraint=True, to_field='id', related_name='purchase_orders', on_delete=OnDelete.RESTRICT)),
                ('warehouse', fields.ForeignKeyField('models.Warehouse', source_field='warehouse_id', db_constraint=True, to_field='id', related_name='purchase_orders', on_delete=OnDelete.RESTRICT)),
                ('account', fields.ForeignKeyField('models.FinancialAccount', source_field='account_id', db_constraint=True, to_field='id', related_name='purchase_orders', on_delete=OnDelete.RESTRICT)),
                ('status', fields.CharField(default='draft', max_length=20)),
                ('order_date', fields.DateField()),
                ('expected_date', fields.DateField(null=True)),
                ('payment_method', fields.CharField(default='boleto', max_length=24)),
                ('installment_count', fields.IntField(default=1)),
                ('first_due_days', fields.IntField(default=30)),
                ('notes', fields.TextField(null=True, unique=False)),
                ('operation_key', fields.UUIDField(null=True, unique=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'purchaseorder', 'app': 'models', 'unique_together': (('organization', 'number'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='PurchaseOrderItem',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('order', fields.ForeignKeyField('models.PurchaseOrder', source_field='order_id', db_constraint=True, to_field='id', related_name='items', on_delete=OnDelete.CASCADE)),
                ('product', fields.ForeignKeyField('models.Product', source_field='product_id', db_constraint=True, to_field='id', related_name='purchase_items', on_delete=OnDelete.RESTRICT)),
                ('position', fields.IntField()),
                ('quantity', fields.DecimalField(max_digits=15, decimal_places=4)),
                ('unit_cost', fields.DecimalField(max_digits=15, decimal_places=6)),
                ('received_quantity', fields.DecimalField(default=0, max_digits=15, decimal_places=4)),
            ],
            options={'table': 'purchaseorderitem', 'app': 'models', 'unique_together': (('order', 'position'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='PurchaseReceipt',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='purchase_receipts', on_delete=OnDelete.CASCADE)),
                ('order', fields.ForeignKeyField('models.PurchaseOrder', source_field='order_id', db_constraint=True, to_field='id', related_name='receipts', on_delete=OnDelete.RESTRICT)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_purchase_receipts', on_delete=OnDelete.RESTRICT)),
                ('number', fields.IntField()),
                ('received_on', fields.DateField()),
                ('document_number', fields.CharField(null=True, max_length=60)),
                ('total', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('status', fields.CharField(default='posted', max_length=12)),
                ('operation_key', fields.UUIDField(unique=True)),
                ('journal_entry', fields.ForeignKeyField('models.JournalEntry', source_field='journal_entry_id', null=True, db_constraint=True, to_field='id', related_name='purchase_receipts', on_delete=OnDelete.RESTRICT)),
                ('reversed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('reversed_by', fields.ForeignKeyField('models.User', source_field='reversed_by_id', null=True, db_constraint=True, to_field='id', related_name='reversed_purchase_receipts', on_delete=OnDelete.RESTRICT)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'purchasereceipt', 'app': 'models', 'unique_together': (('organization', 'number'),), 'pk_attr': 'id', 'table_description': 'Recebimento de mercadoria: entra no estoque e gera contas a pagar.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='PurchaseReceiptItem',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('receipt', fields.ForeignKeyField('models.PurchaseReceipt', source_field='receipt_id', db_constraint=True, to_field='id', related_name='items', on_delete=OnDelete.CASCADE)),
                ('order_item', fields.ForeignKeyField('models.PurchaseOrderItem', source_field='order_item_id', db_constraint=True, to_field='id', related_name='receipt_items', on_delete=OnDelete.RESTRICT)),
                ('quantity', fields.DecimalField(max_digits=15, decimal_places=4)),
                ('unit_cost', fields.DecimalField(max_digits=15, decimal_places=6)),
                ('total_cost', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('stock_movement', fields.OneToOneField('models.StockMovement', source_field='stock_movement_id', db_constraint=True, to_field='id', related_name='purchase_receipt_item', on_delete=OnDelete.RESTRICT)),
            ],
            options={'table': 'purchasereceiptitem', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.AddField(
            model_name='FinancialTitle',
            name='accrual_account',
            field=fields.ForeignKeyField('models.LedgerAccount', source_field='accrual_account_id', null=True, db_constraint=True, to_field='id', related_name='accrual_titles', on_delete=OnDelete.RESTRICT),
        ),
        # A coluna nasce anulável, é preenchida nos títulos existentes e só depois vira
        # NOT NULL: um AddField direto falha em bancos que já têm títulos.
        ops.AddField(
            model_name='FinancialTitle',
            name='origin',
            field=fields.CharField(default='manual', max_length=12, null=True),
        ),
        ops.RunSQL(
            sql=(
                "UPDATE \"financialtitle\" SET origin = 'manual' WHERE origin IS NULL; "
                "ALTER TABLE \"financialtitle\" ALTER COLUMN origin SET DEFAULT 'manual';"
            ),
            reverse_sql='ALTER TABLE "financialtitle" ALTER COLUMN origin DROP DEFAULT;',
        ),
        ops.AlterField(
            model_name='FinancialTitle',
            name='origin',
            field=fields.CharField(default='manual', max_length=12),
        ),
        ops.AddField(
            model_name='FinancialTitle',
            name='source_id',
            field=fields.UUIDField(null=True, db_index=True),
        ),
        ops.AddField(
            model_name='FinancialTitle',
            name='source_type',
            field=fields.CharField(null=True, max_length=24),
        ),
        ops.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
