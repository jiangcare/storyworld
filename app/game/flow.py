"""平台无关的游戏流：任何通道的输入都汇到这里处理。"""
from __future__ import annotations

import logging
import time

from ..ai.policy import InputRejected, check_player_input
from ..ai import script_ai
from ..ai.conversation import social_reply
from ..channel.base import Channel
from ..channel.types import Action, ChannelEvent, parse_command
from ..config import settings
from ..db import SessionLocal, get_store
from ..engine import guidance, narrative, world_service
from ..models import Scene, Script, World, WorldPlayer

logger = logging.getLogger(__name__)

HELP_TEXT = """🎮 StoryWorld · AI 互动小说世界

玩法：创建世界 → 继承角色 → 用文字自由行动。即时单人剧本当场执行并自动保存；旧版剧本每天结算。

命令：
/scripts 浏览可用剧本
/create_world 创建世界（单人在私聊，多人请在群里）
/join 加入当前群里的世界
/start_world 房主开始世界（或人数满自动开始）
/act <你的行动> 执行行动（群内也可直接回复机器人消息）
/status 查看角色状态
/log 前情提要
/guide 玩法与可执行选项（不耗时）
/continue 等待5分钟，推进局势（关键节点暂停）
/resume 恢复最近的旅程、查看状态与上一段剧情
/upload <剧本草稿> 上传你自己的剧本，AI 帮你完善后提交审核

提示：多人局建议在私聊里行动（保密），群里会广播世界公开事件。"""


# ---------------- 动作构造（按钮 payload 不透明字符串） ----------------

def act_actions(world_id: int, day: int, suggestions: list) -> list:
    acts = [
        Action(f"▶️ {s}", f"act:{world_id}:{day}:{i}")
        for i, s in enumerate(suggestions[:4])
    ]
    acts.append(Action("✍️ 自由输入", f"freetext:{world_id}:{day}"))
    return acts


def menu_actions() -> list:
    return [
        Action("📜 剧本列表", "menu:scripts"),
        Action("📋 我的状态", "menu:status"),
        Action("📖 前情提要", "menu:log"),
    ]


def script_actions(scripts: list) -> list:
    return [
        Action(f"{'👤' if s.mode == 'single' else '👥'} {s.title}", f"mk:{s.id}")
        for s in scripts[:12]
    ]


# ---------------- 游戏流 ----------------

_flow: "GameFlow | None" = None


def set_flow(flow: "GameFlow") -> None:
    global _flow
    _flow = flow


def get_flow() -> "GameFlow | None":
    return _flow


def _ch(ev: ChannelEvent) -> Channel:
    """事件来源通道优先，其次全局兜底通道。"""
    flow = get_flow()
    return ev.channel or (flow.channel if flow else None)


class GameFlow:
    def __init__(self, channel: Channel | None = None) -> None:
        self.channel = channel  # 兜底通道；事件自带 channel 时优先使用
        set_flow(self)

    # ============ 入口 ============

    async def dispatch(self, ev: ChannelEvent) -> None:
        if ev.payload:
            await self._handle_action(ev, ev.payload)
            return
        cmd, arg = parse_command(ev.text)
        if cmd is not None:
            await self._handle_command(ev, cmd, arg)
            return
        if ev.reply_to_bot or ev.auto_action:
            await self._do_action(ev, ev.text)
            return
        if not ev.is_private:
            return  # 群内非回复的闲聊不监听（隐私）
        # 私聊里的自由文本 = 行动（文本优先交互）
        await self._do_action(ev, ev.text)

    async def _send_play(self, ev, db, world, player, text, *, explain=False):
        if player is None or not narrative.enabled(world.script.content_json):
            await _ch(ev).send(ev.chat_id, text)
            return
        # 最新存档决定可执行选项；读事务不跨本地存储写操作。
        db.refresh(world)
        db.refresh(player)
        db.commit()
        choices, token = await guidance.remember(world, player)
        buttons = [Action(f"{i}. {choice['label']}", f"pick:{world.id}:{token}:{i}")
                   for i, choice in enumerate(choices, 1)]
        await _ch(ev).send(ev.chat_id, text + "\n\n" + guidance.render(world, player, choices, explain=explain), actions=buttons)

    # ============ 命令 ============

    async def _handle_command(self, ev: ChannelEvent, cmd: str, arg: str) -> None:
        db = SessionLocal()
        try:
            if cmd in ("help", "guide"):
                await self._do_action(ev, "怎么玩")
            elif cmd == "start":
                await _ch(ev).send(
                    ev.chat_id, HELP_TEXT, actions=menu_actions()
                )
            elif cmd == "scripts":
                scripts = world_service.list_approved_scripts(db)
                if not scripts:
                    await _ch(ev).send(ev.chat_id, "暂无可用剧本。")
                    return
                lines = [
                    f"{'👤' if s.mode == 'single' else '👥'} {s.title}（{s.genre}，{'即时行动' if narrative.enabled(s.content_json) else '每日结算'}）\n{s.description}"
                    for s in scripts
                ]
                await _ch(ev).send(
                    ev.chat_id,
                    "📜 可用剧本：\n\n" + "\n\n".join(lines) + "\n\n点击下方按钮创建世界：",
                    actions=script_actions(scripts),
                )
            elif cmd == "create_world":
                scripts = world_service.list_approved_scripts(db)
                if not scripts:
                    await _ch(ev).send(ev.chat_id, "暂无可用剧本。")
                    return
                await _ch(ev).send(
                    ev.chat_id,
                    "选择要创建的剧本（👤单人在当前聊天进行，👥多人需要在一个群里创建）：",
                    actions=script_actions(scripts),
                )
            elif cmd == "join":
                await self._cmd_join(ev, db)
            elif cmd == "start_world":
                await self._cmd_start_world(ev, db)
            elif cmd == "act":
                if not arg:
                    await _ch(ev).send(
                        ev.chat_id,
                        "用法：/act <你的行动>\n例如：/act 我悄悄跟着那个黑衣人",
                    )
                    return
                await self._do_action(ev, arg)
            elif cmd == "status":
                await self._cmd_status(ev, db)
            elif cmd == "log":
                await self._cmd_log(ev, db)
            elif cmd in ("continue", "resume"):
                await self._cmd_narrative(ev, db, cmd)
            elif cmd == "upload":
                await self._cmd_upload(ev, db, arg)
            else:
                await _ch(ev).send(ev.chat_id, "我没认出这个命令。可以输入 /guide 看看眼下能做什么，也可以直接用一句话告诉我。")
        finally:
            db.close()

    async def _cmd_join(self, ev: ChannelEvent, db) -> None:
        if ev.is_private:
            await _ch(ev).send(ev.chat_id, "请在群里使用 /join 加入该群的世界。")
            return
        world = db.query(World).filter(
            World.chat_id == ev.chat_id, World.status == "recruiting"
        ).first()
        if world is None:
            await _ch(ev).send(ev.chat_id, "这个群没有招募中的世界。")
            return
        user = world_service.get_or_create_user(
            db, ev.user_id, platform=ev.platform,
            username=ev.username, display_name=ev.display_name,
        )
        player = world_service.join_world(db, world, user)
        if player is None:
            await _ch(ev).send(
                ev.chat_id, "加入失败：可能已加入、人数已满或世界不在招募期。"
            )
            return
        content = world.script.content_json
        max_p = content.get("max_players", 6)
        count = len([p for p in world.players if p.status in ("alive", "spectator")])
        text = (
            f"✅ 已加入【{world.title}】！\n"
            f"你继承的角色：{player.character_name}（{player.character_role}）\n"
            f"角色卡：{player.character_card.get('public_desc', '')}\n"
            f"当前 {count}/{max_p} 人。"
        )
        if count >= max_p:
            world_service.start_world(db, world)
            text += "\n🎉 人数已满，世界开始！请留意每日剧情推送。"
        await _ch(ev).send(ev.chat_id, text)

    async def _cmd_start_world(self, ev: ChannelEvent, db) -> None:
        world = db.query(World).filter(
            World.chat_id == ev.chat_id, World.status == "recruiting"
        ).first()
        if world is None:
            await _ch(ev).send(ev.chat_id, "这个群没有招募中的世界。")
            return
        user = world_service.get_or_create_user(
            db, ev.user_id, platform=ev.platform,
            username=ev.username, display_name=ev.display_name,
        )
        if world.owner_id != user.id:
            await _ch(ev).send(ev.chat_id, "只有房主可以开始世界。")
            return
        world_service.start_world(db, world)
        await _ch(ev).send(ev.chat_id, "🎬 世界开始！每天固定时间推送剧情，用 /act 行动。")

    async def _cmd_status(self, ev: ChannelEvent, db) -> None:
        user = world_service.get_or_create_user(
            db, ev.user_id, platform=ev.platform,
            username=ev.username, display_name=ev.display_name,
        )
        chat_id = None if ev.is_private else ev.chat_id
        world = world_service.get_active_world(db, user.id, chat_id)
        if world is None:
            await _ch(ev).send(ev.chat_id, "你当前没有进行中的世界。")
            return
        player = world_service.get_player(db, world, user.id)
        if player is None:
            await _ch(ev).send(ev.chat_id, "你不是这个世界的玩家。")
            return
        text = world_service.build_player_status_message(
            player, world, world.script.content_json
        )
        await self._send_play(ev, db, world, player, text)

    async def _cmd_log(self, ev: ChannelEvent, db) -> None:
        user = world_service.get_or_create_user(
            db, ev.user_id, platform=ev.platform,
            username=ev.username, display_name=ev.display_name,
        )
        chat_id = None if ev.is_private else ev.chat_id
        world = world_service.get_active_world(db, user.id, chat_id)
        if world is None:
            await _ch(ev).send(ev.chat_id, "你当前没有进行中的世界。")
            return
        canon = world_service.build_canon_summary(db, world)
        player = world_service.get_player(db, world, user.id)
        mine = ""
        if player:
            mine = world_service.build_player_recent(db, world, player, limit=3)
        parts = [
            f"📖【{world.title}】前情提要",
            "—— 世界公开事件 ——",
            canon or "（暂无）",
        ]
        if mine:
            parts += ["—— 你的经历 ——", mine]
        await _ch(ev).send(ev.chat_id, "\n".join(parts))

    async def _cmd_narrative(self, ev: ChannelEvent, db, cmd: str) -> None:
        from ..engine.script_upgrades import upgrade_lighthouse
        upgrade_lighthouse(db)
        user = world_service.get_or_create_user(
            db, ev.user_id, platform=ev.platform,
            username=ev.username, display_name=ev.display_name,
        )
        query = db.query(World).join(WorldPlayer).filter(
            WorldPlayer.user_id == user.id,
            World.status.in_(("running", "finished")),
        )
        if not ev.is_private:
            query = query.filter(World.chat_id == ev.chat_id)
        world = query.order_by(World.id.desc()).first()
        if world is None:
            await _ch(ev).send(ev.chat_id, "暂无存档，用 /scripts 开始旅程。")
            return
        player = world_service.get_player(db, world, user.id)
        if cmd == "resume":
            text = world_service.build_player_status_message(player, world, world.script.content_json)
            recent = world_service.build_player_recent(db, world, player, limit=1)
            if not recent and narrative.enabled(world.script.content_json):
                recent = world.script.content_json["narrative"]["opening"]
            await self._send_play(ev, db, world, player, recent + "\n\n" + text)
        elif narrative.enabled(world.script.content_json):
            _, text = await narrative.take_turn(db, world, player, "继续观察", advance=True)
            await self._send_play(ev, db, world, player, text)
        else:
            await _ch(ev).send(ev.chat_id, "这个剧本按每日节奏推进。可用 /log 重读剧情，或用 /scripts 选择即时单人体验。")

    async def _cmd_upload(self, ev: ChannelEvent, db, draft: str) -> None:
        if not draft:
            await _ch(ev).send(
                ev.chat_id,
                "用法：/upload <你的剧本草稿>\n\n草稿可以只有世界观、几个角色或一个点子，AI 会帮你完善成完整剧本并提交审核。\n\n示例：/upload 一个末日世界，4个幸存者，有倒计时，第七天有救赎机会",
            )
            return
        try:
            check_player_input(draft, max_length=3000)
        except InputRejected as exc:
            await _ch(ev).send(ev.chat_id, str(exc))
            return
        key = f"draft:{ev.platform}:{ev.user_id}:{int(time.time())}"
        r = await get_store()
        await r.set(key, draft, ex=1800)
        await _ch(ev).send(
            ev.chat_id,
            "已收到草稿 ✅ 请选择剧本类型，AI 将开始完善：",
            actions=[
                Action("👤 单人剧本", f"uplmode:single:{key}"),
                Action("👥 多人剧本", f"uplmode:multi:{key}"),
            ],
        )

    # ============ 行动 ============

    async def _do_action(self, ev: ChannelEvent, text: str) -> None:
        db = SessionLocal()
        try:
            user = world_service.get_or_create_user(
                db, ev.user_id, platform=ev.platform,
                username=ev.username, display_name=ev.display_name,
            )
            chat_id = None if ev.is_private else ev.chat_id
            world = world_service.get_active_world(db, user.id, chat_id)
            if world is None:
                await _ch(ev).send(
                    ev.chat_id,
                    HELP_TEXT if guidance.is_help(text) else (social_reply(text) or "我在呢。")
                    + "\n这里暂时没有正在进行的故事。用 /scripts 挑一个剧本，或 /resume 回顾上次的旅程吧。",
                )
                return
            player = world_service.get_player(db, world, user.id)
            if player is None:
                await _ch(ev).send(ev.chat_id, "你不是这个世界的玩家。")
                return
            ok, msg = await world_service.record_action(db, world, player, text)
            if social_reply(text):
                await _ch(ev).send(ev.chat_id, msg)
            else:
                await self._send_play(ev, db, world, player, msg, explain=guidance.is_help(text))
        finally:
            db.close()

    # ============ 按钮动作 ============

    async def _handle_action(self, ev: ChannelEvent, payload: str) -> None:
        if payload.startswith("mk:"):
            await self._act_create_world(ev, payload[3:])
        elif payload.startswith("pick:"):
            await self._act_pick(ev, payload[5:])
        elif payload.startswith("act:"):
            await self._act_suggested(ev, payload[4:])
        elif payload.startswith("freetext:"):
            await _ch(ev).ack(
                ev,
                "直接用文字告诉我你想做什么，比如：'我去检查那扇锁着的门'",
                alert=True,
            )
        elif payload.startswith("uplmode:"):
            await self._act_upload_mode(ev, payload[8:])
        elif payload.startswith("menu:"):
            await self._act_menu(ev, payload[5:])
        else:
            await _ch(ev).ack(ev, "未知操作。", alert=True)

    async def _act_create_world(self, ev: ChannelEvent, script_id: str) -> None:
        db = SessionLocal()
        try:
            from ..engine.script_upgrades import upgrade_lighthouse
            upgrade_lighthouse(db)
            script = db.get(Script, int(script_id))
            if script is None or script.status != "approved":
                await _ch(ev).ack(ev, "剧本不存在或已下架。", alert=True)
                return
            user = world_service.get_or_create_user(
                db, ev.user_id, platform=ev.platform,
                username=ev.username, display_name=ev.display_name,
            )
            if script.mode == "multi" and ev.is_private:
                await _ch(ev).ack(ev, "多人剧本需要在一个群里创建。", alert=True)
                return
            if script.mode == "single" and not ev.is_private:
                await _ch(ev).ack(ev, "单人剧本请在私聊中创建。", alert=True)
                return

            world = world_service.create_world(
                db, user, script, chat_id=ev.chat_id
            )
            world_service.join_world(db, world, user)
            if script.mode == "single":
                world_service.start_world(db, world)
                await _ch(ev).ack(ev, "世界已创建！")
                if narrative.enabled(script.content_json):
                    await self._send_play(
                        ev, db, world, world.players[0],
                        f"🌍 单人世界【{world.title}】已开始！\n"
                        f"你是{world.players[0].character_name}。\n\n"
                        + script.content_json["narrative"]["opening"]
                        + "\n\n每次行动自动保存；/status 查看角色，/resume 恢复旅程。",
                        explain=True,
                    )
                    return
                await _ch(ev).send(
                    ev.chat_id,
                    f"🌍 单人世界【{world.title}】已创建并开始！\n"
                    f"你继承了角色：{world.players[0].character_name}（{world.players[0].character_role}）\n"
                    f"⏰ 每天 {world.push_hour:02d}:{world.push_minute:02d} 推送新剧情，用 /act 或直接输入文字行动。\n"
                    f"先用 /status 看看你的角色吧。",
                )
            else:
                await _ch(ev).ack(ev, "世界已创建！")
                await _ch(ev).send(
                    ev.chat_id,
                    f"🌍 多人世界【{world.title}】已创建（招募中）！\n"
                    f"你已加入并继承角色：{world.players[0].character_name}（{world.players[0].character_role}）\n"
                    f"让朋友们发 /join 加入（{script.max_players}人上限），满员自动开始，或输入 /start_world 立即开始。\n"
                    f"开始后每天 {world.push_hour:02d}:{world.push_minute:02d} 推送世界剧情。",
                )
        finally:
            db.close()

    async def _act_pick(self, ev, data):
        try:
            world_id, token, index = data.split(":")
            world_id, index = int(world_id), int(index)
        except ValueError:
            await _ch(ev).ack(ev, "选项无效。", alert=True)
            return
        db = SessionLocal()
        try:
            user = world_service.get_or_create_user(db, ev.user_id, platform=ev.platform)
            world = db.get(World, world_id)
            player = world_service.get_player(db, world, user.id) if world else None
            if (not world or not player or not narrative.enabled(world.script.content_json)
                    or world.status != "running" or player.status != "alive"):
                await _ch(ev).ack(ev, "这个选项不可用，请用 /resume 查看你的旅程。", alert=True)
                return
            await _ch(ev).ack(ev, "正在处理选择……")
            _, text = await narrative.take_turn(db, world, player, str(index), choice_token=token)
            await self._send_play(ev, db, world, player, text)
        finally:
            db.close()

    async def _act_suggested(self, ev: ChannelEvent, data: str) -> None:
        try:
            world_id, day, idx = (int(x) for x in data.split(":"))
        except ValueError:
            await _ch(ev).ack(ev, "操作无效。", alert=True)
            return
        db = SessionLocal()
        try:
            world = db.get(World, world_id)
            if world is None or world.status != "running":
                await _ch(ev).ack(ev, "这个世界已结束。", alert=True)
                return
            if world.day != day:
                await _ch(ev).ack(ev, "这个场景已过期，等新一天的剧情吧。", alert=True)
                return
            user = world_service.get_or_create_user(
                db, ev.user_id, platform=ev.platform,
                username=ev.username, display_name=ev.display_name,
            )
            scene = db.query(Scene).filter(
                Scene.world_id == world_id,
                Scene.day == day,
                Scene.user_id == user.id,
            ).first()
            if scene is None or idx < 0 or idx >= len(scene.suggested_actions):
                await _ch(ev).ack(ev, "找不到对应行动。")
                return
            player = world_service.get_player(db, world, user.id)
            if player is None:
                await _ch(ev).ack(ev, "你不是这个世界的玩家。")
                return
            text = scene.suggested_actions[idx]
            ok, msg = await world_service.record_action(db, world, player, text)
            await _ch(ev).ack(ev, msg, alert=not ok)
        finally:
            db.close()

    async def _act_upload_mode(self, ev: ChannelEvent, data: str) -> None:
        try:
            mode, key = data.split(":", 1)
        except ValueError:
            await _ch(ev).ack(ev, "操作无效。", alert=True)
            return
        if mode not in ("single", "multi") or not key.startswith(f"draft:{ev.platform}:{ev.user_id}:"):
            await _ch(ev).ack(ev, "操作无效。", alert=True)
            return
        r = await get_store()
        draft = await r.get(key)
        if not draft:
            await _ch(ev).ack(ev, "草稿已过期，请重新 /upload。", alert=True)
            return
        await _ch(ev).ack(ev, "AI 正在完善剧本，可能需要 1-2 分钟……")
        try:
            content = await script_ai.complete_script(draft, mode)
        except Exception as e:  # noqa: BLE001
            logger.error("剧本完善失败: %s", e)
            await _ch(ev).send(
                ev.chat_id,
                "❌ AI 完善失败（可能是模型服务暂时不可用），请稍后重试。",
            )
            return
        await r.delete(key)

        db = SessionLocal()
        try:
            user = world_service.get_or_create_user(
                db, ev.user_id, platform=ev.platform,
                username=ev.username, display_name=ev.display_name,
            )
            db.add(
                Script(
                    title=content.get("title", "未命名剧本"),
                    description=content.get("description", ""),
                    genre=content.get("genre", ""),
                    mode=content.get("mode", mode),
                    min_players=content.get("min_players", 1),
                    max_players=content.get("max_players", 6),
                    days=content.get("days", 7),
                    status="pending",
                    source="user",
                    author_id=user.id,
                    raw_draft=draft,
                    content_json=content,
                )
            )
            db.commit()
        finally:
            db.close()

        await _ch(ev).send(
            ev.chat_id,
            f"✅ AI 已把你的草稿完善成完整剧本：\n\n"
            f"📜 《{content.get('title', '未命名剧本')}》\n"
            f"题材：{content.get('genre', '未知')} ｜ {content.get('days', 7)} 天\n"
            f"简介：{content.get('description', '')}\n\n"
            f"已提交审核，管理员在后台审核通过后即可上架游玩。",
        )

    async def _act_menu(self, ev: ChannelEvent, section: str) -> None:
        db = SessionLocal()
        try:
            if section == "scripts":
                scripts = world_service.list_approved_scripts(db)
                if not scripts:
                    await _ch(ev).ack(ev, "暂无可用剧本。")
                    return
                lines = [
                    f"{'👤' if s.mode == 'single' else '👥'} {s.title}（{s.genre}，{'即时行动' if narrative.enabled(s.content_json) else '每日结算'}）"
                    for s in scripts
                ]
                await _ch(ev).send(
                    ev.chat_id,
                    "📜 剧本列表：\n" + "\n".join(lines) + "\n\n点击创建：",
                    actions=script_actions(scripts),
                )
            elif section == "status":
                user = world_service.get_or_create_user(
                    db, ev.user_id, platform=ev.platform,
                    username=ev.username, display_name=ev.display_name,
                )
                world = world_service.get_active_world(db, user.id)
                if world is None:
                    await _ch(ev).ack(ev, "你当前没有进行中的世界。")
                    return
                player = world_service.get_player(db, world, user.id)
                text = (
                    world_service.build_player_status_message(
                        player, world, world.script.content_json
                    )
                    if player
                    else "你不是这个世界的玩家。"
                )
                await self._send_play(ev, db, world, player, text)
            elif section == "log":
                user = world_service.get_or_create_user(
                    db, ev.user_id, platform=ev.platform,
                    username=ev.username, display_name=ev.display_name,
                )
                world = world_service.get_active_world(db, user.id)
                if world is None:
                    await _ch(ev).ack(ev, "你当前没有进行中的世界。")
                    return
                canon = world_service.build_canon_summary(db, world)
                player = world_service.get_player(db, world, user.id)
                mine = (
                    world_service.build_player_recent(db, world, player, limit=3)
                    if player
                    else ""
                )
                parts = [
                    f"📖【{world.title}】前情提要",
                    "—— 世界公开事件 ——",
                    canon or "（暂无）",
                ]
                if mine:
                    parts += ["—— 你的经历 ——", mine]
                await _ch(ev).send(ev.chat_id, "\n".join(parts))
        finally:
            db.close()
        await _ch(ev).ack(ev)
