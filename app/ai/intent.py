"""意图层：玩家自由输入理解。"""
import logging

from . import prompts, schemas
from .client import client

logger = logging.getLogger(__name__)

_VALID_ACTIONS = {
    "investigate", "talk", "move", "use", "fight",
    "help", "hide", "rest", "other",
}
_VALID_ATTRS = {"strength", "agility", "intellect", "charm", "luck"}


async def parse_intent(text: str) -> dict:
    data = await client.chat_json(
        prompts.INTENT_SYSTEM_TEMPLATE.format(schema=prompts._schema_hint(schemas.INTENT_OUTPUT_SCHEMA)),
        prompts.intent_user_prompt(text),
        max_tokens=300,
        temperature=0.2,
    )
    return _coerce(data)


def _coerce(data: dict) -> dict:
    action = str(data.get("action_type", "other")).lower()
    if action not in _VALID_ACTIONS:
        action = "other"
    attr = str(data.get("attribute", "")).lower()
    if attr not in _VALID_ATTRS:
        attr = ""
    return {
        "action_type": action,
        "target": str(data.get("target", "")),
        "summary": str(data.get("summary", "")) or "",
        "dice_check": bool(data.get("dice_check", False)),
        "attribute": attr,
    }
