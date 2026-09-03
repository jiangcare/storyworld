"""安全表达式求值器：规则 DSL 的基础。

设计约束（防资源耗尽的关键）：
- 无 while / for / 递归 —— 语法上就不存在，复杂度天然有界
- 白名单函数（全部 O(1)）
- 运行时步数预算，超限熔断
"""
from __future__ import annotations

import random
import re
from typing import Any, Optional

# ---------- 词法 ----------

_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<NUM>\d+\.?\d*|\.\d+)"
    r"|(?P<KEYWORD>and|or|not|true|True|false|False)"
    r"|(?P<IDENT>[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_.\u4e00-\u9fff]*)"
    r"|(?P<OP>[+\-*/%])"
    r"|(?P<CMP>==|!=|<=|>=|<|>)"
    r"|(?P<LP>\()|(?P<RP>\))|(?P<CM>,)"
    r")"
)

_KEYWORD_MAP = {
    "and": "AND", "or": "OR", "not": "NOT",
    "true": "TRUE", "True": "TRUE",
    "false": "FALSE", "False": "FALSE",
}

_FUNC_WHITELIST = {
    "abs": 1, "min": 2, "max": 2, "clamp": 3,
    "floor": 1, "ceil": 1, "round": 1,
    "sqrt": 1,
}


class RuleParseError(ValueError):
    pass


class RuleBudgetExceeded(RuntimeError):
    pass


# ---------- 语法树 ----------

class _Num:
    __slots__ = ("value",)

    def __init__(self, value: float):
        self.value = value


class _Bool:
    __slots__ = ("value",)

    def __init__(self, value: bool):
        self.value = value


class _Path:
    """attr.x / stat.x / const.x / item_count.x / ability.x"""

    __slots__ = ("path",)

    def __init__(self, path: str):
        self.path = path


class _Unary:
    __slots__ = ("op", "node")

    def __init__(self, op: str, node):
        self.op = op
        self.node = node


class _Bin:
    __slots__ = ("op", "left", "right")

    def __init__(self, op: str, left, right):
        self.op = op
        self.left = left
        self.right = right


class _Call:
    __slots__ = ("name", "args")

    def __init__(self, name: str, args: list):
        self.name = name
        self.args = args


class _Ctx:
    """求值上下文：命名空间 + 随机源 + 步数预算。"""

    def __init__(
        self,
        namespaces: Optional[dict] = None,
        rng: Optional[random.Random] = None,
        budget: int = 200_000,
    ):
        self.ns = namespaces or {}
        self.rng = rng or random.Random()
        self.budget = budget
        self.steps = 0

    def tick(self) -> None:
        self.steps += 1
        if self.steps > self.budget:
            raise RuleBudgetExceeded(f"规则执行超过步数预算 {self.budget}")

    def resolve(self, path: str) -> Any:
        parts = path.split(".")
        ns = parts[0]
        if ns not in ("attr", "stat", "const", "item_count", "ability"):
            raise RuleParseError(f"未知命名空间: {ns}")
        table = self.ns.get(ns) or {}
        if len(parts) == 1:
            raise RuleParseError(f"标识符缺少属性: {path}")
        cur: Any = table
        for p in parts[1:]:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return 0.0 if ns in ("attr", "stat", "const", "item_count") else False
        if isinstance(cur, bool) or isinstance(cur, (int, float)):
            return cur
        return float(cur) if ns in ("attr", "stat", "const", "item_count") else bool(cur)


# ---------- 解析 ----------

class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN_RE.match(text, pos)
            if not m:
                raise RuleParseError(f"无法解析的位置 {pos}: ...{text[pos:pos+20]!r}")
            pos = m.end()
            kind = m.lastgroup
            val = m.group(kind)
            if kind == "KEYWORD":
                kind = _KEYWORD_MAP[val]
            self.tokens.append((kind, val))
        self.pos = 0

    def peek(self) -> Optional[tuple[str, str]]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise RuleParseError("表达式意外结束")
        self.pos += 1
        return tok

    def expect(self, kind: str) -> tuple[str, str]:
        tok = self.next()
        if tok[0] != kind:
            raise RuleParseError(f"期望 {kind}，得到 {tok[0]}({tok[1]})")
        return tok

    def parse(self):
        node = self.parse_or()
        if self.peek() is not None:
            raise RuleParseError(f"表达式尾部有多余内容: {self.peek()}")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek() and self.peek()[0] == "OR":
            self.next()
            node = _Bin("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_cmp()
        while self.peek() and self.peek()[0] == "AND":
            self.next()
            node = _Bin("and", node, self.parse_cmp())
        return node

    def parse_cmp(self):
        node = self.parse_add()
        while self.peek() and self.peek()[0] == "CMP":
            op = self.next()[1]
            node = _Bin(op, node, self.parse_add())
        return node

    def parse_add(self):
        node = self.parse_mul()
        while self.peek() and self.peek()[0] == "OP" and self.peek()[1] in ("+", "-"):
            op = self.next()[1]
            node = _Bin(op, node, self.parse_mul())
        return node

    def parse_mul(self):
        node = self.parse_unary()
        while self.peek() and self.peek()[0] == "OP" and self.peek()[1] in ("*", "/", "%"):
            op = self.next()[1]
            node = _Bin(op, node, self.parse_unary())
        return node

    def parse_unary(self):
        tok = self.peek()
        if tok and tok[0] == "OP" and tok[1] == "-":
            self.next()
            return _Unary("-", self.parse_unary())
        if tok and tok[0] == "NOT":
            self.next()
            return _Unary("not", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        tok = self.next()
        kind, val = tok
        if kind == "NUM":
            return _Num(float(val))
        if kind == "TRUE":
            return _Bool(True)
        if kind == "FALSE":
            return _Bool(False)
        if kind == "IDENT":
            if self.peek() and self.peek()[0] == "LP":
                self.next()
                args: list = []
                if not (self.peek() and self.peek()[0] == "RP"):
                    args.append(self.parse_or())
                    while self.peek() and self.peek()[0] == "CM":
                        self.next()
                        args.append(self.parse_or())
                self.expect("RP")
                if val not in _FUNC_WHITELIST:
                    raise RuleParseError(f"函数不在白名单: {val}")
                if len(args) != _FUNC_WHITELIST[val]:
                    raise RuleParseError(f"{val} 需要 {_FUNC_WHITELIST[val]} 个参数")
                return _Call(val, args)
            return _Path(val)
        if kind == "LP":
            node = self.parse_or()
            self.expect("RP")
            return node
        raise RuleParseError(f"意外的记号: {kind}({val})")


# ---------- 编译与求值 ----------

def compile_expr(text: str):
    """解析表达式，返回可调用对象 (ctx) -> 数值/布尔。"""
    node = _Parser(text).parse()
    return _make_ev(node)


def node_count(text: str) -> int:
    """表达式 AST 节点数（复杂度估算用，解析失败抛 RuleParseError）。"""
    node = _Parser(text).parse()
    return _count(node)


def _count(node) -> int:
    if isinstance(node, (_Num, _Bool, _Path)):
        return 1
    if isinstance(node, _Unary):
        return 1 + _count(node.node)
    if isinstance(node, _Bin):
        return 1 + _count(node.left) + _count(node.right)
    if isinstance(node, _Call):
        return 1 + sum(_count(a) for a in node.args)
    raise AssertionError(node)


def _make_ev(node):
    if isinstance(node, _Num):
        return lambda ctx: node.value
    if isinstance(node, _Bool):
        return lambda ctx: node.value
    if isinstance(node, _Path):

        def ev_path(ctx, _p=node.path):
            ctx.tick()
            return ctx.resolve(_p)

        return ev_path
    if isinstance(node, _Unary):

        def ev_unary(ctx, _op=node.op, _n=node.node):
            ctx.tick()
            v = _make_ev(_n)(ctx)
            if _op == "-":
                return -float(v)
            return not bool(v)

        return ev_unary
    if isinstance(node, _Bin):

        def ev_bin(ctx, _op=node.op, _l=node.left, _r=node.right):
            ctx.tick()
            a = _make_ev(_l)(ctx)
            b = _make_ev(_r)(ctx)
            if _op == "+":
                return float(a) + float(b)
            if _op == "-":
                return float(a) - float(b)
            if _op == "*":
                return float(a) * float(b)
            if _op == "/":
                return float(a) / float(b) if float(b) != 0 else 0.0
            if _op == "%":
                return float(a) % float(b) if float(b) != 0 else 0.0
            if _op == "==":
                return float(a) == float(b)
            if _op == "!=":
                return float(a) != float(b)
            if _op == "<":
                return float(a) < float(b)
            if _op == "<=":
                return float(a) <= float(b)
            if _op == ">":
                return float(a) > float(b)
            if _op == ">=":
                return float(a) >= float(b)
            if _op == "and":
                return bool(a) and bool(b)
            if _op == "or":
                return bool(a) or bool(b)
            raise AssertionError(_op)

        return ev_bin
    if isinstance(node, _Call):

        def ev_call(ctx, _name=node.name, _args=node.args):
            ctx.tick()
            vals = [_make_ev(a)(ctx) for a in _args]
            return _apply_func(_name, vals)

        return ev_call
    raise AssertionError(node)


def _apply_func(name: str, vals: list):
    v = [float(x) for x in vals]
    if name == "abs":
        return abs(v[0])
    if name == "min":
        return min(v)
    if name == "max":
        return max(v)
    if name == "clamp":
        lo, hi = v[1], v[2]
        return max(lo, min(hi, v[0]))
    if name == "floor":
        import math

        return math.floor(v[0])
    if name == "ceil":
        import math

        return math.ceil(v[0])
    if name == "round":
        return round(v[0])
    if name == "sqrt":
        import math

        return math.sqrt(max(v[0], 0.0))
    raise AssertionError(name)


def eval_expr(
    expr: str,
    namespaces: Optional[dict] = None,
    rng: Optional[random.Random] = None,
    budget: int = 200_000,
):
    """便捷求值入口（测试与引擎共用）。"""
    return compile_expr(expr)(_Ctx(namespaces, rng, budget))


# ---------- 随机原语（引擎层使用） ----------

def roll_percent(ctx: _Ctx, chance: float) -> bool:
    """按概率 chance∈[0,1] 判定成功。"""
    ctx.tick()
    return ctx.rng.random() < max(0.0, min(1.0, chance))


def pick_weighted(ctx: _Ctx, entries: list) -> Any:
    """加权随机取一项。entries: [{...w...}]；元素自身携带权重键。"""
    total = 0.0
    for e in entries:
        ctx.tick()
        total += max(0.0, float(e.get("w", 1.0)))
    if total <= 0:
        return entries[0] if entries else None
    roll = ctx.rng.random() * total
    acc = 0.0
    for e in entries:
        ctx.tick()
        acc += max(0.0, float(e.get("w", 1.0)))
        if roll < acc:
            return e
    return entries[-1] if entries else None
