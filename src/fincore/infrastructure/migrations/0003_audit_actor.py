from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0002_finance_core')]

    initial = False

    operations = [
        ops.AddField(
            model_name='Category',
            name='created_by',
            field=fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_categories', on_delete=OnDelete.RESTRICT, null=True),
        ),
        ops.AddField(
            model_name='FinancialAccount',
            name='created_by',
            field=fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_accounts', on_delete=OnDelete.RESTRICT, null=True),
        ),
    ]
