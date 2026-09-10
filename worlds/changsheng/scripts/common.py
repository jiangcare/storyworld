"""This world's shared calculation helpers; no platform database or model calls."""
import copy
import hashlib
import json


def begin(request, operations):
    args = request['arguments']
    if not isinstance(args, dict) or args.get('operation') not in operations:
        raise ValueError('这段做法还缺少具体的执行依据。')
    intent = request.get('intent', '').strip()
    if args['operation'] != 'offer' and (intent.startswith(('能不能', '可不可以', '怎么', '如何', '为什么', '多少钱', '假如', '如果'))
            or any(word in intent for word in ('的规则是什么', '要多少灵石', '消耗多少', '有什么条件'))):
        raise ValueError('这次是在问条件，尚未授权实际执行。请依据当前规则说明情况。')
    return copy.deepcopy(request['state']), args


def only(args, keys):
    if set(args) - set(keys) - {'operation'}:
        raise ValueError('这次尝试包含无法核对的额外条件。')


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('这个数量不符合此处的实际条件。')
    return value


def text(value, maximum=180):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('这段记载还不够清楚。')
    return value.strip()


def nearby(state, location):
    here = state['player']['location']
    return location == here or {location, here} <= {'cave', 'courtyard'}


def obj(state, key):
    item = state['objects'].get(key)
    if item is None or not nearby(state, item['location']):
        raise ValueError('那样东西眼下并不在手边。')
    return item


def owned(item):
    if item['owner'] != 'player':
        raise ValueError('这不是你的物件，主人尚未允许你动它。')


def identity(prefix, value):
    return prefix + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def done(state, minutes, facts, **extra):
    player = state['player']
    if player['resources']['hp'] <= 0:
        player['resources']['stone'] -= player['resources']['stone'] // 10
        player['resources']['hp'] = 20
        player['location'] = 'cave'
        player['revivals'] += 1
        facts.append('气血耗尽后，轮回印让你在洞府苏醒；失去一成随身灵石，修为、功法和灵力余额保留。')
    return {'ok': True, 'state': state, 'minutes': minutes, 'facts': facts, **extra}
