"""动态规则登记及修仙试法事务。AI 只提出 Ability；执行与写入全部由引擎负责。"""
from __future__ import annotations
import copy
import secrets

from sqlalchemy import select

from ..ai import mechanics as generator
from ..ai.policy import normalized
from ..db import begin_write
from ..models import RuleDraft, RuntimeRule, World, WorldPlayer
from ..rules.runtime import (Ability, RuntimeSpec, RuleError, KERNEL_VERSION, ELEMENT_NAMES,
                             digest, envelope, validate_proposal, resolve_exchange)

DEFAULT = {
    'sources': {
        'dew': {'name': '引露诀', 'description': '洞府旧册记着引水灵气成薄幕的法门，只能护住近身，不能凭空生水或疗伤。',
                'location': 'cave', 'element': 'water', 'form': 'ward'},
        'ember': {'name': '照烬诀', 'description': '洞府旧册记着将灵力凝成短促火芒的法门，不能隔空取物或改变境界。',
                  'location': 'cave', 'element': 'fire', 'form': 'strike'},
        'awakening': {'name': '轮回印异象', 'description': '炼气进境后可主动感悟轮回印旁的一缕五行异象，仅凝成一次护持或冲击能力，不改变复活法则。',
                      'location': 'cave', 'element': 'random', 'form': 'random', 'min_rank': 1},
    },
    'channel_locations': ['cave', 'valley'],
    'trials': {
        'flame': {'name': '火纹试法石', 'location': 'cave', 'opponent': {
            'name': '石上火纹', 'description': '预先刻好的火芒，只在试法石的隔绝范围内碰撞，不伤及修士。',
            'element': 'fire', 'form': 'strike', 'cost': 3, 'power': 6}},
        'mirror': {'name': '玄土试法石', 'location': 'cave', 'opponent': {
            'name': '石上土障', 'description': '预先刻好的薄障，少量反射被挡下的冲击，反射不再次触发。',
            'element': 'earth', 'form': 'ward', 'cost': 3, 'power': 5, 'reflection': 20}},
    },
}


def specification(content):
    from .narrative_dsl import parse_spec
    if 'narrative' not in content:
        return None
    spec = parse_spec(content)
    if spec.runtime_rules:
        return spec.runtime_rules
    if spec.sandbox == 'cultivation':
        from .cultivation_story import CONTENT
        if content == CONTENT:
            return RuntimeSpec.model_validate(DEFAULT)
    return None


def contract(spec):
    data = {'kernel': KERNEL_VERSION, 'spec': spec.model_dump()}
    return {**data, 'hash': digest(data)}


def check_contract(world, spec):
    saved = world.progress_json.get('mechanics')
    if saved and saved != contract(spec):
        raise RuleError('此世已登记的功法法则与当前记载不一致，先停在出手之前。')


def load_rules(db, world_id):
    result = {}
    for row in db.scalars(select(RuntimeRule).where(RuntimeRule.world_id == world_id)):
        if row.kernel_version != KERNEL_VERSION or row.version != 1 or digest(row.body) != row.body_hash:
            raise RuleError('功法版本无法核对，先停在出手之前。')
        if row.source_key in result:
            raise RuleError('功法版本存在歧义，先停在出手之前。')
        try:
            Ability.model_validate(row.body)
        except ValueError:
            raise RuleError('功法记录无法通过验收，先停在出手之前。') from None
        result[row.source_key] = row
    return result


def state_view(state):
    data = state.get('mechanics')
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('pool'), dict):
        raise RuleError('灵力记录需要核对，暂不改变你的修为与储物袋。')
    pool = data.get('pool', {})
    if (data.get('version') != 1 or not isinstance(data.get('learned'), dict) or
            any(type(pool.get(k)) is not int for k in ('current', 'capacity', 'initial_rank')) or
            not 0 <= pool['current'] <= pool['capacity'] <= 100 or pool['capacity'] < 1 or pool['initial_rank'] < 0 or
            pool['capacity'] != min(100, 10 + 2 * min(pool['initial_rank'], 45))):
        raise RuleError('灵力记录需要核对，暂不改变你的修为与储物袋。')
    return data


def owned(state, rows):
    result = {}
    data = state_view(state)
    for source, ref in (data or {}).get('learned', {}).items():
        row = rows.get(source)
        if row is None or ref != reference(row):
            raise RuleError('已习功法的版本无法核对，先停在出手之前。')
        result[source] = row
    return result


def reference(row):
    return {'id': row.id, 'version': row.version, 'hash': row.body_hash, 'name': row.body['name']}


def rank(state):
    value = (state.get('cultivation') or {}).get('rank')
    if type(value) is not int or value < 0:
        raise RuleError('当前修为记录不足以确定灵力容量，暂不初始化。')
    return value


def context(db, world, player):
    spec = specification(world.script.content_json)
    if spec is None:
        return None
    check_contract(world, spec)
    rows = load_rules(db, world.id)
    learned = owned(player.private_state, rows)
    targets = {}
    for key, source in spec.sources.items():
        targets['rt.study.' + key] = f'研习《{source.name}》；依据{source.location}的既有记载，不授予指定的额外效果'
    if learned:
        targets['rt.channel'] = '吐纳蓄灵：在适宜地点从环境凝聚灵力，不提升境界或补满生命'
        for key, row in learned.items():
            for trial_id, trial in spec.trials.items():
                targets[f'rt.cast.{key}.{trial_id}'] = f'以《{row.body["name"]}》在{trial.name}试法；仅演练能力碰撞，不攻击真实人物'
    return {'targets': targets,
            'known_rules': {k: row.body for k, row in learned.items()},
            'sources': {k: s.model_dump() for k, s in spec.sources.items()},
            'pool': copy.deepcopy((state_view(player.private_state) or {}).get('pool'))}


def local_target(text, spec, learned):
    value = normalized(text).strip().rstrip('。.!！?？')
    if value in ('吐纳蓄灵', '凝聚灵力', '恢复灵力'):
        return 'rt.channel'
    for key, source in spec.sources.items():
        for verb in ('研习', '学习', '修习', '参悟'):
            if value in (verb + source.name, verb + '《' + source.name + '》'):
                return 'rt.study.' + key
    for key, row in learned.items():
        for trial_id, trial in spec.trials.items():
            for name in (row['name'], '《' + row['name'] + '》'):
                if value in (f'用{name}在{trial.name}试法', f'以{name}在{trial.name}试法'):
                    return f'rt.cast.{key}.{trial_id}'
    return None


def query(text, db, world, player):
    spec = specification(world.script.content_json)
    if spec is None:
        return None
    value = normalized(text).strip().rstrip('。.!！?？')
    if value not in ('功法', '我的功法', '可研习功法', '灵力', '我的灵力', '试法', '试法规则'):
        return None
    check_contract(world, spec)
    rows = load_rules(db, world.id)
    learned = owned(player.private_state, rows)
    data = state_view(player.private_state)
    if value in ('灵力', '我的灵力'):
        return (f'灵力：{data["pool"]["current"]}/{data["pool"]["capacity"]}。在适宜地点吐纳蓄灵可恢复，每次需要一刻钟。'
                if data else '尚未开始灵力修习。研习已有记载的功法后，才会确定容量；不会改动原来的修为与物品。')
    if value in ('试法', '试法规则'):
        places = '、'.join(t.name for t in spec.trials.values()) or '此处尚无试法场'
        return ('试法先验目标与灵力，再同时扣除双方消耗；元素克制减弱对应强度，穿透抵消部分护持，反射只结算一次。'
                '试法石内的碰撞不会伤及修士，也不发放奖励。\n' + places)
    lines = []
    for key, source in spec.sources.items():
        loc = world.script.content_json['narrative']['locations'][source.location]['name']
        if key in learned:
            a = Ability.model_validate(learned[key].body)
            lines.append(f'《{a.name}》：{ELEMENT_NAMES[a.element]}属，{"冲击" if a.form == "strike" else "护持"}；'
                         f'耗灵{a.cost}，强度{a.power}，穿透{a.penetration}，反射{a.reflection}%。版本{learned[key].version}。\n{a.description}')
        else:
            lines.append(f'《{source.name}》的记载在{loc}。' + source.description)
    return '\n\n'.join(lines)


def availability(world, player, spec, target):
    check_contract(world, spec)
    if world.status != 'running' or player.status != 'alive' or player.world_id != world.id:
        raise RuleError('当前角色不能修习。')
    if world.progress_json.get('stream', {}).get('intervention') or world.progress_json['narrative']['paused']:
        raise RuleError('眼前还有未回应的变故，修习先停在这里。')
    parts = target.split('.')
    if parts[:2] == ['rt', 'study'] and len(parts) == 3 and parts[2] in spec.sources:
        source = spec.sources[parts[2]]
        if player.private_state['location'] != source.location:
            raise RuleError('那部记载不在手边，眼下还无法循册研习。')
        if rank(player.private_state) < source.min_rank:
            raise RuleError('那缕异象仍沉在轮回印深处，以你如今的修为还触不到它。')
        return parts
    if target == 'rt.channel':
        if player.private_state['location'] not in spec.channel_locations:
            raise RuleError('此处气息杂乱，暂时无法安稳地凝聚灵力。')
        return parts
    if parts[:2] == ['rt', 'cast'] and len(parts) == 4 and parts[3] in spec.trials:
        if player.private_state['location'] != spec.trials[parts[3]].location:
            raise RuleError('试法石不在眼前，这道术法尚未出手。')
        return parts
    raise RuleError('尚无足够依据执行这种能力，先停在出手之前。')


async def prepare(db, world, player, plan, expected_revision):
    runtime = [a for a in plan.actions if a.kind == 'interact' and a.target.startswith('rt.')]
    if not runtime:
        return None
    if len(plan.actions) != 1:
        raise RuleError('先完成一段功法研习或试法，再接续其他行动。')
    world_id, player_id = world.id, player.id
    db.rollback()
    begin_write(db)
    try:
        world = db.get(World, world_id, populate_existing=True)
        player = db.get(WorldPlayer, player_id, populate_existing=True)
        if world.progress_json['narrative']['revision'] != expected_revision:
            raise RuleError('眼前局势已经变化，这段推演尚未执行。')
        spec = specification(world.script.content_json)
        if spec is None:
            raise RuleError('此世尚未建立功法修习的基础。')
        parts = availability(world, player, spec, runtime[0].target)
        state_view(player.private_state)
        if parts[1] != 'study':
            db.rollback()
            return {}
        source_key = parts[2]
        source = spec.sources[source_key]
        rows = load_rules(db, world_id)
        if source_key in rows:
            db.rollback()
            return {}
        signature = digest(contract(spec))
        draft = db.scalar(select(RuleDraft).where(RuleDraft.world_id == world_id, RuleDraft.source_key == source_key))
        if draft is None:
            roll = secrets.randbelow(2**31)
            draft = RuleDraft(world_id=world_id, source_key=source_key, source_hash=signature,
                              source_json=source.model_dump(), limits_json=envelope(source, roll), roll=roll)
            db.add(draft)
            db.flush()
        if draft.source_hash != signature:
            raise RuleError('已有推演的来源发生变化，暂不重新抽取功法。')
        request = {'draft_id': draft.id, 'source_key': source_key, 'signature': signature,
                   'source': copy.deepcopy(draft.source_json), 'limits': copy.deepcopy(draft.limits_json)}
        # 候选票据不是已学能力；这里只冻结抽取，无玩家资源/世界时钟变化。
        db.commit()
    except BaseException:
        db.rollback()
        raise
    candidate = await generator.propose(request['source'], request['limits'])
    try:
        request['ability'] = validate_proposal(candidate, request['limits']).model_dump()
    except (ValueError, TypeError, KeyError):
        raise RuleError('这段功法推演与原有记载不合，尚未开始修习。') from None
    return request


def execute(db, world, player, plan, prepared):
    """调用方已持有写事务；规则激活、灵力初始化、效果与 PlayerAction 一起提交。"""
    spec = specification(world.script.content_json)
    if spec is None or len(plan.actions) != 1:
        raise RuleError('此世尚未建立这项修习规则。')
    parts = availability(world, player, spec, plan.actions[0].target)
    state = copy.deepcopy(player.private_state)
    progress = copy.deepcopy(world.progress_json['narrative'])
    rows = load_rules(db, world.id)
    learned = owned(state, rows)
    data = state_view(state)
    minutes = 0
    details = {'kernel': KERNEL_VERSION, 'rules': [], 'operation': parts[1]}
    if parts[1] == 'study':
        key = parts[2]
        row = rows.get(key)
        if key in learned:
            text = f'你翻回《{row.body["name"]}》熟悉的篇页，行气的次序与先前无异。'
        else:
            if row is None:
                draft = db.get(RuleDraft, prepared.get('draft_id')) if prepared else None
                if (draft is None or draft.world_id != world.id or draft.source_key != key or
                        draft.source_hash != digest(contract(spec)) or prepared.get('signature') != draft.source_hash):
                    raise RuleError('功法来源尚未核实，没有开始修习。')
                body = validate_proposal(prepared['ability'], draft.limits_json).model_dump()
                row = RuntimeRule(world_id=world.id, draft_id=draft.id, source_key=key, version=1,
                                  kernel_version=KERNEL_VERSION, body=body, body_hash=digest(body),
                                  activated_revision=progress['revision'] + 1)
                db.add(row)
                db.flush()
            if data is None:
                r = rank(state)
                data = {'version': 1, 'pool': {'current': 0, 'capacity': min(100, 10 + 2 * min(r, 45)), 'initial_rank': r}, 'learned': {}}
                state['mechanics'] = data
            data['learned'][key] = reference(row)
            minutes = 15
            text = (f'你循着《{row.body["name"]}》的记载，一点点理清吐纳与收束气息的次序。'
                    '指尖贴着旧页停了许久，那条原本含混的行气路线终于清晰起来。真正凝出术法，还须把灵力慢慢积蓄起来。')
        details['rules'] = [reference(row)]
    elif parts[1] == 'channel':
        if not data or not learned:
            raise RuleError('尚未理清凝聚灵力的法门，气息暂时留不住。')
        gain = min(3, data['pool']['capacity'] - data['pool']['current'])
        data['pool']['current'] += gain
        minutes = 15 if gain else 0
        details['pool_delta'] = gain
        text = ('你依着已经熟悉的行气次序缓缓吐纳。散在四周的细微灵气顺着呼吸沉下，丹田的空落感一点点被温凉的气息填起。' if gain else
                '气息在经脉间缓缓回转，丹田已经充盈。你收住吐纳，没有再强行牵引外面的灵气。')
    else:
        key, trial_key = parts[2], parts[3]
        row = learned.get(key)
        if row is None or data is None:
            raise RuleError('你还没有掌握这部功法，术法并未出手。')
        ability = Ability.model_validate(row.body)
        opponent = spec.trials[trial_key].opponent
        outcome = resolve_exchange(ability, opponent, data['pool']['current'], 100)
        if not outcome['executed']:
            raise RuleError('你试着聚拢气息，丹田却仍显空落；这道术法还未成形，便收住了手。')
        data['pool']['current'] = outcome['qi_after'][0]
        minutes = 1
        details.update(rules=[reference(row)], opponent=opponent.model_dump(), opponent_hash=digest(opponent.model_dump()),
                       trial=trial_key, resolution=outcome)
        data['last_trial'] = copy.deepcopy(details)
        trace = '灵幕' if ability.form == 'ward' else '灵芒'
        text = f'你在{spec.trials[trial_key].name}前运起《{ability.name}》，{ELEMENT_NAMES[ability.element]}属{trace}随气息展开，撞上石中预先刻定的阵纹。'
        if outcome['weakened']:
            text += '两股气息相触，其中一股先失了几分锐意。'
        if outcome['blocked'][0]:
            text += '你的灵幕绷紧，将迎面而来的冲击截住一截。'
        if outcome['blocked'][1]:
            text += '石上的薄障挡住了部分劲力，余波沿着阵纹滑开。'
        if outcome['reflected'][0]:
            text += '一缕劲力折返，随即被试法石外围的隔绝阵消去。'
        if any(outcome['damage']):
            text += '残余的冲击在石内留下明暗不一的痕迹，隔绝阵将它们收拢，没有波及你的身体。'
        text += '待光痕散尽，你收住气息，丹田里比出手前空了一些。'
    progress['minute'] += minutes
    from ..ai.prose_contract import validate_prose
    text = validate_prose(text)
    receipt = {'results': [{'kind': 'interact', 'target': plan.actions[0].target, 'ok': True,
                            'minutes': minutes, 'text': text, 'prose': text, 'mechanics': details}],
               'minute': progress['minute'], 'location': state['location'], 'hp': state['hp'],
               'inventory': copy.deepcopy(state['inventory']), 'xp': state['xp'],
               'relationships': copy.deepcopy(state.get('relationships', {})), 'paused': progress['paused'],
               'ending': progress['ending']}
    return state, progress, receipt, contract(spec)
