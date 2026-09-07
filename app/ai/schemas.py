"""LLM 结构化输出协议（pydantic 模式）。"""

# ---- 导演层：世界事件生成 ----
DIRECTOR_OUTPUT_SCHEMA = {
    "public_broadcast": "str，今日世界公开事件（发到世界群，300字以内，中文）",
    "canon_additions": ["str，本次推进写入世界正典的事实条目（3-6条，简洁，供后续生成引用）"],
    "countdown_update": "str，倒计时/进度更新描述（如：距末日还剩 5 天），无则空字符串",
    "chapter_note": "str，给编剧层的内部提示（本日主线张力、各组玩家应遭遇的方向），玩家不可见",
    "world_ended": "bool，本日是否触发终局（大事件/结局），触发时 public_broadcast 需完整交代结局",
}

# ---- 编剧层：玩家个人场景 ----
WRITER_OUTPUT_SCHEMA = {
    "narrative": "str，玩家个人场景正文（250-500字，中文，第二人称'你'，含环境/事件/可能的对话，结尾停在悬念或自然节点）",
    "suggested_actions": ["str，3-4条建议行动（每条不超过18字，玩家可一键点击）"],
    "state_changes": {
        "hp_delta": "int，生命值变化（-10~10，无则0，不能超过角色生命上限）",
        "items_added": ["str，只能引用剧本预设物品 id/名称；最多5个，禁止自创物品或属性"],
        "items_removed": ["str，失去道具（名称或模板id）"],
        "clues_added": ["str，新增线索"],
        "abilities_added": ["str，获得的能力名称（只能引用剧本预设能力，最多3个）"],
        "tasks_done": ["str，玩家完成的任务标题（须与现存任务标题一致）"],
        "flag_set": {"键": "bool，只能使用 mainline 中日期已经到达的 flag 键"},
        "notes": {"键": "str，其他状态记录（好感度、阵营变化等）"},
    },
    "scene_ended": "bool，本日场景是否已到自然结束（玩家无更多事可做）",
}

# ---- 意图层：玩家输入理解 ----
INTENT_OUTPUT_SCHEMA = {
    "reply": "str，不执行行动时对玩家的自然回应或具体澄清问题，最多400字；执行行动时为空",
    "scope": "str，gameplay 或 out_of_scope，必须先判断是否属于当前游戏",
    "action_type": "str，枚举: investigate(调查) | talk(交谈) | move(移动) | use(使用道具) | fight(战斗) | help(求助/协作) | hide(躲藏) | rest(休息) | other(其他)",
    "target": "str，行动对象（人物/地点/物品），无则空",
    "summary": "str，对玩家意图的中文简述（20字内）",
    "dice_check": "bool，是否需要检定（高风险动作如战斗/偷窃/说服）",
    "attribute": "str，检定属性: strength|agility|intellect|charm|luck，无检定则空",
}

# ---- 剧本完善：用户草稿 → 完整剧本 ----
SCRIPT_OUTPUT_SCHEMA = {
    "scope": "str，只有创作游戏剧本的请求为 script；游戏外任务或越权要求返回 out_of_scope，其他字段可以省略",
    "title": "str，剧本标题",
    "description": "str，剧本简介（50字内）",
    "genre": "str，题材（末日/悬疑/奇幻/科幻/古风/都市）",
    "mode": "str，single 或 multi",
    "min_players": "int，最少玩家数",
    "max_players": "int，最多玩家数（单人剧本=1）",
    "days": "int，总天数（3-10）",
    "world": {
        "name": "str，世界名称",
        "background": "str，世界背景设定（300字内）",
        "rules": "str，世界特殊规则（如超自然力量、生存规则，200字内）",
        "countdown": "str，倒计时设定（如'末日降临倒计时'），无则空",
        "countdown_total": "int，倒计时总天数（与 days 一致或为0）",
    },
    "chapters": [
        {
            "day_start": "int，章节起始日（从1开始）",
            "day_end": "int，章节结束日",
            "title": "str，章节名",
            "goal": "str，本章玩家目标（玩家可见，指引方向）",
            "events": [
                {
                    "day": "int，事件发生日（须在章节范围内）",
                    "title": "str，事件名",
                    "desc": "str，事件描述（150字内）",
                    "magnitude": "str，low|mid|high（影响强度）",
                }
            ],
        }
    ],
    "player_cards": [
        {
            "id": "str，唯一标识（如 p1）",
            "name": "str，角色名",
            "role": "str，身份/职业",
            "personality": "str，性格特征（40字内）",
            "secret": "str，秘密（玩家自己知道，40字内）",
            "goal": "str，个人目标",
            "stats": {"strength": "int 1-5", "agility": "int 1-5", "intellect": "int 1-5", "charm": "int 1-5", "luck": "int 1-5"},
            "public_desc": "str，公开简介（他人可见，60字内）",
        }
    ],
    "npcs": [
        {
            "id": "str，唯一标识",
            "name": "str，NPC名",
            "role": "str，身份",
            "personality": "str，性格（40字内）",
            "secret": "str，秘密（40字内）",
            "relation": "str，与玩家的初始关系（友好/中立/敌对）",
        }
    ],
    "items": [
        {"id": "str 唯一标识(英文蛇形)", "name": "str 物品名", "kind": "equip|consumable|material|quest|misc", "slot": "weapon|armor|accessory|hand|空", "stats": {"键": "数值，攻击/防御等，可选"}, "desc": "str 简介，可选"}
    ],
    "abilities": [
        {"id": "str 唯一标识", "name": "str 能力名", "desc": "str 效果描述（注入 AI 用）"}
    ],
    "task_templates": [
        {"id": "str", "title": "str 任务标题", "desc": "str 描述", "metric": "str 可选进度口径", "target": "int 目标值", "reward_items": ["str 奖励物品 id/名称，可选"]}
    ],
    "mainline": [
        {"day": "int 触发日", "beat": "str 节点英文名", "flag": "str 达成后置位的flag", "desc": "str 节点描述（给导演）"}
    ],
    "rules": "数值规则包（可选）。必须用 JSON 规则 DSL：只允许 constants/checks/percent_mods/forge/draw_pools/synthesize 六类；表达式只支持算术+比较+逻辑+白名单函数(abs/min/max/clamp/floor/ceil/round/sqrt)；禁止循环/递归/任意代码。例: checks={'攻击成功': {'formula': 'clamp(0.5+(attr.strength-attr.agility)*0.03, 0.05, 0.95)'}}, forge={'强化': {'success': {'formula': 'max(0.85 - stat.level*0.12, 0.05)'}}}, draw_pools={'补给箱': [{'item': '铁质砍刀', 'w': 30}, {'item': '药品', 'w': 70}]}。保持简洁，不要巨型结构",
    "system_rules": "str，注入模型的世界运行规则（如何推进剧情、时间管理、死亡规则、结算方式等，200字内）",
}
