from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0008_auto_20261104_1802')]

    initial = False

    operations = [
        ops.AddField(
            model_name='FinancialTransaction',
            name='settled_by',
            field=fields.ForeignKeyField('models.User', source_field='settled_by_id', null=True, db_constraint=True, to_field='id', related_name='settled_financial_transactions', on_delete=OnDelete.RESTRICT),
        ),
    ]
