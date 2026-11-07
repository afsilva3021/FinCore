from tortoise import migrations
from tortoise.migrations import operations as ops
from uuid import uuid4
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0015_auto_20261105_2018')]

    initial = False

    operations = [
        ops.CreateModel(
            name='LoginThrottle',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('key', fields.CharField(unique=True, max_length=320)),
                ('failures', fields.IntField(default=0)),
                ('window_started_at', fields.DatetimeField(auto_now=False, auto_now_add=False)),
                ('locked_until', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('updated_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'loginthrottle', 'app': 'models', 'pk_attr': 'id', 'table_description': 'Contador de falhas de login por chave (e-mail ou IP) para bloqueio temporário.'},
            bases=['Model'],
        ),
        # A coluna nasce anulável, é preenchida nas contas existentes e só depois vira
        # NOT NULL: um AddField direto falha em bancos que já têm usuários.
        ops.AddField(
            model_name='User',
            name='session_version',
            field=fields.IntField(default=1, null=True),
        ),
        ops.RunSQL(
            sql=(
                'UPDATE "user" SET session_version = 1 WHERE session_version IS NULL; '
                'ALTER TABLE "user" ALTER COLUMN session_version SET DEFAULT 1;'
            ),
            reverse_sql='ALTER TABLE "user" ALTER COLUMN session_version DROP DEFAULT;',
        ),
        ops.AlterField(
            model_name='User',
            name='session_version',
            field=fields.IntField(default=1),
        ),
    ]
