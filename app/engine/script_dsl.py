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
    if 'world_pack' in content:
        from ..worlds.packs import for_content, PackError
        try:
            pack = for_content(content)
            if content != pack.content():
                errors.append('Skills 剧本需使用已安装版本的完整初始定义')
        except PackError as exc:
            errors.append(str(exc))

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

    # ---- 数据化预设段（可选，存在则做结构检查） ----
    for key in ("items", "abilities", "task_templates"):
        v = content.get(key)
        if v is None:
            continue
        if not isinstance(v, list):
            errors.append(f"{key} 必须是数组")
            continue
        if key == "items":
            ids = []
            for i, it in enumerate(v):
                if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                    errors.append(f"items[{i}] 需要 id 字符串")
                    continue
                if it["id"] in ids:
                    errors.append(f"items 重复 id: {it['id']}")
                ids.append(it["id"])
                if not isinstance(it.get("name"), str):
                    errors.append(f"items[{i}] 需要 name 字符串")
        elif key == "abilities":
            for i, ab in enumerate(v):
                if not isinstance(ab, dict) or not isinstance(ab.get("id"), str):
                    errors.append(f"abilities[{i}] 需要 id 字符串")
                elif not isinstance(ab.get("name"), str):
                    errors.append(f"abilities[{i}] 需要 name 字符串")
        else:  # task_templates
            for i, t in enumerate(v):
                if not isinstance(t, dict) or not isinstance(t.get("title"), str):
                    errors.append(f"task_templates[{i}] 需要 title 字符串")

    ml = content.get("mainline")
    if ml is not None:
        if not isinstance(ml, list):
            errors.append("mainline 必须是数组")
        else:
            for i, b in enumerate(ml):
                if not isinstance(b, dict) or not isinstance(b.get("desc"), str):
                    errors.append(f"mainline[{i}] 需要 desc 字符串")

    # ---- 数值规则包（走规则引擎验收） ----
    rules = content.get("rules")
    if rules not in (None, {}):
        from ..rules import dsl as rules_dsl

        defs = {
            "items": {
                it["id"]: it
                for it in (content.get("items") or [])
                if isinstance(it, dict) and isinstance(it.get("id"), str)
            }
        }
        for e in rules_dsl.validate_rule_pack(rules, defs):
            errors.append(f"rules: {e}")
    if "narrative" in content:
        from .narrative_dsl import parse_spec

        try:
            parse_spec(content)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"narrative: {exc}")
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
