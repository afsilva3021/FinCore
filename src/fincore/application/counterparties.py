"""Cadastro e gestão de clientes, fornecedores e outros contatos."""

import re
from uuid import UUID

from tortoise.exceptions import IntegrityError

from fincore.infrastructure.models import Counterparty, Organization


def _optional(value: str, label: str, limit: int) -> str | None:
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise ValueError(f"{label} deve ter no máximo {limit} caracteres.")
    return cleaned or None


def normalize_tax_id(value: str, person_type: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    expected = 11 if person_type == "individual" else 14
    if len(digits) != expected:
        label = "CPF" if expected == 11 else "CNPJ"
        raise ValueError(f"{label} deve conter {expected} dígitos.")
    return digits


def validate_counterparty(
    *,
    person_type: str,
    name: str,
    tax_id: str,
    is_customer: bool,
    is_supplier: bool,
) -> tuple[str, str | None, str]:
    if person_type not in {"individual", "company"}:
        raise ValueError("Selecione pessoa física ou jurídica.")
    cleaned_name = name.strip()
    if not cleaned_name or len(cleaned_name) > 150:
        raise ValueError("Informe um nome ou razão social de até 150 caracteres.")
    if not is_customer and not is_supplier:
        raise ValueError("Selecione cliente, fornecedor ou ambos.")
    normalized_tax_id = normalize_tax_id(tax_id, person_type)
    party_type = (
        "other" if is_customer and is_supplier else "customer" if is_customer else "supplier"
    )
    return cleaned_name, normalized_tax_id, party_type


async def list_counterparties(
    organization: Organization,
    *,
    role: str = "all",
    status: str = "all",
    limit: int | None = None,
    offset: int = 0,
) -> list[Counterparty]:
    query = Counterparty.filter(organization=organization)
    if role == "customer":
        query = query.filter(is_customer=True)
    elif role == "supplier":
        query = query.filter(is_supplier=True)
    if status == "active":
        query = query.filter(is_active=True)
    elif status == "inactive":
        query = query.filter(is_active=False)
    query = query.order_by("name", "party_type").offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return await query


async def get_counterparty(
    organization: Organization, counterparty_id: UUID
) -> Counterparty | None:
    return await Counterparty.get_or_none(id=counterparty_id, organization=organization)


async def save_counterparty(
    *,
    organization: Organization,
    counterparty_id: UUID | None = None,
    person_type: str,
    name: str,
    trade_name: str = "",
    tax_id: str = "",
    is_customer: bool = False,
    is_supplier: bool = False,
    email: str = "",
    phone: str = "",
    postal_code: str = "",
    street: str = "",
    address_number: str = "",
    complement: str = "",
    district: str = "",
    city: str = "",
    state: str = "",
    notes: str = "",
) -> Counterparty:
    cleaned_name, normalized_tax_id, party_type = validate_counterparty(
        person_type=person_type,
        name=name,
        tax_id=tax_id,
        is_customer=is_customer,
        is_supplier=is_supplier,
    )
    if normalized_tax_id:
        duplicate_query = Counterparty.filter(organization=organization, tax_id=normalized_tax_id)
        if counterparty_id is not None:
            duplicate_query = duplicate_query.exclude(id=counterparty_id)
        if await duplicate_query.exists():
            raise ValueError("Já existe um contato com este CPF/CNPJ nesta organização.")

    values = {
        "name": cleaned_name,
        "party_type": party_type,
        "person_type": person_type,
        "trade_name": _optional(trade_name, "Nome fantasia", 150),
        "tax_id": normalized_tax_id,
        "is_customer": is_customer,
        "is_supplier": is_supplier,
        "email": _optional(email, "E-mail", 254),
        "phone": _optional(phone, "Telefone", 40),
        "postal_code": _optional(postal_code, "CEP", 9),
        "street": _optional(street, "Logradouro", 160),
        "address_number": _optional(address_number, "Número", 20),
        "complement": _optional(complement, "Complemento", 100),
        "district": _optional(district, "Bairro", 100),
        "city": _optional(city, "Cidade", 100),
        "state": _optional(state, "UF", 2),
        "notes": _optional(notes, "Observações", 1000),
    }
    if values["state"]:
        values["state"] = str(values["state"]).upper()
    if values["postal_code"]:
        values["postal_code"] = re.sub(r"\D", "", str(values["postal_code"]))
        if len(str(values["postal_code"])) != 8:
            raise ValueError("CEP deve conter oito dígitos.")

    try:
        if counterparty_id is None:
            return await Counterparty.create(organization=organization, **values)
        contact = await Counterparty.get_or_none(id=counterparty_id, organization=organization)
        if contact is None:
            raise ValueError("Contato não encontrado nesta organização.")
        for key, value in values.items():
            setattr(contact, key, value)
        await contact.save()
        return contact
    except IntegrityError as exc:
        raise ValueError("Já existe um contato com estes dados nesta organização.") from exc


async def set_counterparty_active(
    organization: Organization, counterparty_id: UUID, is_active: bool
) -> Counterparty:
    contact = await Counterparty.get_or_none(id=counterparty_id, organization=organization)
    if contact is None:
        raise ValueError("Contato não encontrado nesta organização.")
    contact.is_active = is_active
    await contact.save(update_fields=["is_active"])
    return contact
