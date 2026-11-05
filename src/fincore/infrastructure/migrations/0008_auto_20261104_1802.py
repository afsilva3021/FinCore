from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0007_auto_20261104_1745')]

    initial = False

    operations = [
        ops.AddField(
            model_name='FinancialTransaction',
            name='reversal_of',
            field=fields.OneToOneField('models.FinancialTransaction', source_field='reversal_of_id', null=True, db_constraint=True, to_field='id', related_name='reversal', on_delete=OnDelete.RESTRICT),
        ),
    ]
