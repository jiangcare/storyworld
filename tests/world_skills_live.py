"""Opt-in real Harness/DeepSeek playthrough. Temporary SQLite, no live chat channels."""
from __future__ import annotations
import asyncio
import copy
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run():
    from tests.support import use_test_database
    use_test_database()
    import app.db as dbmod
    from app.config import settings
    from app.ai.diagnostics import configuration_issues
    from app.engine import world_service, stream
    from app.models import World, WorldPlayer, Script, PlayerAction, NarrativeBeat
    from app.worlds.packs import installed
    from app.worlds import runtime
    if settings.ai_backend != 'harness' or configuration_issues():
        raise RuntimeError('A configured compatible Harness environment is required')
    pack = installed('changsheng')
    dbmod.init_db()
    transcript = []
    path = Path(os.getenv('STORYWORLD_LIVE_TRANSCRIPT', '/tmp/storyworld-skills-live.txt'))
    original = runtime.world_harness.run
    async def traced(session):
        try:
            return await original(session)
        finally:
            Path(str(path) + '.last-run.json').write_text(json.dumps({
                'diagnostic': getattr(session, 'diagnostic', {}), 'calls': session.calls,
                'results': session.results, 'state': session.state}, ensure_ascii=False, indent=2), encoding='utf-8')
    runtime.world_harness.run = traced
    def save():
        path.write_text('\n\n---\n\n'.join(transcript), encoding='utf-8')
    with dbmod.SessionLocal() as db:
        content = pack.content()
        script = Script(**{k: content[k] for k in ('title', 'description', 'genre', 'mode', 'days', 'min_players', 'max_players')},
                        content_json=content, status='approved')
        db.add(script); db.commit()
        user = world_service.get_or_create_user(db, 855931, platform='cli')
        world = world_service.create_world(db, user, script, chat_id=855931)
        player = world_service.join_world(db, world, user)
        world_service.start_world(db, world)
        world_id, player_id = world.id, player.id
    async def say(text, key):
        with dbmod.SessionLocal() as db:
            world, player = db.get(World, world_id), db.get(WorldPlayer, player_id)
            ok, prose = await world_service.record_action(db, world, player, text, request_id=key)
            transcript.append('玩家：' + text + '\n\n' + prose)
            save()
            if not ok:
                raise RuntimeError('Playthrough turn failed: ' + key)
            row = db.query(PlayerAction).order_by(PlayerAction.id.desc()).first()
            Path(str(path) + '.audit.json').write_text(json.dumps(row.intent['skill_world'], ensure_ascii=False, indent=2), encoding='utf-8')
            calls = [call['name'] for call in row.intent['skill_world']['tool_calls']]
            assert '_skill_loaded' in calls and 'world_context' in calls, 'Skill was not actually loaded'
            print(key, 'PASS', ','.join(calls), flush=True)
            state = runtime.state_of(world, player, pack)
            snapshot = copy.deepcopy((world.progress_json, player.private_state))
            ok, replay = await world_service.record_action(db, world, player, text, request_id=key)
            assert ok and replay == prose and snapshot == (world.progress_json, player.private_state)
            return state
    state = await say('我把麻绳绕过竹片打个活结，做成一个能提起来的小把手。', 'creative-cord')
    assert state['objects']['bamboo']['properties']['tied'] or state['objects']['cord']['properties']['tied']
    state = await say('把旧棉布浸湿，铺在水盆口，看看能不能挡住落灰。', 'creative-cloth')
    assert state['objects']['cloth']['properties']['wet'] and state['objects']['basin']['properties']['covered']
    state = await say('我取一片薄木头垫在门边，免得风把门合上。', 'creative-wedge')
    assert any(key.startswith('prop_') for key in state['objects'])
    assert state['objects']['door']['arrangements']['open']['using']
    before = copy.deepcopy(state['player']['resources'])
    state = await say('我问许掌柜：要是我替你收拾药架，借半天炉子行不行？', 'negotiate')
    assert state['player']['resources'] == before
    assert any(m['npc'] == 'xu' for m in state['memories'])
    state = await say('刚才我提的借炉条件，你觉得哪里还没谈清？', 'remember')
    assert state['player']['resources'] == before
    state = await say('我把引露诀摊在膝头，试着学会里面收拢水灵的法子。', 'learn')
    assert 'dew' in state['player']['abilities'] and state['player']['resources']['qi'] == 0
    cost = state['player']['abilities']['dew']['cost']
    for i in range((cost + 2) // 3):
        state = await say('先不求境界进步，我只按引露诀吐纳蓄灵，让丹田里多积些灵力。', 'channel-' + str(i))
    before_qi = state['player']['resources']['qi']
    state = await say('我用引露诀牵出盆里的几滴水，绕到竹片上，把竹片润湿。', 'novel-spell')
    assert state['objects']['bamboo']['properties']['wet']
    assert state['player']['resources']['qi'] == before_qi - cost
    with dbmod.SessionLocal() as db:
        world, player = db.get(World, world_id), db.get(WorldPlayer, player_id)
        before = copy.deepcopy(player.private_state)
        now = world.progress_json['stream']['next_at'] + 1
        text = await runtime.advance(db, world_id, now=now)
        if text is None:
            raise RuntimeError('Autonomous story generation failed')
        transcript.append('世界自行推进：\n\n' + text); save()
        assert player.private_state == before
        assert db.query(NarrativeBeat).count() == 1
        await runtime.advance(db, world_id, now=now + 1000)
        assert db.query(NarrativeBeat).count() == 1, 'undelivered beat was regenerated'
        print('autonomous PASS; no player effects; pending delivery deduplicated', flush=True)
        print('PASS real Skills, Python calculators, three creative combinations, memory, mana, replay, autonomous narration', flush=True)
        print('model:', settings.deepseek_model, 'pack:', json.dumps(pack.reference), flush=True)


if __name__ == '__main__':
    if os.getenv('STORYWORLD_TEST_SKILLS_LIVE') != '1':
        print('SKIP: set STORYWORLD_TEST_SKILLS_LIVE=1 to use the configured real DeepSeek API')
    else:
        try:
            asyncio.run(run())
        except Exception as exc:
            print('FAIL:', type(exc).__name__, str(exc) if isinstance(exc, (AssertionError, RuntimeError)) else '', flush=True)
            raise SystemExit(1)
