"""本地终端通道：编号选择替代按钮，档案身份与 Web / Telegram 隔离。"""
from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from typing import Optional, Sequence, TextIO

from .base import Channel
from .types import Action, ChannelCapabilities, ChannelEvent

CLI_HELP = """🎮 StoryWorld 命令行
直接描述行动，或输入最新选项的编号。
/scripts 选择单人剧本    /status 查看角色    /resume 恢复最近存档
/guide 游戏玩法         /log 查看经历      /continue 继续观察
/settle 结算旧版单人剧本的一天（即时剧本不需要）
/help 命令说明          /quit 退出（每次行动已经自动保存）
/read 继续阅读一段      /stream on 连续阅读    /pause 暂停连续阅读
/autonomy 自主性设置
旧版基础动作可离线使用；Skills 剧本的交互与自主叙事需要配置 AI。"""


def profile_name(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).strip()
    if not 1 <= len(value) <= 32 or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('档案名需为1-32个可显示字符')
    return value


def profile_id(name: str) -> int:
    # 不使用每次进程启动会变化的 hash()，也不将档案名用作路径。
    return int.from_bytes(hashlib.sha256(profile_name(name).encode('utf-8')).digest()[:8], 'big') & ((1 << 63) - 1)


class CLIChannel(Channel):
    capabilities = ChannelCapabilities(name='cli', supports_buttons=True, supports_private_push=True)

    def __init__(self, profile='default', *, output: Optional[TextIO] = None):
        self.profile = profile_name(profile)
        self.user_id = profile_id(self.profile)
        self.output = output if output is not None else sys.stdout
        self.actions: list[Action] = []
        self.message_id = 0
        self.streaming_open = False

    def stream_present(self, user_id):
        return self.streaming_open and user_id == self.user_id

    def write(self, text):
        # 剧本/模型文本不应成为终端控制序列（清屏、改标题、OSC 剪贴板等）。
        safe = ''.join(c for c in str(text) if c in '\n\t' or not unicodedata.category(c).startswith('C'))
        self.output.write(safe + '\n')
        self.output.flush()

    async def send_text(self, chat_id: int, text: str):
        return await self._send_with_actions(chat_id, text, [])

    async def _send_with_actions(self, chat_id: int, text: str, actions: Sequence[Action]):
        if chat_id != self.user_id:
            return None
        self.message_id += 1
        self.actions = list(actions)
        self.write('\n' + text)
        # 即时引导正文已带编号，其他菜单则在终端补上编号。
        numbered = self.actions and all(a.payload.startswith('pick:') and a.label.startswith(f'{i}. ')
                                        for i, a in enumerate(self.actions, 1))
        if self.actions and not numbered:
            for i, action in enumerate(self.actions, 1):
                self.write(f'{i}. {action.label}')
            self.write('输入编号选择，也可以直接输入命令或行动。')
        return self.message_id

    async def ack(self, ev, text='', alert=False):
        if text:
            self.write(text)

    def event(self, *, text='', payload=None):
        return ChannelEvent(platform='cli', user_id=self.user_id, chat_id=self.user_id,
                            username=self.profile, display_name=self.profile, text=text, payload=payload,
                            is_private=True, auto_action=True, channel=self)

    async def scripts(self):
        from ..db import SessionLocal
        from ..engine import world_service, narrative
        with SessionLocal() as db:
            scripts = world_service.list_approved_scripts(db, mode='single')
            lines = [f'{s.title}（{"即时行动" if narrative.enabled(s.content_json) else "每日结算，可用 /settle"}）\n{s.description}'
                     for s in scripts]
            await self.send(self.user_id, '📜 单人剧本：\n\n' + ('\n\n'.join(lines) or '暂无可用剧本。'),
                            actions=[Action(s.title, f'mk:{s.id}') for s in scripts])

    async def settle(self):
        from ..db import SessionLocal
        from ..engine import world_service, narrative, tick
        from ..bot.scheduler import push_tick
        with SessionLocal() as db:
            user = world_service.get_or_create_user(db, self.user_id, platform='cli', display_name=self.profile)
            world = world_service.get_active_world(db, user.id)
            if world is None:
                self.write('当前没有进行中的世界，用 /scripts 创建。')
                return
            if world.owner_id != user.id or world.script.mode != 'single':
                self.write('只能手动结算当前档案自己的单人世界。')
                return
            if narrative.enabled(world.script.content_json):
                self.write('这个剧本每次行动立即结算，无需 /settle。输入 /guide 选择行动。')
                return
            self.write('正在生成并结算当天剧情，请稍候……')
            try:
                result = await tick.run_tick(db, world.id)
            except tick.TickError:
                self.write('这次剧情生成未完成，世界尚未推进。可以稍后重试 /settle。')
                return
            if result is None:
                self.write('本次没有推进，可能已有结算正在进行。用 /status 查看存档。')
                return
            await push_tick(self, db, result)

    async def submit(self, text: str, flow) -> bool:
        """False 表示退出；数字只映射当前展示的服务端 payload。"""
        text = text.strip()
        if not text:
            return True
        if text.lower() in ('/quit', '/exit'):
            return False
        if text.lower() in ('/help', '/start'):
            self.write(CLI_HELP)
            return True
        if text.lower() in ('/scripts', '/create_world'):
            await self.scripts()
            return True
        if text.lower() == '/settle':
            self.actions = []
            await self.settle()
            return True
        payload = None
        if re.fullmatch(r'[0-9]{1,3}', text) and self.actions:
            index = int(text)
            if not 1 <= index <= len(self.actions):
                self.write(f'请选择 1-{len(self.actions)}，或直接描述行动。')
                return True
            payload = self.actions[index - 1].payload
        self.actions = []
        if payload == 'menu:scripts':
            await self.scripts()
        else:
            await flow.dispatch(self.event(text=text if payload is None else '', payload=payload))
        return True

    async def welcome(self, flow):
        from sqlalchemy import select
        from ..db import SessionLocal
        from ..models import World, WorldPlayer
        from ..engine import world_service
        self.write(CLI_HELP + f'\n当前本地档案：{self.profile}')
        with SessionLocal() as db:
            user = world_service.get_or_create_user(db, self.user_id, platform='cli', display_name=self.profile)
            saved = db.scalar(select(World.id).join(WorldPlayer).where(
                WorldPlayer.user_id == user.id, World.status.in_(('running', 'finished'))).limit(1))
        if saved is not None:
            await flow.dispatch(self.event(text='/resume'))
        else:
            await self.scripts()
