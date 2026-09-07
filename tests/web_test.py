"""Web 通道测试：注册/聊天/历史/房间/多人广播（SQLite 文件库，真实 GameFlow）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as dbmod
from tests.support import use_test_database

use_test_database()

from fastapi.testclient import TestClient
from app.web.main import app


def reg(client, nick):
    r = client.post("/api/web/register", json={"nickname": nick})
    assert r.status_code == 200, r.text
    return r.json()


def cookie_header(client):
    return {"cookie": f"sw_web={client.cookies.get('sw_web')}"}


def recv_until_type(ws, want, max_n=10):
    """按序接收直到出现期望类型（跳过 ack 等）。"""
    for _ in range(max_n):
        got = ws.receive_json()
        if got["type"] == want:
            return got
    raise AssertionError(f"未收到期望消息 {want}")


def main():
    from app.db import SessionLocal, init_db
    from app.models import Script

    init_db()
    from seed import seed as run_seed

    run_seed()

    with TestClient(app) as client:
        # ---- 注册 ----
        a = reg(client, "阿伟")
        assert client.get("/api/web/me").status_code == 200
        assert client.get("/web").status_code == 200
        assert client.post("/api/web/register", json={"nickname": "阿伟"}).status_code == 400  # 重名拒绝

        conv = f"u:{a['id']}"
        hdr = cookie_header(client)

        # ---- 个人私聊：/start ----
        with client.websocket_connect(f"/ws/web?conv={conv}", headers=hdr) as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"type": "text", "text": "/start"})
            got = ws.receive_json()
            assert got["type"] == "msg" and "StoryWorld" in got["text"] and got["actions"], got

            # 创建单人世界（先 ack 后消息）
            db = SessionLocal()
            single = db.query(Script).filter(Script.mode == "single").first()
            db.close()
            ws.send_json({"type": "action", "payload": f"mk:{single.id}"})
            got = ws.receive_json()
            assert got["type"] == "ack" and "已创建" in got["text"], got
            got = recv_until_type(ws, "msg")
            assert "单人世界" in got["text"], got

            # 自由文本 = 行动
            ws.send_json({"type": "text", "text": "我去检查地下室"})
            got = recv_until_type(ws, "msg")
            assert "已记录" in got["text"], got

        # ---- 历史持久化 ----
        r = client.get(f"/api/web/conv/{conv}/messages?after=0", headers=hdr)
        assert r.status_code == 200
        texts = "\n".join(m["text"] for m in r.json())
        assert "已记录" in texts and "StoryWorld" in texts, "历史应包含对话"

        # ---- 房间：A 创建 → B 加入 → 多人世界 → 广播 ----
        rr = client.post("/api/web/room", json={"name": "副本测试房"}, headers=hdr)
        assert rr.status_code == 200
        rid = rr.json()["id"]
        rconv = f"r:{rid}"

        b = reg(client, "小李")
        hdr_b = cookie_header(client)
        assert client.post(f"/api/web/room/{rid}/join", headers=hdr_b).status_code == 200

        with client.websocket_connect(f"/ws/web?conv={rconv}", headers=hdr) as wsA, \
             client.websocket_connect(f"/ws/web?conv={rconv}", headers=hdr_b) as wsB:
            wsA.receive_json()
            wsB.receive_json()

            # A 创建多人世界（招募中，A 为房主并自动加入）
            db = SessionLocal()
            multi = db.query(Script).filter(Script.mode == "multi").first()
            db.close()
            wsA.send_json({"type": "action", "payload": f"mk:{multi.id}"})
            assert recv_until_type(wsA, "ack")["text"] == "世界已创建！"
            gotA = recv_until_type(wsA, "msg")
            assert "多人世界" in gotA["text"], gotA
            gotB = recv_until_type(wsB, "msg")
            assert "多人世界" in gotB["text"], "B 应收到房间广播"

            # B 加入世界
            wsB.send_json({"type": "text", "text": "/join"})
            gotB = recv_until_type(wsB, "msg")
            assert "已加入" in gotB["text"], gotB
            recv_until_type(wsA, "msg")  # A 也收到加入广播

            # A（房主）开始世界
            wsA.send_json({"type": "text", "text": "/start_world"})
            gotA = recv_until_type(wsA, "msg")
            assert "世界开始" in gotA["text"], gotA
            recv_until_type(wsB, "msg")

            # B 行动（房间自由文本 = 行动）
            wsB.send_json({"type": "text", "text": "我去望风"})
            gotB = recv_until_type(wsB, "msg")
            assert "已记录" in gotB["text"], gotB
            recv_until_type(wsA, "msg")

        # 房间信息
        info = client.get(f"/api/web/room/{rid}/info", headers=hdr).json()
        assert set(info["members"]) == {"阿伟", "小李"}, info

    print("WEB CHANNEL TEST ALL PASSED (注册/私聊/历史/房间/多人广播 OK)")


if __name__ == "__main__":
    main()
