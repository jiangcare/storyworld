"""离线冒烟测试：SQLite + FakeRedis + Mock LLM 验证全链路（无需 MySQL/Redis/API Key）。

用法：.venv\\Scripts\\python.exe -X utf8 smoke_test.py
"""
import asyncio
import sys

# ---- 1. 打补丁：SQLite 内存库 + FakeRedis（必须在导入 app.db 业务模块前） ----
import fakeredis
import app.db as dbmod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

dbmod.engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}
)
dbmod.SessionLocal = sessionmaker(
    bind=dbmod.engine, autoflush=False, expire_on_commit=False
)
dbmod._redis = fakeredis.FakeAsyncRedis(decode_responses=True)

# ---- 2. Mock LLM ----
import app.ai.director as director_mod
import app.ai.intent as intent_mod
import app.ai.script_ai as script_ai_mod
import app.ai.writer as writer_mod


async def fake_world_update(**kw):
    return {
        "public_broadcast": f"[第{kw['day']}天] 城市上空响起刺耳警报，人群向码头方向骚动。",
        "canon_additions": ["警报响起", "人群向码头聚集"],
        "countdown_update": f"距末日还剩 {max(kw['countdown_total'] - kw['day'], 0)} 天",
        "chapter_note": "注意人群与码头方向",
        "world_ended": bool(kw["day"] >= kw["total_days"]),
    }


async def fake_scene(**kw):
    name = kw["character_card"].get("name", "你")
    return {
        "narrative": f"[第{kw['day']}天] {name}在废墟中发现半罐饮用水，远处传来枪声。\n今日世界：{kw['world_broadcast'][:40]}",
        "suggested_actions": ["查看枪声方向", "继续搜索物资", "回到据点"],
        "state_changes": {
            "hp_delta": -1,
            "items_added": ["半罐饮用水"],
            "items_removed": [],
            "clues_added": [],
            "notes": {},
        },
        "scene_ended": False,
    }


async def fake_intent(text: str):
    return {
        "action_type": "investigate",
        "target": "",
        "summary": text[:20],
        "dice_check": False,
        "attribute": "",
    }


async def fake_complete(draft: str, mode: str):
    def card(i):
        return {"id": f"p{i}", "name": f"角色{i}", "role": "幸存者", "personality": "冷静",
                "secret": "秘密", "goal": "目标",
                "stats": {"strength": 3, "agility": 3, "intellect": 3, "charm": 3, "luck": 3},
                "public_desc": "简介"}
    cards = [card(1)] if mode == "single" else [card(1), card(2)]
    return {
        "title": "测试剧本",
        "description": "AI 完善的测试剧本",
        "genre": "测试",
        "mode": mode,
        "min_players": 1,
        "max_players": 1 if mode == "single" else 4,
        "days": 3,
        "world": {"name": "测试世界", "background": "背景", "rules": "规则", "countdown": "倒计时", "countdown_total": 3},
        "chapters": [
            {"day_start": 1, "day_end": 3, "title": "第一章", "goal": "目标",
             "events": [{"day": 1, "title": "事件", "desc": "描述", "magnitude": "mid"}]}
        ],
        "player_cards": cards,
        "npcs": [],
        "system_rules": "规则",
    }


director_mod.generate_world_update = fake_world_update
writer_mod.generate_scene = fake_scene
intent_mod.parse_intent = fake_intent
script_ai_mod.complete_script = fake_complete

# ---- 3. 场景 ----
from app.db import SessionLocal, init_db
from app.engine import world_service
from app.engine.tick import run_tick
from app.models import CanonEvent, Scene, Script, World


async def main():
    init_db()
    from seed import seed as run_seed

    run_seed()
    db = SessionLocal()

    # ---------- 多人世界 ----------
    multi = db.query(Script).filter(Script.mode == "multi").first()
    u1 = world_service.get_or_create_user(db, 111, username="alice", display_name="Alice")
    u2 = world_service.get_or_create_user(db, 222, username="bob", display_name="Bob")
    w = world_service.create_world(db, u1, multi, chat_id=-100123)
    p1 = world_service.join_world(db, w, u1)
    p2 = world_service.join_world(db, w, u2)
    assert p1 and p2, "多人加入失败"
    assert p1.character_name != p2.character_name, "角色卡应不重复"
    world_service.start_world(db, w)
    assert w.status == "running"

    # 行动点限制
    for i in range(3):
        ok, msg = await world_service.record_action(db, w, p1, f"行动{i}")
        assert ok, msg
    ok, msg = await world_service.record_action(db, w, p1, "第四次应被拒绝")
    assert not ok, "第4次行动应被拒绝"

    # 首次 tick
    res = await run_tick(db, w.id)
    assert res is not None, "tick 无结果"
    w2 = db.get(World, w.id)
    assert w2.day == 2, f"天数应推进到2，实际{w2.day}"
    assert db.query(CanonEvent).filter_by(world_id=w.id).count() >= 1
    scenes = db.query(Scene).filter_by(world_id=w.id, day=1).all()
    assert len(scenes) == 2, f"应有2个场景，实际{len(scenes)}"
    p1 = world_service.get_player(db, w2, u1.id)
    assert p1.private_state["hp"] == 9, f"hp应-1为9，实际{p1.private_state['hp']}"
    assert "半罐饮用水" in p1.private_state["items"], "道具应入库"
    assert res.broadcast and res.scenes[0]["scene"]["narrative"], "广播与叙事非空"
    print("[OK] 多人世界：加入/分配角色/行动点限制/首次tick/状态变更")

    # 连推到底 → 世界完结
    guard = 0
    while True:
        cur = db.get(World, w.id)
        if cur.status == "finished":
            break
        guard += 1
        assert guard < 10, "世界未能完结"
        await run_tick(db, w.id)
    print(f"[OK] 多人世界在 {db.get(World, w.id).day - 1} 天后正常完结，共 {db.query(CanonEvent).filter_by(world_id=w.id).count()} 条世界动态")

    # ---------- 单人世界 ----------
    single = db.query(Script).filter(Script.mode == "single").first()
    u3 = world_service.get_or_create_user(db, 333, username="carol", display_name="Carol")
    ws = world_service.create_world(db, u3, single, chat_id=333)
    ps = world_service.join_world(db, ws, u3)
    assert ps, "单人加入失败"
    assert ps.character_card["id"] == single.content_json["player_cards"][0]["id"], "单人应继承主角卡"
    world_service.start_world(db, ws)
    r2 = await run_tick(db, ws.id)
    assert r2 is not None
    ws2 = db.get(World, ws.id)
    assert ws2.day == 2
    print("[OK] 单人世界：创建/继承主角卡/首次tick")

    # ---------- 剧本 AI 完善 ----------
    content = await script_ai_mod.complete_script("一个末日世界，几个人，有倒计时", "multi")
    from app.engine.script_dsl import validate_script
    assert not validate_script(content), f"AI完善剧本应通过校验: {validate_script(content)}"
    print("[OK] 剧本 AI 完善：草稿 → 完整剧本并通过 DSL 校验")

    print("\n===== SMOKE TEST ALL PASSED =====")


if __name__ == "__main__":
    asyncio.run(main())
