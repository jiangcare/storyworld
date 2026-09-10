"""初始化数据库并写入种子剧本。"""
import copy
import json
import logging

from app.db import init_db, SessionLocal
from app.engine.script_dsl import validate_script
from app.models import AdminUser, Script
from app.engine.demo_story import SEED_DEMO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEED_SCRIPTS = [
    # ---------------- 多人剧本：末日倒数 ----------------
    {
        "title": "末日倒数 21 天",
        "description": "未知病毒席卷全球，文明崩塌。你们是最后的幸存者，21天后人类要么重建，要么灭绝。",
        "genre": "末日",
        "mode": "multi",
        "min_players": 2,
        "max_players": 5,
        "days": 7,
        "content_json": {
            "mode": "multi",
            "min_players": 2,
            "max_players": 5,
            "days": 7,
            "world": {
                "name": "灰烬之城",
                "background": "2027年，'灰烬病毒'在72小时内摧毁了现代社会。城市化为废墟，幸存者聚集在旧城区的废弃超市里。没有网络、没有电力，只有彼此和明天的日出。",
                "rules": "生存规则：食物与药品是硬通货；感染者会攻击人类；夜里街道比白天危险得多。",
                "countdown": "距最终撤离直升机抵达",
                "countdown_total": 7,
            },
            "chapters": [
                {
                    "day_start": 1,
                    "day_end": 2,
                    "title": "黎明前的幸存者",
                    "goal": "清理据点、确认物资，决定是否收留新的幸存者",
                    "events": [
                        {"day": 1, "title": "广播信号", "desc": "一台破旧收音机收到断续信号：'北区大桥……明晚……撤离……'", "magnitude": "mid"},
                        {"day": 2, "title": "陌生来客", "desc": "据点外来了三个人，自称是从北区逃来的，但他们的说辞有矛盾。", "magnitude": "mid"},
                    ],
                },
                {
                    "day_start": 3,
                    "day_end": 5,
                    "title": "暗流",
                    "goal": "查明真相：广播到底可不可信？陌生来客到底是谁？",
                    "events": [
                        {"day": 3, "title": "夜袭", "desc": "深夜，据点被不明身份的人袭击，有人受伤，有人失踪。", "magnitude": "high"},
                        {"day": 4, "title": "内鬼", "desc": "失踪者被找到时已经失忆，而他的口袋里有一枚陌生的徽章。", "magnitude": "mid"},
                        {"day": 5, "title": "大桥爆炸", "desc": "北区大桥方向传来巨响，火光冲天。撤离路线没了。", "magnitude": "high"},
                    ],
                },
                {
                    "day_start": 6,
                    "day_end": 7,
                    "title": "终局",
                    "goal": "在最后期限前找到生路，或揭开这场灾难的真相",
                    "events": [
                        {"day": 6, "title": "最后的信号", "desc": "收音机再次响起：'撤离点改到南码头，午夜，只等你们。'真假未知。", "magnitude": "mid"},
                        {"day": 7, "title": "终极抉择", "desc": "南码头空无一人，而你们身后的城市正在被火焰吞噬。", "magnitude": "high"},
                    ],
                },
            ],
            "player_cards": [
                {"id": "p1", "name": "林晚", "role": "急诊科医生", "personality": "冷静理性，但失去家人后对生死异常执着", "secret": "她私藏了一管疑似解药的血清", "goal": "找到病毒源头", "stats": {"strength": 2, "agility": 3, "intellect": 5, "charm": 3, "luck": 3}, "public_desc": "曾经是全市最好的急诊医生，现在只想救人。"},
                {"id": "p2", "name": "陈野", "role": "退役军人", "personality": "沉默寡言，行动力极强，信奉'先开枪再问话'", "secret": "他曾在军方秘密实验室服役，知道病毒的部分真相", "goal": "活下去并护送一个'重要目标'", "stats": {"strength": 5, "agility": 4, "intellect": 2, "charm": 2, "luck": 3}, "public_desc": "枪法精准的退伍老兵，是据点最可靠的战力。"},
                {"id": "p3", "name": "苏念", "role": "黑客", "personality": "毒舌但讲义气，对电子产品有偏执的爱", "secret": "她黑进过政府的机密网络，知道撤离计划是个骗局", "goal": "修复城市通讯网络，联系外界", "stats": {"strength": 2, "agility": 3, "intellect": 5, "charm": 2, "luck": 4}, "public_desc": "背着三台报废电脑的怪人，但每次都能鼓捣出有用的东西。"},
                {"id": "p4", "name": "老周", "role": "货车司机", "personality": "憨厚乐观，爱讲废话，关键时刻靠得住", "secret": "他偷听到陌生来客深夜的密谈", "goal": "带着大家回到自己的家乡", "stats": {"strength": 4, "agility": 3, "intellect": 2, "charm": 4, "luck": 3}, "public_desc": "开了二十年大货的老司机，认识城里每一条小路。"},
                {"id": "p5", "name": "白鸽", "role": "记者", "personality": "敏锐而多疑，为真相可以冒险", "secret": "她拍到过病毒爆发第一现场的照片，照片里出现了军方徽章", "goal": "把真相公之于众", "stats": {"strength": 2, "agility": 3, "intellect": 4, "charm": 4, "luck": 3}, "public_desc": "失踪人口名单上的人，她却在废墟里活得比谁都精神。"},
            ],
            "npcs": [
                {"id": "n1", "name": "光头强", "role": "据点管理者", "personality": "精明的商人式人物", "secret": "他与陌生来客暗中交易物资", "relation": "中立"},
                {"id": "n2", "name": "小雨", "role": "流浪女孩", "personality": "胆小但消息灵通", "secret": "她看见过夜袭者的脸", "relation": "友好"},
            ],
            "items": [
                {"id": "iron_sword", "name": "铁质砍刀", "kind": "equip", "slot": "weapon", "stats": {"attack": 6}, "desc": "生锈但依然致命的砍刀。"},
                {"id": "old_uniform", "name": "旧军装", "kind": "equip", "slot": "armor", "stats": {"defense": 4}, "desc": "磨损的军装，能挡一些伤害。"},
                {"id": "bandage", "name": "绷带", "kind": "consumable", "desc": "简易止血绷带。"},
                {"id": "alcohol", "name": "医用酒精", "kind": "material", "desc": "消毒用的酒精。"},
                {"id": "iron_ore", "name": "铁矿石", "kind": "material", "desc": "可以熔炼的矿石。"},
                {"id": "charcoal", "name": "木炭", "kind": "material", "desc": "燃料，也是熔炼必需品。"},
                {"id": "medkit", "name": "急救包", "kind": "consumable", "desc": "绷带与酒精制成的急救包，+8 生命。"},
                {"id": "radio_parts", "name": "无线电零件", "kind": "quest", "desc": "修复广播的关键零件。"},
            ],
            "abilities": [
                {"id": "night_vision", "name": "夜视", "desc": "夜战与潜行能力，夜晚探索不会被发现。"},
                {"id": "field_medic", "name": "战地急救", "desc": "战斗中也能稳定处理伤口。"},
            ],
            "task_templates": [
                {"id": "t_collect", "title": "收集物资", "desc": "在废墟中收集食物与药品", "metric": "collected", "target": 5, "reward_items": ["bandage"]},
                {"id": "t_radio", "title": "修好无线电", "desc": "找到零件修好据点里的无线电", "metric": "parts", "target": 1, "reward_items": ["radio_parts"]},
            ],
            "mainline": [
                {"day": 1, "beat": "signal", "flag": "heard_signal", "desc": "收音机收到断续的撤离信号"},
                {"day": 3, "beat": "raid", "flag": "raid_survived", "desc": "深夜袭击：有人受伤、有人失踪"},
                {"day": 5, "beat": "bridge_down", "flag": "bridge_destroyed", "desc": "北区大桥被炸，撤离路线消失"},
                {"day": 7, "beat": "final_choice", "flag": "finale", "desc": "南码头空无一人，城市正在燃烧，终极抉择"},
            ],
            "rules": {
                "constants": {"base_dodge": 0.05, "base_attack": 0.5},
                "checks": {
                    "attack_success": {"formula": "clamp(const.base_attack + (attr.strength - attr.agility) * 0.04 + stat.level * 0.02, 0.05, 0.95)"},
                    "dodge": {"formula": "const.base_dodge + attr.agility * 0.03"},
                    "stealth": {"formula": "clamp(attr.agility * 0.12, 0.05, 0.9)"},
                },
                "percent_mods": {
                    "iron_sword_damage": {"base": 6, "mods": [{"when": "ability.夜视", "pct": 0.25}, {"when": "stat.hp < 3", "pct": 0.2}]},
                },
                "forge": {
                    "sharpen_sword": {"success": {"formula": "max(0.85 - stat.level * 0.12, 0.05)"}},
                },
                "draw_pools": {
                    "supply_crate": [
                        {"item": "bandage", "w": 35}, {"item": "alcohol", "w": 25},
                        {"item": "iron_ore", "w": 20}, {"item": "charcoal", "w": 15},
                        {"item": "iron_sword", "w": 5},
                    ]
                },
                "synthesize": {
                    "make_medkit": {"inputs": [{"item": "bandage", "qty": 2}, {"item": "alcohol", "qty": 1}], "output": {"item": "medkit", "qty": 1}, "chance": 0.9},
                },
            },
            "system_rules": "1) 玩家行动次日结算；2) 生命值归零进入观察者模式；3) 倒计时结束后世界进入终局；4) 玩家之间的信息不对称必须保持——私人场景只写玩家自己能感知的事；5) 死亡/失败都推进剧情，不重开。",
        },
    },
    # ---------------- 单人剧本：孤岛灯塔 ----------------
    {
        "title": "孤岛灯塔",
        "description": "你作为灯塔看守人独自驻守孤岛十二年，直到那天，海里漂来一封信。",
        "genre": "悬疑",
        "mode": "single",
        "min_players": 1,
        "max_players": 1,
        "days": 5,
        "content_json": {
            "mode": "single",
            "min_players": 1,
            "max_players": 1,
            "days": 5,
            "world": {
                "name": "雾岛",
                "background": "北大西洋深处的孤岛，灯塔是你的全部世界。十二年来你只与海鸥和浓雾为伴。直到今天早上，你在灯塔基座下捡到一只漂流瓶，瓶里有一封写着你名字的信。",
                "rules": "岛上只有你一个人，但最近总觉得灯塔二层的灯室里有人影。",
                "countdown": "季风到来前，你必须决定是否离开",
                "countdown_total": 5,
            },
            "chapters": [
                {
                    "day_start": 1,
                    "day_end": 2,
                    "title": "漂流瓶",
                    "goal": "读完信，决定下一步——查灯塔，还是查小岛？",
                    "events": [
                        {"day": 1, "title": "信", "desc": "信上写着：'你父亲不是死于意外。来灯塔地下室，那里有答案。'可你父亲十二年前就去世了，而你从未说过这件事。", "magnitude": "high"},
                    ],
                },
                {
                    "day_start": 3,
                    "day_end": 4,
                    "title": "灯室里的人影",
                    "goal": "找出灯塔里的秘密，以及那个'人'是谁",
                    "events": [
                        {"day": 3, "title": "人影", "desc": "深夜的灯室里有人影晃动，可塔门是从里面锁的。", "magnitude": "mid"},
                        {"day": 4, "title": "地下室", "desc": "地下室有一扇你从未注意过的暗门，门后是一条向下延伸的隧道。", "magnitude": "mid"},
                    ],
                },
                {
                    "day_start": 5,
                    "day_end": 5,
                    "title": "真相",
                    "goal": "在季风到来前，选择留下还是离开",
                    "events": [
                        {"day": 5, "title": "季风", "desc": "海平线上乌云压境，你手里握着信，站在隧道口。", "magnitude": "high"},
                    ],
                },
            ],
            "player_cards": [
                {"id": "p1", "name": "沈屿", "role": "灯塔看守人", "personality": "孤僻而敏锐，习惯独处，观察力极强", "secret": "你十二年前亲眼看见父亲坠海，却从未对任何人提起细节", "goal": "查明父亲死亡的真相", "stats": {"strength": 3, "agility": 3, "intellect": 4, "charm": 2, "luck": 3}, "public_desc": "守了十二年灯塔的沉默者。"},
            ],
            "npcs": [
                {"id": "n1", "name": "老渔夫", "role": "每月送补给的老人", "personality": "话少，但每次上岸都会多留半天", "secret": "他知道你父亲坠海的真相", "relation": "友好"},
                {"id": "n2", "name": "灯室人影", "role": "未知存在", "personality": "？？？", "secret": "它认识你父亲", "relation": "未知"},
            ],
            "system_rules": "1) 单人剧本，每天推进一章剧情；2) 玩家自由输入行动，AI 即时理解并推进；3) 保持悬疑氛围，信息逐步揭示；4) 结局可以是开放的，但必须有'选择的分量'。",
        },
    },
]


from app.engine.lighthouse import realtime_edition

LEGACY_LIGHTHOUSE = copy.deepcopy(SEED_SCRIPTS[1])
SEED_SCRIPTS[1] = realtime_edition(LEGACY_LIGHTHOUSE)
SEED_SCRIPTS.append(SEED_DEMO)
from app.engine.cultivation_story import SEED_CULTIVATION
SEED_SCRIPTS.append(SEED_CULTIVATION)
from app.engine.novel_slice import SEED_SLICE
SEED_SCRIPTS.append(SEED_SLICE)


def seed_scripts(db) -> None:
    """只添加缺少的内置剧本，不创建管理员；由调用方提交事务。"""
    # 种子剧本（幂等：按标题跳过）
    from app.worlds.packs import seeds
    for item in [*SEED_SCRIPTS, *seeds()]:
        exists = db.query(Script).filter(Script.title == item["title"]).first()
        if exists:
            logger.info("跳过已存在剧本: %s", item["title"])
            continue
        errors = validate_script(item["content_json"])
        if errors:
            logger.error("种子剧本校验失败 %s: %s", item["title"], errors)
            continue
        db.add(
            Script(
                title=item["title"],
                description=item["description"],
                genre=item["genre"],
                mode=item["mode"],
                min_players=item["min_players"],
                max_players=item["max_players"],
                days=item["days"],
                status="approved",
                source="official",
                content_json=item["content_json"],
            )
        )
        logger.info("已写入剧本: %s", item["title"])


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        # 默认管理员
        from app.admin.auth import hash_password

        if db.query(AdminUser).count() == 0:
            db.add(AdminUser(username="admin", password_hash=hash_password("admin123")))
            logger.info("已创建默认管理员 admin/admin123（请尽快在后台修改）")

        seed_scripts(db)
        db.commit()
        logger.info("种子数据完成")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
