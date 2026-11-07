import asyncio
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise

from fincore.app import app
from fincore.application.identity import (
    MAX_FAILURES_PER_EMAIL,
    login_with_throttle,
    register_user,
)
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import LoginThrottle, User
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _user() -> User:
    return await register_user(
        "Pessoa Segura", f"seguranca-{uuid4().hex}@example.test", PASSWORD, "Org Segura"
    )


@pytest.mark.asyncio
async def test_email_is_locked_after_repeated_failures_even_with_the_right_password() -> None:
    user = await _user()
    ip = f"10.0.{uuid4().int % 250}.1"
    for _ in range(MAX_FAILURES_PER_EMAIL):
        assert (await login_with_throttle(user.email, "Senha-Errada-123", ip)).session is None

    locked = await login_with_throttle(user.email, PASSWORD, ip)
    assert locked.session is None
    assert locked.locked_minutes > 0


@pytest.mark.asyncio
async def test_unknown_email_is_locked_the_same_way_as_a_real_one() -> None:
    email = f"inexistente-{uuid4().hex}@example.test"
    for _ in range(MAX_FAILURES_PER_EMAIL):
        await login_with_throttle(email, "Qualquer-Senha-1", None)
    assert (await login_with_throttle(email, "Qualquer-Senha-1", None)).locked_minutes > 0


@pytest.mark.asyncio
async def test_lock_expires_and_success_clears_the_counter() -> None:
    user = await _user()
    for _ in range(MAX_FAILURES_PER_EMAIL):
        await login_with_throttle(user.email, "Senha-Errada-123", None)
    past = datetime.now(UTC) - timedelta(hours=1)
    await LoginThrottle.filter(key=f"email:{user.email}").update(
        locked_until=past, window_started_at=past
    )

    result = await login_with_throttle(user.email, PASSWORD, None)
    assert result.session is not None
    assert not await LoginThrottle.exists(key=f"email:{user.email}")


@pytest.mark.asyncio
async def test_login_page_answers_429_while_locked() -> None:
    user = await _user()
    for _ in range(MAX_FAILURES_PER_EMAIL):
        await login_with_throttle(user.email, "Senha-Errada-123", None)
    token = uuid4().hex
    response = await asyncio.to_thread(
        RobynTestClient(app).post,
        "/login",
        headers={
            "cookie": f"{COOKIE_CSRF}={csrf_serializer.dumps(token)}",
            "content-type": "application/x-www-form-urlencoded",
        },
        body=urlencode({"csrf": token, "email": user.email, "password": PASSWORD}),
    )
    assert response.status_code == 429
    assert "Muitas tentativas de login" in response.text


@pytest.mark.asyncio
async def test_logout_revokes_a_copied_session_cookie() -> None:
    user = await _user()
    result = await login_with_throttle(user.email, PASSWORD, None)
    assert result.session is not None
    from fincore.presentation.web import _session_token

    session = _session_token(*result.session)
    token = uuid4().hex
    cookies = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    client = RobynTestClient(app)

    before = await asyncio.to_thread(client.get, "/dashboard", headers={"cookie": cookies})
    assert before.status_code == 200
    await asyncio.to_thread(
        client.post,
        "/logout",
        headers={"cookie": cookies, "content-type": "application/x-www-form-urlencoded"},
        body=f"csrf={token}",
    )
    after = await asyncio.to_thread(client.get, "/dashboard", headers={"cookie": cookies})
    assert after.status_code == 303
    assert after.headers.get("location") == "/login"


def test_every_page_carries_security_headers() -> None:
    response = RobynTestClient(app).get("/login")
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("x-content-type-options") == "nosniff"
    policy = response.headers.get("content-security-policy") or ""
    assert "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy
