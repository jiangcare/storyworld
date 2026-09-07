# DeepSeek Harness 接入

StoryWorld 的 Web 和 Telegram 共用 `app/ai/client.py`，默认经官方 [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) Python SDK 调用 `deepseek-v4-flash`。Harness 负责模型调用与运行时生命周期；游戏规则、存档和玩家可见的对话历史由 StoryWorld 管理。

## 安装与配置

推荐 Python 3.11；官方 SDK 最低 Python 3.10。Linux 发行版需要 glibc 2.28+，旧 CentOS 7 的 glibc 2.17 无法直接运行官方二进制。可使用满足条件的独立容器、虚拟机或新系统，不要替换宿主机系统 Python/glibc。Windows x64 和 macOS 14+ Apple Silicon 也有官方运行时 wheel；本项目目前验证 Linux x64。

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-harness.txt
cp .env.example .env  # 已有 .env 时保留原文件，直接编辑
```

在 `.env` 中设置：

```dotenv
AI_BACKEND=harness
DEEPSEEK_API_KEY=你的DeepSeek平台密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
HARNESS_WORK_DIR=data/harness
```

依赖固定为 `deepseek-harness-sdk==0.1.2rc1`，它会安装同版本 `deepseek-harness-runtime-bin`。这是官方预发布版本，升级前需验证配置 patch 和运行时测试。无需单独安装 Node.js、克隆 Harness 源码或运行 Harness 的 Web 服务。

运行 `seed.py` 初始化示例，然后 `start_all.py` 启动全部通道。仅启动即时单人网页可运行 `run_web.py`。部署后真实 AI 回复还依赖有效的 API Key 和到 DeepSeek 的网络连接。

## 聊天入口

- 平台自己的聊天网页：`http://127.0.0.1:8081/web`。输入昵称注册，支持私聊、房间、按钮、WebSocket 实时回复和持久化历史。该地址在服务启动后可访问；远程主机可通过 SSH 端口转发访问。
- Telegram：配置 `TELEGRAM_BOT_TOKEN`，运行 `start_all.py` 或 `run_bot.py`，向机器人发送 `/start`。采用长轮询，不需要公网回调地址。同一 token 只运行一个轮询进程。
- 后台管理：`http://127.0.0.1:8080`，由 `start_all.py` 或 `run_admin.py` 启动。

Web 和 Telegram 使用相同的游戏流程与 AI 后端，但账号按平台区分，当前没有跨平台账号绑定。

## 调用约束

每次生成使用新的 Harness 实例、会话 ID、临时 home 和工作区。模型上下文只取本次游戏请求，不读取玩家之间的历史，也不加载管理员 `~/.dsh` 下的设置。请求结束或失败后关闭子进程并清理临时会话；进程被强制杀死时可能留下临时目录，可在停止服务后清理 `data/harness/request-*`。

使用 `sdk-minimal` 配置，并通过 `app/ai/harness/storyworld.patch.yml` 禁用终端、文件编辑和诊断上传插件。`json-request.mjs` 通过官方 `agent/request` hook 和 DeepSeek API extension 设置温度、输出长度和 `response_format=json_object`，同时检查请求没有工具。模型不会获得执行本机命令的能力。

默认关闭思考，游戏需要的结构化响应交给原有 Pydantic/规则层继续验收。截断、空回复和非 JSON 对象作为失败处理。整个调用有 `LLM_TIMEOUT` 截止时间（默认 90 秒，另有短暂关闭进程时间），SDK 工作在线程中，不阻塞 WebSocket 事件循环。

未安装 SDK、配置错误或运行时失败会明确报错，不会自动切换模型或绕过 Harness。JSON 格式错误沿用调用方的有限重试。常规错误日志不输出 SDK 原始诊断内容或 API 密钥。

## 旧环境的显式兼容模式

Python 3.9 / 老 Linux 若暂时不能升级，可以安装 `requirements.txt` 并设置 `AI_BACKEND=direct`，使用原有 OpenAI 兼容客户端访问同一个 DeepSeek 模型。该模式不经过 Harness，只是供旧部署选择的兼容路径；默认保持 `harness`。

## 离线验证

```bash
# 不要求安装 Harness，验证路由、参数、会话隔离、失败清理与超时
.venv/bin/python tests/harness_test.py

# 安装 requirements-harness.txt 后：真实官方 SDK/二进制 + 本地模拟 API
STORYWORLD_TEST_HARNESS_RUNTIME=1 .venv/bin/python tests/harness_test.py
```

第二条测试不会调用真实 DeepSeek 或 Telegram，也不需要 API Key；它验证实际 HTTP 请求的模型名、JSON 输出、温度、token 上限、关闭思考、无工具及玩家隔离。它不证明真实账号可用或模型内容质量。
