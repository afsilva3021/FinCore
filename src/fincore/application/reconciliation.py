"""Importação OFX 1.x e conciliação manual, sem alterar o ledger financeiro."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.infrastructure.models import (
    FinancialAccount,
    FinancialTransaction,
    ImportedStatementTransaction,
    Organization,
    ReconciliationEvent,
    ReconciliationMatch,
    User,
)


@dataclass(frozen=True)
class OFXTransaction:
    external_id: str
    posted_at: date
    description: str
    amount: Decimal


def parse_ofx(content: str) -> list[OFXTransaction]:
    """Lê transações OFX 1.x SGML e rejeita registros incompletos ou inválidos."""
    if not content.strip() or "<OFX>" not in content.upper():
        raise ValueError("Arquivo OFX inválido: bloco OFX não encontrado.")
    blocks = re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>)", content, re.I | re.S)
    if not blocks:
        raise ValueError("O extrato OFX não contém transações.")

    def field(block: str, tag: str) -> str:
        match = re.search(rf"<{tag}>([^\r\n<]+)", block, re.I)
        return match.group(1).strip() if match else ""

    results = []
    seen: set[str] = set()
    for block in blocks:
        external_id = field(block, "FITID")
        raw_date = field(block, "DTPOSTED")
        raw_amount = field(block, "TRNAMT")
        description = field(block, "NAME") or field(block, "MEMO") or "Movimento importado"
        if not external_id or len(external_id) > 128 or len(description) > 240:
            raise ValueError("Transação OFX sem identificador ou com descrição inválida.")
        if external_id in seen:
            raise ValueError("O arquivo contém FITID repetido.")
        seen.add(external_id)
        try:
            posted_at = datetime.strptime(raw_date[:8], "%Y%m%d").date()
            amount = Decimal(raw_amount).quantize(Decimal("0.01"))
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Data ou valor inválido em uma transação OFX.") from exc
        if not amount.is_finite() or amount == 0 or abs(amount) > Decimal("9999999999999.99"):
            raise ValueError("O valor de uma transação OFX está fora do limite permitido.")
        results.append(OFXTransaction(external_id, posted_at, description, amount))
    return results


async def import_ofx(
    organization: Organization,
    account_id: UUID,
    content: str,
) -> tuple[int, int]:
    parsed = parse_ofx(content)
    account = await FinancialAccount.get_or_none(id=account_id, organization=organization)
    if account is None:
        raise ValueError("Conta financeira não encontrada.")
    created = 0
    for item in parsed:
        try:
            await ImportedStatementTransaction.create(
                organization=organization,
                account=account,
                external_id=item.external_id,
                posted_at=item.posted_at,
                description=item.description,
                amount=item.amount,
            )
            created += 1
        except IntegrityError:
            # Uma repetição do mesmo extrato é idempotente; FITID identifica o item.
            continue
    return created, len(parsed) - created


async def match_statement_transaction(
    organization: Organization,
    user: User,
    statement_id: UUID,
    financial_transaction_id: UUID,
) -> ReconciliationMatch:
    statement = await ImportedStatementTransaction.get_or_none(
        id=statement_id, organization=organization
    )
    movement = await FinancialTransaction.get_or_none(
        id=financial_transaction_id, organization=organization
    )
    if statement is None or movement is None:
        raise ValueError("Movimentação não encontrada nesta organização.")
    if movement.account_id != statement.account_id:
        raise ValueError("A movimentação pertence a outra conta financeira.")
    if movement.status != "settled":
        raise ValueError("Somente movimentações liquidadas podem ser conciliadas.")
    positive_types = {"income", "transfer_in"}
    expected_amount = (
        movement.amount if movement.transaction_type in positive_types else -movement.amount
    )
    if expected_amount != statement.amount:
        raise ValueError("O valor diverge do extrato; revise antes de conciliar.")
    if movement.competence_date != statement.posted_at:
        raise ValueError("A data de competência diverge da data do extrato.")
    if (
        await ReconciliationMatch.filter(organization=organization)
        .filter(statement_transaction=statement)
        .exists()
    ):
        raise ValueError("Este item de extrato já está conciliado.")
    async with in_transaction():
        match = await ReconciliationMatch.create(
            organization=organization,
            statement_transaction=statement,
            financial_transaction=movement,
            matched_by=user,
        )
        await ReconciliationEvent.create(
            organization=organization,
            statement_transaction=statement,
            financial_transaction=movement,
            actor=user,
            action="matched",
        )
    return match


async def unmatch_statement_transaction(
    organization: Organization, user: User, statement_id: UUID
) -> None:
    statement = await ImportedStatementTransaction.get_or_none(
        id=statement_id, organization=organization
    )
    if statement is None:
        raise ValueError("Item de extrato não encontrado.")
    match = await ReconciliationMatch.filter(
        organization=organization, statement_transaction=statement
    ).first()
    if match is None:
        raise ValueError("Este item de extrato não está conciliado.")
    async with in_transaction():
        await ReconciliationEvent.create(
            organization=organization,
            statement_transaction=statement,
            financial_transaction_id=match.financial_transaction_id,
            actor=user,
            action="unmatched",
        )
        await match.delete()
