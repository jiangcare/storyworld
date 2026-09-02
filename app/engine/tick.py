"""每日剧情推进（tick）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import director as director_ai
from ..ai import writer as writer_ai
from ..db import get_redis
from ..models import CanonEvent, PlayerAction, Scene, World, WorldPlayer
from . import script_dsl, world_service

logger = logging.getLogger(__name__)

FALLBACK_SCENE = {
    "narrative": "你度过了相对平静的一天。世界仍在运转，而你必须做出自己的选择。",
    "suggested_actions": ["继续观察周围", "检查随身物品", "休息保存体力"],
    "state_changes": {"hp_delta": 0, "items_added": [], "items_removed": [], "clues_added": [], "notes": {}},
    "scene_ended": True,
}


class TickError(Exception):
    pass


@dataclass
class TickResult:
    world: World
    day: int
    broadcast: str
    countdown_update: str
    chapter_goal: str
    scenes: list[dict] = field(default_factory=list)  # [{player, scene}]
    world_ended: bool = False
    world_finished: bool = False


async def run_tick(db: Session, world_id: int) -> TickResult | None:
    """推进一个世界一天。返回 None 表示本次无推进。"""
    r = await get_redis()
    lock_key = f"lock:tick:{world_id}"
    locked = await r.set(lock_key, "1", nx=True, ex=600)
    if not locked:
        logger.info("世界 %s 的 tick 正在执行或被锁定，跳过", world_id)
        return None
    try:
        return await _run_tick_locked(db, world_id)
    finally:
        await r.delete(lock_key)


async def _run_tick_locked(db: Session, world_id: int) -> TickResult | None:
    world = db.get(World, world_id)
    if world is None or world.status != "running":
        return None

    content = world.script.content_json
    day = world.day
    total_days = int(content.get("days", 7) or 7)

    # ---- 1. 解析玩家当天行动的意图 ----
    actions = list(
        db.scalars(
            select(PlayerAction)
            .where(PlayerAction.world_id == world_id, PlayerAction.day == day)
            .order_by(PlayerAction.id)
        )
    )
    for act in actions:
        if act.intent is None:
            act.intent = await world_service.parse_action_intent(act.text)
    if actions:
        db.commit()

    # ---- 2. 汇总导演输入 ----
    chapter = script_dsl.chapter_for_day(content, day)
    events = script_dsl.events_for_day(content, day)
    w = content.get("world", {})
    recent_canon = world_service.build_canon_summary(db, world)
    players_status = world_service.build_players_status(db, world)
    actions_summary = world_service.build_actions_summary(db, world, day)

    # ---- 3. 导演：生成世界事件 ----
    try:
        wu = await director_ai.generate_world_update(
            script=content,
            day=day,
            total_days=total_days,
            countdown=w.get("countdown", ""),
            countdown_total=int(w.get("countdown_total", 0) or 0),
            recent_canon=recent_canon,
            chapter_events=script_dsl.format_events_for_prompt(events),
            players_status=players_status,
            actions_summary=actions_summary,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("世界 %s 第 %s 天导演生成失败: %s", world_id, day, e)
        raise TickError(f"导演生成失败: {e}") from e

    broadcast = wu["public_broadcast"]
    db.add(
        CanonEvent(
            world_id=world_id,
            day=day,
            public=True,
            content=broadcast,
        )
    )

    # ---- 4. 编剧：每个存活玩家生成个人场景 ----
    alive = [p for p in world.players if p.status == "alive"]
    result = TickResult(
        world=world,
        day=day,
        broadcast=broadcast,
        countdown_update=wu.get("countdown_update", ""),
        chapter_goal=(chapter or {}).get("goal", ""),
        world_ended=bool(wu.get("world_ended", False)),
    )

    for player in alive:
        player_actions = "；".join(
            f"{a.text}" for a in actions if a.user_id == player.user_id
        )
        try:
            scene = await writer_ai.generate_scene(
                script=content,
                character_card=player.character_card,
                day=day,
                total_days=total_days,
                world_broadcast=broadcast,
                chapter_goal=(chapter or {}).get("goal", ""),
                player_private_state=world_service.build_player_state_text(player),
                player_recent_history=world_service.build_player_recent(db, world, player),
                player_today_actions=player_actions,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("玩家 %s 第 %s 天场景生成失败: %s", player.user_id, day, e)
            scene = dict(FALLBACK_SCENE)
            scene["narrative"] = f"【第{day}天】{scene['narrative']}"

        world_service.apply_state_changes(player, scene["state_changes"])
        db.add(
            Scene(
                world_id=world_id,
                user_id=player.user_id,
                day=day,
                narrative=scene["narrative"],
                suggested_actions=scene["suggested_actions"],
            )
        )
        result.scenes.append({"player": player, "scene": scene})

    # ---- 5. 推进天数 ----
    world.day = day + 1
    world.last_tick_at = datetime.now()
    if world.day > total_days or result.world_ended:
        world.status = "finished"
        world.finished_at = datetime.now()
        result.world_finished = True

    db.commit()
    logger.info("世界 %s 第 %s 天推进完成", world_id, day)
    return result
