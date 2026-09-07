"""Bot 入口：长轮询 + 每日 tick 调度（经通道抽象）。"""
import logging

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..channel import registry as chan_registry
from ..channel.telegram import TelegramChannel
from ..config import settings
from ..db import init_db
from ..game.flow import GameFlow
from . import scheduler

logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.telegram_bot_token:
        raise SystemExit("缺少 TELEGRAM_BOT_TOKEN，请配置 .env 后重试。")

    init_db()
    if settings.telegram_proxy:
        from aiogram.client.session.aiohttp import AiohttpSession

        _session = AiohttpSession(proxy=settings.telegram_proxy)
        bot = Bot(token=settings.telegram_bot_token, session=_session)
    else:
        bot = Bot(token=settings.telegram_bot_token)

    # 通道抽象：Telegram 只是第一个接入的通道
    channel = TelegramChannel(bot)
    chan_registry.register(channel)
    GameFlow(channel)

    dp = Dispatcher()
    dp.include_router(channel.build_router())

    # 每日剧情调度：扫描式，进程重启后也能自动补推
    aps = AsyncIOScheduler()
    aps.add_job(
        scheduler.scan_due_worlds,
        "interval",
        seconds=settings.tick_scan_seconds,
        args=[[channel]],
        id="tick-scan",
        max_instances=1,
        coalesce=True,
    )
    aps.start()
    logger.info("tick 调度器已启动（每 %s 秒扫描）", settings.tick_scan_seconds)

    logger.info("Telegram 通道开始长轮询（本地部署，无需域名）……")
    await dp.start_polling(bot)
