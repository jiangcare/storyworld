"""One speculative world turn: Skills choose tools, scripts compute, the engine commits later."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from ..ai.policy import has_control_instruction
from ..ai.prose_contract import validate_prose
from .packs import PackError


def schema(name, description, properties, required=()):
    return {'name': name, 'description': description, 'parameters': {
        'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}}


def string(description='', **kw):
    return {'type': 'string', 'description': description, **kw}


def bounded_text(value, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or has_control_instruction(value) or any(ord(c) < 32 and c not in '\n\t' for c in value)):
        raise PackError('这段内容无法作为世界记录。')
    return value.strip()


class WorldSession:
    def __init__(self, pack, state, *, text, mode, revision, seed, recent=(), intervention=None, corrections=(), autonomy=None):
        self.pack = pack
        self.state = copy.deepcopy(state)
        self.initial = copy.deepcopy(state)
        self.text = text
        self.mode = mode
        self.revision = revision
        self.seed = seed
        self.recent = list(recent)
        self.intervention = intervention
        self.corrections = list(corrections)
        self.autonomy = autonomy or {k: v for k, v in pack.manifest['clock'].items() if k.endswith('_autonomy')}
        self.loaded = False
        self.context_read = False
        self.rules_read = set()
        self.calls = []
        self.results = []
        self.cache = {}
        self.operations = set()
        self.minutes = 0
        self.calculations = 0
        self.responded = False
        self.needs_intervention = False
        self.validate_state(self.state)

    def tool_schemas(self):
        return [
            schema('world_context', '读取本次玩家意图、真实世界、最近正文、已知限制和人物记忆。', {}),
            schema('read_rule', '按需读取当前剧本的规则全文和计算调用格式。',
                   {'name': string(enum=list(self.pack.manifest['rules']))}, ('name',)),
            schema('calculate', '执行已安装剧本的 Python 计算；成功结果暂存本回合，最终统一提交。不能传入状态或代码。',
                   {'script': string(enum=list(self.pack.manifest['scripts'])),
                    'arguments_json': string('按对应规则格式提供一个 JSON 对象，包含 operation 和参数。'),
                    'quote': string('逐字引用本次玩家原话中授权这个行动的片段；提问不是执行授权。')},
                   ('script', 'arguments_json', 'quote')),
            schema('scene', '登记一段可见环境或 NPC 对话/活动，不修改玩家资源、物件属性或承诺。',
                   {'npc': string('在场 NPC ID；纯环境段落用空字符串。'),
                    'speech': string('人物实际说出的话，可为空。'),
                    'activity': string('人物无资源后果的活动，可为空。'),
                    'destination': string('仅 NPC 确实走到相邻地点时填目的地 ID，否则省略或留空；不能移动玩家。'),
                    'detail': string('可感知的普通场景细节；不得声称未计算的后果。'),
                    'intervention': {'type': 'boolean', 'description': '下一步确实需玩家决定时停在行动前。'},
                    'responded': {'type': 'boolean', 'description': '只有玩家明确回应眼前介入点才为真。'}},
                   ('npc', 'speech', 'activity', 'detail', 'intervention', 'responded')),
        ]

    def visible(self, location):
        here = self.state['player']['location']
        groups = self.pack.world.get('perception_groups', [])
        return location == here or any(here in group and location in group for group in groups)

    def context(self):
        self.context_read = True
        state = self.state
        return {'ok': True, 'mode': self.mode, 'player_input': self.text, 'revision': self.revision,
            'consistency_feedback': self.corrections,
            'autonomy': self.autonomy,
            'intervention': self.intervention, 'player': state['player'],
            'places': state['places'], 'resources': self.pack.world['resources'],
            'objects': {k: v for k, v in state['objects'].items() if self.visible(v['location'])},
            'npcs': {k: {**v, 'heard': [m for m in state['memories'] if k in m['witnesses']][-12:]}
                     for k, v in state['npcs'].items() if self.visible(v['location'])},
            'offers': state['offers'], 'player_memories': [m for m in state['memories'] if 'player' in m['witnesses']][-24:],
            'weather': state['weather'], 'recent': self.recent}

    def call(self, name, args):
        before = len(self.calls)
        result = self._call(name, args)
        if len(self.calls) > before:
            self.calls[-1]['ok'] = result.get('ok', False)
            if result.get('error'):
                self.calls[-1]['error'] = result['error']
        return result

    def _call(self, name, args):
        try:
            if not isinstance(args, dict):
                raise PackError('工具参数必须是对象。')
            if len(self.calls) >= self.pack.manifest['limits']['tool_calls']:
                raise PackError('这一段已经足够长，先写出眼前已发生的结果。')
            self.calls.append({'name': name, 'args': copy.deepcopy(args)})
            if name == '_skill_loaded':
                if args != {'name': self.pack.skill_name}:
                    raise PackError('未加载当前剧本。')
                self.loaded = True
                return {'ok': True}
            if not self.loaded:
                raise PackError('先用 skill 加载当前剧本，再主持游戏。')
            if name == 'world_context':
                if args:
                    raise PackError('世界身份由服务端确定。')
                return copy.deepcopy(self.context())
            if not self.context_read:
                raise PackError('先读取当前世界，不能凭空推演。')
            if name == 'read_rule':
                if set(args) != {'name'} or args['name'] not in self.pack.manifest['rules']:
                    raise PackError('不存在这份规则记载。')
                self.rules_read.add(args['name'])
                return {'ok': True, 'name': args['name'], 'content': self.pack.files[self.pack.manifest['rules'][args['name']]]}
            if name == 'calculate':
                return self.calculate(args)
            if name == 'scene':
                return self.scene(args)
            raise PackError('当前世界没有这项工具。')
        except (PackError, KeyError, TypeError, ValueError) as exc:
            message = str(exc) if isinstance(exc, PackError) else '工具参数不符合这部剧本的格式。'
            return {'ok': False, 'error': message}

    def calculate(self, args):
        if self.mode != 'player':
            raise PackError('世界自主推进没有获得玩家行动或资源消费的授权。')
        if set(args) != {'script', 'arguments_json', 'quote'}:
            raise PackError('计算只能接收剧本脚本名、参数和玩家原话。')
        script = args['script']
        if script not in self.pack.manifest['scripts'] or script not in self.rules_read:
            raise PackError('先阅读对应规则，再调用其中的计算。')
        quote = bounded_text(args['quote'], 500)
        if quote not in self.text:
            raise PackError('玩家没有说过这段授权，不能替角色执行。')
        arguments = json.loads(bounded_text(args['arguments_json'], 6000))
        if not isinstance(arguments, dict):
            raise PackError('计算参数必须是对象。')
        fingerprint = hashlib.sha256(json.dumps([script, arguments], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if fingerprint in self.cache:
            return copy.deepcopy(self.cache[fingerprint])
        operation_key = (script, arguments.get('operation'), arguments.get('target'), arguments.get('source'))
        if operation_key in self.operations:
            raise PackError('这一段已经执行过同类行动，不能换参数重复扣费或获益。')
        if self.calculations >= self.pack.manifest['limits']['calculations']:
            raise PackError('先写出这一段已完成的行动，再继续后面的事情。')
        # Stable draw before any model call; retries on the same revision cannot reroll.
        draw = hashlib.sha256(f'{self.seed}:{script}:{arguments.get("operation")}'.encode()).digest()
        request = {'state': self.state, 'arguments': arguments, 'quote': quote, 'intent': self.text,
                   'revision': self.revision, 'roll': int.from_bytes(draw[:8], 'big') / 2**64}
        with tempfile.TemporaryDirectory(prefix='storyworld-calculation-') as directory:
            root = Path(directory)
            for name, body in self.pack.files.items():
                if name.startswith('scripts/') and name.endswith('.py'):
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(body, encoding='utf-8')
            try:
                result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('runner.py')),
                    str(root / self.pack.manifest['scripts'][script])],
                    input=json.dumps(request, ensure_ascii=False), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', timeout=5, cwd=root,
                    env={'LANG': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8'})
                if result.returncode or len(result.stdout) > 262144:
                    raise PackError('这段计算未能完成，尚未产生后果。')
                output = json.loads(result.stdout)
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                raise PackError('这段计算未能完成，尚未产生后果。') from None
        self.calculations += 1
        if output.get('ok') is not True:
            error = bounded_text(output.get('error', '条件不足，没有执行。'), 240)
            return {'ok': False, 'error': error}
        minutes = output['minutes']
        if type(minutes) is not int or minutes < 0 or self.minutes + minutes > self.pack.manifest['limits']['minutes_per_turn']:
            raise PackError('这次行动跨度过长，先停在当前片段。')
        self.validate_state(output['state'])
        facts = [bounded_text(fact, 600) for fact in output['facts']]
        if not facts or len(facts) > 12:
            raise PackError('计算缺少可核对的后果。')
        public = {k: v for k, v in output.items() if k != 'state'}
        self.state = output['state']
        self.minutes += minutes
        self.operations.add(operation_key)
        self.results.append({'script': script, 'arguments': arguments, 'quote': quote, **public})
        self.cache[fingerprint] = copy.deepcopy(public)
        for fact in facts:
            self.remember('calculation', fact)
        return public

    def remember(self, kind, text, npc=''):
        witnesses = ['player'] + [key for key, value in self.state['npcs'].items() if self.visible(value['location'])]
        self.state['memories'].append({'revision': self.revision + 1, 'kind': kind, 'text': text,
                                       'npc': npc, 'witnesses': witnesses})
        self.state['memories'] = self.state['memories'][-self.pack.manifest['limits']['memories']:]

    def scene(self, args):
        expected = {'npc', 'speech', 'activity', 'detail', 'intervention', 'responded'}
        if set(args) - expected - {'destination'} or expected - set(args) or any(type(args[k]) is not bool for k in ('intervention', 'responded')):
            raise PackError('场景记录格式不完整。')
        npc = args['npc']
        if npc and (npc not in self.state['npcs'] or not self.visible(self.state['npcs'][npc]['location'])):
            raise PackError('这个人物眼下不在场，不能让他听见私下的对话。')
        speech = bounded_text(args['speech'], 400, empty=True)
        activity = bounded_text(args['activity'], 160, empty=True)
        detail = bounded_text(args['detail'], 400, empty=True)
        destination = bounded_text(args.get('destination', ''), 40, empty=True)
        journey = 0
        if destination:
            if not npc:
                raise PackError('只有 NPC 可以通过世界事件改变自己的位置。')
            journey = self.state['places'][self.state['npcs'][npc]['location']]['exits'].get(destination)
            if journey is None:
                raise PackError('那个人不能在这一段里直接走到那里。')
            if self.minutes + journey > self.pack.manifest['limits']['minutes_per_turn']:
                raise PackError('这一段已经足够长，人物的远行留到下一段。')
        if (speech or activity) and not npc:
            raise PackError('人物对白和活动必须有在场的说话人。')
        if not speech and not activity and not detail:
            raise PackError('下一段需要实际的新信息。')
        if args['responded'] and (self.mode != 'player' or not self.intervention):
            raise PackError('没有可由这段输入解除的介入点。')
        if args['responded']:
            from ..engine import guidance
            if (not self.text.strip(' ?？!！。.') or guidance.is_help(self.text) or guidance.is_location_question(self.text)
                    or self.text.strip() in ('观察', '继续观察', '看看周围', '灵力', '功法', '试法规则')):
                raise PackError('这次只是观察或询问，没有替玩家回答眼前的选择。')
        # Scene records have no API for inventory, objects, player decisions, or numbers.
        record = {'kind': 'scene', **copy.deepcopy(args)}
        if any(r == record for r in self.results):
            return {'ok': True, 'replayed': True}
        if any(r.get('kind') == 'scene' for r in self.results):
            raise PackError('这段已有一件人物或环境事件，先把当前片段写完，不要重复添加旁观反应。')
        if activity:
            self.state['npcs'][npc]['activity'] = activity
        for kind, value in (('speech', speech), ('activity', activity), ('detail', detail)):
            if value:
                self.remember(kind, value, npc)
        if destination:
            self.remember('movement', self.state['npcs'][npc]['name'] + '沿路走到' + self.state['places'][destination]['name'] + '。', npc)
            self.state['npcs'][npc]['location'] = destination
        self.minutes += journey
        if self.mode == 'world':
            self.minutes = max(1, self.minutes)
        self.results.append(record)
        self.responded |= args['responded']
        self.needs_intervention |= args['intervention']
        return {'ok': True, 'record': record}

    def validate_state(self, state):
        if not isinstance(state, dict) or set(state) != set(self.pack.world['initial']):
            raise PackError('世界状态结构无法核对。')
        player = state['player']
        if (not isinstance(player, dict) or player.get('location') not in state['places']
                or state['places'] != self.pack.world['initial']['places']):
            raise PackError('世界地点无法核对。')
        for key, definition in self.pack.world['resources'].items():
            value = player['resources'].get(key)
            if type(value) is not int or not 0 <= value <= definition['max']:
                raise PackError('资源记录超出了此世的边界。')
        if set(player['resources']) != set(self.pack.world['resources']):
            raise PackError('出现未定义的资源。')
        for key, bounds in self.pack.manifest['state_limits']['player_ints'].items():
            if type(player.get(key)) is not int or not bounds[0] <= player[key] <= bounds[1]:
                raise PackError('角色成长记录无法核对，暂不重新初始化。')
        for resource, capacity in self.pack.manifest['state_limits']['resource_capacities'].items():
            if player['resources'][resource] > player[capacity]:
                raise PackError('资源池记录超过了角色现有容量。')
        if set(state['npcs']) != set(self.pack.world['initial']['npcs']):
            raise PackError('人物身份无法核对。')
        for npc in state['npcs'].values():
            if npc['location'] not in state['places'] or any(type(v) is not int or not 0 <= v <= 999999 for v in npc['resources'].values()):
                raise PackError('人物位置或物资记录无法核对。')
        if len(json.dumps(state, ensure_ascii=False).encode()) > 230000:
            raise PackError('当前世界记录过大，需要先整理再继续。')

    def finish(self, output):
        if not self.loaded or not self.context_read:
            raise PackError('主持过程没有实际加载剧本和读取世界。')
        if not isinstance(output, dict) or set(output) != {'prose'}:
            raise PackError('正文格式未通过验收。')
        prose = validate_prose(output['prose'])
        if len(prose) > 2400 or has_control_instruction(prose):
            raise PackError('正文越过了当前片段的边界。')
        if self.mode == 'world' and not any(r.get('kind') == 'scene' for r in self.results):
            raise PackError('世界没有产生可保存的新事件。')
        if self.mode == 'world' and self.state['player'] != self.initial['player']:
            raise PackError('自动事件不能改变玩家状态。')
        self.validate_state(self.state)
        return {'prose': prose, 'state': self.state, 'minutes': self.minutes,
            'results': self.results, 'calls': self.calls, 'responded': self.responded,
            'intervention': self.needs_intervention, 'pack': self.pack.reference}
