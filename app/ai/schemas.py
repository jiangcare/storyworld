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
        "hp_delta": "int，生命值变化（无则0）",
        "items_added": ["str，新增道具"],
        "items_removed": ["str，失去道具"],
        "clues_added": ["str，新增线索"],
        "notes": {"键": "str，其他状态记录（好感度、阵营变化等）"},
    },
    "scene_ended": "bool，本日场景是否已到自然结束（玩家无更多事可做）",
}

# ---- 意图层：玩家输入理解 ----
INTENT_OUTPUT_SCHEMA = {
    "action_type": "str，枚举: investigate(调查) | talk(交谈) | move(移动) | use(使用道具) | fight(战斗) | help(求助/协作) | hide(躲藏) | rest(休息) | other(其他)",
    "target": "str，行动对象（人物/地点/物品），无则空",
    "summary": "str，对玩家意图的中文简述（20字内）",
    "dice_check": "bool，是否需要检定（高风险动作如战斗/偷窃/说服）",
    "attribute": "str，检定属性: strength|agility|intellect|charm|luck，无检定则空",
}

# ---- 剧本完善：用户草稿 → 完整剧本 ----
SCRIPT_OUTPUT_SCHEMA = {
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
    "system_rules": "str，注入模型的世界运行规则（如何推进剧情、时间管理、死亡规则、结算方式等，200字内）",
}
