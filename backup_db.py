"""备份当前 SQLite 数据库（包括 WAL 中已提交的数据），拒绝覆盖文件。"""
import argparse
from pathlib import Path
import sqlite3

from app.db import engine


def backup_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError("源数据库不存在，请先初始化")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建，避免路径相同或覆盖另一个存档。
    with destination.open("xb"):
        pass
    try:
        src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(destination))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except Exception:
        destination.unlink()
        raise
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", help="新备份文件的路径")
    args = parser.parse_args()
    print("备份完成：", backup_database(engine.url.database, args.destination))
