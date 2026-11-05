from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0012_auto_20261104_2113')]

    initial = False

    operations = [
        ops.AddField(
            model_name='FinancialTitle',
            name='approver_reference',
            field=fields.CharField(null=True, max_length=160),
        ),
    ]
