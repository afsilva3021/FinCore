import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from robyn.testing import TestClient as RobynTestClient
from tortoise import Tortoise

from fincore.app import app
from fincore.application.identity import register_user
from fincore.application.members import add_member, change_member_role, set_member_active
from fincore.config.database import TORTOISE_ORM
from fincore.infrastructure.models import FinancialAccount, Membership, User
from fincore.presentation.web import COOKIE_AUTH, COOKIE_CSRF, csrf_serializer, serializer

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="Configure DATABASE_URL para o PostgreSQL de teste isolado.",
)

PASSWORD = "Cavalo-Bateria-42"
FORM = "application/x-www-form-urlencoded"


@pytest_asyncio.fixture(autouse=True)
async def database_context() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    yield
    await Tortoise.close_connections()


async def _organization_with_owner():
    owner = await register_user(
        "Dona Perfis", f"perfis-{uuid4().hex}@example.test", PASSWORD, "Org Perfis"
    )
    membership = await Membership.get(user=owner)
    return owner, membership, await membership.organization


def _headers(user: User, membership: Membership) -> tuple[dict[str, str], str]:
    token = uuid4().hex
    session = serializer.dumps(
        {"uid": str(user.id), "org": str(membership.organization_id), "sv": user.session_version}
    )
    cookie = f"{COOKIE_AUTH}={session}; {COOKIE_CSRF}={csrf_serializer.dumps(token)}"
    return {"cookie": cookie, "content-type": FORM}, token


@pytest.mark.asyncio
async def test_viewer_can_read_but_every_write_is_refused() -> None:
    _, _, organization = await _organization_with_owner()
    viewer_membership = await add_member(
        organization, "Leitor", f"leitor-{uuid4().hex}@example.test", PASSWORD, "viewer"
    )
    viewer = await viewer_membership.user
    headers, token = _headers(viewer, viewer_membership)
    client = RobynTestClient(app)

    page = await asyncio.to_thread(client.get, "/accounts", headers=headers)
    assert page.status_code == 200
    assert "Perfil somente leitura" in page.text

    body = f"csrf={token}&name=Bloqueada&account_type=cash&opening_balance=0"
    html = await asyncio.to_thread(client.post, "/accounts", headers=headers, body=body)
    assert html.status_code == 403
    api = await asyncio.to_thread(
        client.post,
        "/api/v1/accounts",
        headers={**headers, "content-type": "application/json", "x-csrf-token": token},
        body='{"name": "Bloqueada API", "account_type": "cash"}',
    )
    assert api.status_code == 403
    assert '"forbidden"' in api.text
    assert not await FinancialAccount.filter(
        organization=organization, name__startswith="Bloqueada"
    ).exists()


@pytest.mark.asyncio
async def test_finance_profile_writes_but_cannot_manage_members() -> None:
    _, _, organization = await _organization_with_owner()
    member = await add_member(
        organization, "Analista", f"analista-{uuid4().hex}@example.test", PASSWORD, "finance"
    )
    headers, token = _headers(await member.user, member)
    client = RobynTestClient(app)

    body = f"csrf={token}&name=Caixa+Financeiro&account_type=cash&opening_balance=0"
    created = await asyncio.to_thread(client.post, "/accounts", headers=headers, body=body)
    assert created.status_code == 303
    members = await asyncio.to_thread(client.get, "/members", headers=headers)
    assert members.status_code == 403


@pytest.mark.asyncio
async def test_owner_adds_member_through_the_page() -> None:
    owner, membership, organization = await _organization_with_owner()
    headers, token = _headers(owner, membership)
    email = f"novo-{uuid4().hex}@example.test"
    body = f"csrf={token}&full_name=Nova+Pessoa&email={email}&password={PASSWORD}&role=viewer"
    response = await asyncio.to_thread(
        RobynTestClient(app).post, "/members", headers=headers, body=body
    )
    assert response.status_code == 303
    assert await Membership.filter(
        organization=organization, user__email=email, role="viewer"
    ).exists()


@pytest.mark.asyncio
async def test_last_active_owner_cannot_be_demoted_or_deactivated() -> None:
    _, membership, organization = await _organization_with_owner()
    with pytest.raises(ValueError, match="pelo menos um proprietário"):
        await change_member_role(organization, membership.id, "viewer")
    with pytest.raises(ValueError, match="pelo menos um proprietário"):
        await set_member_active(organization, membership.id, False)

    second = await add_member(
        organization, "Sócia", f"socia-{uuid4().hex}@example.test", PASSWORD, "owner"
    )
    await change_member_role(organization, membership.id, "finance")
    assert (await Membership.get(id=membership.id)).role == "finance"
    assert (await Membership.get(id=second.id)).role == "owner"


@pytest.mark.asyncio
async def test_members_of_another_organization_cannot_be_changed() -> None:
    _, _, organization = await _organization_with_owner()
    _, other_membership, _ = await _organization_with_owner()
    with pytest.raises(ValueError, match="não encontrado"):
        await change_member_role(organization, other_membership.id, "viewer")
