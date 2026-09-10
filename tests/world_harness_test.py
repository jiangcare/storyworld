"""Official pinned SDK/runtime with a local model fixture, real Skill and real calculator."""
from __future__ import annotations
import copy
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.ai import world_harness
from app.config import settings
from app.worlds.packs import installed
from app.worlds.session import WorldSession


@unittest.skipUnless(os.getenv('STORYWORLD_TEST_HARNESS_RUNTIME') == '1', 'requires the compatible pinned Harness runtime')
class WorldHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_skill_loader_and_python_calculator_are_really_invoked(self):
        requests = []
        pack = installed('changsheng')
        session = WorldSession(pack, pack.world['initial'], text='我把旧布浸湿。', mode='player', revision=0, seed='fixture')
        calls = [('skill', {'name': pack.skill_name}), ('world_context', {}), ('read_rule', {'name': 'living'}),
            ('calculate', {'script': 'living', 'quote': '把旧布浸湿', 'arguments_json': json.dumps({
                'operation': 'manipulate', 'target': 'cloth', 'property': 'wet', 'value': True,
                'using': ['basin'], 'method': '把棉布浸入水盆'}, ensure_ascii=False)})]
        class API(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                index = len(requests)
                requests.append(body)
                if index < len(calls):
                    name, args = calls[index]
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'fixture-' + str(index),
                        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                else:
                    delta = {'role': 'assistant', 'content': json.dumps({'prose': '布浸入水中，灰白的经纬逐渐变深，水珠从指缝滑落。'}, ensure_ascii=False)}
                    finish = 'stop'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                for d, f in [(delta, None), ({}, finish)]:
                    chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'deepseek-v4-flash',
                             'choices': [{'index': 0, 'delta': d, 'finish_reason': f}]}
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), API)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                for key, value in {'ai_backend': 'harness', 'deepseek_api_key': 'local-fixture-only',
                    'deepseek_base_url': f'http://127.0.0.1:{server.server_port}', 'harness_work_dir': directory,
                    'llm_timeout': 40}.items():
                    stack.enter_context(patch.object(settings, key, value))
                before = copy.deepcopy(session.state)
                result = await world_harness.run(session)
                self.assertTrue(result['state']['objects']['cloth']['properties']['wet'])
                self.assertFalse(before['objects']['cloth']['properties']['wet'])
                self.assertEqual(result['minutes'], 1)
                self.assertEqual(result['results'][0]['script'], 'living')
                self.assertTrue(session.loaded)
                self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(len(requests), 5)
            self.assertIn('复合动作必须执行完整的因果链', json.dumps(requests[1]['messages'], ensure_ascii=False))
            self.assertIn('你浸湿了旧棉布', json.dumps(requests[-1]['messages'], ensure_ascii=False))
            for request in requests:
                names = {t['function']['name'] for t in request.get('tools', [])}
                self.assertEqual(names, {'skill', 'world_context', 'read_rule', 'calculate', 'scene'})
                self.assertEqual(request['model'], 'deepseek-v4-flash')
                self.assertNotIn('local-fixture-only', json.dumps(request))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
