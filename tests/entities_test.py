"""实体数据层测试：装备/能力/任务/flag 落库 + 上限 + 结构化提交（SQLite + FakeRedis）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis
import app.db as dbmod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

dbmod.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
dbmod.SessionLocal = sessionmaker(bind=dbmod.engine, autoflush=False, expire_on_commit=False)
dbmod._redis = fakeredis.FakeAsyncRedis(decode_responses=True)


def main():
    from app.db import SessionLocal, init_db
    from app.engine import entities, world_service
    from app.models import DynamicTask, PlayerAbility, PlayerItem, Script, World, WorldPlayer

    init_db()
    from seed import seed as run_seed

    run_seed()
    db = SessionLocal()

    multi = db.query(Script).filter(Script.mode == "multi").first()
    content = multi.content_json
    u1 = world_service.get_or_create_user(db, 8101, username="e1")
    u2 = world_service.get_or_create_user(db, 8102, username="e2")
    w = world_service.create_world(db, u1, multi, chat_id=-801)
    world_service.join_world(db, w, u1)
    world_service.join_world(db, w, u2)
    world_service.start_world(db, w)
    wp = world_service.get_player(db, w, u1.id)

    # ---- 物品：模板授予 / 即兴 / 数量 / 移除 ----
    it = entities.grant_item(db, wp, content, "iron_sword")
    assert it is not None and it.def_id == "iron_sword" and it.name == "铁质砍刀"
    ad = entities.grant_item(db, wp, content, {"name": "神秘怀表", "kind": "quest"})
    assert ad is not None and ad.def_id is None and ad.name == "神秘怀表"
    assert "铁质砍刀" in (wp.private_state or {}).get("items", []), "缓存同步"
    assert entities.remove_item(db, wp, "iron_sword") == 1
    rows = entities.list_items(db, wp)
    assert len(rows) == 1 and rows[0]["name"] == "神秘怀表"

    # ---- 能力：授予/升级 ----
    ab = entities.grant_ability(db, wp, content, "night_vision")
    assert ab is not None and ab.name == "夜视" and ab.level == 1
    ab2 = entities.grant_ability(db, wp, content, "夜视")
    assert ab2.level == 2, "同名升级"
    assert len(entities.list_abilities(db, wp)) == 1

    # ---- 任务：个人任务先做（世界任务上限会挡住后续创建） ----
    tp = entities.create_task(db, w, wp, kind="side", title="收集物资", source="preset")
    assert tp is not None
    assert entities.complete_task_by_title(db, w.id, wp, "收集物资") is True
    assert entities.complete_task_by_title(db, w.id, wp, "收集物资") is False, "已完成不可重复完成"

    # ---- 任务：世界级 + 上限 ----
    t1 = entities.create_task(db, w, None, kind="side", title="清剿街角尸群", source="preset")
    assert t1 is not None
    created = 1
    for i in range(entities.MAX_TASKS_ACTIVE_WORLD + 2):
        if entities.create_task(db, w, None, kind="generated", title=f"AI任务{i}", source="ai"):
            created += 1
    assert created <= entities.MAX_TASKS_ACTIVE_WORLD, "世界级任务上限生效"
    active = [t for t in entities.list_active_tasks(db, w, None)]
    assert len(active) <= entities.MAX_TASKS_ACTIVE_WORLD

    # ---- flag / 主线 ----
    entities.set_flag(w, "bridge_destroyed")
    assert entities.has_flag(w, "bridge_destroyed") is True
    assert entities.has_flag(w, "no") is False
    entities.inc_counter(w, "kills", 3)
    assert (w.progress_json or {}).get("counters", {}).get("kills") == 3
    beat = entities.advance_mainline(w, content)
    assert beat is not None and beat.get("day") == 1
    entities.set_flag(w, beat["flag"])
    assert entities.has_flag(w, "heard_signal") is True

    # ---- 结构化提交（模拟编剧输出） ----
    wp2 = world_service.get_player(db, w, u2.id)
    changes = {
        "items_added": ["bandage", {"name": "锈蚀手枪", "kind": "equip", "slot": "weapon"}],
        "abilities_added": ["field_medic"],
        "flag_set": {"raid_survived": True},
        "tasks_done": ["清剿街角尸群"],
        "hp_delta": -2,
        "clues_added": ["夜袭者留下的弹壳"],
    }
    from app.engine import world_service as ws2

    ws2.apply_state_changes(wp2, changes)
    entities.apply_structured_commit(db, w, wp2, content, changes)
    db.flush()
    items2 = entities.list_items(db, wp2)
    names2 = {i["name"] for i in items2}
    assert names2 == {"绷带", "锈蚀手枪"}, names2
    assert any(a["name"] == "战地急救" for a in entities.list_abilities(db, wp2))
    assert entities.has_flag(w, "raid_survived") is True
    assert all(t["title"] != "清剿街角尸群" for t in entities.list_active_tasks(db, w, None)), "完成任务应离开活动列表"

    # ---- 快照注入文本 ----
    snap = entities.snapshot_for_prompt(db, w, wp2, content)
    assert "锈蚀手枪" in snap and "战地急救" in snap

    # ---- 数据上限防护（物品） ----
    wp3 = world_service.get_player(db, w, u2.id) if False else wp
    assert len(entities.list_items(db, wp3)) < entities.MAX_ITEMS_PER_PLAYER
    granted = 0
    for i in range(entities.MAX_ITEMS_PER_PLAYER + 5):
        r = entities.grant_item(db, wp3, content, {"name": f"塞满背包{i}", "kind": "misc"})
        if r:
            granted += 1
    assert granted <= entities.MAX_ITEMS_PER_PLAYER, "物品上限防护生效"
    assert len(entities.list_items(db, wp3)) == entities.MAX_ITEMS_PER_PLAYER

    print("ENTITIES TEST ALL PASSED (物品/能力/任务/flag/结构化提交/上限防护 OK)")


if __name__ == "__main__":
    main()
