from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0004_double_entry_accounting')]

    initial = False

    operations = [
        ops.AddField(
            model_name='Category',
            name='ledger_account',
            field=fields.ForeignKeyField('models.LedgerAccount', source_field='ledger_account_id', null=True, db_constraint=True, to_field='id', related_name='categories', on_delete=OnDelete.RESTRICT),
        ),
        ops.AddField(
            model_name='FinancialAccount',
            name='ledger_account',
            field=fields.ForeignKeyField('models.LedgerAccount', source_field='ledger_account_id', null=True, db_constraint=True, to_field='id', related_name='financial_accounts', on_delete=OnDelete.RESTRICT),
        ),
        ops.AddField(
            model_name='JournalEntry',
            name='source_transaction',
            field=fields.OneToOneField('models.FinancialTransaction', source_field='source_transaction_id', null=True, db_constraint=True, to_field='id', related_name='journal_entry', on_delete=OnDelete.RESTRICT),
        ),
    ]
