"""规则引擎运行时：上下文构建 + 具体数值操作。

用法：脚本 content_json["rules"] 是规则包；把玩家状态/背包/能力喂给 make_ctx，
然后调用 check/forge/draw/synthesize/percent_mods 等操作，全部确定性可测。
"""
from __future__ import annotations

import random
from typing import Optional

from . import expr

DEFAULT_ATTRS = {"strength": 3, "agility": 3, "intellect": 3, "charm": 3, "luck": 3}


def make_ctx(
    attr: Optional[dict] = None,
    stat: Optional[dict] = None,
    item_count: Optional[dict] = None,
    ability: Optional[dict] = None,
    constants: Optional[dict] = None,
    rng: Optional[random.Random] = None,
    seed: Optional[int] = None,
    budget: int = 200_000,
) -> expr._Ctx:
    """构造求值上下文。rng 优先于 seed；seed 固定可复现（测试用）。"""
    namespaces = {
        "attr": dict(DEFAULT_ATTRS, **(attr or {})),
        "stat": {
            "hp": 10,
            "day": 1,
            "level": 1,
            **(stat or {}),
        },
        "item_count": dict(item_count or {}),
        "ability": dict(ability or {}),
        "const": dict(constants or {}),
    }
    if rng is None:
        rng = random.Random(seed)
    return expr._Ctx(namespaces, rng, budget)


# ---------------- 具体操作 ----------------

def check_chance(pack: dict, name: str, ctx: expr._Ctx) -> float:
    """攻击成功率/闪避等：返回公式算出的概率。"""
    rule = (pack.get("checks") or {}).get(name)
    if not isinstance(rule, dict) or not isinstance(rule.get("formula"), str):
        raise KeyError(f"规则包缺少 checks.{name}")
    return _to_prob(expr.compile_expr(rule["formula"])(ctx))


def check(pack: dict, name: str, ctx: expr._Ctx) -> tuple[float, bool]:
    """按概率掷骰判定：返回 (概率, 是否成功)。"""
    chance = check_chance(pack, name, ctx)
    return chance, expr.roll_percent(ctx, chance)


def percent_mods_total(pack: dict, name: str, ctx: expr._Ctx) -> float:
    """百分比加成：返回乘数（1 + Σ满足条件的 pct）。"""
    group = (pack.get("percent_mods") or {}).get(name)
    if not isinstance(group, dict):
        raise KeyError(f"规则包缺少 percent_mods.{name}")
    bonus = 0.0
    for m in group.get("mods") or []:
        if not isinstance(m, dict):
            continue
        try:
            hit = bool(expr.compile_expr(m["when"])(ctx))
        except expr.RuleParseError:
            hit = False
        if hit:
            bonus += float(m.get("pct", 0.0))
    return 1.0 + bonus


def forge(pack: dict, name: str, ctx: expr._Ctx, level: int) -> tuple[float, bool]:
    """强化成功率：level 绑定到 stat.level 后按公式掷骰。"""
    spec = (pack.get("forge") or {}).get(name)
    if not isinstance(spec, dict):
        raise KeyError(f"规则包缺少 forge.{name}")
    f = spec.get("success")
    if not isinstance(f, dict) or not isinstance(f.get("formula"), str):
        raise KeyError(f"forge.{name}.success.formula 缺失")
    ctx.ns["stat"]["level"] = level
    chance = _to_prob(expr.compile_expr(f["formula"])(ctx))
    return chance, expr.roll_percent(ctx, chance)


def draw(pack: dict, pool: str, ctx: expr._Ctx) -> Optional[str]:
    """抽奖：按权重返回物品 def_id。"""
    entries = (pack.get("draw_pools") or {}).get(pool)
    if not isinstance(entries, list) or not entries:
        raise KeyError(f"规则包缺少 draw_pools.{pool}")
    picked = expr.pick_weighted(ctx, entries)
    return picked.get("item") if picked else None


def synthesize(
    pack: dict, name: str, inventory: dict, ctx: expr._Ctx
) -> dict:
    """合成：inventory: {def_id: qty}。
    返回 {ok, missing: [def_id], chance, result: {def_id: qty}, consumed: {def_id: qty}}
    """
    spec = (pack.get("synthesize") or {}).get(name)
    if not isinstance(spec, dict):
        raise KeyError(f"规则包缺少 synthesize.{name}")
    missing = []
    consumed: dict = {}
    for inp in spec.get("inputs") or []:
        item = inp.get("item")
        qty = int(inp.get("qty", 1))
        have = int(inventory.get(item, 0))
        if have < qty:
            missing.append(item)
        else:
            consumed[item] = qty
    if missing:
        return {"ok": False, "missing": missing, "chance": 0.0, "result": {}, "consumed": {}}
    chance = float(spec.get("chance", 1.0))
    out = spec.get("output") or {}
    success = expr.roll_percent(ctx, chance)
    return {
        "ok": True,
        "missing": [],
        "chance": chance,
        "result": {out.get("item"): int(out.get("qty", 1))} if success else {},
        "consumed": consumed if success else {},
    }


def _to_prob(v) -> float:
    try:
        p = float(v)
    except (TypeError, ValueError):
        p = 0.0
    return max(0.0, min(1.0, p))
