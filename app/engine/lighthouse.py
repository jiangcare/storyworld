"""孤岛灯塔即时版本：保留原信件伏笔，探索结果由作者明确编写。"""
from __future__ import annotations
import copy

LETTER = "你展开那封写着‘沈屿亲启’的信。纸上的字迹被海水晕开，关键一句仍清晰可辨：‘你父亲不是死于意外。来灯塔地下室，那里有答案。’你父亲十二年前就去世了。信没有署名，指向地下室的线索已记下。"


def realtime_edition(legacy):
    entry = copy.deepcopy(legacy)
    entry.update(title="孤岛灯塔 · 即时探索", days=1,
                 description="打开漂流瓶里的信，调查灯室与地下室，在季风前决定留下还是离开。每次探索立即返回结果。")
    content = entry["content_json"]
    content.update(days=1, chapters=[{"day_start": 1, "day_end": 1, "title": "信与灯塔",
                                    "goal": "读信并调查父亲当年的事故，决定留下还是离开", "events": []}],
                   items=[{"id": "letter", "name": "漂流瓶里的信", "kind": "quest"}],
                   system_rules="单人即时探索。每个行动立刻结算；问规则、重读已发现的信息不推进时间。结果由叙事规则决定。")
    content["world"].update(countdown="季风迫近，在风暴封岛前作出选择", countdown_total=1)
    content["narrative"] = {
        "version": 2, "start": "base", "inventory": {"letter": 1},
        "opening": "清晨，你在灯塔基座下捡到一个漂流瓶。里面的信封写着你的名字：沈屿。你已经把信拿在手里，可以直接说‘信上写了什么’，或选择‘打开漂流瓶里的信’。探索会立即得到结果，不用等晚上的推送。",
        "locations": {
            "base": {"name": "灯塔基座", "description": "海雾笼罩着基座。漂流瓶已经打开，信封握在你手里；楼梯通往灯室，侧门通往地下室，石径通向码头。", "exits": {"basement": 3, "lantern": 3, "dock": 5}},
            "basement": {"name": "灯塔地下室", "description": "旧储物架旁放着十二年前的值班簿。墙角有一扇关闭的检修门。", "exits": {"base": 3, "tunnel": 5}},
            "lantern": {"name": "二层灯室", "description": "灯镜缓慢转动。墙上的影子随着光束摇晃，一件旧雨衣搭在灯架上。", "exits": {"base": 3}},
            "tunnel": {"name": "地下检修道", "description": "潮湿的检修道一直通到海崖下。墙上挂着生锈的安全绳，出口旁有一块刻字的木牌。", "exits": {"basement": 5, "dock": 5}},
            "dock": {"name": "小岛码头", "description": "送补给的老渔夫正在系船。季风将至，他问你是否搭船离开；你也可以返回灯塔。", "exits": {"base": 5}},
        },
        "interactions": {
            "read_letter": {"label": "打开漂流瓶里的信", "requires": {"items": {"letter": 1}}, "minutes": 1,
                "aliases": ["漂流瓶的信写的是什么", "漂流瓶里的信写的是什么", "信件内容是什么", "信上写了什么", "读信", "打开信", "看看信", "信写的什么"],
                "success": {"flags": {"letter_read": True}, "clues": ["信上写着：你父亲不是死于意外。来灯塔地下室，那里有答案。"], "xp": 5},
                "success_text": LETTER, "repeat_text": LETTER, "verbatim": True},
            "read_log": {"label": "查阅十二年前的值班簿", "requires": {"location": "basement", "flags": {"letter_read": True}},
                "success": {"flags": {"log_read": True}, "clues": ["事故当天，父亲发现灯塔检修绳被人为割断，仍下到海崖救助搁浅渔船。"], "xp": 5},
                "success_text": "值班簿最后一页写着：‘检修绳有新割痕，不能再用。但崖下还有人。’落款是父亲的名字。你确认信中的指控有实物依据。"},
            "inspect_shadow": {"label": "调查灯室里晃动的人影", "requires": {"location": "lantern"},
                "success": {"flags": {"shadow_checked": True}, "clues": ["灯室里的人影来自旧雨衣和转动的灯镜，这次没有发现其他人。"]},
                "success_text": "你绕过灯镜，发现晃动的影子来自搭在灯架上的旧雨衣。灯室里没有藏人。"},
            "inspect_tunnel": {"label": "检查旧安全绳和出口木牌", "requires": {"location": "tunnel", "flags": {"log_read": True}},
                "success": {"flags": {"evidence_found": True}, "clues": ["安全绳的断口平整；出口木牌留下了当年获救渔船的名字，与老渔夫的船相同。"], "xp": 5},
                "success_text": "断绳上仍能辨认出刀口。出口木牌记着那艘获救渔船的名字，正是老渔夫今天驶来的船。你可以向他核实往事。"},
            "ask_fisher": {"label": "拿调查到的证据向老渔夫核实往事", "requires": {"location": "dock", "flags": {"evidence_found": True}},
                "success": {"flags": {"truth_known": True}, "clues": ["老渔夫承认寄出了那封信：父亲为救他遇难；割绳者的身份仍需带证据到岸上调查。"], "relationships": {"n1": 10}},
                "success_text": "老渔夫承认信是他写的。父亲当年为救他的船下到海崖，却遇上被人割断的安全绳。他愿意带你和证据去岸上作证，但不知道割绳者是谁。"},
            "leave": {"label": "带上证据，搭老渔夫的船离岛", "requires": {"location": "dock", "flags": {"truth_known": True}},
                "success": {"ending": "带信离岛：你带着值班记录与证言离开，准备在岸上继续追查父亲的死因。"},
                "success_text": "你把证据收好，踏上渔船。灯塔渐渐隐入海雾，调查将继续。"},
            "stay": {"label": "决定留下，整理证据并继续守灯", "requires": {"location": "base", "flags": {"truth_known": True}},
                "success": {"ending": "守灯人：你选择留在岛上保管证据，并约定下一班补给船送来调查人员。"},
                "success_text": "你把信与值班记录锁进干燥的柜子，重新点亮灯室。你决定留下，但不再让往事沉默。"},
        },
        "anchors": {},
    }
    return entry
