"""启动 Web 通道服务（浏览器直接玩）。 http://127.0.0.1:8081/web

注意：单独运行本服务时没有每日 tick 调度器（需 start_all.py 同进程跑 bot/调度）。
"""
import logging

import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

if __name__ == "__main__":
    uvicorn.run("app.web.main:app", host="127.0.0.1", port=8081)
