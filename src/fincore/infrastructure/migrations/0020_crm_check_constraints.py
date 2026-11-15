"""CHECK constraints do CRM: situações, tipos e valores também valem no banco.

As tabelas do CRM nascem na migration 0019 e ainda estão vazias, então as constraints
são criadas já validadas.
"""

from tortoise import migrations
from tortoise.migrations import operations as ops

CONSTRAINTS = [
    ("crmpipelinestage", "ck_crm_stage_probability", "win_probability BETWEEN 0 AND 100"),
    (
        "crmlead",
        "ck_crm_lead_status",
        "status IN ('new', 'contacted', 'qualified', 'disqualified', 'converted')",
    ),
    (
        "crmlead",
        "ck_crm_lead_source",
        "source IN ('website', 'referral', 'cold_call', 'event', 'social', 'other')",
    ),
    ("crmlead", "ck_crm_lead_value", "estimated_value IS NULL OR estimated_value >= 0"),
    (
        "crmlead",
        "ck_crm_lead_converted",
        "(status = 'converted') = (converted_counterparty_id IS NOT NULL)",
    ),
    ("crmdeal", "ck_crm_deal_status", "status IN ('open', 'won', 'lost')"),
    ("crmdeal", "ck_crm_deal_amount", "amount >= 0"),
    (
        "crmdeal",
        "ck_crm_deal_closed",
        "(status = 'open') = (closed_at IS NULL) "
        "AND (status <> 'lost' OR lost_reason IS NOT NULL)",
    ),
    (
        "crmdealevent",
        "ck_crm_deal_event_action",
        "action IN ('created', 'updated', 'stage_changed', 'won', 'lost', 'reopened')",
    ),
    (
        "crmactivity",
        "ck_crm_activity_type",
        "activity_type IN ('task', 'call', 'meeting', 'email', 'note')",
    ),
    (
        "crmactivity",
        "ck_crm_activity_one_link",
        "num_nonnulls(lead_id, deal_id, counterparty_id) = 1",
    ),
    (
        "crmproposal",
        "ck_crm_proposal_status",
        "status IN ('draft', 'sent', 'accepted', 'rejected')",
    ),
    (
        "crmproposal",
        "ck_crm_proposal_values",
        "number > 0 AND discount >= 0 AND valid_until >= issue_date",
    ),
    (
        "crmproposal",
        "ck_crm_proposal_accepted_title",
        "(status = 'accepted') = (first_title_id IS NOT NULL)",
    ),
    (
        "crmproposalitem",
        "ck_crm_proposal_item_values",
        "quantity > 0 AND unit_price > 0 AND total > 0",
    ),
]


class Migration(migrations.Migration):
    dependencies = [("models", "0019_crm")]

    initial = False

    operations = [
        ops.RunSQL(
            sql="".join(
                f'ALTER TABLE "{table}" ADD CONSTRAINT {name} CHECK ({condition});'
                for table, name, condition in CONSTRAINTS
            ),
            reverse_sql="".join(
                f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS {name};'
                for table, name, _ in CONSTRAINTS
            ),
        )
    ]
