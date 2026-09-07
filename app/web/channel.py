"""Web 通道：连接注册 + 会话持久化 + 实时投递。

会话（conv）规则：
- u:{user_id}  个人私聊（聊天界面即单人游戏入口）
- r:{room_id}  房间（多人世界的群聊形态，对应 world.chat_id = -room_id）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from ..channel.base import Channel
from ..channel.types import Action, ChannelCapabilities, ChannelEvent
from ..db import SessionLocal
from ..models import WebMessage

logger = logging.getLogger(__name__)


class WebChannel(Channel):
    capabilities = ChannelCapabilities(
        name="web",
        supports_buttons=True,
        supports_private_push=True,
        supports_edit=False,
    )

    def __init__(self) -> None:
        self._conns: dict[str, set] = {}
        self._lock = asyncio.Lock()

    def stream_present(self, user_id):
        return bool(self._conns.get(self.conv_for_user(user_id)))

    # ---------------- 会话解析 ----------------

    @staticmethod
    def conv_for_user(user_id: int) -> str:
        return f"u:{user_id}"

    @staticmethod
    def conv_for_chat(chat_id: int) -> str:
        """world.chat_id → 会话 key。负数=房间，正数=用户私聊。"""
        return f"r:{-chat_id}" if chat_id < 0 else f"u:{chat_id}"

    @staticmethod
    def conv_parts(conv: str) -> tuple[str, int]:
        kind, _, raw = conv.partition(":")
        return kind, int(raw)

    # ---------------- 连接管理 ----------------

    async def register(self, conv_key: str, ws: Any) -> None:
        async with self._lock:
            self._conns.setdefault(conv_key, set()).add(ws)

    async def unregister(self, conv_key: str, ws: Any) -> None:
        async with self._lock:
            s = self._conns.get(conv_key)
            if s:
                s.discard(ws)
                if not s:
                    self._conns.pop(conv_key, None)

    async def _deliver(self, conv_key: str, data: dict) -> None:
        for ws in list(self._conns.get(conv_key, ())):
            try:
                await ws.send_json(data)
            except Exception:  # noqa: BLE001 连接已断开
                await self.unregister(conv_key, ws)

    # ---------------- 持久化 ----------------

    def _persist(
        self,
        conv_key: str,
        role: str,
        text: str,
        actions: Optional[Sequence[Action]] = None,
        sender: Optional[str] = None,
    ) -> int:
        db = SessionLocal()
        try:
            row = WebMessage(
                conv_key=conv_key,
                role=role,
                sender=sender,
                text=text,
                actions=(
                    [{"label": a.label, "payload": a.payload} for a in actions]
                    if actions
                    else None
                ),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()

    async def persist_user_message(self, conv_key: str, nickname: str, text: str) -> int:
        return self._persist(conv_key, "user", text, sender=nickname)

    async def persist_sys(self, conv_key: str, text: str) -> int:
        return self._persist(conv_key, "sys", text)

    # ---------------- 发送（由游戏流/调度器调用） ----------------

    async def send_text(self, chat_id: int, text: str) -> Optional[int]:
        conv = self.conv_for_chat(chat_id)
        mid = self._persist(conv, "bot", text)
        await self._deliver(conv, {"type": "msg", "id": mid, "role": "bot", "text": text, "actions": None})
        return mid

    async def _send_with_actions(
        self, chat_id: int, text: str, actions: Sequence[Action]
    ) -> Optional[int]:
        conv = self.conv_for_chat(chat_id)
        acts = [{"label": a.label, "payload": a.payload} for a in actions]
        mid = self._persist(conv, "bot", text, actions=actions)
        await self._deliver(conv, {"type": "msg", "id": mid, "role": "bot", "text": text, "actions": acts})
        return mid

    async def send_private(
        self,
        user_id: int,
        text: str,
        actions: Optional[Sequence[Action]] = None,
    ) -> Optional[int]:
        conv = self.conv_for_user(user_id)
        acts = [{"label": a.label, "payload": a.payload} for a in (actions or [])]
        mid = self._persist(conv, "bot", text, actions=actions or None)
        await self._deliver(conv, {"type": "msg", "id": mid, "role": "bot", "text": text, "actions": acts or None})
        return mid

    # ---------------- 回执 ----------------

    async def ack(self, ev: ChannelEvent, text: str = "", alert: bool = False) -> None:
        ws = ev.reply_token
        if ws is not None and hasattr(ws, "send_json"):
            try:
                await ws.send_json({"type": "ack", "text": text or "", "alert": alert})
            except Exception:  # noqa: BLE001
                pass
