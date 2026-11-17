"""Vendas (ERP, fase 11): orçamentos/pedidos de venda e faturamentos.

Aditiva: cria `salesorder`, `salesorderitem`, `salesinvoice` e `salesinvoiceitem` e não
altera dados existentes. O perfil `sales` já é aceito por `ck_membership_role` desde a 0021.
"""

from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

FORWARD_SQL = """
ALTER TABLE "salesorder" ADD CONSTRAINT ck_sales_order CHECK (
    status IN ('quote', 'confirmed', 'invoiced', 'cancelled')
    AND number > 0 AND installment_count BETWEEN 1 AND 36
    AND first_due_days BETWEEN 0 AND 365
    AND (valid_until IS NULL OR valid_until >= order_date)
);
ALTER TABLE "salesorderitem" ADD CONSTRAINT ck_sales_order_item CHECK (
    quantity > 0 AND unit_price >= 0 AND discount >= 0 AND position > 0
);
ALTER TABLE "salesinvoice" ADD CONSTRAINT ck_sales_invoice CHECK (
    status IN ('posted', 'reversed') AND total > 0 AND cost_total >= 0 AND number > 0
    AND ((status = 'reversed') = (reversed_at IS NOT NULL))
);
ALTER TABLE "salesinvoiceitem" ADD CONSTRAINT ck_sales_invoice_item CHECK (
    quantity > 0 AND total >= 0 AND cost >= 0
);
-- Um pedido tem no máximo um faturamento efetivado por vez.
CREATE UNIQUE INDEX uq_sales_invoice_posted_per_order ON "salesinvoice" (order_id)
    WHERE status = 'posted';
CREATE INDEX ix_sales_order_org_status ON "salesorder" (organization_id, status);
"""


class Migration(migrations.Migration):
    dependencies = [('models', '0021_purchases')]

    initial = False

    operations = [
        ops.CreateModel(
            name='SalesOrder',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='sales_orders', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_sales_orders', on_delete=OnDelete.RESTRICT)),
                ('number', fields.IntField()),
                ('customer', fields.ForeignKeyField('models.Counterparty', source_field='customer_id', db_constraint=True, to_field='id', related_name='sales_orders', on_delete=OnDelete.RESTRICT)),
                ('warehouse', fields.ForeignKeyField('models.Warehouse', source_field='warehouse_id', db_constraint=True, to_field='id', related_name='sales_orders', on_delete=OnDelete.RESTRICT)),
                ('account', fields.ForeignKeyField('models.FinancialAccount', source_field='account_id', db_constraint=True, to_field='id', related_name='sales_orders', on_delete=OnDelete.RESTRICT)),
                ('status', fields.CharField(default='quote', max_length=12)),
                ('order_date', fields.DateField()),
                ('valid_until', fields.DateField(null=True)),
                ('payment_method', fields.CharField(default='pix', max_length=24)),
                ('installment_count', fields.IntField(default=1)),
                ('first_due_days', fields.IntField(default=0)),
                ('notes', fields.TextField(null=True, unique=False)),
                ('operation_key', fields.UUIDField(null=True, unique=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'salesorder', 'app': 'models', 'unique_together': (('organization', 'number'),), 'pk_attr': 'id', 'table_description': 'Orçamento que vira pedido de venda. Só o faturamento mexe em estoque e financeiro.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='SalesInvoice',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='sales_invoices', on_delete=OnDelete.CASCADE)),
                ('order', fields.ForeignKeyField('models.SalesOrder', source_field='order_id', db_constraint=True, to_field='id', related_name='invoices', on_delete=OnDelete.RESTRICT)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_sales_invoices', on_delete=OnDelete.RESTRICT)),
                ('number', fields.IntField()),
                ('issued_on', fields.DateField()),
                ('total', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('cost_total', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('status', fields.CharField(default='posted', max_length=12)),
                ('operation_key', fields.UUIDField(unique=True)),
                ('revenue_entry', fields.ForeignKeyField('models.JournalEntry', source_field='revenue_entry_id', null=True, db_constraint=True, to_field='id', related_name='sales_revenue_invoices', on_delete=OnDelete.RESTRICT)),
                ('cost_entry', fields.ForeignKeyField('models.JournalEntry', source_field='cost_entry_id', null=True, db_constraint=True, to_field='id', related_name='sales_cost_invoices', on_delete=OnDelete.RESTRICT)),
                ('reversed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('reversed_by', fields.ForeignKeyField('models.User', source_field='reversed_by_id', null=True, db_constraint=True, to_field='id', related_name='reversed_sales_invoices', on_delete=OnDelete.RESTRICT)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'salesinvoice', 'app': 'models', 'unique_together': (('organization', 'number'),), 'pk_attr': 'id', 'table_description': 'Faturamento interno do pedido: baixa estoque, gera contas a receber, receita e CMV.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='SalesOrderItem',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('order', fields.ForeignKeyField('models.SalesOrder', source_field='order_id', db_constraint=True, to_field='id', related_name='items', on_delete=OnDelete.CASCADE)),
                ('product', fields.ForeignKeyField('models.Product', source_field='product_id', db_constraint=True, to_field='id', related_name='sales_items', on_delete=OnDelete.RESTRICT)),
                ('position', fields.IntField()),
                ('quantity', fields.DecimalField(max_digits=15, decimal_places=4)),
                ('unit_price', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('discount', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
            ],
            options={'table': 'salesorderitem', 'app': 'models', 'unique_together': (('order', 'position'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='SalesInvoiceItem',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('invoice', fields.ForeignKeyField('models.SalesInvoice', source_field='invoice_id', db_constraint=True, to_field='id', related_name='items', on_delete=OnDelete.CASCADE)),
                ('order_item', fields.ForeignKeyField('models.SalesOrderItem', source_field='order_item_id', db_constraint=True, to_field='id', related_name='invoice_items', on_delete=OnDelete.RESTRICT)),
                ('quantity', fields.DecimalField(max_digits=15, decimal_places=4)),
                ('total', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('cost', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('stock_movement', fields.OneToOneField('models.StockMovement', source_field='stock_movement_id', null=True, db_constraint=True, to_field='id', related_name='sales_invoice_item', on_delete=OnDelete.RESTRICT)),
            ],
            options={'table': 'salesinvoiceitem', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.RunSQL(sql=FORWARD_SQL, reverse_sql=""),
    ]
