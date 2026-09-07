"""长生修仙的服务端规则；模型只能选择动作，不能指定修为、掉落或复活结果。"""
from __future__ import annotations

import random

from ..ai.policy import normalized

REALMS = ("筑基", "结丹", "元婴", "化神", "炼虚", "合体", "大乘")
HELP = ("这里没有主线任务、通关结局或寿元倒计时，想做什么由你决定。\n"
        "可以在洞府修炼，在青岚谷采药，在坊市买卖，到丹房炼丹、器坊炼器，或去荒山历练。"
        "炼气有十三层，之后逐步提升境界；达到当前修为要求后，在洞府主动突破。\n"
        "采药、出售灵草、炼丹和提升洞府都能反复进行。行动当场显示实际所得、消耗和时间；聊天、问规则、看地图不耗时。\n"
        "气血归零会自动在洞府复苏：保留境界、修为、物品、技艺和人情，损失随身灵石的10%（向上取整），经过60分钟并恢复气血。复活不限次数。\n"
        "输入“地图”“丹方”“复活规则”了解细节，也可以直接点选行动。离线不会扣寿元或自动遇险。")


def enabled(content):
    return content.get("narrative", {}).get("sandbox") == "cultivation"


def initialize(state):
    state["cultivation"] = {"rank": 0, "practice": 0, "alchemy": 0, "forge": 0,
                            "deaths": 0, "cave": 0}


def realm(rank):
    if rank < 13:
        return f"炼气{rank + 1}层"
    offset = rank - 13
    if offset < len(REALMS) * 3:
        return REALMS[offset // 3] + ("初期", "中期", "后期")[offset % 3]
    return f"渡劫后 · 道行第{offset - len(REALMS) * 3 + 1}重"


def required(state):
    return 30 + state["cultivation"]["rank"] * 15


def costs(action, state):
    c = state["cultivation"]
    if action == "breakthrough":
        return {"stone": 3 + c["rank"] * 2}
    if action == "cave":
        return {"stone": 20 * (c["cave"] + 1) ** 2, "ore": 2 * (c["cave"] + 1)}
    return {}


def unavailable(action, state):
    if action == "breakthrough" and state["cultivation"]["practice"] < required(state):
        return f"当前修为 {state['cultivation']['practice']}/{required(state)}，还不足以突破。可以先修炼或服用养气丹。"
    if action == "heal" and state["hp"] >= state["max_hp"]:
        return "气血已经充盈，先把回春丹留在行囊里。"
    return ""


def details(action, state):
    c = state["cultivation"]
    if action == "breakthrough":
        return f"修为需{required(state)}，成功率70%，失败损失部分修为和5气血"
    if action == "meditate":
        return f"修为+{10 + c['rank'] * 2 + c['cave'] * 2}"
    if action == "cave":
        return f"洞府升至{c['cave'] + 1}级，修炼每次多得2修为"
    if action in ("hunt", "ruins"):
        return "有受伤或身陨风险，气血归零自动复活"
    return ""


def resolve(action, state, progress, rng=None):
    """调用方已验收并扣除全部消耗；仅在事务副本上结算。"""
    c, inv = state["cultivation"], state["inventory"]
    rng = rng or random.random
    def add(item, amount):
        inv[item] = inv.get(item, 0) + amount
    def practice(amount):
        c["practice"] += amount
        state["xp"] += amount
    if action == "meditate":
        gain = 10 + c["rank"] * 2 + c["cave"] * 2
        practice(gain)
        return True, f"你收束杂念，沿周天吐纳。修为 +{gain}，目前 {c['practice']}/{required(state)}。"
    if action == "pill":
        gain = 30 + c["rank"] * 3
        practice(gain)
        return True, f"养气丹化作一股暖流融入经脉。修为 +{gain}，目前 {c['practice']}/{required(state)}。"
    if action == "heal":
        gain = min(15, state["max_hp"] - state["hp"])
        state["hp"] += gain
        return True, f"你服下回春丹调息，气血恢复 {gain}。"
    if action == "breakthrough":
        threshold = required(state)
        success = rng() < .7
        if success:
            c["practice"] -= threshold
            c["rank"] += 1
            state["level"] = c["rank"] + 1
            state["max_hp"] = min(100, state["max_hp"] + 2)
            state["hp"] = min(state["max_hp"], state["hp"] + 2)
            return True, f"你冲开了一道关隘，晋入{realm(c['rank'])}。扣除 {threshold} 修为，气血上限提升至 {state['max_hp']}。前路仍由你自行选择。"
        lost = max(1, threshold // 5)
        c["practice"] -= lost
        state["hp"] = max(0, state["hp"] - 5)
        return False, f"灵力未能稳固，这次突破失败。修为 -{lost}，气血 -5；境界保持不变。"
    if action == "gather":
        add("herb", 2)
        return True, "你沿溪辨认药性，采得灵草 ×2。山谷药草会再生，可以继续采集。"
    if action == "mine":
        add("ore", 2)
        return True, "你从浅层矿脉凿下灵铁矿 ×2，收进储物袋。"
    if action == "alchemy":
        count = 1 + c["alchemy"] // 10
        add("qi_pill", count)
        c["alchemy"] += 1
        return True, f"你依丹方控制火候，炼成养气丹 ×{count}，炼丹熟练度 +1（每10点增加1枚产出）。"
    if action == "forge":
        count = 1 + c["forge"] // 10
        add("talisman", count)
        c["forge"] += 1
        return True, f"你将灵铁嵌入护身符，制成护身符 ×{count}，炼器熟练度 +1。历练受伤时自动消耗一张抵挡8点伤害。"
    if action == "cave":
        c["cave"] += 1
        return True, f"你修缮灵脉并布好聚气阵，洞府升至 {c['cave']} 级，今后每次修炼额外获得 {c['cave'] * 2} 修为。"
    if action in ("hunt", "ruins"):
        success = rng() < (.75 if action == "hunt" else .5)
        damage = (3 if action == "hunt" else 6) if success else (12 if action == "hunt" else 24)
        protection = ""
        if inv.get("talisman", 0):
            inv["talisman"] -= 1
            damage = max(0, damage - 8)
            protection = "护身符消耗1张，抵挡8点伤害。"
        state["hp"] = max(0, state["hp"] - damage)
        if success:
            stones = (8 if action == "hunt" else 20) + c["rank"] * 2
            add("stone", stones)
            practice(6 if action == "hunt" else 12)
            return True, f"你谨慎应对险境，带回灵石 ×{stones}，修为 +{6 if action == 'hunt' else 12}。{protection}气血 -{damage}。"
        return False, f"你遭到{'妖兽' if action == 'hunt' else '遗迹禁制'}反击，未取得收获。{protection}气血 -{damage}。"
    raise ValueError("未知修仙动作")


def revive(state, progress, start):
    inv = state["inventory"]
    lost = (inv.get("stone", 0) + 9) // 10
    inv["stone"] = inv.get("stone", 0) - lost
    state["cultivation"]["deaths"] += 1
    state["hp"] = state["max_hp"]
    state["location"] = start
    progress["minute"] += 60
    progress["ending"] = ""
    progress["paused"] = None
    return {"ok": True, "revival": True, "text": f"轮回印亮起，你在洞府复苏，气血恢复至 {state['hp']}。灵石损失 {lost}，复苏经过60分钟；境界、修为、其他物品与人情保留。这是第 {state['cultivation']['deaths']} 次复活。剩余计划已停止。"}


def status(state):
    c = state["cultivation"]
    return (f"🌿 五行杂灵根 · {realm(c['rank'])} · 修为 {c['practice']}/{required(state)}\n"
            f"❤️ 气血 {state['hp']}/{state['max_hp']} · 轮回复苏 {c['deaths']} 次\n"
            f"🏡 洞府 {c['cave']} 级 · 炼丹熟练度 {c['alchemy']} · 炼器熟练度 {c['forge']}")


def query(text, content, state):
    clean = normalized(text).strip().rstrip("?？!！。.")
    if clean in ("复活规则", "怎么复活", "死亡会怎么样", "丹方", "配方", "怎么炼丹", "怎么炼器"):
        if clean in ("丹方", "配方", "怎么炼丹", "怎么炼器"):
            return "丹房：灵草×3 + 灵石×2 → 养气丹，初始产出1枚，每10点炼丹熟练度多1枚。器坊：灵铁矿×2 + 灵石×2 → 护身符，同样随炼器熟练度增产。坊市可买回春丹（6灵石），回血15。洞府升级所需材料见现场选项。"
        return HELP
    if clean in ("修为", "境界", "我的境界", "我的修为", "背包", "储物袋", "我的物品"):
        return status(state) + "\n🎒 " + ("、".join(state.get("items", [])) or "暂无物品")
    if clean in ("地图", "世界地图", "有哪些地方", "附近有什么"):
        locations = content["narrative"]["locations"]
        return (f"🗺 你在{locations[state['location']]['name']}。可以沿道路逐段前往，输入‘去青岚谷’这样的行动即可。\n" +
                "\n".join(f"{loc['name']} → " + "、".join(locations[k]['name'] for k in loc["exits"]) for loc in locations.values()))
    return None


def social(text):
    clean = normalized(text).strip().rstrip("!！。")
    if clean in ("?", "？", "???", "？？？", "迷茫", "不懂", "不知道", "我不知道", "看不懂", "没看懂"):
        return "这里可以按自己的兴趣慢慢修行。想安静一点，可以在洞府说‘修炼’；想出门走走，可以说‘地图’。也可以告诉我哪句话没看懂，我给你解释。"
    return None
