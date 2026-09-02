# 如何接入一个新平台（Channel 适配器）

StoryWorld 的引擎与游戏流完全平台无关。接入"任何能收发文字的平台"只需实现一个 `Channel` 子类。

## 一分钟理解

```
平台消息 → [你的适配器: 归一化为 ChannelEvent] → Channel.handle_event()
                                                        ↓
                                                 GameFlow.dispatch(ev)
                                                        ↓
                引擎/AI 处理后 → channel.send(...) / send_private(...) / ack(...)
```

所有"发送"都走 Channel 基类方法，**引擎永远不知道**你在接 QQ 还是电报。

## Channel 需要实现的东西

```python
from app.channel.base import Channel
from app.channel.types import Action, ChannelCapabilities, ChannelEvent

class QQChannel(Channel):
    capabilities = ChannelCapabilities(
        name="qq",                 # 唯一标识，对应 User.platform
        supports_buttons=False,    # QQ OneBot 无按钮 → 自动文本降级
        supports_private_push=True,
        supports_edit=False,
    )

    # 1) 发送纯文本
    async def send_text(self, chat_id: int, text: str):
        # 把文本发到 chat_id（群或私聊），返回消息 id 或 None
        ...

    # 2) 有按钮能力的平台实现（无按钮的会被基类自动降级成文本行）
    async def _send_with_actions(self, chat_id: int, text: str, actions):
        ...

    # 3) 一次性回执（如按钮点按提示），没有就跳过
    async def ack(self, ev: ChannelEvent, text="", alert=False):
        ...

    # 4) 事件入口：收到平台消息后归一化为 ChannelEvent 并交给基类
    async def on_message(self, raw):   # 你自己平台的回调/WebSocket 里调用
        ev = ChannelEvent(
            platform="qq",
            user_id=..., chat_id=...,
            username=..., display_name=...,
            text=..., payload=...,      # 按钮回传时为 payload
            is_private=...,             # 私聊 True / 群聊 False
            reply_to_bot=...,           # 群内回复机器人 = 行动
            auto_action=False,          # 若你的平台"所有文字都是行动"则 True
            channel=self,
        )
        await self.handle_event(ev)     # 基类方法：转交 GameFlow
```

**ChannelEvent 关键字段**：`user_id` 是平台侧用户 ID；引擎按 `(platform, user_id)` 识别用户，跨平台同一个人会各有一个 User 行。

## 通道能力与交互降级

| 能力 | 有 | 无（自动降级） |
|---|---|---|
| 按钮 | 建议行动渲染成按钮 | 渲染成文本行"· 选项"，玩家直接输入文字（AI 理解意图） |
| 私聊推送 | 每日个人场景主动推送 | 玩家只能通过 /log、/day 拉取 |
| 编辑消息 | 可原地刷新棋盘 | 每次发送新消息 |

**文本优先原则**：自由文字输入永远是主交互，按钮只是便利。所以纯文本平台（QQ/论坛/邮件）也能完整游玩。

## 平台评估速查（2026 视角）

| 平台 | 可行性 | 关键点 |
|---|---|---|
| Telegram | ✅ 已实现 | 官方 API，长轮询本地可跑 |
| Web（自建） | ✅ 已实现 | app/web，含房间多人 |
| QQ | ⭐ 最值得做 | 官方无个人机器人接口，走 OneBot/NapCat 协议端（社区协议，注意账号风险），建议独立仓库发布适配器 |
| 钉钉 | 有条件 | stream 模式免公网，但需"企业内部应用"+成员在组织内，适合企业团建 |
| 微信 | 困难 | 个人号 hook 封号风险高；公众号需备案域名服务器 |
| 微博/贴吧 | 不建议 | 无官方 bot API |

## 接入后接线

- 单一平台：仿照 `app/bot/main.py` 启动
- 多平台 + 每日调度：在 `start_all.py` 里把新 channel 加进 `channels` 列表，调度器会把每日 tick 结果推送到所有通道（每个通道只推本平台用户，按 `User.platform` 过滤）
- 注册通道：`from app.channel import registry; registry.register(ch)`（如需全局可查）

## 最小验收标准

1. 能用文字完成：创建世界 → 行动 → /status → /log
2. 跑通 `tests/flow_test.py` 的思路（用 FakeChannel 模拟你的通道测一遍流程）
3. 说明文档注明：能力声明、ToS/账号风险、依赖的协议端版本
