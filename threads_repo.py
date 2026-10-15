import uuid


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None


class ThreadsRepo:
    """Rejestr wątków. Każda metoda filtruje po user_id — nie ma metody 'po samym thread_id'."""

    def __init__(self, pool):
        self._pool = pool

    async def create(self, user_id: str, label: str | None) -> dict:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "insert into threads (user_id, thread_id, label) values (%s, %s, %s) "
                "returning thread_id, label, created_at, updated_at",
                (user_id, uuid.uuid4(), label),
            )
            return await cur.fetchone()

    async def list(self, user_id: str) -> list[dict]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select thread_id, label, created_at, updated_at from threads "
                "where user_id = %s order by updated_at desc",
                (user_id,),
            )
            return await cur.fetchall()

    async def owns(self, user_id: str, thread_id: str) -> bool:
        tid = _as_uuid(thread_id)
        if tid is None:
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select 1 from threads where user_id = %s and thread_id = %s",
                (user_id, tid),
            )
            return await cur.fetchone() is not None

    async def rename(self, user_id: str, thread_id: str, label: str) -> dict | None:
        tid = _as_uuid(thread_id)
        if tid is None:
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "update threads set label = %s, updated_at = now() "
                "where user_id = %s and thread_id = %s "
                "returning thread_id, label, created_at, updated_at",
                (label, user_id, tid),
            )
            return await cur.fetchone()

    async def delete(self, user_id: str, thread_id: str) -> bool:
        tid = _as_uuid(thread_id)
        if tid is None:
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "delete from threads where user_id = %s and thread_id = %s",
                (user_id, tid),
            )
            return cur.rowcount > 0

    async def touch(self, user_id: str, thread_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "update threads set updated_at = now() where user_id = %s and thread_id = %s",
                (user_id, _as_uuid(thread_id)),
            )