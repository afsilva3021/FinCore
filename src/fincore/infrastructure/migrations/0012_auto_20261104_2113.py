from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0011_auto_20261104_1949')]

    initial = False

    operations = [
        ops.CreateModel(
            name='Counterparty',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='counterparties', on_delete=OnDelete.CASCADE)),
                ('name', fields.CharField(max_length=160)),
                ('party_type', fields.CharField(max_length=12)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'counterparty', 'app': 'models', 'unique_together': (('organization', 'name', 'party_type'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='FinancialTitle',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='financial_titles', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_financial_titles', on_delete=OnDelete.RESTRICT)),
                ('counterparty', fields.ForeignKeyField('models.Counterparty', source_field='counterparty_id', db_constraint=True, to_field='id', related_name='titles', on_delete=OnDelete.RESTRICT)),
                ('account', fields.ForeignKeyField('models.FinancialAccount', source_field='account_id', db_constraint=True, to_field='id', related_name='titles', on_delete=OnDelete.RESTRICT)),
                ('category', fields.ForeignKeyField('models.Category', source_field='category_id', null=True, db_constraint=True, to_field='id', related_name='titles', on_delete=OnDelete.RESTRICT)),
                ('installment_group', fields.UUIDField(null=True, db_index=True)),
                ('title_type', fields.CharField(max_length=12)),
                ('description', fields.CharField(max_length=240)),
                ('document_number', fields.CharField(null=True, max_length=100)),
                ('document_type', fields.CharField(null=True, max_length=40)),
                ('document_series', fields.CharField(null=True, max_length=30)),
                ('cost_center', fields.CharField(null=True, max_length=120)),
                ('project', fields.CharField(null=True, max_length=120)),
                ('original_amount', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('currency', fields.CharField(default='BRL', max_length=3)),
                ('issue_date', fields.DateField()),
                ('due_date', fields.DateField()),
                ('expected_receipt_date', fields.DateField(null=True)),
                ('collection_sent_date', fields.DateField(null=True)),
                ('payment_method', fields.CharField(default='other', max_length=24)),
                ('payment_terms', fields.CharField(default='cash', max_length=16)),
                ('installment_number', fields.IntField(default=1)),
                ('installment_count', fields.IntField(default=1)),
                ('status', fields.CharField(default='open', max_length=12)),
                ('barcode', fields.CharField(null=True, max_length=128)),
                ('beneficiary', fields.CharField(null=True, max_length=160)),
                ('payment_details', fields.TextField(null=True, unique=False)),
                ('related_reference', fields.CharField(null=True, max_length=160)),
                ('charge_reference', fields.CharField(null=True, max_length=160)),
                ('attachment_reference', fields.CharField(null=True, max_length=512)),
                ('notes', fields.TextField(null=True, unique=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'financialtitle', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='TitleSettlement',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='title_settlements', on_delete=OnDelete.CASCADE)),
                ('title', fields.ForeignKeyField('models.FinancialTitle', source_field='title_id', db_constraint=True, to_field='id', related_name='settlements', on_delete=OnDelete.RESTRICT)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_title_settlements', on_delete=OnDelete.RESTRICT)),
                ('financial_transaction', fields.OneToOneField('models.FinancialTransaction', source_field='financial_transaction_id', db_constraint=True, to_field='id', related_name='title_settlement', on_delete=OnDelete.RESTRICT)),
                ('account', fields.ForeignKeyField('models.FinancialAccount', source_field='account_id', db_constraint=True, to_field='id', related_name='title_settlements', on_delete=OnDelete.RESTRICT)),
                ('operation_key', fields.UUIDField(default=uuid4, unique=True)),
                ('payment_method', fields.CharField(max_length=24)),
                ('amount_applied', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('discount', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('interest', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('fine', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('fees', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('net_amount', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('paid_on', fields.DateField()),
                ('proof_reference', fields.CharField(null=True, max_length=512)),
                ('notes', fields.TextField(null=True, unique=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'titlesettlement', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
