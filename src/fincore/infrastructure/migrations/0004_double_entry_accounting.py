from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0003_audit_actor')]

    initial = False

    operations = [
        ops.CreateModel(
            name='JournalEntry',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('idempotency_key', fields.UUIDField(default=uuid4, unique=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='journal_entries', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='journal_entries', on_delete=OnDelete.RESTRICT)),
                ('reversal_of', fields.OneToOneField('models.JournalEntry', source_field='reversal_of_id', null=True, db_constraint=True, to_field='id', related_name='reversal', on_delete=OnDelete.RESTRICT)),
                ('entry_date', fields.DateField()),
                ('description', fields.CharField(max_length=240)),
                ('status', fields.CharField(default='draft', max_length=12)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('posted_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ],
            options={'table': 'journalentry', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='LedgerAccount',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='ledger_accounts', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_ledger_accounts', on_delete=OnDelete.RESTRICT)),
                ('parent', fields.ForeignKeyField('models.LedgerAccount', source_field='parent_id', null=True, db_constraint=True, to_field='id', related_name='children', on_delete=OnDelete.RESTRICT)),
                ('code', fields.CharField(max_length=32)),
                ('name', fields.CharField(max_length=160)),
                ('account_type', fields.CharField(max_length=16)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'ledgeraccount', 'app': 'models', 'unique_together': (('organization', 'code'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='JournalEntryLine',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('journal_entry', fields.ForeignKeyField('models.JournalEntry', source_field='journal_entry_id', db_constraint=True, to_field='id', related_name='lines', on_delete=OnDelete.CASCADE)),
                ('account', fields.ForeignKeyField('models.LedgerAccount', source_field='account_id', db_constraint=True, to_field='id', related_name='journal_lines', on_delete=OnDelete.RESTRICT)),
                ('debit', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('credit', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('currency', fields.CharField(default='BRL', max_length=3)),
            ],
            options={'table': 'journalentryline', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
