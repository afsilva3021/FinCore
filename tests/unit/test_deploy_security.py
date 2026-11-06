from dataclasses import replace

import pytest

from fincore.config.settings import DEFAULT_AUTH_SECRET, Settings, startup_problems
from fincore.infrastructure.db_roles import validate_role_settings

STRONG_SECRET = "k" * 20 + "Z9-" + "x" * 20


def _settings(host: str, secret: str) -> Settings:
    return replace(Settings(), host=host, auth_secret=secret)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_development_may_use_the_default_secret(host: str) -> None:
    assert startup_problems(_settings(host, DEFAULT_AUTH_SECRET)) == []


@pytest.mark.parametrize("secret", [DEFAULT_AUTH_SECRET, "curto-demais"])
def test_exposed_app_refuses_default_or_short_secret(secret: str) -> None:
    problems = startup_problems(_settings("0.0.0.0", secret))
    assert problems and "AUTH_SECRET" in problems[0]


def test_exposed_app_starts_with_a_strong_secret() -> None:
    assert startup_problems(_settings("0.0.0.0", STRONG_SECRET)) == []


def test_app_db_role_must_differ_from_owner_and_have_a_long_password() -> None:
    validate_role_settings("fincore_app", "senha-do-app-bem-longa", "fincore")
    with pytest.raises(ValueError, match="diferente"):
        validate_role_settings("fincore", "senha-do-app-bem-longa", "fincore")
    with pytest.raises(ValueError, match="16"):
        validate_role_settings("fincore_app", "curta", "fincore")
    with pytest.raises(ValueError, match="minúsculas"):
        validate_role_settings('app"; DROP ROLE x; --', "senha-do-app-bem-longa", "fincore")
