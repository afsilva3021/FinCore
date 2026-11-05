from tortoise import fields, migrations
from tortoise.fields.base import OnDelete
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0010_auto_20261104_1935")]

    initial = False

    operations = [
        ops.AddField(
            model_name="Category",
            name="category_kind",
            field=fields.CharField(null=True, max_length=12),
        ),
        ops.AddField(
            model_name="Category",
            name="parent",
            field=fields.ForeignKeyField(
                "models.Category",
                source_field="parent_id",
                null=True,
                db_constraint=True,
                to_field="id",
                related_name="children",
                on_delete=OnDelete.RESTRICT,
            ),
        ),
        ops.AddField(
            model_name="LedgerAccount",
            name="is_synthetic",
            field=fields.BooleanField(null=True),
        ),
        ops.RunSQL(
            "UPDATE category SET category_kind = 'analytic' WHERE category_kind IS NULL; "
            "UPDATE ledgeraccount SET is_synthetic = FALSE WHERE is_synthetic IS NULL;",
            reverse_sql="SELECT 1;",
        ),
        ops.AlterField(
            model_name="Category",
            name="category_kind",
            field=fields.CharField(default="analytic", max_length=12),
        ),
        ops.AlterField(
            model_name="LedgerAccount",
            name="is_synthetic",
            field=fields.BooleanField(default=False),
        ),
    ]
