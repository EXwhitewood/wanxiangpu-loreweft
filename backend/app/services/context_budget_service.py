"""Context budget primitives for editor and writer orchestration.

The editor system treats the model window as one configurable envelope instead
of hand-written per-agent token slices.  Token estimates are deliberately
provider-agnostic; providers can later override them with real usage data.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from typing import Any


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(slots=True)
class ModelContextProfile:
    profile_id: str = "default_200k"
    context_window_tokens: int = 200_000
    reserve_output_tokens: int = 16_000
    soft_compaction_ratio: float = 0.75
    hard_compaction_ratio: float = 0.9
    auto_compaction_enabled: bool = True
    token_estimator: str = "provider_usage_or_heuristic"
    supports_prompt_cache: bool = False

    @property
    def soft_input_limit_tokens(self) -> int:
        usable = max(self.context_window_tokens - self.reserve_output_tokens, 1)
        return int(usable * self.soft_compaction_ratio)

    @property
    def hard_input_limit_tokens(self) -> int:
        usable = max(self.context_window_tokens - self.reserve_output_tokens, 1)
        return int(usable * self.hard_compaction_ratio)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["soft_input_limit_tokens"] = self.soft_input_limit_tokens
        data["hard_input_limit_tokens"] = self.hard_input_limit_tokens
        return data


class ContextBudgetService:
    """Estimate and compare context budgets."""

    DEFAULT_PROFILE = ModelContextProfile()

    def resolve_profile(self, override: dict | None = None) -> ModelContextProfile:
        data = dict(override or {})
        if not data:
            return self.DEFAULT_PROFILE
        return ModelContextProfile(
            profile_id=str(data.get("profile_id") or data.get("id") or "custom"),
            context_window_tokens=int(data.get("context_window_tokens") or 200_000),
            reserve_output_tokens=int(data.get("reserve_output_tokens") or 16_000),
            soft_compaction_ratio=float(data.get("soft_compaction_ratio") or 0.75),
            hard_compaction_ratio=float(data.get("hard_compaction_ratio") or 0.9),
            auto_compaction_enabled=bool(data.get("auto_compaction_enabled", True)),
            token_estimator=str(data.get("token_estimator") or "provider_usage_or_heuristic"),
            supports_prompt_cache=bool(data.get("supports_prompt_cache", False)),
        )

    def estimate_tokens(self, value: Any) -> int:
        """Heuristic token estimate that behaves well for CJK prose and JSON."""
        if value is None:
            return 0
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False, default=str)
            except TypeError:
                value = str(value)
        if not value:
            return 0

        cjk_count = len(_CJK_RE.findall(value))
        word_count = len(_WORD_RE.findall(value))
        other_chars = max(len(value) - cjk_count, 0)

        # Chinese prose is often close to one char per token on common BPEs,
        # while ASCII text is closer to four chars per token.  Add a small
        # structural overhead for JSON punctuation and Markdown labels.
        estimate = cjk_count + math.ceil(other_chars / 4) + math.ceil(word_count * 0.15)
        return max(1, int(estimate * 1.08))

    def attach_token_estimates(self, envelope: dict) -> dict:
        profile = self.resolve_profile(envelope.get("model_context_profile") or envelope.get("budget"))
        total = 0
        for block in envelope.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            tokens = self.estimate_tokens(block.get("content"))
            block["estimated_tokens"] = tokens
            total += tokens
        envelope["token_report"] = {
            **dict(envelope.get("token_report") or {}),
            "estimated_input_tokens": total,
            "limit_tokens": profile.context_window_tokens,
            "reserve_output_tokens": profile.reserve_output_tokens,
            "soft_input_limit_tokens": profile.soft_input_limit_tokens,
            "hard_input_limit_tokens": profile.hard_input_limit_tokens,
            "over_soft_limit": total > profile.soft_input_limit_tokens,
            "over_hard_limit": total > profile.hard_input_limit_tokens,
        }
        return envelope


_budget_service: ContextBudgetService | None = None


def get_context_budget_service() -> ContextBudgetService:
    global _budget_service
    if _budget_service is None:
        _budget_service = ContextBudgetService()
    return _budget_service
