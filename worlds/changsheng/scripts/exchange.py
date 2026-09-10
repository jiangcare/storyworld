"""Persistent proposals and atomic, conserved exchanges; speech is not payment."""
from common import begin, done, identity, integer, nearby, only, text


def inventory(value):
    if not isinstance(value, dict) or len(value) > 2 or set(value) - {'stone', 'herb'}:
        raise ValueError('目前只能核对灵石与灵草的实物交换。')
    return {key: integer(quantity, 1, 99) for key, quantity in value.items()}


def calculate(request):
    state, args = begin(request, ('offer', 'accept', 'decline'))
    if args['operation'] == 'offer':
        only(args, ('npc', 'give', 'take', 'terms', 'service'))
        npc = state['npcs'].get(args['npc'])
        if not npc or not nearby(state, npc['location']):
            raise ValueError('那个人眼下不在附近，尚未听到你的提议。')
        give, take = inventory(args.get('give', {})), inventory(args.get('take', {}))
        terms = text(args['terms'])
        service = args.get('service', '')
        if not isinstance(service, str) or len(service) > 120:
            raise ValueError('劳务条件还不清楚。')
        if service and (give or take):
            raise ValueError('先把劳务条件谈清，再另行核对实物交付。')
        if not service:
            incoming = give.get('stone', 0) + give.get('herb', 0) * 3
            outgoing = take.get('stone', 0) + take.get('herb', 0) * 4
            if not give or not take or set(give).intersection(take) or incoming < outgoing or incoming > outgoing * 2:
                raise ValueError('这个交换不合现有物价，对方尚未接受这样的条件。')
            if any(state['player']['resources'][k] < q for k, q in give.items()) or any(npc['resources'].get(k, 0) < q for k, q in take.items()):
                raise ValueError('双方手边的实物不足以兑现这个报价。')
        # The same terms point at the same offer; do not regenerate a consumed contract.
        key = identity('offer_', [args['npc'], give, take, terms, service, request['revision']])
        if len(state['offers']) >= 60 and key not in state['offers']:
            raise ValueError('未整理的约定已太多，先处理现有报价。')
        state['offers'].setdefault(key, {'npc': args['npc'], 'give': give, 'take': take,
            'terms': terms, 'service': service, 'status': 'offered'})
        return done(state, 0, [npc['name'] + '与你商量条件：' + terms + '。尚未接受，也没有交付。'], offer_id=key)
    only(args, ('offer',))
    offer = state['offers'].get(args['offer'])
    if offer is None:
        raise ValueError('找不到那次具体的报价，先核对之前谈的条件。')
    npc = state['npcs'][offer['npc']]
    if not nearby(state, npc['location']):
        raise ValueError('对方不在眼前，交易暂未进行。')
    if offer['status'] != 'offered':
        return done(state, 0, ['这次约定已是' + ('成交' if offer['status'] == 'accepted' else '拒绝') + '的状态，没有再次交付。'])
    if args['operation'] == 'decline':
        offer['status'] = 'declined'
        return done(state, 0, ['你拒绝了这次条件，对方记住了你的答复。'])
    quote = request['quote']
    if any(word in quote for word in ('能不能', '可不可以', '多少', '假如', '如果', '?', '？')) or not any(
            word in quote for word in ('同意', '接受', '成交', '就这么办', '按这个', '答应', '卖', '买', '换')):
        raise ValueError('你还没有明确答应这笔交易，东西仍各在原主手里。')
    if offer['service']:
        raise ValueError('劳务的履行与租用规则尚未约定完整，这次还没有签下承诺。')
    player = state['player']['resources']
    stock = npc['resources']
    if any(player[k] < q for k, q in offer['give'].items()) or any(stock.get(k, 0) < q for k, q in offer['take'].items()):
        raise ValueError('实物数量已有变化，这笔交易尚未发生。')
    for source, dest, goods in ((player, stock, offer['give']), (stock, player, offer['take'])):
        for key, quantity in goods.items():
            source[key] -= quantity
            dest[key] = dest.get(key, 0) + quantity
    offer['status'] = 'accepted'
    return done(state, 1, ['你明确接受条件，双方当面核对并完成交付：' + offer['terms'] + '。'], exchange={'give': offer['give'], 'take': offer['take']})
