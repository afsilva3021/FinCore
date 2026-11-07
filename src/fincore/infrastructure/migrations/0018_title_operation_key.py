from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0017_check_constraints')]

    initial = False

    operations = [
        ops.AddField(
            model_name='FinancialTitle',
            name='operation_key',
            field=fields.UUIDField(null=True, unique=True),
        ),
    ]
