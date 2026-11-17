"""Rotas: fiscal simulado (perfis tributários e documentos fiscais internos).

Tudo aqui é simulação e a interface diz isso em todas as telas.
"""

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from uuid import UUID

from robyn import Request, Response

from fincore.application.fiscal import (
    cancel_fiscal_document,
    issue_fiscal_document,
    save_tax_profile,
    set_tax_profile_active,
)
from fincore.application.queries import (
    fiscal_document_items,
    fiscal_documents_query,
    get_fiscal_document,
    tax_profiles,
)
from fincore.domain.fiscal import SIMULATION_NOTICE
from fincore.infrastructure.models import FiscalDocument, Organization, User
from fincore.presentation.routes.api import error_response, json_response, pagination
from fincore.presentation.web import (
    CurrentUser,
    _csrf_from_auth,
    _form_data,
    _form_uuid,
    _page,
    _paginate,
    _query,
    _redirect,
    _response,
    _valid_csrf,
    api_login_required,
    login_required,
)

STATUS_LABELS = {"issued": "Emitido (simulado)", "cancelled": "Cancelado"}


def _path_uuid(request: Request, name: str) -> UUID | None:
    try:
        return UUID(request.path_params[name])
    except ValueError, KeyError:
        return None


def _rate(value: str, label: str) -> Decimal:
    """Alíquota em pt-BR ("18", "1,65"); vazio vale zero."""
    text = value.strip().replace("%", "").replace(",", ".")
    try:
        return Decimal(text or "0")
    except InvalidOperation:
        raise ValueError(
            f"Informe a alíquota de {label} como número, por exemplo 18 ou 1,65."
        ) from None


def document_number(document: FiscalDocument) -> str:
    return f"{document.series}/{document.number:06d}"


def document_json(document: FiscalDocument) -> dict[str, object]:
    return {
        "id": str(document.id),
        # Deixa explícito para qualquer consumidor da API que isto não é uma nota fiscal.
        "simulated": True,
        "notice": SIMULATION_NOTICE,
        "number": document_number(document),
        "status": document.status,
        "issued_on": document.issued_on.isoformat(),
        "sales_invoice_id": str(document.sales_invoice_id),
        "customer": {"id": str(document.customer_id), "name": document.customer.name},
        "cfop": document.tax_profile.cfop,
        "total": str(document.total_products),
        "total_icms": str(document.total_icms),
        "total_pis": str(document.total_pis),
        "total_cofins": str(document.total_cofins),
        "cancel_reason": document.cancel_reason,
    }


def register_fiscal_routes(app: object) -> None:
    async def render_documents(
        request: Request,
        user: User,
        organization: Organization,
        *,
        form: dict[str, str] | None = None,
        error: str | None = None,
    ) -> Response:
        query = _query(request)
        status = query.get("status", "") or ""
        status = status if status in STATUS_LABELS else ""
        documents, page_info = await _paginate(
            fiscal_documents_query(organization, status=status), request, keep=("status",)
        )
        body = _page(
            "fiscal/documents.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            documents=[(item, document_number(item)) for item in documents],
            pagination=page_info,
            status=status,
            status_labels=STATUS_LABELS,
            profiles=await tax_profiles(organization),
            notice=SIMULATION_NOTICE,
            form=form or {},
            error=error,
            message=query.get("message", ""),
            active="fiscal",
            section="Fiscal (simulação)",
        )
        return _response(body, 400 if error else 200)

    @app.get("/fiscal")
    @login_required
    async def fiscal_page(request: Request, current: CurrentUser) -> Response:
        return await render_documents(request, current[0], await current[1].organization)

    @app.post("/fiscal/tax-profiles")
    @login_required
    async def create_tax_profile_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/fiscal")
        organization = await current[1].organization
        try:
            await save_tax_profile(
                organization=organization,
                name=form.get("name", ""),
                cfop=form.get("cfop", ""),
                icms_cst=form.get("icms_cst", ""),
                icms_rate=_rate(form.get("icms_rate", ""), "ICMS"),
                pis_rate=_rate(form.get("pis_rate", ""), "PIS"),
                cofins_rate=_rate(form.get("cofins_rate", ""), "COFINS"),
            )
        except ValueError as exc:
            return await render_documents(
                request, current[0], organization, form=form, error=str(exc)
            )
        return _redirect(f"/fiscal?{urlencode({'message': 'Perfil tributário cadastrado.'})}")

    @app.post("/fiscal/tax-profiles/:profile_id/active")
    @login_required
    async def set_tax_profile_active_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/fiscal")
        profile_id = _path_uuid(request, "profile_id")
        is_active = form.get("is_active", "").lower() in {"true", "1", "on"}
        try:
            if profile_id is None:
                raise ValueError("Perfil tributário não encontrado nesta organização.")
            await set_tax_profile_active(await current[1].organization, profile_id, is_active)
        except ValueError:
            return _response("Perfil tributário não encontrado.", 404)
        message = "Perfil reativado." if is_active else "Perfil inativado."
        return _redirect(f"/fiscal?{urlencode({'message': message})}")

    @app.post("/fiscal/documents")
    @login_required
    async def issue_document_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/fiscal")
        organization = await current[1].organization
        try:
            profile = form.get("tax_profile_id", "")
            document, _ = await issue_fiscal_document(
                organization=organization,
                user=current[0],
                sales_invoice_id=_form_uuid(form, "sales_invoice_id", "Faturamento inválido."),
                operation_key=_form_uuid(
                    form, "operation_key", "Recarregue a página e tente novamente."
                ),
                tax_profile_id=(
                    _form_uuid(form, "tax_profile_id", "Selecione um perfil tributário válido.")
                    if profile
                    else None
                ),
            )
        except ValueError as exc:
            return await render_documents(request, current[0], organization, error=str(exc))
        message = urlencode({"message": "Documento fiscal simulado gerado."})
        return _redirect(f"/fiscal/{document.id}?{message}")

    async def render_document(
        request: Request,
        user: User,
        organization: Organization,
        document: FiscalDocument,
        *,
        error: str | None = None,
        reason: str = "",
    ) -> Response:
        body = _page(
            "fiscal/document.html",
            name=user.full_name,
            organization=organization,
            csrf=_csrf_from_auth(request),
            document=document,
            number=document_number(document),
            items=await fiscal_document_items(document),
            status_labels=STATUS_LABELS,
            notice=SIMULATION_NOTICE,
            reason=reason,
            error=error,
            message=_query(request).get("message", ""),
            active="fiscal",
            section="Fiscal (simulação) / Documento",
        )
        return _response(body, 400 if error else 200)

    @app.get("/fiscal/:document_id")
    @login_required
    async def fiscal_document_page(request: Request, current: CurrentUser) -> Response:
        organization = await current[1].organization
        document_id = _path_uuid(request, "document_id")
        document = await get_fiscal_document(organization, document_id) if document_id else None
        if document is None:
            return _response("Documento fiscal não encontrado.", 404)
        return await render_document(request, current[0], organization, document)

    @app.post("/fiscal/:document_id/cancel")
    @login_required
    async def cancel_document_page(request: Request, current: CurrentUser) -> Response:
        form = _form_data(request)
        if not _valid_csrf(request, form.get("csrf", "")):
            return _redirect("/fiscal")
        organization = await current[1].organization
        document_id = _path_uuid(request, "document_id")
        document = await get_fiscal_document(organization, document_id) if document_id else None
        if document is None:
            return _response("Documento fiscal não encontrado.", 404)
        try:
            await cancel_fiscal_document(
                organization=organization, document_id=document.id, reason=form.get("reason", "")
            )
        except ValueError as exc:
            return await render_document(
                request,
                current[0],
                organization,
                document,
                error=str(exc),
                reason=form.get("reason", ""),
            )
        message = urlencode({"message": "Documento fiscal simulado cancelado."})
        return _redirect(f"/fiscal/{document.id}?{message}")

    @app.get("/api/v1/fiscal/documents")
    @api_login_required
    async def api_fiscal_documents(request: Request, current: CurrentUser) -> Response:
        try:
            limit, offset = pagination(request)
        except ValueError:
            return error_response("invalid_pagination", "Paginação inválida.", 400)
        query = fiscal_documents_query(await current[1].organization)
        total = await query.count()
        items = [document_json(item) for item in await query.offset(offset).limit(limit)]
        return json_response(
            {
                "simulated": True,
                "notice": SIMULATION_NOTICE,
                "items": items,
                "limit": limit,
                "offset": offset,
                "total": total,
            }
        )
