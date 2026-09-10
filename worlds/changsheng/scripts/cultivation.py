"""Cultivation belongs to this pack, not to the platform's action dispatcher."""
from common import begin, done, integer, obj, only, text

COUNTERS = {'water': 'fire', 'fire': 'metal', 'metal': 'wood', 'wood': 'earth', 'earth': 'water'}


def calculate(request):
    state, args = begin(request, ('study', 'practice', 'channel', 'breakthrough', 'rest', 'trial'))
    player = state['player']
    pool = player['resources']
    operation = args['operation']
    if operation == 'trial':
        only(args, ('ability', 'target'))
        ability = player['abilities'].get(args['ability'])
        stone = obj(state, args['target'])
        opponent = stone.get('ability')
        if not ability or not opponent or 'trial' not in stone['tags']:
            raise ValueError('功法或试法石尚未准备好。')
        if pool['qi'] < ability['cost']:
            raise ValueError('灵力不足，这道术法并未出手。')
        pool['qi'] -= ability['cost']
        left, right = ability['power'], opponent['power']
        if COUNTERS.get(opponent['element']) == ability['element']:
            left = max(1, left * 3 // 4)
        if COUNTERS.get(ability['element']) == opponent['element']:
            right = max(1, right * 3 // 4)
        blocked = min(left, right) if ability['form'] == 'ward' and opponent['form'] == 'strike' else 0
        reflected = blocked * ability['reflection'] // 100
        resolution = {'kernel': 1, 'phases': ['资格', '代价', '属性', '护持', '一次反射'],
                      'effective_power': [left, right], 'blocked': blocked, 'reflected': reflected,
                      'remaining_impact': max(0, right - blocked), 'player_harmed': False}
        player['last_trial'] = {'ability': args['ability'], 'target': args['target'], 'resolution': resolution}
        facts = ['你运起引露诀，水幕与石中预先刻定的火芒相撞。']
        if blocked:
            facts.append('水幕挡下冲击' + ('，只余一线火芒穿过' if right > blocked else '，火芒没有穿过护持') + '。')
        if reflected:
            facts.append('一缕被挡下的火芒折返，反射仅发生一次。')
        facts.append('隔绝阵将余波收拢，没有伤到你；此次耗去一些灵力。')
        return done(state, 1, facts, resolution=resolution)
    if player['location'] not in ('cave', 'courtyard'):
        raise ValueError('这里不宜安稳行功，先找一处清静的地方。')
    if operation == 'study':
        only(args, ('source', 'proposal'))
        if args['source'] != 'dew':
            raise ValueError('眼前旧册并没有记载这部功法。')
        if 'dew' in player['abilities']:
            return done(state, 0, ['引露诀的行气次序已经熟悉，再读时仍是同一部功法。'], ability=player['abilities']['dew'])
        proposal = args.get('proposal')
        if not isinstance(proposal, dict) or set(proposal) != {'cost', 'power', 'reflection', 'description'}:
            raise ValueError('需要先依旧册补全消耗、护持强度、反射与有限的功法说明。')
        cost = integer(proposal['cost'], 1, 4)
        power = integer(proposal['power'], 1, 8)
        reflection = integer(proposal['reflection'], 0, 30)
        if power + (reflection + 9) // 10 > min(8, cost * 3):
            raise ValueError('这种护持效果超过了旧册所述的行气效率。')
        description = text(proposal['description'])
        if any(word in description for word in ('无限', '无敌', '绝对', '凭空', '起死回生', '不耗', '无消耗')):
            raise ValueError('旧册并没有记载这样的无穷力量。')
        ability = {'name': '引露诀', 'version': 1, 'source': '洞府旧册', 'element': 'water', 'form': 'ward',
                   'cost': cost, 'power': power, 'reflection': reflection, 'description': description,
                   'constraints': '仅牵引两步内的少量现有水，护持或拨动轻物，不能造水或改变生命。'}
        player['abilities']['dew'] = ability
        return done(state, 15, ['你循旧册理清引露诀的吐纳与收束次序，学会这部有限的水属功法。',
                               '学习没有恢复灵力；真正施法还要先积蓄气息。'], ability=ability)
    if operation in ('practice', 'channel'):
        only(args, ('minutes',))
        minutes = integer(args.get('minutes', 15 if operation == 'channel' else 60), 15, 60)
        if operation == 'practice':
            pool['practice'] += minutes // 6
            return done(state, minutes, ['你按基础吐纳法行功，气息沿经脉往复，修为缓缓积累；境界尚未变化。'])
        if not player['abilities']:
            raise ValueError('先理清旧册的行气法门，才能把灵力安稳留在丹田。')
        gain = min(player['qi_capacity'] - pool['qi'], minutes // 15 * 3)
        pool['qi'] += gain
        return done(state, minutes if gain else 0, ['吐纳中，游散的灵气逐渐在丹田积聚。' if gain else '丹田已经充盈，你收住吐纳，没有再强行牵引。'])
    if operation == 'rest':
        only(args, ())
        gain = min(5, 20 - pool['hp'])
        pool['hp'] += gain
        return done(state, 30, ['你暂且休息，绷紧的身体逐渐放松。'] + (['气血恢复了一些。'] if gain else []))
    only(args, ())
    threshold = 30 * (player['rank'] + 1)
    cost = 3 + player['rank'] * 2
    if pool['practice'] < threshold or pool['stone'] < cost:
        raise ValueError('积累的修为或引气用的灵石尚不足，突破还没有开始。')
    if player['rank'] >= 90:
        raise ValueError('更高层次的行气尺度尚未建立，先稳住已有修为。')
    pool['stone'] -= cost
    success = request['roll'] < .7
    if success:
        pool['practice'] -= threshold
        player['rank'] += 1
        player['qi_capacity'] = min(100, 10 + 2 * player['rank'])
        facts = ['你凝神引气，终于越过原先的迟滞，修行进境一层；丹田能容纳更多气息，却没有凭空充满。']
    else:
        pool['practice'] -= max(1, threshold // 5)
        pool['hp'] -= 5
        facts = ['气息在关隘处散乱，你收功时胸口发闷，损失了一些修为与气血；境界没有变化。']
    return done(state, 60, facts, roll=request['roll'], success=success)
