"""叙事时钟独立于玩家消息，持久化介入点和世界事件不越过玩家主导权。"""
from __future__ import annotations
import asyncio
import copy
import io
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.db as dbmod
from tests.support import use_test_database
use_test_database()
from app.channel.base import Channel
from app.channel.cli import CLIChannel
from app.engine import stream, world_service
from app.engine.cultivation_story import SEED_CULTIVATION
from app.engine.script_dsl import validate_script
from app.models import NarrativeBeat, PlayerAction, Script, World, WorldPlayer
from app.channel.types import ChannelCapabilities
from app.ai.prose_contract import validate_prose
from app.config import settings


class WatchingChannel(Channel):
    capabilities = ChannelCapabilities(name='cli', supports_private_push=True)
    def __init__(self):
        self.messages = []
        self.online = True
        self.fail = False
    def stream_present(self, uid):
        return self.online
    async def send_text(self, chat_id, text):
        return await self.send(chat_id, text)
    async def _send_with_actions(self, chat_id, text, actions):
        if self.fail:
            return None
        self.messages.append(text)
        return len(self.messages)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        key = patch.object(settings, 'deepseek_api_key', '')
        key.start()
        self.addCleanup(key.stop)
        dbmod.Base.metadata.drop_all(dbmod.engine)
        dbmod.init_db()
        self.db = dbmod.SessionLocal()
        self.addCleanup(self.db.close)
        script = Script(**copy.deepcopy(SEED_CULTIVATION), status='approved', source='official')
        self.db.add(script)
        self.db.commit()
        user = world_service.get_or_create_user(self.db, 5566, platform='cli')
        self.world = world_service.create_world(self.db, user, script, chat_id=5566)
        self.player = world_service.join_world(self.db, self.world, user)
        world_service.start_world(self.db, self.world)
        self.channel = WatchingChannel()
        self.now = time.time()

    async def scan(self, offset):
        await stream.scan(self.channel, self.now + offset)
        self.db.expire_all()

    async def paused_scene(self):
        for offset in (0, 20, 40, 60):
            await self.scan(offset)

    async def test_three_narrator_messages_without_player_input_then_pause(self):
        before = copy.deepcopy(self.player.private_state)
        await self.paused_scene()
        self.assertEqual(len(self.channel.messages), 3)
        self.assertEqual(len(set(self.channel.messages)), 3)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)
        self.assertEqual(self.player.private_state, before)
        self.assertEqual(self.world.progress_json['narrative']['minute'], 3)
        self.assertTrue(self.world.progress_json['stream']['intervention'])
        self.assertEqual(self.world.progress_json['stream']['world']['npcs']['elder']['location'], 'cave')
        snapshot = copy.deepcopy(self.world.progress_json)
        for offset in (80, 100, 10000):
            await self.scan(offset)
        self.assertEqual(snapshot, self.world.progress_json)
        self.assertEqual(len(self.channel.messages), 3)
        for text in self.channel.messages:
            self.assertEqual(validate_prose(text), text)

    async def test_observation_and_queries_do_not_resolve_but_player_action_does(self):
        await self.paused_scene()
        for text in ('观察', '我的境界', '你好'):
            await world_service.record_action(self.db, self.world, self.player, text)
            self.assertTrue(self.world.progress_json['stream']['intervention'])
        await world_service.record_action(self.db, self.world, self.player, '去青岚谷')
        self.assertIsNone(self.world.progress_json['stream']['intervention'])
        await self.scan(100)
        self.assertEqual(len(self.channel.messages), 4)
        self.assertIn('溪水', self.channel.messages[-1])

    async def test_restart_keeps_cursor_intervention_and_recent_world_prose(self):
        await self.paused_scene()
        with dbmod.SessionLocal() as fresh:
            world = fresh.get(World, self.world.id)
            player = fresh.get(WorldPlayer, self.player.id)
            self.assertTrue(world.progress_json['stream']['intervention'])
            self.assertIn('叩门声', world_service.build_player_recent(fresh, world, player))
            self.assertIsNone(stream.advance(fresh, world.id, self.now + 10000))
        self.assertEqual(self.db.query(NarrativeBeat).count(), 3)

    async def test_failed_delivery_retries_same_record_without_reexecuting_event(self):
        self.channel.fail = True
        await self.scan(0)
        await self.scan(20)
        await self.scan(200)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 1)
        self.assertEqual(self.world.progress_json['narrative']['minute'], 1)
        self.channel.fail = False
        await self.scan(201)
        self.assertEqual(len(self.channel.messages), 1)
        self.assertIsNotNone(self.db.query(NarrativeBeat).one().delivered_at)
        # 下一次扫描才可以提交新的事件。
        await self.scan(202)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 2)

    async def test_simultaneous_scanners_and_player_input_lock(self):
        await self.scan(0)
        await asyncio.gather(stream.scan(self.channel, self.now + 20), stream.scan(self.channel, self.now + 20))
        self.assertEqual(self.db.query(NarrativeBeat).count(), 1)
        async with stream.input_turn('cli', 5566):
            await self.scan(100)
            self.assertEqual(self.db.query(NarrativeBeat).count(), 1)
        await self.scan(101)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 2)

    async def test_offline_does_not_advance_or_catch_up_in_a_burst(self):
        await self.scan(0)
        self.channel.online = False
        await self.scan(10000)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)
        self.channel.online = True
        await self.scan(20000)
        await self.scan(20000)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 1)

    async def test_controls_separate_three_autonomies_and_preserve_agency(self):
        before = copy.deepcopy(self.player.private_state)
        result = stream.control(self.db, self.world, 'autonomy', 'world=95 player=30 narrative=85')
        self.assertIn('World 95 / Player 30 / Narrative 85', result)
        stream.control(self.db, self.world, 'pause')
        await self.scan(100)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)
        stream.control(self.db, self.world, 'stream', 'on')
        stream.control(self.db, self.world, 'autonomy', 'world=0 player=100 narrative=100')
        await self.scan(200)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)
        stream.control(self.db, self.world, 'autonomy', 'world=95 narrative=0')
        await self.scan(300)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)
        stream.control(self.db, self.world, 'autonomy', 'narrative=100')
        await self.scan(400)
        self.assertEqual(self.player.private_state, before)
        self.assertEqual(stream.interval({'narrative_autonomy': 100}), 10)
        self.assertEqual(stream.interval({'narrative_autonomy': 0}), 60)

    async def test_world_eligibility_and_player_detail_are_independent(self):
        content = copy.deepcopy(self.world.script.content_json)
        content['narrative']['stream'] = {'world_autonomy': 50, 'player_autonomy': 0, 'scenes': {'cave': [
            {'text': '门外的脚步忽然停住。', 'minimum_world_autonomy': 70},
            {'text': '风贴着石壁缓缓掠过。', 'player_detail': '衣袖的边角轻轻动了一下。'},
        ]}}
        self.assertEqual(validate_script(content), [])
        self.world.script.content_json = content
        self.db.commit()
        before = copy.deepcopy(self.player.private_state)
        await self.scan(0)
        await self.scan(20)
        self.assertEqual(self.channel.messages[-1], '风贴着石壁缓缓掠过。')
        stream.control(self.db, self.world, 'autonomy', 'player=30')
        await self.scan(40)
        self.assertIn('衣袖的边角', self.channel.messages[-1])
        self.assertNotIn('脚步', self.channel.messages[-1])
        self.assertEqual(self.player.private_state, before)

    async def test_manual_unpause_does_not_solve_intervention_but_pass_is_explicit(self):
        await self.paused_scene()
        stream.control(self.db, self.world, 'pause')
        stream.control(self.db, self.world, 'stream', 'on')
        await self.scan(100)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 3)
        stream.control(self.db, self.world, 'pass')
        await self.scan(120)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 4)

    async def test_invalid_controls_and_commit_failure_are_atomic(self):
        await self.scan(0)
        snapshot = copy.deepcopy(self.world.progress_json)
        stream.control(self.db, self.world, 'autonomy', 'world=0 player=999')
        self.assertEqual(self.world.progress_json, snapshot)
        with patch.object(self.db, 'commit', side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):
                stream.advance(self.db, self.world.id, self.now + 20)
        self.assertEqual(self.world.progress_json, snapshot)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 0)

    def test_event_schema_cannot_contain_player_effects(self):
        content = copy.deepcopy(SEED_CULTIVATION['content_json'])
        content['narrative']['stream'] = {'scenes': {'cave': [{'text': '风吹过石门。', 'hp': -10}]}}
        self.assertTrue(validate_script(content))
        content['narrative']['stream']['scenes']['cave'] = [{'text': '风吹过石门。', 'npcs': {'player': {'location': 'cave', 'activity': '加入宗门'}}}]
        self.assertTrue(validate_script(content))

    def test_contract_allows_npc_question_and_rejects_assistant_or_state_leaks(self):
        self.assertEqual(validate_prose('门外的人问：“你叫什么名字？”'), '门外的人问：“你叫什么名字？”')
        for text in ('你准备怎么办？', '接下来你想做什么？', '请选择：\n1. 开门', '修为 +10',
                     '你决定拼死保护那个女孩。', '已自动保存', '你可以前往山门。',
                     'EVENT:\nzombie_group_arrived\ncount: 7\nfear: +18'):
            with self.assertRaises(ValueError, msg=text):
                validate_prose(text)

    async def test_cli_keeps_streaming_while_input_is_blocked(self):
        from run_cli import play
        done = threading.Event()
        class Terminal(CLIChannel):
            async def _send_with_actions(inner, chat_id, text, actions):
                result = await super(Terminal, inner)._send_with_actions(chat_id, text, actions)
                if '叩门声' in text:
                    done.set()
                return result
        terminal = Terminal('stream-test', output=io.StringIO())
        terminal.user_id = 5566
        def reader(_):
            done.wait(10)
            return '/quit'
        with patch('app.engine.stream.interval', return_value=0):
            await play(terminal, reader)
        self.assertTrue(done.is_set(), terminal.output.getvalue())
        self.assertEqual(self.db.query(PlayerAction).count(), 0)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 3)

    async def test_disconnected_input_releases_lock_under_repeated_cancellation(self):
        import anyio
        with anyio.CancelScope() as scope:
            async with stream.input_turn('cli', 5566):
                scope.cancel()
                await anyio.sleep(0)
        store = await dbmod.get_store()
        self.assertIsNone(await store.get('stream:lock:cli:5566'))
        self.assertIsNone(await store.get('stream:waiting:cli:5566'))
        await self.scan(0)
        await self.scan(20)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 1)

    async def test_autonomous_ai_polishes_committed_event_once_then_retries_same_prose(self):
        before = copy.deepcopy(self.player.private_state)
        async def narrate(text, context, receipt):
            with dbmod.SessionLocal() as fresh:
                self.assertEqual(fresh.query(NarrativeBeat).count(), 1)
                self.assertEqual(fresh.get(WorldPlayer, self.player.id).private_state, before)
            self.assertTrue(context['autonomous_world_event'])
            self.assertIn('石门', receipt['results'][0]['text'])
            return '风声沿着石门细窄的缝隙滑进来，蒲团旁一角微光轻轻晃动。'
        await self.scan(0)
        self.channel.fail = True
        with patch.object(settings, 'deepseek_api_key', 'test'), patch('app.ai.narrative.narrate', AsyncMock(side_effect=narrate)) as writer:
            await self.scan(20)
            self.channel.fail = False
            await self.scan(100)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 1)
        self.assertIn('蒲团旁一角微光', self.channel.messages[0])
        self.assertEqual(self.player.private_state, before)

    def test_web_pushes_without_another_player_message(self):
        from fastapi.testclient import TestClient
        from app.web.main import app
        def receive(ws):
            while True:
                packet = ws.receive_json()
                if packet['type'] == 'msg':
                    return packet
        with patch('app.engine.stream.interval', return_value=0), TestClient(app) as client:
            user = client.post('/api/web/register', json={'nickname': '静静读小说'}).json()
            headers = {'cookie': f"sw_web={client.cookies.get('sw_web')}"}
            with client.websocket_connect(f"/ws/web?conv=u:{user['id']}", headers=headers) as ws:
                ws.receive_json()
                ws.send_json({'type': 'action', 'payload': f'mk:{self.world.script_id}'})
                receive(ws)
                paragraphs = [receive(ws) for _ in range(3)]
                self.assertIn('叩门声', paragraphs[-1]['text'])
                self.assertTrue(all(not p['actions'] for p in paragraphs))
                ws.send_json({'type': 'ping'})
                self.assertEqual(ws.receive_json()['type'], 'pong')
        self.assertEqual(self.db.query(PlayerAction).count(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
