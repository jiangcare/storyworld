"""每日模式的模型输出验收；不允许任意字段、造属性或修改平台状态。"""
from __future__ import annotations

import copy
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .policy import has_control_instruction

ShortText = Annotated[str, Field(min_length=1, max_length=120)]
NoteText = Annotated[str, Field(max_length=300)]


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def no_instructions(self):
        def visit(value):
            if isinstance(value, str) and has_control_instruction(value):
                raise ValueError("模型输出包含游戏外控制指令")
            if isinstance(value, dict):
                for k, v in value.items():
                    visit(k)
                    visit(v)
            if isinstance(value, list):
                for v in value:
                    visit(v)
        visit(self.model_dump())
        return self


class Changes(Output):
    hp_delta: int = Field(default=0, ge=-10, le=10)
    items_added: list[ShortText] = Field(default_factory=list, max_length=5)
    items_removed: list[ShortText] = Field(default_factory=list, max_length=5)
    clues_added: list[ShortText] = Field(default_factory=list, max_length=5)
    abilities_added: list[ShortText] = Field(default_factory=list, max_length=3)
    tasks_done: list[ShortText] = Field(default_factory=list, max_length=3)
    flag_set: dict[ShortText, bool] = Field(default_factory=dict, max_length=5)
    notes: dict[ShortText, NoteText] = Field(default_factory=dict, max_length=5)


class NarrativeOutput(Output):
    narrative: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class SceneOutput(NarrativeOutput):
    suggested_actions: list[ShortText] = Field(default_factory=list, max_length=4)
    state_changes: Changes = Field(default_factory=Changes)
    scene_ended: bool = False


class WorldOutput(Output):
    public_broadcast: str = Field(min_length=1, max_length=1500)
    canon_additions: list[NoteText] = Field(default_factory=list, max_length=6)
    countdown_update: NoteText = ""
    chapter_note: NoteText = ""
    world_ended: bool = False


def validate_scene(data: dict, content: dict, state: dict, day: int) -> dict:
    scene = SceneOutput.model_validate(data).model_dump()
    changes = scene["state_changes"]
    def names(key):
        return {row.get(field) for row in content.get(key, []) for field in ("id", "name")}
    if set(changes["items_added"]) - names("items"):
        raise ValueError("物品必须引用剧本模板")
    if set(changes["abilities_added"]) - names("abilities"):
        raise ValueError("能力必须引用剧本模板")
    if set(changes["items_removed"]) - (names("items") | set(state.get("items", []))):
        raise ValueError("不能移除未知物品")
    flags = {row.get("flag") for row in content.get("mainline", []) if row.get("day", 1) <= day}
    if set(changes["flag_set"]) - flags:
        raise ValueError("不能写入未到达的剧情标志")
    # 老剧本没有 max_hp 字段时使用初始生命 10，已有高生命存档不会被强制削减。
    cap = max(state.get("max_hp", 10), state.get("hp", 10))
    if state.get("hp", 10) + changes["hp_delta"] > cap:
        raise ValueError("生命变化超过角色上限")
    # 模板 ID 归一为名称，避免缓存同时出现 ID/名称，也不允许重复条目放大奖励。
    for key, source in (("items_added", "items"), ("items_removed", "items"), ("abilities_added", "abilities")):
        aliases = {row.get(field): row["name"] for row in content.get(source, []) for field in ("id", "name")}
        changes[key] = list(dict.fromkeys(aliases.get(value, value) for value in changes[key]))
    return scene


def public_script(content: dict) -> dict:
    """导演/编剧共享的剧本视图，不含其他角色或 NPC 的秘密。"""
    value = copy.deepcopy(content)
    for key in ("player_cards", "npcs"):
        value[key] = [{k: row[k] for k in ("id", "name", "role", "public_desc", "relation") if k in row}
                      for row in value.get(key, [])]
    return value
