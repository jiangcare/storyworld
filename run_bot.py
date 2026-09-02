"""启动 Bot（长轮询）。"""
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.bot.main import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main())
