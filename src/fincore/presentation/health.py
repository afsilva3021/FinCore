"""Caso de uso do endpoint de saúde."""

import logging

from tortoise import Tortoise

logger = logging.getLogger(__name__)


async def database_is_ready() -> bool:
    """Retorna se o PostgreSQL aceita uma consulta simples."""
    try:
        connection = Tortoise.get_connection("default")
        await connection.execute_query("SELECT 1")
    except Exception as exc:
        logger.warning("Falha na verificação de saúde do banco (%s)", type(exc).__name__)
        return False
    return True
