from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0006_auto_20261104_1744')]

    initial = False

    operations = [
        ops.CreateModel(
            name='ReconciliationEvent',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='reconciliation_events', on_delete=OnDelete.CASCADE)),
                ('statement_transaction', fields.ForeignKeyField('models.ImportedStatementTransaction', source_field='statement_transaction_id', db_constraint=True, to_field='id', related_name='events', on_delete=OnDelete.RESTRICT)),
                ('financial_transaction', fields.ForeignKeyField('models.FinancialTransaction', source_field='financial_transaction_id', db_constraint=True, to_field='id', related_name='reconciliation_events', on_delete=OnDelete.RESTRICT)),
                ('actor', fields.ForeignKeyField('models.User', source_field='actor_id', db_constraint=True, to_field='id', related_name='reconciliation_events', on_delete=OnDelete.RESTRICT)),
                ('action', fields.CharField(max_length=12)),
                ('occurred_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'reconciliationevent', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
