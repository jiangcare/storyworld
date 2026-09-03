"""规则包 DSL：schema 校验 + 资源上限（防 AI 生成巨型结构）。"""
from __future__ import annotations

from typing import Optional

from . import expr

# 结构上限（复杂度评估的硬边界）
MAX_CHECKS = 200
MAX_POOLS = 50
MAX_POOL_ENTRIES = 500
MAX_MOD_GROUPS = 200
MAX_MODS_PER_GROUP = 100
MAX_SYNTH = 200
MAX_SYNTH_INPUTS = 20
MAX_FORGE = 50
MAX_CONSTANTS = 200

TOP_KEYS = {"constants", "checks", "percent_mods", "forge", "draw_pools", "synthesize"}


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_rule_pack(pack: dict, defs: Optional[dict] = None) -> list:
    """校验规则包。defs 可选：{"items": {def_id: {...}}}，提供时校验物品引用。

    返回错误列表；空列表 = 通过。
    """
    errors: list = []
    if pack is None:
        return []
    if not isinstance(pack, dict):
        return ["rules 必须是 JSON 对象"]

    unknown = set(pack) - TOP_KEYS
    if unknown:
        errors.append(f"rules 含未知键: {sorted(unknown)}（允许: {sorted(TOP_KEYS)}）")

    def has_items():
        return bool(defs and defs.get("items"))

    def check_item_ref(key: str):
        if has_items() and key not in defs["items"]:
            errors.append(f"物品引用不存在: {key}")

    # ---- constants ----
    consts = pack.get("constants") or {}
    if not isinstance(consts, dict):
        errors.append("constants 必须是对象")
    elif len(consts) > MAX_CONSTANTS:
        errors.append(f"constants 数量超限 >{MAX_CONSTANTS}")
    else:
        for k, v in consts.items():
            if not _is_num(v):
                errors.append(f"constants.{k} 必须是数字")

    # ---- checks ----
    checks = pack.get("checks") or {}
    if not isinstance(checks, dict):
        errors.append("checks 必须是对象")
    elif len(checks) > MAX_CHECKS:
        errors.append(f"checks 数量超限 >{MAX_CHECKS}")
    else:
        for name, rule in checks.items():
            if not isinstance(rule, dict) or not isinstance(rule.get("formula"), str):
                errors.append(f"checks.{name} 需要 formula 字符串")
                continue
            try:
                expr.compile_expr(rule["formula"])
            except expr.RuleParseError as e:
                errors.append(f"checks.{name}.formula 解析失败: {e}")

    # ---- percent_mods ----
    mods = pack.get("percent_mods") or {}
    if not isinstance(mods, dict):
        errors.append("percent_mods 必须是对象")
    elif len(mods) > MAX_MOD_GROUPS:
        errors.append(f"percent_mods 分组超限 >{MAX_MOD_GROUPS}")
    else:
        for name, group in mods.items():
            if not isinstance(group, dict) or not _is_num(group.get("base")):
                errors.append(f"percent_mods.{name} 需要数字 base")
                continue
            mlist = group.get("mods") or []
            if not isinstance(mlist, list) or len(mlist) > MAX_MODS_PER_GROUP:
                errors.append(f"percent_mods.{name}.mods 超限 >{MAX_MODS_PER_GROUP}")
                continue
            for i, m in enumerate(mlist):
                if not isinstance(m, dict) or not isinstance(m.get("when"), str):
                    errors.append(f"percent_mods.{name}.mods[{i}] 需要 when 表达式")
                    continue
                if not _is_num(m.get("pct")):
                    errors.append(f"percent_mods.{name}.mods[{i}].pct 必须是数字")
                try:
                    expr.compile_expr(m["when"])
                except expr.RuleParseError as e:
                    errors.append(f"percent_mods.{name}.mods[{i}].when 解析失败: {e}")

    # ---- forge ----
    forge = pack.get("forge") or {}
    if not isinstance(forge, dict):
        errors.append("forge 必须是对象")
    elif len(forge) > MAX_FORGE:
        errors.append(f"forge 数量超限 >{MAX_FORGE}")
    else:
        for name, spec in forge.items():
            f = (spec or {}).get("success") if isinstance(spec, dict) else None
            formula = f.get("formula") if isinstance(f, dict) else None
            if not isinstance(formula, str):
                errors.append(f"forge.{name}.success.formula 缺失")
                continue
            try:
                expr.compile_expr(formula)
            except expr.RuleParseError as e:
                errors.append(f"forge.{name}.success.formula 解析失败: {e}")

    # ---- draw_pools ----
    pools = pack.get("draw_pools") or {}
    if not isinstance(pools, dict):
        errors.append("draw_pools 必须是对象")
    elif len(pools) > MAX_POOLS:
        errors.append(f"draw_pools 数量超限 >{MAX_POOLS}")
    else:
        for name, entries in pools.items():
            if not isinstance(entries, list) or not entries:
                errors.append(f"draw_pools.{name} 需要非空列表")
                continue
            if len(entries) > MAX_POOL_ENTRIES:
                errors.append(f"draw_pools.{name} 条目超限 >{MAX_POOL_ENTRIES}")
                continue
            for i, e in enumerate(entries):
                if not isinstance(e, dict) or not isinstance(e.get("item"), str):
                    errors.append(f"draw_pools.{name}[{i}] 需要 item 字符串")
                    continue
                if not _is_num(e.get("w")):
                    errors.append(f"draw_pools.{name}[{i}].w 必须是数字")
                check_item_ref(e["item"])

    # ---- synthesize ----
    syn = pack.get("synthesize") or {}
    if not isinstance(syn, dict):
        errors.append("synthesize 必须是对象")
    elif len(syn) > MAX_SYNTH:
        errors.append(f"synthesize 数量超限 >{MAX_SYNTH}")
    else:
        for name, spec in syn.items():
            if not isinstance(spec, dict):
                errors.append(f"synthesize.{name} 必须是对象")
                continue
            inputs = spec.get("inputs")
            output = spec.get("output")
            if not isinstance(inputs, list) or not (1 <= len(inputs) <= MAX_SYNTH_INPUTS):
                errors.append(f"synthesize.{name}.inputs 需 1-{MAX_SYNTH_INPUTS} 项")
                continue
            for i, inp in enumerate(inputs):
                if not isinstance(inp, dict) or not isinstance(inp.get("item"), str):
                    errors.append(f"synthesize.{name}.inputs[{i}] 需要 item")
                    continue
                if not (isinstance(inp.get("qty"), int) and inp["qty"] >= 1):
                    errors.append(f"synthesize.{name}.inputs[{i}].qty 需正整数")
                check_item_ref(inp["item"])
            if not isinstance(output, dict) or not isinstance(output.get("item"), str):
                errors.append(f"synthesize.{name}.output 需要 item")
            else:
                check_item_ref(output["item"])
                if not (isinstance(output.get("qty"), int) and output["qty"] >= 1):
                    errors.append(f"synthesize.{name}.output.qty 需正整数")
            if not _is_num(spec.get("chance")):
                errors.append(f"synthesize.{name}.chance 必须是数字")

    return errors
