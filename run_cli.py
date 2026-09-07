"""交互式命令行：python run_cli.py [--profile 档案名]。"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from contextlib import suppress

from app.channel.cli import CLIChannel, profile_name


def prepare_database():
    from app.db import init_db, SessionLocal, begin_write
    from seed import seed_scripts
    init_db()
    with SessionLocal() as db:
        begin_write(db)
        seed_scripts(db)
        db.commit()


async def read_line(reader, prompt):
    """终端读入不占用事件循环；守护线程不会让 Ctrl+C 退出卡在 input。"""
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    def publish(value=None, error=None):
        if not future.done():
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(value)
    def read():
        try:
            value = reader(prompt)
        except BaseException as exc:
            error = EOFError() if isinstance(exc, StopIteration) else exc
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(publish, None, error)
        else:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(publish, value)
    threading.Thread(target=read, daemon=True).start()
    return await future


async def open_demo(channel):
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import Script
    from app.engine import world_service
    from app.engine.novel_slice import CONTENT, OPENING
    with SessionLocal() as db:
        user = world_service.get_or_create_user(db, channel.user_id, platform='cli')
        world = world_service.get_active_world(db, user.id)
        if world is not None and world.script.title != CONTENT['title']:
            channel.write('这个档案已有其他世界，请使用另一个 --profile 试玩 Demo。')
            return False
        if world is None:
            script = db.scalar(select(Script).where(Script.title == CONTENT['title'], Script.status == 'approved'))
            if script is None:
                raise ValueError('Demo 剧本尚未初始化')
            world = world_service.create_world(db, user, script, chat_id=channel.user_id)
            player = world_service.join_world(db, world, user)
            world_service.start_world(db, world)
        else:
            player = next(p for p in world.players if p.user_id == user.id)
        channel.write(world_service.build_player_recent(db, world, player, 1) or OPENING)
    return True


async def play(channel, reader=input, *, demo=False):
    from app.game.flow import GameFlow
    from app.engine import stream
    flow = GameFlow(channel)
    if demo:
        if not await open_demo(channel):
            return
    else:
        await channel.welcome(flow)
    channel.streaming_open = True
    task = asyncio.create_task(stream.run(channel))
    try:
        while True:
            try:
                text = await read_line(reader, '\n你 > ')
            except (EOFError, KeyboardInterrupt):
                break
            try:
                if not await channel.submit(text, flow):
                    break
            except Exception:
                channel.actions = []
                channel.write('刚才的请求未能完整显示。请用 /resume 核对已保存的结果，再决定下一步。')
    finally:
        channel.streaming_open = False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    channel.write('\n已退出。再次使用相同 --profile 启动即可继续已保存的旅程。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='在终端中游玩 StoryWorld 单人剧本')
    parser.add_argument('--demo', action='store_true', help='直接进入雨幕便利店小说 Demo，默认使用独立 novel-demo 档案')
    parser.add_argument('--profile', default=None, help='本地档案名，默认 default；相同名称恢复同一存档')
    args = parser.parse_args(argv)
    try:
        name = profile_name(args.profile or ('novel-demo' if args.demo else 'default'))
    except ValueError as exc:
        parser.error(str(exc))
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    logging.basicConfig(level=logging.CRITICAL)
    try:
        prepare_database()
        asyncio.run(play(CLIChannel(name), demo=args.demo))
    except KeyboardInterrupt:
        print('\n已中断。行动可能已经保存，下次启动后请查看存档。')
    except Exception as exc:
        print(f'命令行启动失败（{type(exc).__name__}），请检查数据库配置与运行环境。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
