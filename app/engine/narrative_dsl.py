"""v0.2 单人规则格式。模型只选择动作，所有效果来自已验收的剧本。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Condition(Spec):
    location: Optional[str] = None
    flags: dict[str, bool] = Field(default_factory=dict)
    items: dict[str, int] = Field(default_factory=dict)
    minute: int = Field(default=0, ge=0, le=43200)


class Effect(Spec):
    hp: int = Field(default=0, ge=-100, le=100)
    xp: int = Field(default=0, ge=0, le=100)
    items: dict[str, int] = Field(default_factory=dict)
    flags: dict[str, bool] = Field(default_factory=dict)
    relationships: dict[str, int] = Field(default_factory=dict)
    clues: list[str] = Field(default_factory=list, max_length=10)
    ending: str = Field(default="", max_length=300)


class Location(Spec):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    exits: dict[str, int] = Field(default_factory=dict)


class Interaction(Spec):
    cultivation_action: Optional[Literal["meditate", "breakthrough", "pill", "heal", "gather", "mine", "alchemy", "forge", "hunt", "ruins", "cave"]] = None
    aliases: list[str] = Field(default_factory=list, max_length=30)
    repeat_text: str = Field(default="", max_length=1000)
    verbatim: bool = False
    label: str = Field(min_length=1, max_length=120)
    requires: Condition = Field(default_factory=Condition)
    minutes: int = Field(default=5, ge=1, le=60)
    cost: dict[str, int] = Field(default_factory=dict)
    check: Optional[str] = None
    success: Effect = Field(default_factory=Effect)
    failure: Effect = Field(default_factory=Effect)
    success_text: str = Field(min_length=1, max_length=1000)
    failure_text: str = Field(default="行动未成功。", max_length=1000)
    once: bool = True


class Anchor(Spec):
    requires: Condition = Field(default_factory=Condition)
    text: str = Field(min_length=1, max_length=1000)
    effect: Effect = Field(default_factory=Effect)
    pause: bool = False
    resume_flag: Optional[str] = None


class NPCWorldChange(Spec):
    location: str
    activity: str = Field(min_length=1, max_length=120)
    fear_delta: int = Field(default=0, ge=-100, le=100)


class StreamVariant(Spec):
    flags: dict[str, bool] = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=1500)
    npcs: dict[str, NPCWorldChange] = Field(default_factory=dict, max_length=20)


class StreamBeat(Spec):
    text: str = Field(min_length=1, max_length=1500)
    minutes: int = Field(default=1, ge=1, le=10)
    weather: Optional[str] = Field(default=None, max_length=80)
    npcs: dict[str, NPCWorldChange] = Field(default_factory=dict, max_length=20)
    intervention: bool = False
    variants: list[StreamVariant] = Field(default_factory=list, max_length=10)
    minimum_world_autonomy: int = Field(default=0, ge=0, le=100)
    player_detail: str = Field(default='', max_length=150)


class StreamSpec(Spec):
    loop: bool = True
    interval_seconds: Optional[int] = Field(default=None, ge=8, le=120)
    world_autonomy: int = Field(default=95, ge=0, le=100)
    player_autonomy: int = Field(default=30, ge=0, le=100)
    narrative_autonomy: int = Field(default=85, ge=0, le=100)
    scenes: dict[str, list[StreamBeat]] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def bounded(self):
        if any(not beats or len(beats) > 20 for beats in self.scenes.values()):
            raise ValueError('每个地点需要1-20个世界事件')
        from ..ai.prose_contract import validate_prose
        for beats in self.scenes.values():
            for beat in beats:
                validate_prose(beat.text)
                for variant in beat.variants:
                    validate_prose(variant.text)
                if beat.player_detail:
                    validate_prose(beat.player_detail)
        return self


class NarrativeSpec(Spec):
    version: Literal[2] = 2
    sandbox: Optional[Literal["cultivation"]] = None
    stream: Optional[StreamSpec] = None
    start: str
    opening: str = Field(min_length=1, max_length=2000)
    locations: dict[str, Location] = Field(min_length=1, max_length=100)
    interactions: dict[str, Interaction] = Field(default_factory=dict, max_length=200)
    anchors: dict[str, Anchor] = Field(default_factory=dict, max_length=100)
    inventory: dict[str, int] = Field(default_factory=dict)
    max_hp: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def references(self):
        if self.stream and self.anchors:
            raise ValueError('叙事流事件不能混用旧版时间锚点，请将世界事件写入 stream')
        if self.start not in self.locations:
            raise ValueError("start 必须引用已有地点")
        for loc in self.locations.values():
            for target, minutes in loc.exits.items():
                if target not in self.locations or not 1 <= minutes <= 60:
                    raise ValueError("出口必须引用已有地点，旅行时间为 1-60 分钟")
        for obj in list(self.interactions.values()) + list(self.anchors.values()):
            if obj.requires.location and obj.requires.location not in self.locations:
                raise ValueError("条件引用了不存在的地点")
            if any(q < 1 or q > 999 for q in obj.requires.items.values()):
                raise ValueError("条件物品数量必须为 1-999")
        for anchor in self.anchors.values():
            if anchor.pause and not anchor.resume_flag:
                raise ValueError("暂停锚点必须指定 resume_flag")
        quantities = [self.inventory] + [i.cost for i in self.interactions.values()]
        if any(q < 1 or q > 999 for inv in quantities for q in inv.values()):
            raise ValueError("初始物品和消耗数量必须为 1-999")
        effects = [a.effect for a in self.anchors.values()]
        effects += [e for i in self.interactions.values() for e in (i.success, i.failure)]
        if self.sandbox:
            if self.anchors or any(e.ending for e in effects):
                raise ValueError("修仙沙盒不允许强制剧情锚点或结局")
            if any(i.once or i.check for i in self.interactions.values()):
                raise ValueError("修仙沙盒交互必须可重复，检定由修仙规则统一处理")
        elif any(i.cultivation_action for i in self.interactions.values()):
            raise ValueError("修仙动作仅能用于 cultivation 沙盒")
        for effect in effects:
            if any(abs(q) > 999 for q in effect.items.values()):
                raise ValueError("物品变化超限")
            if any(abs(q) > 100 for q in effect.relationships.values()):
                raise ValueError("关系变化超限")
        return self


def parse_spec(content: dict) -> NarrativeSpec:
    if content.get("mode") != "single":
        raise ValueError("narrative 运行时仅支持单人剧本")
    spec = NarrativeSpec.model_validate(content["narrative"])
    checks = (content.get("rules") or {}).get("checks", {})
    item_ids = {i["id"] for i in content.get("items", [])}
    if spec.sandbox and not {"stone", "herb", "ore", "qi_pill", "heal_pill", "talisman"} <= item_ids:
        raise ValueError("修仙沙盒缺少基础物品模板")
    npc_ids = {n["id"] for n in content.get("npcs", [])}
    if spec.stream:
        if set(spec.stream.scenes) - set(spec.locations):
            raise ValueError('叙事流必须引用已知地点')
        for beats in spec.stream.scenes.values():
            for beat in beats:
                for event in [beat, *beat.variants]:
                    if set(event.npcs) - npc_ids or any(n.location not in spec.locations for n in event.npcs.values()):
                        raise ValueError('世界事件必须引用已知 NPC 和地点')
    for interaction in spec.interactions.values():
        if interaction.check and interaction.check not in checks:
            raise ValueError("交互引用了不存在的检定")
    inventories = [spec.inventory]
    effects = [a.effect for a in spec.anchors.values()]
    for obj in list(spec.interactions.values()) + list(spec.anchors.values()):
        inventories.append(obj.requires.items)
    for interaction in spec.interactions.values():
        inventories.append(interaction.cost)
        effects.extend([interaction.success, interaction.failure])
    for effect in effects:
        inventories.append(effect.items)
        if set(effect.relationships) - npc_ids:
            raise ValueError("关系效果引用了不存在的 NPC")
    if any(set(inv) - item_ids for inv in inventories):
        raise ValueError("物品引用必须存在于 items 模板")
    return spec
