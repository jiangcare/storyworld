"""导演层：世界剧情推进。"""
import json
import logging

from . import prompts, schemas
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
        script_json=json.dumps(script, ensure_ascii=False),
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
    data = await client.chat_json(system, user, max_tokens=1800)
    return _coerce(data)


def _coerce(data: dict) -> dict:
    def s(key: str, default: str = "") -> str:
        v = data.get(key)
        return v if isinstance(v, str) else default

    def sl(key: str) -> list:
        v = data.get(key)
        return v if isinstance(v, list) else []

    return {
        "public_broadcast": s("public_broadcast") or "（今日无事发生，世界静默。）",
        "canon_additions": [str(x) for x in sl("canon_additions")],
        "countdown_update": s("countdown_update"),
        "chapter_note": s("chapter_note"),
        "world_ended": bool(data.get("world_ended", False)),
    }
