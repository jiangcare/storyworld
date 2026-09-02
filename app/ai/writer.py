"""编剧层：玩家个人场景生成。"""
import json
import logging

from . import prompts, schemas
from .client import client

logger = logging.getLogger(__name__)


async def generate_scene(
    script: dict,
    character_card: dict,
    day: int,
    total_days: int,
    world_broadcast: str,
    chapter_goal: str,
    player_private_state: str,
    player_recent_history: str,
    player_today_actions: str,
) -> dict:
    system = prompts.WRITER_SYSTEM_TEMPLATE.format(
        script_json=json.dumps(script, ensure_ascii=False),
        character_card=json.dumps(character_card, ensure_ascii=False),
        schema=prompts._schema_hint(schemas.WRITER_OUTPUT_SCHEMA),
    )
    user = prompts.writer_user_prompt(
        day=day,
        total_days=total_days,
        world_broadcast=world_broadcast,
        chapter_goal=chapter_goal,
        player_private_state=player_private_state,
        player_recent_history=player_recent_history,
        player_today_actions=player_today_actions,
    )
    data = await client.chat_json(system, user, max_tokens=2200)
    return _coerce(data)


def _coerce(data: dict) -> dict:
    def s(key: str, default: str = "") -> str:
        v = data.get(key)
        return v if isinstance(v, str) else default

    def sl(key: str) -> list:
        v = data.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []

    sc = data.get("state_changes") or {}
    if not isinstance(sc, dict):
        sc = {}
    notes = sc.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}

    return {
        "narrative": s("narrative", "（你度过了平静的一天。）"),
        "suggested_actions": sl("suggested_actions")[:4],
        "state_changes": {
            "hp_delta": int(sc.get("hp_delta", 0) or 0),
            "items_added": sl("items_added"),
            "items_removed": sl("items_removed"),
            "clues_added": sl("clues_added"),
            "notes": {str(k): str(v) for k, v in notes.items()},
        },
        "scene_ended": bool(data.get("scene_ended", False)),
    }
