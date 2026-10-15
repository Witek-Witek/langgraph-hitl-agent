import os
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row


def _db_uri() -> str:
    uri = os.environ.get("DATABASE_URL")
    if not uri:
        raise RuntimeError("Brak DATABASE_URL — ustaw connection string w .env.")
    return uri


@asynccontextmanager
async def checkpointer_single():
    """CLI: jedno połączenie na sesję."""
    async with AsyncPostgresSaver.from_conn_string(_db_uri()) as cp:
        await cp.setup()
        yield cp


@asynccontextmanager
async def open_pool(min_size: int = 1, max_size: int = 10):
    """Pula współdzielona przez checkpointer i rejestr wątków."""
    async with AsyncConnectionPool(
        conninfo=_db_uri(),
        min_size=min_size,
        max_size=max_size,
        kwargs={"autocommit": True, "row_factory": dict_row},
        connection_class=AsyncConnection[DictRow],
    ) as pool:
        yield pool


async def make_checkpointer(pool) -> AsyncPostgresSaver:
    cp = AsyncPostgresSaver(pool)
    await cp.setup()
    return cp


@asynccontextmanager
async def checkpointer_pooled(min_size: int = 1, max_size: int = 10):
    """Kontrakt D-17 bez zmian — dla punktów wejścia, które nie potrzebują rejestru."""
    async with open_pool(min_size, max_size) as pool:
        yield await make_checkpointer(pool)