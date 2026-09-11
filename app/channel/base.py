"""通道基类：游戏流只依赖这里的 send/ack 原语，不感知任何具体平台。"""
from __future__ import annotations

import logging
import time
from typing import Optional, Sequence

from .types import Action, ChannelCapabilities, ChannelEvent

logger = logging.getLogger(__name__)

_TEXT_ACTION_HINT = "💡 你可以直接回复上面的行动，或自由发挥：想做什么就说什么。"


class Channel:
    """文字交互通道抽象。子类实现各平台的具体收发。"""

    capabilities = ChannelCapabilities()

    def note_activity(self, user_id):
        if not hasattr(self, '_stream_activity'):
            self._stream_activity = {}
        self._stream_activity[user_id] = time.time()

    def stream_present(self, user_id):
        # 无在线状态的聊天渠道只在最近一次玩家交互后的15分钟主动叙述。
        return time.time() - getattr(self, '_stream_activity', {}).get(user_id, 0) < 900

    def reading_ready(self, user_id, revision):
        return self.stream_present(user_id)

    # ---------- 发送 ----------

    async def send_text(self, chat_id: int, text: str) -> Optional[int]:
        """发送纯文本，返回消息 ID（不支持则 None）。"""
        raise NotImplementedError

    async def send(
        self,
        chat_id: int,
        text: str,
        actions: Optional[Sequence[Action]] = None,
    ) -> Optional[int]:
        """发送文本 + 可选动作（按钮；无按钮通道自动文本降级）。"""
        actions = list(actions) if actions else []
        if actions and not self.capabilities.supports_buttons:
            lines = [text]
            for a in actions:
                lines.append(f"· {a.label}")
            lines.append(_TEXT_ACTION_HINT)
            return await self.send_text(chat_id, "\n".join(lines))
        return await self._send_with_actions(chat_id, text, actions)

    async def _send_with_actions(
        self, chat_id: int, text: str, actions: Sequence[Action]
    ) -> Optional[int]:
        raise NotImplementedError

    async def send_private(
        self,
        user_id: int,
        text: str,
        actions: Optional[Sequence[Action]] = None,
    ) -> Optional[int]:
        """给用户发私聊消息（推送个人场景）。无私聊能力则仅记日志。"""
        if not self.capabilities.supports_private_push:
            logger.warning("[%s] 通道不支持私聊推送，跳过 user=%s", self.capabilities.name, user_id)
            return None
        return await self.send(user_id, text, actions)

    # ---------- 回执 ----------

    async def ack(self, ev: ChannelEvent, text: str = "", alert: bool = False) -> None:
        """对事件给一次性回执（TG 的 callback.answer 等）。无按钮通道为空操作。"""
        return None

    # ---------- 事件分发 ----------

    async def handle_event(self, ev: ChannelEvent) -> None:
        """子类收到平台消息后调用：归一化后交给游戏流处理。"""
        from ..game.flow import get_flow

        flow = get_flow()
        if flow is None:
            logger.error("未注册游戏流，事件被丢弃: %s", ev)
            return
        try:
            await flow.dispatch(ev)
        except Exception:  # noqa: BLE001
            logger.exception("[%s] 事件处理异常 user=%s", ev.platform, ev.user_id)
