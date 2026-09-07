"""DeepSeek Harness SDK 适配。独立无工具会话，只返回模型文本。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import uuid

from ..config import settings

SDK_VERSION = "0.1.2rc1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = Path(__file__).resolve().parent / "harness"


class HarnessError(RuntimeError):
    pass


def _load_harness():
    if sys.version_info < (3, 10):
        raise HarnessError("DeepSeek Harness 需要 Python 3.10+；请在兼容环境安装 requirements-harness.txt")
    try:
        from importlib.metadata import version
        from deepseek_harness import DeepSeekHarness
    except ImportError as exc:
        raise HarnessError("尚未安装 DeepSeek Harness，请安装 requirements-harness.txt") from exc
    if version("deepseek-harness-sdk") != SDK_VERSION:
        raise HarnessError(f"请使用已验证的 deepseek-harness-sdk=={SDK_VERSION}")
    return DeepSeekHarness


class HarnessBackend:
    async def generate(self, system: str, user: str, *, max_tokens: int, temperature: float) -> str:
        factory = _load_harness()
        # SDK 是同步子进程 API；放在线程中，不阻塞网页/WebSocket 的事件循环。
        return await asyncio.to_thread(self._run, factory, system, user, max_tokens, temperature)

    @staticmethod
    def _run(factory, system, user, max_tokens, temperature):
        root = Path(settings.harness_work_dir).expanduser()
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        root.mkdir(parents=True, exist_ok=True)
        # 不共享玩家会话/环境，也不继承管理员在 ~/.dsh 里启用的插件。
        with tempfile.TemporaryDirectory(prefix="request-", dir=root) as directory:
            home = Path(directory) / "home"
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            patch = Path(directory) / "request.patch.yml"
            # JSON 是合法 YAML；路径/数字通过序列化传递，不拼接可执行表达式。
            patch.write_text(json.dumps([{"insert": [{
                "id": "storyworld-json-request",
                "name": str(PLUGIN_DIR / "json-request.mjs"),
                "config": {"temperature": temperature, "maxTokens": max_tokens},
            }]}]), encoding="utf-8")
            runtime = factory(
                provider="deepseek-official", model=settings.deepseek_model,
                reasoning_effort="off", max_tokens=max_tokens,
                profile="sdk-minimal", dsh_home=str(home), cwd=str(workspace),
                runtime_cwd=str(workspace),
                patches=(str(PLUGIN_DIR / "storyworld.patch.yml"), str(patch)),
                api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url,
                env={"DSH_SYSTEM_PROMPT": system + "\n只返回一个合法 JSON 对象，不使用 Markdown 代码围栏。"},
                initialize_timeout_seconds=min(settings.llm_timeout, 30),
                request_timeout_seconds=settings.llm_timeout,
                shutdown_timeout_seconds=2,
            )
            expired = threading.Event()

            def timeout():
                expired.set()
                runtime.close()

            # SDK 的通知等待是逐次计时，这个截止时间限制整个调用（含持续流输出）。
            timer = threading.Timer(settings.llm_timeout, timeout)
            timer.daemon = True
            timer.start()
            try:
                result = runtime.run(user, session_id="storyworld-" + uuid.uuid4().hex)
                if expired.is_set():
                    raise HarnessError("Harness 请求超时")
                if result.finish_reason != "completed":
                    raise HarnessError("Harness 未完整生成结果")
                if not isinstance(result.final_response, str) or not result.final_response.strip():
                    raise HarnessError("Harness 返回空结果")
                return result.final_response
            except HarnessError:
                raise
            except Exception as exc:
                # SDK 诊断可能包含请求/环境；不向玩家或常规日志输出原始诊断。
                raise HarnessError("Harness 请求超时" if expired.is_set() else
                                   f"Harness 运行失败（{type(exc).__name__}）") from None
            finally:
                timer.cancel()
                timer.join()
                runtime.close()
