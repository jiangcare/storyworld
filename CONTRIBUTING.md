# 贡献指南

感谢你愿意参与 StoryWorld！无论你是写代码、写剧本、写文档还是提 issue，都是贡献。

## 项目速览

```
引擎（平台无关）      app/engine   剧本/世界/行动/每日tick
AI 层               app/ai       DeepSeek 客户端 + 导演/编剧/意图/剧本完善
游戏流（平台无关）     app/game     GameFlow：所有通道的输入汇入
通道抽象             app/channel  Channel 基类 + Telegram 实现
Web 通道             app/web      浏览器聊天 + 房间（多人）
Telegram 运行时       app/bot      接线 + 每日调度
后台管理             app/admin    FastAPI 剧本/世界管理
```

**核心原则**：引擎与游戏流不依赖任何具体平台。新平台 = 新增一个 `Channel` 适配器（见 `docs/channel-adapter.md`）。

## 本地开发

```bash
git clone <repo>
cd storyworld
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt   # Windows
cp .env.example .env     # 填入 TELEGRAM_BOT_TOKEN / DEEPSEEK_API_KEY
```

无需外部数据库服务，首次启动自动创建 `data/storyworld.db`；可用 `DATABASE_URL` 修改位置。

## 跑测试

```bash
python smoke_test.py
python tests/flow_test.py
python tests/web_test.py
python tests/rules_test.py
python tests/entities_test.py
python tests/narrative_test.py
python tests/sqlite_test.py
python tests/admin_test.py
python tests/local_storage_test.py
```

测试使用与实际部署相同的 SQLite 文件库、WAL 和持久化会话，仅模拟 AI。每套测试使用隔离的临时目录，不改动开发者存档。提交前请完成以上回归；存储修改需要覆盖多连接/多进程竞争及重启读取。

## 提交剧本（不会写代码也能贡献！）

剧本 = 一个 JSON 世界包，规范见 `docs/script-spec.md`。流程：

1. 按规范写剧本 JSON（可以先用 `/upload` 让 AI 帮你起草，再人工打磨）
2. 用校验器检查：`python -m app.engine.script_dsl < file.json`（开发中，或直接在测试里调用 validate_script）
3. 发 PR 到 `scripts/` 目录（尚未建立时先发 issue 讨论格式）
4. 维护者评审通过后合入，玩家 `seed.py` 后即可游玩

**剧本许可证**：社区剧本默认 CC BY-NC-SA 4.0（见 docs/script-spec.md）。

## 代码风格

- Python 3.9 兼容（不用 PEP 604 的 `X | None` 注解，用 `Optional[...]` 或文件头 `from __future__ import annotations`）
- 平台无关的代码不 import aiogram/fastapi 等平台库
- 用户可见文案中文；代码注释中文/英文均可
- 新增依赖请在 requirements.txt 注明用途

## PR 流程

1. fork + 分支（`feature/xxx` 或 `fix/xxx`）
2. 小步提交，PR 描述清楚动机与验证
3. CI 通过 + 至少一位维护者 review 后合入

有任何疑问先开 issue 讨论，比直接大 PR 更高效。
