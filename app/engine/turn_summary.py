"""由提交前后状态生成简短变化，完整规则回执保留在行动记录中。"""
from __future__ import annotations


def changes(content, before, after, elapsed, *, revived=False):
    from . import cultivation
    parts = []
    if before['location'] != after['location']:
        parts.append('抵达' + content['narrative']['locations'][after['location']]['name'])
    if cultivation.enabled(content):
        old, new = before['cultivation'], after['cultivation']
        if new['rank'] != old['rank']:
            parts.append('晋入' + cultivation.realm(new['rank']))
        diff = new['practice'] - old['practice']
        if diff:
            parts.append(f'修为 {diff:+d}（{new["practice"]}/{cultivation.required(after)}）')
        for key, name in (('alchemy', '炼丹熟练度'), ('forge', '炼器熟练度'), ('cave', '洞府等级')):
            if new[key] != old[key]:
                parts.append(f'{name} {new[key] - old[key]:+d}')
        if revived:
            parts.append(f'轮回印复苏，第{new["deaths"]}次，气血恢复至{after["hp"]}')
    elif before['xp'] != after['xp']:
        parts.append(f'经验 {after["xp"] - before["xp"]:+d}')
    if not revived and before['hp'] != after['hp']:
        parts.append(f'气血 {after["hp"] - before["hp"]:+d}')
    if before['max_hp'] != after['max_hp']:
        parts.append(f'气血上限 {after["max_hp"]}')
    for item in content.get('items', []):
        key = item['id']
        diff = after['inventory'].get(key, 0) - before['inventory'].get(key, 0)
        if diff:
            parts.append(f'{item["name"]} {diff:+d}')
    names = {n['id']: n['name'] for n in content.get('npcs', [])}
    for key, value in after['relationships'].items():
        diff = value - before['relationships'].get(key, 0)
        if diff:
            parts.append(f'{names.get(key, key)}的人情 {diff:+d}')
    for clue in after.get('clues', []):
        if clue not in before.get('clues', []):
            parts.append('发现：' + clue)
    if elapsed:
        parts.append(f'经过{elapsed}分钟')
    return ' · '.join(parts)


def ending(receipt):
    if receipt['ending']:
        return receipt['ending']
    if receipt['paused']:
        return '关键事件暂停，等待你的决定。'
    return ''


def with_narration(text, receipt):
    return '\n\n'.join(part for part in (text, receipt.get('summary', ''), ending(receipt)) if part)
