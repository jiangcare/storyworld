"""每日剧情推进（tick）。"""
from __future__ import annotations

import copy
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import director as director_ai
from ..ai import writer as writer_ai
from ..db import begin_write, get_store
from ..models import CanonEvent, PlayerAction, RuntimeEntry, Scene, World, WorldPlayer
from . import entities, narrative, script_dsl, world_service

logger = logging.getLogger(__name__)

FALLBACK_SCENE = {
    "narrative": "你度过了相对平静的一天。世界仍在运转，而你必须做出自己的选择。",
    "suggested_actions": ["继续观察周围", "检查随身物品", "休息保存体力"],
    "state_changes": {
        "hp_delta": 0, "items_added": [], "items_removed": [], "clues_added": [],
        "abilities_added": [], "tasks_done": [], "flag_set": {}, "notes": {},
    },
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
    r = await get_store()
    lock_key = f"lock:tick:{world_id}"
    token = secrets.token_hex(16)
    db.rollback()
    locked = await r.set(lock_key, token, nx=True, ex=600)
    if not locked:
        logger.info("世界 %s 的 tick 正在执行或被锁定，跳过", world_id)
        return None
    try:
        return await _run_tick_locked(db, world_id, token)
    finally:
        db.rollback()
        await r.compare_delete(lock_key, token)


async def _run_tick_locked(db: Session, world_id: int, lease_token=None) -> TickResult | None:
    world = db.get(World, world_id)
    if world is None or world.status != "running":
        return None
    content = copy.deepcopy(world.script.content_json)
    if narrative.enabled(content):
        return None
    day = world.day
    total_days = int(content.get("days", 7) or 7)
    actions = list(db.scalars(select(PlayerAction).where(
        PlayerAction.world_id == world_id, PlayerAction.day == day,
    ).order_by(PlayerAction.id)))
    action_inputs = [{"id": a.id, "user_id": a.user_id, "text": a.text,
                      "intent": copy.deepcopy(a.intent)} for a in actions]
    chapter = script_dsl.chapter_for_day(content, day)
    events = script_dsl.events_for_day(content, day)
    w = content.get("world", {})
    recent_canon = world_service.build_canon_summary(db, world)
    players_status = world_service.build_players_status(db, world)
    actions_summary = world_service.build_actions_summary(db, world, day)
    world_progress = copy.deepcopy(world.progress_json)
    player_inputs = []
    for player in world.players:
        if player.status != "alive":
            continue
        player_inputs.append({
            "id": player.id, "user_id": player.user_id,
            "card": copy.deepcopy(player.character_card),
            "state": copy.deepcopy(player.private_state),
            "state_text": world_service.build_player_state_text(player),
            "recent": world_service.build_player_recent(db, world, player),
            "data": entities.snapshot_for_prompt(db, world, player, content),
        })
    # 所有 AI 输入先拍快照；生成全部玩家场景时不持有数据库写事务。
    db.rollback()
    for action in action_inputs:
        if action["intent"] is None:
            action["intent"] = await world_service.parse_action_intent(action["text"])
    try:
        wu = await director_ai.generate_world_update(
            script=content, day=day, total_days=total_days,
            countdown=w.get("countdown", ""), countdown_total=int(w.get("countdown_total", 0) or 0),
            recent_canon=recent_canon, chapter_events=script_dsl.format_events_for_prompt(events),
            players_status=players_status, actions_summary=actions_summary,
        )
    except Exception as exc:
        raise TickError(f"导演生成失败: {exc}") from exc

    generated = []
    for player in player_inputs:
        try:
            scene = await writer_ai.generate_scene(
                script=content, character_card=player["card"], day=day, total_days=total_days,
                world_broadcast=wu["public_broadcast"], chapter_goal=(chapter or {}).get("goal", ""),
                player_private_state=player["state_text"], player_recent_history=player["recent"],
                player_today_actions="；".join(a["text"] for a in action_inputs if a["user_id"] == player["user_id"]),
                player_data=player["data"],
            )
        except Exception:
            logger.exception("玩家 %s 第 %s 天场景生成失败", player["user_id"], day)
            scene = copy.deepcopy(FALLBACK_SCENE)
            scene["narrative"] = f"【第{day}天】{scene['narrative']}"
        generated.append((player, scene))

    try:
        begin_write(db)
        db.refresh(world)
        if world.status != "running" or world.day != day or world.progress_json != world_progress or world.script.content_json != content:
            db.rollback()
            return None
        if lease_token is not None:
            lease = db.get(RuntimeEntry, f"lock:tick:{world_id}")
            if not lease or lease.value != lease_token or lease.expires_at <= time.time():
                db.rollback()
                return None  # 已失去租约的旧任务不得提交，也不得删除新任务的锁。
        current_players = list(db.scalars(select(WorldPlayer).where(
            WorldPlayer.world_id == world_id, WorldPlayer.status == "alive",
        ).execution_options(populate_existing=True)))
        by_id = {p.id: p for p in current_players}
        if set(by_id) != {p["id"] for p in player_inputs} or any(
            by_id[p["id"]].private_state != p["state"] or by_id[p["id"]].character_card != p["card"]
            for p in player_inputs
        ):
            db.rollback()
            return None
        current_actions = list(db.scalars(select(PlayerAction).where(
            PlayerAction.world_id == world_id, PlayerAction.day == day,
        ).order_by(PlayerAction.id)))
        if [(a.id, a.text) for a in current_actions] != [(a["id"], a["text"]) for a in action_inputs]:
            db.rollback()
            return None
        for row, data in zip(current_actions, action_inputs):
            row.intent = data["intent"]
        result = TickResult(world=world, day=day, broadcast=wu["public_broadcast"],
                            countdown_update=wu.get("countdown_update", ""),
                            chapter_goal=(chapter or {}).get("goal", ""),
                            world_ended=bool(wu.get("world_ended", False)))
        db.add(CanonEvent(world_id=world_id, day=day, public=True, content=result.broadcast))
        for data, scene in generated:
            player = by_id[data["id"]]
            world_service.apply_state_changes(player, scene["state_changes"])
            entities.apply_structured_commit(db, world, player, content, scene["state_changes"])
            db.add(Scene(world_id=world_id, user_id=player.user_id, day=day,
                         narrative=scene["narrative"], suggested_actions=scene["suggested_actions"]))
            result.scenes.append({"player": player, "scene": scene})
        world.day = day + 1
        world.last_tick_at = datetime.now()
        if world.day > total_days or result.world_ended:
            world.status = "finished"
            world.finished_at = datetime.now()
            result.world_finished = True
        db.commit()
        logger.info("世界 %s 第 %s 天推进完成", world_id, day)
        return result
    except Exception as exc:
        db.rollback()
        raise TickError(f"世界状态提交失败: {exc}") from exc
