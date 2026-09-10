"""显式启用的真实 Harness/DeepSeek 验收；独立临时 SQLite，不启动任何聊天通道。"""
from __future__ import annotations
import asyncio
import copy
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run():
    import app.db as dbmod
    from tests.support import use_test_database
    use_test_database()
    from app.config import settings
    from app.ai.diagnostics import configuration_issues
    from app.engine import world_service
    from app.engine.cultivation_story import SEED_CULTIVATION
    from app.models import Script, RuntimeRule, PlayerAction
    from app.ai.prose_contract import validate_prose
    if settings.ai_backend != 'harness' or configuration_issues():
        raise RuntimeError('真实验收需要配置可用的 Harness 环境')
    dbmod.init_db()
    transcript = []
    with dbmod.SessionLocal() as db:
        script = Script(**copy.deepcopy(SEED_CULTIVATION), status='approved', source='official')
        db.add(script)
        db.commit()
        user = world_service.get_or_create_user(db, 10002, platform='cli')
        world = world_service.create_world(db, user, script, chat_id=10002)
        player = world_service.join_world(db, world, user)
        world_service.start_world(db, world)
        async def say(text, request):
            ok, result = await world_service.record_action(db, world, player, text, request_id=request)
            transcript.append(text + '\n\n' + result)
            destination = os.getenv('STORYWORLD_LIVE_TRANSCRIPT')
            if destination:
                Path(destination).write_text('\n\n---\n\n'.join(transcript), encoding='utf-8')
            if not ok:
                raise RuntimeError('真实游戏交互未通过：' + result)
            validate_prose(result)
            return result
        await say('我把洞府那册引露诀摊开，试着研读其中护身的行气法门。', 'live-study')
        assert db.query(RuntimeRule).count() == 1
        ability = db.query(RuntimeRule).one().body
        assert player.private_state['mechanics']['pool']['current'] == 0
        for i in range((ability['cost'] + 2) // 3):
            await say('吐纳蓄灵', f'live-channel-{i}')
        text = '我想用刚学会的引露诀，在火纹试法石前试试能不能挡住火芒。'
        result = await say(text, 'live-trial')
        state = copy.deepcopy(player.private_state)
        assert state['mechanics']['last_trial']['resolution']['executed']
        ok, replay = await world_service.record_action(db, world, player, text, request_id='live-trial')
        assert ok and replay == result and player.private_state == state
        assert db.query(RuntimeRule).count() == 1
        print('PASS: 真实意图解析、功法生成、灵力事务、能力碰撞、小说正文与请求重放')
        print('model:', settings.deepseek_model, 'rules:', db.query(RuntimeRule).count(), 'actions:', db.query(PlayerAction).count())
        destination = os.getenv('STORYWORLD_LIVE_TRANSCRIPT')
        if destination:
            Path(destination).write_text('\n\n---\n\n'.join(transcript), encoding='utf-8')
            print('transcript saved')


if __name__ == '__main__':
    if os.getenv('STORYWORLD_TEST_RULES_LIVE') != '1':
        print('SKIP: set STORYWORLD_TEST_RULES_LIVE=1 to call the configured real DeepSeek API')
    else:
        # 不打印 SDK 原始异常、请求对象或环境变量。
        try:
            asyncio.run(run())
        except Exception as exc:
            print('FAIL:', type(exc).__name__)
            raise SystemExit(1)
