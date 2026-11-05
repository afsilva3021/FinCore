from unittest.mock import AsyncMock, patch

import pytest

from fincore.presentation.health import database_is_ready


@pytest.mark.asyncio
async def test_database_is_ready_when_query_succeeds() -> None:
    connection = AsyncMock()
    with patch("fincore.presentation.health.Tortoise.get_connection", return_value=connection):
        assert await database_is_ready() is True
    connection.execute_query.assert_awaited_once_with("SELECT 1")


@pytest.mark.asyncio
async def test_database_is_not_ready_when_query_fails() -> None:
    connection = AsyncMock()
    connection.execute_query.side_effect = RuntimeError("database unavailable")
    with patch("fincore.presentation.health.Tortoise.get_connection", return_value=connection):
        assert await database_is_ready() is False
