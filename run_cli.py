"""交互式命令行：python run_cli.py [--profile 档案名]。"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.channel.cli import CLIChannel, profile_name


def prepare_database():
    from app.db import init_db, SessionLocal, begin_write
    from seed import seed_scripts
    init_db()
    with SessionLocal() as db:
        begin_write(db)
        seed_scripts(db)
        db.commit()


async def play(channel, reader=input):
    from app.game.flow import GameFlow
    flow = GameFlow(channel)
    await channel.welcome(flow)
    while True:
        try:
            # 终端专用事件循环没有后台调度；等待输入不会推进世界或占用数据库事务。
            text = reader('\n你 > ')
        except (EOFError, KeyboardInterrupt):
            break
        try:
            if not await channel.submit(text, flow):
                break
        except Exception:
            # 引擎可能已经提交状态；不能声称保存失败，也不自动重放动作。
            channel.actions = []
            channel.write('刚才的请求未能完整显示。请用 /resume 核对已保存的结果，再决定下一步。')
    channel.write('\n已退出。再次使用相同 --profile 启动即可继续已保存的旅程。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='在终端中游玩 StoryWorld 单人剧本')
    parser.add_argument('--profile', default='default', help='本地档案名，默认 default；相同名称恢复同一存档')
    args = parser.parse_args(argv)
    try:
        name = profile_name(args.profile)
    except ValueError as exc:
        parser.error(str(exc))
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    logging.basicConfig(level=logging.CRITICAL)
    try:
        prepare_database()
        asyncio.run(play(CLIChannel(name)))
    except KeyboardInterrupt:
        print('\n已中断。行动可能已经保存，下次启动后请查看存档。')
    except Exception as exc:
        print(f'命令行启动失败（{type(exc).__name__}），请检查数据库配置与运行环境。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
