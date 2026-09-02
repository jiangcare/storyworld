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

## 许可证

- 仓库自带种子剧本：CC BY-NC-SA 4.0（署名-非商业-相同方式共享）
- 社区投稿剧本：建议同样采用 CC BY-NC-SA；如需商用（如进剧本商店），请在 PR 说明以便单独洽谈
- **代码**（不含剧本文本）是 MIT，见根目录 LICENSE
