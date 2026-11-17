"""Cadastros de produto (ERP, fase 8): grupos, unidades de medida, produtos e depósitos.

Aditiva: cria quatro tabelas novas e não altera dados existentes. Além das tabelas:

- `CHECK` constraints de valores e formatos e índices únicos parciais (um depósito padrão
  por organização; código de barras único por organização quando informado);
- organizações que já existiam recebem as unidades usuais e o depósito padrão, como as
  organizações novas recebem no cadastro;
- `ck_membership_role` passa a aceitar o perfil `stock` (Estoque).
"""

from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

ROLES_BEFORE = "role IN ('owner', 'finance', 'viewer')"
ROLES_AFTER = "role IN ('owner', 'finance', 'stock', 'viewer')"

FORWARD_SQL = f"""
ALTER TABLE "product" ADD CONSTRAINT ck_product_values CHECK (
    sale_price >= 0 AND min_stock >= 0 AND currency ~ '^[A-Z]{{3}}$'
    AND sku = upper(sku) AND sku <> ''
    AND (barcode IS NULL OR barcode ~ '^([0-9]{{8}}|[0-9]{{12,14}})$')
    AND (ncm IS NULL OR ncm ~ '^[0-9]{{8}}$')
);
ALTER TABLE "unitofmeasure" ADD CONSTRAINT ck_unit_values CHECK (
    decimal_places BETWEEN 0 AND 4 AND code = upper(code) AND code <> ''
);
ALTER TABLE "warehouse" ADD CONSTRAINT ck_warehouse_values CHECK (
    code = upper(code) AND code <> '' AND (is_active OR NOT is_default)
);
ALTER TABLE "productgroup" ADD CONSTRAINT ck_product_group_parent CHECK (parent_id <> id);
CREATE UNIQUE INDEX uq_warehouse_default_per_org ON "warehouse" (organization_id)
    WHERE is_default;
CREATE UNIQUE INDEX uq_product_barcode_per_org ON "product" (organization_id, barcode)
    WHERE barcode IS NOT NULL;
CREATE INDEX ix_product_org_name ON "product" (organization_id, name);

INSERT INTO "unitofmeasure" (id, organization_id, code, name, decimal_places, is_active, created_at)
SELECT gen_random_uuid(), o.id, u.code, u.name, u.decimal_places, TRUE, now()
FROM "organization" o
CROSS JOIN (VALUES
    ('UN', 'Unidade', 0), ('CX', 'Caixa', 0), ('KG', 'Quilograma', 3),
    ('L', 'Litro', 3), ('M', 'Metro', 2)
) AS u(code, name, decimal_places)
ON CONFLICT (organization_id, code) DO NOTHING;

INSERT INTO "warehouse" (id, organization_id, code, name, is_default, is_active, created_at)
SELECT gen_random_uuid(), o.id, 'PRINCIPAL', 'Depósito principal', TRUE, TRUE, now()
FROM "organization" o
WHERE NOT EXISTS (SELECT 1 FROM "warehouse" w WHERE w.organization_id = o.id);

ALTER TABLE "membership" DROP CONSTRAINT IF EXISTS ck_membership_role;
ALTER TABLE "membership" ADD CONSTRAINT ck_membership_role CHECK ({ROLES_AFTER});
"""

# O retorno falha de propósito se ainda houver membros com o perfil `stock`: troque o
# perfil deles antes de desfazer a migration.
REVERSE_SQL = f"""
ALTER TABLE "membership" DROP CONSTRAINT IF EXISTS ck_membership_role;
ALTER TABLE "membership" ADD CONSTRAINT ck_membership_role CHECK ({ROLES_BEFORE});
"""


class Migration(migrations.Migration):
    dependencies = [('models', '0018_title_operation_key')]

    initial = False

    operations = [
        ops.CreateModel(
            name='ProductGroup',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='product_groups', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', null=True, db_constraint=True, to_field='id', related_name='created_product_groups', on_delete=OnDelete.SET_NULL)),
                ('parent', fields.ForeignKeyField('models.ProductGroup', source_field='parent_id', null=True, db_constraint=True, to_field='id', related_name='children', on_delete=OnDelete.RESTRICT)),
                ('name', fields.CharField(max_length=120)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'productgroup', 'app': 'models', 'unique_together': (('organization', 'name'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='UnitOfMeasure',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='units_of_measure', on_delete=OnDelete.CASCADE)),
                ('code', fields.CharField(max_length=6)),
                ('name', fields.CharField(max_length=60)),
                ('decimal_places', fields.SmallIntField(default=0)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'unitofmeasure', 'app': 'models', 'unique_together': (('organization', 'code'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='Product',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='products', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_products', on_delete=OnDelete.RESTRICT)),
                ('group', fields.ForeignKeyField('models.ProductGroup', source_field='group_id', null=True, db_constraint=True, to_field='id', related_name='products', on_delete=OnDelete.RESTRICT)),
                ('unit', fields.ForeignKeyField('models.UnitOfMeasure', source_field='unit_id', db_constraint=True, to_field='id', related_name='products', on_delete=OnDelete.RESTRICT)),
                ('sku', fields.CharField(max_length=40)),
                ('name', fields.CharField(max_length=160)),
                ('description', fields.TextField(null=True, unique=False)),
                ('barcode', fields.CharField(null=True, max_length=14)),
                ('sale_price', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('currency', fields.CharField(default='BRL', max_length=3)),
                ('min_stock', fields.DecimalField(default=0, max_digits=15, decimal_places=4)),
                ('tracks_stock', fields.BooleanField(default=True)),
                ('ncm', fields.CharField(null=True, max_length=8)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('updated_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'product', 'app': 'models', 'unique_together': (('organization', 'sku'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='Warehouse',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='warehouses', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', null=True, db_constraint=True, to_field='id', related_name='created_warehouses', on_delete=OnDelete.SET_NULL)),
                ('code', fields.CharField(max_length=20)),
                ('name', fields.CharField(max_length=120)),
                ('is_default', fields.BooleanField(default=False)),
                ('is_active', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'warehouse', 'app': 'models', 'unique_together': (('organization', 'code'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
