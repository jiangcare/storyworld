"""实体服务：运行时数据化存储（装备/能力/任务/主线进度/世界 flag）。

设计：
- 预设模板放剧本 content_json（items/abilities/task_templates/mainline）
- 运行时实例落独立表（player_items/player_abilities/dynamic_tasks + World.progress_json）
- AI 即兴产出（编剧 state_changes / 导演）也走同一入口，受数量上限保护
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import DynamicTask, PlayerAbility, PlayerItem, World, WorldPlayer

logger = logging.getLogger(__name__)

# 数量上限（防 AI 无限造物 / 状态无限膨胀）
MAX_ITEMS_PER_PLAYER = 40
MAX_ABILITIES_PER_PLAYER = 24
MAX_TASKS_ACTIVE_WORLD = 12
MAX_TASKS_ACTIVE_PLAYER = 6
MAX_MAINLINE_BEATS = 200


# ================= 装备/物品 =================

def resolve_item_def(content: dict, key: str) -> Optional[dict]:
    """按 def_id 或名称查剧本 items 模板。"""
    for it in content.get("items") or []:
        if it.get("id") == key or it.get("name") == key:
            return it
    return None


def grant_item(
    db: Session,
    wp: WorldPlayer,
    content: dict,
    spec,
    note: Optional[str] = None,
) -> Optional[PlayerItem]:
    """授予物品。spec: str（模板 id/名称 → 查表，查不到则即兴创建）
    或 dict {def_id|name, qty?, level?, kind?, slot?, extra?}。
    超过上限或已满则跳过并记录。返回实例或 None。
    """
    if isinstance(spec, str):
        key = spec
        qty, level = 1, 0
        extra = {"adhoc": True} if resolve_item_def(content, key) is None else None
    elif isinstance(spec, dict):
        key = str(spec.get("def_id") or spec.get("name") or "")
        qty = int(spec.get("qty", 1) or 1)
        level = int(spec.get("level", 0) or 0)
        extra = spec.get("extra")
    else:
        return None
    if not key:
        return None

    count = db.scalar(
        select(func.count(PlayerItem.id)).where(
            PlayerItem.world_player_id == wp.id
        )
    ) or 0
    if count + 1 > MAX_ITEMS_PER_PLAYER:
        logger.warning("玩家 %s 物品已达上限，跳过授予 %s", wp.id, key)
        return None

    tpl = resolve_item_def(content, key)
    row = PlayerItem(
        world_player_id=wp.id,
        def_id=tpl["id"] if tpl else None,
        name=tpl.get("name", key) if tpl else key,
        kind=(tpl or {}).get("kind", spec.get("kind") if isinstance(spec, dict) else None) or "misc",
        slot=(tpl or {}).get("slot") or (spec.get("slot") if isinstance(spec, dict) else None),
        quantity=max(1, qty),
        level=level,
        extra=extra,
    )
    db.add(row)
    db.flush()
    # 同步缓存（private_state.items 名称列表，兼容旧读取）
    cache = wp.private_state or {}
    names = list(cache.get("items") or [])
    if row.name not in names:
        names.append(row.name)
    cache["items"] = names
    if note:
        cache.setdefault("notes", {})[f"获得:{row.name}"] = note
    wp.private_state = cache
    return row


def remove_item(
    db: Session,
    wp: WorldPlayer,
    key: str,
    qty: int = 1,
) -> int:
    """移除物品（按名称/def_id 匹配行，优先消耗数量）。返回移除数量。"""
    rows = list(
        db.scalars(
            select(PlayerItem).where(
                PlayerItem.world_player_id == wp.id,
                (PlayerItem.def_id == key) | (PlayerItem.name == key),
            )
        )
    )
    removed = 0
    for row in rows:
        if removed >= qty:
            break
        take = min(row.quantity, qty - removed)
        row.quantity -= take
        removed += take
        if row.quantity <= 0:
            db.delete(row)
    if removed:
        cache = wp.private_state or {}
        cache["items"] = list(cache.get("items") or [])
        db.flush()
    return removed


def list_items(db: Session, wp: WorldPlayer) -> list[dict]:
    rows = db.scalars(
        select(PlayerItem)
        .where(PlayerItem.world_player_id == wp.id)
        .order_by(PlayerItem.id)
    )
    return [
        {
            "def_id": r.def_id,
            "name": r.name,
            "kind": r.kind,
            "slot": r.slot,
            "quantity": r.quantity,
            "level": r.level,
            "extra": r.extra,
        }
        for r in rows
    ]


# ================= 能力 =================

def grant_ability(
    db: Session,
    wp: WorldPlayer,
    content: dict,
    key: str,
    level: int = 1,
) -> Optional[PlayerAbility]:
    """授予/升级能力（按名称唯一）。模板查 content.abilities，查不到即兴。"""
    tpl = None
    for ab in content.get("abilities") or []:
        if ab.get("id") == key or ab.get("name") == key:
            tpl = ab
            break
    name = tpl.get("name", key) if tpl else key
    row = db.scalar(
        select(PlayerAbility).where(
            PlayerAbility.world_player_id == wp.id, PlayerAbility.name == name
        )
    )
    if row is not None:
        row.level += 1
        db.flush()
        return row
    count = db.scalar(
        select(func.count(PlayerAbility.id)).where(
            PlayerAbility.world_player_id == wp.id
        )
    ) or 0
    if count >= MAX_ABILITIES_PER_PLAYER:
        logger.warning("玩家 %s 能力已达上限，跳过 %s", wp.id, key)
        return None
    row = PlayerAbility(
        world_player_id=wp.id,
        def_id=tpl["id"] if tpl else None,
        name=name,
        level=level,
        extra=(tpl or {}).get("extra"),
    )
    db.add(row)
    db.flush()
    return row


def list_abilities(db: Session, wp: WorldPlayer) -> list[dict]:
    rows = db.scalars(
        select(PlayerAbility)
        .where(PlayerAbility.world_player_id == wp.id)
        .order_by(PlayerAbility.id)
    )
    return [{"def_id": r.def_id, "name": r.name, "level": r.level} for r in rows]


# ================= 任务 =================

def create_task(
    db: Session,
    world: World,
    wp: Optional[WorldPlayer],
    *,
    kind: str = "side",
    title: str,
    desc: str = "",
    source: str = "preset",
    metric: Optional[str] = None,
    target: int = 1,
) -> Optional[DynamicTask]:
    """创建任务。预设（task_templates）或 AI 动态生成都走这里；有活动上限保护。"""
    active_world = db.scalar(
        select(func.count(DynamicTask.id)).where(
            DynamicTask.world_id == world.id, DynamicTask.status == "active"
        )
    ) or 0
    if active_world >= MAX_TASKS_ACTIVE_WORLD:
        logger.warning("世界 %s 活动任务已达上限", world.id)
        return None
    if wp is not None:
        active_me = db.scalar(
            select(func.count(DynamicTask.id)).where(
                DynamicTask.world_id == world.id,
                DynamicTask.world_player_id == wp.id,
                DynamicTask.status == "active",
            )
        ) or 0
        if active_me >= MAX_TASKS_ACTIVE_PLAYER:
            logger.warning("玩家 %s 活动任务已达上限", wp.id)
            return None
    row = DynamicTask(
        world_id=world.id,
        world_player_id=wp.id if wp else None,
        kind=kind,
        title=title,
        desc=desc,
        status="active",
        source=source,
        progress_json={"current": 0, "target": max(1, target), "metric": metric},
    )
    db.add(row)
    db.flush()
    prog = world.progress_json or {}
    prog.setdefault("spawned", {})["tasks"] = int(prog.get("spawned", {}).get("tasks", 0)) + 1
    world.progress_json = prog
    return row


def complete_task_by_title(db: Session, world_id: int, wp: Optional[WorldPlayer], title: str) -> bool:
    """完成任务：个人任务匹配玩家本人；世界级任务（无属主）任何玩家完成即生效。"""
    from sqlalchemy import or_

    cond = DynamicTask.world_player_id.is_(None)
    if wp is not None:
        cond = or_(cond, DynamicTask.world_player_id == wp.id)
    row = db.scalar(
        select(DynamicTask).where(
            DynamicTask.world_id == world_id,
            cond,
            DynamicTask.title == title,
            DynamicTask.status == "active",
        )
    )
    if row is None:
        return False
    row.status = "done"
    prog = row.progress_json or {}
    prog["current"] = prog.get("target", 1)
    row.progress_json = prog
    db.flush()
    return True


def list_active_tasks(db: Session, world: World, wp: Optional[WorldPlayer]) -> list[dict]:
    q = select(DynamicTask).where(
        DynamicTask.world_id == world.id, DynamicTask.status == "active"
    ).order_by(DynamicTask.id)
    rows = db.scalars(q)
    out = []
    for t in rows:
        if t.world_player_id is None or (wp is not None and t.world_player_id == wp.id):
            out.append(
                {
                    "id": t.id,
                    "kind": t.kind,
                    "title": t.title,
                    "desc": t.desc,
                    "progress": t.progress_json,
                    "source": t.source,
                    "owner": t.world_player_id is not None,
                }
            )
    return out


# ================= 世界进度 / flag =================

def world_progress(world: World) -> dict:
    return world.progress_json or {}


def set_flag(world: World, key: str, value=True) -> None:
    prog = world.progress_json or {}
    prog.setdefault("flags", {})[key] = value
    world.progress_json = prog


def has_flag(world: World, key: str) -> bool:
    return bool(((world.progress_json or {}).get("flags") or {}).get(key))


def inc_counter(world: World, key: str, delta: int = 1) -> int:
    prog = world.progress_json or {}
    counters = prog.setdefault("counters", {})
    v = int(counters.get(key, 0)) + delta
    counters[key] = v
    world.progress_json = prog
    return v


def advance_mainline(world: World, content: dict) -> Optional[dict]:
    """主线节拍推进：返回当前节拍定义（用于导演提示）。"""
    beats = content.get("mainline") or []
    if not beats:
        return None
    prog = world.progress_json or {}
    idx = int(prog.get("mainline", {}).get("beat_index", -1))
    prog.setdefault("mainline", {})["beat_index"] = min(idx + 1, len(beats) - 1)
    world.progress_json = prog
    beat = beats[prog["mainline"]["beat_index"]]
    return beat


def current_beat(world: World, content: dict) -> Optional[dict]:
    beats = content.get("mainline") or []
    if not beats:
        return None
    idx = int(((world.progress_json or {}).get("mainline") or {}).get("beat_index", -1))
    if not (0 <= idx < len(beats)):
        return beats[0]
    return beats[idx]


# ================= 结构化提交（tick 用） =================

def apply_structured_commit(
    db: Session,
    world: World,
    wp: WorldPlayer,
    content: dict,
    changes: dict,
) -> None:
    """把编剧输出的 state_changes 结构化落到数据层（装备/能力/任务/flag）。"""
    for spec in changes.get("items_added", []):
        grant_item(db, wp, content, spec)
    for spec in changes.get("items_removed", []):
        remove_item(db, wp, spec if isinstance(spec, str) else str(spec.get("name") or ""))
    for ab in changes.get("abilities_added", []):
        if isinstance(ab, str):
            grant_ability(db, wp, content, ab)
        elif isinstance(ab, dict):
            grant_ability(db, wp, content, str(ab.get("name") or ab.get("def_id") or ""),
                          level=int(ab.get("level", 1) or 1))
    for title in changes.get("tasks_done", []):
        complete_task_by_title(db, world.id, wp, title)
    flags = changes.get("flag_set") or {}
    if isinstance(flags, dict):
        for k, v in flags.items():
            set_flag(world, k, v)
    db.flush()


def build_ctx_data(
    db: Session, world: World, wp: WorldPlayer, content: dict
) -> dict:
    """为规则引擎构造上下文数据（attr/stat/counts/abilities/consts）。"""
    from ..rules.engine import make_ctx

    card = wp.character_card or {}
    state = wp.private_state or {}
    counts: dict = {}
    for it in list_items(db, wp):
        key = it["def_id"] or it["name"]
        counts[key] = counts.get(key, 0) + it["quantity"]
    abi = {a["name"]: True for a in list_abilities(db, wp)}
    rules = content.get("rules") or {}
    constants = rules.get("constants") or {}
    return make_ctx(
        attr=card.get("stats") or {},
        stat={"hp": int(state.get("hp", 10)), "day": int(world.day)},
        item_count=counts,
        ability=abi,
        constants=constants,
    )


def _fmt_item(i: dict) -> str:
    s = i["name"]
    if i.get("level"):
        s += f"+{i['level']}"
    if i.get("quantity", 1) > 1:
        s += f"x{i['quantity']}"
    return s


def snapshot_for_prompt(
    db: Session, world: World, wp: WorldPlayer, content: dict
) -> str:
    """玩家数据化状态摘要（注入编剧提示词，保证叙事与数据一致）。"""
    state = wp.private_state or {}
    items = list_items(db, wp)
    lines = [
        f"- 状态: hp={state.get('hp', 10)}",
        "- 装备/物品: "
        + ("; ".join(_fmt_item(i) for i in items) if items else "无"),
    ]
    abilities = list_abilities(db, wp)
    if abilities:
        lines.append("- 能力: " + "; ".join(f"{a['name']} Lv{a['level']}" for a in abilities))
    tasks = list_active_tasks(db, world, wp)
    if tasks:
        lines.append("- 任务: " + "; ".join(t["title"] for t in tasks))
    beat = current_beat(world, content)
    if beat:
        lines.append(f"- 主线节点: {beat.get('desc', beat.get('name', ''))}")
    flags = (world.progress_json or {}).get("flags") or {}
    if flags:
        lines.append("- 世界标志: " + ", ".join(k for k, v in flags.items() if v)[:120])
    return "\n".join(lines)
