这不是一个等待玩家提问并回答的 AI 聊天游戏。它是一部由游戏世界持续驱动、由 AI 实时写作、玩家可以随时介入并改变走向的可游玩小说。

1. 世界自主发展，阅读节奏由玩家掌握：默认读完一段再继续，连续阅读由玩家开启。
2. 玩家默认自由输入，而不是默认做选择题。
3. 游戏机制默认隐藏在小说下面，而不是用系统语言打断小说。

# StoryWorld · AI 互动小说世界

首个[剧本 Skills 版「长生录 · 青岚山居」](docs/story-skills.md)已提供：由 Harness 实际加载剧本 Skill，按需调用包内 Python，生成对话、日常事件和自由组合行为。当前主机用 `./run_cli_compatible.sh --story changsheng` 直接开始；正常 Python 3.11 环境用 `python run_cli.py --story changsheng`。默认独立档案，旧存档保留。

新增[动态功法最小闭环](docs/runtime-rules-play.md)：修仙世界可按需研习功法、吐纳蓄灵并在试法石检验五行克制、穿透与反射。规则由 AI 提案、引擎验收和持久保存，首次研习需要可用的 Harness。当前旧宿主机可用 `./run_cli_compatible.sh` 进入已准备的兼容环境。

当前设计收敛为[受规则约束的生成式文字世界](docs/generative-world-design.md)，验证自由意图、实时内容生成与持久后果；这些能力尚未全部实现。现有[十分钟小说 Demo](docs/novel-vertical-slice.md)是预设内容的回归样本：运行 `python run_cli.py --demo`，直接进入「雨幕便利店」。默认使用独立 `novel-demo` 档案；用 `/read` 继续阅读，或 `/stream on` 开启连续叙述。新一轮试玩使用 `--profile 雨夜二周目`。约每30秒一段、20段后在危险介入点停住。暂无 API 时使用作者正文，有配置时通过 DeepSeek 润色。

> **最高 UX 约束：首先是一部正在实时发生、玩家身处其中的小说；其次才是游戏。** 开发与验收遵循 [Narrator Contract](docs/narrator-contract.md)。修仙世界已接入独立叙事时钟与[阅读节奏](docs/reading-pace.md)：默认逐段阅读，可选连续阅读，介入点暂停；三种自主性默认 World 95 / Player 30 / Narrative 85。

终端直接游玩：在项目环境运行 `python run_cli.py`，自动添加内置剧本，输入编号创建世界后自由描述行动。默认回复以小说正文为主，输入 `/guide` 才展开编号选项。使用 `python run_cli.py --profile 青岚散修` 保存独立档案，下次启动自动恢复。详见 [命令行模式](docs/cli.md)。

> **开放修仙养成**：新增「长生录 · 凡尘修仙」，九个连通地点，自由修炼、采药、炼丹、炼器、交易和经营洞府。没有主线或结局，死亡自动复苏，保留成长；支持 Web 和 Telegram。更新后运行 `python seed.py` 添加入口，输入 `/scripts` 创建世界。详见 [玩法与复活规则](docs/cultivation-sandbox.md)。

> **v0.2 开发进度**：已提供「钟楼雨夜 · 即时单人体验」和「孤岛灯塔 · 即时探索」，自由输入即时执行规则、关键剧情暂停、每回合自动保存，使用 `/continue` 推进、`/resume` 恢复。运行 `seed.py` 添加示例剧本。试玩、事务设计及尚未完成项见 [v0.2 开发说明](docs/mvp-v02-progress.md)。下文的每日结算说明适用于旧版剧本。

> **多平台 · AI 实时生成 · 本地部署** 的文字冒险游戏平台：单人闯关、兄弟群多人、恋人剧本、论坛大型副本……引擎与游戏流平台无关，任何"能收发文字"的平台都能接入（Telegram / Web 已实现，其他渠道暂缓）。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![CI](https://img.shields.io/github/actions/workflow/status/jiangcare/storyworld/ci.yml?branch=main)

基于多通道的多人/单人 AI 文字冒险游戏平台。玩家创建/加入一个小说世界、继承角色卡，每天固定时间由 AI 自动推进剧情（世界事件 + 个人场景），玩家用**自由文字**行动，AI 理解意图并在次日结算。支持官方剧本与**用户上传剧本（AI 协助完善 + 后台审核）**。

- **AI 生成**：通过官方 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) SDK 管理调用，默认模型 `deepseek-v4-flash`，独立无工具会话与结构化 JSON 输出
- **数据化玩法**：剧本预设（物品/能力/任务/主线）+ 运行时实体表（结构化提交）；**数值规则引擎**（JSON DSL：攻击/闪避/百分比加成/强化/抽奖/合成），每剧本规则可不同，AI 生成需过复杂度验收闸门
- **输入边界**：只接受游戏内行动，范围不明或越权输入不记账；状态变更由服务端验收，详见 [游戏输入约束](docs/game-boundary.md)
- **存储**：SQLite（SQLAlchemy），游戏存档、登录会话、草稿和结算租约保存在本地文件，无需额外服务
- **通道**：Telegram（aiogram 3 长轮询）+ Web（浏览器聊天室，含房间多人），本地部署无需域名/服务器
- **后台**：FastAPI + Jinja2 + Bootstrap，管理剧本、审核上传、监控世界

> 想参与？欢迎贡献代码、剧本、文档与新的平台适配器 → [贡献指南](CONTRIBUTING.md) · [剧本规范](docs/script-spec.md) · [新增平台通道](docs/channel-adapter.md)

## 目录结构

```
storyworld/
├─ run_bot.py / run_web.py / run_cli.py / run_admin.py / start_all.py  # 启动入口
├─ seed.py               # 初始化数据库 + 写入种子剧本
├─ smoke_test.py         # 离线全链路测试
├─ data/storyworld.db    # 自动创建的本地数据库（不提交到版本库）
├─ app/
│  ├─ config.py          # .env 配置
│  ├─ db.py              # SQLite 连接、WAL、短写事务
│  ├─ models.py          # 数据模型（含 platform 多平台用户维度）
│  ├─ ai/                # DeepSeek 客户端 + 导演/编剧/意图/剧本完善
│  ├─ engine/            # 剧本 DSL、世界服务、实体层、每日 tick（平台无关）
│  ├─ rules/             # 数值规则引擎：JSON DSL + 白名单求值 + 复杂度验收
│  ├─ game/              # GameFlow：平台无关的游戏交互流
│  ├─ channel/           # 通道抽象：Action/ChannelEvent/Channel 基类 + Telegram 实现
│  ├─ web/               # Web 通道：网页聊天界面（注册/房间/WebSocket）+ 前端页面
│  ├─ bot/               # Telegram 运行时（接线通道 + 每日推送调度）
│  └─ admin/             # FastAPI 后台管理系统
├─ tests/                # 引擎/通道/规则/实体/存储/后台测试
├─ docs/                 # 剧本规范、通道适配器文档
└─ .github/workflows/    # CI（全部使用真实 SQLite 临时文件）
```

## 多平台通道架构

游戏引擎（`engine/`）与交互逻辑（`game/flow.py`）完全**平台无关**；任何"能收发文字的平台"只要实现一个通道适配器即可接入：

```
引擎/AI/剧本 ── GameFlow（平台无关：命令解析/按钮动作/行动）── Channel 抽象
                                                              ├─ Telegram ✅（app/channel/telegram.py）
                                                              ├─ Web ✅（app/web，浏览器+房间多人）
                                                              ├─ QQ(OneBot) / 钉钉 stream → 待开发
                                                              └─ 任何文字平台 → 实现一个适配器（docs/channel-adapter.md）
```

- **通道能力协商**：每个 Channel 声明 `supports_buttons / supports_private_push / supports_edit`
- **文本优先**：无按钮的平台自动把选项降级为文本行（"· 选项"），玩家直接输入文字，AI 理解意图——自由输入本来就是主交互
- **用户多平台**：`User.platform + tg_id` 唯一（telegram/qq/dingtalk/web...），同一引擎服务所有平台
- **按钮 payload 不透明**：适配器只需原样回传 `act:3:1:0` 这类短字符串

| 平台 | 通道状态 | 说明 |
|---|---|---|
| Telegram | ✅ 已实现 | 按钮 + 私聊推送 + 长轮询 |
| Web（自建） | ✅ 已实现 | 浏览器聊天页 `http://127.0.0.1:8081/web`，注册昵称即玩 |
| QQ（OneBot/NapCat） | 待开发 | 本地协议端，文本交互 |
| 钉钉（stream 模式） | 待开发 | 免公网，需企业内部应用 |
| 微信/微博/贴吧 | 不建议 | ToS 风险 > 收益（详见对话记录） |

## Web 通道使用（浏览器直接玩）

打开 `http://127.0.0.1:8081/web`，输入昵称即进入个人世界聊天室：

- **自然回应**：问候、情绪、模糊表达也会得到回应；需要时追问具体目标。交流不扣行动点、不推进剧情，无法理解或模型暂时不可用时仍有本地回应。
- **孤岛灯塔即时版**：读信、调查当场返回结果，信件可免费重读；原版尚未结算的开场存档自动衔接。升级范围与保留规则见 [灯塔版本说明](docs/lighthouse-realtime.md)。
- **即时单人引导**：开局、状态和每轮回复显示目标与当前可执行选项。回复编号或点击按钮即可行动；“怎么玩”“这是要干嘛”及 `/guide` 显示说明，不扣时间。观察不耗时，`/continue` 明确等待 5 分钟。
- **个人私聊**：输入 `/start` 开始，自由文字 = 行动（网页点击建议行动按钮亦可）
- **单人闯关**：点"剧本列表"选 👤 单人剧本，世界在个人聊天里推进
- **房间（多人/副本雏形）**：
  - 点顶栏"🏠 建房"或输入 `/room 房间名` 创建房间
  - 把房间 ID 告诉朋友，他们输入 `/join_room ID` 加入（也可用顶栏建房按钮）
  - 房主在房间里 `/create_world` 选 👥 多人剧本 → 成员 `/join` → 房主 `/start_world`
  - 房间内所有文字 = 行动；世界动态广播给房间所有人，个人场景推送到各自私聊
- `/rooms` 查看已加入的房间；消息历史持久化，刷新不丢；断线自动重连
- 需要 `start_all.py`（或 bot 进程）运行每日调度，Web 玩家才能收到每日剧情推送

## 多通道共用规则

- 一个世界可跨平台游玩：玩家在任意接入的通道都能 `/join`、行动（`User.platform` 区分身份）
- 每日 tick 结果由调度器推送到**所有通道**（每个通道只推本平台的用户）
- 无按钮通道自动文本降级；Web/Telegram 支持按钮与建议行动

## 快速开始

### 1. 安装和配置

默认 Harness 模式需要 Python 3.10+（推荐 3.11，带标准库 `sqlite3`）；Linux 需要 glibc 2.28+。无需安装 MySQL、Redis 或 Docker。SDK 自带 Harness 运行时，无需额外启动 AI 网关或 Harness 网页。安装限制及旧环境兼容模式见 [AI 接入说明](docs/ai-harness.md)。

```bash
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install -r requirements-harness.txt
# Windows 则使用 .venv\Scripts\python
cp .env.example .env
```

编辑 `.env`，填写 `DEEPSEEK_API_KEY`，保留 `AI_BACKEND=harness` 和 `DEEPSEEK_MODEL=deepseek-v4-flash`；仅使用浏览器时可以留空 `TELEGRAM_BOT_TOKEN`。
数据库默认地址是 `DATABASE_URL=sqlite:///data/storyworld.db`，首次启动自动建库。相对路径始终以项目根目录为基准，分别启动 Web、Bot、后台也会共享同一个文件。

### 2. 初始化并启动

```bash
.venv/bin/python seed.py
.venv/bin/python start_all.py
```

打开 `http://127.0.0.1:8081/web`。仅需 Web 即时单人体验时，可执行 `.venv/bin/python run_web.py`。

### 3. 测试与备份

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python smoke_test.py
.venv/bin/python tests/flow_test.py
.venv/bin/python tests/web_test.py
.venv/bin/python tests/rules_test.py
.venv/bin/python tests/entities_test.py
.venv/bin/python tests/narrative_test.py
.venv/bin/python tests/sqlite_test.py
.venv/bin/python tests/admin_test.py
.venv/bin/python tests/local_storage_test.py
.venv/bin/python tests/harness_test.py
.venv/bin/python tests/game_boundary_test.py
.venv/bin/python tests/guidance_test.py
.venv/bin/python tests/conversation_test.py
.venv/bin/python tests/lighthouse_test.py
```

测试创建独立临时数据库，仅模拟 AI，不连接外部服务，也不改动实际存档。CI 另外在 Python 3.11 安装官方 SDK，使用本地模拟 DeepSeek HTTP 接口验证真实 Harness 运行时；复现方式见 [AI 接入说明](docs/ai-harness.md)。

数据库使用 WAL 模式，运行时可能同时出现 `.db-wal` 和 `.db-shm` 文件。备份请使用 `python backup_db.py backups/storyworld.db`；该命令使用 SQLite 在线备份接口，不需要停机，且不会覆盖已有文件。存储路径、并发限制和旧数据说明见 [本地存储文档](docs/local-storage.md)。

### 4. 启动

```bash
.venv\Scripts\python run_bot.py     # Telegram Bot（前台运行）
.venv\Scripts\python run_web.py     # Web 通道 http://127.0.0.1:8081/web（浏览器直接玩）
.venv\Scripts\python run_admin.py   # 后台管理系统 http://127.0.0.1:8080（默认 admin/admin123）
.venv\Scripts\python start_all.py   # 一键：Bot + Web + 后台 + 每日调度（推荐本地部署）
```

> Bot 使用**长轮询**（出站连接），NAT/防火墙后可直接运行。每日剧情推送要求机器在推送时间点在线（建议树莓派/旧手机/NAS 7×24 运行）。`start_all.py` 把 Telegram/Web/后台/调度器放同一进程，Web 玩家也能收到每日剧情推送（离线消息在下次打开时补齐）。

## Telegram 使用

| 命令 | 说明 |
|---|---|
| `/start` | 欢迎菜单 |
| `/scripts` | 浏览可用剧本 |
| `/create_world` | 创建世界（单人在私聊；多人需在群里） |
| `/join` | 加入当前群的世界（自动分配角色卡） |
| `/start_world` | 房主开始世界（满员自动开始） |
| `/act <行动>` | 执行行动（群内也可**回复机器人消息**） |
| `/status` | 角色状态（生命/道具/线索/章节目标） |
| `/log` | 前情提要 |
| `/upload <草稿>` | 上传剧本草稿，AI 完善后提交审核 |

**玩法节奏**：每天固定时间（默认 20:00，可在 `.env` 改 `PUSH_HOUR/PUSH_MINUTE`）自动推送「世界动态 + 你的个人场景」，带建议行动按钮；每人每天 3 点行动（`MAX_ACTION_POINTS`），行动次日结算。

**多人局**：私聊是个人剧情线（保密），群聊广播世界公开事件；玩家间信息不对称（秘密/线索各自持有）。

## 剧本格式（content_json）

```json
{
  "world": {"name": "世界名", "background": "...", "rules": "...", "countdown": "倒计时名", "countdown_total": 7},
  "chapters": [{"day_start": 1, "day_end": 2, "title": "章名", "goal": "本章目标", "events": [{"day": 1, "title": "事件", "desc": "...", "magnitude": "mid"}]}],
  "player_cards": [{"id": "p1", "name": "角色", "role": "身份", "personality": "...", "secret": "...", "goal": "...", "stats": {"strength": 3, "agility": 3, "intellect": 3, "charm": 3, "luck": 3}, "public_desc": "..."}],
  "npcs": [{"id": "n1", "name": "NPC", "role": "...", "personality": "...", "secret": "...", "relation": "友好"}],
  "system_rules": "注入模型的运行规则",
  "days": 7, "mode": "multi", "min_players": 2, "max_players": 5
}
```

- 单人剧本：`mode="single"`，`player_cards` 恰好 1 个主角卡
- 多人剧本：`mode="multi"`，2~8 个角色卡
- `chapters` 必须连续覆盖 1~days 全部天数

## AI 流水线

```
玩家自由输入 → [意图理解] → 每日 tick：
  [导演] 汇总玩家行动 + 章节事件 → 世界公开事件（写世界正典）
  [编剧] 每个玩家 → 个人场景 + 建议行动 + 状态变化（生命/道具/线索）
```

- 所有 LLM 调用使用**结构化 JSON 输出**，游戏状态由 Bot 确定性记账（道具/线索/生命不依赖模型）
- 上下文管理：世界正典（近期公开事件）+ 玩家私有状态 + 角色卡 + 最近个人场景
- 玩家行动**次日结算**（延迟结算），缺席玩家由 AI 托管不惩罚

## 用户上传剧本流程

1. 玩家在 Bot 里 `/upload <草稿>`，选择单人/多人
2. AI 调用「剧本架构师」把草稿完善成完整剧本 JSON
3. 剧本进入 `pending`（待审核）状态
4. 管理员在后台 `/admin/scripts` 的审核页对比「原始草稿 vs AI 完善版」，通过后上架

## 配置项（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | - | BotFather 获取 |
| `DEEPSEEK_API_KEY` | - | DeepSeek 平台 Key |
| `AI_BACKEND` | harness | 官方 Harness SDK；旧部署可显式选 direct |
| `DEEPSEEK_BASE_URL` | https://api.deepseek.com | OpenAI 兼容地址 |
| `DEEPSEEK_MODEL` | deepseek-v4-flash | DeepSeek 模型名，不自动降级 |
| `HARNESS_WORK_DIR` | data/harness | 独立请求的临时工作目录，结束后清理 |
| `DATABASE_URL` | sqlite:///data/storyworld.db | 本地 SQLite 文件地址 |
| `ADMIN_USERNAME/PASSWORD` | admin/admin123 | 后台默认账号 |
| `PUSH_HOUR/PUSH_MINUTE` | 20:00 | 每日推送时间 |
| `MAX_ACTION_POINTS` | 3 | 每人每天行动点 |
| `TICK_SCAN_SECONDS` | 60 | tick 扫描间隔 |

## 开源与许可

- **代码**：MIT（见 [LICENSE](LICENSE)）
- **种子剧本与示例内容**：CC BY-NC-SA 4.0（见 [剧本规范](docs/script-spec.md)）
- 参与方式见 [贡献指南](CONTRIBUTING.md)；安全相关见 [SECURITY.md](SECURITY.md)

## Roadmap（欢迎认领）

已完成 ✅：
- [x] 剧本数据化：预设（物品/能力/任务模板/主线节点）+ 运行时实体表（装备/能力/任务/flag，结构化提交）
- [x] 数值规则引擎：JSON DSL（攻击/闪避/百分比加成/强化/抽奖/合成）+ 白名单安全求值 + 复杂度静态评估验收（AI 生成的规则包过闸门才生效，防资源耗尽）

待办：
- [ ] 玩法命令接入规则引擎：/draw 抽奖、/forge 强化、/synthesize 合成（`app.rules.engine` 已就绪）
- [ ] QQ（OneBot/NapCat）通道适配器（建议独立仓库发布）
- [ ] 钉钉 stream 通道
- [ ] 恋人/兄弟专属双人剧本包 + 双人私聊世界模式
- [ ] 剧本商店 / 精品剧本评审与推荐
- [ ] 大型副本：场景分组合并生成（控 LLM 成本）+ 观战模式
- [ ] 成就系统与章节结算报告
- [ ] AI 生成场景配图（本地出图）

## 注意事项

- 后台默认账号 `admin/admin123`，部署后请立即修改
- 遵守各平台规则：禁止真钱赌博类玩法
- 每日推送要求机器在线；重启后调度器会自动补推错过的世界
