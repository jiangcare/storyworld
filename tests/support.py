"""所有集成测试使用真实临时 SQLite 文件，不读写开发者存档。"""
import atexit
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import sessionmaker

import app.db as dbmod


def use_test_database():
    directory = TemporaryDirectory(prefix="storyworld-test-")
    engine = dbmod.create_db_engine("sqlite:///" + str(Path(directory.name) / "test.db"))
    dbmod.engine = engine
    dbmod.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def cleanup():
        engine.dispose()
        directory.cleanup()
    atexit.register(cleanup)
    return engine


async def fake_game_intent(text, context=None):
    """存储测试只替换模型，保留真实意图协议和引擎验收。"""
    return {"scope": "gameplay", "action_type": "investigate", "target": "",
            "summary": "尝试观察四周", "dice_check": False, "attribute": ""}
