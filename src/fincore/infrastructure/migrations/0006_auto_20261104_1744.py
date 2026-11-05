from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0005_financial_journal_link')]

    initial = False

    operations = [
        ops.CreateModel(
            name='ImportedStatementTransaction',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='imported_transactions', on_delete=OnDelete.CASCADE)),
                ('account', fields.ForeignKeyField('models.FinancialAccount', source_field='account_id', db_constraint=True, to_field='id', related_name='imported_transactions', on_delete=OnDelete.RESTRICT)),
                ('external_id', fields.CharField(max_length=128)),
                ('posted_at', fields.DateField()),
                ('description', fields.CharField(max_length=240)),
                ('amount', fields.DecimalField(max_digits=15, decimal_places=2)),
                ('currency', fields.CharField(default='BRL', max_length=3)),
                ('imported_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'importedstatementtransaction', 'app': 'models', 'unique_together': (('account', 'external_id'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='ReconciliationMatch',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='reconciliation_matches', on_delete=OnDelete.CASCADE)),
                ('statement_transaction', fields.OneToOneField('models.ImportedStatementTransaction', source_field='statement_transaction_id', db_constraint=True, to_field='id', related_name='match', on_delete=OnDelete.CASCADE)),
                ('financial_transaction', fields.OneToOneField('models.FinancialTransaction', source_field='financial_transaction_id', db_constraint=True, to_field='id', related_name='reconciliation_match', on_delete=OnDelete.RESTRICT)),
                ('matched_by', fields.ForeignKeyField('models.User', source_field='matched_by_id', db_constraint=True, to_field='id', related_name='reconciliation_matches', on_delete=OnDelete.RESTRICT)),
                ('matched_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'reconciliationmatch', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
