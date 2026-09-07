"""导演层：世界剧情推进。"""
import json
import logging

from . import prompts, schemas
from .outputs import WorldOutput, public_script
from .client import client, LLMError

logger = logging.getLogger(__name__)


async def generate_world_update(
    script: dict,
    day: int,
    total_days: int,
    countdown: str,
    countdown_total: int,
    recent_canon: str,
    chapter_events: str,
    players_status: str,
    actions_summary: str,
) -> dict:
    system = prompts.DIRECTOR_SYSTEM_TEMPLATE.format(
        schema=prompts._schema_hint(schemas.DIRECTOR_OUTPUT_SCHEMA),
    )
    user = prompts.director_user_prompt(
        day=day,
        total_days=total_days,
        countdown=countdown,
        countdown_total=countdown_total,
        recent_canon=recent_canon,
        chapter_events=chapter_events,
        players_status=players_status,
        actions_summary=actions_summary,
    )
    user = json.dumps({"script": public_script(script), "game_context": user}, ensure_ascii=False)
    data = await client.chat_json(system, user, max_tokens=1800)
    return _coerce(data)


def _coerce(data: dict) -> dict:
    return WorldOutput.model_validate(data).model_dump()
