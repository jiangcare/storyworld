"""SQLAlchemy 数据模型（SQLite）。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now()


class RuntimeEntry(Base):
    """带过期时间的会话、草稿和租约；与游戏存档保存在同一个 SQLite 文件。"""

    __tablename__ = "runtime_entries"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)


class User(Base):
    """平台用户（platform + platform 用户ID 唯一）。"""

    __tablename__ = "users"
    __table_args__ = (Index("uq_platform_tgid", "platform", "tg_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), default="telegram", index=True)
    # telegram | qq | dingtalk | web | ...
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)  # 平台侧用户 ID
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AdminUser(Base):
    """后台管理系统账号。"""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Script(Base):
    """剧本。用户上传的草稿经 AI 完善后以 status=pending 进入审核队列。"""

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(32), default="")
    mode: Mapped[str] = mapped_column(String(16), default="multi")  # single | multi
    min_players: Mapped[int] = mapped_column(Integer, default=1)
    max_players: Mapped[int] = mapped_column(Integer, default=6)
    days: Mapped[int] = mapped_column(Integer, default=7)
    status: Mapped[str] = mapped_column(String(16), default="approved", index=True)
    # pending(待审核) | approved(已上架) | disabled(下架)
    source: Mapped[str] = mapped_column(String(16), default="official")  # official | user
    author_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    raw_draft: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 用户上传的原始草稿
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class World(Base):
    """一局游戏世界。单人局=1 个玩家；多人局绑定一个 Telegram 群。"""

    __tablename__ = "worlds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="recruiting", index=True)
    # recruiting(招募中) | running(进行中) | finished(已完结) | aborted(已中止)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # 绑定的群
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    day: Mapped[int] = mapped_column(Integer, default=1)
    push_hour: Mapped[int] = mapped_column(Integer, default=20)
    push_minute: Mapped[int] = mapped_column(Integer, default=0)
    last_tick_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # {mainline: {beat_index}, flags: {}, counters: {}, spawned: {tasks: n}}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    script: Mapped["Script"] = relationship()
    players: Mapped[list["WorldPlayer"]] = relationship(
        back_populates="world", cascade="all, delete-orphan"
    )


class WorldPlayer(Base):
    """世界中的玩家角色。"""

    __tablename__ = "world_players"
    __table_args__ = (Index("uq_world_user", "world_id", "user_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    character_name: Mapped[str] = mapped_column(String(64))
    character_role: Mapped[str] = mapped_column(String(64), default="")
    character_card: Mapped[dict] = mapped_column(JSON, default=dict)  # 角色卡快照
    status: Mapped[str] = mapped_column(String(16), default="alive")
    # alive | dead | spectator | left
    join_order: Mapped[int] = mapped_column(Integer, default=0)
    private_state: Mapped[dict] = mapped_column(JSON, default=dict)
    # {hp, items:[], clues:[], faction, relationships:{}, notes:{}}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    world: Mapped["World"] = relationship(back_populates="players")


class CanonEvent(Base):
    """世界正典：公开的剧情时间线（世界群广播的内容）。"""

    __tablename__ = "canon_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    day: Mapped[int] = mapped_column(Integer, default=1)
    public: Mapped[bool] = mapped_column(Boolean, default=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class NarrativeBeat(Base):
    """自主世界事件与持久投递队列；不伪装成玩家行动。"""
    __tablename__ = 'narrative_beats'
    __table_args__ = (Index('uq_beat_world_revision', 'world_id', 'revision', unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey('worlds.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    receipt: Mapped[dict] = mapped_column(JSON)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RuleDraft(Base):
    """规则候选票据：先冻结来源、随机档位，失败重试不重新抽取。"""
    __tablename__ = 'rule_drafts'
    __table_args__ = (Index('uq_rule_draft_source', 'world_id', 'source_key', unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey('worlds.id'), index=True)
    source_key: Mapped[str] = mapped_column(String(32))
    source_hash: Mapped[str] = mapped_column(String(64))
    source_json: Mapped[dict] = mapped_column(JSON)
    limits_json: Mapped[dict] = mapped_column(JSON)
    roll: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RuntimeRule(Base):
    """已验收的不可变规则快照；服务不提供原地修改和删除接口。"""
    __tablename__ = 'runtime_rules'
    __table_args__ = (Index('uq_runtime_rule_version', 'world_id', 'source_key', 'version', unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey('worlds.id'), index=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey('rule_drafts.id'), unique=True)
    source_key: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)
    kernel_version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[dict] = mapped_column(JSON)
    body_hash: Mapped[str] = mapped_column(String(64))
    activated_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Scene(Base):
    """每个玩家每天的个人场景（私聊推送的内容）。"""

    __tablename__ = "scenes"
    __table_args__ = (Index("uq_world_day_user", "world_id", "day", "user_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[int] = mapped_column(Integer)
    narrative: Mapped[str] = mapped_column(Text)
    suggested_actions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PlayerAction(Base):
    """玩家行动记录（当天输入，次日结算）。"""

    __tablename__ = "player_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # tick 时填充
    outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WebRoom(Base):
    """Web 房间（多人世界的群聊形态）。房间 id 直接作为 world.chat_id 的负数使用。"""

    __tablename__ = "web_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    @property
    def chat_id(self) -> int:
        """房间对应的世界 chat_id（负数，避免与用户 id 混淆）。"""
        return -self.id


class WebRoomMember(Base):
    """房间成员。"""

    __tablename__ = "web_room_members"
    __table_args__ = (Index("uq_room_user", "room_id", "user_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("web_rooms.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WebMessage(Base):
    """Web 通道聊天记录（会话持久化，刷新/离线后可拉取）。conv_key: u:{user_id} 或 r:{room_id}"""

    __tablename__ = "web_messages"
    __table_args__ = (Index("ix_conv_id", "conv_key", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conv_key: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(8))  # user | bot | sys
    sender: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    actions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PlayerItem(Base):
    """玩家持有的物品/装备实例（运行时数据，源为剧本 items 模板或 AI 即兴创建）。"""

    __tablename__ = "player_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_player_id: Mapped[int] = mapped_column(
        ForeignKey("world_players.id"), index=True
    )
    def_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # 剧本模板 id
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="misc")
    # equip(装备) | consumable(消耗品) | material(材料) | quest(任务物品) | misc
    slot: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # weapon | armor | accessory | hand | null
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    level: Mapped[int] = mapped_column(Integer, default=0)  # 强化等级
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 词缀/自定义属性
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PlayerAbility(Base):
    """玩家能力实例（源为剧本 abilities 模板或 AI 即兴创建）。"""

    __tablename__ = "player_abilities"
    __table_args__ = (Index("uq_wp_ability", "world_player_id", "name", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_player_id: Mapped[int] = mapped_column(
        ForeignKey("world_players.id"), index=True
    )
    def_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer, default=1)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class DynamicTask(Base):
    """任务/剧情目标实例：主线节拍、支线、AI 动态生成，带进度状态机。"""

    __tablename__ = "dynamic_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    world_player_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_players.id"), nullable=True, index=True
    )  # None = 世界级任务
    kind: Mapped[str] = mapped_column(String(16), default="side")
    # main(主线) | side(支线预设) | generated(AI 动态)
    title: Mapped[str] = mapped_column(String(128))
    desc: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # active | done | failed | dropped
    source: Mapped[str] = mapped_column(String(16), default="preset")  # preset | ai
    progress_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # {current, target, metric, flags_done: []}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now, onupdate=now
    )
