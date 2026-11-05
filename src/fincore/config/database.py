"""Configuração do Tortoise ORM e do banco PostgreSQL."""

from fincore.config.settings import settings

TORTOISE_ORM = {
    "connections": {"default": settings.database_url},
    "apps": {
        "models": {
            "models": ["fincore.infrastructure.models"],
            "default_connection": "default",
            "migrations": "fincore.infrastructure.migrations",
        }
    },
}
