"""独立叙事时钟、世界事件 Director、介入暂停与持久投递队列。"""
from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import datetime
import logging
import secrets
import time

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from ..db import SessionLocal, begin_write, get_store
from ..config import settings
from ..models import NarrativeBeat, RuntimeEntry, User, World, WorldPlayer
from .narrative_dsl import parse_spec

logger = logging.getLogger(__name__)


def _lease(store, key, token, *, nx=False):
    # 极短的同步事务，避免连接取消时异步线程已写入租约却来不及清理。
    with store.engine.begin() as conn:
        conn.execute(delete(RuntimeEntry).where(RuntimeEntry.key == key, RuntimeEntry.expires_at <= time.time()))
        stmt = insert(RuntimeEntry).values(key=key, value=token, expires_at=time.time() + 600)
        stmt = stmt.on_conflict_do_nothing(index_elements=['key']) if nx else stmt.on_conflict_do_update(
            index_elements=['key'], set_={'value': token, 'expires_at': time.time() + 600})
        return bool(conn.execute(stmt).rowcount)


def _release(store, keys, token):
    # CancelScope 可反复取消 await；清理阶段不设置可取消等待点。
    with store.engine.begin() as conn:
        conn.execute(delete(RuntimeEntry).where(RuntimeEntry.key.in_(keys), RuntimeEntry.value == token))


def specification(content):
    if content.get('world_pack'):
        from ..worlds.runtime import stream_spec
        return stream_spec(content)
    if 'narrative' not in content:
        return None
    spec = parse_spec(content)
    if spec.stream:
        return spec.stream
    if spec.sandbox == 'cultivation':
        from .cultivation_story import CONTENT
        if content != CONTENT:
            return None
        from .stream_scenes import cultivation_stream
        template = cultivation_stream()
        # 自定义修仙地图不自动套用不相干的内置事件。
        if set(template.scenes) <= set(spec.locations) and {'elder', 'merchant'} <= {n['id'] for n in content.get('npcs', [])}:
            return template
    return None


def interval(state):
    return state.get('interval_seconds') or max(8, 60 - state['narrative_autonomy'] // 2)


def initial(spec, now):
    state = {'world_autonomy': spec.world_autonomy, 'player_autonomy': spec.player_autonomy,
             'narrative_autonomy': spec.narrative_autonomy, 'manual_pause': False,
             'intervention': None, 'completed': False, 'interval_seconds': spec.interval_seconds, 'cursors': {}, 'world': {'npcs': {}, 'weather': ''}}
    state['next_at'] = now + interval(state)
    return state


def after_action(progress, receipt, now=None):
    """玩家明确行动后才释放介入点；纯聊天、观察和失败验收不会释放。"""
    if 'stream' not in progress:
        return
    state = progress['stream']
    if any(r.get('minutes', 0) > 0 and r.get('kind') in ('move', 'interact', 'rest') for r in receipt['results']):
        state['intervention'] = None
        state['next_at'] = (time.time() if now is None else now) + interval(state)


def advance(db, world_id, now=None):
    """一次最多提交一个世界事件。没有 await，没有玩家状态写入或 PlayerAction。"""
    now = time.time() if now is None else now
    db.rollback()
    try:
        begin_write(db)
        world = db.scalar(select(World).where(World.id == world_id).execution_options(populate_existing=True))
        if world is None or world.status != 'running':
            db.rollback()
            return None
        if world.script.content_json.get('world_pack'):
            db.rollback()
            return None  # Generated Skill events use the asynchronous runtime in scan().
        spec = specification(world.script.content_json)
        player = db.scalar(select(WorldPlayer).where(WorldPlayer.world_id == world.id,
                                                       WorldPlayer.user_id == world.owner_id))
        if not spec or player is None or player.status != 'alive':
            db.rollback()
            return None
        progress = copy.deepcopy(world.progress_json)
        state = progress.setdefault('stream', initial(spec, now))
        if 'stream' not in world.progress_json:
            world.progress_json = progress
            db.commit()
            return None
        if (state.get('completed') or state['manual_pause'] or state['intervention'] or not state['world_autonomy'] or
                not state['narrative_autonomy'] or progress['narrative']['paused'] or
                progress['narrative']['ending'] or state['next_at'] > now):
            db.rollback()
            return None
        # 未投递段落必须先投递，不产生无限积压。
        if db.scalar(select(NarrativeBeat.id).where(NarrativeBeat.world_id == world_id,
                                                     NarrativeBeat.delivered_at.is_(None)).limit(1)):
            db.rollback()
            return None
        location = player.private_state['location']
        beats = spec.scenes.get(location, [])
        cursor = state['cursors'].get(location, 0)
        candidates = [(cursor + offset, beats[(cursor + offset) % len(beats)]) for offset in range(len(beats))
                      if spec.loop or cursor + offset < len(beats)]
        choice = next(((index, beat) for index, beat in candidates if beat.minimum_world_autonomy <= state['world_autonomy']), None)
        if choice is None:
            db.rollback()
            return None
        index, beat = choice
        state['cursors'][location] = index + 1
        if not spec.loop and index + 1 == len(beats):
            state['completed'] = True
        flags = progress['narrative']['flags']
        variant = next((v for v in beat.variants if all(flags.get(k, False) == val for k, val in v.flags.items())), None)
        event = variant or beat
        if beat.weather:
            state['world']['weather'] = beat.weather
        for key, change in event.npcs.items():
            previous = state['world']['npcs'].get(key, {})
            state['world']['npcs'][key] = {'location': change.location, 'activity': change.activity,
                'fear': max(0, min(100, previous.get('fear', 0) + change.fear_delta))}
        timeline = progress['narrative']
        timeline['minute'] += beat.minutes
        timeline['revision'] += 1
        state['next_at'] = now + interval(state)
        if beat.intervention:
            state['intervention'] = {'location': location, 'cursor': index, 'revision': timeline['revision']}
        text = event.text
        if state['player_autonomy'] >= 30 and beat.player_detail:
            text += '\n\n' + beat.player_detail
        from ..ai.prose_contract import validate_prose
        text = validate_prose(text)
        record = NarrativeBeat(world_id=world.id, user_id=player.user_id, revision=timeline['revision'],
            text=text, receipt={'event': f'{location}:{index}', 'minute': timeline['minute'], 'branch': dict(variant.flags) if variant else {},
                                'world': copy.deepcopy(state['world']), 'intervention_required': beat.intervention})
        world.progress_json = progress
        world.day = 1 + timeline['minute'] // 1440
        db.add(record)
        db.commit()
        return record.id
    except BaseException:
        db.rollback()
        raise


@asynccontextmanager
async def input_turn(platform, user_id):
    """跨进程串行玩家输入和世界事件投递；等候输入优先于下一自动事件。"""
    store = await get_store()
    token = secrets.token_hex(16)
    scope = f'{platform}:{user_id}'
    waiting, lock = f'stream:waiting:{scope}', f'stream:lock:{scope}'
    _lease(store, waiting, token)
    acquired = False
    try:
        while not acquired:
            acquired = _lease(store, lock, token, nx=True)
            if not acquired:
                await asyncio.sleep(.1)
        _release(store, [waiting], token)
        yield
    finally:
        _release(store, [waiting, lock] if acquired else [waiting], token)


async def scan(channel, now=None):
    now = time.time() if now is None else now
    platform = channel.capabilities.name
    with SessionLocal() as db:
        rows = list(db.execute(select(World.id, User.tg_id).join(User, User.id == World.owner_id)
                              .where(World.status == 'running', User.platform == platform)
                              .order_by(World.id.desc())))
    # 一个玩家只向当前最近的进行中世界投递。
    seen = set()
    store = await get_store()
    for world_id, user_id in rows:
        if user_id in seen:
            continue
        seen.add(user_id)
        if not channel.stream_present(user_id):
            continue
        scope = f'{platform}:{user_id}'
        lock, token = f'stream:lock:{scope}', secrets.token_hex(16)
        if await store.get(f'stream:waiting:{scope}') or not _lease(store, lock, token, nx=True):
            continue
        try:
            with SessionLocal() as db:
                world = db.get(World, world_id)
                if world and world.script.content_json.get('world_pack'):
                    from ..worlds import runtime
                    await until_player_input(runtime.advance(db, world_id, now), store, f'stream:waiting:{scope}')
                else:
                    advance(db, world_id, now)
                record = db.scalar(select(NarrativeBeat).where(NarrativeBeat.world_id == world_id,
                      NarrativeBeat.delivered_at.is_(None)).order_by(NarrativeBeat.id).limit(1))
                if record:
                    await deliver_record(channel, db, record, user_id)
        except Exception:
            logger.exception('自主叙事投递失败，保留事件等待重试')
        finally:
            _release(store, [lock], token)


async def until_player_input(operation, store, waiting_key):
    """An uncommitted generated passage yields promptly to newly arrived player input."""
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            if await store.get(waiting_key):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return None
            await asyncio.wait({task}, timeout=.1)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def flush_pending(channel, user_id):
    """玩家介入前先投递已提交的旧段落，避免回应后倒序出现前一事件。调用方持有输入锁。"""
    with SessionLocal() as db:
        world_id = db.scalar(select(World.id).join(User, User.id == World.owner_id).where(
            User.platform == channel.capabilities.name, User.tg_id == user_id,
            World.status == 'running').order_by(World.id.desc()).limit(1))
        if world_id is None:
            return
        record = db.scalar(select(NarrativeBeat).where(NarrativeBeat.world_id == world_id,
                    NarrativeBeat.delivered_at.is_(None)).order_by(NarrativeBeat.id).limit(1))
        if record is None:
            return
        await deliver_record(channel, db, record, user_id, polish=False)


async def deliver_record(channel, db, record, user_id, *, polish=True):
    record_id, text = record.id, record.text
    if polish and settings.deepseek_api_key and not record.receipt.get('narrated'):
        from ..ai import narrative as narrator
        from .world_service import recent_passages
        world = db.get(World, record.world_id)
        player = db.scalar(select(WorldPlayer).where(WorldPlayer.world_id == world.id,
                                                       WorldPlayer.user_id == record.user_id))
        receipt = copy.deepcopy(record.receipt)
        receipt['results'] = [{'text': text, 'ok': True}]
        context = {'world': world.title, 'character': player.character_name, 'sandbox': bool(world.script.content_json['narrative'].get('sandbox')),
                   'autonomous_world_event': True, 'recent': recent_passages(db, world, player, 3)[:-1],
                   'world_state': receipt['world']}
        db.rollback()
        try:
            # 不让润色长期阻塞玩家介入；事实早已落库，超时直接使用作者正文。
            text = await asyncio.wait_for(narrator.narrate('', context, receipt), timeout=min(8, settings.llm_timeout))
        except Exception:
            logger.info('自主事件润色未完成，使用已保存的作者正文')
        begin_write(db)
        record = db.get(NarrativeBeat, record_id)
        record.text = text
        record.receipt = {**record.receipt, 'narrated': True}
        db.commit()
    db.rollback()
    delivered = await channel.send_private(user_id, text)
    if delivered is not None:
        begin_write(db)
        db.get(NarrativeBeat, record_id).delivered_at = datetime.now()
        db.commit()


async def run(channel):
    while True:
        try:
            await scan(channel)
        except Exception:
            logger.exception('叙事时钟扫描失败')
        await asyncio.sleep(1)


def control(db, world, command, arg=''):
    """显式系统界面；暂停不会改写未完成的介入点。"""
    spec = specification(world.script.content_json)
    if not spec:
        return '这个剧本尚未定义自主世界事件，仍可自由行动。'
    now = time.time()
    world_id = world.id
    db.rollback()
    try:
        begin_write(db)
        world = db.get(World, world_id, populate_existing=True)
        progress = copy.deepcopy(world.progress_json)
        state = progress.setdefault('stream', initial(spec, now))
        if command == 'pause':
            state['manual_pause'] = True
        elif command == 'stream' and arg in ('on', 'off'):
            state['manual_pause'] = arg == 'off'
            state['next_at'] = now + interval(state)
        elif command == 'pass':
            state['intervention'] = None
            state['next_at'] = now + interval(state)
        elif command == 'autonomy' and arg:
            names = {'world': 'world_autonomy', 'player': 'player_autonomy', 'narrative': 'narrative_autonomy'}
            updates = {}
            for part in arg.split():
                key, value = part.split('=')
                if key not in names or not value.isdecimal() or not 0 <= int(value) <= 100:
                    raise ValueError('参数必须是 world/player/narrative=0..100')
                updates[names[key]] = int(value)
            state.update(updates)
            state['next_at'] = now + interval(state)
        elif command == 'stream' and arg not in ('', 'status'):
            raise ValueError('用法：/stream on、/stream off 或 /stream status')
        world.progress_json = progress
        db.commit()
    except ValueError as exc:
        db.rollback()
        return str(exc) if str(exc).startswith(('参数', '用法')) else '用法：/autonomy world=95 player=30 narrative=85'
    except BaseException:
        db.rollback()
        raise
    status = '暂停' if state['manual_pause'] or not state['world_autonomy'] or not state['narrative_autonomy'] else ('等待玩家介入' if state['intervention'] else '运行中')
    if state.get('completed'):
        status = '本段已讲完（仍可自由行动）'
    return (f'叙事流：{status}\nWorld {state["world_autonomy"]} / Player {state["player_autonomy"]} / Narrative {state["narrative_autonomy"]}'
            f'\n段落间隔约 {interval(state)} 秒。/pause 暂停，/stream on 恢复；/pass 表示暂不介入当前事件。')
