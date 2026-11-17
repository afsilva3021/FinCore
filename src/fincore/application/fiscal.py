"""Fiscal simulado: perfis tributários e documento fiscal interno gerado do faturamento.

**Simulação.** Não gera XML, não assina e não transmite nada à SEFAZ. O documento não tem
valor fiscal e não lança no financeiro, no estoque nem na contabilidade.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.application.numbering import next_document_number
from fincore.domain.crm import clean_text
from fincore.domain.fiscal import (
    DEFAULT_PROFILE,
    ensure_can_cancel,
    item_taxes,
    validate_cfop,
    validate_icms_cst,
    validate_rate,
)
from fincore.infrastructure.models import (
    FiscalDocument,
    FiscalDocumentItem,
    Organization,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
    TaxProfile,
    User,
)

SERIES = "1"


async def save_tax_profile(
    *,
    organization: Organization,
    name: str,
    cfop: str,
    icms_cst: str,
    icms_rate: Decimal = Decimal("0"),
    pis_rate: Decimal = Decimal("0"),
    cofins_rate: Decimal = Decimal("0"),
) -> TaxProfile:
    values = {
        "name": clean_text(name, "O nome do perfil tributário", 80),
        "cfop": validate_cfop(cfop),
        "icms_cst": validate_icms_cst(icms_cst),
        "icms_rate": validate_rate(icms_rate, "ICMS"),
        "pis_rate": validate_rate(pis_rate, "PIS"),
        "cofins_rate": validate_rate(cofins_rate, "COFINS"),
    }
    try:
        return await TaxProfile.create(organization=organization, **values)
    except IntegrityError:
        raise ValueError("Já existe um perfil tributário com este nome.") from None


async def set_tax_profile_active(
    organization: Organization, profile_id: UUID, is_active: bool
) -> TaxProfile:
    profile = await TaxProfile.get_or_none(id=profile_id, organization=organization)
    if profile is None:
        raise ValueError("Perfil tributário não encontrado nesta organização.")
    profile.is_active = is_active
    await profile.save(update_fields=["is_active"])
    return profile


async def default_tax_profile(organization: Organization) -> TaxProfile:
    """Primeiro perfil ativo; se a organização não tem nenhum, cria o perfil de exemplo."""
    profile = (
        await TaxProfile.filter(organization=organization, is_active=True)
        .order_by("created_at")
        .first()
    )
    if profile is not None:
        return profile
    try:
        return await TaxProfile.create(organization=organization, **DEFAULT_PROFILE)
    except IntegrityError:
        existing = await TaxProfile.get(organization=organization, name=DEFAULT_PROFILE["name"])
        if not existing.is_active:
            existing.is_active = True
            await existing.save(update_fields=["is_active"])
        return existing


async def issue_fiscal_document(
    *,
    organization: Organization,
    user: User,
    sales_invoice_id: UUID,
    operation_key: UUID,
    tax_profile_id: UUID | None = None,
) -> tuple[FiscalDocument, bool]:
    """Gera o documento fiscal simulado de um faturamento efetivado.

    Devolve o documento e se foi criado agora. Um faturamento tem no máximo um documento
    emitido; a numeração é sequencial por série, sem buracos nem repetição.
    """
    existing = await FiscalDocument.get_or_none(operation_key=operation_key)
    if existing is not None:
        if (
            existing.organization_id != organization.id
            or existing.sales_invoice_id != sales_invoice_id
        ):
            raise ValueError("Chave de operação já utilizada.")
        return existing, False
    if tax_profile_id is None:
        profile = await default_tax_profile(organization)
    else:
        profile = await TaxProfile.get_or_none(
            id=tax_profile_id, organization=organization, is_active=True
        )
        if profile is None:
            raise ValueError("Selecione um perfil tributário ativo desta organização.")

    try:
        async with in_transaction():
            invoice = (
                await SalesInvoice.filter(id=sales_invoice_id, organization=organization)
                .select_for_update()
                .first()
            )
            if invoice is None:
                raise ValueError("Faturamento não encontrado nesta organização.")
            if invoice.status != "posted":
                raise ValueError("Só um faturamento efetivado gera documento fiscal.")
            if await FiscalDocument.filter(sales_invoice=invoice, status="issued").exists():
                raise ValueError("Este faturamento já tem um documento fiscal emitido.")
            order = await SalesOrder.get(id=invoice.order_id)
            invoice_items = await SalesInvoiceItem.filter(invoice=invoice)
            order_items = {
                item.id: item
                for item in await SalesOrderItem.filter(order=order).select_related("product__unit")
            }
            lines = []
            for invoice_item in invoice_items:
                order_item = order_items[invoice_item.order_item_id]
                taxes = item_taxes(
                    invoice_item.total, profile.icms_rate, profile.pis_rate, profile.cofins_rate
                )
                lines.append((order_item, invoice_item, taxes))
            lines.sort(key=lambda line: line[0].position)
            document = await FiscalDocument.create(
                organization=organization,
                created_by=user,
                number=await next_document_number(organization, f"fiscal_document_{SERIES}"),
                series=SERIES,
                sales_invoice=invoice,
                tax_profile=profile,
                customer_id=order.customer_id,
                issued_on=date.today(),
                total_products=sum((line[1].total for line in lines), Decimal("0.00")),
                total_icms=sum((line[2].icms for line in lines), Decimal("0.00")),
                total_pis=sum((line[2].pis for line in lines), Decimal("0.00")),
                total_cofins=sum((line[2].cofins for line in lines), Decimal("0.00")),
                operation_key=operation_key,
            )
            await FiscalDocumentItem.bulk_create(
                [
                    FiscalDocumentItem(
                        document=document,
                        position=order_item.position,
                        product=order_item.product,
                        description=order_item.product.name,
                        ncm=order_item.product.ncm,
                        cfop=profile.cfop,
                        unit=order_item.product.unit.code,
                        quantity=invoice_item.quantity,
                        unit_price=order_item.unit_price,
                        total=invoice_item.total,
                        icms_amount=taxes.icms,
                        pis_amount=taxes.pis,
                        cofins_amount=taxes.cofins,
                    )
                    for order_item, invoice_item, taxes in lines
                ]
            )
    except IntegrityError:
        existing = await FiscalDocument.get_or_none(
            operation_key=operation_key, organization=organization
        )
        if existing is None:
            raise
        return existing, False
    return document, True


async def cancel_fiscal_document(
    *, organization: Organization, document_id: UUID, reason: str
) -> FiscalDocument:
    """Cancela o documento simulado. O número continua usado e não é reaproveitado."""
    cleaned = clean_text(reason, "O motivo do cancelamento", 240, required=False)
    async with in_transaction():
        document = (
            await FiscalDocument.filter(id=document_id, organization=organization)
            .select_for_update()
            .first()
        )
        if document is None:
            raise ValueError("Documento fiscal não encontrado nesta organização.")
        ensure_can_cancel(document.status, cleaned)
        document.status = "cancelled"
        document.cancel_reason = cleaned
        document.cancelled_at = datetime.now(UTC)
        await document.save(update_fields=["status", "cancel_reason", "cancelled_at"])
        return document
