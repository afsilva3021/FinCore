"""Comandos operacionais do FinCore."""

import asyncio

from tortoise import Tortoise

from fincore.application.finance import backfill_settled_movement_journals
from fincore.config.database import TORTOISE_ORM


async def _backfill_accounting() -> int:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    try:
        return await backfill_settled_movement_journals()
    finally:
        await Tortoise.close_connections()


def backfill_accounting_main() -> None:
    created = asyncio.run(_backfill_accounting())
    print(f"Lançamentos contábeis criados para {created} movimentação(ões) liquidadas.")
