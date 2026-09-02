"""后台管理系统验证：FastAPI TestClient + 真实 MySQL + fakeredis 会话。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis
import app.db as dbmod

dbmod._redis = fakeredis.FakeAsyncRedis(decode_responses=True)

from fastapi.testclient import TestClient  # noqa: E402
from app.admin.main import app  # noqa: E402


def main():
    with TestClient(app) as c:
        # 未登录 → 重定向登录页
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 303, f"未登录应303，实际{r.status_code}"

        # 登录页可访问
        r = c.get("/admin/login")
        assert r.status_code == 200, f"登录页应200，实际{r.status_code}"

        # 错误密码 → 回显错误
        r = c.post("/admin/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
        assert "错误" in r.text or r.status_code == 200, "错误密码应回显"

        # 正确登录 → 303
        r = c.post("/admin/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
        assert r.status_code == 303, f"登录成功应303，实际{r.status_code}"

        # 仪表盘（真实 MySQL 数据）
        r = c.get("/admin")
        assert r.status_code == 200 and "仪表盘" in r.text, "仪表盘应渲染"

        # 剧本列表（含种子剧本）
        r = c.get("/admin/scripts")
        assert r.status_code == 200 and "末日倒数" in r.text, "剧本列表应含种子剧本"

        # 待审核过滤页
        r = c.get("/admin/scripts?status=pending")
        assert r.status_code == 200

        # 世界管理（真实 MySQL 中的测试世界）
        r = c.get("/admin/worlds")
        assert r.status_code == 200 and "世界" in r.text, "世界页应渲染"

        # 用户管理
        r = c.get("/admin/users")
        assert r.status_code == 200

        # 退出登录
        r = c.get("/admin/logout", follow_redirects=False)
        assert r.status_code == 303

        print("ADMIN TESTCLIENT ALL PASSED (登录/会话/剧本/世界/用户页 OK)")


if __name__ == "__main__":
    main()
