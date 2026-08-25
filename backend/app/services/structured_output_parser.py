"""Structured output parser for LLM JSON payloads.

Critical agents should not silently turn malformed model output into an empty
dict. This parser centralizes JSON cleanup, optional schema validation, and
typed parse errors so workflow nodes can surface the real failure.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


class StructuredOutputParseError(ValueError):
    """Raised when a model response cannot be parsed into the required shape."""

    def __init__(self, message: str, *, error_type: str = "json_parse_failed", raw_preview: str = "") -> None:
        super().__init__(message)
        self.error_type = error_type
        self.raw_preview = raw_preview


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ParsedStructuredOutput:
    data: Any
    cleaned_text: str
    repairs_applied: list[str]


class StructuredOutputParser:
    """Parse model JSON with deterministic cleanup and typed errors."""

    @classmethod
    def parse(cls, raw_content: str, *, require_object: bool = False) -> ParsedStructuredOutput:
        raw = (raw_content or "").strip()
        if not raw:
            raise StructuredOutputParseError(
                "LLM returned an empty response",
                error_type="empty_response",
                raw_preview="",
            )

        candidates = cls._candidate_texts(raw)
        last_error: Exception | None = None
        for cleaned, repairs in candidates:
            try:
                data = json.loads(cleaned)
                if require_object and not isinstance(data, dict):
                    raise StructuredOutputParseError(
                        "Parsed JSON is not an object",
                        error_type="schema_validation_failed",
                        raw_preview=raw[:200],
                    )
                return ParsedStructuredOutput(data=data, cleaned_text=cleaned, repairs_applied=repairs)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue

        message = "LLM response does not contain valid JSON"
        if last_error:
            message = f"{message}: {last_error}"
        raise StructuredOutputParseError(
            message,
            error_type="json_parse_failed",
            raw_preview=raw[:200],
        )

    @classmethod
    def parse_model(cls, raw_content: str, model: type[T]) -> tuple[T, ParsedStructuredOutput]:
        parsed = cls.parse(raw_content, require_object=True)
        try:
            return model.model_validate(parsed.data), parsed
        except ValidationError as exc:
            raise StructuredOutputParseError(
                f"Parsed JSON failed schema validation: {exc}",
                error_type="schema_validation_failed",
                raw_preview=(raw_content or "")[:200],
            ) from exc

    @classmethod
    def _candidate_texts(cls, raw: str) -> list[tuple[str, list[str]]]:
        candidates: list[tuple[str, list[str]]] = [(raw, [])]

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
        if fenced:
            candidates.append((fenced.group(1).strip(), ["strip_code_fence"]))

        extracted = cls._extract_balanced_json(raw)
        if extracted and extracted != raw:
            candidates.append((extracted, ["extract_balanced_json"]))

        for text, repairs in list(candidates):
            no_trailing_commas = re.sub(r",\s*([\]}])", r"\1", text)
            if no_trailing_commas != text:
                candidates.append((no_trailing_commas, repairs + ["remove_trailing_commas"]))

        repaired = cls._repair_truncated(raw)
        if repaired:
            candidates.append((repaired, ["repair_truncated_json"]))

        seen: set[str] = set()
        unique: list[tuple[str, list[str]]] = []
        for text, repairs in candidates:
            key = text.strip()
            if key and key not in seen:
                seen.add(key)
                unique.append((key, repairs))
        return unique

    @staticmethod
    def _extract_balanced_json(text: str) -> str | None:
        starts = [i for i, ch in enumerate(text) if ch in "[{"]
        if not starts:
            return None
        for start in starts:
            opening = text[start]
            closing = "]" if opening == "[" else "}"
            end = text.rfind(closing)
            if end > start:
                return text[start:end + 1].strip()
        return None

    @staticmethod
    def _repair_truncated(text: str) -> str | None:
        start = next((i for i, ch in enumerate(text) if ch in "[{"), -1)
        if start < 0:
            return None
        fragment = text[start:].strip()
        stack: list[str] = []
        in_string = False
        escape_next = False
        for ch in fragment:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in "[{":
                stack.append(ch)
            elif ch in "]}":
                if not stack:
                    return None
                expected = "[" if ch == "]" else "{"
                if stack[-1] != expected:
                    return None
                stack.pop()
        if not stack and not in_string:
            return None
        suffix = '"' if in_string else ""
        suffix += "".join("]" if ch == "[" else "}" for ch in reversed(stack))
        return fragment + suffix

