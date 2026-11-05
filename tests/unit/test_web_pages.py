from pathlib import Path

from robyn.testing import TestClient as RobynTestClient

from fincore.app import app


def test_login_page_renders_portuguese_html_and_csrf_cookie() -> None:
    response = RobynTestClient(app).get("/login")

    assert response.status_code == 200
    assert "Acesse sua conta" in response.text
    assert 'lang="pt-BR"' in response.text
    assert "bootstrap" in response.text.lower()
    assert 'id="fincoreConfirmModal"' in response.text


def test_registration_page_renders_organization_fields() -> None:
    response = RobynTestClient(app).get("/register")

    assert response.status_code == 200
    assert "Crie sua conta" in response.text
    assert 'name="organization"' in response.text


def test_user_confirmations_use_the_shared_modal_instead_of_browser_dialogs() -> None:
    script = Path(__file__).parents[2] / "static" / "js" / "app.js"
    source = script.read_text()

    assert "showConfirmation" in source
    assert "window.alert(" not in source
    assert "window.confirm(" not in source


def test_versioned_api_exposes_health_and_requires_session_for_business_data(
    monkeypatch,
) -> None:
    async def database_ready() -> bool:
        return True

    monkeypatch.setattr("fincore.presentation.web.database_is_ready", database_ready)
    client = RobynTestClient(app)

    health = client.get("/api/v1/health")
    accounts = client.get("/api/v1/accounts")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": "v1"}
    assert accounts.status_code == 401
    assert accounts.json()["error"]["code"] == "unauthenticated"
