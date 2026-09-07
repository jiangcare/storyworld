"""通道核心类型：归一化事件 / 按钮动作 / 能力声明。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Action:
    """一个可点击/可选择的动作。payload 为通道不透明字符串（适配器原样回传）。"""

    label: str
    payload: str


@dataclass
class ChannelCapabilities:
    """通道能力声明：调度器据此决定推送/交互策略。"""

    name: str = "generic"
    supports_buttons: bool = False      # 是否支持按钮式交互（否则文本降级）
    supports_private_push: bool = False  # 能否主动给用户发私聊消息
    supports_edit: bool = False          # 能否编辑已发消息


@dataclass
class ChannelEvent:
    """归一化事件：所有平台的输入都变成这个结构。"""

    platform: str                       # telegram | qq | dingtalk | web | cli ...
    user_id: int                        # 平台侧用户 ID
    chat_id: int                        # 平台侧聊天 ID（群或私聊）
    username: Optional[str] = None
    display_name: Optional[str] = None
    text: str = ""                      # 用户文本（自由输入/命令原文）
    payload: Optional[str] = None       # 按钮动作回传（有值时表示按钮被按下）
    is_private: bool = False            # 是否私聊
    reply_to_bot: bool = False          # 是否回复了机器人消息（群内=行动）
    auto_action: bool = False           # 自由文本直接当行动（Web 游戏客户端语义）
    message_id: Optional[int] = None
    reply_token: object = None          # 适配器私有回执句柄（如 callback query）
    channel: object = None              # 来源通道实例（事件回发用）
    _extra: dict = field(default_factory=dict)

    @property
    def user_key(self) -> tuple[str, int]:
        return (self.platform, self.user_id)


def parse_command(text: str) -> tuple[Optional[str], str]:
    """从文本解析 (命令名, 参数)。非命令返回 (None, text)。"""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, text
    parts = text[1:].split(None, 1)
    if not parts:
        return "", ""
    cmd = parts[0].lower()
    # 去掉 @botname 后缀
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg
