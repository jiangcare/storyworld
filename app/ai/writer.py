"""编剧层：玩家个人场景生成。"""
from __future__ import annotations

import json
import logging

from . import prompts, schemas
from .outputs import SceneOutput, public_script
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
    player_data: str = "",
) -> dict:
    system = prompts.WRITER_SYSTEM_TEMPLATE.format(
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
        player_data=player_data,
    )
    user = json.dumps({"script": public_script(script), "character_card": character_card, "game_context": user}, ensure_ascii=False)
    data = await client.chat_json(system, user, max_tokens=2200)
    return _coerce(data)


def _coerce(data: dict) -> dict:
    return SceneOutput.model_validate(data).model_dump()
