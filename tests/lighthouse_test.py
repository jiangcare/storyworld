"""孤岛灯塔：即时结果、重读、开场存档升级与原对话回归。"""
from __future__ import annotations
import copy
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.db as dbmod
from tests.support import use_test_database
use_test_database()
from seed import LEGACY_LIGHTHOUSE, SEED_SCRIPTS
from app.engine import world_service, narrative, guidance, tick
from app.engine.lighthouse import LETTER
from app.engine.script_upgrades import upgrade_lighthouse
from app.engine.script_dsl import validate_script
from app.game.flow import GameFlow
from app.channel.types import ChannelEvent
from app.models import PlayerAction, Scene, Script, World


class LighthouseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dbmod.Base.metadata.drop_all(dbmod.engine)
        dbmod.init_db()
        self.db = dbmod.SessionLocal()
        self.addCleanup(self.db.close)
        mock = patch('app.engine.narrative.ai.parse_plan', AsyncMock(side_effect=AssertionError('不能用模型猜信件')))
        self.parser = mock.start()
        self.addCleanup(mock.stop)
        mock = patch('app.engine.narrative.ai.narrate', AsyncMock(side_effect=RuntimeError('offline')))
        self.narrator = mock.start()
        self.addCleanup(mock.stop)

    def make_world(self, legacy=False, edited=False):
        edition = copy.deepcopy(LEGACY_LIGHTHOUSE if legacy else SEED_SCRIPTS[1])
        if edited:
            edition['content_json']['world']['background'] = '作者修改后的背景'
        script = Script(**edition, source='official', status='approved')
        self.db.add(script)
        self.db.commit()
        user = world_service.get_or_create_user(self.db, 8678, platform='telegram')
        world = world_service.create_world(self.db, user, script, chat_id=8678)
        player = world_service.join_world(self.db, world, user)
        world_service.start_world(self.db, world)
        return world, player, script

    async def test_read_letter_returns_authored_result_immediately_even_without_ai(self):
        world, player, _ = self.make_world()
        self.assertEqual(validate_script(world.script.content_json), [])
        ok, result = await world_service.record_action(self.db, world, player, '漂流瓶的信写的是什么')
        self.assertTrue(ok)
        self.assertIn('你父亲不是死于意外。来灯塔地下室，那里有答案。', result)
        self.assertNotIn('已记录你的行动', result)
        self.assertEqual(world.progress_json['narrative']['minute'], 1)
        self.assertEqual(player.private_state['xp'], 5)
        self.assertIn('letter_read', world.progress_json['narrative']['flags'])
        self.assertIsNotNone(self.db.query(PlayerAction).one().outcome)
        self.parser.assert_not_called()
        self.narrator.assert_not_called()
        self.assertIsNone(await tick.run_tick(self.db, world.id))

    async def test_repeated_letter_question_rereads_without_consuming_time_or_reward(self):
        world, player, _ = self.make_world()
        await world_service.record_action(self.db, world, player, '读信')
        before = copy.deepcopy((world.progress_json, player.private_state))
        ok, result = await world_service.record_action(self.db, world, player, '信件内容是什么？')
        self.assertTrue(ok)
        self.assertIn(LETTER, result)
        self.assertEqual(before, (world.progress_json, player.private_state))
        self.assertEqual(self.db.query(PlayerAction).count(), 1)

    async def test_original_transcript_gets_location_rules_followup_and_letter(self):
        self.make_world()
        channel = SimpleNamespace(send=AsyncMock(), ack=AsyncMock())
        flow = GameFlow(channel)
        for text, expected in [('这是哪', '灯塔基座'), ('游戏规则时什么', '问玩法、查看状态和观察不耗时'),
                               ('都说说', '问玩法、查看状态和观察不耗时'),
                               ('漂流瓶的信写的是什么', '你父亲不是死于意外'),
                               ('信件内容是什么', '你父亲不是死于意外')]:
            await flow.dispatch(ChannelEvent(platform='telegram', user_id=8678, chat_id=8678,
                                             is_private=True, channel=channel, text=text))
            self.assertIn(expected, channel.send.call_args.args[1])
        self.db.expire_all()
        self.assertEqual(self.db.query(PlayerAction).count(), 1)
        self.parser.assert_not_called()

    async def test_full_story_can_reach_ending_with_numbered_choices(self):
        world, player, _ = self.make_world()
        for _ in range(12):
            if world.status == 'finished':
                break
            await guidance.remember(world, player)
            ok, _ = await world_service.record_action(self.db, world, player, '1')
            self.assertTrue(ok)
        self.assertEqual(world.status, 'finished')
        self.assertIn('带信离岛', world.progress_json['narrative']['ending'])

    async def test_pending_day_one_save_is_upgraded_without_losing_history(self):
        world, player, old = self.make_world(legacy=True)
        old_content = copy.deepcopy(old.content_json)
        original_id = world.id
        for text in ['漂流瓶的信写的是什么', '信件内容是什么']:
            self.db.add(PlayerAction(world_id=world.id, user_id=player.user_id, day=1, text=text))
        self.db.commit()
        pending_ids = [a.id for a in self.db.query(PlayerAction)]
        ok, result = await world_service.record_action(self.db, world, player, '信件内容是什么')
        self.assertTrue(ok)
        self.assertIn('你父亲不是死于意外', result)
        self.assertEqual(world.id, original_id)
        self.assertNotEqual(world.script_id, old.id)
        self.assertEqual(old.content_json, old_content)
        self.assertEqual(old.status, 'disabled')
        self.assertEqual(world.progress_json['legacy_daily']['action_ids'], pending_ids)
        self.assertEqual(self.db.query(PlayerAction).count(), 3)
        self.assertEqual(player.private_state['xp'], 5)
        for action_id in pending_ids:
            self.assertIsNone(self.db.get(PlayerAction, action_id).outcome)

    def test_advanced_save_and_custom_story_are_not_rewritten(self):
        world, player, old = self.make_world(legacy=True)
        world.day = 2
        world.last_tick_at = datetime.now()
        self.db.add(Scene(world_id=world.id, user_id=player.user_id, day=1, narrative='已有经历', suggested_actions=[]))
        self.db.commit()
        state = copy.deepcopy(player.private_state)
        upgrade_lighthouse(self.db)
        self.assertEqual(world.script_id, old.id)
        self.assertEqual(world.day, 2)
        self.assertEqual(player.private_state, state)
        self.assertFalse(narrative.enabled(world.script.content_json))
        _, _, custom = self.make_world(legacy=True, edited=True)
        upgrade_lighthouse(self.db)
        self.assertEqual(custom.status, 'approved')
        self.assertFalse(narrative.enabled(custom.content_json))

    def test_upgrade_is_idempotent_and_old_buttons_are_removed_from_list(self):
        self.make_world(legacy=True)
        for _ in range(2):
            available = world_service.list_approved_scripts(self.db)
            self.assertEqual([s.title for s in available], ['孤岛灯塔 · 即时探索'])
        self.assertEqual(self.db.query(Script).count(), 2)

    def test_failed_upgrade_rolls_back_script_and_save_together(self):
        world, player, old = self.make_world(legacy=True)
        state = copy.deepcopy(player.private_state)
        with patch('app.engine.narrative.initialize', side_effect=RuntimeError('stop')):
            with self.assertRaises(RuntimeError):
                upgrade_lighthouse(self.db)
        self.assertEqual(self.db.query(Script).count(), 1)
        self.assertEqual(old.status, 'approved')
        self.assertEqual(world.script_id, old.id)
        self.assertEqual(player.private_state, state)


if __name__ == '__main__':
    unittest.main(verbosity=2)
