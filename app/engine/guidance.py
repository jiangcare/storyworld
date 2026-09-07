"""从剧本和存档生成可执行引导，不依赖模型猜测玩家需要什么。"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

from ..ai.narrative import Action, Plan
from ..ai.policy import normalized
from ..db import get_store
from .narrative_dsl import parse_spec

_HELP = {"怎么玩", "这个游戏怎么玩", "游戏怎么玩", "这是要干嘛", "这是要干什么", "要干嘛",
         "我要干嘛", "我该干嘛", "我该做什么", "接下来做什么", "现在做什么", "不知道要干嘛",
         "不知道干什么", "不知道做什么", "帮助", "游戏帮助", "玩法", "游戏目标", "目标是什么",
         "游戏规则是什么", "游戏规则时什么", "游戏规则", "规则是什么", "规则",
         "都说说", "都说一下", "详细说说", "具体说说",
         "有什么可以做", "我能做什么", "提示", "给我提示", "help", "how to play"}


def is_help(text):
    return normalized(text).strip().rstrip("?？!！。.").strip().lower() in _HELP


def is_location_question(text):
    return normalized(text).strip().rstrip("?？!！。.") in {"这是哪", "这是哪里", "这里是哪", "这里是哪里", "我在哪", "我在哪里"}


def number(text):
    match = re.fullmatch(r"(?:选|选择)?\s*([0-9]{1,3})[.、]?", normalized(text).strip())
    return int(match[1]) if match else None


def objective(content, day=1):
    chapter = next((c for c in content.get("chapters", [])
                    if c.get("day_start", 1) <= day <= c.get("day_end", day)), {})
    return chapter.get("goal") or content.get("description") or "探索当前场景，寻找线索与出路"


def options(content, state, progress):
    from .narrative import matches
    spec = parse_spec(content)
    if progress["ending"] or state["hp"] <= 0:
        return []
    safe, risky = [], []
    for key, interaction in spec.interactions.items():
        if (interaction.once and key in progress["done"]) or not matches(interaction.requires, state, progress):
            continue
        if any(state["inventory"].get(k, 0) < q for k, q in interaction.cost.items()):
            continue
        label = f"{interaction.label}（{interaction.minutes}分钟" + ("，有失败风险）" if interaction.check else "）")
        (risky if interaction.check else safe).append({"kind": "interact", "target": key, "label": label})
    def destination_priority(key):
        there = dict(state, location=key)
        return -sum(1 for name, interaction in spec.interactions.items()
                    if not interaction.check and name not in progress["done"]
                    and matches(interaction.requires, there, progress)
                    and all(there["inventory"].get(k, 0) >= q for k, q in interaction.cost.items()))
    exits = spec.locations[state["location"]].exits
    moves = [{"kind": "move", "target": key, "label": f"去{spec.locations[key].name}（{exits[key]}分钟）"}
             for key in sorted(exits, key=destination_priority)]
    choices = (safe + moves + risky)[:5]
    choices.append({"kind": "look", "target": "", "label": "观察当前环境（不耗时）"})
    return choices


def render(world, player, choices, *, explain=False):
    content = world.script.content_json
    progress = world.progress_json["narrative"]
    if progress["ending"]:
        return "本次旅程已结束。用 /resume 重读结局，或 /scripts 开始新的故事。"
    lines = [f"🎯 当前目标：{objective(content, world.day)}"]
    if progress["revision"] <= 1 and world.progress_json.get("legacy_daily"):
        lines.append("已衔接到即时探索版。此前待结算的输入保留在历史中，接下来的探索会当场返回结果。")
    if explain:
        lines += [content.get("world", {}).get("background", ""),
                  "你扮演故事里的角色：探索、寻找线索，再决定怎么行动。",
                  "问玩法、查看状态和观察不耗时；移动和交互会消耗标注的分钟。/continue 会等待5分钟，关键事件暂停时需先作决定。"]
    if progress["paused"]:
        lines.append("⏸ 眼下有紧急事件等待决定，可以移动、调查或处理现场；不用反复等待。")
    lines.append("现在可以：")
    lines += [f"{i}. {choice['label']}" for i, choice in enumerate(choices, 1)]
    lines.append("回复编号或点击按钮，也可以直接描述行动。输入“怎么玩”随时查看引导。")
    return "\n".join(line for line in lines if line)


def _fingerprint(content):
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _key(world, player):
    return f"guidance:{world.id}:{player.id}"


async def remember(world, player):
    choices = options(world.script.content_json, player.private_state, world.progress_json["narrative"])
    token = secrets.token_hex(4)
    store = await get_store()
    await store.set(_key(world, player), json.dumps({
        "revision": world.progress_json["narrative"]["revision"], "token": token,
        "script": _fingerprint(world.script.content_json), "options": choices,
    }, ensure_ascii=False), ex=3600)
    return choices, token


async def resolve(world, player, index, token=None):
    store = await get_store()
    raw = await store.get(_key(world, player))
    if not raw:
        raise ValueError("还没有可选择的列表，或列表已过期。请从下方新选项中选择。")
    menu = json.loads(raw)
    if (menu["revision"] != world.progress_json["narrative"]["revision"]
            or menu["script"] != _fingerprint(world.script.content_json)
            or (token is not None and token != menu["token"])):
        raise ValueError("场景或选项已更新，请从下方新选项中重新选择。")
    if not 1 <= index <= len(menu["options"]):
        raise ValueError(f"请回复 1-{len(menu['options'])} 中的编号，或直接描述行动。")
    choice = menu["options"][index - 1]
    if choice not in options(world.script.content_json, player.private_state, world.progress_json["narrative"]):
        raise ValueError("这个行动现在无法执行，请重新选择。")
    return Plan(actions=[Action(kind=choice["kind"], target=choice["target"])])
