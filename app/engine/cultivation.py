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
        passages = (
            "你在蒲团上盘膝坐定，依着功法缓缓吐纳。起初，只有一缕微凉的气息随着呼吸入体；待心神沉静下来，那股凉意渐渐化开，沿经脉游走，经过的地方泛起细微的酥麻。你不敢催得太急，只守住呼吸的节奏，引着灵气一点点归入丹田。",
            "你阖上双眼，将呼吸放得绵长。熟悉的行功路线在心中铺开，散在经脉间的灵气被一点点拢起，缓缓汇成细流。每经过一处转折，你便稍稍放缓，让那股温热自行流过。等这一轮周天运转完毕，你才松开一直轻扣的手指。",
            "重新坐定后，你没有急着催动功法，而是先听着自己的呼吸。杂念渐渐沉下去，丹田里那一团微弱的暖意也清晰起来。你循着这点暖意引气入脉，细密的麻痒从腹中缓缓散开，又随着吐息收拢；直到气息平稳，才慢慢睁眼。",
        )
        passage = passages[progress['revision'] % len(passages)]
        return True, passage + f"\n\n一个小时的修炼结束，修为 +{gain}（{c['practice']}/{required(state)}）。"
    if action == "pill":
        gain = 30 + c["rank"] * 3
        practice(gain)
        return True, f"丹药入口，淡淡的草木苦味在舌根散开。你缓缓闭目，等药力从腹中升起，再依功法引着那股暖流沿经脉游走。几次吐纳后，躁动的药力终于温顺下来，融入丹田。\n\n修为 +{gain}（{c['practice']}/{required(state)}）。"
    if action == "heal":
        gain = min(15, state["max_hp"] - state["hp"])
        state["hp"] += gain
        return True, f"你将回春丹咽下，慢慢调整呼吸。药力带着温意从腹中散开，原先绷紧的身体一点点松缓，气息也不再那样急促。\n\n气血恢复 {gain}。"
    if action == "breakthrough":
        threshold = required(state)
        success = rng() < .7
        if success:
            c["practice"] -= threshold
            c["rank"] += 1
            state["level"] = c["rank"] + 1
            state["max_hp"] = min(100, state["max_hp"] + 2)
            state["hp"] = min(state["max_hp"], state["hp"] + 2)
            return True, f"你将灵力收拢，一遍遍引向那道迟滞之处。经脉传来隐隐的胀痛，你咬紧牙关，仍旧守着吐纳的节奏。僵持许久，那股阻力终于松动，灵气骤然贯通，随即在丹田中缓缓沉稳下来。你长长吐出一口气，掌心已满是汗。\n\n晋入{realm(c['rank'])}，消耗 {threshold} 修为，气血上限提升至 {state['max_hp']}。"
        lost = max(1, threshold // 5)
        c["practice"] -= lost
        state["hp"] = max(0, state["hp"] - 5)
        return False, f"灵力推至关隘时忽然一滞。你试着稳住气息，胸口却陡然绞紧，凝聚的灵气散作乱流。你只得咬牙收功，任冷汗沿额角滑下；那道关隘仍旧没有松动。\n\n突破失败，修为 -{lost}，气血 -5；境界保持不变。"
    if action == "gather":
        add("herb", 2)
        return True, "你沿着溪岸俯身寻找，指尖拨开沾湿的草叶，泥土的凉意透进掌心。辨清灵草后，你小心松开根边的土，一株株完整取出，拂去泥屑，再用草叶裹好收进储物袋。\n\n采得灵草 ×2。"
    if action == "mine":
        add("ore", 2)
        return True, "矿壁泛着黯淡的赤色。你沿裂隙试探着落凿，金石相击的声音在矿道里来回震荡。细碎石屑落上衣袖，你停下来揉了揉发麻的手腕，才将松动的矿块逐一撬下。\n\n获得灵铁矿 ×2。"
    if action == "alchemy":
        count = 1 + c["alchemy"] // 10
        add("qi_pill", count)
        c["alchemy"] += 1
        return True, f"灵草投入炉中，苦涩的药香很快被热气托起。你守在丹炉前，随着药液的翻涌一点点调整火候，直到杂乱的气味渐渐收拢。揭开炉盖时，余热扑上脸颊，凝成的丹药安静地躺在炉底。\n\n炼成养气丹 ×{count}，炼丹熟练度 +1。"
    if action == "forge":
        count = 1 + c["forge"] // 10
        add("talisman", count)
        c["forge"] += 1
        return True, f"灵铁在炉火里渐渐透红。你屏住呼吸，将它嵌入符体，指尖沿着纹路缓缓导入灵力。最后一处纹路接通时，符面微微一亮，随即沉寂下来；你等它冷却，才仔细收好。\n\n制成护身符 ×{count}，炼器熟练度 +1。"
    if action == "cave":
        c["cave"] += 1
        return True, f"你沿石室重新理顺灵脉，将材料一一嵌进阵位。最后一块灵石落定，周遭散乱的灵气开始缓缓收拢。你退回蒲团旁，闭目感受片刻，呼吸间的气息比先前凝实了些。\n\n洞府升至 {c['cave']} 级，修炼额外收益增至 {c['cave'] * 2} 修为。"
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
            scene = ("林间猛然响起枝叶折断的声音。你侧身避开妖兽的扑击，借它收势不及的空隙稳住身形，几番周旋后终于脱出险处。" if action == 'hunt' else
                     "残损的阵纹忽明忽暗。你试探着挪动脚步，等那一线灵光黯下去，才屏息穿过禁制的间隙。直到将所得收好，紧绷的肩背才稍稍松开。")
            return True, scene + f"\n\n带回灵石 ×{stones}，修为 +{6 if action == 'hunt' else 12}。{protection}气血 -{damage}。"
        scene = ("妖兽的反扑比你预想得更快。你来不及收势，只觉一股大力撞来，呼吸顿时一窒，耳边尽是急促的心跳。" if action == 'hunt' else
                 "脚边的阵纹骤然亮起。你察觉不妙，却已来不及避开，沉重的灵压迎面压下，将刚刚提起的气息硬生生打散。")
        return False, scene + f"\n\n这次未取得收获。{protection}气血 -{damage}。"
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
    return {"ok": True, "revival": True, "text": f"意识沉入黑暗时，轮回印深处浮起一点微光。再睁眼，触手已是洞府冰凉的石地。你试着握了握手，熟悉的灵力仍在经脉间流转，方才的险境却像一场刚醒的梦。\n\n你在洞府复苏，气血恢复至 {state['hp']}。灵石损失 {lost}，复苏经过60分钟；成长与其他物品保留。第 {state['cultivation']['deaths']} 次复活，原计划的后续动作停止。"}


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
        return "气血归零后，轮回印会带你回洞府，恢复气血。境界、修为、技艺、物品和人情保留；损失一成随身灵石（向上取整），经过60分钟。没有灵石也能复苏，次数不限。"
    if clean in ("修为", "境界", "我的境界", "我的修为"):
        c = state['cultivation']
        return f"你目前处于{realm(c['rank'])}，修为 {c['practice']}/{required(state)}。"
    if clean in ("背包", "储物袋", "我的物品"):
        return "储物袋里有：" + ("、".join(state.get("items", [])) or "暂无物品")
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
