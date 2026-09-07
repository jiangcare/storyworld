"""SQLite 数据库：WAL、外键、短写事务和持久化本地会话。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_db_engine(database_url: str):
    url = make_url(database_url)
    if url.drivername not in ("sqlite", "sqlite+pysqlite") or url.query:
        raise ValueError("DATABASE_URL 需为 SQLite 文件地址，例如 sqlite:///data/storyworld.db")
    memory = url.database in (None, "", ":memory:")
    if not memory:
        path = Path(url.database).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(path.resolve()))
    options = {"poolclass": StaticPool} if memory else {}
    result = create_engine(url, connect_args={"check_same_thread": False, "timeout": 10},
                           echo=settings.debug, **options)

    @event.listens_for(result, "connect")
    def configure(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        if not memory:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return result


engine = create_db_engine(settings.database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """建表（幂等）。"""
    from . import models  # noqa: F401  确保模型注册

    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        Base.metadata.create_all(bind=conn)
        conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def begin_write(db) -> None:
    """在任何读/改之前获取 SQLite 写保留锁。调用方负责提交/回滚，锁内禁止 await。"""
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")


async def get_store():
    from .local_store import LocalStore

    return LocalStore(engine)
