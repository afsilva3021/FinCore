from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0001_identity')]

    initial = False

    operations = [
        ops.CreateModel(
            name='Category',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='categories', on_delete=OnDelete.CASCADE)),
                ('name', fields.CharField(max_length=100)),
                ('transaction_type', fields.CharField(max_length=12)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'category', 'app': 'models', 'unique_together': (('organization', 'name', 'transaction_type'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='FinancialAccount',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='financial_accounts', on_delete=OnDelete.CASCADE)),
                ('name', fields.CharField(max_length=120)),
                ('account_type', fields.CharField(default='bank', max_length=20)),
                ('currency', fields.CharField(default='BRL', max_length=3)),
                ('opening_balance', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'financialaccount', 'app': 'models', 'unique_together': (('organization', 'name'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='FinancialTransaction',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('operation_key', fields.UUIDField(default=uuid4, unique=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='transactions', on_delete=OnDelete.CASCADE)),
                ('account', fields.ForeignKeyField('models.FinancialAccount', source_field='account_id', db_constraint=True, to_field='id', related_name='transactions', on_delete=OnDelete.RESTRICT)),
                ('category', fields.ForeignKeyField('models.Category', source_field='category_id', null=True, db_constraint=True, to_field='id', related_name='transactions', on_delete=OnDelete.RESTRICT)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='financial_transactions', on_delete=OnDelete.RESTRICT)),
                ('transfer_id', fields.UUIDField(null=True, db_index=True)),
                ('transaction_type', fields.CharField(max_length=16)),
                ('description', fields.CharField(max_length=240)),
                ('amount', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('currency', fields.CharField(default='BRL', max_length=3)),
                ('competence_date', fields.DateField()),
                ('due_date', fields.DateField(null=True)),
                ('settled_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('status', fields.CharField(default='open', max_length=12)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'financialtransaction', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
