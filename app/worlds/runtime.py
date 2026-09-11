"""Generic Skills lifecycle and SQLite commit boundary. No cultivation action IDs."""
from __future__ import annotations

import copy
import logging
import secrets
import time
from types import SimpleNamespace

from sqlalchemy import select

from ..ai import world_harness, world_review
from ..ai.client import LLMError
from ..ai.harness_backend import HarnessError
from ..ai.diagnostics import failure_reply, report_failure
from ..ai.policy import InputRejected, check_player_input
from ..db import begin_write
from ..models import World, WorldPlayer, PlayerAction, NarrativeBeat, SkillTurnTicket
from .packs import PackError, for_content
from .session import WorldSession

logger = logging.getLogger(__name__)


def stream_spec(content):
    pack = for_content(content)
    return SimpleNamespace(**pack.manifest['clock'])


def initialize(world, player, content):
    from ..engine.stream import initial
    pack = for_content(content)
    state = copy.deepcopy(pack.world['initial'])
    player_state = state.pop('player')
    world.progress_json['skill_world'] = {'pack': pack.reference, 'state': state}
    world.progress_json['stream'] = initial(stream_spec(content), time.time())
    player.private_state['skill_player'] = player_state
    sync_player(player.private_state, pack)


def sync_player(private_state, pack):
    state = private_state['skill_player']
    private_state.update(pack.bind_player(state))
    private_state['inventory'] = {k: v for k, v in state['resources'].items() if pack.world['resources'][k]['value'] > 0}
    private_state['items'] = [pack.world['resources'][k]['name'] + ' ×' + str(v) for k, v in private_state['inventory'].items() if v]


def state_of(world, player, pack):
    saved = world.progress_json.get('skill_world')
    if not saved or saved.get('pack') != pack.reference or 'skill_player' not in player.private_state:
        raise PackError('这段旅程的剧本版本或角色记录无法核对，暂不改写存档。')
    state = copy.deepcopy(saved['state'])
    state['player'] = copy.deepcopy(player.private_state['skill_player'])
    return state


def replay(db, world_id, user_id, text, request_id):
    if not request_id:
        return None
    row = db.scalar(select(PlayerAction).where(PlayerAction.world_id == world_id, PlayerAction.user_id == user_id,
        PlayerAction.intent['request_id'].as_string() == request_id).limit(1))
    if row:
        if row.text != text:
            raise PackError('同一条消息的内容与已保存记录不同，请另发一条新消息。')
        return row.outcome
    return None


def prepare(db, world_id, player_id, text, mode):
    from ..engine.world_service import recent_passages
    db.rollback()
    begin_write(db)
    try:
        world = db.get(World, world_id, populate_existing=True)
        player = db.get(WorldPlayer, player_id, populate_existing=True)
        if not world or not player or player.world_id != world.id or player.user_id != world.owner_id:
            raise PackError('找不到当前旅程中的角色。')
        if world.status != 'running' or player.status != 'alive':
            raise PackError('当前旅程无法继续推进。')
        pack = for_content(world.script.content_json)
        state = state_of(world, player, pack)
        revision = world.progress_json['narrative']['revision']
        ticket = db.scalar(select(SkillTurnTicket).where(SkillTurnTicket.world_id == world.id,
                                                        SkillTurnTicket.revision == revision))
        if ticket is None:
            ticket = SkillTurnTicket(world_id=world.id, revision=revision, seed=secrets.token_hex(32), pack_hash=pack.digest)
            db.add(ticket)
        if ticket.pack_hash != pack.digest:
            raise PackError('这段推演所用的剧本规则已经改变，暂不重新抽取结果。')
        baseline = {'progress': copy.deepcopy(world.progress_json), 'player': copy.deepcopy(player.private_state),
                    'content': copy.deepcopy(world.script.content_json), 'user_id': player.user_id}
        session = WorldSession(pack, state, text=text, mode=mode, revision=revision, seed=ticket.seed,
            recent=recent_passages(db, world, player, 4), intervention=world.progress_json['stream']['intervention'],
            autonomy={k: v for k, v in world.progress_json['stream'].items() if k.endswith('_autonomy')})
        db.commit()  # Only freeze the random ticket; no gameplay effects yet.
        return session, baseline
    except BaseException:
        db.rollback()
        raise


def commit(db, world_id, player_id, session, baseline, output, *, request_id=None, now=None):
    from ..engine.stream import interval
    db.rollback()
    begin_write(db)
    try:
        world = db.get(World, world_id, populate_existing=True)
        player = db.get(WorldPlayer, player_id, populate_existing=True)
        if not world or not player or player.world_id != world_id or player.user_id != baseline['user_id']:
            raise PackError('当前角色无法核对。')
        saved = replay(db, world_id, player.user_id, session.text, request_id)
        if saved is not None:
            db.rollback()
            return saved
        if (world.status != 'running' or player.status != 'alive' or world.progress_json != baseline['progress']
                or player.private_state != baseline['player'] or world.script.content_json != baseline['content']):
            raise PackError('眼前局势刚有变化，这段尝试尚未执行；请接着最新的一段继续。')
        # Reject hot edits between proposal and commit, including calculator files.
        pack = for_content(world.script.content_json)
        if output.get('reviewed') is not True or output['pack'] != pack.reference or output['state'] != session.state:
            raise PackError('计算结果与本次推演不一致。')
        progress = copy.deepcopy(world.progress_json)
        state = copy.deepcopy(output['state'])
        changed_player = state.pop('player')
        progress['skill_world']['state'] = state
        narrative = progress['narrative']
        narrative['revision'] += 1
        narrative['minute'] += output['minutes']
        clock = progress['stream']
        clock['read_requested'] = False
        if session.mode == 'player' and (output['responded'] or any(r.get('minutes', 0) for r in output['results'])):
            clock['intervention'] = None
        if output['intervention']:
            clock['intervention'] = {'revision': narrative['revision'], 'reason': '故事停在需要你介入的时刻。'}
        clock['next_at'] = (time.time() if now is None else now) + interval(clock)
        world.progress_json = progress
        world.day = 1 + narrative['minute'] // 1440
        receipt = {'pack': pack.reference, 'results': output['results'], 'tool_calls': output['calls'],
                   'revision': narrative['revision'], 'minutes': output['minutes'], 'narrated': True, 'reviewed': True}
        if session.mode == 'player':
            private = copy.deepcopy(player.private_state)
            private['skill_player'] = changed_player
            sync_player(private, pack)
            player.private_state = private
            db.add(PlayerAction(world_id=world_id, user_id=player.user_id, day=world.day, text=session.text,
                intent={'revision': narrative['revision'], 'request_id': request_id, 'skill_world': receipt}, outcome=output['prose']))
        else:
            if changed_player != player.private_state['skill_player']:
                raise PackError('自主世界事件不能改变玩家。')
            db.add(NarrativeBeat(world_id=world_id, user_id=player.user_id, revision=narrative['revision'],
                                text=output['prose'], receipt=receipt))
        db.commit()  # State, consequences, memory and delivered prose are one atomic fact.
        return output['prose']
    except BaseException:
        db.rollback()
        raise


async def take_turn(db, world, player, text, *, request_id=None):
    world_id, player_id = world.id, player.id
    try:
        check_player_input(text)
        saved = replay(db, world.id, player.user_id, text, request_id)
        if saved is not None:
            return True, saved
        session, baseline = prepare(db, world_id, player_id, text, 'player')
        session, output = await generate(session)
        return True, commit(db, world_id, player_id, session, baseline, output, request_id=request_id)
    except (InputRejected, PackError) as exc:
        return False, str(exc)
    except HarnessError as exc:
        report_failure(exc.code)
        return False, failure_reply(exc.code)
    except LLMError as exc:
        report_failure(exc.code)
        return False, failure_reply(exc.code)
    except Exception:
        db.rollback()
        logger.error('Skills 回合未完成，候选状态未提交', exc_info=False)
        return False, '这一段还未能保存，行动没有生效。稍后再试，仍从原处接着写。'


async def generate(session):
    """At most one retry; discarded proposals never enter the save or visible story."""
    for attempt in range(2):
        try:
            output = await world_harness.run(session)
        except HarnessError as exc:
            if exc.code != 'invalid_ai_response' or attempt:
                raise
            issues = [getattr(session, 'diagnostic', {}).get('validation_error', '正文格式未通过验收。')]
        else:
            review = await world_review.check(session, output)
            if review.consistent:
                output['reviewed'] = True
                return session, output
            issues = review.issues
        if attempt == 0:
            session = WorldSession(session.pack, session.initial, text=session.text, mode=session.mode,
                revision=session.revision, seed=session.seed, recent=session.recent,
                intervention=session.intervention, corrections=issues, autonomy=session.autonomy)
    raise PackError('这一段的叙述与实际后果还未能核对，世界没有改变；稍后再接着写。')


async def advance(db, world_id, now=None, *, can_read=lambda: True):
    from ..engine.stream import reading_allowed
    now = time.time() if now is None else now
    world = db.get(World, world_id, populate_existing=True)
    if not world or not world.script.content_json.get('world_pack') or world.status != 'running':
        return None
    clock = world.progress_json.get('stream', {})
    if (not clock or not reading_allowed(clock) or not can_read() or clock['manual_pause'] or clock['intervention'] or not clock['world_autonomy']
            or not clock['narrative_autonomy'] or clock['next_at'] > now):
        return None
    if db.scalar(select(NarrativeBeat.id).where(NarrativeBeat.world_id == world_id, NarrativeBeat.delivered_at.is_(None)).limit(1)):
        return None
    player = db.scalar(select(WorldPlayer).where(WorldPlayer.world_id == world_id, WorldPlayer.user_id == world.owner_id))
    if not player or player.status != 'alive':
        return None
    try:
        session, baseline = prepare(db, world_id, player.id, '', 'world')
        session, output = await generate(session)
        if not can_read():
            return None
        commit(db, world_id, player.id, session, baseline, output, now=now)
        return output['prose']
    except (HarnessError, PackError, LLMError):
        # API failure is not a new plot event. Back off without changing the clock of the world.
        db.rollback()
        begin_write(db)
        world = db.get(World, world_id, populate_existing=True)
        progress = copy.deepcopy(world.progress_json)
        progress['stream']['next_at'] = now + 60
        world.progress_json = progress
        db.commit()
        logger.info('Skills 自主片段未完成，保留原世界并稍后重试')
        return None


def status(world, player):
    pack = for_content(world.script.content_json)
    state = state_of(world, player, pack)
    current = state['player']
    resources = current['resources']
    values = {**pack.bind_player(current), 'player': current, 'resources': resources,
              'place': state['places'][current['location']]['name'],
              'inventory': '、'.join(pack.world['resources'][k]['name'] + str(v) for k, v in resources.items() if pack.world['resources'][k]['value']),
              'abilities': '、'.join(a['name'] for a in current.get('abilities', {}).values()) or '暂无'}
    lines = [world.title] + [line.format_map(values) for line in pack.manifest['status_lines']]
    return '\n'.join(lines)
