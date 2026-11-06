"""Casos de uso de cadastro e autenticação."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from fincore.infrastructure.models import LedgerAccount, Membership, Organization, User

password_hasher = PasswordHasher()

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
PASSWORD_RULES_TEXT = (
    "Use de 12 a 128 caracteres, combinando pelo menos 3 destes tipos: letra minúscula, "
    "letra maiúscula, número e símbolo."
)


def password_problems(password: str, *, email: str = "", full_name: str = "") -> list[str]:
    """Lista os motivos pelos quais a senha não é aceita (lista vazia = senha válida)."""
    problems: list[str] = []
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        problems.append("A senha deve ter de 12 a 128 caracteres.")
    classes = sum(
        (
            any(char.islower() for char in password),
            any(char.isupper() for char in password),
            any(char.isdigit() for char in password),
            any(not char.isalnum() for char in password),
        )
    )
    if classes < 3:
        problems.append(
            "Combine pelo menos 3 destes tipos: letra minúscula, letra maiúscula, número e símbolo."
        )
    if len(set(password)) < 5:
        problems.append("A senha repete poucos caracteres diferentes; escolha uma mais variada.")
    lowered = password.lower()
    personal = [email.split("@")[0].lower()] + [part.lower() for part in full_name.split()]
    if any(len(item) >= 4 and item in lowered for item in personal):
        problems.append("A senha não pode conter seu nome ou a parte inicial do seu e-mail.")
    return problems


def validate_password(password: str, *, email: str = "", full_name: str = "") -> None:
    problems = password_problems(password, email=email, full_name=full_name)
    if problems:
        raise ValueError(" ".join(problems))


async def register_user(full_name: str, email: str, password: str, organization_name: str) -> User:
    """Cria uma organização e seu usuário proprietário."""
    if (
        not full_name
        or len(full_name) > 160
        or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        or len(email) > 254
        or not organization_name
        or len(organization_name) > 160
    ):
        raise ValueError("Confira nome, e-mail e organização.")
    validate_password(password, email=email, full_name=full_name)
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
