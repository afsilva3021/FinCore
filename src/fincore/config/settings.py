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


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8080"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    database_url: str = _database_url()
    auth_secret: str = os.getenv("AUTH_SECRET", "local-development-key-change-before-deploying")
    secure_cookies: bool = os.getenv("SECURE_COOKIES", "false").lower() == "true"


settings = Settings()
