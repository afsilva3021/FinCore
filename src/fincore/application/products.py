"""Cadastros de produto: produtos, grupos, unidades de medida e depósitos."""

from decimal import Decimal
from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.domain.money import MAX_AMOUNT, quantize_money
from fincore.domain.products import (
    DEFAULT_UNITS,
    DEFAULT_WAREHOUSE,
    fit_quantity_to_unit,
    normalize_barcode,
    normalize_ncm,
    normalize_sku,
    normalize_unit_code,
    normalize_warehouse_code,
    required_name,
    validate_group_parent,
    validate_unit_decimals,
)
from fincore.infrastructure.models import (
    Organization,
    Product,
    ProductGroup,
    StockBalance,
    StockMovement,
    UnitOfMeasure,
    User,
    Warehouse,
)

MAX_GROUP_DEPTH = 10


async def create_default_catalog(organization: Organization, user: User | None) -> None:
    """Unidades usuais e o depósito padrão de uma organização nova. Idempotente."""
    existing = set(
        await UnitOfMeasure.filter(organization=organization).values_list("code", flat=True)
    )
    await UnitOfMeasure.bulk_create(
        [
            UnitOfMeasure(organization=organization, code=code, name=name, decimal_places=places)
            for code, name, places in DEFAULT_UNITS
            if code not in existing
        ]
    )
    if not await Warehouse.filter(organization=organization).exists():
        code, name = DEFAULT_WAREHOUSE
        await Warehouse.create(
            organization=organization, created_by=user, code=code, name=name, is_default=True
        )


# --- Grupos ---------------------------------------------------------------------------


async def _group_ancestors(group: ProductGroup) -> list[UUID]:
    """IDs do grupo e de todos os grupos acima dele."""
    chain = [group.id]
    parent_id = group.parent_id
    while parent_id is not None and len(chain) <= MAX_GROUP_DEPTH:
        chain.append(parent_id)
        parent_id = (await ProductGroup.filter(id=parent_id).values_list("parent_id", flat=True))[0]
    return chain


async def save_product_group(
    *,
    organization: Organization,
    user: User,
    name: str,
    parent_id: UUID | None = None,
    group_id: UUID | None = None,
) -> ProductGroup:
    cleaned = required_name(name, "um nome de grupo", 120)
    parent = None
    if parent_id is not None:
        parent = await ProductGroup.get_or_none(
            id=parent_id, organization=organization, is_active=True
        )
        if parent is None:
            raise ValueError("Selecione um grupo superior ativo desta organização.")
        ancestors = await _group_ancestors(parent)
        validate_group_parent(group_id, ancestors)
        if len(ancestors) >= MAX_GROUP_DEPTH:
            raise ValueError(f"Grupos aceitam no máximo {MAX_GROUP_DEPTH} níveis.")
    try:
        if group_id is None:
            return await ProductGroup.create(
                organization=organization, created_by=user, name=cleaned, parent=parent
            )
        group = await ProductGroup.get_or_none(id=group_id, organization=organization)
        if group is None:
            raise ValueError("Grupo não encontrado nesta organização.")
        group.name = cleaned
        group.parent = parent
        await group.save()
        return group
    except IntegrityError as exc:
        raise ValueError("Já existe um grupo com este nome.") from exc


async def set_product_group_active(
    organization: Organization, group_id: UUID, is_active: bool
) -> ProductGroup:
    group = await ProductGroup.get_or_none(id=group_id, organization=organization)
    if group is None:
        raise ValueError("Grupo não encontrado nesta organização.")
    group.is_active = is_active
    await group.save(update_fields=["is_active"])
    return group


# --- Unidades de medida ---------------------------------------------------------------


async def save_unit(
    *,
    organization: Organization,
    code: str,
    name: str,
    decimal_places: int,
    unit_id: UUID | None = None,
) -> UnitOfMeasure:
    values = {
        "code": normalize_unit_code(code),
        "name": required_name(name, "um nome de unidade", 60),
        "decimal_places": validate_unit_decimals(decimal_places),
    }
    try:
        if unit_id is None:
            return await UnitOfMeasure.create(organization=organization, **values)
        unit = await UnitOfMeasure.get_or_none(id=unit_id, organization=organization)
        if unit is None:
            raise ValueError("Unidade não encontrada nesta organização.")
        for key, value in values.items():
            setattr(unit, key, value)
        await unit.save()
        return unit
    except IntegrityError as exc:
        raise ValueError("Já existe uma unidade com esta sigla.") from exc


async def set_unit_active(organization: Organization, unit_id: UUID, is_active: bool):
    unit = await UnitOfMeasure.get_or_none(id=unit_id, organization=organization)
    if unit is None:
        raise ValueError("Unidade não encontrada nesta organização.")
    unit.is_active = is_active
    await unit.save(update_fields=["is_active"])
    return unit


# --- Depósitos ------------------------------------------------------------------------


async def save_warehouse(
    *,
    organization: Organization,
    user: User,
    code: str,
    name: str,
    warehouse_id: UUID | None = None,
) -> Warehouse:
    values = {
        "code": normalize_warehouse_code(code),
        "name": required_name(name, "um nome de depósito", 120),
    }
    try:
        async with in_transaction():
            if warehouse_id is None:
                # O primeiro depósito da organização nasce como padrão.
                await Organization.filter(id=organization.id).select_for_update().first()
                has_default = await Warehouse.filter(
                    organization=organization, is_default=True
                ).exists()
                return await Warehouse.create(
                    organization=organization,
                    created_by=user,
                    is_default=not has_default,
                    **values,
                )
            warehouse = await Warehouse.get_or_none(id=warehouse_id, organization=organization)
            if warehouse is None:
                raise ValueError("Depósito não encontrado nesta organização.")
            for key, value in values.items():
                setattr(warehouse, key, value)
            await warehouse.save()
            return warehouse
    except IntegrityError as exc:
        raise ValueError("Já existe um depósito com este código.") from exc


async def set_warehouse_active(
    organization: Organization, warehouse_id: UUID, is_active: bool
) -> Warehouse:
    warehouse = await Warehouse.get_or_none(id=warehouse_id, organization=organization)
    if warehouse is None:
        raise ValueError("Depósito não encontrado nesta organização.")
    if warehouse.is_default and not is_active:
        raise ValueError("Defina outro depósito como padrão antes de inativar este.")
    if not is_active and await StockBalance.filter(warehouse=warehouse, quantity__gt=0).exists():
        raise ValueError("Este depósito ainda tem estoque; transfira ou ajuste o saldo antes.")
    warehouse.is_active = is_active
    await warehouse.save(update_fields=["is_active"])
    return warehouse


async def set_default_warehouse(organization: Organization, warehouse_id: UUID) -> Warehouse:
    """Troca o depósito padrão; a organização sempre mantém exatamente um."""
    async with in_transaction():
        # Bloqueia a organização para que duas trocas simultâneas não se cruzem.
        await Organization.filter(id=organization.id).select_for_update().first()
        warehouse = await Warehouse.get_or_none(id=warehouse_id, organization=organization)
        if warehouse is None:
            raise ValueError("Depósito não encontrado nesta organização.")
        if not warehouse.is_active:
            raise ValueError("Reative o depósito antes de defini-lo como padrão.")
        await (
            Warehouse.filter(organization=organization, is_default=True)
            .exclude(id=warehouse.id)
            .update(is_default=False)
        )
        warehouse.is_default = True
        await warehouse.save(update_fields=["is_default"])
        return warehouse


# --- Produtos -------------------------------------------------------------------------


async def save_product(
    *,
    organization: Organization,
    user: User,
    sku: str,
    name: str,
    unit_id: UUID,
    sale_price: Decimal = Decimal("0"),
    min_stock: Decimal = Decimal("0"),
    group_id: UUID | None = None,
    description: str = "",
    barcode: str = "",
    ncm: str = "",
    tracks_stock: bool = True,
    product_id: UUID | None = None,
) -> Product:
    """Cria ou altera um produto. SKU e código de barras são únicos por organização."""
    normalized_sku = normalize_sku(sku)
    cleaned_name = required_name(name, "uma descrição do produto", 160)
    normalized_barcode = normalize_barcode(barcode)
    normalized_ncm = normalize_ncm(ncm)
    if len(description.strip()) > 2000:
        raise ValueError("Detalhes do produto devem ter no máximo 2000 caracteres.")
    if not sale_price.is_finite() or sale_price < 0 or sale_price > MAX_AMOUNT:
        raise ValueError("Preço de venda deve ser zero ou maior e respeitar o limite permitido.")

    product = None
    if product_id is not None:
        product = await Product.get_or_none(id=product_id, organization=organization)
        if product is None:
            raise ValueError("Produto não encontrado nesta organização.")

    unit = await UnitOfMeasure.get_or_none(id=unit_id, organization=organization)
    # Unidade inativa só continua válida para o produto que já a usava.
    if unit is None or (not unit.is_active and (product is None or product.unit_id != unit.id)):
        raise ValueError("Selecione uma unidade de medida ativa desta organização.")
    group = None
    if group_id is not None:
        group = await ProductGroup.get_or_none(id=group_id, organization=organization)
        if group is None or (
            not group.is_active and (product is None or product.group_id != group.id)
        ):
            raise ValueError("Selecione um grupo ativo desta organização.")

    if product is not None:
        # Com movimentos gravados, a unidade define o sentido das quantidades do histórico.
        if product.unit_id != unit.id and await StockMovement.filter(product=product).exists():
            raise ValueError(
                "Este produto já tem movimentos de estoque; a unidade de medida não pode mudar."
            )
        if (
            product.tracks_stock
            and not tracks_stock
            and await StockBalance.filter(product=product, quantity__gt=0).exists()
        ):
            raise ValueError("Zere o estoque do produto antes de desligar o controle de estoque.")

    duplicates = Product.filter(organization=organization)
    if product is not None:
        duplicates = duplicates.exclude(id=product.id)
    if await duplicates.filter(sku=normalized_sku).exists():
        raise ValueError("Já existe um produto com este código (SKU).")
    if normalized_barcode and await duplicates.filter(barcode=normalized_barcode).exists():
        raise ValueError("Já existe um produto com este código de barras.")

    values = {
        "sku": normalized_sku,
        "name": cleaned_name,
        "description": description.strip() or None,
        "group": group,
        "unit": unit,
        "barcode": normalized_barcode,
        "sale_price": quantize_money(sale_price),
        "min_stock": fit_quantity_to_unit(min_stock, unit.decimal_places, label="o estoque mínimo"),
        "tracks_stock": tracks_stock,
        "ncm": normalized_ncm,
    }
    try:
        if product is None:
            return await Product.create(organization=organization, created_by=user, **values)
        for key, value in values.items():
            setattr(product, key, value)
        await product.save()
        return product
    except IntegrityError as exc:
        # Corrida entre dois envios com o mesmo SKU ou código de barras.
        raise ValueError("Já existe um produto com este código (SKU) ou código de barras.") from exc


async def set_product_active(
    organization: Organization, product_id: UUID, is_active: bool
) -> Product:
    product = await Product.get_or_none(id=product_id, organization=organization)
    if product is None:
        raise ValueError("Produto não encontrado nesta organização.")
    product.is_active = is_active
    await product.save(update_fields=["is_active", "updated_at"])
    return product
