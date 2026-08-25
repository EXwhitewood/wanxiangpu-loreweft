import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.config import settings
from app.models.api_config import APIFormat
from app.services.alert_service import get_alert_service
from app.services.llm_task_profiles import LLMTaskType, get_profile


logger = logging.getLogger(__name__)


# 可重试的异常类型（网络抖动、超时、限流）
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


# 方案 28 + R4-6: LLM 调用结构化结果，携带 cache metrics
@dataclass
class LLMResult:
    """LLM 调用结构化结果。

    兼容策略：generate() 仍返回 str，内部委托 generate_structured() 取 .content；
    新的缓存指标消费者可直接调用 generate_structured() 获取完整 LLMResult。
    """

    content: str | None
    reasoning_content: str | None = None
    tool_calls: list | None = None
    finish_reason: str | None = None
    error_type: str | None = None
    # cache metrics（方案 28）
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_hit_rate: float = 0.0
    stable_prefix_hash: str = ""
    dynamic_suffix_hash: str = ""
    # debug info（兼容现有 debug_info 字典）
    debug_info: dict | None = None


# 方案 28 Phase B: Prompt 缓存策略
@dataclass
class PromptCachePolicy:
    """Prompt 缓存策略。

    - Anthropic: 使用 cache_control breakpoint 注入 stable_prefix_blocks
    - OpenAI: 自动缓存，只需保证前缀稳定，无需特殊处理
    """

    enabled: bool = True
    provider: str = "openai_compatible"  # openai_compatible | anthropic
    stable_prefix_blocks: list[str] = field(default_factory=list)
    dynamic_blocks: list[str] = field(default_factory=list)
    cache_hint_strategy: str = "provider_default"  # provider_default | explicit_breakpoint


def build_prompt_cache_policy(system_prompt: str) -> tuple[PromptCachePolicy, str]:
    """费用优化：为任意 agent 构建 prompt cache policy。

    将 system_prompt 作为稳定前缀，计算 hash 供本地缓存推导。
    返回 (cache_policy, stable_prefix_hash)。
    """
    import hashlib as _hashlib
    stable_hash = _hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    policy = PromptCachePolicy(
        enabled=True,
        provider="openai_compatible",
        stable_prefix_blocks=[system_prompt],
        dynamic_blocks=[],
        cache_hint_strategy="provider_default",
    )
    return policy, stable_hash


class LLMClient:
    def __init__(
        self,
        api_format: APIFormat,
        api_key: str,
        base_url: str,
        model: str,
    ):
        self.api_format = api_format
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        # P0-11: 共享 httpx.AsyncClient（连接池复用，减少 TCP/TLS 握手开销）
        self._shared_http: httpx.AsyncClient | None = None
        self._http_lock = asyncio.Lock()
        # 方案 28 A5: 本地 prefix 复用计数（provider 不支持 cache metrics 时推导 cache_hit_rate）
        self._prefix_reuse_counts: dict[str, int] = {}

    @staticmethod
    def _http_client(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            trust_env=settings.llm_trust_env_proxy,
        )

    async def _get_http_client(self, timeout: float = 120.0) -> httpx.AsyncClient:
        """P0-11: 获取共享 httpx.AsyncClient（异步双检锁）。

        首次调用时创建共享 client，后续复用同一实例。
        timeout 仅在创建时生效；如需不同 timeout，调用方应在请求级别覆盖
        （httpx 支持 per-request timeout）。
        """
        if self._shared_http is None:
            async with self._http_lock:
                if self._shared_http is None:
                    self._shared_http = httpx.AsyncClient(
                        timeout=timeout,
                        trust_env=settings.llm_trust_env_proxy,
                    )
        return self._shared_http

    async def aclose(self) -> None:
        """P0-11: 关闭共享 httpx.AsyncClient。应用关闭时调用以释放连接池。"""
        async with self._http_lock:
            if self._shared_http is not None:
                await self._shared_http.aclose()
                self._shared_http = None

    @staticmethod
    def _backoff(
        attempt: int,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> float:
        """指数退避 + jitter，避免雪崩。429 限流时更长退避。

        attempt 从 0 开始，返回秒数。
        retry_after：若服务端通过 Retry-After header 指定等待时间，优先使用。
        """
        if retry_after is not None and retry_after > 0:
            # 服务端明确指定 Retry-After，遵循之（加上少量 jitter）
            return float(retry_after) + random.uniform(0, 0.5)
        if status_code == 429:
            # 限流：更长退避（30s 起）
            base = 30.0 * (2 ** attempt)
        else:
            # 普通错误：1, 2, 4, 8
            base = float(2 ** attempt)
        jitter = random.uniform(0, 0.5)
        return base + jitter

    @staticmethod
    def _extract_retry_after(response: httpx.Response | None) -> float | None:
        """从 HTTP 响应中提取 Retry-After header（秒）。

        Retry-After 可能是秒数（整数）或 HTTP-date，分别尝试解析。
        解析失败或缺失时返回 None。
        """
        if response is None:
            return None
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if not raw:
            return None
        # 优先尝试解析为秒数
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
        # 尝试解析为 HTTP-date
        try:
            from email.utils import parsedate_to_datetime
            import datetime as _dt

            target_dt = parsedate_to_datetime(raw)
            date_raw = headers.get("Date")
            now_dt = parsedate_to_datetime(date_raw) if date_raw else _dt.datetime.now(_dt.timezone.utc)
            if target_dt is None or now_dt is None:
                return None
            delta = (target_dt - now_dt).total_seconds()
            return max(0.0, float(delta))
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _as_dict(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: LLMClient._as_dict(v) for k, v in value.items()}
        if isinstance(value, list):
            return [LLMClient._as_dict(v) for v in value]
        if hasattr(value, "model_dump"):
            return LLMClient._as_dict(value.model_dump())
        if hasattr(value, "dict"):
            return LLMClient._as_dict(value.dict())
        return value

    @staticmethod
    def _decode_tool_arguments(arguments: Any) -> dict:
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            decoded = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            logger.warning("[LLMClient] Invalid tool arguments JSON, using empty object")
            return {}
        if isinstance(decoded, dict):
            return decoded
        logger.warning("[LLMClient] Tool arguments JSON is not an object, using empty object")
        return {}

    # -------------------------------------------------- 方案 28: cache metrics 辅助

    # 已知支持原生 prompt cache 的 provider/model 前缀（粗粒度判断，未命中走本地推导）
    # 注意：以下列表仅用于"已知支持"快速判断，并非排他性白名单。
    # _parse_openai_cache_metrics 会自动探测所有已知缓存字段（OpenAI/DeepSeek/Anthropic 等），
    # 任何 provider 返回了已知的缓存字段都能被识别，无需在此列表中逐一添加。
    _CACHE_CAPABLE_MODEL_PREFIXES: tuple[str, ...] = (
        "gpt-4o", "gpt-4.1", "gpt-4.5", "o1", "o3", "o4",
        "claude-3", "claude-sonnet", "claude-opus", "claude-haiku",
        "deepseek",
    )

    def _provider_supports_cache(self, model: str) -> bool:
        """B3: 粗粒度判断 provider/model 是否支持原生 prompt cache。

        通用策略：默认乐观（返回 True），让所有 provider 都尝试读取真实缓存指标。
        _parse_openai_cache_metrics / _parse_anthropic_cache_metrics 会自动探测
        所有已知缓存字段，有值就用真实指标，没有才回退本地推导。
        这样任何新模型/provider 只要返回了已知格式的缓存字段，都能自动受益，
        无需每次添加新模型时都修改 _CACHE_CAPABLE_MODEL_PREFIXES。
        """
        return True

    @staticmethod
    def _parse_openai_cache_metrics(usage: dict) -> tuple[int, int, int, float]:
        """A3: 解析 OpenAI 兼容 API usage 中的 cache metrics（通用自动探测）。

        返回 (cache_read_tokens, cache_creation_tokens, cached_prompt_tokens, cache_hit_rate)。
        自动探测所有已知缓存字段，有哪个用哪个：
        - DeepSeek: usage.prompt_cache_hit_tokens
        - OpenAI: usage.prompt_tokens_details.cached_tokens
        - 通用 fallback: usage.cached_tokens / usage.cache_read_tokens
        """
        if not usage:
            return 0, 0, 0, 0.0
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        # 自动探测所有已知缓存字段
        cached_tokens = 0
        for field, extractor in (
            ("prompt_cache_hit_tokens", lambda u: u.get("prompt_cache_hit_tokens", 0)),
            ("cached_tokens", lambda u: (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
            ("cache_read_tokens", lambda u: u.get("cache_read_tokens", 0)),
        ):
            val = extractor(usage) or 0
            if val:
                cached_tokens = val
                break
        hit_rate = (cached_tokens / prompt_tokens) if prompt_tokens else 0.0
        return cached_tokens, 0, cached_tokens, hit_rate

    @staticmethod
    def _parse_anthropic_cache_metrics(usage: dict) -> tuple[int, int, int, float]:
        """A3: 解析 Anthropic usage 中的 cache metrics。

        Anthropic: usage.cache_read_input_tokens / cache_creation_input_tokens
        """
        if not usage:
            return 0, 0, 0, 0.0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
        input_tokens = usage.get("input_tokens", 0) or 0
        hit_rate = (cache_read / input_tokens) if input_tokens else 0.0
        return cache_read, cache_creation, cache_read, hit_rate

    def _derive_local_cache_metrics(self, stable_prefix_hash: str) -> tuple[int, float]:
        """A5: provider 不支持 cache metrics 时，使用本地缓存推导。

        基于 stable_prefix_hash 的复用次数推导 cache_hit_rate。
        第 1 次访问视为 cache miss（0.0），第 2 次起视为命中（1.0）。
        返回 (cached_prompt_tokens, cache_hit_rate)。
        """
        if not stable_prefix_hash:
            return 0, 0.0
        reuse_count = self._prefix_reuse_counts.get(stable_prefix_hash, 0) + 1
        self._prefix_reuse_counts[stable_prefix_hash] = reuse_count
        if reuse_count <= 1:
            return 0, 0.0
        return 1, 1.0

    @staticmethod
    def _build_anthropic_system_with_cache(
        system_prompt: str,
        cache_policy: PromptCachePolicy | None,
    ) -> str | list[dict]:
        """B2: 构造 Anthropic system 参数，注入 cache_control breakpoint。

        无 cache_policy 时返回 str（向后兼容）。
        有 cache_policy 时返回 content blocks，在 stable_prefix 末尾打 breakpoint。
        """
        if cache_policy is None or not cache_policy.enabled:
            return system_prompt

        stable_blocks = cache_policy.stable_prefix_blocks
        if stable_blocks:
            blocks: list[dict] = []
            for idx, block_text in enumerate(stable_blocks):
                block = {"type": "text", "text": block_text}
                if idx == len(stable_blocks) - 1:
                    block["cache_control"] = {"type": "ephemeral"}
                blocks.append(block)
            return blocks
        # 未显式提供 blocks，把整个 system_prompt 作为 stable prefix 打 breakpoint
        return [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]

    def _openai_base_url(self) -> str:
        if self.base_url.endswith("/v1"):
            return self.base_url
        return f"{self.base_url}/v1"

    def _anthropic_base_url(self) -> str:
        if self.base_url.endswith("/v1"):
            return self.base_url[:-3].rstrip("/")
        return self.base_url

    def _openai_client(self, timeout: float) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self._openai_base_url(),
            http_client=self._http_client(timeout),
            max_retries=0,
        )

    def _anthropic_client(self, timeout: float) -> AsyncAnthropic:
        return AsyncAnthropic(
            api_key=self.api_key,
            base_url=self._anthropic_base_url(),
            http_client=self._http_client(timeout),
        )

    def _convert_to_anthropic_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        api_messages: list[dict] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                if content:
                    system_parts.append(content)
                continue

            if role == "tool":
                tool_content = content or "(empty result)"
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": tool_content,
                        }
                    ],
                })
                continue

            if role == "assistant" and msg.get("tool_calls"):
                parts: list[dict] = []
                if content:
                    parts.append({"type": "text", "text": content})
                for tool_call in msg["tool_calls"]:
                    fn = tool_call.get("function", {})
                    parts.append({
                        "type": "tool_use",
                        "id": tool_call.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": self._decode_tool_arguments(fn.get("arguments", "{}")),
                    })
                api_messages.append({"role": "assistant", "content": parts})
                continue

            if content:
                api_messages.append({
                    "role": "assistant" if role == "assistant" else "user",
                    "content": content,
                })

        return "\n\n".join(system_parts), api_messages

    @staticmethod
    def _convert_to_anthropic_tools(tools: list[dict]) -> list[dict]:
        anthropic_tools = []
        for tool in tools:
            fn = tool.get("function", {})
            anthropic_tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return anthropic_tools

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
        task_type: LLMTaskType | None = None,
        cache_policy: PromptCachePolicy | None = None,
        stable_prefix_hash: str = "",
    ) -> dict:
        """工具调用路径。支持 task_type 预设 + 自动重试退避。

        显式 max_tokens/timeout 覆盖 task_type 预设；
        未传 task_type 且显式参数为 None 时回退到旧默认值（向后兼容）。

        费用优化：cache_policy + stable_prefix_hash 用于 cache metrics 日志。
        """
        # P0-13: 接入 LLMetricsRegistry
        from app.services.llm_metrics import get_llm_metrics
        metrics = get_llm_metrics()
        api_fmt = str(self.api_format)
        task_type_str = str(task_type) if task_type is not None else ""

        max_retries = 2
        if task_type is not None:
            profile = get_profile(task_type)
            max_tokens = max_tokens if max_tokens is not None else profile.max_tokens
            timeout = timeout if timeout is not None else profile.timeout
            max_retries = profile.max_retries
        else:
            max_tokens = max_tokens if max_tokens is not None else 4096
            timeout = timeout if timeout is not None else 120.0

        metrics.inc_active(api_format=api_fmt, model=self.model, task_type=task_type_str)
        try:
            for attempt in range(max_retries + 1):
                attempt_start = time.monotonic()
                try:
                    if self.api_format == "openai_compatible":
                        result = await self._call_openai_with_tools(
                            messages, tools, temperature, max_tokens, timeout,
                            cache_policy=cache_policy, stable_prefix_hash=stable_prefix_hash,
                        )
                    else:
                        result = await self._call_anthropic_with_tools(
                            messages, tools, temperature, max_tokens, timeout,
                            cache_policy=cache_policy, stable_prefix_hash=stable_prefix_hash,
                        )
                    metrics.record_call(
                        api_format=api_fmt,
                        model=self.model,
                        task_type=task_type_str,
                        status="success",
                        duration_s=time.monotonic() - attempt_start,
                        # 修复（循环 #16 根因）：generate_with_tools 的 result 是 dict
                        #   （由 _call_openai_with_tools / _call_anthropic_with_tools 返回），
                        #   不是 LLMResult 对象，没有 .debug_info 属性。
                        #   原代码 result.debug_info 导致 AttributeError，使所有 tool-calling
                        #   LLM 请求静默失败（蓝图官、editor_in_chief、outline_architect 等全部受影响）。
                        #   修复：在 _call_*_with_tools 的返回 dict 中添加 "usage" 字段，这里用 .get()。
                        usage=result.get("usage") if isinstance(result, dict) else (result.debug_info.get("usage") if result and result.debug_info else None),
                    )
                    return result
                except (asyncio.TimeoutError, httpx.ConnectError, httpx.ReadError) as e:
                    metrics.record_call(
                        api_format=api_fmt,
                        model=self.model,
                        task_type=task_type_str,
                        status="timeout" if isinstance(e, asyncio.TimeoutError) else "error",
                        duration_s=time.monotonic() - attempt_start,
                    )
                    if attempt < max_retries:
                        wait = self._backoff(attempt)
                        metrics.record_retry(
                            api_format=api_fmt,
                            model=self.model,
                            reason="timeout" if isinstance(e, asyncio.TimeoutError) else "network_error",
                        )
                        logger.warning(
                            "[LLMClient] generate_with_tools retryable error "
                            f"(attempt {attempt + 1}/{max_retries + 1}), retry in {wait:.1f}s: {e}"
                        )
                        await asyncio.sleep(wait)
                        continue
                    get_alert_service().alert(
                        "critical", "llm_retry_exhausted",
                        f"generate_with_tools {type(e).__name__}: {e} "
                        f"(model={self.model}, attempts={max_retries + 1})"
                    )
                    raise
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code
                    metrics.record_call(
                        api_format=api_fmt,
                        model=self.model,
                        task_type=task_type_str,
                        status="rate_limited" if status_code == 429 else "error",
                        duration_s=time.monotonic() - attempt_start,
                    )
                    if status_code in _RETRYABLE_HTTP_STATUS and attempt < max_retries:
                        retry_after = self._extract_retry_after(e.response)
                        wait = self._backoff(
                            attempt, status_code=status_code, retry_after=retry_after,
                        )
                        metrics.record_retry(
                            api_format=api_fmt,
                            model=self.model,
                            reason=f"http_{status_code}",
                        )
                        logger.warning(
                            f"[LLMClient] generate_with_tools HTTP {status_code} "
                            f"(attempt {attempt + 1}/{max_retries + 1}), retry in {wait:.1f}s"
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise
        finally:
            metrics.dec_active(api_format=api_fmt, model=self.model, task_type=task_type_str)

    async def _call_openai_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float,
        max_tokens: int,
        timeout: float,
        *,
        cache_policy: PromptCachePolicy | None = None,
        stable_prefix_hash: str = "",
    ) -> dict:
        sanitized_messages = self._sanitize_openai_messages(messages)
        logger.info(
            "[LLMClient] generate_with_tools: model=%s, messages_count=%s, roles=%s, tools_count=%s",
            self.model,
            len(sanitized_messages),
            [m.get("role") for m in sanitized_messages],
            len(tools),
        )

        async with self._openai_client(timeout) as client:
            response = await client.chat.completions.create(
                model=self.model,
                messages=sanitized_messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        data = self._as_dict(response)
        # 费用优化可观测性：记录 cache metrics（generate_with_tools 路径）
        if cache_policy is not None and cache_policy.enabled:
            usage = data.get("usage") or {}
            cached_prompt, _, _, hit_rate = self._parse_openai_cache_metrics(usage)
            prompt_tokens = usage.get("prompt_tokens", 0) or 0
            logger.info(
                "[LLMClient] cache_policy active (tools) | model=%s | prompt_tokens=%d | cached=%d | hit_rate=%.2f%% | stable_hash=%s",
                self.model, prompt_tokens, cached_prompt, hit_rate * 100, stable_prefix_hash[:12] or "none",
            )
        choice = data["choices"][0]
        message = choice["message"]
        tool_calls = message.get("tool_calls", [])
        # T11.2: 读取 finish_reason 透传给调用方（区分 length/content_filter/stop）
        finish_reason = choice.get("finish_reason", "") or ""

        if tool_calls:
            parsed_calls = []
            for tc in tool_calls:
                parsed_calls.append({
                    "id": tc.get("id", ""),
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", "{}"),
                    },
                })
            return {
                "has_tool_calls": True,
                "tool_calls": parsed_calls,
                "assistant_message": {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": tc.get("function", {}).get("arguments", "{}"),
                            },
                        }
                        for tc in tool_calls
                    ],
                    **({"reasoning_content": message["reasoning_content"]} if message.get("reasoning_content") else {}),
                },
                "finish_reason": finish_reason,
                # 修复（循环 #16）：添加 usage 供 generate_with_tools 的 metrics.record_call 使用
                "usage": data.get("usage"),
            }

        return {
            "has_tool_calls": False,
            "content": message.get("content") or "",
            "reasoning_content": message.get("reasoning_content") or "",
            "finish_reason": finish_reason,
            # 修复（循环 #16）：添加 usage 供 generate_with_tools 的 metrics.record_call 使用
            "usage": data.get("usage"),
        }

    def _sanitize_openai_messages(self, messages: list[dict]) -> list[dict]:
        result = []
        for msg in messages:
            role = msg.get("role", "")

            if role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                content = msg.get("content", "")
                if not content:
                    content = "(empty result)"
                result.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content,
                })
                continue

            if role == "assistant":
                new_msg = {"role": "assistant"}
                content = msg.get("content")
                tool_calls = msg.get("tool_calls")
                reasoning_content = msg.get("reasoning_content")

                if tool_calls:
                    new_msg["tool_calls"] = tool_calls
                    if content:
                        new_msg["content"] = content
                else:
                    new_msg["content"] = content if content else ""

                if reasoning_content:
                    new_msg["reasoning_content"] = reasoning_content

                result.append(new_msg)
                continue

            if role == "system":
                content = msg.get("content", "")
                if content:
                    result.append({"role": "system", "content": content})
                continue

            if role == "user":
                content = msg.get("content", "")
                if content:
                    result.append({"role": "user", "content": content})
                continue

            content = msg.get("content", "")
            if content:
                result.append({"role": "user", "content": content})

        has_user = any(m.get("role") == "user" for m in result)
        if not has_user:
            last_system_idx = -1
            for i, m in enumerate(result):
                if m.get("role") == "system":
                    last_system_idx = i
            placeholder = {"role": "user", "content": "请继续。"}
            if last_system_idx >= 0:
                result.insert(last_system_idx + 1, placeholder)
            else:
                result.append(placeholder)

        return result

    async def _call_anthropic_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float,
        max_tokens: int,
        timeout: float,
        *,
        cache_policy: PromptCachePolicy | None = None,
        stable_prefix_hash: str = "",
    ) -> dict:
        system_content, api_messages = self._convert_to_anthropic_messages(messages)
        anthropic_tools = self._convert_to_anthropic_tools(tools)
        # 费用优化：注入 cache_control breakpoint
        if cache_policy is not None and cache_policy.enabled and isinstance(system_content, str):
            system_content = self._build_anthropic_system_with_cache(system_content, cache_policy)

        async with self._anthropic_client(timeout) as client:
            response = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_content,
                messages=api_messages,
                tools=anthropic_tools,
                temperature=temperature,
            )

        data = self._as_dict(response)
        # 费用优化可观测性：记录 cache metrics（generate_with_tools Anthropic 路径）
        if cache_policy is not None and cache_policy.enabled:
            usage = data.get("usage") or {}
            cache_read, _, _, hit_rate = self._parse_anthropic_cache_metrics(usage)
            input_tokens = usage.get("input_tokens", 0) or 0
            logger.info(
                "[LLMClient] cache_policy active (tools/anthropic) | model=%s | input_tokens=%d | cache_read=%d | hit_rate=%.2f%% | stable_hash=%s",
                self.model, input_tokens, cache_read, hit_rate * 100, stable_prefix_hash[:12] or "none",
            )
        content_blocks = data.get("content", [])
        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
        text_blocks = [b for b in content_blocks if b.get("type") == "text"]

        if tool_use_blocks:
            parsed_calls = []
            for tb in tool_use_blocks:
                parsed_calls.append({
                    "id": tb.get("id", ""),
                    "function": {
                        "name": tb.get("name", ""),
                        "arguments": json.dumps(tb.get("input", {}), ensure_ascii=False),
                    },
                })
            return {
                "has_tool_calls": True,
                "tool_calls": parsed_calls,
                "assistant_message": {
                    "role": "assistant",
                    "content": "".join(b.get("text", "") for b in text_blocks) or "",
                    "tool_calls": [
                        {
                            "id": tb.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tb.get("name", ""),
                                "arguments": json.dumps(tb.get("input", {}), ensure_ascii=False),
                            },
                        }
                        for tb in tool_use_blocks
                    ],
                },
                # 修复（循环 #16）：添加 usage 供 generate_with_tools 的 metrics.record_call 使用
                "usage": data.get("usage"),
            }

        text = "".join(b.get("text", "") for b in text_blocks)
        return {
            "has_tool_calls": False,
            "content": text,
            # 修复（循环 #16）：添加 usage 供 generate_with_tools 的 metrics.record_call 使用
            "usage": data.get("usage"),
        }

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
        response_format: dict | None = None,
        debug_info: dict | None = None,
        task_type: LLMTaskType | None = None,
        stable_prefix_hash: str | None = None,
        cache_policy: "PromptCachePolicy | None" = None,
    ) -> str:
        """非工具调用路径。保持返回 str（向后兼容）。

        显式 max_tokens/timeout 覆盖 task_type 预设；
        未传 task_type 且显式参数为 None 时回退到旧默认值（向后兼容）。

        方案 28 + R4-6: 内部委托 generate_structured()，返回 .content。
        新的缓存指标消费者请直接调用 generate_structured() 获取 LLMResult。
        缓存优化：stable_prefix_hash / cache_policy 透传给 generate_structured()，
        让所有调用 generate() 的 agents 都能受益于 prompt 缓存。
        """
        result = await self.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
            debug_info=debug_info,
            task_type=task_type,
            stable_prefix_hash=stable_prefix_hash,
            cache_policy=cache_policy,
        )
        return result.content or ""

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
        response_format: dict | None = None,
        debug_info: dict | None = None,
        task_type: LLMTaskType | None = None,
        stable_prefix_hash: str = "",
        dynamic_suffix_hash: str = "",
        cache_policy: PromptCachePolicy | None = None,
    ) -> LLMResult:
        """方案 28 + R4-6: 结构化 LLM 调用，返回 LLMResult（含 cache metrics）。

        支持 task_type 预设 + 自动重试退避。
        显式 max_tokens/timeout 覆盖 task_type 预设；
        未传 task_type 且显式参数为 None 时回退到旧默认值（向后兼容）。

        - stable_prefix_hash: 调用方（如 UnifiedContextBuilder 消费者）传入的稳定前缀 hash
        - cache_policy: Phase B 缓存策略（Anthropic 注入 cache_control breakpoint）
        """
        # P0-13: 接入 LLMetricsRegistry
        from app.services.llm_metrics import get_llm_metrics
        metrics = get_llm_metrics()
        api_fmt = str(self.api_format)
        task_type_str = str(task_type) if task_type is not None else ""

        max_retries = 2
        if task_type is not None:
            profile = get_profile(task_type)
            max_tokens = max_tokens if max_tokens is not None else profile.max_tokens
            timeout = timeout if timeout is not None else profile.timeout
            max_retries = profile.max_retries
        else:
            max_tokens = max_tokens if max_tokens is not None else 4096
            timeout = timeout if timeout is not None else 120.0

        metrics.inc_active(api_format=api_fmt, model=self.model, task_type=task_type_str)
        try:
            for attempt in range(max_retries + 1):
                attempt_start = time.monotonic()
                try:
                    if self.api_format == "openai_compatible":
                        result = await self._call_openai_compatible(
                            system_prompt,
                            user_prompt,
                            temperature,
                            max_tokens,
                            stream=False,
                            timeout=timeout,
                            response_format=response_format,
                            debug_info=debug_info,
                            stable_prefix_hash=stable_prefix_hash,
                            cache_policy=cache_policy,
                        )
                    else:
                        result = await self._call_anthropic_compatible(
                            system_prompt,
                            user_prompt,
                            temperature,
                            max_tokens,
                            stream=False,
                            timeout=timeout,
                            debug_info=debug_info,
                            stable_prefix_hash=stable_prefix_hash,
                            cache_policy=cache_policy,
                        )
                    metrics.record_call(
                        api_format=api_fmt,
                        model=self.model,
                        task_type=task_type_str,
                        status="success",
                        duration_s=time.monotonic() - attempt_start,
                        usage=result.debug_info.get("usage") if result and result.debug_info else None,
                    )
                    # A4: 补充 dynamic_suffix_hash（call 方法不关心该字段）
                    if dynamic_suffix_hash and not result.dynamic_suffix_hash:
                        result.dynamic_suffix_hash = dynamic_suffix_hash
                    return result
                except (asyncio.TimeoutError, httpx.ConnectError, httpx.ReadError) as e:
                    metrics.record_call(
                        api_format=api_fmt,
                        model=self.model,
                        task_type=task_type_str,
                        status="timeout" if isinstance(e, asyncio.TimeoutError) else "error",
                        duration_s=time.monotonic() - attempt_start,
                    )
                    if attempt < max_retries:
                        wait = self._backoff(attempt)
                        metrics.record_retry(
                            api_format=api_fmt,
                            model=self.model,
                            reason="timeout" if isinstance(e, asyncio.TimeoutError) else "network_error",
                        )
                        logger.warning(
                            "[LLMClient] generate_structured retryable error "
                            f"(attempt {attempt + 1}/{max_retries + 1}), retry in {wait:.1f}s: {e}"
                        )
                        await asyncio.sleep(wait)
                        continue
                    get_alert_service().alert(
                        "critical", "llm_retry_exhausted",
                        f"generate_structured {type(e).__name__}: {e} "
                        f"(model={self.model}, attempts={max_retries + 1})"
                    )
                    raise
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code
                    metrics.record_call(
                        api_format=api_fmt,
                        model=self.model,
                        task_type=task_type_str,
                        status="rate_limited" if status_code == 429 else "error",
                        duration_s=time.monotonic() - attempt_start,
                    )
                    if status_code in _RETRYABLE_HTTP_STATUS and attempt < max_retries:
                        retry_after = self._extract_retry_after(e.response)
                        wait = self._backoff(
                            attempt, status_code=status_code, retry_after=retry_after,
                        )
                        metrics.record_retry(
                            api_format=api_fmt,
                            model=self.model,
                            reason=f"http_{status_code}",
                        )
                        logger.warning(
                            f"[LLMClient] generate_structured HTTP {status_code} "
                            f"(attempt {attempt + 1}/{max_retries + 1}), retry in {wait:.1f}s"
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise
        finally:
            metrics.dec_active(api_format=api_fmt, model=self.model, task_type=task_type_str)

    async def generate_stream_with_messages(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float | None = None,
    ) -> AsyncGenerator[str, None]:
        if self.api_format == "openai_compatible":
            factory = lambda: self._stream_openai_messages(
                messages, temperature, max_tokens, timeout
            )
        else:
            factory = lambda: self._stream_anthropic_messages(
                messages, temperature, max_tokens, timeout
            )
        async for chunk in self._stream_with_connection_retry(factory):
            yield chunk

    async def _stream_with_connection_retry(
        self,
        stream_factory: Any,
        *,
        max_retries: int = 2,
    ) -> AsyncGenerator[str, None]:
        """流式输出的连接阶段重试包装（P2-26）。

        Phase 1（可重试）：建立连接 + 获取首个 chunk。
        重试条件：连接错误 / 5xx / 429，最多 max_retries 次。
        Phase 2（不重试）：首个 chunk 已消费后，流式输出剩余内容，错误直接抛出。

        stream_factory 是无参 callable，每次调用返回一个新的 async generator。
        重试时调用 stream_factory() 创建全新的流，避免复用已损坏的迭代器。
        """
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            stream = stream_factory()
            try:
                first_chunk = await stream.__anext__()
            except StopAsyncIteration:
                # 空流，视为成功完成
                return
            except (asyncio.TimeoutError, httpx.ConnectError, httpx.ReadError) as e:
                last_error = e
                if attempt < max_retries:
                    wait = self._backoff(attempt)
                    logger.warning(
                        "[LLMClient] stream connection retryable error "
                        f"(attempt {attempt + 1}/{max_retries + 1}), "
                        f"retry in {wait:.1f}s: {e}"
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
            except httpx.HTTPStatusError as e:
                last_error = e
                if (
                    e.response.status_code in _RETRYABLE_HTTP_STATUS
                    and attempt < max_retries
                ):
                    retry_after = self._extract_retry_after(e.response)
                    wait = self._backoff(
                        attempt,
                        status_code=e.response.status_code,
                        retry_after=retry_after,
                    )
                    logger.warning(
                        f"[LLMClient] stream connection HTTP {e.response.status_code} "
                        f"(attempt {attempt + 1}/{max_retries + 1}), "
                        f"retry in {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
            # Phase 1 成功，进入 Phase 2（不重试）
            yield first_chunk
            async for chunk in stream:
                yield chunk
            return
        if last_error:
            raise last_error

    async def _stream_openai_messages(
        self, messages: list[dict], temperature: float, max_tokens: int,
        timeout: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """T11: 流式输出。对推理模型同时 yield reasoning_content（独立流，不混入 content）。

        chunk 类型：
        - str: content 文本（向后兼容旧调用方）
        - dict: {"type": "reasoning", "text": ...} 推理内容
        """
        async with self._openai_client(timeout or 120.0) as client:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                data = self._as_dict(chunk)
                try:
                    delta = data["choices"][0].get("delta", {})
                except (KeyError, IndexError):
                    continue
                # T11.1: reasoning_content 独立 yield（不混入 content）
                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                content = delta.get("content", "")
                if content:
                    yield content

    async def _stream_anthropic_messages(
        self, messages: list[dict], temperature: float, max_tokens: int,
        timeout: float | None = None,
    ) -> AsyncGenerator[str, None]:
        system_content, api_messages = self._convert_to_anthropic_messages(messages)
        async with self._anthropic_client(timeout or 120.0) as client:
            stream = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_content,
                messages=api_messages,
                temperature=temperature,
                stream=True,
            )
            async for event in stream:
                data = self._as_dict(event)
                if data.get("type") != "content_block_delta":
                    continue
                delta = data.get("delta", {})
                text = delta.get("text", "")
                if text:
                    yield text

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if self.api_format == "openai_compatible":
            factory = lambda: self._stream_openai_messages(
                messages, temperature, max_tokens
            )
        else:
            factory = lambda: self._stream_anthropic_messages(
                messages, temperature, max_tokens
            )
        async for chunk in self._stream_with_connection_retry(factory):
            yield chunk

    async def _call_openai_compatible(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        timeout: float = 120.0,
        response_format: dict | None = None,
        debug_info: dict | None = None,
        stable_prefix_hash: str = "",
        cache_policy: PromptCachePolicy | None = None,
    ) -> LLMResult | AsyncGenerator[str, None]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if stream:
            return self._stream_openai_messages(messages, temperature, max_tokens, timeout)

        # B3: 不支持 cache 的 provider 自动降级（OpenAI 走自动缓存，cache_policy 无需特殊注入）
        if cache_policy is not None and not self._provider_supports_cache(self.model):
            logger.debug(
                "[LLMClient] provider %s does not support cache, skipping cache_policy",
                self.model,
            )
            cache_policy = None

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        # OpenAI 自动缓存，保证前缀稳定即可，无需 cache_control 注入

        async with self._openai_client(timeout) as client:
            response = await client.chat.completions.create(**kwargs)

        data = self._as_dict(response)
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content")
        reasoning_content = message.get("reasoning_content") or ""
        finish_reason = choice.get("finish_reason", "") or ""

        # 方案 29：推理模型用完 max_tokens 时 content 可能为 None
        if content is None:
            if finish_reason == "length":
                logger.warning(
                    "[LLMClient] _call_openai_compatible returned None content "
                    f"with finish_reason=length, max_tokens={max_tokens} "
                    "may be insufficient for reasoning model"
                )
                error_type = "max_tokens_exhausted"
            else:
                logger.warning(
                    "[LLMClient] _call_openai_compatible returned None content "
                    f"with finish_reason={finish_reason}"
                )
                error_type = "empty_content"
            content = ""
        else:
            error_type = ""

        # 方案 28 A3: 解析 OpenAI cache metrics
        usage = data.get("usage") or {}
        cache_read, cache_creation, cached_prompt, hit_rate = self._parse_openai_cache_metrics(usage)
        # A5: provider 不支持 cache metrics 时本地推导
        if not self._provider_supports_cache(self.model) and stable_prefix_hash:
            local_cached, local_rate = self._derive_local_cache_metrics(stable_prefix_hash)
            cached_prompt = local_cached
            hit_rate = local_rate

        result_debug_info = {
            "api_format": self.api_format,
            "model": self.model,
            "usage": usage,
            "response_chars": len(content or ""),
            "reasoning_content": reasoning_content,
            "finish_reason": finish_reason,
            "error_type": error_type,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cached_prompt_tokens": cached_prompt,
            "cache_hit_rate": hit_rate,
        }
        if debug_info is not None:
            debug_info.update(result_debug_info)

        # 费用优化可观测性：记录 cache metrics 和 prompt 规模
        if cache_policy is not None and cache_policy.enabled:
            prompt_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
            logger.info(
                "[LLMClient] cache_policy active | model=%s | prompt_tokens=%d | cached=%d | hit_rate=%.2f%% | stable_hash=%s",
                self.model, prompt_tokens, cached_prompt, hit_rate * 100, stable_prefix_hash[:12] or "none",
            )

        return LLMResult(
            content=content,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
            error_type=error_type,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cached_prompt_tokens=cached_prompt,
            cache_hit_rate=hit_rate,
            stable_prefix_hash=stable_prefix_hash,
            debug_info=result_debug_info,
        )

    async def _call_anthropic_compatible(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        timeout: float = 120.0,
        debug_info: dict | None = None,
        stable_prefix_hash: str = "",
        cache_policy: PromptCachePolicy | None = None,
    ) -> LLMResult | AsyncGenerator[str, None]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if stream:
            return self._stream_anthropic_messages(messages, temperature, max_tokens, timeout)

        # B3: 不支持 cache 的 provider 自动降级
        if cache_policy is not None and not self._provider_supports_cache(self.model):
            logger.debug(
                "[LLMClient] provider %s does not support cache, skipping cache_policy",
                self.model,
            )
            cache_policy = None

        # B2: Anthropic cache_control breakpoint 注入（无 cache_policy 时返回 str，向后兼容）
        system_param = self._build_anthropic_system_with_cache(system_prompt, cache_policy)

        async with self._anthropic_client(timeout) as client:
            response = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_param,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=temperature,
            )

        data = self._as_dict(response)
        # 方案 29：保留 thinking 内容（Claude extended thinking 兼容）
        text_parts = []
        thinking_parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                thinking_parts.append(block.get("thinking", ""))
        content = "".join(text_parts) if text_parts else ""
        reasoning_content = "\n".join(thinking_parts) if thinking_parts else ""
        finish_reason = data.get("stop_reason", "") or ""

        # 空 content 显式处理
        if not content:
            if finish_reason == "max_tokens":
                logger.warning(
                    "[LLMClient] _call_anthropic_compatible returned empty content "
                    f"with stop_reason=max_tokens, max_tokens={max_tokens} "
                    "may be insufficient for reasoning model"
                )
                error_type = "max_tokens_exhausted"
            else:
                error_type = "empty_content"
        else:
            error_type = ""

        # 方案 28 A3: 解析 Anthropic cache metrics
        usage = data.get("usage") or {}
        cache_read, cache_creation, cached_prompt, hit_rate = self._parse_anthropic_cache_metrics(usage)
        # A5: provider 不支持 cache metrics 时本地推导
        if not self._provider_supports_cache(self.model) and stable_prefix_hash:
            local_cached, local_rate = self._derive_local_cache_metrics(stable_prefix_hash)
            cached_prompt = local_cached
            hit_rate = local_rate

        result_debug_info = {
            "api_format": self.api_format,
            "model": self.model,
            "usage": usage,
            "response_chars": len(content or ""),
            "reasoning_content": reasoning_content,
            "finish_reason": finish_reason,
            "error_type": error_type,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cached_prompt_tokens": cached_prompt,
            "cache_hit_rate": hit_rate,
        }
        if debug_info is not None:
            debug_info.update(result_debug_info)

        # 费用优化可观测性：记录 cache metrics 和 prompt 规模
        if cache_policy is not None and cache_policy.enabled:
            prompt_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
            logger.info(
                "[LLMClient] cache_policy active | model=%s | prompt_tokens=%d | cached=%d | hit_rate=%.2f%% | stable_hash=%s",
                self.model, prompt_tokens, cached_prompt, hit_rate * 100, stable_prefix_hash[:12] or "none",
            )

        return LLMResult(
            content=content,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
            error_type=error_type,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cached_prompt_tokens=cached_prompt,
            cache_hit_rate=hit_rate,
            stable_prefix_hash=stable_prefix_hash,
            debug_info=result_debug_info,
        )
