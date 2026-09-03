"""剧本完善：用户草稿 → 完整剧本 JSON。"""
import json
import logging

from . import prompts, schemas
from .client import client

logger = logging.getLogger(__name__)


async def complete_script(draft: str, mode: str = "multi") -> dict:
    """把用户上传的剧本草稿交给 AI 完善为完整剧本。"""
    mode = "single" if mode == "single" else "multi"
    system = prompts.SCRIPT_BUILDER_SYSTEM_TEMPLATE.format(
        schema=prompts._schema_hint(schemas.SCRIPT_OUTPUT_SCHEMA)
    )
    data = await client.chat_json(
        system,
        prompts.script_builder_user_prompt(mode, draft),
        max_tokens=4000,
    )
    return _normalize(data, mode)


def _normalize(data: dict, mode: str) -> dict:
    """结构兜底：保证关键字段存在且类型正确，并对 AI 生成的规则包做验收。"""
    data = data if isinstance(data, dict) else {}
    world = data.get("world") or {}
    if not isinstance(world, dict):
        world = {}
    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        chapters = []
    cards = data.get("player_cards")
    if not isinstance(cards, list):
        cards = []
    npcs = data.get("npcs")
    if not isinstance(npcs, list):
        npcs = []
    days = int(data.get("days", 7) or 7)
    if mode == "single" and cards:
        # 单人剧本只保留第一个主角卡
        cards = cards[:1]

    items = data.get("items")
    if not isinstance(items, list):
        items = []
    abilities = data.get("abilities")
    if not isinstance(abilities, list):
        abilities = []
    task_templates = data.get("task_templates")
    if not isinstance(task_templates, list):
        task_templates = []
    mainline = data.get("mainline")
    if not isinstance(mainline, list):
        mainline = []

    # ---- 规则包验收闸门：不过关就回退为空规则并记录报告 ----
    rules = data.get("rules")
    if not isinstance(rules, dict):
        rules = {}
    from ..rules import complexity as rules_complexity

    rules_ok, report = rules_complexity.rules_acceptable(rules)
    acceptance = {"rules": report}
    if not rules_ok:
        rules = {}
        logger.warning("AI 生成的规则包未通过验收，已回退为空规则: %s", report["violations"][:5])

    return {
        "title": str(data.get("title", "未命名剧本")),
        "description": str(data.get("description", "")),
        "genre": str(data.get("genre", "")),
        "mode": mode,
        "min_players": int(data.get("min_players", 1) or 1),
        "max_players": int(data.get("max_players", 6) or 6),
        "days": max(1, min(days, 30)),
        "world": {
            "name": str(world.get("name", "未知世界")),
            "background": str(world.get("background", "")),
            "rules": str(world.get("rules", "")),
            "countdown": str(world.get("countdown", "")),
            "countdown_total": int(world.get("countdown_total", 0) or 0),
        },
        "chapters": chapters,
        "player_cards": cards,
        "npcs": npcs,
        "items": items[:120],
        "abilities": abilities[:60],
        "task_templates": task_templates[:30],
        "mainline": mainline[:60],
        "rules": rules,
        "_acceptance": acceptance,
        "system_rules": str(data.get("system_rules", "")),
    }


def to_json(script: dict) -> str:
    return json.dumps(script, ensure_ascii=False, indent=2)
