"""通道抽象 + 平台无关游戏流测试：SQLite 文件库 + Mock LLM + FakeChannel。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as dbmod
from tests.support import use_test_database

use_test_database()

from app.channel.base import Channel
from app.channel.types import Action, ChannelCapabilities, ChannelEvent


class FakeChannel(Channel):
    """记录所有发送，供断言。"""

    capabilities = ChannelCapabilities(
        name="fake", supports_buttons=True, supports_private_push=True
    )

    def __init__(self):
        self.sent = []  # (chat_id, text, actions)
        self.acked = []  # (text, alert)

    async def send_text(self, chat_id: int, text: str):
        self.sent.append((chat_id, text, None))
        return len(self.sent)

    async def _send_with_actions(self, chat_id: int, text: str, actions):
        self.sent.append((chat_id, text, list(actions)))
        return len(self.sent)

    async def ack(self, ev, text="", alert=False):
        self.acked.append((text, alert))


# Mock LLM
import app.ai.director as director_mod
import app.ai.intent as intent_mod
import app.ai.script_ai as script_ai_mod
import app.ai.writer as writer_mod


async def fake_world_update(**kw):
    return {"public_broadcast": "世界事件", "canon_additions": ["x"], "countdown_update": "",
            "chapter_note": "", "world_ended": False}


async def fake_scene(**kw):
    return {"narrative": "场景正文", "suggested_actions": ["行动A", "行动B"],
            "state_changes": {"hp_delta": 0, "items_added": [], "items_removed": [], "clues_added": [], "notes": {}},
            "scene_ended": False}


async def fake_intent(text, context=None):
    return {"scope": "gameplay", "action_type": "other", "target": "", "summary": text[:20], "dice_check": False, "attribute": ""}


async def fake_complete(draft, mode):
    return {"title": "流测试剧本", "description": "", "genre": "测试", "mode": mode,
            "min_players": 1, "max_players": 4, "days": 3,
            "world": {"name": "W", "background": "B", "rules": "", "countdown": "", "countdown_total": 0},
            "chapters": [{"day_start": 1, "day_end": 3, "title": "章", "goal": "目标", "events": []}],
            "player_cards": [{"id": "p1", "name": "甲", "role": "r", "personality": "p", "secret": "s",
                              "goal": "g", "stats": {"strength": 3, "agility": 3, "intellect": 3, "charm": 3, "luck": 3},
                              "public_desc": "d"}],
            "npcs": [], "system_rules": ""}


director_mod.generate_world_update = fake_world_update
writer_mod.generate_scene = fake_scene
intent_mod.parse_intent = fake_intent
script_ai_mod.complete_script = fake_complete

from app.db import SessionLocal, init_db  # noqa: E402
from app.game.flow import GameFlow  # noqa: E402
from app.models import Script, World  # noqa: E402


async def main():
    init_db()
    from seed import seed as run_seed
    run_seed()

    ch = FakeChannel()
    flow = GameFlow(ch)
    db = SessionLocal()

    def ev(user, chat, text="", payload=None, private=True):
        return ChannelEvent(platform="fake", user_id=user, chat_id=chat,
                            username=f"u{user}", display_name=f"U{user}",
                            text=text, payload=payload, is_private=private)

    # 1) /start
    await flow.dispatch(ev(1, 100, text="/start"))
    assert any("StoryWorld" in t for _, t, _ in ch.sent), "start 应返回帮助"

    # 2) 单人世界创建（私聊）：找 single 剧本 payload
    single = db.query(Script).filter(Script.mode == "single").first()
    ch.sent.clear()
    await flow.dispatch(ev(1, 100, payload=f"mk:{single.id}"))
    world = db.query(World).filter(World.title == single.title).first()
    assert world and world.status == "running", "单人世界应创建并运行"
    assert any("单人世界" in t for _, t, _ in ch.sent)

    # 3) 私聊自由文本 = 行动（文本优先）
    ch.sent.clear()
    await flow.dispatch(ev(1, 100, text="我去检查地下室"))
    assert any("已记录" in t for _, t, _ in ch.sent), "自由文本应记为行动"

    # 4) 多人创建限制：私聊点多人 → 拒绝
    multi = db.query(Script).filter(Script.mode == "multi").first()
    ch.acked.clear()
    await flow.dispatch(ev(2, 200, payload=f"mk:{multi.id}"))
    assert any("群" in t for t, _ in ch.acked), "私聊创建多人世界应被拒绝"

    # 5) 多人世界在群聊创建 + 第三人加入 + 行动按钮模拟
    ch.sent.clear()
    await flow.dispatch(ev(2, 300, payload=f"mk:{multi.id}", private=False))
    w2 = db.query(World).filter(World.title == multi.title, World.status == "recruiting").first()
    assert w2 is not None, "群内应创建招募中的多人世界"
    ch.sent.clear()
    await flow.dispatch(ev(3, 300, text="/join", private=False))
    assert any("已加入" in t for _, t, _ in ch.sent), "第三人应能加入"

    # 6) 菜单按钮
    ch.sent.clear()
    await flow.dispatch(ev(1, 100, payload="menu:scripts"))
    assert any("剧本列表" in t for _, t, _ in ch.sent)

    # 7) upload → uplmode → pending 剧本
    ch.sent.clear()
    await flow.dispatch(ev(1, 100, text="/upload 一个悬疑世界 一个侦探"))
    modes = [a for _, _, acts in ch.sent for a in (acts or []) if a.payload.startswith("uplmode:")]
    assert modes, "应给出单人/多人选择"
    ch.sent.clear()
    await flow.dispatch(ev(1, 100, payload=modes[0].payload))
    pending = db.query(Script).filter(Script.status == "pending").first()
    assert pending is not None and pending.source == "user", "应生成待审核剧本"
    assert any("已提交审核" in t for _, t, _ in ch.sent)

    # 8) 无按钮通道文本降级
    from app.channel.base import Channel as C2
    from app.channel.types import Action as A2

    class TextOnly(C2):
        capabilities = ChannelCapabilities(name="textonly", supports_buttons=False)
        def __init__(self):
            self.sent = []
        async def send_text(self, chat_id, text):
            self.sent.append(text)
            return 1
        async def _send_with_actions(self, chat_id, text, actions):
            raise AssertionError("无按钮通道不应调用 _send_with_actions")

    tc = TextOnly()
    await tc.send(1, "正文", actions=[A2("选项1", "x:1"), A2("选项2", "x:2")])
    merged = tc.sent[-1]
    assert "· 选项1" in merged and "· 选项2" in merged and "直接回复" in merged, "应文本降级渲染选项"

    print("CHANNEL/GAME FLOW TEST ALL PASSED")


if __name__ == "__main__":
    asyncio.run(main())
