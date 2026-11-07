"""Perfis de acesso e membros da organização."""

from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from fincore.application.identity import password_hasher, validate_password
from fincore.infrastructure.models import Membership, Organization, User

ROLE_LABELS = {
    "owner": "Proprietário",
    "finance": "Financeiro",
    "viewer": "Somente leitura",
}
ROLE_DESCRIPTIONS = {
    "owner": "Tudo, inclusive gerenciar membros e perfis.",
    "finance": "Cadastra e altera dados financeiros e contábeis; não gerencia membros.",
    "viewer": "Consulta telas e relatórios; não altera nada.",
}
WRITE_ROLES = frozenset({"owner", "finance"})


def can_write(role: str) -> bool:
    return role in WRITE_ROLES


def can_manage_members(role: str) -> bool:
    return role == "owner"


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def _validate_role(role: str) -> str:
    if role not in ROLE_LABELS:
        raise ValueError("Selecione um perfil de acesso válido.")
    return role


async def list_members(organization: Organization) -> list[Membership]:
    return (
        await Membership.filter(organization=organization)
        .select_related("user")
        .order_by("-is_active", "user__full_name")
    )


async def add_member(
    organization: Organization, full_name: str, email: str, password: str, role: str
) -> Membership:
    """Cria um usuário já vinculado à organização com o perfil escolhido.

    Para e-mails que já têm conta, apenas cria o vínculo; a senha informada é ignorada
    e a pessoa continua entrando com a própria senha.
    """
    full_name = full_name.strip()
    email = email.strip().lower()
    _validate_role(role)
    if not full_name or len(full_name) > 160 or "@" not in email or len(email) > 254:
        raise ValueError("Informe nome e e-mail válidos.")
    async with in_transaction():
        user = await User.get_or_none(email=email)
        if user is None:
            validate_password(password, email=email, full_name=full_name)
            user = await User.create(
                email=email, full_name=full_name, password_hash=password_hasher.hash(password)
            )
        try:
            return await Membership.create(user=user, organization=organization, role=role)
        except IntegrityError:
            raise ValueError("Esta pessoa já é membro da organização.") from None


async def _member_for_update(organization: Organization, membership_id: UUID) -> Membership:
    member = (
        await Membership.filter(id=membership_id, organization=organization)
        .select_for_update()
        .first()
    )
    if member is None:
        raise ValueError("Membro não encontrado nesta organização.")
    return member


async def _ensure_another_owner(organization: Organization, member: Membership) -> None:
    if member.role != "owner" or not member.is_active:
        return
    others = (
        await Membership.filter(organization=organization, role="owner", is_active=True)
        .exclude(id=member.id)
        .select_for_update()
        .count()
    )
    if others == 0:
        raise ValueError("A organização precisa manter pelo menos um proprietário ativo.")


async def change_member_role(
    organization: Organization, membership_id: UUID, role: str
) -> Membership:
    _validate_role(role)
    async with in_transaction():
        member = await _member_for_update(organization, membership_id)
        if role != "owner":
            await _ensure_another_owner(organization, member)
        member.role = role
        await member.save(update_fields=["role"])
        return member


async def set_member_active(
    organization: Organization, membership_id: UUID, active: bool
) -> Membership:
    """Ativa/desativa o acesso; o vínculo e o histórico de auditoria são preservados."""
    async with in_transaction():
        member = await _member_for_update(organization, membership_id)
        if not active:
            await _ensure_another_owner(organization, member)
        member.is_active = active
        await member.save(update_fields=["is_active"])
        return member
