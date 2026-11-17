"""Estoque: entradas, saídas, transferências e estornos, com custo médio e diário contábil.

Todo movimento é gravado numa única transação PostgreSQL com a posição (`StockBalance`)
bloqueada, de modo que saídas simultâneas não deixam o saldo negativo. Movimentos não
são alterados nem apagados: correção é feita por estorno ou por ajuste.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.domain.inventory import (
    ADJUSTMENT,
    IN,
    OPENING,
    OUT,
    REVERSAL,
    TRANSFER,
    Applied,
    InsufficientStock,
    Position,
    apply_entry,
    apply_entry_at_value,
    apply_exit,
    ensure_manual_reason,
    ensure_reversible,
    undo_entry,
    undo_exit,
)
from fincore.domain.products import fit_quantity_to_unit
from fincore.infrastructure.models import (
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    Organization,
    Product,
    StockBalance,
    StockMovement,
    User,
    Warehouse,
)

# Contas contábeis do estoque, criadas na primeira movimentação da organização.
STOCK_ACCOUNT = ("SYS-STOCK", "Estoque de mercadorias", "asset")
LOSS_ACCOUNT = ("SYS-STOCK-LOSS", "Perdas e ajustes de estoque", "expense")
GAIN_ACCOUNT = ("SYS-STOCK-GAIN", "Ajustes positivos de estoque", "revenue")
EQUITY_ACCOUNT = ("SYS-EQUITY", "Patrimônio de abertura", "equity")
# (sentido, motivo) -> (conta a débito, conta a crédito). Transferência não muda o valor
# total do estoque, então não gera lançamento.
POSTING_RULES = {
    (IN, OPENING): (STOCK_ACCOUNT, EQUITY_ACCOUNT),
    (IN, ADJUSTMENT): (STOCK_ACCOUNT, GAIN_ACCOUNT),
    (OUT, ADJUSTMENT): (LOSS_ACCOUNT, STOCK_ACCOUNT),
}


class OperationKeyConflict(ValueError):
    """A chave de operação já foi usada por outra organização."""

    def __init__(self) -> None:
        super().__init__("Chave de operação já utilizada.")


async def _ledger_account(organization: Organization, user: User, spec: tuple[str, str, str]):
    code, name, account_type = spec
    account = await LedgerAccount.get_or_none(organization=organization, code=code)
    if account is not None:
        return account
    try:
        return await LedgerAccount.create(
            organization=organization,
            created_by=user,
            code=code,
            name=name,
            account_type=account_type,
        )
    except IntegrityError:
        # Outro envio criou a conta ao mesmo tempo.
        return await LedgerAccount.get(organization=organization, code=code)


async def _posting_accounts(
    organization: Organization, user: User, direction: str, reason: str
) -> tuple[LedgerAccount, LedgerAccount] | None:
    rule = POSTING_RULES.get((direction, reason))
    if rule is None:
        return None
    return (
        await _ledger_account(organization, user, rule[0]),
        await _ledger_account(organization, user, rule[1]),
    )


async def _post_journal(
    *,
    organization: Organization,
    user: User,
    entry_date: date,
    description: str,
    debit_account_id: UUID,
    credit_account_id: UUID,
    amount: Decimal,
    reversal_of: JournalEntry | None = None,
) -> JournalEntry | None:
    """Lançamento efetivado de duas linhas; movimento sem valor não gera lançamento."""
    if amount <= 0:
        return None
    entry = await JournalEntry.create(
        idempotency_key=uuid4(),
        organization=organization,
        created_by=user,
        reversal_of=reversal_of,
        entry_date=entry_date,
        description=description[:240],
        status="posted",
        posted_at=datetime.now(UTC),
    )
    await JournalEntryLine.bulk_create(
        [
            JournalEntryLine(
                journal_entry=entry, account_id=debit_account_id, debit=amount, credit=0
            ),
            JournalEntryLine(
                journal_entry=entry, account_id=credit_account_id, debit=0, credit=amount
            ),
        ]
    )
    return entry


async def _product_for_movement(organization: Organization, product_id: UUID) -> Product:
    product = (
        await Product.filter(id=product_id, organization=organization)
        .select_related("unit")
        .first()
    )
    if product is None:
        raise ValueError("Produto não encontrado nesta organização.")
    if not product.is_active:
        raise ValueError("Reative o produto antes de movimentar o estoque.")
    if not product.tracks_stock:
        raise ValueError("Este produto não controla estoque.")
    return product


async def _warehouse_for_movement(organization: Organization, warehouse_id: UUID) -> Warehouse:
    warehouse = await Warehouse.get_or_none(id=warehouse_id, organization=organization)
    if warehouse is None:
        raise ValueError("Depósito não encontrado nesta organização.")
    if not warehouse.is_active:
        raise ValueError("Reative o depósito antes de movimentar o estoque.")
    return warehouse


def _validated_date(occurred_on: date) -> date:
    if occurred_on > date.today():
        raise ValueError("A data do movimento não pode estar no futuro.")
    return occurred_on


def _validated_notes(notes: str) -> str | None:
    cleaned = " ".join(notes.split())
    if len(cleaned) > 240:
        raise ValueError("Observações devem ter no máximo 240 caracteres.")
    return cleaned or None


async def _ensure_balance(organization: Organization, product_id: UUID, warehouse_id: UUID) -> UUID:
    """Garante a linha de posição (fora da transação principal) e devolve o ID dela."""
    filters = {"product_id": product_id, "warehouse_id": warehouse_id}
    existing = await StockBalance.get_or_none(**filters)
    if existing is None:
        try:
            existing = await StockBalance.create(organization=organization, **filters)
        except IntegrityError:
            existing = await StockBalance.get(**filters)
    return existing.id


async def _locked_balances(balance_ids: list[UUID]) -> dict[UUID, StockBalance]:
    """Bloqueia as posições sempre na mesma ordem, para duas operações não se travarem."""
    locked = {}
    for balance_id in sorted(set(balance_ids)):
        locked[balance_id] = await StockBalance.filter(id=balance_id).select_for_update().get()
    return locked


async def _record(
    *,
    balance: StockBalance,
    organization: Organization,
    user: User,
    direction: str,
    reason: str,
    quantity: Decimal,
    applied: Applied,
    occurred_on: date,
    notes: str | None,
    operation_key: UUID | None = None,
    transfer_id: UUID | None = None,
    reversal_of: StockMovement | None = None,
    journal_entry: JournalEntry | None = None,
) -> StockMovement:
    """Atualiza a posição bloqueada e grava o movimento com a fotografia do saldo."""
    balance.quantity = applied.position.quantity
    balance.total_value = applied.position.total_value
    balance.movement_count += 1
    await balance.save(update_fields=["quantity", "total_value", "movement_count", "updated_at"])
    return await StockMovement.create(
        organization=organization,
        created_by=user,
        product_id=balance.product_id,
        warehouse_id=balance.warehouse_id,
        sequence=balance.movement_count,
        direction=direction,
        reason=reason,
        quantity=quantity,
        unit_cost=applied.unit_cost,
        total_cost=applied.total_cost,
        balance_quantity=applied.position.quantity,
        balance_value=applied.position.total_value,
        occurred_on=occurred_on,
        notes=notes,
        operation_key=operation_key,
        transfer_id=transfer_id,
        reversal_of=reversal_of,
        journal_entry=journal_entry,
    )


def _position(balance: StockBalance) -> Position:
    return Position(balance.quantity, balance.total_value)


async def _existing_operation(
    organization: Organization, operation_key: UUID
) -> StockMovement | None:
    existing = await StockMovement.get_or_none(operation_key=operation_key)
    if existing is not None and existing.organization_id != organization.id:
        raise OperationKeyConflict
    return existing


def _insufficient(product: Product, error: InsufficientStock) -> ValueError:
    available = f"{error.available.normalize():f}".replace(".", ",")
    return ValueError(
        f"Estoque insuficiente: há {available} {product.unit.code} de {product.name} "
        "neste depósito."
    )


async def register_stock_movement(
    *,
    organization: Organization,
    user: User,
    operation_key: UUID,
    direction: str,
    reason: str,
    product_id: UUID,
    warehouse_id: UUID,
    quantity: Decimal,
    occurred_on: date,
    unit_cost: Decimal = Decimal("0"),
    notes: str = "",
) -> tuple[StockMovement, bool]:
    """Entrada (saldo inicial ou ajuste) ou saída (ajuste) manual.

    Entradas usam o custo unitário informado; saídas usam o custo médio vigente. Devolve
    o movimento e se ele foi criado agora (False = reenvio com a mesma chave).
    """
    existing = await _existing_operation(organization, operation_key)
    if existing is not None:
        return existing, False
    ensure_manual_reason(direction, reason)
    product = await _product_for_movement(organization, product_id)
    warehouse = await _warehouse_for_movement(organization, warehouse_id)
    quantity = fit_quantity_to_unit(quantity, product.unit.decimal_places)
    occurred_on = _validated_date(occurred_on)
    cleaned_notes = _validated_notes(notes)
    accounts = await _posting_accounts(organization, user, direction, reason)
    balance_id = await _ensure_balance(organization, product.id, warehouse.id)
    label = "Entrada" if direction == IN else "Saída"
    try:
        async with in_transaction():
            balance = (await _locked_balances([balance_id]))[balance_id]
            try:
                if direction == IN:
                    applied = apply_entry(_position(balance), quantity, unit_cost)
                else:
                    applied = apply_exit(_position(balance), quantity)
            except InsufficientStock as error:
                raise _insufficient(product, error) from None
            journal = await _post_journal(
                organization=organization,
                user=user,
                entry_date=occurred_on,
                description=f"{label} de estoque: {product.sku} em {warehouse.code}",
                debit_account_id=accounts[0].id,
                credit_account_id=accounts[1].id,
                amount=applied.total_cost,
            )
            movement = await _record(
                balance=balance,
                organization=organization,
                user=user,
                direction=direction,
                reason=reason,
                quantity=quantity,
                applied=applied,
                occurred_on=occurred_on,
                notes=cleaned_notes,
                operation_key=operation_key,
                journal_entry=journal,
            )
    except IntegrityError:
        # Dois envios simultâneos com a mesma chave: vale o que gravou primeiro.
        existing = await _existing_operation(organization, operation_key)
        if existing is None:
            raise
        return existing, False
    return movement, True


async def transfer_stock(
    *,
    organization: Organization,
    user: User,
    operation_key: UUID,
    product_id: UUID,
    source_warehouse_id: UUID,
    destination_warehouse_id: UUID,
    quantity: Decimal,
    occurred_on: date,
    notes: str = "",
) -> tuple[StockMovement, StockMovement, bool]:
    """Move estoque entre depósitos pelo custo médio da origem.

    O valor que sai da origem é exatamente o que entra no destino, então o valor total
    do estoque não muda e não há lançamento contábil.
    """
    existing = await _existing_operation(organization, operation_key)
    if existing is not None:
        return existing, await _transfer_in_leg(existing), False
    if source_warehouse_id == destination_warehouse_id:
        raise ValueError("Escolha depósitos de origem e destino diferentes.")
    product = await _product_for_movement(organization, product_id)
    source = await _warehouse_for_movement(organization, source_warehouse_id)
    destination = await _warehouse_for_movement(organization, destination_warehouse_id)
    quantity = fit_quantity_to_unit(quantity, product.unit.decimal_places)
    occurred_on = _validated_date(occurred_on)
    cleaned_notes = _validated_notes(notes)
    source_id = await _ensure_balance(organization, product.id, source.id)
    destination_id = await _ensure_balance(organization, product.id, destination.id)
    transfer_id = uuid4()
    common = {
        "organization": organization,
        "user": user,
        "reason": TRANSFER,
        "quantity": quantity,
        "occurred_on": occurred_on,
        "notes": cleaned_notes,
        "transfer_id": transfer_id,
    }
    try:
        async with in_transaction():
            balances = await _locked_balances([source_id, destination_id])
            try:
                out_applied = apply_exit(_position(balances[source_id]), quantity)
            except InsufficientStock as error:
                raise _insufficient(product, error) from None
            in_applied = apply_entry_at_value(
                _position(balances[destination_id]), quantity, out_applied.total_cost
            )
            out_leg = await _record(
                balance=balances[source_id],
                direction=OUT,
                applied=out_applied,
                operation_key=operation_key,
                **common,
            )
            in_leg = await _record(
                balance=balances[destination_id], direction=IN, applied=in_applied, **common
            )
    except IntegrityError:
        existing = await _existing_operation(organization, operation_key)
        if existing is None:
            raise
        return existing, await _transfer_in_leg(existing), False
    return out_leg, in_leg, True


async def _transfer_in_leg(out_leg: StockMovement) -> StockMovement:
    if out_leg.transfer_id is None:
        raise OperationKeyConflict
    return await StockMovement.get(transfer_id=out_leg.transfer_id, direction=IN)


async def reverse_stock_movement(
    *, organization: Organization, user: User, movement_id: UUID
) -> list[StockMovement]:
    """Estorna um movimento (as duas pernas, se for transferência) por movimento oposto.

    O original é preservado. Só o último movimento de cada posição pode ser estornado.
    """
    movement = await StockMovement.get_or_none(id=movement_id, organization=organization)
    if movement is None:
        raise ValueError("Movimento não encontrado nesta organização.")
    if movement.transfer_id is not None:
        legs = await StockMovement.filter(
            organization=organization, transfer_id=movement.transfer_id
        ).order_by("direction")
    else:
        legs = [movement]
    balance_ids = {
        leg.id: await _ensure_balance(organization, leg.product_id, leg.warehouse_id)
        for leg in legs
    }
    new_transfer_id = uuid4() if movement.transfer_id is not None else None
    today = date.today()
    reversals = []
    async with in_transaction():
        balances = await _locked_balances(list(balance_ids.values()))
        for leg in legs:
            balance = balances[balance_ids[leg.id]]
            ensure_reversible(
                reason=leg.reason,
                already_reversed=await StockMovement.filter(reversal_of_id=leg.id).exists(),
                sequence=leg.sequence,
                last_sequence=balance.movement_count,
            )
            undo = undo_entry if leg.direction == IN else undo_exit
            applied = undo(_position(balance), leg.quantity, leg.total_cost)
            journal = None
            if leg.journal_entry_id is not None:
                original_entry = await JournalEntry.get(id=leg.journal_entry_id)
                lines = await JournalEntryLine.filter(journal_entry=original_entry)
                debit_line = next(line for line in lines if line.debit > 0)
                credit_line = next(line for line in lines if line.credit > 0)
                journal = await _post_journal(
                    organization=organization,
                    user=user,
                    entry_date=today,
                    description=f"Estorno: {original_entry.description}",
                    # Invertido: o que foi debitado é creditado, e vice-versa.
                    debit_account_id=credit_line.account_id,
                    credit_account_id=debit_line.account_id,
                    amount=leg.total_cost,
                    reversal_of=original_entry,
                )
            reversals.append(
                await _record(
                    balance=balance,
                    organization=organization,
                    user=user,
                    direction=OUT if leg.direction == IN else IN,
                    reason=REVERSAL,
                    quantity=leg.quantity,
                    applied=applied,
                    occurred_on=today,
                    notes=None,
                    transfer_id=new_transfer_id,
                    reversal_of=leg,
                    journal_entry=journal,
                )
            )
    return reversals
