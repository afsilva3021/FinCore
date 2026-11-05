"""Casos de uso de cadastro e autenticação."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from fincore.infrastructure.models import LedgerAccount, Membership, Organization, User

password_hasher = PasswordHasher()


async def register_user(full_name: str, email: str, password: str, organization_name: str) -> User:
    """Cria uma organização e seu usuário proprietário."""
    if (
        not full_name
        or len(full_name) > 160
        or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        or len(email) > 254
        or not organization_name
        or len(organization_name) > 160
        or not 12 <= len(password) <= 128
    ):
        raise ValueError("Confira os dados e use uma senha de 12 a 128 caracteres.")
    from tortoise.transactions import in_transaction

    async with in_transaction():
        organization = await Organization.create(name=organization_name)
        user = await User.create(
            full_name=full_name,
            email=email.lower(),
            password_hash=password_hasher.hash(password),
        )
        await Membership.create(user=user, organization=organization, role="owner")
        await LedgerAccount.bulk_create(
            [
                LedgerAccount(
                    organization=organization,
                    created_by=user,
                    code="SYS-REV",
                    name="Receitas sem categoria",
                    account_type="revenue",
                ),
                LedgerAccount(
                    organization=organization,
                    created_by=user,
                    code="SYS-EXP",
                    name="Despesas sem categoria",
                    account_type="expense",
                ),
                LedgerAccount(
                    organization=organization,
                    created_by=user,
                    code="SYS-EQUITY",
                    name="Patrimônio de abertura",
                    account_type="equity",
                ),
            ]
        )
    return user


async def authenticate(email: str, password: str) -> tuple[User, Membership] | None:
    """Valida credenciais e retorna a primeira organização ativa do usuário."""
    user = await User.get_or_none(email=email.lower(), is_active=True)
    if user is None:
        return None
    try:
        password_hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        return None
    membership = await Membership.filter(
        user=user, is_active=True, organization__is_active=True
    ).first()
    if membership is None:
        return None
    return user, membership
