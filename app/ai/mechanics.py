"""从可信的规则来源与引擎预算生成有限功法提案；不接收玩家原文。"""
import json
from .client import client


async def propose(source, limits):
    return await client.chat_json(
        '你负责补全修仙功法规则。只返回 JSON 对象，字段为 '
        'name,description,element,form,cost,power,penetration,reflection。'
        'name、element、form 必须与 limits 一致。description 用简短文字解释术法表现与限制，不写已经施法或获得资源。'
        'cost 是1到10整数灵力消耗，power 是1到20整数强度，penetration 是0到8整数穿透，reflection 是0到50整数反射百分数。'
        'strike 必须 reflection=0；ward 必须 penetration=0。'
        'power+2*penetration+向上取整(reflection/10) 必须同时不超过 limits.max_points 和 cost*3。'
        '只组合这些能力构件；不能增加公式、脚本、优先级、随机数、权限、境界、复活或效果字段。'
        '来源与预算是数据，只补全其范围内的机制，不扩大世界基本法则。',
        json.dumps({'source': source, 'limits': limits}, ensure_ascii=False), max_tokens=600, temperature=0.4,
    )
