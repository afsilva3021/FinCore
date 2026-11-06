import asyncio
import os
from urllib.parse import urlencode
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise

from fincore.app import app
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import User
from fincore.presentation.web import COOKIE_CSRF, csrf_serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _submit(**fields: str):
    token = uuid4().hex
    headers = {
        "cookie": f"{COOKIE_CSRF}={csrf_serializer.dumps(token)}",
        "content-type": "application/x-www-form-urlencoded",
    }
    body = urlencode({"csrf": token, **fields})
    client = RobynTestClient(app)
    return await asyncio.to_thread(client.post, "/register", headers=headers, body=body)


def _fields(email: str, password: str, confirm: str) -> dict[str, str]:
    return {
        "full_name": "Pessoa Cadastro",
        "organization": "Org Cadastro",
        "email": email,
        "password": password,
        "password_confirm": confirm,
    }


@pytest.mark.asyncio
async def test_registration_requires_matching_confirmation_and_keeps_typed_fields() -> None:
    email = f"confirmacao-{uuid4().hex}@example.test"
    response = await _submit(**_fields(email, "Cavalo-Bateria-42", "Cavalo-Bateria-43"))

    assert response.status_code == 400
    assert "A confirmação de senha não confere com a senha." in response.text
    assert f'value="{email}"' in response.text
    assert 'value="Org Cadastro"' in response.text
    assert "Cavalo-Bateria-4" not in response.text
    assert not await User.exists(email=email)


@pytest.mark.asyncio
async def test_registration_rejects_weak_password_on_the_server() -> None:
    email = f"fraca-{uuid4().hex}@example.test"
    response = await _submit(**_fields(email, "senhafracademais", "senhafracademais"))

    assert response.status_code == 400
    assert "3 destes tipos" in response.text
    assert not await User.exists(email=email)


@pytest.mark.asyncio
async def test_registration_with_strong_confirmed_password_creates_the_account() -> None:
    email = f"forte-{uuid4().hex}@example.test"
    response = await _submit(**_fields(email, "Cavalo-Bateria-42", "Cavalo-Bateria-42"))

    assert response.status_code == 303
    assert response.headers.get("location") == "/dashboard"
    assert await User.exists(email=email)


def test_registration_page_asks_for_password_confirmation() -> None:
    page = RobynTestClient(app).get("/register")

    assert page.status_code == 200
    assert 'name="password_confirm"' in page.text
    assert "pelo menos 3 destes tipos" in page.text
