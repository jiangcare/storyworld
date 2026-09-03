# 剧本规范（Script DSL v1）

剧本是 StoryWorld 的核心内容资产：一个 JSON 文件定义一个完整的小说世界。引擎按天推进世界剧情，AI 负责把剧本"演活"，每次游玩都是不同的故事。

## 结构总览

```json
{
  "title": "剧本标题",
  "description": "简介（50字内）",
  "genre": "题材",
  "mode": "multi",            // single=单人 | multi=多人
  "min_players": 2,
  "max_players": 5,
  "days": 7,                  // 总天数 1-30
  "world": {
    "name": "世界名",
    "background": "世界背景设定（300字内）",
    "rules": "特殊规则（超自然力量/生存规则等）",
    "countdown": "倒计时名（如'距撤离直升机抵达'）",
    "countdown_total": 7
  },
  "chapters": [
    {
      "day_start": 1, "day_end": 2,
      "title": "章节名",
      "goal": "本章玩家目标（玩家可见，指引方向）",
      "events": [
        {"day": 1, "title": "事件名", "desc": "事件描述（150字内）", "magnitude": "mid"}
      ]
    }
  ],
  "player_cards": [ /* 可玩角色卡，见下 */ ],
  "npcs": [ /* 非玩家角色 */ ],
  "system_rules": "注入 AI 的世界运行规则（200字内）"
}
```

## 字段说明

### world
| 字段 | 必填 | 说明 |
|---|---|---|
| name | ✅ | 世界名称 |
| background | ✅ | 背景设定，是导演/编剧的"世界宪法" |
| rules | 建议 | 本世界的特殊规则，如"感染者夜里活跃" |
| countdown / countdown_total | 可选 | 倒计时机制：每过一天倒计时减少，制造紧迫感 |

### chapters（必须连续覆盖 1~days 全部天数）
- `day_start`~`day_end` 定义章节跨度（建议 1-3 天一章，总 3-5 章）
- `goal` 每章一个明确目标 → 解决玩家"不知道该干嘛"的迷茫
- `events` 章节内固定事件：导演在对应天数必须让事件发生
  - `magnitude`: `low`（环境/铺垫）| `mid`（事件/转折）| `high`（大事件/危机）
  - 建议每天至多 1 个 high，注意张力曲线：铺垫→发展→高潮→余韵

### player_cards（角色卡）
```json
{
  "id": "p1",
  "name": "林晚",
  "role": "急诊科医生",
  "personality": "性格特征（40字内）",
  "secret": "角色秘密（玩家自己知道）",
  "goal": "个人目标",
  "stats": {"strength": 3, "agility": 3, "intellect": 4, "charm": 3, "luck": 3},
  "public_desc": "公开简介（他人可见，60字内）"
}
```
- 单人剧本：恰好 1 个主角卡；重要人物放 `npcs`
- 多人剧本：2-8 个角色卡；角色间要有**潜在冲突与合作空间**（秘密/目标互相牵扯 = 戏剧性）
- `stats` 五项 1-5：strength/agility/intellect/charm/luck

### npcs
```json
{"id": "n1", "name": "光头强", "role": "据点管理者", "personality": "...", "secret": "...", "relation": "友好|中立|敌对"}
```
- NPC 的秘密是给 AI 的"隐藏剧情"，可在合适时机揭晓

### system_rules（给 AI 的规则，很重要）
写清：节奏控制（不要每拍都逼玩家决策）、延迟结算（行动次日见结果）、死亡/失败规则（失败也推进剧情）、信息不对称维护、结局条件。参考种子剧本。

## 校验规则（引擎 enforce 的部分）

- `days` 1-30；`mode` ∈ {single, multi}
- 单人 `player_cards` 恰好 1；多人 2-8
- `chapters` 无缺口覆盖 1~days（引擎只按此推进，缺一天剧情会断）
- `world.name` 必填

离线校验：`python -c "import json,sys; from app.engine.script_dsl import validate_script; print(validate_script(json.load(open('你的剧本.json',encoding='utf-8'))))"`

## 单人 vs 多人设计要点

| | 单人 | 多人 |
|---|---|---|
| 叙事核心 | 内心戏、氛围、抉择的分量 | 玩家间的信息不对称、互相利用/协作 |
| 节奏 | AI 按你的节奏走，注意别让玩家被推着走 | 每天行动点限制天然制造"追更感" |
| 结局 | 开放结局要有分量 | 多结局最好按阵营/目标分别结算 |

## 创作工作流

1. 先写 100 字世界观点子
2. 在 bot 里 `/upload <点子>` 让 AI 起草完整剧本（或照本文手动写）
3. 用上面的校验命令检查
4. 发 PR 到仓库（或先发 issue 讨论）

## 数据化预设（v1.1，可选但推荐）

剧本可携带结构化预设数据，运行时引擎会把它们实例化到数据库行（预设=模板，世界/玩家=实例）：

```json
"items": [
  {"id": "iron_sword", "name": "铁质砍刀", "kind": "equip", "slot": "weapon",
   "stats": {"attack": 6}, "desc": "生锈但依然致命的砍刀。"}
],
"abilities": [{"id": "night_vision", "name": "夜视", "desc": "夜战与潜行能力。"}],
"task_templates": [
  {"id": "t_radio", "title": "修好无线电", "desc": "...", "metric": "parts", "target": 1,
   "reward_items": ["radio_parts"]}
],
"mainline": [
  {"day": 1, "beat": "signal", "flag": "heard_signal", "desc": "收到撤离信号"},
  {"day": 7, "beat": "final_choice", "flag": "finale", "desc": "终极抉择"}
]
```

运行时实例表（引擎自动维护，数量有上限防止无限膨胀）：

| 表 | 内容 | 上限 |
|---|---|---|
| `player_items` | 持有的物品/装备实例（强化等级、词缀） | 每玩家 40 |
| `player_abilities` | 能力实例（可升级） | 每玩家 24 |
| `dynamic_tasks` | 任务实例（主线/支线/AI 生成，进度状态机） | 活动任务世界级 12 / 个人 6 |
| `World.progress_json` | 主线节拍索引、flag、计数器 | — |

**结构化提交**：编剧/导演输出里的「获得物品、完成任务、关键剧情 flag」会由引擎落到上面这些表（编剧 `state_changes.items_added` 支持写模板 id/名称，或 `{"name": 自创物品}` 现场建档）。叙事与数据通过「玩家数据化状态」快照保持一致性——AI 每轮都能看到玩家当前的装备/能力/任务/主线节点，不会写崩。

## 数值规则包（rules DSL，v1.1）

每个剧本可以带自己的确定性数值规则，只支持以下六类（全部 JSON）：

```json
"rules": {
  "constants": {"base_attack": 0.5},
  "checks": {
    "attack_success": {"formula": "clamp(const.base_attack + (attr.strength - attr.agility) * 0.04, 0.05, 0.95)"},
    "dodge": {"formula": "0.05 + attr.agility * 0.03"}
  },
  "percent_mods": {
    "sword_damage": {"base": 6, "mods": [{"when": "ability.夜视", "pct": 0.25}]}
  },
  "forge": {"sharpen": {"success": {"formula": "max(0.85 - stat.level * 0.12, 0.05)"}}},
  "draw_pools": {"supply": [{"item": "bandage", "w": 35}, {"item": "iron_sword", "w": 5}]},
  "synthesize": {"medkit": {"inputs": [{"item": "bandage", "qty": 2}, {"item": "alcohol", "qty": 1}],
                            "output": {"item": "medkit", "qty": 1}, "chance": 0.9}}
}
```

**表达式语言**（checks/forge/mod 的 when 里使用）：
- 运算：`+ - * / %`、比较 `== != < <= > >=`、逻辑 `and or not`、括号
- 白名单函数：`abs min max clamp floor ceil round sqrt`
- 命名空间：`attr.strength`（角色五维）、`stat.hp/day/level`、`const.*`（常量）、`item_count.*`（持有数量）、`ability.*`（是否拥有能力，支持中文名）
- 缺失键按 0/False 处理；**语言层面无循环、无递归、无 IO、无任意函数**——复杂度天然有界

**运行时操作**（`app.rules.engine`，确定性、可注入种子复现）：`check`（按概率掷骰）、`forge`（强化成功率随等级递减）、`draw`（权重抽奖）、`synthesize`（材料合成）、`percent_mods_total`（条件百分比加成）。

## 规则验收与算法复杂度评估

剧本创建/上传（含 AI 完善）与后台保存都会过**验收闸门**：

1. **结构校验**（`rules.dsl`）：六类段类型检查、表达式可编译、抽奖池条目 ≤500、mod 数 ≤100、合成输入 ≤20、物品引用必须存在于剧本 `items`
2. **复杂度静态评估**（`rules.complexity`）：逐条给出最坏步数与等级（公式/合成 = O(1)，抽奖池 = O(池长) 且有硬上限），规则包总预算 ≤10 万步；超限/超深表达式（单个 >200 节点）直接拒绝
3. **运行时双保险**：求值器步数计数器（默认 20 万）+ 结构化上限

AI 生成剧本时，若 AI 交出的规则包没过闸门 → **自动回退为空规则**并在 `content_json["_acceptance"]["rules"]` 记录报告（后台审核页可看到）。管理员手工编辑剧本时校验失败会阻止保存并回显原因——**规则不可能跑起来耗尽服务器资源**。

> 说明：规则引擎目前是完整的确定性计算层（引擎 API + 测试就绪）；把攻击/抽奖/强化做成玩家可触发的玩法命令（如 /draw、/forge）在 Roadmap 中，接入即用 `app.rules.engine`。

## 许可证

- 仓库自带种子剧本：CC BY-NC-SA 4.0（署名-非商业-相同方式共享）
- 社区投稿剧本：建议同样采用 CC BY-NC-SA；如需商用（如进剧本商店），请在 PR 说明以便单独洽谈
- **代码**（不含剧本文本）是 MIT，见根目录 LICENSE
