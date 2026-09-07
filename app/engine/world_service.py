"""世界服务：创建世界、加入、行动、状态查询。"""
from __future__ import annotations

import copy
import logging
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai import intent as intent_ai
from ..ai.policy import InputRejected, REFUSAL, check_player_input
from ..config import settings
from ..db import begin_write
from ..models import (
    CanonEvent,
    PlayerAction,
    RuntimeEntry,
    Scene,
    Script,
    User,
    World,
    WorldPlayer,
)
from . import narrative, script_dsl

logger = logging.getLogger(__name__)


# ---------------- 用户 ----------------

def get_or_create_user(
    db: Session, platform_id: int, *, platform: str = "telegram",
    username: str | None = None, display_name: str | None = None,
) -> User:
    user = db.scalar(
        select(User).where(User.platform == platform, User.tg_id == platform_id)
    )
    if user is None:
        user = User(
            platform=platform,
            tg_id=platform_id,
            username=username,
            display_name=display_name or username or str(platform_id),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if (username and user.username != username) or (
            display_name and user.display_name != display_name
        ):
            user.username = username or user.username
            user.display_name = display_name or user.display_name
            db.commit()
    return user


# ---------------- 剧本 ----------------

def list_approved_scripts(db: Session, mode: str | None = None) -> list[Script]:
    q = select(Script).where(Script.status == "approved").order_by(Script.id.desc())
    if mode:
        q = q.where(Script.mode == mode)
    return list(db.scalars(q))


# ---------------- 世界 ----------------

def create_world(db: Session, user: User, script: Script, chat_id: int | None = None) -> World:
    world = World(
        script_id=script.id,
        title=script.title,
        status="recruiting",
        chat_id=chat_id,
        owner_id=user.id,
        push_hour=settings.push_hour,
        push_minute=settings.push_minute,
    )
    db.add(world)
    db.commit()
    db.refresh(world)
    return world


def _next_character_card(content: dict, db: Session, world_id: int) -> dict:
    """挑选第一个未被本世界玩家占用的角色卡。"""
    cards = content.get("player_cards", [])
    taken = set()
    for card in db.scalars(
        select(WorldPlayer.character_card).where(
            WorldPlayer.world_id == world_id,
            WorldPlayer.status.in_(("alive", "spectator")),
        )
    ):
        if isinstance(card, dict) and card.get("id"):
            taken.add(card["id"])
    for card in cards:
        if card.get("id") not in taken:
            return card
    return cards[len(taken) % len(cards)] if cards else {
        "id": "p0",
        "name": "无名者",
        "role": "幸存者",
        "personality": "沉默谨慎",
        "secret": "",
        "goal": "活下去",
        "stats": {"strength": 3, "agility": 3, "intellect": 3, "charm": 3, "luck": 3},
        "public_desc": "一个普通的幸存者。",
    }


def join_world(db: Session, world: World, user: User) -> WorldPlayer | None:
    """加入世界，分配角色。返回 None 表示失败（人数已满/已加入/不在招募期）。"""
    if world.status != "recruiting":
        return None
    existing = db.scalar(
        select(WorldPlayer).where(
            WorldPlayer.world_id == world.id, WorldPlayer.user_id == user.id
        )
    )
    if existing:
        return None
    content = world.script.content_json
    players_now = len(list(db.scalars(select(WorldPlayer).where(WorldPlayer.world_id == world.id))))
    if players_now >= content.get("max_players", 6):
        return None

    card = _next_character_card(content, db, world.id)
    order = max(
        [0]
        + list(
            db.scalars(select(WorldPlayer.join_order).where(WorldPlayer.world_id == world.id))
        )
    ) + 1
    player = WorldPlayer(
        world_id=world.id,
        user_id=user.id,
        character_name=card.get("name", "无名者"),
        character_role=card.get("role", ""),
        character_card=card,
        join_order=order,
        private_state={
            "hp": 10,
            "items": [],
            "clues": [],
            "faction": "",
            "relationships": {},
            "notes": {},
        },
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return player


def start_world(db: Session, world: World) -> World:
    """世界开始运行：单人局在创建后自动开始；多人局满员或房主决定时开始。"""
    if world.status == "recruiting":
        if narrative.enabled(world.script.content_json):
            player = get_player(db, world, world.owner_id)
            if player is None:
                raise ValueError("单人世界需要先创建角色")
            narrative.initialize(world, player, world.script.content_json)
        world.status = "running"
        world.started_at = world.started_at or __import__("datetime").datetime.now()
        db.add(
            CanonEvent(
                world_id=world.id,
                day=1,
                public=True,
                content=f"【世界开启】{world.script.content_json.get('world', {}).get('background', '')}",
            )
        )
        db.commit()
    return world


def get_active_world(db: Session, user_id: int, chat_id: int | None = None) -> World | None:
    """获取用户当前进行中的世界。群聊优先按 chat_id，私聊取最近加入的。"""
    q = (
        select(World)
        .join(WorldPlayer, WorldPlayer.world_id == World.id)
        .where(
            WorldPlayer.user_id == user_id,
            World.status == "running",
            WorldPlayer.status.in_(("alive", "spectator")),
        )
        .order_by(World.id.desc())
    )
    if chat_id is not None:
        q = q.where(World.chat_id == chat_id)
    return db.scalar(q)


def get_player(db: Session, world: World, user_id: int) -> WorldPlayer | None:
    return db.scalar(
        select(WorldPlayer).where(
            WorldPlayer.world_id == world.id, WorldPlayer.user_id == user_id
        )
    )


# ---------------- 行动 ----------------

async def record_action(
    db: Session, world: World, player: WorldPlayer, text: str
) -> tuple[bool, str]:
    """记录玩家当日行动（受行动点限制）。返回 (是否成功, 消息)。"""
    if world.status != "running":
        return False, "当前世界不在进行中。"
    if player.status != "alive":
        return False, "你的角色已死亡，进入观察者模式。"
    try:
        check_player_input(text)
    except InputRejected as exc:
        return False, str(exc)

    if narrative.enabled(world.script.content_json):
        return await narrative.take_turn(db, world, player, text)

    world_id, player_id, expected_day = world.id, player.id, world.day
    # 快速拒绝已无行动点的请求，避免每次无效提交都消耗 AI 调用；提交时仍原子复查。
    used = db.scalar(select(func.count(PlayerAction.id)).where(
        PlayerAction.world_id == world_id, PlayerAction.user_id == player.user_id,
        PlayerAction.day == expected_day,
    )) or 0
    if used >= settings.max_action_points:
        return False, f"今天的行动点已用完（{settings.max_action_points}点）。等明天的新剧情吧。"
    context = {"world": world.script.content_json.get("world", {}),
               "character": player.character_name, "state": copy.deepcopy(player.private_state)}
    db.rollback()
    intent = await parse_action_intent(text, context)
    if intent is None:
        return False, REFUSAL
    try:
        begin_write(db)
        db.refresh(world)
        db.refresh(player)
        if world.status != "running" or world.day != expected_day or player.status != "alive" or player.world_id != world_id:
            db.rollback()
            return False, "世界或角色当前不能行动。"
        lease = db.get(RuntimeEntry, f"lock:tick:{world_id}")
        if lease and lease.expires_at and lease.expires_at > time.time():
            db.rollback()
            return False, "世界正在结算，请等新剧情到来后再行动。"
        used = db.scalar(select(func.count(PlayerAction.id)).where(
            PlayerAction.world_id == world_id, PlayerAction.user_id == player.user_id,
            PlayerAction.day == world.day,
        )) or 0
        if used >= settings.max_action_points:
            db.rollback()
            return False, f"今天的行动点已用完（{settings.max_action_points}点）。等明天的新剧情吧。"
        day = world.day
        db.add(PlayerAction(world_id=world_id, user_id=player.user_id, day=day, text=text, intent=intent))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("记录行动失败 player=%s", player_id)
        return False, "行动未能保存，请稍后重试。"
    used += 1
    left = settings.max_action_points - used
    return True, f"已记录你的行动（第{day}天）。今日剩余行动点：{left}。"


async def parse_action_intent(text: str, context: dict | None = None) -> dict | None:
    """解析失败或范围不明时不产生意图；绝不能把原文作为兜底转交其他模型。"""
    try:
        check_player_input(text)
        return intent_ai.validate_intent(await intent_ai.parse_intent(text, context))
    except Exception as exc:
        logger.warning("游戏意图未获准：%s", type(exc).__name__)
        return None


# ---------------- 状态/摘要 ----------------

def build_players_status(db: Session, world: World) -> str:
    lines = []
    for p in world.players:
        if p.status != "alive":
            continue
        state = p.private_state or {}
        lines.append(
            f"- {p.character_name}（{p.character_role}）：生命{state.get('hp', 10)}，道具[{', '.join(state.get('items', [])) or '无'}]"
        )
    return "\n".join(lines) or "（暂无存活玩家）"


def build_actions_summary(db: Session, world: World, day: int) -> str:
    acts = list(
        db.scalars(
            select(PlayerAction)
            .where(PlayerAction.world_id == world.id, PlayerAction.day == day)
            .order_by(PlayerAction.id)
        )
    )
    if not acts:
        return ""
    lines = []
    for a in acts:
        name = "玩家"
        for p in world.players:
            if p.user_id == a.user_id:
                name = p.character_name
                break
        summary = intent_ai.safe_summary(a.intent)
        if summary:
            lines.append(f"- {name}：{summary}")
    return "\n".join(lines)


def build_canon_summary(db: Session, world: World, limit: int = 12) -> str:
    events = list(
        db.scalars(
            select(CanonEvent)
            .where(CanonEvent.world_id == world.id, CanonEvent.public.is_(True))
            .order_by(CanonEvent.id.desc())
            .limit(limit)
        )
    )
    events.reverse()
    return "\n".join(f"[第{e.day}天] {e.content}" for e in events)


def build_player_recent(db: Session, world: World, player: WorldPlayer, limit: int = 2) -> str:
    if narrative.enabled(world.script.content_json):
        actions = list(db.scalars(select(PlayerAction).where(
            PlayerAction.world_id == world.id, PlayerAction.user_id == player.user_id,
            PlayerAction.outcome.is_not(None),
        ).order_by(PlayerAction.id.desc()).limit(limit)))
        return "\n---\n".join(a.outcome for a in reversed(actions))
    scenes = list(
        db.scalars(
            select(Scene)
            .where(Scene.world_id == world.id, Scene.user_id == player.user_id)
            .order_by(Scene.day.desc())
            .limit(limit)
        )
    )
    scenes.reverse()
    return "\n---\n".join(f"[第{s.day}天] {s.narrative[:200]}" for s in scenes)


def build_player_state_text(player: WorldPlayer) -> str:
    s = player.private_state or {}
    return (
        f"生命: {s.get('hp', 10)} | 道具: {', '.join(s.get('items', [])) or '无'} | "
        f"线索: {', '.join(s.get('clues', [])) or '无'} | 阵营: {s.get('faction') or '无'}"
    )


def apply_state_changes(player: WorldPlayer, changes: dict) -> None:
    """把编剧层的 state_changes 应用到玩家私有状态。"""
    s = copy.deepcopy(player.private_state or {})
    s.setdefault("hp", 10)
    s.setdefault("items", [])
    s.setdefault("clues", [])
    s.setdefault("faction", "")
    s.setdefault("relationships", {})
    s.setdefault("notes", {})

    s["hp"] = int(s.get("hp", 10)) + int(changes.get("hp_delta", 0) or 0)
    if s["hp"] <= 0:
        s["hp"] = 0
        player.status = "dead"
        s["notes"]["死因"] = "生命归零"

    for item in changes.get("items_added", []):
        if item and item not in s["items"]:
            s["items"].append(item)
    for item in changes.get("items_removed", []):
        if item in s["items"]:
            s["items"].remove(item)
    for clue in changes.get("clues_added", []):
        if clue and clue not in s["clues"]:
            s["clues"].append(clue)
    for k, v in (changes.get("notes") or {}).items():
        s["notes"][str(k)] = str(v)

    player.private_state = s


def build_player_status_message(player: WorldPlayer, world: World, content: dict) -> str:
    s = player.private_state or {}
    if narrative.enabled(content):
        spec = narrative.parse_spec(content)
        progress = world.progress_json["narrative"]
        names = {n["id"]: n["name"] for n in content.get("npcs", [])}
        return "\n".join([
            f"🧭 世界：{world.title}",
            f"🧬 角色：{player.character_name}（{player.character_role}）",
            f"📍 {spec.locations[s['location']].name} · 已过 {progress['minute']} 分钟",
            f"❤️ 生命：{s['hp']}/{s['max_hp']} · 等级 {s['level']} · 经验 {s['xp']}",
            "🎒 道具：" + ("、".join(s.get("items", [])) or "无"),
            "🔎 线索：" + ("；".join(s.get("clues", [])) or "无"),
            "🤝 关系：" + ("、".join(f"{names.get(k, k)} {v:+d}" for k, v in s.get("relationships", {}).items()) or "尚未建立"),
            f"🏁 {progress['ending']}" if progress["ending"] else (
                "⏸ 关键事件等待你的决定" if progress["paused"] else "直接描述行动，或 /continue 继续观察"),
            "💾 进度已自动保存，下次使用 /resume 继续。",
        ])
    ch = script_dsl.chapter_for_day(content, world.day)
    lines = [
        f"🧭 世界：{world.title}",
        f"📅 第 {world.day} 天 / 共 {content.get('days', 7)} 天",
    ]
    if ch:
        lines.append(f"🎯 当前章节【{ch.get('title', '')}】：{ch.get('goal', '')}")
    if world.script.content_json.get("world", {}).get("countdown"):
        total = content.get("world", {}).get("countdown_total", 0)
        if total:
            lines.append(f"⏳ {content['world']['countdown']}：剩余 {max(total - world.day, 0)} 天")
    lines += [
        f"🧬 角色：{player.character_name}（{player.character_role}）",
        f"❤️ 生命：{s.get('hp', 10)}",
        f"🎒 道具：{', '.join(s.get('items', [])) or '无'}",
        f"🔎 线索：{', '.join(s.get('clues', [])) or '无'}",
    ]
    if s.get("notes"):
        lines.append(f"📝 记录：{', '.join(f'{k}:{v}' for k, v in s['notes'].items())}")
    return "\n".join(lines)
