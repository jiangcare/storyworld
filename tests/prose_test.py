"""普通回合以正文为主，按需提供菜单；叙述不影响已提交的规则结果。"""
from __future__ import annotations
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.db as dbmod
from tests.support import use_test_database
use_test_database()
from app.ai import narrative as ai
from app.config import settings
from app.engine import world_service
from app.engine.cultivation_story import SEED_CULTIVATION
from app.game.flow import GameFlow
from app.channel.types import ChannelEvent
from app.models import PlayerAction, Script, WorldPlayer


class ProseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dbmod.Base.metadata.drop_all(dbmod.engine)
        dbmod.init_db()
        self.db = dbmod.SessionLocal()
        self.addCleanup(self.db.close)
        key = patch.object(settings, 'deepseek_api_key', '')
        key.start()
        self.addCleanup(key.stop)
        self.script = Script(**copy.deepcopy(SEED_CULTIVATION), source='official', status='approved')
        self.db.add(self.script)
        self.db.commit()
        user = world_service.get_or_create_user(self.db, 7744, platform='telegram')
        self.world = world_service.create_world(self.db, user, self.script, chat_id=7744)
        self.player = world_service.join_world(self.db, self.world, user)
        world_service.start_world(self.db, self.world)
        self.channel = SimpleNamespace(send=AsyncMock(), ack=AsyncMock())
        self.flow = GameFlow(self.channel)

    async def dispatch(self, text):
        await self.flow.dispatch(ChannelEvent(platform='telegram', user_id=7744, chat_id=7744,
                                             is_private=True, text=text, channel=self.channel))
        self.db.expire_all()
        return self.channel.send.call_args.args[1]

    def assert_prose(self, text):
        for redundant in ('现在可以', '回复编号', '—— 世界记录 ——', '已自动保存', '炼丹熟练度', '🎒', '🎯'):
            self.assertNotIn(redundant, text)
        self.assertFalse(self.channel.send.call_args.kwargs.get('actions'))

    async def test_repeated_meditation_varies_sensory_prose_without_panels(self):
        first = await self.dispatch('开始修炼')
        second = await self.dispatch('继续修炼')
        for text in (first, second):
            self.assert_prose(text)
            self.assertIn('经脉', text)
            self.assertIn('修为 +10', text)
            self.assertLess(len(text), 300)
        self.assertNotEqual(first.split('\n\n')[0], second.split('\n\n')[0])
        self.assertEqual(self.player.private_state['cultivation']['practice'], 20)
        self.assertEqual(self.world.progress_json['narrative']['minute'], 120)

    async def test_guide_only_when_requested_and_never_silently_renumbers(self):
        guide = await self.dispatch('/guide')
        self.assertEqual(guide.count('这里没有主线任务'), 1)
        self.assertTrue(self.channel.send.call_args.kwargs['actions'])
        count = self.db.query(PlayerAction).count()
        result = await self.dispatch('1')
        self.assert_prose(result)
        stale = await self.dispatch('1')
        self.assertIn('/guide', stale)
        self.assert_prose(stale)
        self.assertEqual(self.db.query(PlayerAction).count(), count + 1)
        await self.dispatch('/options')
        self.assertTrue(self.channel.send.call_args.kwargs['actions'])

    async def test_queries_answer_only_the_requested_information(self):
        bag = await self.dispatch('储物袋')
        self.assertIn('灵石', bag)
        self.assertNotIn('修为', bag)
        self.assert_prose(bag)
        realm = await self.dispatch('我的境界')
        self.assertIn('炼气1层', realm)
        self.assertNotIn('灵草', realm)
        revival = await self.dispatch('复活规则')
        self.assertIn('次数不限', revival)
        self.assertNotIn('采药', revival)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    async def test_ai_narrates_after_commit_uses_recent_prose_and_saves_compact_result(self):
        await self.dispatch('修炼')
        async def narrate(text, context, receipt):
            with dbmod.SessionLocal() as fresh:
                stored = fresh.get(WorldPlayer, self.player.id)
                self.assertEqual(stored.private_state['cultivation']['practice'], 20)
                self.assertIsNotNone(fresh.query(PlayerAction).order_by(PlayerAction.id.desc()).first().outcome)
            self.assertIn('丹田', context['recent'][0])
            self.assertNotIn('interactions', context)
            self.assertEqual(receipt['xp'], 20)
            return '你循着熟悉的气息再次入定，灵气缓缓流过经脉，归入丹田。'
        with patch.object(settings, 'deepseek_api_key', 'test-only'), patch.object(ai, 'narrate', side_effect=narrate):
            result = await self.dispatch('修炼')
        self.assert_prose(result)
        self.assertEqual(result.count('灵气缓缓'), 1)
        self.assertEqual(result.count('修为 +10'), 1)
        record = self.db.query(PlayerAction).order_by(PlayerAction.id.desc()).first()
        self.assertEqual(record.outcome, result)
        self.assertIn('cultivation', record.intent['receipt'])  # 完整回执仍存档

    async def test_ai_failure_keeps_vivid_fallback_and_commits_only_once(self):
        with patch.object(settings, 'deepseek_api_key', 'test-only'), \
                patch.object(ai, 'narrate', AsyncMock(side_effect=RuntimeError('offline'))):
            result = await self.dispatch('修炼')
        self.assert_prose(result)
        self.assertIn('微凉', result)
        self.assertEqual(self.db.query(PlayerAction).count(), 1)
        self.assertEqual(self.player.private_state['cultivation']['practice'], 10)

    async def test_failed_action_is_concise_and_does_not_call_narrator(self):
        with patch.object(settings, 'deepseek_api_key', 'test-only'), patch.object(ai, 'narrate', AsyncMock()) as narrator:
            result = await self.dispatch('突破')
        self.assertIn('还不足以突破', result)
        self.assert_prose(result)
        self.assertLess(len(result), 100)
        narrator.assert_not_called()
        self.assertEqual(self.world.progress_json['narrative']['minute'], 0)

    async def test_narrator_prompt_and_format_validation(self):
        context = {'sandbox': True, 'recent': ['先前已发生的吐纳'], 'location': {'name': '洞府'}}
        receipt = {'results': [{'ok': True, 'text': '修为 +10'}]}
        with patch.object(ai.client, 'chat_json', AsyncMock(return_value={'narrative': '灵气沿经脉缓缓游走。'})) as call:
            self.assertEqual(await ai.narrate('RAW-PLAYER-INPUT', context, receipt), '灵气沿经脉缓缓游走。')
        self.assertIn('小说', call.call_args.args[0])
        self.assertIn('不要菜单', call.call_args.args[0])
        self.assertNotIn('RAW-PLAYER-INPUT', call.call_args.args[1])
        for output in ('现在可以：\n1. 修炼\n2. 采药', '灵气' * 300, '🎒 储物袋：灵石12'):
            with patch.object(ai.client, 'chat_json', AsyncMock(return_value={'narrative': output})):
                with self.assertRaises(ValueError):
                    await ai.narrate('修炼', context, receipt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
