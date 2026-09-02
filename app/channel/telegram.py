"""Telegram 通道：aiogram 网关，把 TG 消息/回调归一化为 ChannelEvent。"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .base import Channel
from .types import Action, ChannelCapabilities, ChannelEvent

logger = logging.getLogger(__name__)


class TelegramChannel(Channel):
    capabilities = ChannelCapabilities(
        name="telegram",
        supports_buttons=True,
        supports_private_push=True,
        supports_edit=True,
    )

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._me_id: Optional[int] = None

    async def _bot_id(self) -> int:
        if self._me_id is None:
            me = await self.bot.get_me()
            self._me_id = me.id
        return self._me_id

    # ---------- 发送 ----------

    async def send_text(self, chat_id: int, text: str) -> Optional[int]:
        try:
            msg = await self.bot.send_message(chat_id, text)
            return msg.message_id
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("[telegram] 发送失败 chat=%s: %s", chat_id, e)
            return None

    async def _send_with_actions(
        self, chat_id: int, text: str, actions: Sequence[Action]
    ) -> Optional[int]:
        b = InlineKeyboardBuilder()
        for a in actions[:12]:
            if len(a.payload) > 64:
                logger.warning("[telegram] 按钮 payload 超长已跳过: %s", a.payload)
                continue
            b.button(text=a.label, callback_data=a.payload)
        b.adjust(1)
        try:
            msg = await self.bot.send_message(
                chat_id, text, reply_markup=b.as_markup()
            )
            return msg.message_id
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("[telegram] 发送失败 chat=%s: %s", chat_id, e)
            return None

    # ---------- 回执 ----------

    async def ack(self, ev: ChannelEvent, text: str = "", alert: bool = False) -> None:
        cb: Optional[CallbackQuery] = ev.reply_token if isinstance(ev.reply_token, CallbackQuery) else None
        if cb is not None:
            try:
                await cb.answer(text or "", show_alert=alert)
            except Exception:  # noqa: BLE001
                pass

    # ---------- 归一化与分发 ----------

    def build_router(self) -> Router:
        router = Router()

        @router.message(F.text)
        async def _on_message(message: Message):
            if message.from_user is None or message.from_user.is_bot:
                return
            try:
                is_reply_bot = False
                rtm = message.reply_to_message
                if rtm is not None and rtm.from_user is not None:
                    is_reply_bot = (
                        rtm.from_user.is_bot and rtm.from_user.id == await self._bot_id()
                    )
                ev = ChannelEvent(
                    platform="telegram",
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    username=message.from_user.username,
                    display_name=message.from_user.full_name,
                    text=message.text or "",
                    is_private=message.chat.type == "private",
                    reply_to_bot=is_reply_bot,
                    message_id=message.message_id,
                    channel=self,
                )
                await self.handle_event(ev)
            except Exception:  # noqa: BLE001
                logger.exception("[telegram] 消息处理异常")

        @router.callback_query(F.data)
        async def _on_callback(callback: CallbackQuery):
            if callback.from_user is None or callback.from_user.is_bot:
                return
            ev = ChannelEvent(
                platform="telegram",
                user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                username=callback.from_user.username,
                display_name=callback.from_user.full_name,
                payload=callback.data or "",
                is_private=callback.message.chat.type == "private",
                reply_token=callback,
                channel=self,
            )
            await self.handle_event(ev)

        return router
