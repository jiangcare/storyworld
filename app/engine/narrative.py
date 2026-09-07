"""Narrative Transaction: Validate → Calculate → Commit → Narrative.

单人存档保存在现有 World.progress_json / WorldPlayer.private_state 中。
PlayerAction.intent 保存动作与规则回执，outcome 保存可重读的叙述。
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime

from sqlalchemy import select

from ..ai import narrative as ai
from ..ai.policy import InputRejected, check_player_input, normalized
from ..ai.conversation import ConversationReply, fallback_reply, social_reply
from ..db import begin_write
from ..config import settings
from ..models import PlayerAction, World, WorldPlayer
from ..rules import engine as rules
from .narrative_dsl import Condition, Effect, parse_spec
from . import guidance, cultivation, turn_summary

logger = logging.getLogger(__name__)


def enabled(content: dict) -> bool:
    return "narrative" in content


def initialize(world, player, content):
    spec = parse_spec(content)
    world.progress_json = {**(world.progress_json or {}), "narrative": {
        "revision": 0, "minute": 0, "flags": {}, "fired": [], "done": [],
        "paused": None, "ending": "",
    }}
    player.private_state = {**(player.private_state or {}),
        "hp": spec.max_hp, "max_hp": spec.max_hp, "location": spec.start,
        "inventory": dict(spec.inventory), "xp": 0, "level": 1,
        "stats": dict(player.character_card.get("stats", rules.DEFAULT_ATTRS)),
    }
    if spec.sandbox:
        cultivation.initialize(player.private_state)
    sync_items(player.private_state, content)


def sync_items(state, content):
    names = {i["id"]: i["name"] for i in content.get("items", [])}
    state["items"] = [f"{names.get(k, k)} ×{q}" for k, q in state["inventory"].items() if q > 0]


def matches(cond: Condition, state, progress):
    return (not cond.location or cond.location == state["location"]) and (
        progress["minute"] >= cond.minute
    ) and all(progress["flags"].get(k, False) == v for k, v in cond.flags.items()) and all(
        state["inventory"].get(k, 0) >= q for k, q in cond.items.items()
    )


def apply_effect(effect: Effect, state, progress):
    for item, delta in effect.items.items():
        if state["inventory"].get(item, 0) + delta < 0:
            raise ValueError("剧情效果缺少所需物品")
    state["hp"] = max(0, min(state["max_hp"], state["hp"] + effect.hp))
    state["xp"] += effect.xp
    state["level"] = 1 + state["xp"] // 10
    for item, delta in effect.items.items():
        state["inventory"][item] = state["inventory"].get(item, 0) + delta
    progress["flags"].update(effect.flags)
    for npc, delta in effect.relationships.items():
        state["relationships"][npc] = max(-100, min(100, state["relationships"].get(npc, 0) + delta))
    for clue in effect.clues:
        if clue not in state["clues"]:
            state["clues"].append(clue)
    if effect.ending:
        progress["ending"] = effect.ending


def context_for(content, state, progress):
    """意图层可看交互标签；不暴露锚点效果、NPC 秘密和隐藏线索。"""
    spec = parse_spec(content)
    return {
        "world": content.get("world", {}).get("name", ""),
        "location": spec.locations[state["location"]].model_dump(),
        "locations": {k: v.name for k, v in spec.locations.items()},
        "interactions": {k: v.label for k, v in spec.interactions.items() if k not in progress["done"]},
        "player": copy.deepcopy(state), "minute": progress["minute"],
        "paused": progress["paused"],
        "play_style": cultivation.HELP if spec.sandbox else "探索剧情，按玩家意愿行动",
    }


def action_cost(interaction, state):
    cost = dict(interaction.cost)
    if interaction.cultivation_action:
        for key, amount in cultivation.costs(interaction.cultivation_action, state).items():
            cost[key] = cost.get(key, 0) + amount
    return cost


def evaluate(content, state, progress, plan: ai.Plan, rng=None):
    """在副本上计算；失败的后续动作停止，已执行步骤仍产生真实后果。"""
    plan = ai.Plan.model_validate(plan.model_dump())
    spec = parse_spec(content)
    before_state, before_minute = state, progress['minute']
    state, progress = copy.deepcopy(state), copy.deepcopy(progress)
    results = []
    for action in plan.actions:
        if progress["ending"] or state["hp"] <= 0:
            break
        before_pause = progress["paused"]
        if before_pause and action.kind in ("wait", "rest"):
            results.append({"ok": False, "text": "关键事件正在等待你的决定，请直接描述行动。"})
            break
        result = {"kind": action.kind, "target": action.target, "ok": True}
        minutes = 0
        if action.kind == "move":
            exits = spec.locations[state["location"]].exits
            if action.target not in exits:
                result.update(ok=False, text="这里没有通往该地点的路线。")
            else:
                minutes = exits[action.target]
                state["location"] = action.target
                result["text"] = f"你抵达{spec.locations[action.target].name}。{spec.locations[action.target].description}"
        elif action.kind == "interact":
            interaction = spec.interactions.get(action.target)
            cost = action_cost(interaction, state) if interaction else {}
            if interaction is None or action.target in progress["done"]:
                result.update(ok=False, text="这件事当前无法再次执行。")
            elif not matches(interaction.requires, state, progress):
                result.update(ok=False, text="当前地点、线索或物品还不满足这个行动的条件。")
                if spec.sandbox and interaction.requires.location and interaction.requires.location != state["location"]:
                    place = spec.locations[interaction.requires.location].name
                    result["text"] = f"{interaction.label}需要在{place}进行。可以先输入‘地图’查看路线。"
            elif interaction.cultivation_action and cultivation.unavailable(interaction.cultivation_action, state):
                result.update(ok=False, text=cultivation.unavailable(interaction.cultivation_action, state))
            elif any(state["inventory"].get(k, 0) < q for k, q in cost.items()):
                result.update(ok=False, text="随身物品不足，无法执行这个行动。")
            else:
                minutes = interaction.minutes
                for item, qty in cost.items():
                    state["inventory"][item] -= qty
                success = True
                if interaction.check:
                    ctx = rules.make_ctx(attr=state["stats"], stat=state,
                                         item_count=state["inventory"],
                                         constants=content.get("rules", {}).get("constants"), rng=rng)
                    chance, success = rules.check(content["rules"], interaction.check, ctx)
                    result["check"] = {"name": interaction.check, "chance": chance, "success": success}
                apply_effect(interaction.success if success else interaction.failure, state, progress)
                result.update(ok=success, text=interaction.success_text if success else interaction.failure_text)
                if interaction.cultivation_action:
                    success, text = cultivation.resolve(interaction.cultivation_action, state, progress, rng)
                    result.update(ok=success, text=text)
                if spec.sandbox:
                    state["level"] = state["cultivation"]["rank"] + 1
                    if cost:
                        names = {i["id"]: i["name"] for i in content["items"]}
                        result["text"] += "\n消耗：" + "、".join(f"{names[k]} ×{q}" for k, q in cost.items())
                if success and interaction.once:
                    progress["done"].append(action.target)
        elif action.kind == "look":
            result["text"] = spec.locations[state["location"]].description
        elif action.kind == "rest":
            minutes = 15
            healed = min(2, state["max_hp"] - state["hp"])
            state["hp"] += healed
            result["text"] = f"你休息片刻，恢复 {healed} 点生命。"
        else:
            minutes = 5
            result["text"] = "你暂时观察局势，时间继续流逝。"
        progress["minute"] += minutes
        result["minutes"] = minutes
        results.append(result)

        if progress["paused"]:
            anchor = spec.anchors[progress["paused"]]
            if progress["flags"].get(anchor.resume_flag, False):
                progress["paused"] = None
        # 无效行动不能触发剧情或推进时间；检定失败仍可触发后果。
        if result["ok"] or minutes:
            for key, anchor in spec.anchors.items():
                if progress["ending"] or state["hp"] <= 0:
                    break
                if key in progress["fired"] or not matches(anchor.requires, state, progress):
                    continue
                apply_effect(anchor.effect, state, progress)
                progress["fired"].append(key)
                results.append({"anchor": key, "ok": True, "text": anchor.text})
                if anchor.pause and not progress["flags"].get(anchor.resume_flag, False):
                    progress["paused"] = key
                    break
        if state["hp"] <= 0:
            if spec.sandbox:
                results.append(cultivation.revive(state, progress, spec.start))
                break  # 复活改变了位置，不再执行玩家在身陨前提交的后续动作。
            progress["ending"] = progress["ending"] or "你的生命归零，这次旅程结束了。"
        if progress["ending"]:
            progress["paused"] = None
        if not result["ok"] or (progress["paused"] and progress["paused"] != before_pause):
            break
    sync_items(state, content)
    receipt = {"results": results, "minute": progress["minute"], "location": state["location"],
               "hp": state["hp"], "inventory": state["inventory"], "xp": state["xp"],
               "relationships": state["relationships"], "paused": progress["paused"],
               "ending": progress["ending"]}
    if spec.sandbox:
        receipt["cultivation"] = cultivation.status(state)
    receipt['summary'] = turn_summary.changes(content, before_state, state, progress['minute'] - before_minute,
                                               revived=any(r.get('revival') for r in results))
    return state, progress, receipt


def format_receipt(receipt):
    lines = []
    for result in receipt["results"]:
        line = result["text"]
        if "check" in result:
            line += f"（成功率 {result['check']['chance']:.0%}，{'成功' if result['check']['success'] else '失败'}）"
        lines.append(line)
    if receipt["ending"]:
        lines.append(f"🏁 {receipt['ending']}")
    elif receipt["paused"]:
        lines.append("⏸ 关键事件暂停，等待你自由输入决定。")
    return "\n\n".join(lines)


async def take_turn(db, world, player, text, *, advance=False, choice_token=None):
    try:
        check_player_input(text)
    except InputRejected as exc:
        return False, str(exc)
    social = (cultivation.social(text) if cultivation.enabled(world.script.content_json) else None) or social_reply(text)
    if social:
        return True, social
    if guidance.is_location_question(text):
        spec = parse_spec(world.script.content_json)
        loc = spec.locations[player.private_state["location"]]
        return True, f"你现在在{loc.name}，扮演{player.character_name}（{player.character_role}）。\n{loc.description}"
    if guidance.is_help(text):
        if cultivation.enabled(world.script.content_json):
            return True, cultivation.HELP
        return True, "你可以探索环境、寻找线索，并决定角色的行动。查看引导不会推进时间。"
    world_id, player_id, user_id = world.id, player.id, player.user_id
    content = copy.deepcopy(world.script.content_json)
    explicit = None
    clean = normalized(text).strip().rstrip("?？!！。.")
    if cultivation.enabled(content):
        reply = cultivation.query(text, content, player.private_state)
        if reply:
            return True, reply
        if clean in ('开始修炼', '继续打坐', '开始打坐'):
            explicit = ai.Plan(actions=[ai.Action(kind='interact', target='meditate')])
        for key, loc in parse_spec(content).locations.items():
            if clean in ("去" + loc.name, "前往" + loc.name, "走到" + loc.name):
                explicit = ai.Plan(actions=[ai.Action(kind="move", target=key)])
                break
        if clean in ("休息", "调息", "休息恢复"):
            explicit = ai.Plan(actions=[ai.Action(kind="rest")])
    for key, interaction in parse_spec(content).interactions.items():
        aliases = interaction.aliases + ([interaction.label] if cultivation.enabled(content) else [])
        if clean not in aliases:
            continue
        if not cultivation.enabled(content) and not matches(interaction.requires, player.private_state, world.progress_json["narrative"]):
            continue
        if key in world.progress_json["narrative"]["done"] and interaction.repeat_text:
            return True, interaction.repeat_text + "\n这条线索已经记录，重读不消耗时间。"
        explicit = ai.Plan(actions=[ai.Action(kind="interact", target=key)])
        break
    previous = copy.deepcopy(world.progress_json["narrative"])
    context = context_for(content, player.private_state, previous)
    recent = list(db.scalars(select(PlayerAction.outcome).where(
        PlayerAction.world_id == world_id, PlayerAction.user_id == user_id, PlayerAction.outcome.is_not(None)
    ).order_by(PlayerAction.id.desc()).limit(2)))
    # 释放读取事务，LLM 网络等待不占用数据库行锁。
    db.rollback()
    try:
        index = guidance.number(text)
        if advance:
            plan = ai.Plan(actions=[ai.Action(kind="wait")])
        elif index is not None:
            try:
                plan = await guidance.resolve(world, player, index, choice_token)
            except ValueError as exc:
                return False, str(exc)
        elif explicit is not None:
            plan = explicit
        elif text.strip() in ("继续观察", "观察四周", "观察", "看看周围"):
            plan = ai.Plan(actions=[ai.Action(kind="look")])
        else:
            plan = await ai.parse_plan(text, context)
    except ConversationReply as exc:
        return False, str(exc) + "\n这次只是聊聊，没有推进游戏时间。"
    except InputRejected as exc:
        return False, str(exc)
    except Exception as exc:
        logger.warning("单人意图未能完成：%s", type(exc).__name__)
        return False, fallback_reply(context, unavailable=True) + "\n你还停留在原处，这次没有消耗游戏时间。"

    try:
        begin_write(db)
        world = db.scalar(select(World).where(World.id == world_id).execution_options(populate_existing=True))
        player = db.scalar(select(WorldPlayer).where(WorldPlayer.id == player_id).execution_options(populate_existing=True))
        if world is None or player is None or player.world_id != world_id or player.user_id != user_id:
            db.rollback()
            return False, "找不到你的角色。"
        if world.status != "running" or player.status != "alive":
            db.rollback()
            return False, "这次旅程已经结束，可用 /resume 重读结局。"
        progress = world.progress_json["narrative"]
        if progress["revision"] != previous["revision"] or world.script.content_json != content:
            db.rollback()
            return False, "世界刚刚发生了变化，请查看 /status 后重新行动。"
        state, progress, receipt = evaluate(content, player.private_state, progress, plan)
        progress["revision"] += 1
        world.progress_json = {**world.progress_json, "narrative": progress}
        player.private_state = state
        world.day = 1 + progress["minute"] // 1440
        if state["hp"] <= 0:
            player.status = "dead"
        if progress["ending"]:
            world.status = "finished"
            world.finished_at = datetime.now()
        fallback = format_receipt(receipt)
        record = PlayerAction(world_id=world_id, user_id=user_id, day=world.day, text=text,
                              intent={"actions": plan.model_dump()["actions"], "receipt": receipt,
                                      "revision": progress["revision"]}, outcome=fallback)
        db.add(record)
        db.commit()  # 状态、检定、兜底结果同一事务持久化；只有此后才调用叙述者。
        record_id = record.id
    except Exception:
        db.rollback()
        logger.exception("单人事务未提交")
        return False, "本次行动未能保存，世界未改变，请重试。"

    sandbox = cultivation.enabled(content)
    if (all(not r.get('ok') and not r.get('minutes') for r in receipt['results'])
            or (sandbox and not settings.deepseek_api_key)) or (not sandbox and len(plan.actions) == 1 and plan.actions[0].kind == "interact"
            and parse_spec(content).interactions.get(plan.actions[0].target)
            and parse_spec(content).interactions[plan.actions[0].target].verbatim):
        return True, fallback

    try:
        # 不传玩家原文或未来事件；最近已保存的叙述帮助衔接场景、避免重复措辞。
        narration = await ai.narrate(text, {"world": context["world"], "character": player.character_name,
                                           "location": parse_spec(content).locations[state['location']].model_dump(),
                                           "recent": [entry[-1200:] for entry in reversed(recent)],
                                           "sandbox": sandbox}, receipt)
        outcome = turn_summary.with_narration(narration, receipt)
        record = db.get(PlayerAction, record_id)
        record.outcome = outcome
        db.commit()
        return True, outcome
    except Exception:
        db.rollback()
        logger.warning("单人叙述失败，使用已提交回执", exc_info=True)
        return True, fallback
