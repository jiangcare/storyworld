"""Harness 路由/清理测试；设置 STORYWORLD_TEST_HARNESS_RUNTIME=1 验证真实 SDK + 本地假 API。"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.ai.client import LLMClient, LLMError
from app.ai.policy import GAME_BOUNDARY
from app.ai.harness_backend import HarnessBackend, HarnessError


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = self.stack.enter_context(tempfile.TemporaryDirectory())
        for key, value in {"ai_backend": "harness", "deepseek_api_key": "local-test-key",
                           "harness_work_dir": self.root, "llm_timeout": 5}.items():
            self.stack.enter_context(patch.object(settings, key, value))

    async def test_default_route_and_generation_parameters(self):
        client = LLMClient()
        with patch.object(client._harness, "generate", AsyncMock(return_value='{"ok":true}')) as generate, \
                patch("app.ai.client.AsyncOpenAI") as direct:
            self.assertEqual(await client.chat_json("系统", "玩家", max_tokens=321, temperature=0.2), {"ok": True})
            generate.assert_awaited_once_with(GAME_BOUNDARY + "\n\n【本次游戏职责】\n系统", "玩家", max_tokens=321, temperature=0.2)
            direct.assert_not_called()

    async def test_direct_mode_explicitly_selected(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"mode":"direct"}'), finish_reason="stop")]))
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(settings, "ai_backend", "direct"), \
                patch("app.ai.client.AsyncOpenAI", return_value=sdk), \
                patch.object(HarnessBackend, "generate", AsyncMock()) as harness:
            self.assertEqual(await LLMClient().chat_json("系统", "玩家"), {"mode": "direct"})
            self.assertEqual(create.call_args.kwargs["model"], "deepseek-v4-flash")
            self.assertEqual(create.call_args.kwargs["response_format"], {"type": "json_object"})
            harness.assert_not_called()

    async def test_direct_truncated_json_is_rejected(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"looks_complete":true}'), finish_reason="length")]))
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(settings, "ai_backend", "direct"), \
                patch("app.ai.client.AsyncOpenAI", return_value=sdk):
            with self.assertRaises(LLMError):
                await LLMClient().chat_json("system", "user", retries=0)

    async def test_missing_key_and_sdk_do_not_silently_fall_back(self):
        with patch.object(settings, "deepseek_api_key", ""), \
                patch.object(HarnessBackend, "generate", AsyncMock()) as generate:
            with self.assertRaisesRegex(LLMError, "DEEPSEEK_API_KEY") as missing:
                await LLMClient().chat_json("system", "user")
            self.assertEqual(missing.exception.code, "missing_api_key")
            generate.assert_not_called()
        with patch.object(HarnessBackend, "generate", AsyncMock(side_effect=HarnessError("SDK 缺失"))) as generate, \
                patch("app.ai.client.AsyncOpenAI") as direct:
            with self.assertRaisesRegex(LLMError, "SDK 缺失"):
                await LLMClient().chat_json("system", "user")
            self.assertEqual(generate.await_count, 1)
            direct.assert_not_called()

    async def test_invalid_json_retries_but_never_returns_empty_success(self):
        with patch.object(HarnessBackend, "generate", AsyncMock(side_effect=['{"cut":', '[1,2]', '{"ok":1}'])) as generate:
            self.assertEqual(await LLMClient().chat_json("system", "user"), {"ok": 1})
            self.assertEqual(generate.await_count, 3)
        with patch.object(HarnessBackend, "generate", AsyncMock(return_value="")):
            with self.assertRaises(LLMError):
                await LLMClient().chat_json("system", "user", retries=0)

    async def test_sessions_are_isolated_and_cleaned(self):
        seen = []
        class Runtime:
            def __init__(self, **kwargs):
                self.options = kwargs
                self.closed = False
                seen.append(self)
            def run(self, user, session_id):
                self.session_id = session_id
                self.request_patch = json.loads(Path(self.options["patches"][1]).read_text())
                self.workspace_exists = Path(self.options["cwd"]).is_dir()
                return SimpleNamespace(finish_reason="completed", final_response='{"ok":1}')
            def close(self):
                self.closed = True
        with patch("app.ai.harness_backend._load_harness", return_value=Runtime):
            await asyncio.gather(*[HarnessBackend().generate("system", "user", max_tokens=512, temperature=0.4)
                                   for _ in range(2)])
        self.assertNotEqual(seen[0].options["dsh_home"], seen[1].options["dsh_home"])
        self.assertNotEqual(seen[0].session_id, seen[1].session_id)
        for runtime in seen:
            self.assertTrue(runtime.closed and runtime.workspace_exists)
            self.assertEqual(runtime.options["profile"], "sdk-minimal")
            self.assertEqual(runtime.request_patch[0]["insert"][0]["config"], {"maxTokens": 512, "temperature": 0.4})
            self.assertIn("system", runtime.options["env"]["DSH_SYSTEM_PROMPT"])
            self.assertFalse(Path(runtime.options["cwd"]).exists())
        self.assertEqual(list(Path(self.root).iterdir()), [])

    async def test_failure_truncation_empty_and_timeout_close_runtime(self):
        seen = []
        class Runtime:
            outcome = None
            def __init__(self, **kwargs):
                self.closed = threading.Event()
                seen.append(self)
            def run(self, *args, **kwargs):
                if self.outcome == "timeout":
                    if not self.closed.wait(2):
                        raise AssertionError("timeout did not stop runtime")
                    raise RuntimeError("closed")
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return self.outcome
            def close(self):
                self.closed.set()
        for result in [RuntimeError("secret must not escape"),
                       SimpleNamespace(finish_reason="max-tokens", final_response='{"ok":1}'),
                       SimpleNamespace(finish_reason="completed", final_response=""), "timeout"]:
            Runtime.outcome = result
            with self.subTest(result=str(result)), \
                    patch("app.ai.harness_backend._load_harness", return_value=Runtime), \
                    patch.object(settings, "llm_timeout", 0.1):
                with self.assertRaises(HarnessError) as caught:
                    await HarnessBackend().generate("system", "user", max_tokens=1, temperature=0)
                self.assertNotIn("secret", str(caught.exception))
                if result == "timeout":
                    self.assertIn("超时", str(caught.exception))
                self.assertTrue(seen[-1].closed.is_set())
                self.assertEqual(list(Path(self.root).iterdir()), [])


@unittest.skipUnless(os.getenv("STORYWORLD_TEST_HARNESS_RUNTIME") == "1", "需要安装 requirements-harness.txt 的兼容环境")
class HarnessRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bundled_runtime_sends_tool_free_structured_deepseek_request(self):
        requests = []
        release = threading.Event()
        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                prompt = json.dumps(body["messages"])
                if "player-timeout" in prompt:
                    release.wait(15)
                    return
                finish = "length" if "player-truncated" in prompt else "stop"
                for delta, reason in [({"role": "assistant", "content": '{"story":"雨停了"}'}, None), ({}, finish)]:
                    chunk = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1,
                             "model": "deepseek-v4-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                for key, value in {"ai_backend": "harness", "deepseek_api_key": "local-test-key",
                                   "deepseek_base_url": f"http://127.0.0.1:{server.server_port}",
                                   "deepseek_model": "deepseek-v4-flash", "harness_work_dir": directory,
                                   "llm_timeout": 30}.items():
                    stack.enter_context(patch.object(settings, key, value))
                for player in ("player-one", "player-two"):
                    result = await LLMClient().chat_json("仅使用提供的故事状态", player, max_tokens=512, temperature=0.4, retries=0)
                    self.assertEqual(result, {"story": "雨停了"})
                with self.assertRaises(LLMError):
                    await LLMClient().chat_json("system", "player-truncated", retries=0)
                with patch.object(settings, "llm_timeout", 5):
                    with self.assertRaisesRegex(LLMError, "超时"):
                        await asyncio.wait_for(LLMClient().chat_json("system", "player-timeout", retries=0), timeout=12)
                self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(len(requests), 4)
            for index, (path, authorization, body) in enumerate(requests[:2]):
                self.assertTrue(path.endswith("/chat/completions"), path)
                self.assertEqual(authorization, "Bearer local-test-key")
                self.assertEqual(body["model"], "deepseek-v4-flash")
                self.assertEqual(body["response_format"], {"type": "json_object"})
                self.assertEqual(body["max_tokens"], 512)
                self.assertEqual(body["temperature"], 0.4)
                self.assertFalse(body.get("tools"))
                self.assertEqual(body["thinking"], {"type": "disabled"})
                messages = body["messages"]
                self.assertIn("仅使用提供的故事状态", json.dumps(messages, ensure_ascii=False))
                self.assertIn(("player-one", "player-two")[index], json.dumps(messages))
                self.assertNotIn(("player-two", "player-one")[index], json.dumps(messages))
        finally:
            release.set()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
