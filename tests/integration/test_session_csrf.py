import asyncio
import os
import time
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise

from fincore.app import app
from fincore.application.identity import register_user
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import FinancialAccount, Membership
from fincore.presentation.web import (
    COOKIE_AUTH,
    COOKIE_CSRF,
    CSRF_TTL_SECONDS,
    SESSION_TTL_SECONDS,
    csrf_serializer,
    serializer,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


def test_csrf_token_lives_as_long_as_the_session() -> None:
    assert CSRF_TTL_SECONDS >= SESSION_TTL_SECONDS


@pytest.mark.asyncio
async def test_forms_keep_working_after_the_first_hour_of_session(monkeypatch) -> None:
    suffix = uuid4().hex
    user = await register_user(
        "Sessão Longa", f"sessao-{suffix}@example.test", "senha-cookie-antigo-1", "Org Sessão"
    )
    membership = await Membership.filter(user=user).first()
    assert membership is not None

    # Cookies emitidos há 2 horas: antes da correção, o CSRF expirava em 1 hora e
    # a página passava a renderizar o formulário com token vazio.
    issued_at = time.time() - 2 * 60 * 60
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: issued_at)
    token = "token-emitido-ha-duas-horas"
    session = serializer.dumps({"uid": str(user.id), "org": str(membership.organization_id)})
    csrf_cookie = csrf_serializer.dumps(token)
    monkeypatch.undo()

    headers = {"cookie": f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_cookie}"}
    client = RobynTestClient(app)
    page = await asyncio.to_thread(client.get, "/accounts", headers=headers)
    assert page.status_code == 200
    assert f'name="csrf" value="{token}"' in page.text

    body = f"csrf={token}&name=Caixa+{suffix[:8]}&account_type=cash&opening_balance=0"
    created = await asyncio.to_thread(
        client.post,
        "/accounts",
        headers={**headers, "content-type": "application/x-www-form-urlencoded"},
        body=body,
    )
    assert created.status_code == 303
    assert await FinancialAccount.filter(
        organization_id=membership.organization_id, name=f"Caixa {suffix[:8]}"
    ).exists()
