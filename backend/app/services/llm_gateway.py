"""Unified LLM gateway for repair agents.

FBI V2 agents should call this gateway instead of constructing LLMClient
directly. The gateway is intentionally small: it loads the normal project
agent configuration, calls the current LLMClient API, and returns structured
diagnostics for empty responses, JSON parse failures, and provider errors.
"""
from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel

from app.services.agent_config import AgentConfigManager
from app.services.llm_client import LLMClient
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)


class LLMGatewayResult(BaseModel):
    ok: bool = False
    content: str = ""
    parsed_json: dict | list | None = None
    provider: str = ""
    model: str = ""
    finish_reason: str | None = None
    refusal: str | None = None
    content_filter: dict | None = None
    usage: dict | None = None
    request_id: str | None = None
    raw_choice: dict | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0


class LLMGateway:
    """Shared LLM caller with stable result semantics."""

    def __init__(self, agent_name: str = "core_generation"):
        self.agent_name = agent_name
        self.config_manager = AgentConfigManager()

    async def _client(self, model: str | None = None) -> LLMClient:
        cfg = await self.config_manager.get_agent_config(self.agent_name)
        return LLMClient(
            api_format=cfg.api_format,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=model or cfg.model,
        )

    async def generate_text(
        self,
        prompt: str = "",
        system: str = "",
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        timeout: float | None = None,
        task_type: LLMTaskType | None = None,
        **kwargs,
    ) -> LLMGatewayResult:
        """Generate plain text and report empty/provider failures explicitly.

        方案 29：支持 task_type 预设，未传时回退到显式参数或旧默认值。
        """
        start = time.monotonic()
        try:
            client = await self._client(model=model)
            content = await client.generate(
                system_prompt=system,
                user_prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format=kwargs.get("response_format"),
                task_type=task_type,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            if not content or not content.strip():
                return LLMGatewayResult(
                    ok=False,
                    provider=str(client.api_format),
                    model=client.model,
                    error_type="empty_content",
                    error_message="LLM returned empty content",
                    duration_ms=duration_ms,
                )

            return LLMGatewayResult(
                ok=True,
                content=content,
                provider=str(client.api_format),
                model=client.model,
                duration_ms=duration_ms,
            )
        except TimeoutError:
            return LLMGatewayResult(
                ok=False,
                error_type="provider_timeout",
                error_message="LLM request timed out",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            error_message = str(exc)
            logger.warning("[LLMGateway] request failed: %s", error_message)
            return LLMGatewayResult(
                ok=False,
                error_type=self._classify_error(error_message),
                error_message=error_message[:500],
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def generate_json(
        self,
        prompt: str = "",
        system: str = "",
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.3,
        timeout: float | None = None,
        task_type: LLMTaskType | None = None,
        **kwargs,
    ) -> LLMGatewayResult:
        """Generate JSON and parse it from raw or fenced output."""
        json_system = (system + "\n\n" if system else "") + "Return valid JSON only."
        result = await self.generate_text(
            prompt=prompt,
            system=json_system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            task_type=task_type,
            **kwargs,
        )
        if not result.ok:
            return result

        json_text = self._extract_json(result.content)
        if not json_text:
            result.ok = False
            result.error_type = "json_parse_failed"
            result.error_message = "Could not extract JSON from LLM response"
            return result

        try:
            result.parsed_json = json.loads(json_text)
            return result
        except json.JSONDecodeError as exc:
            result.ok = False
            result.error_type = "json_parse_failed"
            result.error_message = f"JSON parse failed: {exc}"
            return result

    async def generate_patch(
        self,
        prompt: str = "",
        system: str = "",
        model: str | None = None,
        **kwargs,
    ) -> LLMGatewayResult:
        """Generate a structured patch payload for FBI V2."""
        patch_schema = {
            "status": "patched | no_patch | needs_restructure | unsafe",
            "reason": "...",
            "patches": [
                {
                    "operation": "replace_span | rewrite_window | insert_after | append_tail | rewrite_scene",
                    "target_text": "existing text or anchor text",
                    "replacement": "replacement text for replace/rewrite/append",
                    "insert_text": "bridge text for insert_after",
                    "confidence": 0.8,
                }
            ],
            "expected_resolved_issue_ids": ["..."],
            "may_affect_issue_ids": ["..."],
        }
        patch_system = (
            (system + "\n\n" if system else "")
            + "Return a JSON patch payload in exactly this shape:\n"
            + json.dumps(patch_schema, ensure_ascii=False, indent=2)
        )
        return await self.generate_json(
            prompt=prompt,
            system=patch_system,
            model=model,
            temperature=0.3,
            **kwargs,
        )

    def _extract_json(self, text: str) -> str | None:
        text = text.strip()
        if (text.startswith("{") and text.endswith("}")) or (
            text.startswith("[") and text.endswith("]")
        ):
            return text

        import re

        fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if fenced:
            return fenced.group(1).strip()

        first_obj = text.find("{")
        last_obj = text.rfind("}")
        if first_obj >= 0 and last_obj > first_obj:
            return text[first_obj : last_obj + 1]

        first_arr = text.find("[")
        last_arr = text.rfind("]")
        if first_arr >= 0 and last_arr > first_arr:
            return text[first_arr : last_arr + 1]

        return None

    def _classify_error(self, error: str) -> str:
        lowered = error.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return "provider_timeout"
        if (
            "content_filter" in lowered
            or "content filter" in lowered
            or "safety" in lowered
            or "refusal" in lowered
        ):
            return "content_filtered"
        if "length" in lowered or "max_tokens" in lowered:
            return "finish_length"
        if "connection" in lowered or "network" in lowered:
            return "transport_error"
        if "api_key" in lowered or "authentication" in lowered or "unauthorized" in lowered:
            return "auth_error"
        return "unknown_error"


_gateway: LLMGateway | None = None


def get_llm_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
