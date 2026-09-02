"""剧本 DSL 校验与查询辅助。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ScriptError(ValueError):
    pass


def validate_script(content: dict) -> list[str]:
    """返回错误列表；空列表 = 合法。"""
    errors: list[str] = []
    if not isinstance(content, dict):
        return ["剧本内容必须是 JSON 对象"]

    mode = content.get("mode", "multi")
    if mode not in ("single", "multi"):
        errors.append("mode 必须是 single 或 multi")

    days = int(content.get("days", 0) or 0)
    if days < 1 or days > 30:
        errors.append("days 必须在 1-30")

    cards = content.get("player_cards")
    if not isinstance(cards, list) or not cards:
        errors.append("player_cards 至少需要 1 个角色卡")
    elif mode == "single" and len(cards) != 1:
        errors.append("单人剧本 player_cards 必须恰好 1 个主角卡")
    elif mode == "multi" and not (2 <= len(cards) <= 8):
        errors.append("多人剧本 player_cards 建议 2-8 个角色卡")

    chapters = content.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        errors.append("chapters 至少需要 1 章")
    else:
        covered = set()
        for ch in chapters:
            ds, de = ch.get("day_start"), ch.get("day_end")
            if not isinstance(ds, int) or not isinstance(de, int) or ds > de:
                errors.append(f"章节 {ch.get('title', '?')} 日期范围非法")
                continue
            if ds < 1 or de > days:
                errors.append(f"章节 {ch.get('title', '?')} 超出总天数范围")
            covered.update(range(ds, de + 1))
        missing = [d for d in range(1, days + 1) if d not in covered]
        if missing:
            errors.append(f"章节未覆盖天数: {missing}")

    world = content.get("world")
    if not isinstance(world, dict) or not world.get("name"):
        errors.append("world.name 必填")
    return errors


def chapter_for_day(content: dict, day: int) -> dict | None:
    for ch in content.get("chapters", []):
        if ch.get("day_start", 0) <= day <= ch.get("day_end", 0):
            return ch
    return None


def events_for_day(content: dict, day: int) -> list[dict]:
    out = []
    for ch in content.get("chapters", []):
        for ev in ch.get("events", []):
            if ev.get("day") == day:
                out.append(ev)
    return out


def format_events_for_prompt(events: list[dict]) -> str:
    if not events:
        return ""
    lines = []
    for ev in events:
        lines.append(
            f"- [强度{ev.get('magnitude', 'mid')}] {ev.get('title', '事件')}: {ev.get('desc', '')}"
        )
    return "\n".join(lines)
