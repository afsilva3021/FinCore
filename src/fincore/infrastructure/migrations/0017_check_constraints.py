"""CHECK constraints: as regras de valor e de estado passam a valer também no banco.

Cada constraint é criada como NOT VALID (vale para toda escrita nova) e em seguida
validada. Se um banco antigo tiver linhas fora da regra, a validação é pulada com um
aviso (NOTICE) em vez de derrubar a migration; corrija os dados e rode
`ALTER TABLE <tabela> VALIDATE CONSTRAINT <nome>`.
"""

from tortoise import migrations
from tortoise.migrations import operations as ops

CONSTRAINTS = [
    ("financialtransaction", "ck_transaction_amount_positive", "amount > 0"),
    (
        "financialtransaction",
        "ck_transaction_type",
        "transaction_type IN ('income', 'expense', 'transfer_in', 'transfer_out')",
    ),
    ("financialtransaction", "ck_transaction_status", "status IN ('open', 'settled')"),
    ("financialtransaction", "ck_transaction_currency", "currency ~ '^[A-Z]{3}$'"),
    ("financialtitle", "ck_title_amount_positive", "original_amount > 0"),
    ("financialtitle", "ck_title_type", "title_type IN ('payable', 'receivable')"),
    (
        "financialtitle",
        "ck_title_status",
        "status IN ('open', 'partial', 'settled', 'cancelled')",
    ),
    (
        "titlesettlement",
        "ck_settlement_amounts",
        "amount_applied > 0 AND net_amount > 0 AND discount >= 0 AND interest >= 0 "
        "AND fine >= 0 AND fees >= 0",
    ),
    (
        "journalentryline",
        "ck_journal_line_one_side",
        "debit >= 0 AND credit >= 0 AND ((debit > 0) <> (credit > 0))",
    ),
    ("journalentry", "ck_journal_entry_status", "status IN ('draft', 'posted')"),
    (
        "ledgeraccount",
        "ck_ledger_account_type",
        "account_type IN ('asset', 'liability', 'equity', 'revenue', 'expense')",
    ),
    (
        "financialaccount",
        "ck_financial_account",
        "account_type IN ('bank', 'cash', 'wallet') AND opening_balance >= 0",
    ),
    (
        "category",
        "ck_category",
        "transaction_type IN ('income', 'expense') "
        "AND category_kind IN ('synthetic', 'analytic')",
    ),
    (
        "counterparty",
        "ck_counterparty_types",
        "party_type IN ('customer', 'supplier', 'other') "
        "AND person_type IN ('individual', 'company')",
    ),
    ("reconciliationevent", "ck_reconciliation_action", "action IN ('matched', 'unmatched')"),
    ("membership", "ck_membership_role", "role IN ('owner', 'finance', 'viewer')"),
    ("loginthrottle", "ck_login_throttle_failures", "failures >= 0"),
]


def _add(table: str, name: str, condition: str) -> str:
    return f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
        ALTER TABLE "{table}" ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID;
    END IF;
    BEGIN
        ALTER TABLE "{table}" VALIDATE CONSTRAINT {name};
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'Constraint % criada, mas há linhas antigas fora da regra em %; '
            'corrija-as e rode VALIDATE CONSTRAINT.', '{name}', '{table}';
    END;
END $$;"""


class Migration(migrations.Migration):
    dependencies = [("models", "0016_login_throttle_session_version")]

    initial = False

    operations = [
        ops.RunSQL(
            sql="".join(_add(*item) for item in CONSTRAINTS),
            reverse_sql="".join(
                f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS {name};'
                for table, name, _ in CONSTRAINTS
            ),
        )
    ]
