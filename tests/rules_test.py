"""规则引擎测试：表达式/DSL/运行时操作/复杂度评估/验收闸门/AI 归一化。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

from app.rules import complexity, dsl, engine, expr

# 样例规则包（与 seed 中的末日剧本一致，纯数据）
SAMPLE_PACK = {
    "constants": {"base_dodge": 0.05, "base_attack": 0.5},
    "checks": {
        "attack_success": {"formula": "clamp(const.base_attack + (attr.strength - attr.agility) * 0.04 + stat.level * 0.02, 0.05, 0.95)"},
        "dodge": {"formula": "const.base_dodge + attr.agility * 0.03"},
    },
    "percent_mods": {
        "sword_damage": {"base": 6, "mods": [{"when": "ability.夜视", "pct": 0.25}, {"when": "stat.hp < 3", "pct": 0.2}]},
    },
    "forge": {"sharpen": {"success": {"formula": "max(0.85 - stat.level * 0.12, 0.05)"}}},
    "draw_pools": {
        "crate": [
            {"item": "bandage", "w": 35}, {"item": "alcohol", "w": 25},
            {"item": "iron_ore", "w": 20}, {"item": "charcoal", "w": 15},
            {"item": "iron_sword", "w": 5},
        ]
    },
    "synthesize": {
        "medkit": {"inputs": [{"item": "bandage", "qty": 2}, {"item": "alcohol", "qty": 1}],
                   "output": {"item": "medkit", "qty": 1}, "chance": 0.9}
    },
}
DEFS = {"items": {k: {} for k in ("bandage", "alcohol", "iron_ore", "charcoal", "iron_sword", "medkit")}}


def ctx(**kw):
    defaults = dict(
        attr={"strength": 5, "agility": 3},
        stat={"hp": 10, "day": 2, "level": 1},
        item_count={"bandage": 2, "alcohol": 1},
        ability={"夜视": True},
        constants=SAMPLE_PACK["constants"],
        seed=7,
    )
    defaults.update(kw)
    return engine.make_ctx(**defaults)


def test_expr():
    ns = {"attr": {"strength": 5, "agility": 3}, "stat": {"hp": 7},
          "const": {"x": 2}, "ability": {"剑术": True}, "item_count": {"a": 1}}
    assert engine._to_prob(expr.eval_expr("clamp(0.5+(attr.strength-attr.agility)*0.06,0.05,0.95)", ns)) == 0.62
    assert expr.eval_expr("stat.hp < 3 and attr.strength >= 4", ns) is False
    assert expr.eval_expr("ability.剑术", ns) is True
    assert expr.eval_expr("const.x * 10", ns) == 20.0
    assert expr.eval_expr("not (1 > 2) and true", ns) is True
    assert expr.eval_expr("stat.missing + 1", ns) == 1.0  # 缺失键 → 0
    # 语法/安全拒绝
    for bad in ("while true", "evil.func()", "sqrt()", "randint(1)", "1 ++ 2"):
        try:
            expr.compile_expr(bad)
            raise AssertionError(f"应拒绝: {bad}")
        except expr.RuleParseError:
            pass
    # 步数预算
    try:
        expr.eval_expr("1+1+1+1+1+1+1+1", ns, budget=4)
        raise AssertionError("应超预算")
    except expr.RuleBudgetExceeded:
        pass
    print("[OK] expr: 算术/逻辑/CJK/缺失键/安全拒绝/预算")


def test_dsl():
    assert dsl.validate_rule_pack(SAMPLE_PACK, DEFS) == []
    bad = dict(SAMPLE_PACK)
    bad["hack"] = {}
    assert any("未知键" in e for e in dsl.validate_rule_pack(bad, DEFS))
    bad2 = dict(SAMPLE_PACK)
    bad2["checks"] = {"x": {"formula": "while 1"}}
    assert any("解析失败" in e for e in dsl.validate_rule_pack(bad2, DEFS))
    bad3 = dict(SAMPLE_PACK)
    bad3["draw_pools"] = {"huge": [{"item": f"i{n}", "w": 1} for n in range(dsl.MAX_POOL_ENTRIES + 1)]}
    assert any("超限" in e for e in dsl.validate_rule_pack(bad3, DEFS))
    bad4 = dict(SAMPLE_PACK)
    bad4["synthesize"] = {"x": {"inputs": [{"item": "不存在的东西", "qty": 1}], "output": {"item": "medkit", "qty": 1}, "chance": 1}}
    assert any("物品引用不存在" in e for e in dsl.validate_rule_pack(bad4, DEFS))
    print("[OK] dsl: 结构校验/未知键/解析失败/上限/引用检查")


def test_engine():
    c = ctx()
    chance, ok = engine.check(SAMPLE_PACK, "attack_success", c)
    assert 0.05 <= chance <= 0.95
    # 同种子确定性
    c2 = ctx()
    _, ok2 = engine.check(SAMPLE_PACK, "attack_success", c2)
    assert ok == ok2
    # 重复 300 次：成功率接近概率（±8%）
    hits = sum(1 for _ in range(300) if engine.check(SAMPLE_PACK, "attack_success", ctx(seed=random.randint(1, 99999)))[1])
    p = engine.check_chance(SAMPLE_PACK, "attack_success", ctx())
    assert abs(hits / 300 - p) < 0.08, (hits, p)
    # 强化成功率随等级递减
    c0, _, _ = None, None, None
    for lv in (0, 3, 8):
        ch, _ = engine.forge(SAMPLE_PACK, "sharpen", ctx(stat={"hp": 10, "day": 2, "level": lv}), lv)
        assert 0.05 <= ch <= 0.85
    ch0, _ = engine.forge(SAMPLE_PACK, "sharpen", ctx(), 0)
    ch8, _ = engine.forge(SAMPLE_PACK, "sharpen", ctx(), 8)
    assert ch0 > ch8, "强化等级越高成功率越低"
    # 抽奖：2000 次分布近似权重
    counts = {}
    for _ in range(2000):
        item = engine.draw(SAMPLE_PACK, "crate", ctx(seed=random.randint(1, 99999)))
        counts[item] = counts.get(item, 0) + 1
    assert abs(counts.get("bandage", 0) / 2000 - 0.35) < 0.04
    assert abs(counts.get("iron_sword", 0) / 2000 - 0.05) < 0.03
    # 合成：可行+概率；缺材料
    inv = {"bandage": 2, "alcohol": 1}
    r = engine.synthesize(SAMPLE_PACK, "medkit", inv, ctx(seed=1))
    assert r["ok"] is True and r["consumed"] == {"bandage": 2, "alcohol": 1}
    r2 = engine.synthesize(SAMPLE_PACK, "medkit", {"bandage": 2}, ctx())
    assert r2["ok"] is False and r2["missing"] == ["alcohol"]
    # 百分比加成：夜视 +25%
    mult = engine.percent_mods_total(SAMPLE_PACK, "sword_damage", ctx())
    assert abs(mult - 1.25) < 1e-9
    mult2 = engine.percent_mods_total(SAMPLE_PACK, "sword_damage", ctx(ability={}))
    assert mult2 == 1.0
    print("[OK] engine: 检定确定性/概率分布/强化曲线/抽奖分布/合成/百分比加成")


def test_complexity():
    ok, rep = complexity.rules_acceptable(SAMPLE_PACK)
    assert ok and rep["total_worst_ops"] > 0 and rep["grade"] == "bounded"
    kinds = {r["kind"] for r in rep["rules"]}
    assert "check" in kinds and "draw" in kinds and "synthesize" in kinds
    # 巨型抽奖池 → 拒绝
    huge = dict(SAMPLE_PACK)
    huge["draw_pools"] = {"huge": [{"item": f"i{n}", "w": 1} for n in range(dsl.MAX_POOL_ENTRIES + 1)]}
    ok2, rep2 = complexity.rules_acceptable(huge)
    assert not ok2 and any("超限" in v for v in rep2["violations"])
    # 超长表达式 → 节点数超限（括号不产生节点，用长加法链构造深 AST）
    deep = dict(SAMPLE_PACK)
    deep["checks"] = {"x": {"formula": "+".join(["1"] * 260)}}
    ok3, rep3 = complexity.rules_acceptable(deep)
    assert not ok3 and any("节点数" in v for v in rep3["violations"]), rep3
    # 空规则通过
    ok4, _ = complexity.rules_acceptable({})
    assert ok4
    print("[OK] complexity: 静态评估/等级/巨型结构拒绝/验收闸门")


def test_script_normalize_acceptance():
    from app.ai import script_ai

    good = {
        "title": "T", "mode": "multi", "days": 3, "world": {"name": "W", "background": "B"},
        "chapters": [{"day_start": 1, "day_end": 3, "title": "C", "goal": "G", "events": []}],
        "player_cards": [{"id": "p1", "name": "A", "stats": {}}],
        "items": [{"id": "sw", "name": "剑"}],
        "rules": SAMPLE_PACK,
    }
    out = script_ai._normalize(good, "multi")
    assert out["rules"] == SAMPLE_PACK and out["_acceptance"]["rules"]["ok"] is True
    assert out["items"][0]["id"] == "sw"

    bad = dict(good)
    bad["rules"] = {"checks": {"x": {"formula": "evil_func(1)"}}}  # 未白名单函数
    out2 = script_ai._normalize(bad, "multi")
    assert out2["rules"] == {} and out2["_acceptance"]["rules"]["ok"] is False
    print("[OK] script_ai: 规则包验收通过保留 / 失败自动回退为空规则")


if __name__ == "__main__":
    test_expr()
    test_dsl()
    test_engine()
    test_complexity()
    test_script_normalize_acceptance()
    print("\n===== RULES TEST ALL PASSED =====")
