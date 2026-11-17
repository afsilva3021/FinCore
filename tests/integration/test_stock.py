"""Estoque: custo médio, concorrência, idempotência, estorno, contabilidade e banco."""

import asyncio
import json
import os
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError
from tortoise.functions import Sum

from fincore.app import app
from fincore.application.accounting import get_ledger_balances
from fincore.application.identity import register_user
from fincore.application.inventory import (
    OperationKeyConflict,
    register_stock_movement,
    reverse_stock_movement,
    transfer_stock,
)
from fincore.application.members import add_member
from fincore.application.products import save_product, save_warehouse, set_warehouse_active
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    Membership,
    StockBalance,
    StockMovement,
    UnitOfMeasure,
    Warehouse,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
TODAY = date.today()


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


class Shop:
    """Organização com um produto e dois depósitos, e atalhos para movimentar."""

    @classmethod
    async def create(cls, unit: str = "UN") -> Shop:
        self = cls()
        self.user = await register_user(
            "Dona Estoque", f"estoque-{uuid4().hex}@example.test", PASSWORD, "Org Estoque"
        )
        self.organization = await (await Membership.get(user=self.user)).organization
        self.unit = await UnitOfMeasure.get(organization=self.organization, code=unit)
        self.product = await self.new_product()
        self.main = await Warehouse.get(organization=self.organization, is_default=True)
        self.store = await save_warehouse(
            organization=self.organization, user=self.user, code="LOJA", name="Loja"
        )
        return self

    async def new_product(self, **extra):
        return await save_product(
            organization=self.organization,
            user=self.user,
            sku=f"EST-{uuid4().hex[:8]}",
            name="Produto de estoque",
            unit_id=self.unit.id,
            **extra,
        )

    async def move(self, direction, quantity, cost="0", *, reason="adjustment", **extra):
        values = {
            "organization": self.organization,
            "user": self.user,
            "operation_key": uuid4(),
            "direction": direction,
            "reason": reason,
            "product_id": self.product.id,
            "warehouse_id": self.main.id,
            "quantity": D(quantity),
            "unit_cost": D(cost),
            "occurred_on": TODAY,
            **extra,
        }
        movement, _ = await register_stock_movement(**values)
        return movement

    async def transfer(self, quantity, **extra):
        values = {
            "organization": self.organization,
            "user": self.user,
            "operation_key": uuid4(),
            "product_id": self.product.id,
            "source_warehouse_id": self.main.id,
            "destination_warehouse_id": self.store.id,
            "quantity": D(quantity),
            "occurred_on": TODAY,
            **extra,
        }
        return await transfer_stock(**values)

    async def balance(self, warehouse=None, product=None):
        row = await StockBalance.get_or_none(
            product=product or self.product, warehouse=warehouse or self.main
        )
        return (row.quantity, row.total_value) if row else (D("0"), D("0"))

    async def ledger(self) -> dict[str, D]:
        rows = await get_ledger_balances(self.organization)
        return {account.code: balance for account, balance in rows if balance}


async def _assert_balances_match_movements(organization) -> None:
    """A posição guardada tem de ser igual à soma dos movimentos, em quantidade e valor."""
    for balance in await StockBalance.filter(organization=organization):
        totals = {}
        for direction in ("in", "out"):
            row = (
                await StockMovement.filter(
                    product_id=balance.product_id,
                    warehouse_id=balance.warehouse_id,
                    direction=direction,
                )
                .annotate(quantity_sum=Sum("quantity"), cost_sum=Sum("total_cost"))
                .values("quantity_sum", "cost_sum")
            )
            totals[direction] = (row[0]["quantity_sum"] or D(0), row[0]["cost_sum"] or D(0))
        assert balance.quantity == totals["in"][0] - totals["out"][0]
        assert balance.total_value == totals["in"][1] - totals["out"][1]
        last = (
            await StockMovement.filter(
                product_id=balance.product_id, warehouse_id=balance.warehouse_id
            )
            .order_by("-sequence")
            .first()
        )
        assert last.sequence == balance.movement_count
        assert (last.balance_quantity, last.balance_value) == (
            balance.quantity,
            balance.total_value,
        )


async def test_average_cost_follows_entries_and_exits() -> None:
    shop = await Shop.create()
    first = await shop.move("in", "10", "5.00", reason="opening")
    assert (first.sequence, first.total_cost, first.balance_value) == (1, D("50.00"), D("50.00"))
    await shop.move("in", "10", "7.00")
    assert await shop.balance() == (D("20"), D("120.00"))

    out = await shop.move("out", "5")
    assert (out.unit_cost, out.total_cost) == (D("6.000000"), D("30.00"))
    assert (out.balance_quantity, out.balance_value) == (D("15"), D("90.00"))
    assert await shop.balance() == (D("15"), D("90.00"))
    await _assert_balances_match_movements(shop.organization)


async def test_exit_beyond_the_balance_is_refused_and_nothing_is_written() -> None:
    shop = await Shop.create()
    await shop.move("in", "3", "10.00")
    journals = await JournalEntry.filter(organization=shop.organization).count()

    with pytest.raises(ValueError, match="Estoque insuficiente: há 3 UN"):
        await shop.move("out", "4")
    with pytest.raises(ValueError, match="Estoque insuficiente"):
        await shop.move("out", "1", warehouse_id=shop.store.id)
    assert await shop.balance() == (D("3"), D("30.00"))
    assert await StockMovement.filter(organization=shop.organization).count() == 1
    assert await JournalEntry.filter(organization=shop.organization).count() == journals


async def test_simultaneous_exits_never_oversell() -> None:
    shop = await Shop.create()
    await shop.move("in", "5", "2.00")

    results = await asyncio.gather(
        *(shop.move("out", "1") for _ in range(12)), return_exceptions=True
    )
    succeeded = [item for item in results if isinstance(item, StockMovement)]
    refused = [item for item in results if isinstance(item, ValueError)]
    assert (len(succeeded), len(refused)) == (5, 7)
    assert await shop.balance() == (D("0"), D("0.00"))
    assert sorted(item.sequence for item in succeeded) == [2, 3, 4, 5, 6]
    await _assert_balances_match_movements(shop.organization)


async def test_resending_the_same_operation_key_does_not_move_stock_twice() -> None:
    shop = await Shop.create()
    key = uuid4()
    values = {
        "organization": shop.organization,
        "user": shop.user,
        "operation_key": key,
        "direction": "in",
        "reason": "adjustment",
        "product_id": shop.product.id,
        "warehouse_id": shop.main.id,
        "quantity": D("4"),
        "unit_cost": D("2.50"),
        "occurred_on": TODAY,
    }
    first, created = await register_stock_movement(**values)
    again, created_again = await register_stock_movement(**values)
    assert (created, created_again) == (True, False) and again.id == first.id

    burst_key = uuid4()
    burst = await asyncio.gather(
        *(register_stock_movement(**{**values, "operation_key": burst_key}) for _ in range(6))
    )
    assert len({movement.id for movement, _ in burst}) == 1
    assert sum(created for _, created in burst) == 1
    assert await shop.balance() == (D("8"), D("20.00"))

    other = await Shop.create()
    with pytest.raises(OperationKeyConflict):
        await other.move("in", "1", "1.00", operation_key=key)
    with pytest.raises(OperationKeyConflict):
        await other.transfer("1", operation_key=key)
    assert await other.balance() == (D("0"), D("0"))


async def test_transfer_moves_quantity_and_exact_value_without_journal() -> None:
    shop = await Shop.create()
    await shop.move("in", "3", "3.333333")  # R$ 10,00 no total
    journals = await JournalEntry.filter(organization=shop.organization).count()

    key = uuid4()
    out_leg, in_leg, created = await shop.transfer("2", operation_key=key)
    assert created and out_leg.transfer_id == in_leg.transfer_id
    assert (out_leg.direction, in_leg.direction) == ("out", "in")
    assert out_leg.total_cost == in_leg.total_cost == D("6.67")
    assert out_leg.journal_entry_id is None and in_leg.journal_entry_id is None
    assert await shop.balance() == (D("1"), D("3.33"))
    assert await shop.balance(shop.store) == (D("2"), D("6.67"))
    assert await JournalEntry.filter(organization=shop.organization).count() == journals

    repeated_out, repeated_in, created = await shop.transfer("2", operation_key=key)
    assert not created and (repeated_out.id, repeated_in.id) == (out_leg.id, in_leg.id)
    assert await shop.balance(shop.store) == (D("2"), D("6.67"))

    with pytest.raises(ValueError, match="diferentes"):
        await shop.transfer("1", destination_warehouse_id=shop.main.id)
    with pytest.raises(ValueError, match="Estoque insuficiente"):
        await shop.transfer("5")
    await _assert_balances_match_movements(shop.organization)


async def test_opposite_simultaneous_transfers_do_not_deadlock() -> None:
    shop = await Shop.create()
    await shop.move("in", "20", "1.00")
    await shop.move("in", "20", "1.00", warehouse_id=shop.store.id)

    forward = [shop.transfer("1") for _ in range(6)]
    backward = [
        shop.transfer("1", source_warehouse_id=shop.store.id, destination_warehouse_id=shop.main.id)
        for _ in range(6)
    ]
    await asyncio.wait_for(asyncio.gather(*forward, *backward), timeout=30)
    assert await shop.balance() == (D("20"), D("20.00"))
    assert await shop.balance(shop.store) == (D("20"), D("20.00"))
    await _assert_balances_match_movements(shop.organization)


async def test_adjustments_post_balanced_journal_entries() -> None:
    shop = await Shop.create()
    opening = await shop.move("in", "10", "5.00", reason="opening")
    gain = await shop.move("in", "2", "5.00")
    loss = await shop.move("out", "3")
    free = await shop.move("in", "1", "0")

    assert await shop.ledger() == {
        "SYS-STOCK": D("45.00"),
        "SYS-EQUITY": D("50.00"),
        "SYS-STOCK-GAIN": D("10.00"),
        "SYS-STOCK-LOSS": D("15.00"),
    }
    # O saldo da conta de estoque é igual ao valor das posições.
    assert (await shop.balance())[1] == D("45.00")
    assert free.journal_entry_id is None  # entrada sem valor não gera lançamento
    for movement in (opening, gain, loss):
        entry = await JournalEntry.get(id=movement.journal_entry_id)
        lines = await JournalEntryLine.filter(journal_entry=entry)
        assert entry.status == "posted" and entry.posted_at is not None
        assert len(lines) == 2
        assert sum(line.debit for line in lines) == sum(line.credit for line in lines)
        assert sum(line.debit for line in lines) == movement.total_cost
    assert await LedgerAccount.filter(organization=shop.organization, code="SYS-STOCK").count() == 1


async def test_reversal_restores_stock_and_ledger_and_keeps_history() -> None:
    shop = await Shop.create()
    await shop.move("in", "10", "5.00", reason="opening")
    loss = await shop.move("out", "4")
    before = await shop.ledger()

    (reversal,) = await reverse_stock_movement(
        organization=shop.organization, user=shop.user, movement_id=loss.id
    )
    assert (reversal.direction, reversal.reason, reversal.sequence) == ("in", "reversal", 3)
    assert reversal.reversal_of_id == loss.id and reversal.total_cost == loss.total_cost
    assert await shop.balance() == (D("10"), D("50.00"))
    assert await shop.ledger() == {"SYS-STOCK": D("50.00"), "SYS-EQUITY": D("50.00")}
    assert before["SYS-STOCK-LOSS"] == D("20.00")
    entry = await JournalEntry.get(id=reversal.journal_entry_id)
    assert entry.reversal_of_id == loss.journal_entry_id
    # O original continua lá, igual.
    stored = await StockMovement.get(id=loss.id)
    assert (stored.direction, stored.quantity, stored.total_cost) == ("out", D("4"), D("20.00"))

    with pytest.raises(ValueError, match="já foi estornado"):
        await reverse_stock_movement(
            organization=shop.organization, user=shop.user, movement_id=loss.id
        )
    with pytest.raises(ValueError, match="não pode ser estornado"):
        await reverse_stock_movement(
            organization=shop.organization, user=shop.user, movement_id=reversal.id
        )
    assert await StockMovement.filter(organization=shop.organization).count() == 3
    await _assert_balances_match_movements(shop.organization)


async def test_only_the_last_movement_of_a_position_can_be_reversed() -> None:
    shop = await Shop.create()
    first = await shop.move("in", "10", "5.00")
    await shop.move("in", "10", "9.00")

    with pytest.raises(ValueError, match="último movimento"):
        await reverse_stock_movement(
            organization=shop.organization, user=shop.user, movement_id=first.id
        )
    assert await shop.balance() == (D("20"), D("140.00"))

    other = await Shop.create()
    with pytest.raises(ValueError, match="não encontrado"):
        await reverse_stock_movement(
            organization=other.organization, user=other.user, movement_id=first.id
        )


async def test_transfer_reversal_undoes_both_legs_or_neither() -> None:
    shop = await Shop.create()
    await shop.move("in", "10", "5.00")
    out_leg, in_leg, _ = await shop.transfer("4")

    # Qualquer uma das pernas estorna a transferência inteira.
    reversals = await reverse_stock_movement(
        organization=shop.organization, user=shop.user, movement_id=in_leg.id
    )
    assert {item.reversal_of_id for item in reversals} == {out_leg.id, in_leg.id}
    assert len({item.transfer_id for item in reversals}) == 1
    assert await shop.balance() == (D("10"), D("50.00"))
    assert await shop.balance(shop.store) == (D("0"), D("0.00"))

    # Se o destino já movimentou depois, nada é desfeito em nenhum dos lados.
    out_leg, in_leg, _ = await shop.transfer("4")
    await shop.move("out", "1", warehouse_id=shop.store.id)
    with pytest.raises(ValueError, match="último movimento"):
        await reverse_stock_movement(
            organization=shop.organization, user=shop.user, movement_id=out_leg.id
        )
    assert await shop.balance() == (D("6"), D("30.00"))
    assert await shop.balance(shop.store) == (D("3"), D("15.00"))
    await _assert_balances_match_movements(shop.organization)


async def test_movement_validations() -> None:
    shop = await Shop.create()
    other = await Shop.create()

    with pytest.raises(ValueError, match="inteiros"):
        await shop.move("in", "1.5", "1.00")
    with pytest.raises(ValueError, match="maior que zero"):
        await shop.move("in", "0", "1.00")
    with pytest.raises(ValueError, match="futuro"):
        await shop.move("in", "1", "1.00", occurred_on=TODAY + timedelta(days=1))
    with pytest.raises(ValueError, match="tipo de movimentação"):
        await shop.move("out", "1", reason="opening")
    with pytest.raises(ValueError, match="tipo de movimentação"):
        await shop.move("in", "1", "1.00", reason="transfer")
    with pytest.raises(ValueError, match="custo unitário"):
        await shop.move("in", "1", "-1")
    with pytest.raises(ValueError, match="Produto não encontrado"):
        await shop.move("in", "1", "1.00", product_id=other.product.id)
    with pytest.raises(ValueError, match="Depósito não encontrado"):
        await shop.move("in", "1", "1.00", warehouse_id=other.main.id)
    with pytest.raises(ValueError, match="240"):
        await shop.move("in", "1", "1.00", notes="x" * 241)

    untracked = await shop.new_product(tracks_stock=False)
    with pytest.raises(ValueError, match="não controla estoque"):
        await shop.move("in", "1", "1.00", product_id=untracked.id)
    await set_warehouse_active(shop.organization, shop.store.id, False)
    with pytest.raises(ValueError, match="Reative o depósito"):
        await shop.move("in", "1", "1.00", warehouse_id=shop.store.id)
    assert await StockMovement.filter(organization=shop.organization).count() == 0

    # Unidade fracionada aceita frações até as casas dela.
    kilos = await Shop.create("KG")
    await kilos.move("in", "1.250", "8.00")
    assert await kilos.balance() == (D("1.25"), D("10.00"))
    with pytest.raises(ValueError, match="3 casas"):
        await kilos.move("in", "1.2505", "8.00")


async def test_products_and_warehouses_with_stock_are_protected() -> None:
    shop = await Shop.create()
    kilo = await UnitOfMeasure.get(organization=shop.organization, code="KG")
    await shop.move("in", "5", "2.00")
    base = {
        "organization": shop.organization,
        "user": shop.user,
        "product_id": shop.product.id,
        "sku": shop.product.sku,
        "name": "Renomeado",
    }

    with pytest.raises(ValueError, match="unidade de medida não pode mudar"):
        await save_product(**base, unit_id=kilo.id)
    with pytest.raises(ValueError, match="Zere o estoque"):
        await save_product(**base, unit_id=shop.unit.id, tracks_stock=False)
    assert (await save_product(**base, unit_id=shop.unit.id)).name == "Renomeado"

    await shop.transfer("5")
    with pytest.raises(ValueError, match="ainda tem estoque"):
        await set_warehouse_active(shop.organization, shop.store.id, False)


async def test_database_keeps_movements_immutable_and_values_valid() -> None:
    shop = await Shop.create()
    movement = await shop.move("in", "5", "2.00")
    connection = Tortoise.get_connection("default")

    with pytest.raises(IntegrityError):
        await connection.execute_query(
            "UPDATE stockmovement SET quantity = 999 WHERE id = $1", [movement.id]
        )
    with pytest.raises(IntegrityError):
        await connection.execute_query("DELETE FROM stockmovement WHERE id = $1", [movement.id])
    stored = await StockMovement.get(id=movement.id)
    assert stored.quantity == D("5")

    balance = await StockBalance.get(product=shop.product, warehouse=shop.main)
    for column, value in (("quantity", "-1"), ("total_value", "-0.01")):
        with pytest.raises(IntegrityError):
            await connection.execute_query(
                f"UPDATE stockbalance SET {column} = {value} WHERE id = $1", [balance.id]
            )
    with pytest.raises(IntegrityError):
        await StockBalance.create(
            organization=shop.organization, product=shop.product, warehouse=shop.main
        )


# --- Páginas, API e perfis ------------------------------------------------------------

FORM = "application/x-www-form-urlencoded"


async def _headers(user) -> tuple[dict[str, str], str]:
    membership = await Membership.get(user=user)
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(membership.organization_id), "sv": user.session_version}
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    return {"cookie": cookie, "content-type": FORM}, token


async def test_stock_pages_register_show_and_reverse_movements() -> None:
    shop = await Shop.create()
    headers, token = await _headers(shop.user)
    client = RobynTestClient(app)

    async def post(path: str, body: str):
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    empty = await asyncio.to_thread(client.get, "/stock", headers=headers)
    assert empty.status_code == 200 and "Nenhuma posição de estoque" in empty.text
    assert "Nova movimentação" in empty.text and shop.product.sku in empty.text

    key = uuid4()
    base = f"operation_key={key}&product_id={shop.product.id}&warehouse_id={shop.main.id}"
    entry = f"{base}&kind=opening&quantity=1.500&unit_cost=2,50&occurred_on={TODAY}"
    assert (await post("/stock/movements", entry)).status_code == 303
    assert (await post("/stock/movements", entry)).status_code == 303  # reenvio do formulário
    assert await shop.balance() == (D("1500"), D("3750.00"))

    page = await asyncio.to_thread(client.get, "/stock", headers=headers)
    assert "1.500 UN" in page.text and "R$ 3.750,00" in page.text and "R$ 2,50" in page.text

    # Recusa mostra o motivo, mantém o formulário e a mesma chave, e não grava.
    exit_key = uuid4()
    too_much = (
        f"operation_key={exit_key}&product_id={shop.product.id}&warehouse_id={shop.main.id}"
        f"&kind=exit&quantity=2000&occurred_on={TODAY}&notes=Quebra"
    )
    refused = await post("/stock/movements", too_much)
    assert refused.status_code == 400
    assert "Estoque insuficiente: há 1500 UN" in refused.text
    assert f'value="{exit_key}"' in refused.text and 'value="Quebra"' in refused.text
    assert "data-drawer-initially-open" in refused.text
    invalid_date = await post("/stock/movements", f"{base}&kind=entry&quantity=1&occurred_on=x")
    assert invalid_date.status_code == 400 and "Informe a data" in invalid_date.text

    transfer = (
        f"operation_key={uuid4()}&product_id={shop.product.id}&warehouse_id={shop.main.id}"
        f"&destination_warehouse_id={shop.store.id}&kind=transfer&quantity=500"
        f"&occurred_on={TODAY}"
    )
    assert (await post("/stock/movements", transfer)).status_code == 303
    assert await shop.balance(shop.store) == (D("500"), D("1250.00"))

    kardex = await asyncio.to_thread(client.get, "/stock/movements", headers=headers)
    assert kardex.status_code == 200
    for text in ("Saldo inicial", "Transferência enviada", "Transferência recebida"):
        assert text in kardex.text
    # Só os dois lados da transferência (últimos de cada posição) oferecem estorno.
    assert kardex.text.count("/reverse") == 2

    out_leg = await StockMovement.get(
        organization=shop.organization, reason="transfer", direction="out"
    )
    assert (await post(f"/stock/movements/{out_leg.id}/reverse", "")).status_code == 303
    assert await shop.balance() == (D("1500"), D("3750.00"))
    again = await post(f"/stock/movements/{out_leg.id}/reverse", "")
    assert again.status_code == 400 and "já foi estornado" in again.text
    assert (await post(f"/stock/movements/{uuid4()}/reverse", "")).status_code == 404

    filtered = await asyncio.to_thread(
        client.get,
        "/stock/movements",
        headers=headers,
        query_params={"warehouse": str(shop.store.id)},
    )
    assert "Saldo inicial" not in filtered.text and "Estorno de entrada" in filtered.text

    products_page = await asyncio.to_thread(client.get, "/products", headers=headers)
    assert "Em estoque" in products_page.text and ">1.500<" in products_page.text

    no_csrf = await asyncio.to_thread(
        client.post, "/stock/movements", headers=headers, body=f"csrf=errado&{entry}"
    )
    assert no_csrf.status_code == 303
    assert await StockMovement.filter(organization=shop.organization).count() == 5


async def test_low_stock_products_are_listed_on_the_stock_page() -> None:
    shop = await Shop.create()
    low = await shop.new_product(min_stock=D("10"))
    await shop.move("in", "4", "1.00", product_id=low.id)
    await shop.move("in", "3", "1.00", product_id=low.id, warehouse_id=shop.store.id)
    headers, _ = await _headers(shop.user)
    client = RobynTestClient(app)

    page = await asyncio.to_thread(client.get, "/stock", headers=headers)
    assert "Produtos abaixo do estoque mínimo" in page.text and low.sku in page.text
    assert "7 UN" in page.text and "3 UN" in page.text  # tem 7 (dois depósitos), faltam 3

    await shop.move("in", "3", "1.00", product_id=low.id)
    page = await asyncio.to_thread(client.get, "/stock", headers=headers)
    assert "Produtos abaixo do estoque mínimo" not in page.text


async def test_stock_api_moves_lists_and_reverses() -> None:
    shop = await Shop.create("KG")
    headers, token = await _headers(shop.user)
    api = {**headers, "content-type": "application/json", "x-csrf-token": token}
    client = RobynTestClient(app)

    async def post(path: str, payload: dict | None = None):
        return await asyncio.to_thread(
            client.post, path, headers=api, body=json.dumps(payload or {})
        )

    payload = {
        "type": "entry",
        "operation_key": str(uuid4()),
        "product_id": str(shop.product.id),
        "warehouse_id": str(shop.main.id),
        "quantity": "2.5",
        "unit_cost": "8.40",
        "occurred_on": TODAY.isoformat(),
    }
    created = await post("/api/v1/stock/movements", payload)
    assert created.status_code == 201
    (item,) = json.loads(created.text)["items"]
    assert item["direction"] == "in" and item["reason"] == "adjustment"
    assert (item["quantity"], item["total_cost"]) == ("2.5000", "21.00")
    assert item["balance_quantity"] == "2.5000" and item["journal_entry_id"]
    repeated = await post("/api/v1/stock/movements", payload)
    assert repeated.status_code == 200
    assert json.loads(repeated.text)["items"][0]["id"] == item["id"]

    transfer = {
        **payload,
        "type": "transfer",
        "operation_key": str(uuid4()),
        "destination_warehouse_id": str(shop.store.id),
        "quantity": "1",
    }
    moved = await post("/api/v1/stock/movements", transfer)
    assert moved.status_code == 201
    out_leg, in_leg = json.loads(moved.text)["items"]
    assert out_leg["transfer_id"] == in_leg["transfer_id"] and out_leg["total_cost"] == "8.40"

    for invalid in (
        {**payload, "operation_key": str(uuid4()), "type": "exit", "quantity": "99"},
        {**payload, "operation_key": str(uuid4()), "quantity": "2,5"},
        {**payload, "operation_key": str(uuid4()), "quantity": 2.5},
        {**payload, "operation_key": str(uuid4()), "occurred_on": "17/11/2026"},
        {**payload, "operation_key": "nao-e-uuid"},
        {**payload, "operation_key": str(uuid4()), "type": "sale"},
        {**transfer, "operation_key": str(uuid4()), "destination_warehouse_id": ""},
    ):
        response = await post("/api/v1/stock/movements", invalid)
        assert response.status_code == 400, invalid
        assert json.loads(response.text)["error"]["code"] == "invalid_stock_movement"

    other = await Shop.create()
    other_headers, other_token = await _headers(other.user)
    stolen_key = await asyncio.to_thread(
        client.post,
        "/api/v1/stock/movements",
        headers={**other_headers, "content-type": "application/json", "x-csrf-token": other_token},
        body=json.dumps(
            {
                **payload,
                "product_id": str(other.product.id),
                "warehouse_id": str(other.main.id),
                "quantity": "1",
            }
        ),
    )
    assert stolen_key.status_code == 409
    assert json.loads(stolen_key.text)["error"]["code"] == "conflict"

    balances = await asyncio.to_thread(client.get, "/api/v1/stock/balances", headers=api)
    rows = {row["warehouse_code"]: row for row in json.loads(balances.text)["items"]}
    assert rows["PRINCIPAL"]["quantity"] == "1.5000" and rows["LOJA"]["total_value"] == "8.40"
    assert rows["PRINCIPAL"]["average_cost"] == "8.400000" and rows["LOJA"]["unit"] == "KG"
    listing = await asyncio.to_thread(
        client.get,
        "/api/v1/stock/movements",
        headers=api,
        query_params={"warehouse_id": str(shop.store.id)},
    )
    assert json.loads(listing.text)["total"] == 1

    blocked = await post(f"/api/v1/stock/movements/{item['id']}/reverse")
    assert blocked.status_code == 409  # a transferência veio depois
    assert json.loads(blocked.text)["error"]["code"] == "invalid_state"
    undone = await post(f"/api/v1/stock/movements/{in_leg['id']}/reverse")
    assert undone.status_code == 201 and len(json.loads(undone.text)["items"]) == 2
    assert (await post(f"/api/v1/stock/movements/{uuid4()}/reverse")).status_code == 404
    assert await shop.balance() == (D("2.5"), D("21.00"))

    no_csrf = await asyncio.to_thread(
        client.post,
        "/api/v1/stock/movements",
        headers={**api, "x-csrf-token": "errado"},
        body=json.dumps({**payload, "operation_key": str(uuid4())}),
    )
    assert no_csrf.status_code == 403
    assert (await asyncio.to_thread(client.get, "/api/v1/stock/balances")).status_code == 401
    await _assert_balances_match_movements(shop.organization)


async def test_only_owner_and_stock_profiles_move_stock() -> None:
    shop = await Shop.create()
    client = RobynTestClient(app)

    def body(token: str) -> str:
        return (
            f"csrf={token}&operation_key={uuid4()}&product_id={shop.product.id}"
            f"&warehouse_id={shop.main.id}&kind=entry&quantity=1&unit_cost=1&occurred_on={TODAY}"
        )

    for role, expected in (("stock", 303), ("finance", 403), ("viewer", 403)):
        membership = await add_member(
            shop.organization, role, f"{role}-{uuid4().hex}@example.test", PASSWORD, role
        )
        headers, token = await _headers(await membership.user)
        response = await asyncio.to_thread(
            client.post, "/stock/movements", headers=headers, body=body(token)
        )
        assert response.status_code == expected, role
        api = await asyncio.to_thread(
            client.post,
            "/api/v1/stock/movements",
            headers={**headers, "content-type": "application/json", "x-csrf-token": token},
            body="{}",
        )
        assert api.status_code == (400 if expected == 303 else 403), role
        page = await asyncio.to_thread(client.get, "/stock", headers=headers)
        assert page.status_code == 200
        assert ("is-readonly" in page.text) == (expected == 403), role
    assert await shop.balance() == (D("1"), D("1.00"))
