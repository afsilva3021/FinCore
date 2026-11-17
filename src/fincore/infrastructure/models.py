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
    # Incrementado no logout: invalida todos os tokens de sessão já emitidos.
    session_version = fields.IntField(default=1)


class LoginThrottle(Model):
    """Contador de falhas de login por chave (e-mail ou IP) para bloqueio temporário."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    key = fields.CharField(max_length=320, unique=True)
    failures = fields.IntField(default=0)
    window_started_at = fields.DatetimeField()
    locked_until = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)


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
    bank_code = fields.CharField(max_length=4, null=True)
    branch_number = fields.CharField(max_length=30, null=True)
    account_number = fields.CharField(max_length=30, null=True)
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
    person_type = fields.CharField(max_length=12, default="individual")
    trade_name = fields.CharField(max_length=150, null=True)
    tax_id = fields.CharField(max_length=14, null=True)
    is_customer = fields.BooleanField(default=False)
    is_supplier = fields.BooleanField(default=False)
    email = fields.CharField(max_length=254, null=True)
    phone = fields.CharField(max_length=40, null=True)
    postal_code = fields.CharField(max_length=9, null=True)
    street = fields.CharField(max_length=160, null=True)
    address_number = fields.CharField(max_length=20, null=True)
    complement = fields.CharField(max_length=100, null=True)
    district = fields.CharField(max_length=100, null=True)
    city = fields.CharField(max_length=100, null=True)
    state = fields.CharField(max_length=2, null=True)
    notes = fields.TextField(null=True)
    is_active = fields.BooleanField(default=True)
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
    # Chave de idempotência da criação (API); fica só na 1ª parcela do grupo.
    operation_key = fields.UUIDField(null=True, unique=True)
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
    # `manual` = cadastrado à mão (lança só na baixa). `purchase`/`sale` = gerado por um
    # documento e já lançado por competência contra `accrual_account`.
    origin = fields.CharField(max_length=12, default="manual")
    accrual_account = fields.ForeignKeyField(
        "models.LedgerAccount", related_name="accrual_titles", null=True, on_delete=fields.RESTRICT
    )
    source_type = fields.CharField(max_length=24, null=True)
    source_id = fields.UUIDField(null=True, db_index=True)
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


# --- Cadastros de produto (ERP, fase 8) -------------------------------------------------


class ProductGroup(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="product_groups", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_product_groups", null=True, on_delete=fields.SET_NULL
    )
    parent = fields.ForeignKeyField(
        "models.ProductGroup", related_name="children", null=True, on_delete=fields.RESTRICT
    )
    name = fields.CharField(max_length=120)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "name"),)


class UnitOfMeasure(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="units_of_measure", on_delete=fields.CASCADE
    )
    code = fields.CharField(max_length=6)
    name = fields.CharField(max_length=60)
    # Casas decimais aceitas nas quantidades desta unidade (0 = só inteiros).
    decimal_places = fields.SmallIntField(default=0)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "code"),)


class Warehouse(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="warehouses", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_warehouses", null=True, on_delete=fields.SET_NULL
    )
    code = fields.CharField(max_length=20)
    name = fields.CharField(max_length=120)
    # Exatamente um depósito padrão por organização (índice único parcial no banco).
    is_default = fields.BooleanField(default=False)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "code"),)


class Product(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="products", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_products", on_delete=fields.RESTRICT
    )
    group = fields.ForeignKeyField(
        "models.ProductGroup", related_name="products", null=True, on_delete=fields.RESTRICT
    )
    unit = fields.ForeignKeyField(
        "models.UnitOfMeasure", related_name="products", on_delete=fields.RESTRICT
    )
    sku = fields.CharField(max_length=40)
    name = fields.CharField(max_length=160)
    description = fields.TextField(null=True)
    # GTIN/EAN; único por organização quando informado (índice único parcial no banco).
    barcode = fields.CharField(max_length=14, null=True)
    sale_price = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = fields.CharField(max_length=3, default="BRL")
    min_stock = fields.DecimalField(max_digits=15, decimal_places=4, default=0)
    tracks_stock = fields.BooleanField(default=True)
    # Referência fiscal informada pelo usuário; não é validada contra a tabela oficial.
    ncm = fields.CharField(max_length=8, null=True)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("organization", "sku"),)


# --- Estoque (ERP, fase 9) --------------------------------------------------------------


class StockBalance(Model):
    """Posição de um produto em um depósito.

    É um cache: a fonte da verdade são os `StockMovement`. Só é alterado dentro da
    transação que grava o movimento, com a linha bloqueada.
    """

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="stock_balances", on_delete=fields.CASCADE
    )
    product = fields.ForeignKeyField(
        "models.Product", related_name="stock_balances", on_delete=fields.RESTRICT
    )
    warehouse = fields.ForeignKeyField(
        "models.Warehouse", related_name="stock_balances", on_delete=fields.RESTRICT
    )
    quantity = fields.DecimalField(max_digits=15, decimal_places=4, default=0)
    # Valor do estoque ao custo médio; custo médio = total_value / quantity.
    total_value = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    # Quantos movimentos já passaram por esta posição; numera o próximo movimento.
    movement_count = fields.IntField(default=0)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("product", "warehouse"),)


class StockMovement(Model):
    """Movimento de estoque efetivado. Imutável: correção só por movimento de estorno."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="stock_movements", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_stock_movements", on_delete=fields.RESTRICT
    )
    product = fields.ForeignKeyField(
        "models.Product", related_name="stock_movements", on_delete=fields.RESTRICT
    )
    warehouse = fields.ForeignKeyField(
        "models.Warehouse", related_name="stock_movements", on_delete=fields.RESTRICT
    )
    # Ordem do movimento dentro do par produto/depósito (1, 2, 3...).
    sequence = fields.IntField()
    direction = fields.CharField(max_length=3)
    reason = fields.CharField(max_length=16)
    quantity = fields.DecimalField(max_digits=15, decimal_places=4)
    unit_cost = fields.DecimalField(max_digits=15, decimal_places=6)
    total_cost = fields.DecimalField(max_digits=15, decimal_places=2)
    # Posição logo depois deste movimento (para o kardex e para auditoria).
    balance_quantity = fields.DecimalField(max_digits=15, decimal_places=4)
    balance_value = fields.DecimalField(max_digits=15, decimal_places=2)
    occurred_on = fields.DateField()
    notes = fields.CharField(max_length=240, null=True)
    # Chave de idempotência; numa transferência fica só na perna de saída.
    operation_key = fields.UUIDField(null=True, unique=True)
    transfer_id = fields.UUIDField(null=True, db_index=True)
    reversal_of = fields.OneToOneField(
        "models.StockMovement", related_name="reversal", null=True, on_delete=fields.RESTRICT
    )
    journal_entry = fields.ForeignKeyField(
        "models.JournalEntry", related_name="stock_movements", null=True, on_delete=fields.RESTRICT
    )
    # Documento que originou o movimento (compras e vendas, nas próximas fases).
    source_type = fields.CharField(max_length=24, null=True)
    source_id = fields.UUIDField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("product", "warehouse", "sequence"),)


# --- Numeração de documentos e compras (ERP, fase 10) -----------------------------------


class DocumentSequence(Model):
    """Último número usado por tipo de documento em cada organização."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="document_sequences", on_delete=fields.CASCADE
    )
    kind = fields.CharField(max_length=24)
    last_number = fields.IntField(default=0)

    class Meta:
        unique_together = (("organization", "kind"),)


class PurchaseOrder(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="purchase_orders", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_purchase_orders", on_delete=fields.RESTRICT
    )
    number = fields.IntField()
    supplier = fields.ForeignKeyField(
        "models.Counterparty", related_name="purchase_orders", on_delete=fields.RESTRICT
    )
    warehouse = fields.ForeignKeyField(
        "models.Warehouse", related_name="purchase_orders", on_delete=fields.RESTRICT
    )
    # Conta financeira e categoria sugeridas para os títulos a pagar gerados no recebimento.
    account = fields.ForeignKeyField(
        "models.FinancialAccount", related_name="purchase_orders", on_delete=fields.RESTRICT
    )
    status = fields.CharField(max_length=20, default="draft")
    order_date = fields.DateField()
    expected_date = fields.DateField(null=True)
    payment_method = fields.CharField(max_length=24, default="boleto")
    installment_count = fields.IntField(default=1)
    # Dias entre o recebimento e o vencimento da primeira parcela.
    first_due_days = fields.IntField(default=30)
    notes = fields.TextField(null=True)
    operation_key = fields.UUIDField(null=True, unique=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "number"),)


class PurchaseOrderItem(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    order = fields.ForeignKeyField(
        "models.PurchaseOrder", related_name="items", on_delete=fields.CASCADE
    )
    product = fields.ForeignKeyField(
        "models.Product", related_name="purchase_items", on_delete=fields.RESTRICT
    )
    position = fields.IntField()
    quantity = fields.DecimalField(max_digits=15, decimal_places=4)
    unit_cost = fields.DecimalField(max_digits=15, decimal_places=6)
    # Cache do que já foi recebido (recebimentos não estornados); muda só com o pedido bloqueado.
    received_quantity = fields.DecimalField(max_digits=15, decimal_places=4, default=0)

    class Meta:
        unique_together = (("order", "position"),)


class PurchaseReceipt(Model):
    """Recebimento de mercadoria: entra no estoque e gera contas a pagar."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="purchase_receipts", on_delete=fields.CASCADE
    )
    order = fields.ForeignKeyField(
        "models.PurchaseOrder", related_name="receipts", on_delete=fields.RESTRICT
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_purchase_receipts", on_delete=fields.RESTRICT
    )
    number = fields.IntField()
    received_on = fields.DateField()
    document_number = fields.CharField(max_length=60, null=True)
    total = fields.DecimalField(max_digits=15, decimal_places=2)
    status = fields.CharField(max_length=12, default="posted")
    operation_key = fields.UUIDField(unique=True)
    journal_entry = fields.ForeignKeyField(
        "models.JournalEntry",
        related_name="purchase_receipts",
        null=True,
        on_delete=fields.RESTRICT,
    )
    reversed_at = fields.DatetimeField(null=True)
    reversed_by = fields.ForeignKeyField(
        "models.User",
        related_name="reversed_purchase_receipts",
        null=True,
        on_delete=fields.RESTRICT,
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "number"),)


class PurchaseReceiptItem(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    receipt = fields.ForeignKeyField(
        "models.PurchaseReceipt", related_name="items", on_delete=fields.CASCADE
    )
    order_item = fields.ForeignKeyField(
        "models.PurchaseOrderItem", related_name="receipt_items", on_delete=fields.RESTRICT
    )
    quantity = fields.DecimalField(max_digits=15, decimal_places=4)
    unit_cost = fields.DecimalField(max_digits=15, decimal_places=6)
    total_cost = fields.DecimalField(max_digits=15, decimal_places=2)
    stock_movement = fields.OneToOneField(
        "models.StockMovement", related_name="purchase_receipt_item", on_delete=fields.RESTRICT
    )


# --- Vendas (ERP, fase 11) --------------------------------------------------------------


class SalesOrder(Model):
    """Orçamento que vira pedido de venda. Só o faturamento mexe em estoque e financeiro."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="sales_orders", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_sales_orders", on_delete=fields.RESTRICT
    )
    number = fields.IntField()
    customer = fields.ForeignKeyField(
        "models.Counterparty", related_name="sales_orders", on_delete=fields.RESTRICT
    )
    warehouse = fields.ForeignKeyField(
        "models.Warehouse", related_name="sales_orders", on_delete=fields.RESTRICT
    )
    # Conta financeira sugerida para os títulos a receber gerados no faturamento.
    account = fields.ForeignKeyField(
        "models.FinancialAccount", related_name="sales_orders", on_delete=fields.RESTRICT
    )
    status = fields.CharField(max_length=12, default="quote")
    order_date = fields.DateField()
    # Validade do orçamento; depois dela não pode mais ser confirmado.
    valid_until = fields.DateField(null=True)
    payment_method = fields.CharField(max_length=24, default="pix")
    installment_count = fields.IntField(default=1)
    # Dias entre o faturamento e o vencimento da primeira parcela.
    first_due_days = fields.IntField(default=0)
    notes = fields.TextField(null=True)
    operation_key = fields.UUIDField(null=True, unique=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "number"),)


class SalesOrderItem(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    order = fields.ForeignKeyField(
        "models.SalesOrder", related_name="items", on_delete=fields.CASCADE
    )
    product = fields.ForeignKeyField(
        "models.Product", related_name="sales_items", on_delete=fields.RESTRICT
    )
    position = fields.IntField()
    quantity = fields.DecimalField(max_digits=15, decimal_places=4)
    unit_price = fields.DecimalField(max_digits=15, decimal_places=2)
    # Desconto em reais sobre a linha (quantidade x preço).
    discount = fields.DecimalField(max_digits=15, decimal_places=2, default=0)

    class Meta:
        unique_together = (("order", "position"),)


class SalesInvoice(Model):
    """Faturamento interno do pedido: baixa estoque, gera contas a receber, receita e CMV."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="sales_invoices", on_delete=fields.CASCADE
    )
    order = fields.ForeignKeyField(
        "models.SalesOrder", related_name="invoices", on_delete=fields.RESTRICT
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_sales_invoices", on_delete=fields.RESTRICT
    )
    number = fields.IntField()
    issued_on = fields.DateField()
    total = fields.DecimalField(max_digits=15, decimal_places=2)
    # Custo das mercadorias vendidas, ao custo médio do momento do faturamento.
    cost_total = fields.DecimalField(max_digits=15, decimal_places=2)
    status = fields.CharField(max_length=12, default="posted")
    operation_key = fields.UUIDField(unique=True)
    revenue_entry = fields.ForeignKeyField(
        "models.JournalEntry",
        related_name="sales_revenue_invoices",
        null=True,
        on_delete=fields.RESTRICT,
    )
    cost_entry = fields.ForeignKeyField(
        "models.JournalEntry",
        related_name="sales_cost_invoices",
        null=True,
        on_delete=fields.RESTRICT,
    )
    reversed_at = fields.DatetimeField(null=True)
    reversed_by = fields.ForeignKeyField(
        "models.User", related_name="reversed_sales_invoices", null=True, on_delete=fields.RESTRICT
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "number"),)


class SalesInvoiceItem(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    invoice = fields.ForeignKeyField(
        "models.SalesInvoice", related_name="items", on_delete=fields.CASCADE
    )
    order_item = fields.ForeignKeyField(
        "models.SalesOrderItem", related_name="invoice_items", on_delete=fields.RESTRICT
    )
    quantity = fields.DecimalField(max_digits=15, decimal_places=4)
    total = fields.DecimalField(max_digits=15, decimal_places=2)
    cost = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    # Vazio para produtos que não controlam estoque.
    stock_movement = fields.OneToOneField(
        "models.StockMovement",
        related_name="sales_invoice_item",
        null=True,
        on_delete=fields.RESTRICT,
    )


# --- CRM (ERP, fase 12) -----------------------------------------------------------------


class PipelineStage(Model):
    """Etapa do funil de vendas. `kind` diz se a oportunidade está aberta, ganha ou perdida."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="pipeline_stages", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=60)
    position = fields.IntField()
    kind = fields.CharField(max_length=8, default="open")
    is_active = fields.BooleanField(default=True)

    class Meta:
        unique_together = (("organization", "name"),)


class Lead(Model):
    """Contato comercial que ainda não é cliente."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="leads", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_leads", on_delete=fields.RESTRICT
    )
    name = fields.CharField(max_length=160)
    company = fields.CharField(max_length=160, null=True)
    email = fields.CharField(max_length=254, null=True)
    phone = fields.CharField(max_length=40, null=True)
    source = fields.CharField(max_length=60, null=True)
    status = fields.CharField(max_length=12, default="open")
    # Cliente criado ou vinculado quando o lead é convertido.
    counterparty = fields.ForeignKeyField(
        "models.Counterparty", related_name="leads", null=True, on_delete=fields.RESTRICT
    )
    notes = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)


class Opportunity(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="opportunities", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_opportunities", on_delete=fields.RESTRICT
    )
    owner = fields.ForeignKeyField(
        "models.User", related_name="owned_opportunities", on_delete=fields.RESTRICT
    )
    title = fields.CharField(max_length=160)
    lead = fields.ForeignKeyField(
        "models.Lead", related_name="opportunities", null=True, on_delete=fields.RESTRICT
    )
    counterparty = fields.ForeignKeyField(
        "models.Counterparty", related_name="opportunities", null=True, on_delete=fields.RESTRICT
    )
    stage = fields.ForeignKeyField(
        "models.PipelineStage", related_name="opportunities", on_delete=fields.RESTRICT
    )
    expected_value = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    expected_close = fields.DateField(null=True)
    lost_reason = fields.CharField(max_length=240, null=True)
    # Orçamento gerado a partir desta oportunidade.
    sales_order = fields.ForeignKeyField(
        "models.SalesOrder", related_name="opportunities", null=True, on_delete=fields.SET_NULL
    )
    closed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)


class OpportunityStageChange(Model):
    """Histórico de mudanças de etapa de uma oportunidade."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    opportunity = fields.ForeignKeyField(
        "models.Opportunity", related_name="stage_changes", on_delete=fields.CASCADE
    )
    from_stage = fields.ForeignKeyField(
        "models.PipelineStage", related_name="changes_from", null=True, on_delete=fields.RESTRICT
    )
    to_stage = fields.ForeignKeyField(
        "models.PipelineStage", related_name="changes_to", on_delete=fields.RESTRICT
    )
    changed_by = fields.ForeignKeyField(
        "models.User", related_name="opportunity_stage_changes", on_delete=fields.RESTRICT
    )
    note = fields.CharField(max_length=240, null=True)
    changed_at = fields.DatetimeField(auto_now_add=True)


class CrmActivity(Model):
    """Tarefa, ligação, reunião ou nota ligada a um lead ou a uma oportunidade."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="crm_activities", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_crm_activities", on_delete=fields.RESTRICT
    )
    kind = fields.CharField(max_length=8, default="task")
    subject = fields.CharField(max_length=200)
    due_on = fields.DateField(null=True)
    done_at = fields.DatetimeField(null=True)
    lead = fields.ForeignKeyField(
        "models.Lead", related_name="activities", null=True, on_delete=fields.CASCADE
    )
    opportunity = fields.ForeignKeyField(
        "models.Opportunity", related_name="activities", null=True, on_delete=fields.CASCADE
    )
    created_at = fields.DatetimeField(auto_now_add=True)


# --- Fiscal simulado (ERP, fase 13) -----------------------------------------------------
# Nada aqui tem valor fiscal: não há XML, assinatura nem transmissão à SEFAZ.


class TaxProfile(Model):
    """Dados tributários informados pelo usuário para a simulação (não são conferidos)."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="tax_profiles", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=80)
    cfop = fields.CharField(max_length=4)
    # CST (2 dígitos) ou CSOSN (3 dígitos) do ICMS.
    icms_cst = fields.CharField(max_length=3)
    icms_rate = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    pis_rate = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    cofins_rate = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "name"),)


class FiscalDocument(Model):
    """Documento fiscal interno gerado de um faturamento. Simulação, sem valor fiscal."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="fiscal_documents", on_delete=fields.CASCADE
    )
    created_by = fields.ForeignKeyField(
        "models.User", related_name="created_fiscal_documents", on_delete=fields.RESTRICT
    )
    number = fields.IntField()
    series = fields.CharField(max_length=3, default="1")
    sales_invoice = fields.ForeignKeyField(
        "models.SalesInvoice", related_name="fiscal_documents", on_delete=fields.RESTRICT
    )
    tax_profile = fields.ForeignKeyField(
        "models.TaxProfile", related_name="fiscal_documents", on_delete=fields.RESTRICT
    )
    customer = fields.ForeignKeyField(
        "models.Counterparty", related_name="fiscal_documents", on_delete=fields.RESTRICT
    )
    issued_on = fields.DateField()
    total_products = fields.DecimalField(max_digits=15, decimal_places=2)
    total_icms = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_pis = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_cofins = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    status = fields.CharField(max_length=10, default="issued")
    cancel_reason = fields.CharField(max_length=240, null=True)
    cancelled_at = fields.DatetimeField(null=True)
    operation_key = fields.UUIDField(unique=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("organization", "series", "number"),)


class FiscalDocumentItem(Model):
    id = fields.UUIDField(primary_key=True, default=uuid4)
    document = fields.ForeignKeyField(
        "models.FiscalDocument", related_name="items", on_delete=fields.CASCADE
    )
    position = fields.IntField()
    product = fields.ForeignKeyField(
        "models.Product", related_name="fiscal_items", on_delete=fields.RESTRICT
    )
    # Cópia dos dados no momento da emissão: o documento não muda se o cadastro mudar.
    description = fields.CharField(max_length=160)
    ncm = fields.CharField(max_length=8, null=True)
    cfop = fields.CharField(max_length=4)
    unit = fields.CharField(max_length=6)
    quantity = fields.DecimalField(max_digits=15, decimal_places=4)
    unit_price = fields.DecimalField(max_digits=15, decimal_places=2)
    total = fields.DecimalField(max_digits=15, decimal_places=2)
    icms_amount = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    pis_amount = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
    cofins_amount = fields.DecimalField(max_digits=15, decimal_places=2, default=0)
