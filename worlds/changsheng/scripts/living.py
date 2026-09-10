"""Ordinary objects compose through their properties and material affordances."""
from common import begin, done, identity, obj, only, owned, text


def calculate(request):
    state, args = begin(request, ('travel', 'introduce', 'manipulate'))
    player = state['player']
    if args['operation'] == 'travel':
        only(args, ('destination',))
        destination = args['destination']
        minutes = state['places'][player['location']]['exits'].get(destination)
        if minutes is None:
            raise ValueError('眼前没有直接通向那里的路。')
        player['location'] = destination
        return done(state, minutes, ['你沿路走到' + state['places'][destination]['name'] + '。'])
    if args['operation'] == 'introduce':
        only(args, ('name', 'material', 'purpose'))
        material = args['material']
        if material not in state['places'][player['location']]['supplies']:
            raise ValueError('附近没有这种余料，不能凭想象拿出实物。')
        name, purpose = text(args['name'], 18), text(args['purpose'], 80)
        if any(word in name + purpose for word in ('灵石', '丹药', '法宝', '神兵', '无敌', '无限', '黄金', '灵药')):
            raise ValueError('这里只有普通余料，并无那样的宝物。')
        key = identity('prop_', [material, name, player['location']])
        if key in state['objects']:
            return done(state, 0, ['手边仍是先前取出的' + name + '。'], object_id=key)
        if sum(k.startswith('prop_') for k in state['objects']) >= 24:
            raise ValueError('手边的零碎已经够多，先试着用已有的物件。')
        tags = {'bamboo': ['lever', 'light'], 'wood': ['lever', 'light'],
                'cloth': ['absorbent', 'cover', 'flexible'], 'cord': ['flexible', 'light']}[material]
        state['objects'][key] = {'name': name, 'location': player['location'], 'owner': 'player',
            'material': material, 'tags': tags, 'properties': {'wet': False, 'tied': False},
            'mutable': {'wet': {'values': [True, False], 'needs': ['water', 'absorbent']},
                        'tied': {'values': [True, False], 'needs': ['flexible', 'lever']}}}
        return done(state, 1, ['你从普通余料中取出' + name + '，尚未把它用于其他物件。'],
                    object_id=key, object=state['objects'][key], unperformed_intention=purpose,
                    next_step='若玩家还要求使用它，继续调用 manipulate；仅取出材料没有完成后续安排。')
    only(args, ('target', 'property', 'value', 'using', 'method', 'spell'))
    target = obj(state, args['target'])
    owned(target)
    prop = args['property']
    condition = target['mutable'].get(prop)
    value = args['value']
    if condition is None or type(value) is not bool or value not in condition['values']:
        raise ValueError('这种改变超出了眼前物件可以做到的范围。')
    using = args.get('using', [])
    if not isinstance(using, list) or len(using) > 4 or any(not isinstance(k, str) for k in using):
        raise ValueError('先理清手里有哪些用具。')
    tags = set()
    for key in using:
        item = obj(state, key)
        owned(item)
        tags.update(item['tags'])
        if item['properties'].get('wet'):
            tags.add('water')
    spell = args.get('spell')
    if spell:
        ability = player['abilities'].get(spell)
        if not ability or ability['element'] != 'water':
            raise ValueError('你还没有掌握牵引水灵的法门。')
        if not any('water' in o['tags'] and o['owner'] == 'player' and
                   o['location'] in (player['location'], 'cave' if player['location'] == 'courtyard' else player['location'])
                   for o in state['objects'].values()):
            raise ValueError('近处并无可供牵引的水，术法不能凭空造水。')
        if player['resources']['qi'] < ability['cost']:
            raise ValueError('丹田里尚显空落，水线还未成形便散开了。')
        if 'heavy' in target['tags'] or prop not in ('wet', 'open'):
            raise ValueError('这点水灵只能浸湿近物或拨动轻巧机关，尚不能如此运用。')
        tags.update(('water', 'lever'))
    if condition['needs'] and not tags.intersection(condition['needs']):
        raise ValueError('眼下的用具还不足以完成这个做法。')
    arrangement = {'using': using, 'method': text(args['method']), 'spell': spell}
    if target['properties'].get(prop) == value and (not using and not spell or
            target.get('arrangements', {}).get(prop) == arrangement):
        return done(state, 0, [target['name'] + '已经处于你想要的状态，没有重复用力。'])
    if spell:
        player['resources']['qi'] -= ability['cost']
    target['properties'][prop] = value
    target.setdefault('arrangements', {})[prop] = arrangement
    labels = {'open': ('打开', '合上'), 'wet': ('浸湿', '弄干'), 'tied': ('系牢', '解开'), 'covered': ('盖住', '揭开')}
    verb = labels[prop][0 if value else 1]
    return done(state, 1, [f'你{verb}了{target["name"]}。做法：' + args['method']], changed={args['target']: {prop: value}})
