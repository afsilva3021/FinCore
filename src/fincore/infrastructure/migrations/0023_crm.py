"""CRM (ERP, fase 12): funil, leads, oportunidades, histórico de etapas e atividades.

Aditiva: cria `pipelinestage`, `lead`, `opportunity`, `opportunitystagechange` e
`crmactivity`. Organizações que já existiam recebem as etapas padrão do funil, como as
novas recebem no cadastro.
"""

from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields

FORWARD_SQL = """
ALTER TABLE "pipelinestage" ADD CONSTRAINT ck_pipeline_stage CHECK (
    kind IN ('open', 'won', 'lost') AND position > 0 AND name <> ''
);
ALTER TABLE "lead" ADD CONSTRAINT ck_lead CHECK (
    status IN ('open', 'converted', 'discarded')
    AND ((status = 'converted') = (counterparty_id IS NOT NULL))
);
ALTER TABLE "opportunity" ADD CONSTRAINT ck_opportunity CHECK (
    expected_value >= 0 AND (lead_id IS NOT NULL OR counterparty_id IS NOT NULL)
);
ALTER TABLE "crmactivity" ADD CONSTRAINT ck_crm_activity CHECK (
    kind IN ('task', 'call', 'meeting', 'note')
    AND ((lead_id IS NULL) <> (opportunity_id IS NULL))
);
CREATE INDEX ix_opportunity_org_stage ON "opportunity" (organization_id, stage_id);
CREATE INDEX ix_lead_org_status ON "lead" (organization_id, status);

INSERT INTO "pipelinestage" (id, organization_id, name, position, kind, is_active)
SELECT gen_random_uuid(), o.id, s.name, s.position, s.kind, TRUE
FROM "organization" o
CROSS JOIN (VALUES
    ('Novo', 1, 'open'), ('Qualificado', 2, 'open'), ('Proposta', 3, 'open'),
    ('Negociação', 4, 'open'), ('Ganho', 5, 'won'), ('Perdido', 6, 'lost')
) AS s(name, position, kind)
ON CONFLICT (organization_id, name) DO NOTHING;
"""


class Migration(migrations.Migration):
    dependencies = [('models', '0022_sales')]

    initial = False

    operations = [
        ops.CreateModel(
            name='Lead',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='leads', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_leads', on_delete=OnDelete.RESTRICT)),
                ('name', fields.CharField(max_length=160)),
                ('company', fields.CharField(null=True, max_length=160)),
                ('email', fields.CharField(null=True, max_length=254)),
                ('phone', fields.CharField(null=True, max_length=40)),
                ('source', fields.CharField(null=True, max_length=60)),
                ('status', fields.CharField(default='open', max_length=12)),
                ('counterparty', fields.ForeignKeyField('models.Counterparty', source_field='counterparty_id', null=True, db_constraint=True, to_field='id', related_name='leads', on_delete=OnDelete.RESTRICT)),
                ('notes', fields.TextField(null=True, unique=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'lead', 'app': 'models', 'pk_attr': 'id', 'table_description': 'Contato comercial que ainda não é cliente.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='PipelineStage',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='pipeline_stages', on_delete=OnDelete.CASCADE)),
                ('name', fields.CharField(max_length=60)),
                ('position', fields.IntField()),
                ('kind', fields.CharField(default='open', max_length=8)),
                ('is_active', fields.BooleanField(default=True)),
            ],
            options={'table': 'pipelinestage', 'app': 'models', 'unique_together': (('organization', 'name'),), 'pk_attr': 'id', 'table_description': 'Etapa do funil de vendas. `kind` diz se a oportunidade está aberta, ganha ou perdida.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='Opportunity',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='opportunities', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_opportunities', on_delete=OnDelete.RESTRICT)),
                ('owner', fields.ForeignKeyField('models.User', source_field='owner_id', db_constraint=True, to_field='id', related_name='owned_opportunities', on_delete=OnDelete.RESTRICT)),
                ('title', fields.CharField(max_length=160)),
                ('lead', fields.ForeignKeyField('models.Lead', source_field='lead_id', null=True, db_constraint=True, to_field='id', related_name='opportunities', on_delete=OnDelete.RESTRICT)),
                ('counterparty', fields.ForeignKeyField('models.Counterparty', source_field='counterparty_id', null=True, db_constraint=True, to_field='id', related_name='opportunities', on_delete=OnDelete.RESTRICT)),
                ('stage', fields.ForeignKeyField('models.PipelineStage', source_field='stage_id', db_constraint=True, to_field='id', related_name='opportunities', on_delete=OnDelete.RESTRICT)),
                ('expected_value', fields.DecimalField(default=0, max_digits=15, decimal_places=2)),
                ('expected_close', fields.DateField(null=True)),
                ('lost_reason', fields.CharField(null=True, max_length=240)),
                ('sales_order', fields.ForeignKeyField('models.SalesOrder', source_field='sales_order_id', null=True, db_constraint=True, to_field='id', related_name='opportunities', on_delete=OnDelete.SET_NULL)),
                ('closed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'opportunity', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='CrmActivity',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='crm_activities', on_delete=OnDelete.CASCADE)),
                ('created_by', fields.ForeignKeyField('models.User', source_field='created_by_id', db_constraint=True, to_field='id', related_name='created_crm_activities', on_delete=OnDelete.RESTRICT)),
                ('kind', fields.CharField(default='task', max_length=8)),
                ('subject', fields.CharField(max_length=200)),
                ('due_on', fields.DateField(null=True)),
                ('done_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('lead', fields.ForeignKeyField('models.Lead', source_field='lead_id', null=True, db_constraint=True, to_field='id', related_name='activities', on_delete=OnDelete.CASCADE)),
                ('opportunity', fields.ForeignKeyField('models.Opportunity', source_field='opportunity_id', null=True, db_constraint=True, to_field='id', related_name='activities', on_delete=OnDelete.CASCADE)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'crmactivity', 'app': 'models', 'pk_attr': 'id', 'table_description': 'Tarefa, ligação, reunião ou nota ligada a um lead ou a uma oportunidade.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='OpportunityStageChange',
            fields=[
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('opportunity', fields.ForeignKeyField('models.Opportunity', source_field='opportunity_id', db_constraint=True, to_field='id', related_name='stage_changes', on_delete=OnDelete.CASCADE)),
                ('from_stage', fields.ForeignKeyField('models.PipelineStage', source_field='from_stage_id', null=True, db_constraint=True, to_field='id', related_name='changes_from', on_delete=OnDelete.RESTRICT)),
                ('to_stage', fields.ForeignKeyField('models.PipelineStage', source_field='to_stage_id', db_constraint=True, to_field='id', related_name='changes_to', on_delete=OnDelete.RESTRICT)),
                ('changed_by', fields.ForeignKeyField('models.User', source_field='changed_by_id', db_constraint=True, to_field='id', related_name='opportunity_stage_changes', on_delete=OnDelete.RESTRICT)),
                ('note', fields.CharField(null=True, max_length=240)),
                ('changed_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'opportunitystagechange', 'app': 'models', 'pk_attr': 'id', 'table_description': 'Histórico de mudanças de etapa de uma oportunidade.'},
            bases=['Model'],
        ),
        ops.RunSQL(sql=FORWARD_SQL, reverse_sql=""),
    ]
