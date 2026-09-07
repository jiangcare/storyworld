"""SQLite 文件库 集成测试（仅模拟 LLM，验证真实 SQLite 存档与事务）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as dbmod
from tests.support import use_test_database

use_test_database()

import app.ai.director as director_mod
import app.ai.writer as writer_mod


async def fake_world_update(**kw):
    return {
        "public_broadcast": f"[第{kw['day']}天] 城市上空响起警报，人群向码头涌动。",
        "canon_additions": ["警报响起", "码头方向出现火光"],
        "countdown_update": "",
        "chapter_note": "",
        "world_ended": False,
    }


async def fake_scene(**kw):
    return {
        "narrative": f"场景：{kw['character_card'].get('name', '你')}在废墟中发现线索。",
        "suggested_actions": ["查看火光方向", "继续搜索", "返回据点"],
        "state_changes": {
            "hp_delta": -1,
            "items_added": ["手电筒"],
            "items_removed": [],
            "clues_added": ["神秘字条"],
            "notes": {"关系": "光头强 中立"},
        },
        "scene_ended": False,
    }


director_mod.generate_world_update = fake_world_update
writer_mod.generate_scene = fake_scene


async def main():
    from seed import seed
    seed()
    from app.engine import world_service
    from app.engine.tick import run_tick
    from app.models import CanonEvent, Scene, Script, World

    db = dbmod.SessionLocal()
    multi = db.query(Script).filter(Script.mode == "multi").first()
    assert multi, "缺少多人剧本"

    u1 = world_service.get_or_create_user(db, 9001, username="sqlite_a", display_name="测试甲")
    u2 = world_service.get_or_create_user(db, 9002, username="sqlite_b", display_name="测试乙")
    w = world_service.create_world(db, u1, multi, chat_id=-999)
    world_service.join_world(db, w, u1)
    world_service.join_world(db, w, u2)
    world_service.start_world(db, w)
    assert w.status == "running"

    p1 = world_service.get_player(db, w, u1.id)
    ok, msg = await world_service.record_action(db, w, p1, "我悄悄前往码头调查火光")
    assert ok, msg

    res = await run_tick(db, w.id)
    w2 = db.get(World, w.id)
    assert res is not None and w2.day == 2, f"tick 后应到第2天: {w2.day}"
    assert len(res.scenes) == 2, f"应有2个场景: {len(res.scenes)}"
    assert db.query(CanonEvent).filter_by(world_id=w.id).count() >= 1

    p1b = world_service.get_player(db, w2, u1.id)
    st = p1b.private_state
    assert st["hp"] == 9, f"hp 应为9: {st['hp']}"
    assert "手电筒" in st["items"] and "神秘字条" in st["clues"], st
    assert st["notes"].get("关系") == "光头强 中立", st
    print("SQLITE TICK TEST PASSED (中文/JSON列/事务 OK)")


if __name__ == "__main__":
    asyncio.run(main())
