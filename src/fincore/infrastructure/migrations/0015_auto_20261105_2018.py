from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0014_counterparty_details')]

    initial = False

    operations = [
        ops.AddField(
            model_name='FinancialAccount',
            name='account_number',
            field=fields.CharField(null=True, max_length=30),
        ),
        ops.AddField(
            model_name='FinancialAccount',
            name='bank_code',
            field=fields.CharField(null=True, max_length=4),
        ),
        ops.AddField(
            model_name='FinancialAccount',
            name='branch_number',
            field=fields.CharField(null=True, max_length=30),
        ),
    ]
