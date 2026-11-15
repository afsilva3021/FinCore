from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0020_crm_check_constraints')]

    initial = False

    operations = [
        ops.AddField(
            model_name='Organization',
            name='books_closed_at',
            field=fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
        ),
        ops.AddField(
            model_name='Organization',
            name='books_closed_by',
            field=fields.ForeignKeyField('models.User', source_field='books_closed_by_id', null=True, db_constraint=True, to_field='id', related_name='closed_periods', on_delete=OnDelete.RESTRICT),
        ),
        ops.AddField(
            model_name='Organization',
            name='books_closed_until',
            field=fields.DateField(null=True),
        ),
        ops.AddField(
            model_name='TitleSettlement',
            name='reversal_transaction',
            field=fields.OneToOneField('models.FinancialTransaction', source_field='reversal_transaction_id', null=True, db_constraint=True, to_field='id', related_name='reversed_title_settlement', on_delete=OnDelete.RESTRICT),
        ),
        ops.AddField(
            model_name='TitleSettlement',
            name='reversed_at',
            field=fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
        ),
        ops.AddField(
            model_name='TitleSettlement',
            name='reversed_by',
            field=fields.ForeignKeyField('models.User', source_field='reversed_by_id', null=True, db_constraint=True, to_field='id', related_name='reversed_title_settlements', on_delete=OnDelete.RESTRICT),
        ),
    ]
