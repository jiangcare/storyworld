"""本地持久化键值存储。短事务跨进程互斥，租约按持有者释放。"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.sqlite import insert

from .models import RuntimeEntry


class LocalStore:
    def __init__(self, engine):
        self.engine = engine

    async def get(self, key):
        def read():
            with self.engine.connect() as conn:
                return conn.scalar(select(RuntimeEntry.value).where(
                    RuntimeEntry.key == key,
                    or_(RuntimeEntry.expires_at.is_(None), RuntimeEntry.expires_at > time.time()),
                ))
        return await asyncio.to_thread(read)

    async def set(self, key, value, *, ex=None, nx=False):
        if ex is not None and ex <= 0:
            raise ValueError("过期时间必须为正数")

        def write():
            with self.engine.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                now = time.time()
                conn.execute(delete(RuntimeEntry).where(RuntimeEntry.expires_at <= now))
                stmt = insert(RuntimeEntry).values(key=key, value=str(value),
                                                   expires_at=now + ex if ex is not None else None)
                if nx:
                    stmt = stmt.on_conflict_do_nothing(index_elements=["key"])
                else:
                    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={
                        "value": stmt.excluded.value, "expires_at": stmt.excluded.expires_at,
                    })
                changed = conn.execute(stmt).rowcount
                conn.commit()
                return bool(changed)
        return await asyncio.to_thread(write)

    async def delete(self, key):
        return await self._delete(key)

    async def compare_delete(self, key, value):
        """旧持有者不能误删已经过期并被别人重新取得的租约。"""
        return await self._delete(key, str(value))

    async def _delete(self, key, value=None):
        def write():
            with self.engine.begin() as conn:
                stmt = delete(RuntimeEntry).where(RuntimeEntry.key == key)
                if value is not None:
                    stmt = stmt.where(RuntimeEntry.value == value)
                return conn.execute(stmt).rowcount
        return await asyncio.to_thread(write)
