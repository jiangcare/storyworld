"""统一 AI 入口：默认经 DeepSeek Harness，显式 direct 模式兼容旧部署。"""
from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from ..config import settings
from .harness_backend import HarnessBackend, HarnessError
from .policy import GAME_BOUNDARY

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        self._client = None
        self._harness = HarnessBackend()

    async def chat_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        retries: int = 2,
    ) -> dict:
        """调用模型并要求返回 JSON 对象（response_format=json_object）。"""
        if not settings.deepseek_api_key:
            raise LLMError("尚未配置 DEEPSEEK_API_KEY")
        if settings.ai_backend == "direct" and self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.llm_timeout,
                max_retries=0,
            )
        system = GAME_BOUNDARY + "\n\n【本次游戏职责】\n" + system
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                options = {"temperature": temperature if temperature is not None else settings.llm_temperature,
                           "max_tokens": max_tokens if max_tokens is not None else settings.llm_max_tokens}
                if settings.ai_backend == "harness":
                    raw = await self._harness.generate(system, user, **options)
                else:
                    resp = await self._client.chat.completions.create(
                        model=settings.deepseek_model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        response_format={"type": "json_object"}, **options,
                    )
                    if resp.choices[0].finish_reason != "stop":
                        raise LLMError("模型未完整生成结果")
                    raw = resp.choices[0].message.content or ""
                data = self._extract_json(raw)
                if not isinstance(data, dict):
                    raise LLMError("模型返回非 JSON 对象")
                return data
            except HarnessError as exc:
                raise LLMError(str(exc)) from None
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("LLM 调用失败(第%s次): %s", attempt + 1, type(e).__name__)
        raise LLMError(f"LLM 调用最终失败（{type(last_err).__name__}）") from None

    @staticmethod
    def _extract_json(raw: str) -> object:
        raw = raw.strip()
        # 容错：剥离可能包裹的 ```json ... ``` 代码块
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 尝试截取第一个 { 到最后一个 }
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                return json.loads(raw[start : end + 1])
            raise


client = LLMClient()
