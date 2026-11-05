from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

class Migration(migrations.Migration):
    initial = True

    operations = [
        ops.CreateModel(
            name='Organization',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('name', fields.CharField(max_length=160)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('is_active', fields.BooleanField(default=True)),
            ],
            options={'table': 'organization', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='User',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('email', fields.CharField(unique=True, db_index=True, max_length=254)),
                ('password_hash', fields.CharField(max_length=256)),
                ('full_name', fields.CharField(max_length=160)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('is_active', fields.BooleanField(default=True)),
            ],
            options={'table': 'user', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='Membership',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('user', fields.ForeignKeyField('models.User', source_field='user_id', db_constraint=True, to_field='id', related_name='memberships', on_delete=OnDelete.CASCADE)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='memberships', on_delete=OnDelete.CASCADE)),
                ('role', fields.CharField(default='owner', max_length=32)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'membership', 'app': 'models', 'unique_together': (('user', 'organization'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
