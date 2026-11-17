"""Cadastros de produto: casos de uso, páginas, API, perfis e regras no banco."""

import asyncio
import json
import os
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from fincore.app import app
from fincore.application.identity import register_user
from fincore.application.members import add_member
from fincore.application.products import (
    create_default_catalog,
    save_product,
    save_product_group,
    save_unit,
    save_warehouse,
    set_default_warehouse,
    set_unit_active,
    set_warehouse_active,
)
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import (
    FinancialAccount,
    Membership,
    Product,
    ProductGroup,
    UnitOfMeasure,
    User,
    Warehouse,
)
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
FORM = "application/x-www-form-urlencoded"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _organization(label: str = "Org Produtos"):
    user = await register_user(
        "Dona Produtos", f"produtos-{uuid4().hex}@example.test", PASSWORD, label
    )
    membership = await Membership.get(user=user)
    return user, membership, await membership.organization


def _headers(user: User, membership: Membership) -> tuple[dict[str, str], str]:
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(membership.organization_id), "sv": user.session_version}
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    return {"cookie": cookie, "content-type": FORM}, token


async def _unit(organization, code: str = "UN") -> UnitOfMeasure:
    return await UnitOfMeasure.get(organization=organization, code=code)


def _sku() -> str:
    return f"SKU-{uuid4().hex[:8].upper()}"


# --- Casos de uso ---------------------------------------------------------------------


async def test_new_organization_gets_default_units_and_one_default_warehouse() -> None:
    user, _, organization = await _organization()
    codes = set(
        await UnitOfMeasure.filter(organization=organization).values_list("code", flat=True)
    )
    assert codes == {"UN", "CX", "KG", "L", "M"}
    warehouses = await Warehouse.filter(organization=organization)
    assert [(item.code, item.is_default) for item in warehouses] == [("PRINCIPAL", True)]

    # Idempotente: repetir não duplica nada.
    await create_default_catalog(organization, user)
    assert await UnitOfMeasure.filter(organization=organization).count() == 5
    assert await Warehouse.filter(organization=organization).count() == 1


async def test_product_is_saved_normalized_and_updated() -> None:
    user, _, organization = await _organization()
    group = await save_product_group(organization=organization, user=user, name="Vestuário")
    sku = _sku()
    product = await save_product(
        organization=organization,
        user=user,
        sku=f" {sku.lower()} ",
        name="  Camiseta   básica ",
        unit_id=(await _unit(organization)).id,
        group_id=group.id,
        barcode="789 1000-100103",
        ncm="6109.10.00",
        sale_price=Decimal("49.9"),
        min_stock=Decimal("10"),
    )
    stored = await Product.get(id=product.id)
    assert (stored.sku, stored.name) == (sku, "Camiseta básica")
    assert (stored.barcode, stored.ncm) == ("7891000100103", "61091000")
    assert stored.sale_price == Decimal("49.90")
    assert stored.min_stock == Decimal("10.0000")
    assert stored.currency == "BRL" and stored.tracks_stock and stored.is_active

    await save_product(
        organization=organization,
        user=user,
        product_id=product.id,
        sku=sku,
        name="Camiseta premium",
        unit_id=(await _unit(organization)).id,
        sale_price=Decimal("59.90"),
        tracks_stock=False,
    )
    stored = await Product.get(id=product.id)
    assert stored.name == "Camiseta premium" and stored.sale_price == Decimal("59.90")
    assert stored.group_id is None and stored.barcode is None and not stored.tracks_stock


async def test_sku_and_barcode_are_unique_inside_the_organization_only() -> None:
    user, _, organization = await _organization()
    other_user, _, other_organization = await _organization("Outra Org")
    unit = await _unit(organization)
    sku = _sku()
    common = {"name": "Produto", "barcode": "7891000100103"}
    await save_product(organization=organization, user=user, sku=sku, unit_id=unit.id, **common)

    with pytest.raises(ValueError, match="SKU"):
        await save_product(
            organization=organization, user=user, sku=sku.lower(), name="Outro", unit_id=unit.id
        )
    with pytest.raises(ValueError, match="código de barras"):
        await save_product(
            organization=organization, user=user, sku=_sku(), unit_id=unit.id, **common
        )
    # Outra organização pode usar o mesmo SKU e o mesmo código de barras.
    await save_product(
        organization=other_organization,
        user=other_user,
        sku=sku,
        unit_id=(await _unit(other_organization)).id,
        **common,
    )
    assert await Product.filter(sku=sku).count() == 2


async def test_simultaneous_creation_with_the_same_sku_keeps_only_one() -> None:
    user, _, organization = await _organization()
    unit = await _unit(organization)
    sku = _sku()

    async def create():
        return await save_product(
            organization=organization, user=user, sku=sku, name="Corrida", unit_id=unit.id
        )

    results = await asyncio.gather(*(create() for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(item, Product) for item in results) == 1
    assert all(isinstance(item, (Product, ValueError)) for item in results)
    assert await Product.filter(organization=organization, sku=sku).count() == 1


async def test_product_rejects_invalid_values_and_foreign_references() -> None:
    user, _, organization = await _organization()
    _, _, other_organization = await _organization("Outra Org")
    unit = await _unit(organization)
    foreign_unit = await _unit(other_organization)
    base = {"organization": organization, "user": user, "name": "Produto", "unit_id": unit.id}

    with pytest.raises(ValueError, match="unidade"):
        await save_product(**{**base, "unit_id": foreign_unit.id}, sku=_sku())
    with pytest.raises(ValueError, match="Preço"):
        await save_product(**base, sku=_sku(), sale_price=Decimal("-0.01"))
    with pytest.raises(ValueError, match="inteiros"):
        await save_product(**base, sku=_sku(), min_stock=Decimal("1.5"))
    with pytest.raises(ValueError, match="dígito verificador"):
        await save_product(**base, sku=_sku(), barcode="7891000100104")
    with pytest.raises(ValueError, match="grupo"):
        await save_product(**base, sku=_sku(), group_id=uuid4())
    assert await Product.filter(organization=organization).count() == 0

    # Unidade com casas decimais aceita fração no estoque mínimo.
    kilo = await _unit(organization, "KG")
    product = await save_product(
        **{**base, "unit_id": kilo.id}, sku=_sku(), min_stock=Decimal("1.5")
    )
    assert product.min_stock == Decimal("1.5000")


async def test_inactive_unit_cannot_be_chosen_but_stays_on_existing_products() -> None:
    user, _, organization = await _organization()
    unit = await save_unit(organization=organization, code="pct", name="Pacote", decimal_places=0)
    assert unit.code == "PCT"
    product = await save_product(
        organization=organization, user=user, sku=_sku(), name="Arroz", unit_id=unit.id
    )
    await set_unit_active(organization, unit.id, False)

    with pytest.raises(ValueError, match="unidade"):
        await save_product(
            organization=organization, user=user, sku=_sku(), name="Feijão", unit_id=unit.id
        )
    updated = await save_product(
        organization=organization,
        user=user,
        product_id=product.id,
        sku=product.sku,
        name="Arroz tipo 1",
        unit_id=unit.id,
    )
    assert updated.name == "Arroz tipo 1"
    with pytest.raises(ValueError, match="sigla"):
        await save_unit(organization=organization, code="PCT", name="Outro", decimal_places=0)


async def test_group_hierarchy_refuses_cycles_duplicates_and_foreign_parents() -> None:
    user, _, organization = await _organization()
    other_user, _, other_organization = await _organization("Outra Org")
    drinks = await save_product_group(organization=organization, user=user, name="Bebidas")
    sodas = await save_product_group(
        organization=organization, user=user, name="Refrigerantes", parent_id=drinks.id
    )
    assert sodas.parent_id == drinks.id

    with pytest.raises(ValueError, match="subgrupo"):
        await save_product_group(
            organization=organization,
            user=user,
            group_id=drinks.id,
            name="Bebidas",
            parent_id=sodas.id,
        )
    with pytest.raises(ValueError, match="subgrupo"):
        await save_product_group(
            organization=organization,
            user=user,
            group_id=drinks.id,
            name="Bebidas",
            parent_id=drinks.id,
        )
    with pytest.raises(ValueError, match="Já existe"):
        await save_product_group(organization=organization, user=user, name="Bebidas")
    foreign = await save_product_group(
        organization=other_organization, user=other_user, name="Alheio"
    )
    with pytest.raises(ValueError, match="grupo superior"):
        await save_product_group(
            organization=organization, user=user, name="Invasor", parent_id=foreign.id
        )
    assert (await ProductGroup.get(id=drinks.id)).parent_id is None


async def test_organization_always_has_exactly_one_default_warehouse() -> None:
    user, _, organization = await _organization()
    main = await Warehouse.get(organization=organization, is_default=True)
    store = await save_warehouse(organization=organization, user=user, code="loja-01", name="Loja")
    assert store.code == "LOJA-01" and not store.is_default

    with pytest.raises(ValueError, match="padrão"):
        await set_warehouse_active(organization, main.id, False)
    await set_default_warehouse(organization, store.id)
    defaults = await Warehouse.filter(organization=organization, is_default=True)
    assert [item.id for item in defaults] == [store.id]

    await set_warehouse_active(organization, main.id, False)
    with pytest.raises(ValueError, match="Reative"):
        await set_default_warehouse(organization, main.id)
    with pytest.raises(ValueError, match="código"):
        await save_warehouse(organization=organization, user=user, code="LOJA-01", name="Cópia")

    # Trocas simultâneas não deixam dois padrões nem nenhum.
    await set_warehouse_active(organization, main.id, True)
    await asyncio.gather(
        *(
            set_default_warehouse(organization, target)
            for target in (main.id, store.id, main.id, store.id)
        )
    )
    assert await Warehouse.filter(organization=organization, is_default=True).count() == 1


async def test_database_enforces_product_rules_even_without_the_application() -> None:
    user, _, organization = await _organization()
    unit = await _unit(organization)
    base = {"organization": organization, "created_by": user, "unit": unit, "name": "Direto"}

    for invalid in (
        {"sku": _sku(), "sale_price": Decimal("-1")},
        {"sku": _sku(), "min_stock": Decimal("-1")},
        {"sku": "minusculo"},
        {"sku": _sku(), "barcode": "123"},
        {"sku": _sku(), "ncm": "12"},
    ):
        with pytest.raises(IntegrityError):
            await Product.create(**base, **invalid)

    await Product.create(**base, sku=_sku(), barcode="7891000100103")
    with pytest.raises(IntegrityError):
        await Product.create(**base, sku=_sku(), barcode="7891000100103")
    with pytest.raises(IntegrityError):
        await Warehouse.create(organization=organization, code="SEGUNDO", name="x", is_default=True)
    with pytest.raises(IntegrityError):
        await UnitOfMeasure.create(organization=organization, code="XX", name="x", decimal_places=5)


# --- Páginas --------------------------------------------------------------------------


async def test_product_pages_create_filter_edit_and_inactivate() -> None:
    user, membership, organization = await _organization()
    headers, token = _headers(user, membership)
    client = RobynTestClient(app)
    unit = await _unit(organization)
    sku = _sku()

    empty = await asyncio.to_thread(client.get, "/products", headers=headers)
    assert empty.status_code == 200
    assert "Nenhum produto encontrado" in empty.text and "Novo produto" in empty.text

    body = (
        f"csrf={token}&sku={sku}&name=Caneca+Azul&unit_id={unit.id}"
        "&sale_price=1.234,50&min_stock=5&tracks_stock=true&barcode=7891000100103"
    )
    created = await asyncio.to_thread(client.post, "/products", headers=headers, body=body)
    assert created.status_code == 303
    product = await Product.get(organization=organization, sku=sku)
    assert product.sale_price == Decimal("1234.50") and product.min_stock == Decimal("5")

    listing = await asyncio.to_thread(
        client.get, "/products", headers=headers, query_params={"q": "caneca"}
    )
    assert "Caneca Azul" in listing.text and "R$ 1.234,50" in listing.text
    missing = await asyncio.to_thread(
        client.get, "/products", headers=headers, query_params={"q": "inexistente"}
    )
    assert "Caneca Azul" not in missing.text
    assert "Nenhum produto corresponde aos filtros" in missing.text

    # Erro de validação devolve 400, mantém o que foi digitado e não grava.
    invalid = await asyncio.to_thread(
        client.post,
        "/products",
        headers=headers,
        body=f"csrf={token}&sku={sku}&name=Duplicado&unit_id={unit.id}",
    )
    assert invalid.status_code == 400
    assert "Já existe um produto com este código" in invalid.text
    assert 'value="Duplicado"' in invalid.text
    assert await Product.filter(organization=organization).count() == 1

    edit = await asyncio.to_thread(client.get, f"/products/{product.id}/edit", headers=headers)
    assert edit.status_code == 200 and 'value="1.234,50"' in edit.text
    updated = await asyncio.to_thread(
        client.post,
        f"/products/{product.id}/update",
        headers=headers,
        body=f"csrf={token}&sku={sku}&name=Caneca+Verde&unit_id={unit.id}&sale_price=20",
    )
    assert updated.status_code == 303
    assert (await Product.get(id=product.id)).name == "Caneca Verde"

    off = await asyncio.to_thread(
        client.post,
        f"/products/{product.id}/active",
        headers=headers,
        body=f"csrf={token}&is_active=false",
    )
    assert off.status_code == 303
    assert not (await Product.get(id=product.id)).is_active
    active_only = await asyncio.to_thread(
        client.get, "/products", headers=headers, query_params={"status": "active"}
    )
    assert "Caneca Verde" not in active_only.text


async def test_product_writes_require_a_valid_csrf_token() -> None:
    user, membership, organization = await _organization()
    headers, _ = _headers(user, membership)
    client = RobynTestClient(app)
    unit = await _unit(organization)

    response = await asyncio.to_thread(
        client.post,
        "/products",
        headers=headers,
        body=f"csrf=token-errado&sku={_sku()}&name=Sem+CSRF&unit_id={unit.id}",
    )
    assert response.status_code == 303
    api = await asyncio.to_thread(
        client.post,
        "/api/v1/products",
        headers={**headers, "content-type": "application/json", "x-csrf-token": "errado"},
        body=json.dumps({"sku": _sku(), "name": "Sem CSRF", "unit_id": str(unit.id)}),
    )
    assert api.status_code == 403
    assert await Product.filter(organization=organization).count() == 0


async def test_settings_page_manages_groups_units_and_warehouses() -> None:
    user, membership, organization = await _organization()
    headers, token = _headers(user, membership)
    client = RobynTestClient(app)

    async def post(path: str, body: str):
        return await asyncio.to_thread(
            client.post, path, headers=headers, body=f"csrf={token}&{body}"
        )

    page = await asyncio.to_thread(client.get, "/product-settings", headers=headers)
    assert page.status_code == 200
    assert "Depósito principal" in page.text and "Quilograma" in page.text

    assert (await post("/product-settings/groups", "name=Bebidas")).status_code == 303
    group = await ProductGroup.get(organization=organization, name="Bebidas")
    assert (
        await post("/product-settings/units", "code=pct&name=Pacote&decimal_places=0")
    ).status_code == 303
    unit = await UnitOfMeasure.get(organization=organization, code="PCT")
    assert (await post("/product-settings/warehouses", "code=loja&name=Loja")).status_code == 303
    store = await Warehouse.get(organization=organization, code="LOJA")

    renamed = await post(f"/product-settings/groups/{group.id}/update", "name=Bebidas+frias")
    assert renamed.status_code == 303
    assert (await ProductGroup.get(id=group.id)).name == "Bebidas frias"
    assert (
        await post(f"/product-settings/units/{unit.id}/active", "is_active=false")
    ).status_code == 303
    assert not (await UnitOfMeasure.get(id=unit.id)).is_active
    assert (await post(f"/product-settings/warehouses/{store.id}/default", "")).status_code == 303
    assert (await Warehouse.get(id=store.id)).is_default

    # Erro aparece na página com 400 e nada é gravado.
    duplicate = await post("/product-settings/units", "code=KG&name=Outro&decimal_places=0")
    assert duplicate.status_code == 400 and "Já existe uma unidade" in duplicate.text
    blocked = await post(f"/product-settings/warehouses/{store.id}/active", "is_active=false")
    assert blocked.status_code == 400 and "padrão" in blocked.text
    assert (await Warehouse.get(id=store.id)).is_active
    bad_id = await post("/product-settings/groups/nao-e-uuid/update", "name=X")
    assert bad_id.status_code == 404


# --- API ------------------------------------------------------------------------------


async def test_products_api_creates_lists_and_details() -> None:
    user, membership, organization = await _organization()
    headers, token = _headers(user, membership)
    api = {**headers, "content-type": "application/json", "x-csrf-token": token}
    client = RobynTestClient(app)
    unit = await _unit(organization, "KG")
    sku = _sku()

    payload = {
        "sku": sku,
        "name": "Café em grãos",
        "unit_id": str(unit.id),
        "sale_price": "42.90",
        "min_stock": "2.5",
        "barcode": "7891000100103",
    }
    created = await asyncio.to_thread(
        client.post, "/api/v1/products", headers=api, body=json.dumps(payload)
    )
    assert created.status_code == 201
    data = json.loads(created.text)
    assert data["sku"] == sku and data["sale_price"] == "42.90"
    assert data["min_stock"] == "2.5000" and data["unit"]["code"] == "KG"
    assert data["group"] is None and data["is_active"] is True

    repeated = await asyncio.to_thread(
        client.post, "/api/v1/products", headers=api, body=json.dumps(payload)
    )
    assert repeated.status_code == 409
    assert json.loads(repeated.text)["error"]["code"] == "conflict"

    for invalid in (
        {**payload, "sku": _sku(), "barcode": "", "sale_price": "42,90"},
        {**payload, "sku": _sku(), "barcode": "", "sale_price": "1.999"},
        {**payload, "sku": _sku(), "barcode": "", "sale_price": 42.9},
        {**payload, "sku": _sku(), "barcode": "", "unit_id": ""},
        {**payload, "sku": _sku(), "barcode": "", "tracks_stock": "sim"},
    ):
        response = await asyncio.to_thread(
            client.post, "/api/v1/products", headers=api, body=json.dumps(invalid)
        )
        assert response.status_code == 400, invalid
        assert json.loads(response.text)["error"]["code"] == "invalid_product"
    assert await Product.filter(organization=organization).count() == 1

    listing = await asyncio.to_thread(
        client.get, "/api/v1/products", headers=api, query_params={"q": "café", "limit": "10"}
    )
    listed = json.loads(listing.text)
    assert listed["total"] == 1 and listed["items"][0]["id"] == data["id"]
    detail = await asyncio.to_thread(client.get, f"/api/v1/products/{data['id']}", headers=api)
    assert detail.status_code == 200 and json.loads(detail.text)["name"] == "Café em grãos"
    unknown = await asyncio.to_thread(client.get, f"/api/v1/products/{uuid4()}", headers=api)
    assert unknown.status_code == 404
    bad_page = await asyncio.to_thread(
        client.get, "/api/v1/products", headers=api, query_params={"limit": "0"}
    )
    assert bad_page.status_code == 400
    anonymous = await asyncio.to_thread(client.get, "/api/v1/products")
    assert anonymous.status_code == 401


# --- Perfis ---------------------------------------------------------------------------


async def test_stock_profile_writes_products_but_not_finance_and_vice_versa() -> None:
    _, _, organization = await _organization()
    unit = await _unit(organization)
    client = RobynTestClient(app)

    async def member(role: str):
        membership = await add_member(
            organization, role, f"{role}-{uuid4().hex}@example.test", PASSWORD, role
        )
        return _headers(await membership.user, membership)

    def product_body(token: str, sku: str) -> str:
        return f"csrf={token}&sku={sku}&name=Perfil&unit_id={unit.id}"

    account_body = "name=Conta+Perfil-{}&account_type=cash&opening_balance=0"

    # Estoque: altera cadastros de produto; só consulta o financeiro.
    headers, token = await member("stock")
    sku = _sku()
    ok = await asyncio.to_thread(
        client.post, "/products", headers=headers, body=product_body(token, sku)
    )
    assert ok.status_code == 303
    assert await Product.filter(organization=organization, sku=sku).exists()
    name = account_body.format(uuid4().hex[:6])
    denied = await asyncio.to_thread(
        client.post, "/accounts", headers=headers, body=f"csrf={token}&{name}"
    )
    assert denied.status_code == 403 and "Estoque" in denied.text
    accounts_page = await asyncio.to_thread(client.get, "/accounts", headers=headers)
    assert accounts_page.status_code == 200 and "is-readonly" in accounts_page.text
    products_page = await asyncio.to_thread(client.get, "/products", headers=headers)
    assert "is-readonly" not in products_page.text

    # Financeiro: o contrário.
    headers, token = await member("finance")
    sku = _sku()
    denied = await asyncio.to_thread(
        client.post, "/products", headers=headers, body=product_body(token, sku)
    )
    assert denied.status_code == 403
    api_denied = await asyncio.to_thread(
        client.post,
        "/api/v1/products",
        headers={**headers, "content-type": "application/json", "x-csrf-token": token},
        body=json.dumps({"sku": sku, "name": "Perfil", "unit_id": str(unit.id)}),
    )
    assert api_denied.status_code == 403 and '"forbidden"' in api_denied.text
    settings_denied = await asyncio.to_thread(
        client.post, "/product-settings/groups", headers=headers, body=f"csrf={token}&name=Negado"
    )
    assert settings_denied.status_code == 403
    assert not await Product.filter(organization=organization, sku=sku).exists()
    assert not await ProductGroup.filter(organization=organization, name="Negado").exists()
    products_page = await asyncio.to_thread(client.get, "/products", headers=headers)
    assert products_page.status_code == 200 and "is-readonly" in products_page.text
    name = account_body.format(uuid4().hex[:6])
    ok = await asyncio.to_thread(
        client.post, "/accounts", headers=headers, body=f"csrf={token}&{name}"
    )
    assert ok.status_code == 303
    assert await FinancialAccount.filter(organization=organization).count() == 1

    # Somente leitura: nada.
    headers, token = await member("viewer")
    denied = await asyncio.to_thread(
        client.post, "/products", headers=headers, body=product_body(token, _sku())
    )
    assert denied.status_code == 403 and "somente leitura" in denied.text
