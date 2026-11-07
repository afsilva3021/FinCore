"""Casos de uso de cadastro e autenticação."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from tortoise.expressions import F
from tortoise.transactions import in_transaction

from fincore.infrastructure.models import (
    LedgerAccount,
    LoginThrottle,
    Membership,
    Organization,
    User,
)

password_hasher = PasswordHasher()
# Hash de uma senha aleatória: verificar contra ele quando o e-mail não existe faz a
# resposta levar o mesmo tempo, sem revelar quais e-mails estão cadastrados.
_DUMMY_HASH = password_hasher.hash("fincore-dummy-password-for-constant-time")

LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCK = timedelta(minutes=15)
MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_IP = 20

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
    try:
        password_hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
    except VerifyMismatchError, VerificationError:
        return None
    if user is None:
        return None
    membership = await Membership.filter(
        user=user, is_active=True, organization__is_active=True
    ).first()
    if membership is None:
        return None
    return user, membership


@dataclass(frozen=True, slots=True)
class LoginResult:
    session: tuple[User, Membership] | None
    locked_minutes: int = 0


def _throttle_keys(email: str, ip: str | None) -> list[tuple[str, int]]:
    keys = [(f"email:{email.strip().lower()[:300]}", MAX_FAILURES_PER_EMAIL)]
    if ip:
        keys.append((f"ip:{ip[:300]}", MAX_FAILURES_PER_IP))
    return keys


async def _locked_minutes(keys: list[tuple[str, int]], now: datetime) -> int:
    rows = await LoginThrottle.filter(
        key__in=[key for key, _ in keys], locked_until__gt=now
    ).values_list("locked_until", flat=True)
    if not rows:
        return 0
    remaining = max(rows) - now
    return max(1, int(remaining.total_seconds() // 60) + 1)


async def _register_failure(keys: list[tuple[str, int]], now: datetime) -> None:
    async with in_transaction() as connection:
        for key, limit in keys:
            # Upsert evita corrida entre duas primeiras falhas simultâneas na mesma chave.
            await connection.execute_query(
                "INSERT INTO loginthrottle (id, key, failures, window_started_at, updated_at) "
                "VALUES ($1, $2, 0, $3, $3) ON CONFLICT (key) DO NOTHING",
                [str(uuid4()), key, now],
            )
            throttle = (
                await LoginThrottle.filter(key=key).using_db(connection).select_for_update().get()
            )
            if now - throttle.window_started_at > LOGIN_WINDOW:
                throttle.failures = 0
                throttle.window_started_at = now
                throttle.locked_until = None
            throttle.failures += 1
            if throttle.failures >= limit:
                throttle.locked_until = now + LOGIN_LOCK
            await throttle.save(using_db=connection)


async def login_with_throttle(email: str, password: str, ip: str | None) -> LoginResult:
    """Autentica com bloqueio temporário após falhas repetidas por e-mail ou por IP.

    O bloqueio vale para o e-mail digitado mesmo que ele não exista, então a resposta
    não revela se a conta está cadastrada. Um login bem-sucedido zera o contador do e-mail.
    """
    now = datetime.now(UTC)
    keys = _throttle_keys(email, ip)
    locked = await _locked_minutes(keys, now)
    if locked:
        return LoginResult(session=None, locked_minutes=locked)
    session = await authenticate(email.strip(), password)
    if session is None:
        await _register_failure(keys, now)
        locked = await _locked_minutes(keys, now)
        return LoginResult(session=None, locked_minutes=locked)
    await LoginThrottle.filter(key=keys[0][0]).delete()
    return LoginResult(session=session)


async def revoke_sessions(user: User) -> None:
    """Invalida todos os tokens de sessão do usuário (logout em todos os dispositivos)."""
    await User.filter(id=user.id).update(session_version=F("session_version") + 1)
