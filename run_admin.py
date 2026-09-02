"""StoryWorld 后台管理入口。

启动方式（项目根目录）：
    python run_admin.py
访问：http://127.0.0.1:8080/admin
默认账号：admin / admin123（首次启动自动创建）
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.admin.main:app", host="127.0.0.1", port=8080)
