"""凡人修仙氛围的原创开放养成世界；无限复活和数值成长是本游戏自定义规则。"""

def interaction(label, location=None, *, action=None, aliases=(), cost=None, effect=None,
                text="行动已完成。", minutes=15, flags=None):
    entry = {"label": label, "requires": {}, "minutes": minutes, "once": False,
             "aliases": list(aliases), "cost": cost or {}, "success": effect or {},
             "success_text": text, "verbatim": True}
    if location:
        entry["requires"]["location"] = location
    if flags:
        entry["requires"]["flags"] = flags
    if action:
        entry["cultivation_action"] = action
    return entry


CONTENT = {
    "title": "长生录 · 凡尘修仙", "description": "开放修仙养成：自由修炼、采药、炼丹、炼器、交易、结交同道与经营洞府。没有主线和结局，身陨后无限复苏。",
    "genre": "修仙养成", "mode": "single", "min_players": 1, "max_players": 1,
    # 兼容旧剧本元数据；sandbox 不按章节或 days 终止。
    "days": 1,
    "world": {"name": "青岚修真界", "background": "你是一名身具五行杂灵根的凡人，在青岚山脉租下一处简陋洞府，从炼气一层开始修行。坊市、宗门、药谷和荒野互相连通，修士为灵石与机缘奔走。你拥有独属此世的轮回印，身陨仍能在洞府醒来。这里的同道和故事为原创，复活是本游戏的特殊规则。"},
    "chapters": [{"day_start": 1, "day_end": 1, "title": "岁月无尽", "goal": "", "events": []}],
    "player_cards": [{"id": "wanderer", "name": "无名散修", "role": "五行杂灵根修士",
        "public_desc": "无门无派，修途由自己决定。", "goal": "",
        "stats": {"strength": 3, "agility": 3, "intellect": 4, "charm": 3, "luck": 3}}],
    "npcs": [{"id": "merchant", "name": "许掌柜", "role": "坊市商人", "personality": "精打细算，讲信用", "relation": "陌生"},
             {"id": "elder", "name": "顾长老", "role": "青岚宗执事", "personality": "平和，尊重散修的选择", "relation": "陌生"}],
    "items": [{"id": key, "name": name, "kind": kind} for key, name, kind in (
        ("stone", "灵石", "currency"), ("herb", "灵草", "material"), ("ore", "灵铁矿", "material"),
        ("qi_pill", "养气丹", "consumable"), ("heal_pill", "回春丹", "consumable"),
        ("talisman", "护身符", "consumable"))],
    "abilities": [], "task_templates": [], "mainline": [],
    "system_rules": "自由修仙养成，没有主线目标、结局、寿元倒计时或每日行动点。仅服务端规则可以改变状态；所有行动即时结算。轮回印无限复活是本游戏原创规则。拒绝凭聊天更改境界、掉落和复活代价。",
    "narrative": {
        "version": 2, "sandbox": "cultivation", "start": "cave", "max_hp": 20,
        "inventory": {"stone": 12, "herb": 3, "heal_pill": 1},
        "opening": "洞府石门推开，青岚山色尽收眼底。你只是一名炼气一层的散修，储物袋里有12灵石、3株灵草和1枚回春丹。你可以安静修炼，也可以出门采药、炼丹经商、拜入宗门或深入荒野。没人催你完成任务，岁月也不会宣告通关。若身陨，轮回印会带你回洞府，保留成长并扣去一成随身灵石。先做哪件事，全凭你的兴致。",
        "locations": {
            "cave": {"name": "无名洞府", "description": "简陋的石室里铺着蒲团。此处可以修炼、突破、布设聚气阵。山路通向药谷、坊市和宗门。", "exits": {"valley": 5, "market": 10, "sect": 15}},
            "valley": {"name": "青岚谷", "description": "溪流两侧灵草再生，采药没有危险。向北可以回洞府，向东通往坊市，深处通向荒山。", "exits": {"cave": 5, "market": 5, "wilds": 10}},
            "market": {"name": "青石坊市", "description": "许掌柜收购灵草、丹药与护身符。灵草每株卖3灵石、买4灵石，养气丹卖8灵石、买10灵石，护身符卖8灵石；回春丹售价6灵石。丹房和器坊向修士开放。", "exits": {"cave": 10, "valley": 5, "alchemy": 2, "forge": 2, "sect": 10}},
            "alchemy": {"name": "百草丹房", "description": "丹炉里火候正稳。3株灵草加2灵石可以炼制养气丹；技艺熟练后，一炉出丹会更多。", "exits": {"market": 2}},
            "forge": {"name": "听火器坊", "description": "石台上备着炼器工具。2块灵铁矿加2灵石可以制作护身符，在荒野受击时自动消耗并抵挡8伤害。", "exits": {"market": 2, "mine": 10}},
            "sect": {"name": "青岚宗山门", "description": "顾长老正在石阶旁招待来客。你可以与他论道，或者自愿登记成为外门弟子；也可以一直做散修。", "exits": {"cave": 15, "market": 10}},
            "wilds": {"name": "雾隐荒山", "description": "妖兽在山林里游走，历练成功率75%，成功得灵石和修为、受3伤害，失败受12伤害。深处遗迹更加危险。矿脉在山脚。", "exits": {"valley": 10, "mine": 5, "ruins": 15}},
            "mine": {"name": "赤铁矿脉", "description": "浅层矿脉稳定，每次开采可获得2块灵铁矿。矿道通向器坊和荒山。", "exits": {"forge": 10, "wilds": 5}},
            "ruins": {"name": "落星遗迹", "description": "残存的阵纹时明时灭。探寻成功率50%，成功得更多灵石和修为、受6伤害，失败受24伤害。低境界修士可能身陨，护身符可以减伤。", "exits": {"wilds": 15}},
        },
        "interactions": {
            "meditate": interaction("静坐修炼", "cave", action="meditate", aliases=("修炼", "打坐", "打坐修炼", "闭关", "继续修炼"), minutes=60),
            "breakthrough": interaction("尝试突破境界", "cave", action="breakthrough", aliases=("突破", "突破境界", "尝试突破"), minutes=60),
            "cave_upgrade": interaction("扩建洞府聚气阵", "cave", action="cave", aliases=("升级洞府", "扩建洞府"), minutes=60),
            "gather": interaction("采集灵草", "valley", action="gather", aliases=("采药", "采集", "采灵草")),
            "sell_herb": interaction("出售1株灵草，得3灵石", "market", aliases=("卖灵草", "出售灵草", "卖药草"), cost={"herb": 1}, effect={"items": {"stone": 3}}, text="许掌柜验过药性，收下1株灵草，付给你3灵石。", minutes=1),
            "buy_herb": interaction("购买1株灵草", "market", aliases=("买灵草",), cost={"stone": 4}, effect={"items": {"herb": 1}}, text="你付出4灵石，买到1株灵草。", minutes=1),
            "sell_pill": interaction("出售1枚养气丹，得8灵石", "market", aliases=("卖丹药", "卖养气丹"), cost={"qi_pill": 1}, effect={"items": {"stone": 8}}, text="你出售1枚养气丹，获得8灵石。", minutes=1),
            "buy_pill": interaction("购买1枚养气丹", "market", aliases=("买养气丹",), cost={"stone": 10}, effect={"items": {"qi_pill": 1}}, text="你付出10灵石，买到1枚养气丹。", minutes=1),
            "buy_heal": interaction("购买1枚回春丹", "market", aliases=("买回春丹",), cost={"stone": 6}, effect={"items": {"heal_pill": 1}}, text="你付出6灵石，买到1枚回春丹。", minutes=1),
            "sell_talisman": interaction("出售1张护身符，得8灵石", "market", aliases=("卖护身符",), cost={"talisman": 1}, effect={"items": {"stone": 8}}, text="你出售1张护身符，获得8灵石。", minutes=1),
            "merchant_chat": interaction("与许掌柜聊聊生意", "market", aliases=("和许掌柜聊天",), effect={"relationships": {"merchant": 1}}, text="许掌柜说：‘药谷采来的灵草，本店每株收3灵石。炼丹未熟练前，直接卖药也能糊口。’你们熟悉了一些。", minutes=5),
            "alchemy": interaction("炼制养气丹", "alchemy", action="alchemy", aliases=("炼丹", "炼制丹药"), cost={"herb": 3, "stone": 2}, minutes=30),
            "forge": interaction("制作护身符", "forge", action="forge", aliases=("炼器", "打造护身符"), cost={"ore": 2, "stone": 2}, minutes=30),
            "mine": interaction("开采灵铁矿", "mine", action="mine", aliases=("挖矿", "采矿")),
            "hunt": interaction("在荒山历练", "wilds", action="hunt", aliases=("历练", "打妖兽", "猎妖"), minutes=30),
            "ruins": interaction("探索遗迹禁制", "ruins", action="ruins", aliases=("探索遗迹", "探险", "探索"), minutes=30),
            "sect_chat": interaction("与顾长老论道", "sect", aliases=("论道", "与长老论道"), effect={"relationships": {"elder": 1}}, text="顾长老谈起守住本心：‘境界之外，炼丹、炼器、结交同道，也都是修行。’你们的人情增进了一分。"),
            "join_sect": interaction("自愿加入青岚宗外门", "sect", aliases=("加入宗门", "拜入宗门"), flags={"sect_member": False}, effect={"flags": {"sect_member": True}}, text="顾长老将你的名字记入外门名册。你仍可自由修行，不会被派发强制任务。"),
            "leave_sect": interaction("辞别宗门，恢复散修身份", "sect", aliases=("退出宗门",), flags={"sect_member": True}, effect={"flags": {"sect_member": False}}, text="你交还外门名册凭证，恢复散修身份；既有人情保留。"),
            "take_pill": interaction("服用1枚养气丹", action="pill", aliases=("吃养气丹", "服用养气丹"), cost={"qi_pill": 1}, minutes=10),
            "heal": interaction("服用1枚回春丹，恢复最多15气血", action="heal", aliases=("吃回春丹", "服用回春丹"), cost={"heal_pill": 1}, minutes=5),
        },
        "anchors": {},
    },
}
SEED_CULTIVATION = {key: CONTENT[key] for key in ("title", "description", "genre", "mode", "min_players", "max_players", "days")}
SEED_CULTIVATION["content_json"] = CONTENT
