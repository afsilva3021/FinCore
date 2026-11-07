"""Casos de uso do núcleo financeiro, sempre limitados à organização ativa."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from tortoise.exceptions import IntegrityError
from tortoise.functions import Sum
from tortoise.transactions import in_transaction

from fincore.application.money import parse_brl
from fincore.infrastructure.models import (
    Category,
    FinancialAccount,
    FinancialTransaction,
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    Organization,
    User,
)

CENT = Decimal("0.01")


def parse_money(value: str) -> Decimal:
    """Converte valor decimal pt-BR ou padrão sem aceitar float ou negativos."""
    return parse_brl(
        value,
        allow_zero=False,
        empty_as_zero=False,
        invalid_message="Informe um valor monetário válido.",
        range_message="O valor deve ser maior que zero e respeitar o limite permitido.",
    )


def parse_opening_balance(value: str) -> Decimal:
    """Converte saldo inicial, aceitando zero e rejeitando valores negativos."""
    return parse_brl(
        value,
        allow_zero=True,
        empty_as_zero=True,
        invalid_message="Informe um saldo inicial válido.",
        range_message="O saldo inicial deve ser positivo e respeitar o limite permitido.",
    )


async def _system_account(
    organization: Organization,
    user: User,
    code: str,
    name: str,
    account_type: str,
) -> LedgerAccount:
    account = await LedgerAccount.get_or_none(organization=organization, code=code)
    if account:
        return account
    return await LedgerAccount.create(
        organization=organization,
        created_by=user,
        code=code,
        name=name,
        account_type=account_type,
    )


async def _create_journal(
    *,
    organization: Organization,
    user: User,
    idempotency_key: UUID,
    entry_date: date,
    description: str,
    lines: list[tuple[LedgerAccount, Decimal, Decimal]],
    source_transaction: FinancialTransaction | None = None,
) -> JournalEntry:
    if source_transaction is not None:
        existing = await JournalEntry.get_or_none(source_transaction=source_transaction)
        if existing:
            return existing
    existing = await JournalEntry.get_or_none(idempotency_key=idempotency_key)
    if existing:
        return existing
    posted_at = datetime.now(UTC)
    entry = await JournalEntry.create(
        idempotency_key=idempotency_key,
        organization=organization,
        created_by=user,
        source_transaction=source_transaction,
        entry_date=entry_date,
        description=description[:240],
        status="posted",
        posted_at=posted_at,
    )
    await JournalEntryLine.bulk_create(
        [
            JournalEntryLine(
                journal_entry=entry,
                account=account,
                debit=debit.quantize(CENT),
                credit=credit.quantize(CENT),
                currency="BRL",
            )
            for account, debit, credit in lines
        ]
    )
    return entry


async def _ensure_financial_ledger_account(
    organization: Organization, user: User, account: FinancialAccount
) -> LedgerAccount:
    if account.ledger_account_id:
        return await LedgerAccount.get(id=account.ledger_account_id, organization=organization)
    ledger_account = await LedgerAccount.create(
        organization=organization,
        created_by=user,
        code=f"FIN-{uuid4().hex[:12].upper()}",
        name=account.name,
        account_type="asset",
    )
    await FinancialAccount.filter(id=account.id).update(ledger_account_id=ledger_account.id)
    if account.opening_balance:
        equity = await _system_account(
            organization, user, "SYS-EQUITY", "Patrimônio de abertura", "equity"
        )
        await _create_journal(
            organization=organization,
            user=user,
            idempotency_key=uuid5(NAMESPACE_URL, f"fincore:opening:{account.id}"),
            entry_date=account.created_at.date(),
            description=f"Saldo inicial: {account.name}"[:240],
            lines=[
                (ledger_account, account.opening_balance, Decimal("0.00")),
                (equity, Decimal("0.00"), account.opening_balance),
            ],
        )
    return ledger_account


async def _create_movement_journal(
    organization: Organization, user: User, movement: FinancialTransaction
) -> JournalEntry | None:
    if movement.status != "settled" or movement.transaction_type == "transfer_in":
        return None
    existing = await JournalEntry.get_or_none(source_transaction=movement)
    if existing:
        return existing
    source_account = await FinancialAccount.get(id=movement.account_id, organization=organization)
    source_ledger = await _ensure_financial_ledger_account(organization, user, source_account)
    if movement.transaction_type == "transfer_out":
        target_movement = await FinancialTransaction.get_or_none(
            organization=organization,
            transfer_id=movement.transfer_id,
            transaction_type="transfer_in",
        )
        if target_movement is None:
            raise ValueError("A transferência não possui o movimento de destino.")
        target_account = await FinancialAccount.get(
            id=target_movement.account_id, organization=organization
        )
        target_ledger = await _ensure_financial_ledger_account(organization, user, target_account)
        lines = [
            (target_ledger, movement.amount, Decimal("0.00")),
            (source_ledger, Decimal("0.00"), movement.amount),
        ]
        description = f"Transferência: {movement.description}"
    else:
        category = None
        if movement.category_id:
            category = await Category.get_or_none(
                id=movement.category_id, organization=organization
            )
        if category and category.ledger_account_id:
            category_ledger = await LedgerAccount.get(id=category.ledger_account_id)
        else:
            code = "SYS-REV" if movement.transaction_type == "income" else "SYS-EXP"
            name = "Receitas sem categoria" if code == "SYS-REV" else "Despesas sem categoria"
            kind = "revenue" if code == "SYS-REV" else "expense"
            category_ledger = await _system_account(organization, user, code, name, kind)
        if movement.transaction_type == "income":
            lines = [
                (source_ledger, movement.amount, Decimal("0.00")),
                (category_ledger, Decimal("0.00"), movement.amount),
            ]
        else:
            lines = [
                (category_ledger, movement.amount, Decimal("0.00")),
                (source_ledger, Decimal("0.00"), movement.amount),
            ]
        description = movement.description
    return await _create_journal(
        organization=organization,
        user=user,
        idempotency_key=uuid5(NAMESPACE_URL, f"fincore:transaction:{movement.id}"),
        entry_date=movement.competence_date,
        description=description,
        lines=lines,
        source_transaction=movement,
    )


async def create_account(
    organization: Organization,
    user: User,
    name: str,
    account_type: str,
    opening_balance: Decimal,
    bank_name: str | None = None,
    bank_code: str | None = None,
    branch_number: str | None = None,
    account_number: str | None = None,
) -> FinancialAccount:
    if not name or len(name) > 120 or account_type not in {"bank", "cash", "wallet"}:
        raise ValueError("Confira o nome e o tipo da conta.")
    if account_type == "bank" and bank_name and len(bank_name) > 120:
        raise ValueError("O nome do banco deve ter até 120 caracteres.")
    bank_code = bank_code.strip() if bank_code else None
    branch_number = branch_number.strip() if branch_number else None
    account_number = account_number.strip() if account_number else None
    if bank_code and (len(bank_code) > 4 or not bank_code.isdigit()):
        raise ValueError("O código do banco deve conter até 4 dígitos.")
    if branch_number and len(branch_number) > 30:
        raise ValueError("A agência deve ter até 30 caracteres.")
    if branch_number and not re.fullmatch(r"[0-9]{1,4}-[0-9]", branch_number):
        raise ValueError("Informe a agência com dígito no formato 0000-0.")
    if account_number and len(account_number) > 30:
        raise ValueError("A conta deve ter até 30 caracteres.")
    if opening_balance < 0 or opening_balance > Decimal("9999999999999.99"):
        raise ValueError("O saldo inicial não pode ser negativo.")
    async with in_transaction():
        ledger_account = await LedgerAccount.create(
            organization=organization,
            created_by=user,
            code=f"FIN-{uuid4().hex[:12].upper()}",
            name=name,
            account_type="asset",
        )
        account = await FinancialAccount.create(
            organization=organization,
            created_by=user,
            ledger_account=ledger_account,
            name=name,
            bank_name=(bank_name.strip() or None) if bank_name else None,
            bank_code=bank_code or None,
            branch_number=branch_number or None,
            account_number=account_number or None,
            account_type=account_type,
            opening_balance=opening_balance.quantize(CENT),
        )
        if opening_balance:
            equity = await _system_account(
                organization, user, "SYS-EQUITY", "Patrimônio de abertura", "equity"
            )
            await _create_journal(
                organization=organization,
                user=user,
                idempotency_key=uuid5(NAMESPACE_URL, f"fincore:opening:{account.id}"),
                entry_date=date.today(),
                description=f"Saldo inicial: {name}"[:240],
                lines=[
                    (ledger_account, opening_balance, Decimal("0.00")),
                    (equity, Decimal("0.00"), opening_balance),
                ],
            )
    return account


async def update_account(
    organization: Organization,
    account_id: UUID,
    name: str,
    account_type: str,
    bank_name: str | None = None,
    bank_code: str | None = None,
    branch_number: str | None = None,
    account_number: str | None = None,
) -> FinancialAccount:
    """Atualiza os dados cadastrais sem alterar o saldo ou o diário contábil."""
    account = await FinancialAccount.get_or_none(id=account_id, organization=organization)
    if account is None:
        raise ValueError("Conta não encontrada.")
    if not name or len(name) > 120 or account_type not in {"bank", "cash", "wallet"}:
        raise ValueError("Confira o nome e o tipo da conta.")
    bank_name = bank_name.strip() if bank_name else None
    bank_code = bank_code.strip() if bank_code else None
    branch_number = branch_number.strip() if branch_number else None
    account_number = account_number.strip() if account_number else None
    if account_type == "bank" and not all((bank_code, branch_number, account_number)):
        raise ValueError("Informe código do banco, agência com dígito e número da conta.")
    if bank_name and len(bank_name) > 120:
        raise ValueError("O nome do banco deve ter até 120 caracteres.")
    if bank_code and (len(bank_code) > 4 or not bank_code.isdigit()):
        raise ValueError("O código do banco deve conter até 4 dígitos.")
    if branch_number and (
        len(branch_number) > 30 or not re.fullmatch(r"[0-9]{1,4}-[0-9]", branch_number)
    ):
        raise ValueError("Informe a agência com dígito no formato 0000-0.")
    if account_number and len(account_number) > 30:
        raise ValueError("A conta deve ter até 30 caracteres.")

    async with in_transaction():
        account.name = name.strip()
        account.account_type = account_type
        account.bank_name = bank_name or None
        account.bank_code = bank_code or None
        account.branch_number = branch_number or None
        account.account_number = account_number or None
        await account.save(
            update_fields=[
                "name",
                "account_type",
                "bank_name",
                "bank_code",
                "branch_number",
                "account_number",
            ]
        )
        ledger_account = await account.ledger_account
        if ledger_account is not None:
            ledger_account.name = account.name
            await ledger_account.save(update_fields=["name"])
    return account


async def set_account_active(
    organization: Organization, account_id: UUID, is_active: bool
) -> FinancialAccount:
    account = await FinancialAccount.get_or_none(id=account_id, organization=organization)
    if account is None:
        raise ValueError("Conta não encontrada.")
    account.is_active = is_active
    await account.save(update_fields=["is_active"])
    return account


async def create_category(
    organization: Organization,
    user: User,
    name: str,
    transaction_type: str,
    category_kind: str = "analytic",
    parent_id: UUID | None = None,
) -> Category:
    if not name or len(name) > 100 or transaction_type not in {"income", "expense"}:
        raise ValueError("Confira o nome e o tipo da categoria.")
    if category_kind not in {"synthetic", "analytic"}:
        raise ValueError("Selecione um grupo sintético ou analítico.")
    parent = None
    ledger_parent = None
    if parent_id:
        parent = await Category.get_or_none(id=parent_id, organization=organization, is_active=True)
        if (
            parent is None
            or parent.category_kind != "synthetic"
            or parent.parent_id is not None
            or parent.transaction_type != transaction_type
        ):
            raise ValueError("A categoria superior deve ser sintética e do mesmo tipo.")
        if category_kind == "synthetic":
            raise ValueError(
                "Subcategorias devem ser analíticas; grupos sintéticos ficam no nível superior."
            )
        if parent.ledger_account_id:
            ledger_parent = await LedgerAccount.get(id=parent.ledger_account_id)
    account_type = "revenue" if transaction_type == "income" else "expense"
    code_prefix = "CAT-REV" if transaction_type == "income" else "CAT-EXP"
    async with in_transaction():
        ledger_account = await LedgerAccount.create(
            organization=organization,
            created_by=user,
            code=f"{code_prefix}-{uuid4().hex[:10].upper()}",
            name=name,
            account_type=account_type,
            parent=ledger_parent,
            is_synthetic=category_kind == "synthetic",
        )
        return await Category.create(
            organization=organization,
            created_by=user,
            ledger_account=ledger_account,
            parent=parent,
            name=name,
            transaction_type=transaction_type,
            category_kind=category_kind,
        )


async def create_movement(
    *,
    organization: Organization,
    user: User,
    operation_key: UUID,
    account_id: UUID,
    category_id: UUID | None,
    transaction_type: str,
    description: str,
    amount: Decimal,
    competence_date: date,
    due_date: date | None,
    settled: bool,
) -> FinancialTransaction:
    if transaction_type not in {"income", "expense"} or not description or len(description) > 240:
        raise ValueError("Confira o tipo e a descrição da movimentação.")
    if amount <= 0 or amount > Decimal("9999999999999.99"):
        raise ValueError("O valor deve ser maior que zero e respeitar o limite permitido.")
    account = await FinancialAccount.get_or_none(
        id=account_id, organization=organization, is_active=True
    )
    if account is None:
        raise ValueError("Selecione uma conta desta organização.")
    category = None
    if category_id:
        category = await Category.get_or_none(
            id=category_id,
            organization=organization,
            transaction_type=transaction_type,
            is_active=True,
        )
        if category is None:
            raise ValueError("Selecione uma categoria válida para este tipo.")
        if category.category_kind != "analytic":
            raise ValueError("Selecione uma categoria analítica para a movimentação.")
    existing = await FinancialTransaction.get_or_none(
        operation_key=operation_key, organization=organization
    )
    if existing:
        if existing.status == "settled":
            await _create_movement_journal(organization, user, existing)
        return existing
    if await FinancialTransaction.exists(operation_key=operation_key):
        raise ValueError("Esta chave de operação já foi utilizada.")
    try:
        async with in_transaction():
            movement = await FinancialTransaction.create(
                operation_key=operation_key,
                organization=organization,
                account=account,
                category=category,
                created_by=user,
                settled_by=user if settled else None,
                transaction_type=transaction_type,
                description=description,
                amount=amount.quantize(CENT),
                competence_date=competence_date,
                due_date=due_date,
                settled_at=datetime.now(UTC) if settled else None,
                status="settled" if settled else "open",
            )
            if settled:
                await _create_movement_journal(organization, user, movement)
        return movement
    except IntegrityError:
        duplicate = await FinancialTransaction.get_or_none(
            operation_key=operation_key, organization=organization
        )
        if duplicate:
            return duplicate
        raise ValueError("Esta chave de operação já foi utilizada.") from None


async def create_transfer(
    *,
    organization: Organization,
    user: User,
    operation_key: UUID,
    source_id: UUID,
    target_id: UUID,
    amount: Decimal,
    description: str,
    competence_date: date,
) -> UUID:
    if source_id == target_id:
        raise ValueError("As contas de origem e destino devem ser diferentes.")
    if amount <= 0 or amount > Decimal("9999999999999.99"):
        raise ValueError("O valor deve ser maior que zero e respeitar o limite permitido.")
    if not description or len(description) > 240:
        raise ValueError("Informe uma descrição de até 240 caracteres.")
    existing = await FinancialTransaction.get_or_none(
        operation_key=operation_key, organization=organization
    )
    if existing:
        return existing.transfer_id or existing.id
    if await FinancialTransaction.exists(operation_key=operation_key):
        raise ValueError("Esta chave de operação já foi utilizada.")
    source = await FinancialAccount.filter(
        id__in=[source_id, target_id], organization=organization, is_active=True
    ).all()
    accounts = {account.id: account for account in source}
    if source_id not in accounts or target_id not in accounts:
        raise ValueError("As duas contas precisam pertencer à organização atual.")
    transfer_id = uuid4()
    try:
        async with in_transaction():
            source_movement = await FinancialTransaction.create(
                operation_key=operation_key,
                organization=organization,
                account=accounts[source_id],
                created_by=user,
                settled_by=user,
                transfer_id=transfer_id,
                transaction_type="transfer_out",
                description=description,
                amount=amount.quantize(CENT),
                competence_date=competence_date,
                settled_at=datetime.now(UTC),
                status="settled",
            )
            await FinancialTransaction.create(
                organization=organization,
                account=accounts[target_id],
                created_by=user,
                settled_by=user,
                transfer_id=transfer_id,
                transaction_type="transfer_in",
                description=description,
                amount=amount.quantize(CENT),
                competence_date=competence_date,
                settled_at=datetime.now(UTC),
                status="settled",
            )
            await _create_movement_journal(organization, user, source_movement)
    except IntegrityError:
        duplicate = await FinancialTransaction.get_or_none(
            operation_key=operation_key, organization=organization
        )
        if duplicate:
            return duplicate.transfer_id or duplicate.id
        raise ValueError("Esta chave de operação já foi utilizada.") from None
    return transfer_id


async def settle_movement(organization: Organization, user: User, transaction_id: UUID) -> bool:
    async with in_transaction():
        movement = (
            await FinancialTransaction.filter(
                id=transaction_id, organization=organization, status="open"
            )
            .select_for_update()
            .first()
        )
        if movement is None:
            return False
        settled_at = datetime.now(UTC)
        await FinancialTransaction.filter(id=movement.id, organization=organization).update(
            status="settled", settled_at=settled_at, settled_by_id=user.id
        )
        movement.status = "settled"
        movement.settled_at = settled_at
        movement.settled_by_id = user.id
        await _create_movement_journal(organization, user, movement)
    return True


async def reverse_movement(
    organization: Organization,
    user: User,
    transaction_id: UUID,
    operation_key: UUID,
    competence_date: date,
) -> FinancialTransaction:
    """Adds a compensating movement and journal while preserving the posted original."""
    existing_key = await FinancialTransaction.get_or_none(
        operation_key=operation_key, organization=organization
    )
    if existing_key:
        return existing_key
    async with in_transaction():
        selected = (
            await FinancialTransaction.filter(
                id=transaction_id, organization=organization, status="settled"
            )
            .select_for_update()
            .first()
        )
        if selected is None:
            raise ValueError("Somente movimentações liquidadas podem ser estornadas.")
        original = selected
        if selected.transaction_type == "transfer_in":
            original = (
                await FinancialTransaction.filter(
                    organization=organization,
                    transfer_id=selected.transfer_id,
                    transaction_type="transfer_out",
                )
                .select_for_update()
                .first()
            )
            if original is None:
                raise ValueError("A transferência original está incompleta.")
        if await FinancialTransaction.filter(reversal_of=original).exists():
            raise ValueError("Esta movimentação já possui estorno.")
        if await FinancialTransaction.exists(operation_key=operation_key):
            raise ValueError("Esta chave de operação já foi utilizada.")

        if original.transaction_type == "transfer_out":
            incoming = await FinancialTransaction.get_or_none(
                organization=organization,
                transfer_id=original.transfer_id,
                transaction_type="transfer_in",
            )
            if incoming is None:
                raise ValueError("A transferência original está incompleta.")
            reverse_transfer_id = uuid4()
            movement = await FinancialTransaction.create(
                operation_key=operation_key,
                organization=organization,
                account_id=incoming.account_id,
                created_by=user,
                settled_by=user,
                transfer_id=reverse_transfer_id,
                reversal_of=original,
                transaction_type="transfer_out",
                description=f"Estorno: {original.description}"[:240],
                amount=original.amount,
                competence_date=competence_date,
                settled_at=datetime.now(UTC),
                status="settled",
            )
            await FinancialTransaction.create(
                organization=organization,
                account_id=original.account_id,
                created_by=user,
                settled_by=user,
                transfer_id=reverse_transfer_id,
                transaction_type="transfer_in",
                description=f"Estorno: {original.description}"[:240],
                amount=original.amount,
                competence_date=competence_date,
                settled_at=datetime.now(UTC),
                status="settled",
            )
        else:
            reverse_type = "expense" if original.transaction_type == "income" else "income"
            movement = await FinancialTransaction.create(
                operation_key=operation_key,
                organization=organization,
                account_id=original.account_id,
                created_by=user,
                settled_by=user,
                reversal_of=original,
                transaction_type=reverse_type,
                description=f"Estorno: {original.description}"[:240],
                amount=original.amount,
                competence_date=competence_date,
                settled_at=datetime.now(UTC),
                status="settled",
            )
        await _create_movement_journal(organization, user, movement)
        return movement


async def get_account_balances(
    organization: Organization,
    *,
    include_inactive: bool = False,
) -> list[tuple[FinancialAccount, Decimal]]:
    query = FinancialAccount.filter(organization=organization)
    if not include_inactive:
        query = query.filter(is_active=True)
    accounts = await query.all()
    # Soma no banco por conta e tipo (antes: todas as movimentações em memória).
    rows = (
        await FinancialTransaction.filter(organization=organization, status="settled")
        .annotate(total=Sum("amount"))
        .group_by("account_id", "transaction_type")
        .values("account_id", "transaction_type", "total")
    )
    changes: dict[UUID, Decimal] = {}
    for row in rows:
        sign = Decimal("-1") if row["transaction_type"] in {"expense", "transfer_out"} else 1
        changes[row["account_id"]] = changes.get(row["account_id"], Decimal("0")) + sign * Decimal(
            row["total"]
        )
    return [
        (account, account.opening_balance + changes.get(account.id, Decimal("0")))
        for account in accounts
    ]


async def backfill_settled_movement_journals() -> int:
    """Idempotently creates ledger entries missing for previously settled movements."""
    movements = await FinancialTransaction.filter(status="settled").order_by("created_at")
    created = 0
    for movement in movements:
        if movement.transaction_type == "transfer_in":
            continue
        if await JournalEntry.exists(source_transaction=movement):
            continue
        async with in_transaction():
            organization = await movement.organization
            user = await movement.created_by
            await _create_movement_journal(organization, user, movement)
        created += 1
    return created
