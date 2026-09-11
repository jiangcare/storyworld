"""Real SQLite and actual pack subprocesses; only the model's tool choices are fixtures."""
from __future__ import annotations
import asyncio
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.support import use_test_database
use_test_database()
import app.db as dbmod
from app.ai.harness_backend import HarnessError
from app.channel.cli import CLIChannel
from app.engine import world_service, stream
from app.game.flow import GameFlow
from app.models import World, WorldPlayer, Script, PlayerAction, NarrativeBeat, SkillTurnTicket
from app.worlds import runtime
from app.worlds.packs import installed, PackError
from app.worlds.session import WorldSession
from run_cli import open_story


def boot(session):
    assert session.call('_skill_loaded', {'name': session.pack.skill_name})['ok']
    assert session.call('world_context', {})['ok']


def calc(session, script, args, quote=None):
    assert session.call('read_rule', {'name': script})['ok']
    return session.call('calculate', {'script': script, 'arguments_json': json.dumps(args, ensure_ascii=False),
                                      'quote': session.text if quote is None else quote})


def scene(session, *, npc='xu', speech='这点潮气，药草可不爱受。', detail='', activity='', intervention=False, responded=False):
    return session.call('scene', dict(npc=npc, speech=speech, detail=detail, activity=activity,
                                    intervention=intervention, responded=responded))


WET = {'operation': 'manipulate', 'target': 'cloth', 'property': 'wet', 'value': True,
       'using': ['basin'], 'method': '将棉布浸入盆水'}


class WorldSkillsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        review = patch.object(runtime.world_review, 'check', AsyncMock(return_value=runtime.world_review.Review(consistent=True)))
        self.review = review.start()
        self.addCleanup(review.stop)
        dbmod.Base.metadata.drop_all(dbmod.engine)
        dbmod.init_db()
        self.db = dbmod.SessionLocal()
        self.addCleanup(self.db.close)
        self.pack = installed('changsheng')
        content = self.pack.content()
        script = Script(**{k: content[k] for k in ('title', 'description', 'genre', 'mode', 'days', 'min_players', 'max_players')},
                        content_json=content, status='approved')
        self.db.add(script)
        self.db.commit()
        self.user = world_service.get_or_create_user(self.db, 86213, platform='cli')
        self.world = world_service.create_world(self.db, self.user, script, chat_id=86213)
        self.player = world_service.join_world(self.db, self.world, self.user)
        world_service.start_world(self.db, self.world)

    def snapshot(self):
        self.db.refresh(self.world)
        self.db.refresh(self.player)
        return copy.deepcopy((self.world.progress_json, self.player.private_state))

    async def test_reading_default_and_old_save_do_not_advance_while_away(self):
        before = self.snapshot()
        with patch.object(runtime.world_harness, 'run', AsyncMock()) as model:
            self.assertIsNone(await runtime.advance(self.db, self.world.id, now=10**11))
            self.assertEqual(before, self.snapshot())
            progress = copy.deepcopy(self.world.progress_json)
            progress['stream'].pop('reading_mode')
            progress['stream'].pop('read_requested')
            self.world.progress_json = progress
            self.db.commit()
            before = self.snapshot()
            self.assertIsNone(await runtime.advance(self.db, self.world.id, now=10**11))
            self.assertEqual(before, self.snapshot())
            model.assert_not_called()

    async def test_one_read_request_one_passage_no_player_choice_or_repeat(self):
        before = self.snapshot()[1]
        revision = self.world.progress_json['narrative']['revision']
        for _ in range(2):
            self.assertEqual(stream.control(self.db, self.world, 'read', str(revision)), '')
        async def model(session):
            boot(session)
            scene(session)
            return session.finish({'prose': '许掌柜抖了抖袖口的雨水，望向檐下。'})
        with patch.object(runtime.world_harness, 'run', side_effect=model) as ai:
            self.assertTrue(await runtime.advance(self.db, self.world.id, now=10**11))
            self.assertFalse(self.world.progress_json['stream']['read_requested'])
            self.assertEqual(self.snapshot()[1], before)
            self.assertEqual(self.db.query(PlayerAction).count(), 0)
            # Reconnecting and clicking next consumes the pending paragraph, not a second permit.
            channel = CLIChannel('reading-test', output=io.StringIO())
            channel.user_id = 86213
            await channel.submit('/read ' + str(revision + 1), GameFlow(channel))
            self.db.expire_all()
            self.assertIsNotNone(self.db.query(NarrativeBeat).first().delivered_at)
            self.assertFalse(self.world.progress_json['stream']['read_requested'])
            self.assertIn('许掌柜', channel.output.getvalue())
            with dbmod.SessionLocal() as fresh:
                self.assertIsNone(await runtime.advance(fresh, self.world.id, now=10**11 + 10000))
            self.assertIn('已有新的段落', stream.control(self.db, self.world, 'read', str(revision)))
            self.assertEqual(ai.call_count, 1)
        progress = copy.deepcopy(self.world.progress_json)
        progress['stream']['intervention'] = {'reason': '等待答复'}
        self.world.progress_json = progress
        self.db.commit()
        self.assertIn('不会替你作答', stream.control(self.db, self.world, 'read'))
        self.assertEqual(self.world.progress_json['stream']['intervention'], {'reason': '等待答复'})

    async def test_leaving_reader_before_commit_discards_autonomous_candidate(self):
        stream.control(self.db, self.world, 'read')
        before = self.snapshot()
        active = True
        async def model(session):
            nonlocal active
            boot(session)
            scene(session)
            active = False
            return session.finish({'prose': '许掌柜将药包往怀里拢了拢。'})
        with patch.object(runtime.world_harness, 'run', side_effect=model):
            self.assertIsNone(await runtime.advance(self.db, self.world.id, now=10**11, can_read=lambda: active))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)

    async def test_web_reader_requires_fresh_visible_revision_and_disconnect_clears_it(self):
        from app.web.channel import WebChannel
        channel = WebChannel()
        socket = object()
        await channel.register('u:86213', socket)
        self.assertFalse(channel.reading_ready(86213, 0))
        channel.reader_update(socket, {'revision': 0, 'ready': True})
        self.assertTrue(channel.reading_ready(86213, 0))
        self.assertFalse(channel.reading_ready(86213, 1))
        self.assertFalse(channel.reading_ready(123, 0))
        channel.reader_update(socket, {'revision': 0, 'ready': False})
        self.assertFalse(channel.reading_ready(86213, 0))
        channel.reader_update(socket, {'revision': 0, 'ready': True})
        with patch('app.web.channel.time.monotonic', return_value=10**11):
            self.assertFalse(channel.reading_ready(86213, 0))
        await channel.unregister('u:86213', socket)
        self.assertFalse(channel.reading_ready(86213, 0))

    async def say(self, text, steps, prose='湿润的棉布贴着掌心，盆水沿布角滴落。', request_id=None):
        async def model(session):
            boot(session)
            steps(session)
            return session.finish({'prose': prose})
        with patch.object(runtime.world_harness, 'run', side_effect=model), \
                patch('app.engine.narrative.ai.parse_plan', side_effect=AssertionError('Skills must not use legacy action parsing')):
            return await world_service.record_action(self.db, self.world, self.player, text, request_id=request_id)

    async def test_real_pack_script_changes_composed_objects_and_survives_restart(self):
        def steps(s):
            self.assertTrue(calc(s, 'living', WET)['ok'])
            self.assertTrue(calc(s, 'living', {'operation': 'manipulate', 'target': 'basin', 'property': 'covered',
                'value': True, 'using': ['cloth'], 'method': '将湿棉布盖在盆口'})['ok'])
        ok, _ = await self.say('我把布浸湿，再盖在水盆上。', steps)
        self.assertTrue(ok)
        with dbmod.SessionLocal() as db:
            world, player = db.get(World, self.world.id), db.get(WorldPlayer, self.player.id)
            state = runtime.state_of(world, player, self.pack)
            self.assertTrue(state['objects']['cloth']['properties']['wet'])
            self.assertTrue(state['objects']['basin']['properties']['covered'])
            self.assertEqual(state['objects']['basin']['arrangements']['covered']['using'], ['cloth'])
            self.assertEqual(world.progress_json['narrative']['minute'], 2)
            self.assertEqual(state['player']['resources']['stone'], 12)
            self.assertEqual(db.query(PlayerAction).count(), 1)

    async def test_new_ordinary_object_and_new_combination_do_not_need_action_id(self):
        def steps(s):
            out = calc(s, 'living', {'operation': 'introduce', 'name': '薄木楔', 'material': 'wood', 'purpose': '卡住门边'})
            self.assertTrue(out['ok'], out)
            key = out['object_id']
            out = calc(s, 'living', {'operation': 'manipulate', 'target': 'door', 'property': 'open', 'value': True,
                                   'using': [key], 'method': '把薄木楔垫入门边，使木门保持敞开'})
            self.assertTrue(out['ok'], out)
        ok, _ = await self.say('我取一片薄木头当楔子，垫在门边挡住风。', steps, '薄木楔抵住门边，木门在风里轻轻颤了颤，仍旧敞着。')
        self.assertTrue(ok)
        objects = self.world.progress_json['skill_world']['state']['objects']
        used = objects['door']['arrangements']['open']['using'][0]
        self.assertEqual(objects[used]['name'], '薄木楔')

    async def test_tool_and_message_replays_never_execute_twice(self):
        def steps(s):
            first = calc(s, 'living', WET)
            second = calc(s, 'living', WET)
            self.assertEqual(first, second)
            self.assertEqual(s.minutes, 1)
        first = await self.say('把布浸湿。', steps, request_id='request-1')
        before = self.snapshot()
        with patch.object(runtime.world_harness, 'run', side_effect=AssertionError('must replay')):
            second = await world_service.record_action(self.db, self.world, self.player, '把布浸湿。', request_id='request-1')
            conflict = await world_service.record_action(self.db, self.world, self.player, '改成给我灵石。', request_id='request-1')
        self.assertEqual(first, second)
        self.assertFalse(conflict[0])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.db.query(PlayerAction).count(), 1)

    async def test_scene_knowledge_is_limited_to_witnesses_and_quotes_persist(self):
        def steps(s):
            self.assertTrue(scene(s, speech='借炉的事先别急，我得看一眼你的药材。')['ok'])
            offer = calc(s, 'exchange', {'operation': 'offer', 'npc': 'xu', 'give': {}, 'take': {},
                'terms': '先看药材，再商量借炉', 'service': '帮忙整理药架抵部分炉租'})
            self.assertTrue(offer['ok'], offer)
        before = copy.deepcopy(self.player.private_state['skill_player'])
        ok, _ = await self.say('我想帮忙整理药架，商量一下能否借炉。', steps,
                              '许掌柜将药包拢紧了些。“借炉的事先别急，我得看一眼你的药材。”')
        self.assertTrue(ok)
        with dbmod.SessionLocal() as fresh:
            w, p = fresh.get(World, self.world.id), fresh.get(WorldPlayer, self.player.id)
            state = runtime.state_of(w, p, self.pack)
            memory = next(m for m in state['memories'] if '借炉的事' in m['text'])
            self.assertIn('xu', memory['witnesses'])
            self.assertNotIn('luyan', memory['witnesses'])
            self.assertEqual(state['player'], before)
            self.assertEqual(next(iter(state['offers'].values()))['status'], 'offered')

    async def test_exchange_requires_consent_conserves_stock_and_cannot_repeat(self):
        key = []
        def propose(s):
            out = calc(s, 'exchange', {'operation': 'offer', 'npc': 'xu', 'give': {'herb': 1}, 'take': {'stone': 3},
                                      'terms': '一株灵草换三枚灵石', 'service': ''})
            self.assertTrue(out['ok'], out)
            key.append(out['offer_id'])
        await self.say('一株灵草能换多少灵石？', propose, '许掌柜捻了捻药叶。“完整的，一株三枚。”')
        self.assertEqual(self.player.private_state['skill_player']['resources']['herb'], 3)
        def accept(s):
            self.assertTrue(calc(s, 'exchange', {'operation': 'accept', 'offer': key[0]})['ok'])
        ok, _ = await self.say('成交，就按这个换。', accept, '你递过灵草，许掌柜将三枚灵石放进你摊开的掌心。')
        self.assertTrue(ok)
        state = runtime.state_of(self.world, self.player, self.pack)
        self.assertEqual((state['player']['resources']['stone'], state['player']['resources']['herb']), (15, 2))
        self.assertEqual((state['npcs']['xu']['resources']['stone'], state['npcs']['xu']['resources']['herb']), (77, 13))
        before = copy.deepcopy(state)
        await self.say('我接受刚才那笔报价。', accept, '那次交付已经完成，双方没有再交换东西。')
        self.assertEqual(runtime.state_of(self.world, self.player, self.pack)['player'], before['player'])

    async def test_missing_skill_invalid_paths_forged_state_and_unearned_rewards_rejected(self):
        s = WorldSession(self.pack, self.pack.world['initial'], text='我把布浸湿', mode='player', revision=0, seed='a')
        self.assertFalse(s.call('world_context', {})['ok'])
        boot(s)
        before = copy.deepcopy(s.state)
        self.assertFalse(s.call('read_rule', {'name': '../../.env'})['ok'])
        self.assertFalse(s.call('calculate', {'script': '../../shell', 'arguments_json': '{}', 'quote': s.text})['ok'])
        self.assertFalse(calc(s, 'living', {**WET, 'state': {'stone': 99999}})['ok'])
        self.assertFalse(calc(s, 'living', WET, '玩家没有说过的话')['ok'])
        self.assertFalse(calc(s, 'living', {'operation': 'introduce', 'name': '灵石', 'material': 'wood', 'purpose': '卖钱'})['ok'])
        self.assertEqual(s.state, before)

    async def test_questions_and_unknown_input_never_silently_spend_resources(self):
        s = WorldSession(self.pack, self.pack.world['initial'], text='怎么修炼？', mode='player', revision=0, seed='a')
        boot(s)
        self.assertFalse(calc(s, 'cultivation', {'operation': 'practice', 'minutes': 60})['ok'])
        self.assertEqual(s.minutes, 0)
        ok, _ = await self.say('都说说', lambda s: self.assertTrue(scene(s, speech='先从屋檐下说起吧，这场雨把大家的事都凑到一块了。')['ok']),
                              '许掌柜抬手抹了把额角的水。“先从屋檐下说起吧，这场雨把大家的事都凑到一块了。”')
        self.assertTrue(ok)
        self.assertEqual(self.world.progress_json['narrative']['minute'], 0)

    async def test_study_channel_and_novel_use_of_ability_have_persistent_cost(self):
        def study(s):
            out = calc(s, 'cultivation', {'operation': 'study', 'source': 'dew', 'proposal': {
                'cost': 2, 'power': 5, 'reflection': 10, 'description': '借近水护持周身，牵引轻物。'}})
            self.assertTrue(out['ok'], out)
        await self.say('我研读引露诀。', study, '你循着旧页理清吐纳次序，指尖在最后一行停了许久。')
        self.assertEqual(self.player.private_state['skill_player']['resources']['qi'], 0)
        await self.say('我吐纳一刻，积蓄灵力。', lambda s: self.assertTrue(calc(s, 'cultivation', {'operation': 'channel', 'minutes': 15})['ok']),
                       '吐纳之间，一线温凉的气息渐渐沉入丹田。')
        def use(s):
            self.assertTrue(calc(s, 'living', {'operation': 'manipulate', 'target': 'door', 'property': 'open', 'value': False,
                'using': [], 'spell': 'dew', 'method': '牵引盆中细水线，拨动轻巧门闩将门合上'})['ok'])
        ok, _ = await self.say('用引露诀牵出细水线，拨动门闩把门合上。', use, '水线从盆沿细细升起，绕过门闩轻轻一牵，木门缓缓合拢。')
        self.assertTrue(ok)
        self.assertFalse(self.world.progress_json['skill_world']['state']['objects']['door']['properties']['open'])
        self.assertEqual(self.player.private_state['skill_player']['resources']['qi'], 1)

    async def test_failure_discards_calculation_but_keeps_same_random_ticket(self):
        before = self.snapshot()
        async def failing(s):
            boot(s)
            self.assertTrue(calc(s, 'living', WET)['ok'])
            raise HarnessError('network stopped')
        with patch.object(runtime.world_harness, 'run', side_effect=failing):
            ok, _ = await world_service.record_action(self.db, self.world, self.player, '把布浸湿。')
        self.assertFalse(ok)
        self.assertEqual(before, self.snapshot())
        seed = self.db.query(SkillTurnTicket).one().seed
        await self.say('把布浸湿。', lambda s: (self.assertEqual(s.seed, seed), calc(s, 'living', WET)))
        self.assertEqual(self.db.query(SkillTurnTicket).count(), 1)

    async def test_commit_failure_does_not_leave_partial_state_or_prose(self):
        before = self.snapshot()
        original = self.db.commit
        count = 0
        def fail():
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('commit failed')
            return original()
        with patch.object(self.db, 'commit', side_effect=fail):
            ok, _ = await self.say('把布浸湿。', lambda s: calc(s, 'living', WET))
        self.assertFalse(ok)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    async def test_generation_releases_database_lock_and_cannot_overwrite_new_state(self):
        def steps(s):
            calc(s, 'living', WET)
            with dbmod.SessionLocal() as other:
                world = other.get(World, self.world.id)
                progress = copy.deepcopy(world.progress_json)
                progress['narrative']['revision'] += 1
                world.progress_json = progress
                other.commit()
        ok, _ = await self.say('把布浸湿。', steps)
        self.assertFalse(ok)
        self.db.refresh(self.world)
        self.assertFalse(self.world.progress_json['skill_world']['state']['objects']['cloth']['properties']['wet'])
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    async def test_autonomous_event_pause_pending_delivery_and_restart(self):
        stream.control(self.db, self.world, 'read')
        async def model(s):
            boot(s)
            self.assertEqual(s.mode, 'world')
            self.assertFalse(calc(s, 'cultivation', {'operation': 'practice'})['ok'])
            self.assertTrue(scene(s, speech='借你檐下搁半日药，成么？', intervention=True)['ok'])
            return s.finish({'prose': '许掌柜在檐外站定，药包仍夹在臂弯。“借你檐下搁半日药，成么？”'})
        before = copy.deepcopy(self.player.private_state)
        with patch.object(runtime.world_harness, 'run', side_effect=model) as ai:
            text = await runtime.advance(self.db, self.world.id, now=10**11)
            self.assertTrue(text)
            await runtime.advance(self.db, self.world.id, now=10**11 + 1000)
            self.assertEqual(ai.await_count, 1)
        self.assertEqual(self.player.private_state, before)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 1)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)
        with dbmod.SessionLocal() as db:
            self.assertTrue(db.get(World, self.world.id).progress_json['stream']['intervention'])
        def cannot_answer(s):
            self.assertFalse(scene(s, responded=True)['ok'])
        await self.say('？', cannot_answer, '许掌柜抬了抬手里的药包，仍在等你答复。')
        self.assertTrue(self.world.progress_json['stream']['intervention'])
        stream.control(self.db, self.world, 'pass')
        with patch.object(runtime.world_harness, 'run', side_effect=AssertionError('pending must deliver first')):
            self.assertIsNone(await runtime.advance(self.db, self.world.id, now=10**11 + 2000))

    async def test_bad_prose_cannot_commit_world_changes(self):
        before = self.snapshot()
        ok, _ = await self.say('把布浸湿。', lambda s: calc(s, 'living', WET), '你可以继续修炼。请选择下一步。')
        self.assertFalse(ok)
        self.assertEqual(before, self.snapshot())

    async def test_semantic_repair_restarts_from_original_state_and_commits_once(self):
        self.review.side_effect = [runtime.world_review.Review(consistent=False, issues=['不得把未点燃的炉子写成正在燃烧。']),
                                   runtime.world_review.Review(consistent=True)]
        calls = []
        def steps(s):
            calls.append(s)
            self.assertFalse(s.state['objects']['cloth']['properties']['wet'])
            self.assertTrue(calc(s, 'living', WET)['ok'])
        ok, _ = await self.say('把布浸湿。', steps)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].seed, calls[1].seed)
        self.assertTrue(calls[1].corrections)
        self.assertEqual(self.world.progress_json['narrative']['minute'], 1)
        self.assertEqual(self.db.query(PlayerAction).count(), 1)

    async def test_semantic_failure_does_not_persist_contradictory_memories(self):
        before = self.snapshot()
        self.review.return_value = runtime.world_review.Review(consistent=False, issues=['scene 与实际物件状态矛盾。'])
        ok, _ = await self.say('把布浸湿。', lambda s: (calc(s, 'living', WET), scene(s, detail='炉火在远处亮着。')))
        self.assertFalse(ok)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    async def test_concurrent_same_request_has_one_commit_and_one_seed(self):
        arrived, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def model(s):
            nonlocal count
            boot(s)
            calc(s, 'living', WET)
            count += 1
            if count == 2:
                arrived.set()
            await release.wait()
            return s.finish({'prose': '布浸入水中，灰白的经纬慢慢变深。'})
        async def worker():
            with dbmod.SessionLocal() as db:
                world, player = db.get(World, self.world.id), db.get(WorldPlayer, self.player.id)
                return await world_service.record_action(db, world, player, '我把布浸湿。', request_id='parallel-1')
        with patch.object(runtime.world_harness, 'run', side_effect=model):
            left, right = asyncio.create_task(worker()), asyncio.create_task(worker())
            await asyncio.wait_for(arrived.wait(), 5)
            release.set()
            a, b = await asyncio.gather(left, right)
        self.assertTrue(a[0], a)
        self.assertEqual(a, b)
        self.assertEqual(self.db.query(PlayerAction).count(), 1)
        self.assertEqual(self.db.query(SkillTurnTicket).count(), 1)
        self.db.refresh(self.world)
        self.assertEqual(self.world.progress_json['narrative']['minute'], 1)

    async def test_cancellation_discards_speculative_effects(self):
        before = self.snapshot()
        entered = asyncio.Event()
        async def model(s):
            boot(s)
            calc(s, 'living', WET)
            entered.set()
            await asyncio.Event().wait()
        with patch.object(runtime.world_harness, 'run', side_effect=model):
            task = asyncio.create_task(world_service.record_action(self.db, self.world, self.player, '把布浸湿。'))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    async def test_other_world_keeps_its_own_objects_and_memories(self):
        await self.say('把布浸湿。', lambda s: calc(s, 'living', WET))
        user = world_service.get_or_create_user(self.db, 792156, platform='cli')
        world = world_service.create_world(self.db, user, self.world.script, chat_id=792156)
        player = world_service.join_world(self.db, world, user)
        world_service.start_world(self.db, world)
        state = runtime.state_of(world, player, self.pack)
        self.assertFalse(state['objects']['cloth']['properties']['wet'])
        self.assertEqual(state['memories'], [])

    async def test_pause_during_world_generation_is_not_overwritten(self):
        stream.control(self.db, self.world, 'read')
        async def model(s):
            boot(s)
            scene(s, detail='日光落在庭院里。')
            with dbmod.SessionLocal() as other:
                stream.control(other, other.get(World, self.world.id), 'pause')
            return s.finish({'prose': '日光落在庭院里，草叶上的水珠微微一亮。'})
        with patch.object(runtime.world_harness, 'run', side_effect=model):
            self.assertIsNone(await runtime.advance(self.db, self.world.id, now=10**11))
        self.db.refresh(self.world)
        self.assertTrue(self.world.progress_json['stream']['manual_pause'])
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)

    async def test_arriving_player_interrupts_uncommitted_autonomous_generation(self):
        stream.control(self.db, self.world, 'read')
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        async def model(s):
            boot(s)
            scene(s, detail='风拨动檐下的水纹。')
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        channel = CLIChannel('scan-test', output=io.StringIO())
        channel.user_id = 86213
        channel.streaming_open = True
        with patch.object(runtime.world_harness, 'run', side_effect=model):
            task = asyncio.create_task(stream.scan(channel, now=10**11))
            await asyncio.wait_for(entered.wait(), 3)
            async with stream.input_turn('cli', 86213):
                self.assertTrue(cancelled.is_set())
            await asyncio.wait_for(task, 3)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)
        self.db.refresh(self.world)
        self.assertEqual(self.world.progress_json['narrative']['revision'], 0)

    async def test_cli_entry_uses_pack_and_restores_same_profile(self):
        channel = CLIChannel('Skills试玩', output=io.StringIO())
        self.assertTrue(await open_story(channel, 'changsheng'))
        flow = GameFlow(channel)
        async def model(s):
            boot(s)
            calc(s, 'living', WET)
            return s.finish({'prose': '盆沿的水晃了晃，湿棉布贴上你的掌心。'})
        with patch.object(runtime.world_harness, 'run', side_effect=model):
            await channel.submit('我把旧布浸湿。', flow)
        self.assertFalse(channel.actions)
        resumed = CLIChannel('Skills试玩', output=io.StringIO())
        self.assertTrue(await open_story(resumed, 'changsheng'))
        self.assertIn('湿棉布', resumed.output.getvalue())
        self.assertEqual(self.db.query(World).count(), 2)  # test world plus this CLI profile

    def test_pack_reference_rejects_versions_paths_and_missing_modules(self):
        from app.worlds.packs import for_content
        for name in ('../changsheng', '/etc', 'not-installed'):
            with self.assertRaises(PackError):
                installed(name)
        content = self.pack.content()
        content['world_pack']['hash'] = 'wrong'
        with self.assertRaises(PackError):
            for_content(content)


if __name__ == '__main__':
    unittest.main(verbosity=2)
