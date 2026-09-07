"""真实 SQLite 的重启、过期、多进程互斥和游戏事务回归。"""
import asyncio
import copy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.db as dbmod
from tests.support import use_test_database

use_test_database()

from app.local_store import LocalStore
from app.models import PlayerAction, RuntimeEntry, Scene, Script, World, WorldPlayer
from app.engine import tick, world_service
from app.ai.narrative import Action, Plan
from app.engine.demo_story import DEMO


class StorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dbmod.Base.metadata.drop_all(dbmod.engine)
        dbmod.init_db()
        self.store = LocalStore(dbmod.engine)
        from tests.support import fake_game_intent
        mock = patch("app.ai.intent.parse_intent", side_effect=fake_game_intent)
        mock.start()
        self.addCleanup(mock.stop)

    def world(self, realtime=False):
        from seed import SEED_SCRIPTS
        content = copy.deepcopy(DEMO if realtime else SEED_SCRIPTS[0]["content_json"])
        db = dbmod.SessionLocal()
        script = Script(title="存储测试", mode=content["mode"], content_json=content)
        db.add(script)
        db.commit()
        user = world_service.get_or_create_user(db, 123)
        world = world_service.create_world(db, user, script)
        player = world_service.join_world(db, world, user)
        if not realtime:
            second = world_service.get_or_create_user(db, 456)
            world_service.join_world(db, world, second)
        world_service.start_world(db, world)
        ids = world.id, player.id
        db.close()
        return ids

    def test_file_settings_and_foreign_keys(self):
        with dbmod.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("PRAGMA journal_mode").scalar(), "wal")
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
        with dbmod.SessionLocal() as db:
            db.add(World(script_id=999, owner_id=999, title="无效引用"))
            with self.assertRaises(IntegrityError):
                db.commit()
        self.assertTrue(Path(dbmod.engine.url.database).is_absolute())
        with self.assertRaises(ValueError):
            dbmod.create_db_engine("mysql://localhost/unused")

    async def test_sessions_survive_new_engine_and_expire(self):
        await self.store.set("web_session:token", 42, ex=60)
        await self.store.set("draft:1", "中文剧本", ex=60)
        other_engine = dbmod.create_db_engine(str(dbmod.engine.url))
        try:
            other = LocalStore(other_engine)
            self.assertEqual(await other.get("web_session:token"), "42")
            self.assertEqual(await other.get("draft:1"), "中文剧本")
            await other.delete("web_session:token")
            self.assertIsNone(await self.store.get("web_session:token"))
        finally:
            other_engine.dispose()
        with patch("app.local_store.time.time", return_value=100):
            await self.store.set("expiring", "value", ex=5)
        with patch("app.local_store.time.time", return_value=106):
            self.assertIsNone(await self.store.get("expiring"))
            self.assertTrue(await self.store.set("expiring", "new", ex=5, nx=True))

    async def test_online_backup_includes_committed_wal_and_never_overwrites(self):
        from backup_db import backup_database
        import sqlite3
        from tempfile import TemporaryDirectory
        await self.store.set("draft:backup", "还在 WAL 中的故事", ex=60)
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "backup.db"
            backup_database(dbmod.engine.url.database, destination)
            with sqlite3.connect(str(destination)) as conn:
                value = conn.execute("SELECT value FROM runtime_entries WHERE key = ?", ("draft:backup",)).fetchone()[0]
                self.assertEqual(value, "还在 WAL 中的故事")
            with self.assertRaises(FileExistsError):
                backup_database(dbmod.engine.url.database, destination)

    async def test_atomic_locks_across_processes_and_owner_release(self):
        code = """import asyncio
from app.db import get_store
async def main():
    store = await get_store()
    print(int(await store.set('process-lock', 'owner', ex=60, nx=True)))
asyncio.run(main())
"""
        env = dict(os.environ, DATABASE_URL=str(dbmod.engine.url))
        async def worker():
            return await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code],
                                           cwd=dbmod.PROJECT_ROOT, env=env, capture_output=True,
                                           text=True, check=True, timeout=30)
        results = await asyncio.gather(*[worker() for _ in range(4)])
        self.assertEqual(sum(int(p.stdout.strip()) for p in results), 1)
        with patch("app.local_store.time.time", return_value=100):
            await self.store.set("lease", "old", ex=1)
        with patch("app.local_store.time.time", return_value=102):
            self.assertTrue(await self.store.set("lease", "new", ex=60, nx=True))
            self.assertEqual(await self.store.compare_delete("lease", "old"), 0)
            self.assertEqual(await self.store.get("lease"), "new")
            self.assertEqual(await self.store.compare_delete("lease", "new"), 1)

    async def test_action_quota_atomic_and_persistent(self):
        world_id, player_id = self.world()
        def submit():
            with dbmod.SessionLocal() as db:
                return asyncio.run(world_service.record_action(
                    db, db.get(World, world_id), db.get(WorldPlayer, player_id), "观察",
                ))[0]
        accepted = await asyncio.gather(*[asyncio.to_thread(submit) for _ in range(8)])
        self.assertEqual(sum(accepted), 3)
        with dbmod.SessionLocal() as db:
            self.assertEqual(db.query(PlayerAction).count(), 3)
        self.assertFalse(await asyncio.to_thread(submit))

    async def test_concurrent_realtime_plans_commit_only_once(self):
        world_id, player_id = self.world(realtime=True)
        ready = asyncio.Event()
        parsed = 0
        async def parse(*args):
            nonlocal parsed
            parsed += 1
            if parsed == 2:
                ready.set()
            await ready.wait()
            return Plan(actions=[Action(kind="move", target="archive")])
        async def submit():
            with dbmod.SessionLocal() as db:
                return await world_service.record_action(db, db.get(World, world_id),
                                                        db.get(WorldPlayer, player_id), "去档案室")
        with patch("app.engine.narrative.ai.parse_plan", side_effect=parse), \
                patch("app.engine.narrative.ai.narrate", AsyncMock(return_value="你走进档案室。")):
            results = await asyncio.gather(submit(), submit())
        self.assertEqual(sum(ok for ok, _ in results), 1)
        with dbmod.SessionLocal() as db:
            self.assertEqual(db.query(PlayerAction).count(), 1)
            self.assertEqual(db.get(World, world_id).progress_json["narrative"]["minute"], 5)

    async def test_tick_does_not_lock_writes_during_ai_and_saves_json(self):
        world_id, player_id = self.world()
        calls = 0
        async def writer(**kwargs):
            nonlocal calls
            calls += 1
            # 旧实现会在第一个玩家 flush 后持写锁，第二次写会话将阻塞。
            await asyncio.wait_for(self.store.set("session:while-ai", "user", ex=60), timeout=3)
            return {"narrative": "场景", "suggested_actions": [],
                    "state_changes": {"hp_delta": -1, "items_added": ["绷带"], "notes": {"记忆": "雨夜"}}}
        with patch("app.engine.tick.director_ai.generate_world_update", AsyncMock(return_value={"public_broadcast": "世界动态"})), \
                patch("app.engine.tick.writer_ai.generate_scene", side_effect=writer):
            for _ in range(2):
                with dbmod.SessionLocal() as db:
                    result = await tick.run_tick(db, world_id)
                    self.assertIsNotNone(result)
        self.assertEqual(calls, 4)
        with dbmod.SessionLocal() as db:
            self.assertEqual(db.get(World, world_id).day, 3)
            state = db.get(WorldPlayer, player_id).private_state
            self.assertEqual(state["hp"], 8)
            self.assertEqual(state["notes"]["记忆"], "雨夜")
            self.assertEqual(db.query(Scene).count(), 4)

    async def test_expired_tick_cannot_commit_or_unlock_new_owner(self):
        world_id, _ = self.world()
        async def director(**kwargs):
            await self.store.set(f"lock:tick:{world_id}", "new-owner", ex=60)
            return {"public_broadcast": "旧任务不应提交"}
        with patch("app.engine.tick.director_ai.generate_world_update", side_effect=director), \
                patch("app.engine.tick.writer_ai.generate_scene", AsyncMock(return_value={
                    "narrative": "场景", "suggested_actions": [], "state_changes": {},
                })):
            with dbmod.SessionLocal() as db:
                self.assertIsNone(await tick.run_tick(db, world_id))
        self.assertEqual(await self.store.get(f"lock:tick:{world_id}"), "new-owner")
        with dbmod.SessionLocal() as db:
            self.assertEqual(db.get(World, world_id).day, 1)
            self.assertEqual(db.query(Scene).count(), 0)


if __name__ == "__main__":
    unittest.main()
