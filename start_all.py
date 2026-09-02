"""一键启动：Telegram Bot + 后台管理(8080) + Web 通道(8081) + 每日调度（同一进程）。

用法：.venv\\Scripts\\python.exe start_all.py
- 后台管理：http://127.0.0.1:8080
- Web 通道：  http://127.0.0.1:8081/web  （浏览器直接玩）
- Telegram Bot：长轮询（未配置 token 时自动跳过）
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from uvicorn import Config, Server

from app.bot.scheduler import scan_due_worlds
from app.config import settings
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("start_all")


async def run() -> None:
    init_db()

    # ---- 通道 ----
    from app.game.flow import GameFlow
    from app.web.main import web_channel

    channels = []
    if settings.telegram_bot_token:
        from aiogram import Bot, Dispatcher
        from app.channel.telegram import TelegramChannel

        bot = Bot(token=settings.telegram_bot_token)
        tg = TelegramChannel(bot)
        channels.append(tg)
        dp = Dispatcher()
        dp.include_router(tg.build_router())

        async def _poll():
            logger.info("Telegram 通道开始长轮询……")
            await dp.start_polling(bot)

        asyncio.create_task(_poll())
        logger.info("Telegram 通道已启用")
    else:
        logger.warning("未配置 TELEGRAM_BOT_TOKEN，跳过 Telegram 通道")

    channels.append(web_channel)
    GameFlow()  # 事件自带 channel，flow 为全局单例

    # ---- 每日调度：所有通道共享 ----
    aps = AsyncIOScheduler()
    aps.add_job(
        scan_due_worlds,
        "interval",
        seconds=settings.tick_scan_seconds,
        args=[channels],
        id="tick-scan",
        max_instances=1,
        coalesce=True,
    )
    aps.start()
    logger.info("tick 调度器已启动（每 %s 秒扫描，通道: %s）", settings.tick_scan_seconds,
                [c.capabilities.name for c in channels])

    # ---- Web 服务 ----
    web_server = Server(Config("app.web.main:app", host="127.0.0.1", port=8081, log_level="warning"))
    web_task = asyncio.create_task(web_server.serve())
    logger.info("Web 通道: http://127.0.0.1:8081/web")

    # ---- 后台管理 ----
    admin_server = Server(Config("app.admin.main:app", host="127.0.0.1", port=8080, log_level="warning"))
    admin_task = asyncio.create_task(admin_server.serve())
    logger.info("后台管理: http://127.0.0.1:8080  (默认 admin/admin123)")

    await asyncio.gather(web_task, admin_task)


if __name__ == "__main__":
    asyncio.run(run())
