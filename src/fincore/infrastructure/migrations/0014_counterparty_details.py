from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0013_auto_20261104_2122")]

    initial = False

    operations = [
        ops.AddField(
            model_name="Counterparty",
            name="address_number",
            field=fields.CharField(null=True, max_length=20),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="city",
            field=fields.CharField(null=True, max_length=100),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="complement",
            field=fields.CharField(null=True, max_length=100),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="district",
            field=fields.CharField(null=True, max_length=100),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="email",
            field=fields.CharField(null=True, max_length=254),
        ),
        ops.AddField(
            model_name="Counterparty", name="is_active", field=fields.BooleanField(null=True)
        ),
        ops.AddField(
            model_name="Counterparty", name="is_customer", field=fields.BooleanField(null=True)
        ),
        ops.AddField(
            model_name="Counterparty", name="is_supplier", field=fields.BooleanField(null=True)
        ),
        ops.AddField(
            model_name="Counterparty", name="notes", field=fields.TextField(null=True, unique=False)
        ),
        ops.AddField(
            model_name="Counterparty",
            name="person_type",
            field=fields.CharField(null=True, max_length=12),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="phone",
            field=fields.CharField(null=True, max_length=40),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="postal_code",
            field=fields.CharField(null=True, max_length=9),
        ),
        ops.AddField(
            model_name="Counterparty", name="state", field=fields.CharField(null=True, max_length=2)
        ),
        ops.AddField(
            model_name="Counterparty",
            name="street",
            field=fields.CharField(null=True, max_length=160),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="tax_id",
            field=fields.CharField(null=True, max_length=14),
        ),
        ops.AddField(
            model_name="Counterparty",
            name="trade_name",
            field=fields.CharField(null=True, max_length=150),
        ),
        ops.RunSQL(
            """UPDATE counterparty SET is_active = TRUE,
            is_customer = (party_type = 'customer'),
            is_supplier = (party_type = 'supplier'), person_type = 'individual'
            WHERE is_active IS NULL OR is_customer IS NULL
            OR is_supplier IS NULL OR person_type IS NULL;
            CREATE UNIQUE INDEX "uidx_counterparty_org_tax_id" ON "counterparty"
            ("organization_id", "tax_id") WHERE "tax_id" IS NOT NULL""",
            reverse_sql='DROP INDEX IF EXISTS "uidx_counterparty_org_tax_id"',
        ),
        ops.AlterField(
            model_name="Counterparty", name="is_active", field=fields.BooleanField(default=True)
        ),
        ops.AlterField(
            model_name="Counterparty", name="is_customer", field=fields.BooleanField(default=False)
        ),
        ops.AlterField(
            model_name="Counterparty", name="is_supplier", field=fields.BooleanField(default=False)
        ),
        ops.AlterField(
            model_name="Counterparty",
            name="person_type",
            field=fields.CharField(default="individual", max_length=12),
        ),
    ]
