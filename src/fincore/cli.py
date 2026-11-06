"""Comandos operacionais do FinCore."""

import asyncio

from tortoise import Tortoise

from fincore.application.finance import backfill_settled_movement_journals
from fincore.config.database import TORTOISE_ORM
from fincore.config.settings import settings
from fincore.infrastructure.db_roles import ensure_app_role, settings_from_env


async def _backfill_accounting() -> int:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    try:
        return await backfill_settled_movement_journals()
    finally:
        await Tortoise.close_connections()


def backfill_accounting_main() -> None:
    created = asyncio.run(_backfill_accounting())
    print(f"Lançamentos contábeis criados para {created} movimentação(ões) liquidadas.")


def setup_app_db_role_main() -> None:
    """Cria/atualiza o usuário restrito da aplicação. Roda com o usuário dono do banco."""
    role, password = settings_from_env()
    asyncio.run(ensure_app_role(settings.database_url, role, password))
    print(f"Usuário de banco da aplicação '{role}' pronto (somente leitura/escrita de dados).")
