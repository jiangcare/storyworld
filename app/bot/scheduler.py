"""每日 tick 调度与结果推送（平台无关，经多个 Channel 发送）。"""
import logging
from datetime import datetime
from typing import Sequence

from sqlalchemy import select

from ..channel.base import Channel
from ..config import settings
from ..db import session_scope
from ..engine import tick
from ..game.flow import act_actions
from ..models import User, World

logger = logging.getLogger(__name__)


def _scheduled_dt(world: World) -> datetime:
    now = datetime.now()
    return now.replace(hour=world.push_hour, minute=world.push_minute, second=0, microsecond=0)


def _is_due(world: World) -> bool:
    """今日是否到了推送时间且本日尚未 tick。"""
    now = datetime.now()
    scheduled = _scheduled_dt(world)
    if now < scheduled:
        return False
    if world.last_tick_at is None:
        return True
    return world.last_tick_at < scheduled


async def scan_due_worlds(channels: Sequence[Channel]) -> None:
    """扫描所有运行中的世界，执行到点推进，结果推送到所有通道。"""
    async with session_scope() as db:
        worlds = list(
            db.scalars(select(World).where(World.status == "running").order_by(World.id))
        )
    for world in worlds:
        if not _is_due(world):
            continue
        async with session_scope() as db:
            try:
                result = await tick.run_tick(db, world.id)
            except tick.TickError as e:
                logger.error("世界 %s tick 失败（稍后重试）: %s", world.id, e)
                continue
            if result is None:
                continue
            for channel in channels:
                await push_tick(channel, db, result)


async def push_tick(channel: Channel, db, result: tick.TickResult) -> None:
    """推送世界广播 + 每个玩家的个人场景。"""
    world = result.world

    countdown_line = ""
    if result.countdown_update:
        countdown_line = f"\n⏳ {result.countdown_update}"

    # 单人私聊世界：广播与场景合并，避免重复
    merge_scene = None
    for item in result.scenes:
        user = db.get(User, item["player"].user_id)
        if user and world.chat_id == user.tg_id:
            merge_scene = item
            break

    # 1) 世界广播
    if world.chat_id and merge_scene is None:
        broadcast_text = (
            f"🌍【{world.title}】第 {result.day} 天 · 世界动态\n\n"
            f"{result.broadcast}{countdown_line}"
        )
        if result.world_finished:
            broadcast_text += "\n\n🏁 这个世界的故事已经走到终点。"
        await channel.send(world.chat_id, broadcast_text)

    # 2) 玩家私聊场景
    for item in result.scenes:
        player, scene = item["player"], item["scene"]
        user = db.get(User, player.user_id)
        if user is None or user.platform != channel.capabilities.name:
            continue
        if item is merge_scene:
            header = (
                f"🌍【{world.title}】第 {result.day} 天\n\n"
                f"【世界动态】{result.broadcast}\n{countdown_line}\n\n"
                f"【你的视角】\n{scene['narrative']}"
            )
        else:
            header = f"🌍【{world.title}】第 {result.day} 天 · 你的视角\n\n{scene['narrative']}"

        if result.world_finished:
            text = header + "\n\n🏁 世界完结。感谢你的旅程！"
            await channel.send_private(user.tg_id, text)
        else:
            text = (
                header
                + f"\n\n💡 今天你可以行动 {settings.max_action_points} 次（次日结算）。\n"
                + (f"🎯 章节目标：{result.chapter_goal}\n" if result.chapter_goal else "")
            )
            await channel.send_private(
                user.tg_id,
                text,
                actions=act_actions(world.id, result.day, scene["suggested_actions"]),
            )
