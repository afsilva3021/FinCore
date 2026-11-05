from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0009_auto_20261104_1820')]

    initial = False

    operations = [
        ops.AddField(
            model_name='FinancialAccount',
            name='bank_name',
            field=fields.CharField(null=True, max_length=120),
        ),
    ]
