"""旧版每日行动也必须先通过范围判断，不接受游戏外任务。"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from . import prompts, schemas
from .client import client
from .conversation import ConversationReply, REPLY_INSTRUCTION, validated_reply, fallback_reply
from .policy import InputRejected, REFUSAL, check_player_input, has_control_instruction


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["gameplay", "out_of_scope"]
    action_type: Literal["investigate", "talk", "move", "use", "fight", "help", "hide", "rest", "other"]
    target: str = Field(max_length=80)
    summary: str = Field(max_length=120)
    dice_check: bool
    attribute: Literal["strength", "agility", "intellect", "charm", "luck", ""]
    reply: str = Field(default="", max_length=400)


def validate_intent(data: dict) -> dict:
    intent = Intent.model_validate(data)
    if intent.scope != "gameplay":
        raise ConversationReply(validated_reply(intent.reply) if intent.reply.strip() else fallback_reply())
    if not intent.summary.strip():
        raise ConversationReply(fallback_reply())
    if has_control_instruction(intent.summary + " " + intent.target):
        raise InputRejected(REFUSAL)
    return intent.model_dump(exclude={"reply"})


def safe_summary(data: dict | None) -> str:
    try:
        return validate_intent(data)["summary"]
    except (ValueError, TypeError):
        return ""


async def parse_intent(text: str, context: dict | None = None) -> dict:
    check_player_input(text)
    data = await client.chat_json(
        prompts.INTENT_SYSTEM_TEMPLATE.format(schema=prompts._schema_hint(schemas.INTENT_OUTPUT_SCHEMA)) + REPLY_INSTRUCTION,
        prompts.intent_user_prompt(text, context), max_tokens=550, temperature=0.1,
    )
    return validate_intent(data)
