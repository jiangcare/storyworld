"""规则包复杂度静态评估与验收闸门。

设计：规则语言无循环/无递归（语法上不存在），所有执行路径要么 O(1)，
要么 O(集合规模) 且集合规模有硬上限（dsl.MAX_*）。因此最坏总步数可静态计算。
"""
from __future__ import annotations

from . import dsl, expr

# 预算（可调）
EXPR_MAX_NODES = 200          # 单个表达式 AST 节点上限
TOTAL_MAX_OPS = 100_000       # 规则包最坏总步数上限


def assess_rules(pack: dict) -> dict:
    """评估规则包，返回报告：
    {ok, total_worst_ops, grade, rules: [{name, kind, worst_ops, grade}], violations: []}
    """
    if not pack:
        return {
            "ok": True,
            "total_worst_ops": 0,
            "grade": "empty",
            "rules": [],
            "violations": [],
        }
    rules_report: list = []
    violations: list = []
    total = 0

    def add(name: str, kind: str, ops: int, note: str = ""):
        nonlocal total
        total += ops
        rules_report.append(
            {"name": name, "kind": kind, "worst_ops": ops, "grade": note or _grade(ops)}
        )

    def expr_ops(rule_name: str, field: str, text: str):
        try:
            return expr.node_count(text)
        except expr.RuleParseError as e:
            violations.append(f"{rule_name}.{field} 表达式解析失败: {e}")
            return 0

    for name, rule in (pack.get("checks") or {}).items():
        formula = (rule or {}).get("formula", "") if isinstance(rule, dict) else ""
        n = expr_ops(f"checks.{name}", "formula", formula)
        if n > EXPR_MAX_NODES:
            violations.append(f"checks.{name}.formula 节点数 {n} 超限 >{EXPR_MAX_NODES}")
        add(f"checks.{name}", "check", n + 1)

    for name, group in (pack.get("percent_mods") or {}).items():
        mlist = (group or {}).get("mods", []) if isinstance(group, dict) else []
        ops = 1
        for i, m in enumerate(mlist or []):
            w = (m or {}).get("when", "") if isinstance(m, dict) else ""
            n = expr_ops(f"percent_mods.{name}", f"mods[{i}].when", w)
            if n > EXPR_MAX_NODES:
                violations.append(f"percent_mods.{name}.mods[{i}].when 节点数超限")
            ops += n + 1
        add(f"percent_mods.{name}", "percent_mods", ops,
            note=f"O(m) m={len(mlist or [])}")

    for name, spec in (pack.get("forge") or {}).items():
        f = ((spec or {}).get("success") or {}) if isinstance(spec, dict) else {}
        formula = f.get("formula", "") if isinstance(f, dict) else ""
        n = expr_ops(f"forge.{name}", "success.formula", formula)
        if n > EXPR_MAX_NODES:
            violations.append(f"forge.{name}.success.formula 节点数超限")
        add(f"forge.{name}", "forge", n + 1)

    for name, entries in (pack.get("draw_pools") or {}).items():
        n = len(entries) if isinstance(entries, list) else 0
        add(f"draw_pools.{name}", "draw", n + 1, note=f"O(pool) n={n}")

    for name, spec in (pack.get("synthesize") or {}).items():
        inputs = (spec or {}).get("inputs", []) if isinstance(spec, dict) else []
        n = len(inputs) if isinstance(inputs, list) else 0
        add(f"synthesize.{name}", "synthesize", n + 1, note=f"O(m) m={n}")

    total += len(pack.get("constants") or {})
    if total > TOTAL_MAX_OPS:
        violations.append(
            f"规则包最坏总步数 {total} 超限 >{TOTAL_MAX_OPS}，请精简规则"
        )
    if total > 0 and not violations:
        violations = []
    return {
        "ok": not violations,
        "total_worst_ops": total,
        "grade": "bounded" if total > 0 else "empty",
        "rules": rules_report,
        "violations": violations,
    }


def _grade(ops: int) -> str:
    return "O(1)" if ops <= EXPR_MAX_NODES else "O(bounded)"


def rules_acceptable(pack: dict) -> tuple[bool, dict]:
    """验收闸门：结构校验 + 复杂度评估同时通过才算可用。"""
    errors = dsl.validate_rule_pack(pack)
    report = assess_rules(pack)
    if errors:
        report["violations"] = [f"schema: {e}" for e in errors] + report["violations"]
        report["ok"] = False
    return report["ok"], report
