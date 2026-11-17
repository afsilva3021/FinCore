"""Fiscal simulado (ERP, fase 13): perfis tributários e documentos fiscais internos.

Aditiva: cria `taxprofile`, `fiscaldocument` e `fiscaldocumentitem`. Nada aqui tem valor
fiscal: não há XML, assinatura nem transmissão à SEFAZ.
"""

from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

FORWARD_SQL = """
ALTER TABLE "taxprofile" ADD CONSTRAINT ck_tax_profile CHECK (
    cfop ~ '^[567][0-9]{3}$' AND icms_cst ~ '^[0-9]{2,3}$'
    AND icms_rate BETWEEN 0 AND 100 AND pis_rate BETWEEN 0 AND 100
    AND cofins_rate BETWEEN 0 AND 100
);
ALTER TABLE "fiscaldocument" ADD CONSTRAINT ck_fiscal_document CHECK (
    status IN ('issued', 'cancelled') AND number > 0 AND total_products > 0
    AND total_icms >= 0 AND total_pis >= 0 AND total_cofins >= 0
    AND ((status = 'cancelled') = (cancelled_at IS NOT NULL))
    AND (status = 'issued' OR cancel_reason IS NOT NULL)
);
ALTER TABLE "fiscaldocumentitem" ADD CONSTRAINT ck_fiscal_document_item CHECK (
    quantity > 0 AND unit_price >= 0 AND total >= 0 AND icms_amount >= 0
    AND pis_amount >= 0 AND cofins_amount >= 0 AND position > 0
);
-- Um faturamento tem no máximo um documento emitido por vez.
CREATE UNIQUE INDEX uq_fiscal_document_issued_per_invoice ON "fiscaldocument" (sales_invoice_id)
    WHERE status = 'issued';
"""


class Migration(migrations.Migration):
    dependencies = [('models', '0023_crm')]

    initial = False

    operations = [
        ops.CreateModel(
            name='TaxProfile',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='tax_profiles', on_delete=OnDelete.CASCADE)),
                ('name', fields.CharField(max_length=80)),
                ('cfop', fields.CharField(max_length=4)),
                ('icms_cst', fields.CharField(max_length=3)),
                ('icms_rate', fields.DecimalField(default=0, max_digits=5, decimal_places=2)),
                ('pis_rate', fields.DecimalField(default=0, max_digits=5, decimal_places=2)),
                ('cofins_rate', fields.DecimalField(default=0, max_digits=5, decimal_places=2)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'taxprofile', 'app': 'models', 'unique_together': (('organization', 'name'),), 'pk_attr': 'id', 'table_description': 'Dados tributários informados pelo usuário para a simulação (não são conferidos).'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='FiscalDocument',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='fiscal_documents', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_fiscal_documents', on_delete=OnDelete.RESTRICT)),
                ('number', fields.IntField()),
                ('series', fields.CharField(default='1', max_length=3)),
                ('sales_invoice', fields.ForeignKeyField('models.SalesInvoice', source_field='sales_invoice_id', db_constraint=True, to_field='id', related_name='fiscal_documents', on_delete=OnDelete.RESTRICT)),
                ('tax_profile', fields.ForeignKeyField('models.TaxProfile', source_field='tax_profile_id', db_constraint=True, to_field='id', related_name='fiscal_documents', on_delete=OnDelete.RESTRICT)),
                ('customer', fields.ForeignKeyField('models.Counterparty', source_field='customer_id', db_constraint=True, to_field='id', related_name='fiscal_documents', on_delete=OnDelete.RESTRICT)),
                ('issued_on', fields.DateField()),
                ('total_products', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('total_icms', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('total_pis', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('total_cofins', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('status', fields.CharField(default='issued', max_length=10)),
                ('cancel_reason', fields.CharField(null=True, max_length=240)),
                ('cancelled_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('operation_key', fields.UUIDField(unique=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'fiscaldocument', 'app': 'models', 'unique_together': (('organization', 'series', 'number'),), 'pk_attr': 'id', 'table_description': 'Documento fiscal interno gerado de um faturamento. Simulação, sem valor fiscal.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='FiscalDocumentItem',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('document', fields.ForeignKeyField('models.FiscalDocument', source_field='document_id', db_constraint=True, to_field='id', related_name='items', on_delete=OnDelete.CASCADE)),
                ('position', fields.IntField()),
                ('product', fields.ForeignKeyField('models.Product', source_field='product_id', db_constraint=True, to_field='id', related_name='fiscal_items', on_delete=OnDelete.RESTRICT)),
                ('description', fields.CharField(max_length=160)),
                ('ncm', fields.CharField(null=True, max_length=8)),
                ('cfop', fields.CharField(max_length=4)),
                ('unit', fields.CharField(max_length=6)),
                ('quantity', fields.DecimalField(max_digits=15, decimal_places=4)),
                ('unit_price', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('total', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('icms_amount', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('pis_amount', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('cofins_amount', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
            ],
            options={'table': 'fiscaldocumentitem', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.RunSQL(sql=FORWARD_SQL, reverse_sql=""),
    ]
