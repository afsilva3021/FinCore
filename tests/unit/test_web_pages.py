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


def test_password_toggle_renders_font_awesome_eye_icons() -> None:
    client = RobynTestClient(app)

    for path in ("/login", "/register"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'class="fa-solid fa-eye-slash"' in response.text
        assert 'aria-pressed="false"' in response.text

    script = Path(__file__).parents[2] / "static" / "js" / "app.js"
    source = script.read_text()
    assert "icon.classList.toggle('fa-eye', visible)" in source
    assert "icon.classList.toggle('fa-eye-slash', !visible)" in source


def test_open_navigation_accordion_reveals_its_last_link() -> None:
    script = Path(__file__).parents[2] / "static" / "js" / "app.js"
    source = script.read_text()

    assert "setOpenNavAccordion = (activeButton, shouldOpen, revealEnd = false)" in source
    assert "panel.lastElementChild?.scrollIntoView({ block: 'nearest' })" in source
    assert "setOpenNavAccordion(button, shouldOpen, shouldOpen)" in source


def test_dashboard_sidebar_contains_customer_and_supplier_links() -> None:
    template = Path(__file__).parents[2] / "templates" / "dashboard.html"
    source = template.read_text()
    finance_group = source.split('<div class="nav-group" id="nav-finance">', 1)[1].split(
        "</div>", 1
    )[0]

    assert 'href="/customers"' in finance_group
    assert 'href="/suppliers"' in finance_group


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

    monkeypatch.setattr("fincore.presentation.routes.api.database_is_ready", database_ready)
    client = RobynTestClient(app)

    health = client.get("/api/v1/health")
    accounts = client.get("/api/v1/accounts")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": "v1"}
    assert accounts.status_code == 401
    assert accounts.json()["error"]["code"] == "unauthenticated"
