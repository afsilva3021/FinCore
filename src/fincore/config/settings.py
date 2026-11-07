"""Leitura de configurações por variáveis de ambiente."""

import os
from dataclasses import dataclass
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


def _database_url() -> str:
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url
    username = quote(os.getenv("POSTGRES_USER", "fincore"), safe="")
    password = quote(os.getenv("POSTGRES_PASSWORD", "change-me-locally"), safe="")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5433")
    database = quote(os.getenv("POSTGRES_DB", "fincore"), safe="")
    return f"postgres://{username}:{password}@{host}:{port}/{database}"


DEFAULT_AUTH_SECRET = "local-development-key-change-before-deploying"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8080"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    database_url: str = _database_url()
    auth_secret: str = os.getenv("AUTH_SECRET", DEFAULT_AUTH_SECRET)
    # IPs de proxies reversos confiáveis; só deles o X-Forwarded-For é aceito.
    trusted_proxies: frozenset[str] = frozenset(
        item.strip() for item in os.getenv("TRUSTED_PROXIES", "").split(",") if item.strip()
    )
    secure_cookies: bool = os.getenv("SECURE_COOKIES", "false").lower() == "true"


def startup_problems(config: Settings) -> list[str]:
    """Configurações inseguras que impedem a aplicação de subir exposta na rede."""
    problems: list[str] = []
    exposed = config.host not in LOOPBACK_HOSTS
    if exposed and (config.auth_secret == DEFAULT_AUTH_SECRET or len(config.auth_secret) < 32):
        problems.append(
            "AUTH_SECRET precisa ser trocado (32+ caracteres aleatórios) quando APP_HOST "
            "não é local. Gere um com: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return problems


settings = Settings()
