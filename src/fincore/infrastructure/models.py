"""Modelos persistentes de identidade e isolamento por organização."""

from uuid import uuid4

from tortoise import fields
from tortoise.models import Model


class Organization(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    name = fields.CharField(max_length=160)
    created_at = fields.DatetimeField(auto_now_add=True)
    is_active = fields.BooleanField(default=True)


class User(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    email = fields.CharField(max_length=254, unique=True, db_index=True)
    password_hash = fields.CharField(max_length=256)
    full_name = fields.CharField(max_length=160)
    created_at = fields.DatetimeField(auto_now_add=True)
    is_active = fields.BooleanField(default=True)


class Membership(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    user = fields.ForeignKeyField(
        "models.User", related_name="memberships", on_delete=fields.CASCADE
    )
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="memberships", on_delete=fields.CASCADE
    )
    role = fields.CharField(max_length=32, default="owner")
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("user", "organization"),)


class FinancialAccount(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="financial_accounts", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_accounts", on_delete=fields.RESTRICT, null=True
    )
    ledger_account = fields.ForeignKeyField(
        "models.LedgerAccount",
        related_name="financial_accounts",
        null=True,
        on_delete=fields.RESTRICT,
    )
    name = fields.CharField(max_length=120)
    bank_name = fields.CharField(max_length=120, null=True)
    account_type = fields.CharField(max_length=20, default="bank")
    currency = fields.CharField(max_length=3, default="BRL")
    opening_balance = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "name"),)


class Category(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="categories", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_categories", on_delete=fields.RESTRICT, null=True
    )
    ledger_account = fields.ForeignKeyField(
        "models.LedgerAccount",
        related_name="categories",
        null=True,
        on_delete=fields.RESTRICT,
    )
    parent = fields.ForeignKeyField(
        "models.Category", related_name="children", null=True, on_delete=fields.RESTRICT
    )
    name = fields.CharField(max_length=100)
    transaction_type = fields.CharField(max_length=12)
    category_kind = fields.CharField(max_length=12, default="analytic")
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "name", "transaction_type"),)


class FinancialTransaction(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    operation_key = fields.UUIDField(unique=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="transactions", on_delete=fields.CASCADE
    )
    account = fields.ForeignKeyField(
        "models.FinancialAccount", related_name="transactions", on_delete=fields.RESTRICT
    )
    category = fields.ForeignKeyField(
        "models.Category", related_name="transactions", null=True, on_delete=fields.RESTRICT
    )
    reversal_of = fields.OneToOneField(
        "models.FinancialTransaction",
        related_name="reversal",
        null=True,
        on_delete=fields.RESTRICT,
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="financial_transactions", on_delete=fields.RESTRICT
    )
    settled_by = fields.ForeignKeyField(
        "models.User",
        related_name="settled_financial_transactions",
        null=True,
        on_delete=fields.RESTRICT,
    )
    transfer_id = fields.UUIDField(null=True, db_index=True)
    transaction_type = fields.CharField(max_length=16)
    description = fields.CharField(max_length=240)
    amount = fields.DecimalField(max_digits=15, decimal_places=2)
    currency = fields.CharField(max_length=3, default="BRL")
    competence_date = fields.DateField()
    due_date = fields.DateField(null=True)
    settled_at = fields.DatetimeField(null=True)
    status = fields.CharField(max_length=12, default="open")
    created_at = fields.DatetimeField(auto_now_add=True)


class Counterparty(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="counterparties", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=160)
    party_type = fields.CharField(max_length=12)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "name", "party_type"),)


class FinancialTitle(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="financial_titles", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_financial_titles", on_delete=fields.RESTRICT
    )
    counterparty = fields.ForeignKeyField(
        "models.Counterparty", related_name="titles", on_delete=fields.RESTRICT
    )
    account = fields.ForeignKeyField(
        "models.FinancialAccount", related_name="titles", on_delete=fields.RESTRICT
    )
    category = fields.ForeignKeyField(
        "models.Category", related_name="titles", null=True, on_delete=fields.RESTRICT
    )
    installment_group = fields.UUIDField(null=True, db_index=True)
    title_type = fields.CharField(max_length=12)
    description = fields.CharField(max_length=240)
    document_number = fields.CharField(max_length=100, null=True)
    document_type = fields.CharField(max_length=40, null=True)
    document_series = fields.CharField(max_length=30, null=True)
    cost_center = fields.CharField(max_length=120, null=True)
    project = fields.CharField(max_length=120, null=True)
    original_amount = fields.DecimalField(max_digits=15, decimal_places=2)
    currency = fields.CharField(max_length=3, default="BRL")
    issue_date = fields.DateField()
    due_date = fields.DateField()
    expected_receipt_date = fields.DateField(null=True)
    collection_sent_date = fields.DateField(null=True)
    payment_method = fields.CharField(max_length=24, default="other")
    payment_terms = fields.CharField(max_length=16, default="cash")
    installment_number = fields.IntField(default=1)
    installment_count = fields.IntField(default=1)
    status = fields.CharField(max_length=12, default="open")
    barcode = fields.CharField(max_length=128, null=True)
    beneficiary = fields.CharField(max_length=160, null=True)
    approver_reference = fields.CharField(max_length=160, null=True)
    payment_details = fields.TextField(null=True)
    related_reference = fields.CharField(max_length=160, null=True)
    charge_reference = fields.CharField(max_length=160, null=True)
    attachment_reference = fields.CharField(max_length=512, null=True)
    notes = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)


class TitleSettlement(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="title_settlements", on_delete=fields.CASCADE
    )
    title = fields.ForeignKeyField(
        "models.FinancialTitle", related_name="settlements", on_delete=fields.RESTRICT
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_title_settlements", on_delete=fields.RESTRICT
    )
    financial_transaction = fields.OneToOneField(
        "models.FinancialTransaction",
        related_name="title_settlement",
        on_delete=fields.RESTRICT,
    )
    account = fields.ForeignKeyField(
        "models.FinancialAccount", related_name="title_settlements", on_delete=fields.RESTRICT
    )
    operation_key = fields.UUIDField(unique=True, default=uuid4)
    payment_method = fields.CharField(max_length=24)
    amount_applied = fields.DecimalField(max_digits=15, decimal_places=2)
    discount = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    interest = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    fine = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    fees = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    net_amount = fields.DecimalField(max_digits=15, decimal_places=2)
    paid_on = fields.DateField()
    proof_reference = fields.CharField(max_length=512, null=True)
    notes = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)


class LedgerAccount(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="ledger_accounts", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_ledger_accounts", on_delete=fields.RESTRICT
    )
    parent = fields.ForeignKeyField(
        "models.LedgerAccount", related_name="children", null=True, on_delete=fields.RESTRICT
    )
    code = fields.CharField(max_length=32)
    name = fields.CharField(max_length=160)
    account_type = fields.CharField(max_length=16)
    is_synthetic = fields.BooleanField(default=False)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "code"),)


class JournalEntry(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    idempotency_key = fields.UUIDField(unique=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="journal_entries", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="journal_entries", on_delete=fields.RESTRICT
    )
    reversal_of = fields.OneToOneField(
        "models.JournalEntry", related_name="reversal", null=True, on_delete=fields.RESTRICT
    )
    source_transaction = fields.OneToOneField(
        "models.FinancialTransaction",
        related_name="journal_entry",
        null=True,
        on_delete=fields.RESTRICT,
    )
    entry_date = fields.DateField()
    description = fields.CharField(max_length=240)
    status = fields.CharField(max_length=12, default="draft")
    created_at = fields.DatetimeField(auto_now_add=True)
    posted_at = fields.DatetimeField(null=True)


class JournalEntryLine(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    journal_entry = fields.ForeignKeyField(
        "models.JournalEntry", related_name="lines", on_delete=fields.CASCADE
    )
    account = fields.ForeignKeyField(
        "models.LedgerAccount", related_name="journal_lines", on_delete=fields.RESTRICT
    )
    debit = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    credit = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = fields.CharField(max_length=3, default="BRL")


class ImportedStatementTransaction(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="imported_transactions", on_delete=fields.CASCADE
    )
    account = fields.ForeignKeyField(
        "models.FinancialAccount", related_name="imported_transactions", on_delete=fields.RESTRICT
    )
    external_id = fields.CharField(max_length=128)
    posted_at = fields.DateField()
    description = fields.CharField(max_length=240)
    amount = fields.DecimalField(max_digits=15, decimal_places=2)
    currency = fields.CharField(max_length=3, default="BRL")
    imported_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("account", "external_id"),)


class ReconciliationMatch(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="reconciliation_matches", on_delete=fields.CASCADE
    )
    statement_transaction = fields.OneToOneField(
        "models.ImportedStatementTransaction",
        related_name="match",
        on_delete=fields.CASCADE,
    )
    financial_transaction = fields.OneToOneField(
        "models.FinancialTransaction",
        related_name="reconciliation_match",
        on_delete=fields.RESTRICT,
    )
    matched_by = fields.ForeignKeyField(
        "models.User", related_name="reconciliation_matches", on_delete=fields.RESTRICT
    )
    matched_at = fields.DatetimeField(auto_now_add=True)


class ReconciliationEvent(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="reconciliation_events", on_delete=fields.CASCADE
    )
    statement_transaction = fields.ForeignKeyField(
        "models.ImportedStatementTransaction",
        related_name="events",
        on_delete=fields.RESTRICT,
    )
    financial_transaction = fields.ForeignKeyField(
        "models.FinancialTransaction",
        related_name="reconciliation_events",
        on_delete=fields.RESTRICT,
    )
    actor = fields.ForeignKeyField(
        "models.User", related_name="reconciliation_events", on_delete=fields.RESTRICT
    )
    action = fields.CharField(max_length=12)
    occurred_at = fields.DatetimeField(auto_now_add=True)
