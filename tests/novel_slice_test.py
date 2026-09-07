"""十分钟体验的可自动验证部分；不以机器测试代替真人阅读评价。"""
from __future__ import annotations
import copy
import io
import json
import os
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.stream_test import WatchingChannel, dbmod, settings
from app.ai.client import client
from app.ai.prose_contract import validate_prose
from app.channel.cli import CLIChannel
from app.engine import stream, world_service
from app.engine.novel_slice import CONTENT, OPENING, SEED_SLICE
from app.engine.script_dsl import validate_script
from app.models import NarrativeBeat, PlayerAction, Script, World
from run_cli import open_demo


class NovelSliceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        key = patch.object(settings, 'deepseek_api_key', '')
        key.start()
        self.addCleanup(key.stop)
        dbmod.Base.metadata.drop_all(dbmod.engine)
        dbmod.init_db()
        self.db = dbmod.SessionLocal()
        self.addCleanup(self.db.close)
        self.script = Script(**copy.deepcopy(SEED_SLICE), status='approved', source='official')
        self.db.add(self.script)
        self.db.commit()
        self.user = world_service.get_or_create_user(self.db, 5566, platform='cli')
        self.world = world_service.create_world(self.db, self.user, self.script, chat_id=5566)
        self.player = world_service.join_world(self.db, self.world, self.user)
        world_service.start_world(self.db, self.world)
        self.channel = WatchingChannel()
        self.now = time.time()

    async def scan(self, seconds):
        await stream.scan(self.channel, self.now + seconds)
        self.db.expire_all()

    async def say(self, text):
        return await world_service.record_action(self.db, self.world, self.player, text)

    async def test_ten_minutes_twenty_events_no_input_no_player_effects(self):
        self.assertEqual(validate_script(CONTENT), [])
        self.assertEqual(len(CONTENT['npcs']), 3)
        self.assertEqual(len(CONTENT['narrative']['locations']), 1)
        before = copy.deepcopy(self.player.private_state)
        await self.scan(0)
        for step in range(1, 21):
            await self.scan(step * 30 - 1)
            self.assertEqual(len(self.channel.messages), step - 1)
            await self.scan(step * 30)
            self.assertEqual(len(self.channel.messages), step)
            self.assertEqual(self.world.progress_json['narrative']['minute'], step)
            self.assertEqual(bool(self.world.progress_json['stream']['intervention']), step == 20)
        self.assertEqual(len(set(self.channel.messages)), 20)
        self.assertEqual(self.player.private_state, before)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)
        self.assertEqual(self.db.query(NarrativeBeat).count(), 20)
        self.assertEqual(self.world.progress_json['stream']['world']['npcs']['wang']['fear'], 18)
        for text in [OPENING, *self.channel.messages]:
            self.assertEqual(validate_prose(text), text)
        before = copy.deepcopy(self.world.progress_json)
        await self.scan(10000)
        self.assertEqual(before, self.world.progress_json)
        with dbmod.SessionLocal() as restarted:
            world = restarted.get(World, self.world.id)
            self.assertIsNone(stream.advance(restarted, world.id, self.now + 10001))
        stream.control(self.db, self.world, 'pass')
        await self.scan(10002)
        self.assertEqual(len(self.channel.messages), 20)  # /pass 不能重播剧本

    async def test_hide_changes_pov_without_rewinding_and_restart_keeps_branch(self):
        for step in range(5):
            await self.scan(step * 30)
        _, text = await self.say('我不进去，我躲到对面的车后面观察。')
        self.assertIn('车身', text)
        self.assertTrue(self.world.progress_json['narrative']['flags']['hidden'])
        await self.scan(150)
        self.assertEqual(self.world.progress_json['stream']['cursors']['store'], 5)
        self.assertIn('老人把凳子', self.channel.messages[-1])
        self.assertNotIn('以前进货', self.channel.messages[-1])  # 车后听不清室内对白
        record = self.db.query(NarrativeBeat).order_by(NarrativeBeat.id.desc()).first()
        self.assertEqual(record.receipt['branch'], {'hidden': True})
        with dbmod.SessionLocal() as restarted:
            stream.advance(restarted, self.world.id, self.now + 180)
        await self.scan(180)  # 先投递重启进程已经提交的段落
        for step in range(7, 21):
            await self.scan(step * 30)
        self.assertIn('半辆车', self.channel.messages[-1])
        self.assertEqual(self.db.query(PlayerAction).count(), 1)
        self.assertEqual(self.player.private_state['inventory'], {})

    async def test_freeform_compound_intent_changes_npc_plan_not_only_prose(self):
        for step in range(5):
            await self.scan(step * 30)
        parser = AsyncMock(return_value={'scope': 'gameplay', 'actions': [
            {'kind': 'interact', 'target': 'barricade'}, {'kind': 'interact', 'target': 'ask_exit'}]})
        with patch.object(client, 'chat_json', parser):
            ok, text = await self.say('我先把门堵死，问他们这里有没有后门。')
        self.assertTrue(ok)
        self.assertIn('木楔', text)
        self.assertIn('房东封了', text)
        context = json.loads(parser.call_args.args[1])['context']
        self.assertEqual(len(context['recent']), 2)
        self.assertIn('world_state', context)
        for step in range(5, 8):
            await self.scan(step * 30)
        npc = self.world.progress_json['stream']['world']['npcs']['wang']
        self.assertEqual(npc['activity'], '扶住老赵的凳子')
        self.assertIn('你卡在轮下的木楔', self.channel.messages[-1])
        for step in range(8, 21):
            await self.scan(step * 30)
        self.assertIn('货架和木楔仍守在原位', self.channel.messages[-1])
        self.assertNotIn('歪轮猛地折', self.channel.messages[-1])

    async def test_inventory_is_silent_and_duplicate_taking_does_not_award_again(self):
        await self.say('堵门')
        ok, text = await self.say('拿水和饼干')
        self.assertTrue(ok)
        self.assertIn('三瓶', text)
        self.assertEqual(validate_prose(text), text)
        self.assertEqual(self.player.private_state['inventory'], {'water': 3, 'biscuit': 2})
        before = copy.deepcopy(self.player.private_state)
        await self.say('拿水和饼干')
        self.assertEqual(before, self.player.private_state)
        status = world_service.build_player_status_message(self.player, self.world, CONTENT)
        self.assertIn('矿泉水', status)
        self.assertIn('×3', status)
        self.assertNotIn('×3', text)

    async def test_unknown_intent_responds_without_forcing_action_or_stopping_clock(self):
        await self.scan(0)
        await self.scan(30)
        before = copy.deepcopy((self.player.private_state, self.world.progress_json))
        with patch.object(client, 'chat_json', AsyncMock(return_value={'scope': 'out_of_scope', 'actions': [],
                'reply': '要让车发出声音，还需要说明你打算动它的哪个部位。'})):
            _, text = await self.say('我把车改成声源引开他们')
        self.assertIn('哪个部位', text)
        self.assertEqual(before, (self.player.private_state, self.world.progress_json))
        with patch.object(client, 'chat_json', AsyncMock(side_effect=RuntimeError('offline'))):
            _, fallback = await self.say('你好')
        self.assertNotIn('选', fallback)
        self.assertNotIn('我等你', fallback)
        self.assertEqual(before, (self.player.private_state, self.world.progress_json))
        await self.scan(60)
        self.assertEqual(len(self.channel.messages), 2)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    async def test_demo_entry_is_prose_only_and_resume_does_not_create_new_world(self):
        output = io.StringIO()
        channel = CLIChannel('切片直达', output=output)
        self.assertTrue(await open_demo(channel))
        self.assertEqual(output.getvalue().strip(), OPENING)
        count = self.db.query(World).count()
        self.assertTrue(await open_demo(channel))
        self.assertEqual(self.db.query(World).count(), count)

    def test_real_demo_cli_entrypoint_and_saved_inventory(self):
        root = Path(__file__).resolve().parent.parent
        with TemporaryDirectory(prefix='novel-demo-cli-') as directory:
            env = dict(os.environ, DATABASE_URL='sqlite:///' + str(Path(directory) / 'game.db'),
                       DEEPSEEK_API_KEY='', TELEGRAM_BOT_TOKEN='', PYTHONIOENCODING='utf-8')
            command = [sys.executable, str(root / 'run_cli.py'), '--demo']
            first = subprocess.run(command, input='堵门\n拿水和饼干\n/quit\n', text=True, encoding='utf-8',
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=directory, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn(OPENING, first.stdout)
            self.assertIn('三瓶', first.stdout)
            self.assertNotIn('输入编号', first.stdout)
            second = subprocess.run(command, input='/status\n/quit\n', text=True, encoding='utf-8',
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=directory, timeout=30)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn('矿泉水 ×3', second.stdout)
            self.assertNotIn(OPENING, second.stdout)

    async def test_ai_failures_have_diagnostics_and_do_not_blame_player_or_change_state(self):
        from app.ai.client import LLMError
        from app.ai.diagnostics import configure_cli_log, logger
        before = copy.deepcopy((self.player.private_state, self.world.progress_json))
        secret = 'sensitive-provider-response-do-not-log'
        with TemporaryDirectory(prefix='ai-error-log-') as directory:
            path = configure_cli_log(Path(directory) / 'ai.log')
            try:
                cases = [(LLMError(secret, code='missing_api_key'), 'DEEPSEEK_API_KEY'),
                         (LLMError(secret, code='harness_setup'), '运行环境'),
                         (LLMError(secret), '服务暂时'), (ValueError(secret), '未能正常生成')]
                for error, expected in cases:
                    with patch.object(client, 'chat_json', AsyncMock(side_effect=error)):
                        ok, text = await self.say('我想看看窗边有什么')
                    self.assertFalse(ok)
                    self.assertIn(expected, text)
                    self.assertNotIn('请说清', text)
                    self.assertNotIn(secret, text)
                    self.assertEqual(before, (self.player.private_state, self.world.progress_json))
                log = path.read_text()
                self.assertIn('code=missing_api_key', log)
                self.assertIn('code=harness_setup', log)
                self.assertIn('code=invalid_ai_response', log)
                self.assertNotIn(secret, log)
                self.assertNotIn('我想看看', log)
            finally:
                for handler in logger.handlers[:]:
                    if getattr(handler, '_storyworld_cli', False):
                        logger.removeHandler(handler)
                        handler.close()
                logger.propagate = True
        self.assertEqual(self.db.query(PlayerAction).count(), 0)

    def test_contract_examples_and_branch_schema_are_enforced(self):
        for text in ['TIME: 18:46 → 18:48', '事件触发：丧尸群到达。', '王强恐惧值增加18。',
                     '时间经过2分钟。', '【系统】获得矿泉水×3。', 'Inventory updated']:
            with self.assertRaises(ValueError, msg=text):
                validate_prose(text)
        self.assertEqual(validate_prose('“怎么会有这么多？”'), '“怎么会有这么多？”')
        for mutation in ({'hp': -10}, {'npcs': {'unknown': {'location': 'store', 'activity': '走近'}}}):
            content = copy.deepcopy(CONTENT)
            content['narrative']['stream']['scenes']['store'][0]['variants'][0].update(mutation)
            self.assertTrue(validate_script(content))


if __name__ == '__main__':
    unittest.main()
