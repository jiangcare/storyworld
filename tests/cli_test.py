"""终端真实游戏流、SQLite 档案、编号按钮与进程重启回归。"""
from __future__ import annotations

import copy
import io
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.db as dbmod
from tests.support import use_test_database
use_test_database()
from app.channel.cli import CLIChannel, profile_id
from app.channel.types import Action
from app.engine import world_service
from app.game.flow import GameFlow
from app.models import AdminUser, PlayerAction, Script, User, World, WorldPlayer
from run_cli import play, prepare_database
from seed import LEGACY_LIGHTHOUSE


class CLITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dbmod.Base.metadata.drop_all(dbmod.engine)
        prepare_database()
        self.output = io.StringIO()
        self.channel = CLIChannel('青岚客', output=self.output)
        self.flow = GameFlow(self.channel)
        self.db = dbmod.SessionLocal()
        self.addCleanup(self.db.close)
        for target in ('app.engine.narrative.ai.parse_plan', 'app.engine.narrative.ai.narrate'):
            mock = patch(target, AsyncMock(side_effect=AssertionError('基础流程不应依赖 AI')))
            mock.start()
            self.addCleanup(mock.stop)

    async def create_cultivation(self):
        await self.channel.welcome(self.flow)
        index = next(i for i, action in enumerate(self.channel.actions, 1) if '长生录' in action.label)
        await self.channel.submit(str(index), self.flow)
        self.db.expire_all()
        world = self.db.query(World).order_by(World.id.desc()).first()
        return world, self.db.query(WorldPlayer).filter_by(world_id=world.id).one()

    def test_prepare_is_idempotent_and_does_not_create_admin_account(self):
        count = self.db.query(Script).count()
        prepare_database()
        self.assertEqual(self.db.query(Script).count(), count)
        self.assertEqual(self.db.query(AdminUser).count(), 0)
        self.assertEqual(profile_id('青岚客'), profile_id(' 青岚客 '))
        self.assertNotEqual(profile_id('甲'), profile_id('乙'))
        with self.assertRaises(ValueError):
            CLIChannel('\x1b[31m')

    async def test_create_action_number_and_resume_in_new_channel(self):
        world, player = await self.create_cultivation()
        index = next(i for i, action in enumerate(self.channel.actions, 1) if '静坐修炼' in action.label)
        await self.channel.submit(str(index), self.flow)
        self.assertIn('修为 +10', self.output.getvalue())
        self.db.refresh(player)
        self.assertEqual(player.private_state['cultivation']['practice'], 10)
        self.assertEqual(self.db.query(PlayerAction).count(), 1)
        self.assertEqual(self.db.get(User, player.user_id).platform, 'cli')
        resumed = CLIChannel('青岚客', output=io.StringIO())
        await resumed.welcome(GameFlow(resumed))
        self.assertIn('修为 10/30', resumed.output.getvalue())
        self.assertIn('长生录', resumed.output.getvalue())
        self.assertTrue(resumed.actions)

    async def test_profiles_and_platforms_do_not_share_saves(self):
        world, player = await self.create_cultivation()
        world_service.get_or_create_user(self.db, self.channel.user_id, platform='telegram')
        other = CLIChannel('另一位', output=io.StringIO())
        await other.welcome(GameFlow(other))
        self.assertNotIn('当前本地档案：青岚客', other.output.getvalue())
        self.assertTrue(all(a.payload.startswith('mk:') for a in other.actions))
        await other.submit('/status', GameFlow(other))
        self.assertIn('没有进行中的世界', other.output.getvalue())
        await self.channel.send_text(other.user_id, '别人的隐私')
        self.assertNotIn('别人的隐私', self.output.getvalue())

    async def test_invalid_number_and_stale_number_do_not_change_state(self):
        world, player = await self.create_cultivation()
        old_action = next(a for a in self.channel.actions if '静坐修炼' in a.label)
        await self.channel.submit('999', self.flow)
        self.assertIn('请选择', self.output.getvalue())
        self.assertEqual(self.db.query(PlayerAction).count(), 0)
        await self.channel.submit('修炼', self.flow)
        self.channel.actions = [old_action]
        await self.channel.submit('1', self.flow)
        self.assertIn('场景或选项已更新', self.output.getvalue())
        self.assertEqual(self.db.query(PlayerAction).count(), 1)

    async def test_menu_has_only_single_player_and_no_raw_payloads(self):
        await self.channel.submit('/scripts', self.flow)
        for action in self.channel.actions:
            script = self.db.get(Script, int(action.payload.split(':')[1]))
            self.assertEqual(script.mode, 'single')
        self.assertNotIn('mk:', self.output.getvalue())
        self.assertIn('1.', self.output.getvalue())
        self.channel.actions = [Action('剧本', 'menu:scripts')]
        await self.channel.submit('1', self.flow)
        self.assertTrue(all('👥' not in action.label for action in self.channel.actions))

    async def test_readonly_commands_and_settle_do_not_advance_sandbox(self):
        world, player = await self.create_cultivation()
        for text in ('/help', '/status', '/resume', '/guide', '地图', '/settle', ''):
            await self.channel.submit(text, self.flow)
        self.db.refresh(world)
        self.assertEqual(world.progress_json['narrative']['minute'], 0)
        self.assertEqual(self.db.query(PlayerAction).count(), 0)
        self.assertIn('无需 /settle', self.output.getvalue())

    async def test_manual_daily_settlement_uses_real_tick_and_only_local_output(self):
        edition = copy.deepcopy(LEGACY_LIGHTHOUSE)
        edition['title'] = '旧版单人测试'
        script = Script(**edition, status='approved', source='user')
        self.db.add(script)
        self.db.commit()
        user = world_service.get_or_create_user(self.db, self.channel.user_id, platform='cli')
        world = world_service.create_world(self.db, user, script, chat_id=self.channel.user_id)
        world_service.join_world(self.db, world, user)
        world_service.start_world(self.db, world)
        from app.engine.tick import FALLBACK_SCENE
        update = {'public_broadcast': '岛上日升', 'countdown_update': '', 'world_ended': False}
        with patch('app.engine.tick.director_ai.generate_world_update', AsyncMock(return_value=update)), \
                patch('app.engine.tick.writer_ai.generate_scene', AsyncMock(return_value=copy.deepcopy(FALLBACK_SCENE))):
            await self.channel.submit('/settle', self.flow)
        self.db.refresh(world)
        self.assertEqual(world.day, 2)
        self.assertIn('岛上日升', self.output.getvalue())
        self.assertIn('相对平静的一天', self.output.getvalue())
        self.assertTrue(self.channel.actions)

    async def test_quit_eof_interrupt_and_dispatch_error(self):
        for signal in ('/quit', EOFError(), KeyboardInterrupt()):
            channel = CLIChannel('退出测试', output=io.StringIO())
            def reader(_):
                if isinstance(signal, BaseException):
                    raise signal
                return signal
            await play(channel, reader)
            self.assertIn('已退出', channel.output.getvalue())
        channel = CLIChannel('错误测试', output=io.StringIO())
        values = iter(['任意行动', '/quit'])
        with patch.object(GameFlow, 'dispatch', AsyncMock(side_effect=RuntimeError('不要泄漏此异常细节'))):
            await play(channel, lambda _: next(values))
        self.assertIn('/resume 核对', channel.output.getvalue())
        self.assertNotIn('不要泄漏', channel.output.getvalue())

    def test_output_cannot_execute_terminal_control_sequences(self):
        self.channel.write('正常\x1b]52;c;payload\x07\x1b[2J\r文本')
        output = self.output.getvalue()
        for control in ('\x1b', '\x07', '\r'):
            self.assertNotIn(control, output)
        self.assertIn('正常', output)

    def test_real_entrypoint_accepts_stdin_and_reopens_save_without_services(self):
        root = Path(__file__).resolve().parent.parent
        with TemporaryDirectory(prefix='storyworld-cli-process-') as directory:
            env = dict(os.environ, DATABASE_URL='sqlite:///' + str(Path(directory) / 'game.db'),
                       DEEPSEEK_API_KEY='', TELEGRAM_BOT_TOKEN='', PYTHONIOENCODING='utf-8')
            command = [sys.executable, str(root / 'run_cli.py'), '--profile', '进程档案']
            first = subprocess.run(command, input='1\n修炼\n/quit\n', text=True, encoding='utf-8',
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=directory, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn('修为 +10', first.stdout)
            second = subprocess.run(command, input='/quit\n', text=True, encoding='utf-8',
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=directory, timeout=30)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn('修为 10/30', second.stdout)
            self.assertNotIn('Traceback', second.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
