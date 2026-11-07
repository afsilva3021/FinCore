"""Regras de plano de contas, partidas dobradas e estornos imutáveis."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.functions import Sum
from tortoise.transactions import in_transaction

from fincore.domain.accounting import (
    ACCOUNT_TYPES,
    ensure_balanced,
    ensure_can_post,
    normal_balance,
    normalize_journal_lines,
)
from fincore.domain.money import parse_brl
from fincore.infrastructure.models import (
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    Organization,
    User,
)


def parse_line_amount(value: str) -> Decimal:
    return parse_brl(
        value,
        allow_zero=True,
        empty_as_zero=True,
        invalid_message="Informe valores de débito e crédito válidos.",
        range_message="Débitos e créditos devem ser valores positivos válidos.",
    )


async def _validated_lines(
    organization: Organization, lines: list[tuple[UUID, Decimal, Decimal]]
) -> list[tuple[LedgerAccount, Decimal, Decimal]]:
    clean_lines = normalize_journal_lines(lines)
    account_ids = {account_id for account_id, _, _ in clean_lines}
    accounts = await LedgerAccount.filter(
        id__in=account_ids, organization=organization, is_active=True
    ).all()
    if len(accounts) != len(account_ids):
        raise ValueError("Todas as contas contábeis devem pertencer à organização atual.")
    if any(account.is_synthetic for account in accounts):
        raise ValueError("Lançamentos devem usar contas analíticas, não grupos sintéticos.")
    account_map = {account.id: account for account in accounts}
    return [(account_map[account_id], debit, credit) for account_id, debit, credit in clean_lines]


async def create_ledger_account(
    organization: Organization,
    user: User,
    code: str,
    name: str,
    account_type: str,
    parent_id: UUID | None = None,
    is_synthetic: bool = False,
) -> LedgerAccount:
    if not code or len(code) > 32 or not name or len(name) > 160:
        raise ValueError("Informe código e nome válidos para a conta contábil.")
    if account_type not in ACCOUNT_TYPES:
        raise ValueError("Selecione uma natureza contábil válida.")
    parent = None
    if parent_id:
        parent = await LedgerAccount.get_or_none(
            id=parent_id, organization=organization, is_active=True
        )
        if parent is None or not parent.is_synthetic:
            raise ValueError("A conta superior deve ser um grupo sintético da organização.")
        if parent.account_type != account_type:
            raise ValueError("A natureza da conta deve corresponder à conta superior.")
    return await LedgerAccount.create(
        organization=organization,
        created_by=user,
        code=code.strip(),
        name=name.strip(),
        account_type=account_type,
        parent=parent,
        is_synthetic=is_synthetic,
    )


async def create_journal_entry(
    *,
    organization: Organization,
    user: User,
    idempotency_key: UUID,
    entry_date: date,
    description: str,
    lines: list[tuple[UUID, Decimal, Decimal]],
) -> JournalEntry:
    if not description.strip() or len(description) > 240:
        raise ValueError("Informe uma descrição de até 240 caracteres.")
    validated_lines = await _validated_lines(organization, lines)
    existing = await JournalEntry.get_or_none(
        idempotency_key=idempotency_key, organization=organization
    )
    if existing:
        return existing
    if await JournalEntry.exists(idempotency_key=idempotency_key):
        raise ValueError("Esta chave de operação já foi utilizada.")
    try:
        async with in_transaction():
            entry = await JournalEntry.create(
                idempotency_key=idempotency_key,
                organization=organization,
                created_by=user,
                entry_date=entry_date,
                description=description.strip(),
                status="draft",
            )
            await JournalEntryLine.bulk_create(
                [
                    JournalEntryLine(
                        journal_entry=entry,
                        account=account,
                        debit=debit,
                        credit=credit,
                    )
                    for account, debit, credit in validated_lines
                ]
            )
        return entry
    except IntegrityError:
        duplicate = await JournalEntry.get_or_none(
            idempotency_key=idempotency_key, organization=organization
        )
        if duplicate:
            return duplicate
        raise ValueError("Esta chave de operação já foi utilizada.") from None


async def update_draft_journal_entry(
    *,
    organization: Organization,
    entry_id: UUID,
    entry_date: date,
    description: str,
    lines: list[tuple[UUID, Decimal, Decimal]],
) -> JournalEntry:
    if not description.strip() or len(description) > 240:
        raise ValueError("Informe uma descrição de até 240 caracteres.")
    validated_lines = await _validated_lines(organization, lines)
    async with in_transaction():
        entry = (
            await JournalEntry.filter(id=entry_id, organization=organization, status="draft")
            .select_for_update()
            .first()
        )
        if entry is None:
            raise ValueError("Somente rascunhos podem ser alterados.")
        await JournalEntryLine.filter(journal_entry=entry).delete()
        await JournalEntryLine.bulk_create(
            [
                JournalEntryLine(
                    journal_entry=entry,
                    account=account,
                    debit=debit,
                    credit=credit,
                )
                for account, debit, credit in validated_lines
            ]
        )
        entry.entry_date = entry_date
        entry.description = description.strip()
        await entry.save(update_fields=["entry_date", "description"])
    return entry


async def post_journal_entry(organization: Organization, entry_id: UUID) -> JournalEntry:
    async with in_transaction():
        entry = (
            await JournalEntry.filter(id=entry_id, organization=organization)
            .select_for_update()
            .first()
        )
        if entry is None:
            raise ValueError("O lançamento não existe.")
        if entry.status == "posted":
            return entry
        ensure_can_post(entry.status)
        lines = await JournalEntryLine.filter(journal_entry=entry).all()
        ensure_balanced(
            [(line.debit, line.credit) for line in lines], {line.currency for line in lines}
        )
        entry.status = "posted"
        entry.posted_at = datetime.now(UTC)
        await entry.save(update_fields=["status", "posted_at"])
    return entry


async def reverse_journal_entry(
    organization: Organization, user: User, entry_id: UUID
) -> JournalEntry:
    async with in_transaction():
        original = (
            await JournalEntry.filter(id=entry_id, organization=organization, status="posted")
            .select_for_update()
            .first()
        )
        if original is None:
            raise ValueError("Somente lançamentos efetivados podem ser estornados.")
        if await JournalEntry.exists(reversal_of=original):
            raise ValueError("Este lançamento já possui estorno.")
        original_lines = await JournalEntryLine.filter(journal_entry=original).all()
        reversal = await JournalEntry.create(
            organization=organization,
            created_by=user,
            reversal_of=original,
            entry_date=date.today(),
            description=f"Estorno: {original.description}"[:240],
            status="posted",
            posted_at=datetime.now(UTC),
        )
        await JournalEntryLine.bulk_create(
            [
                JournalEntryLine(
                    journal_entry=reversal,
                    account_id=line.account_id,
                    debit=line.credit,
                    credit=line.debit,
                    currency=line.currency,
                )
                for line in original_lines
            ]
        )
    return reversal


async def get_ledger_balances(
    organization: Organization,
) -> list[tuple[LedgerAccount, Decimal]]:
    accounts = await LedgerAccount.filter(organization=organization, is_active=True).order_by(
        "code"
    )
    account_rows: list[tuple[LedgerAccount, Decimal]] = []
    direct_balances: dict[UUID, Decimal] = {}
    children: dict[UUID, list[LedgerAccount]] = {}
    # Uma única agregação no banco para todas as contas (antes: uma query por conta).
    sums = {
        row["account_id"]: (row["debit"] or Decimal("0.00"), row["credit"] or Decimal("0.00"))
        for row in await JournalEntryLine.filter(
            journal_entry__organization=organization, journal_entry__status="posted"
        )
        .annotate(debit=Sum("debit"), credit=Sum("credit"))
        .group_by("account_id")
        .values("account_id", "debit", "credit")
    }
    for account in accounts:
        debit, credit = sums.get(account.id, (Decimal("0.00"), Decimal("0.00")))
        balance = normal_balance(account.account_type, debit, credit)
        direct_balances[account.id] = balance
        if account.parent_id:
            children.setdefault(account.parent_id, []).append(account)

    totals: dict[UUID, Decimal] = {}

    def account_total(account: LedgerAccount) -> Decimal:
        if account.id not in totals:
            totals[account.id] = direct_balances[account.id] + sum(
                (account_total(child) for child in children.get(account.id, [])),
                Decimal("0.00"),
            )
        return totals[account.id]

    for account in accounts:
        account_rows.append((account, account_total(account)))
    return account_rows
