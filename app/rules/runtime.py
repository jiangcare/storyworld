"""版本化、有限构件的功法规则。没有 eval、自由公式、递归触发或任意状态路径。"""
from __future__ import annotations
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

KERNEL_VERSION = 1
ELEMENTS = ('water', 'fire', 'metal', 'wood', 'earth', 'neutral')
COUNTERS = {'water': 'fire', 'fire': 'metal', 'metal': 'wood', 'wood': 'earth', 'earth': 'water'}
ELEMENT_NAMES = dict(zip(ELEMENTS, ('水', '火', '金', '木', '土', '无属')))
PHASES = ('eligibility', 'cost', 'interaction', 'impact', 'reflection')


class RuleError(ValueError):
    """可向玩家解释的固定规则错误；不携带模型原文。"""


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Ability(Strict):
    name: str = Field(min_length=1, max_length=24)
    description: str = Field(min_length=1, max_length=180)
    element: Literal['water', 'fire', 'metal', 'wood', 'earth', 'neutral']
    form: Literal['strike', 'ward']
    cost: int = Field(ge=1, le=10)
    power: int = Field(ge=1, le=20)
    penetration: int = Field(default=0, ge=0, le=8)
    reflection: int = Field(default=0, ge=0, le=50)

    @model_validator(mode='after')
    def semantics(self):
        from ..ai.policy import has_control_instruction
        if has_control_instruction(self.name + '\n' + self.description):
            raise ValueError('功法描述含控制指令')
        if self.form == 'strike' and self.reflection:
            raise ValueError('只有护持能力可以反射')
        if self.form == 'ward' and self.penetration:
            raise ValueError('只有攻击能力可以穿透')
        if self.points > self.cost * 3:
            raise ValueError('能力效果超过灵力效率上限')
        return self

    @property
    def points(self):
        return self.power + 2 * self.penetration + (self.reflection + 9) // 10


class Source(Strict):
    name: str = Field(min_length=1, max_length=24)
    description: str = Field(min_length=1, max_length=240)
    location: str = Field(min_length=1, max_length=40)
    element: Literal['water', 'fire', 'metal', 'wood', 'earth', 'neutral', 'random']
    form: Literal['strike', 'ward', 'random'] = 'strike'
    tier: int = Field(default=1, ge=1, le=3)
    min_rank: int = Field(default=0, ge=0, le=30)


class Trial(Strict):
    name: str = Field(min_length=1, max_length=24)
    location: str = Field(min_length=1, max_length=40)
    opponent: Ability


class RuntimeSpec(Strict):
    kernel: Literal[1] = 1
    sources: dict[str, Source] = Field(min_length=1, max_length=20)
    channel_locations: list[str] = Field(min_length=1, max_length=20)
    trials: dict[str, Trial] = Field(default_factory=dict, max_length=10)

    @model_validator(mode='after')
    def identities(self):
        import re
        if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,23}', key) for key in [*self.sources, *self.trials]):
            raise ValueError('规则来源与试法场需稳定的短 ID')
        if len({s.name for s in self.sources.values()}) != len(self.sources):
            raise ValueError('功法来源名称不能重复')
        from ..ai.policy import has_control_instruction
        if any(has_control_instruction(s.name + s.description) for s in self.sources.values()):
            raise ValueError('规则来源含控制指令')
        if any(t.opponent.points > 24 for t in self.trials.values()):
            raise ValueError('试法能力超出本阶段力量上限')
        return self


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def envelope(source: Source, roll: int):
    """抽取在调用模型前由引擎完成并保存；模型只能在相同档位中设计能力。"""
    return {'kernel': KERNEL_VERSION, 'element': ELEMENTS[roll % 5] if source.element == 'random' else source.element,
            'form': ('strike', 'ward')[(roll // 5) % 2] if source.form == 'random' else source.form,
            'max_points': 8 * source.tier, 'name': source.name}


def validate_proposal(data, limits):
    ability = Ability.model_validate(data)
    if (limits['kernel'] != KERNEL_VERSION or ability.name != limits['name'] or
            ability.element != limits['element'] or ability.form != limits['form'] or ability.points > limits['max_points']):
        raise RuleError('这段功法推演超出了原有记载，尚不能据此修习。')
    # 固定边界演算；相同规则用于试法双方，拒绝资源不足时执行。
    for qi in (0, ability.cost - 1, ability.cost, 100):
        outcome = resolve_exchange(ability, ability, qi, qi)
        if qi < ability.cost and outcome['executed']:
            raise RuleError('功法消耗未通过边界检查。')
        if outcome['executed'] and any(v < 0 for v in outcome['qi_after']):
            raise RuleError('功法消耗未通过边界检查。')
    return ability


def resolve_exchange(left: Ability, right: Ability, left_qi: int, right_qi: int):
    """同时结算一个已确定的交锋；反射只结算一次，不再次触发反射。

    元素克制只将对应能力的有效强度下调四分之一，不产生绝对免疫。
    输出 damage 按左右受击方索引，所有代价同时承诺或均不执行。
    """
    left, right = Ability.model_validate(left.model_dump()), Ability.model_validate(right.model_dump())
    if any(type(q) is not int or not 0 <= q <= 100 for q in (left_qi, right_qi)):
        raise RuleError('灵力记录不符合当前规则。')
    costs = [left.cost, right.cost]
    if left_qi < costs[0] or right_qi < costs[1]:
        return {'kernel': KERNEL_VERSION, 'executed': False, 'phases': list(PHASES[:1]),
                'qi_after': [left_qi, right_qi], 'damage': [0, 0], 'reason': 'insufficient_qi'}
    pair = [left, right]
    powers = [a.power for a in pair]
    weakened = []
    for i, ability in enumerate(pair):
        if COUNTERS.get(pair[1-i].element) == ability.element:
            powers[i] = max(1, powers[i] * 3 // 4)
            weakened.append(i)
    damage = [0, 0]
    reflected = [0, 0]
    blocked = [0, 0]
    for i, attack in enumerate(pair):
        if attack.form != 'strike':
            continue
        j = 1 - i
        defense = max(0, powers[j] - attack.penetration) if pair[j].form == 'ward' else 0
        blocked[j] = min(powers[i], defense)
        damage[j] = powers[i] - blocked[j]
        reflected[i] = blocked[j] * pair[j].reflection // 100
    return {'kernel': KERNEL_VERSION, 'executed': True, 'phases': list(PHASES),
            'qi_after': [left_qi - costs[0], right_qi - costs[1]],
            'damage': [damage[i] + reflected[i] for i in (0, 1)],
            'reflected': reflected, 'blocked': blocked, 'effective_power': powers, 'weakened': weakened}
