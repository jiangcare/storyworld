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
from ..ai.policy import InputRejected, check_player_input
from ..db import begin_write
from ..models import PlayerAction, World, WorldPlayer
from ..rules import engine as rules
from .narrative_dsl import Condition, Effect, parse_spec
from . import guidance

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
    }


def evaluate(content, state, progress, plan: ai.Plan, rng=None):
    """在副本上计算；失败的后续动作停止，已执行步骤仍产生真实后果。"""
    plan = ai.Plan.model_validate(plan.model_dump())
    spec = parse_spec(content)
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
            if interaction is None or action.target in progress["done"]:
                result.update(ok=False, text="这件事当前无法再次执行。")
            elif not matches(interaction.requires, state, progress):
                result.update(ok=False, text="当前地点、线索或物品还不满足这个行动的条件。")
            elif any(state["inventory"].get(k, 0) < q for k, q in interaction.cost.items()):
                result.update(ok=False, text="随身物品不足，无法执行这个行动。")
            else:
                minutes = interaction.minutes
                for item, qty in interaction.cost.items():
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
    return state, progress, receipt


def format_receipt(receipt):
    lines = []
    for result in receipt["results"]:
        line = result["text"]
        if "check" in result:
            line += f"（成功率 {result['check']['chance']:.0%}，{'成功' if result['check']['success'] else '失败'}）"
        lines.append(line)
    lines.append(f"⏱ 已过 {receipt['minute']} 分钟 · 生命 {receipt['hp']} · 已自动保存")
    if receipt["ending"]:
        lines.append(f"🏁 {receipt['ending']}")
    elif receipt["paused"]:
        lines.append("⏸ 关键事件暂停，等待你自由输入决定。")
    return "\n".join(lines)


async def take_turn(db, world, player, text, *, advance=False, choice_token=None):
    try:
        check_player_input(text)
    except InputRejected as exc:
        return False, str(exc)
    if guidance.is_help(text):
        return True, "你可以探索环境、寻找线索，并决定角色的行动。查看引导不会推进时间。"
    world_id, player_id, user_id = world.id, player.id, player.user_id
    content = copy.deepcopy(world.script.content_json)
    previous = copy.deepcopy(world.progress_json["narrative"])
    context = context_for(content, player.private_state, previous)
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
        elif text.strip() in ("继续观察", "观察四周", "观察", "看看周围"):
            plan = ai.Plan(actions=[ai.Action(kind="look")])
        else:
            plan = await ai.parse_plan(text, context)
    except InputRejected as exc:
        return False, str(exc)
    except Exception:
        logger.warning("单人意图解析失败", exc_info=True)
        return False, "暂时无法把这段意图转成可执行行动；世界未改变。请更具体地描述目标与做法。"

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

    try:
        # 叙述上下文不包含尚未执行的交互，回执始终随文显示供玩家核对。
        narration = await ai.narrate(text, {"world": context["world"], "character": player.character_name}, receipt)
        outcome = narration + "\n\n—— 世界记录 ——\n" + fallback
        record = db.get(PlayerAction, record_id)
        record.outcome = outcome
        db.commit()
        return True, outcome
    except Exception:
        db.rollback()
        logger.warning("单人叙述失败，使用已提交回执", exc_info=True)
        return True, fallback
