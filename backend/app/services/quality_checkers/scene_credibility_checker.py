from __future__ import annotations

import re
from typing import Any

from app.models.violation import make_violation
from app.utils.dash_artifacts import (
    DASH_ARTIFACT_RE,
    count_dash_artifacts,
    dash_artifact_density_per_1000,
)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s*")
_EXPLANATORY_DASH_RE = re.compile(rf"[^。！？!?]{{0,40}}(?:{DASH_ARTIFACT_RE.pattern})(?:不是|并非|而是|说明|意味着|确认|明白|意识到)[^。！？!?]{{0,80}}")
_NOT_A_BUT_C_RE = re.compile(r"不是[^。！？!?]{1,30}(?:不是|并非)[^。！？!?]{1,40}而是[^。！？!?]{1,60}")
_CONCLUSION_VERB_RE = re.compile(r"(?:他|她|它|他们|她们|角色|主角|[一-龥]{1,6})(?:终于|忽然|立刻|瞬间)?(?:意识到|确认|明白|知道)[^。！？!?]{10,80}(?:这意味着|说明|不是|而是)")
_IMPOSSIBLE_MEMORY_RE = re.compile(
    r"(?:闭上眼|一瞬间|瞬间|立刻|马上|脑中|记忆里)[^。！？!?]{0,40}"
    r"(?:全部|完整|所有|每一(?:页|章|幕|段|件)|从头到尾)[^。！？!?]{0,50}"
    r"(?:想起|回忆|记起|翻过|复盘|知道)"
)
_UNSUPPORTED_CERTAINTY_RE = re.compile(r"(?:毫无根据|没有证据|没有来由|不知为何)[^。！？!?]{0,50}(?:确定|断定|明白|知道)")


class SceneCredibilityChecker:
    """Rule-based checker for the Scene Credibility Protocol.

    It is intentionally domain-neutral. The checker only cares about reusable
    narrative failure modes: contract-forbidden claims, knowledge overreach,
    unsupported cognitive certainty, implausible complete memory, and
    explanatory narration artifacts.
    """

    def check(
        self,
        text: str,
        credibility_contract: dict | None = None,
        *,
        mode: str = "assist",
    ) -> dict:
        if not text:
            return self._report([], [], {}, credibility_contract or {})

        contract = credibility_contract or {}
        violations: list[dict] = []
        advisories: list[dict] = []

        violations.extend(self._check_fact_boundaries(text, contract, mode))
        violations.extend(self._check_knowledge_boundaries(text, contract, mode))
        violations.extend(self._check_plausibility(text, mode))
        narration_result = self._check_narration(text, mode)
        violations.extend(narration_result["violations"])
        advisories.extend(narration_result["advisories"])

        metrics = {
            "forbidden_claim_count": sum(1 for v in violations if v.get("type") == "fact_boundary_conflict"),
            "knowledge_boundary_count": sum(1 for v in violations if v.get("type") == "knowledge_boundary_violation"),
            "plausibility_count": sum(1 for v in violations if v.get("type") in {"plausibility_break", "memory_plausibility_break"}),
            "narration_artifact_count": sum(1 for v in violations if v.get("type") in {"narration_explanation_artifact", "explanatory_punctuation_artifact"}),
            "emdash_pair_count": count_dash_artifacts(text),
            "dash_artifact_count": count_dash_artifacts(text),
        }
        return self._report(violations, advisories, metrics, contract)

    def _check_fact_boundaries(self, text: str, contract: dict, mode: str) -> list[dict]:
        violations: list[dict] = []
        for boundary in _as_list(contract.get("fact_boundaries")):
            if not isinstance(boundary, dict):
                continue
            label = str(boundary.get("label") or "事实边界")
            for claim in _as_list(boundary.get("forbidden_claims")):
                claim_text = _clean_claim(claim)
                if len(claim_text) < 2 or claim_text not in text:
                    continue
                violations.append(make_violation(
                    "fact_boundary_conflict",
                    "critical",
                    f"正文写入了可信度合同禁止的断言：{claim_text}",
                    source="scene_credibility",
                    target_span=_clip_around(text, claim_text),
                    expected_behavior=f"删除或改写该断言，保持「{label}」与场景事实边界一致。",
                    suggested_strategy="patch_text",
                    blocks_commit=mode in {"assist", "enforce"},
                    evidence={"label": label, "forbidden_claim": claim_text},
                ))
                if len(violations) >= 5:
                    return violations
        return violations

    def _check_knowledge_boundaries(self, text: str, contract: dict, mode: str) -> list[dict]:
        violations: list[dict] = []
        for snapshot in _as_list(contract.get("knowledge_snapshots")):
            if not isinstance(snapshot, dict):
                continue
            display_name = str(snapshot.get("display_name") or "角色")
            for claim in _as_list(snapshot.get("cannot_know")):
                claim_text = _clean_claim(claim)
                if len(claim_text) < 6 or claim_text not in text:
                    continue
                violations.append(make_violation(
                    "knowledge_boundary_violation",
                    "high",
                    f"{display_name} 的认知越界：正文直接写出了角色当前不应知道的信息。",
                    source="scene_credibility",
                    target_span=_clip_around(text, claim_text),
                    expected_behavior="将确定性叙述改为基于观察、对话、线索或误判的有限推断。",
                    suggested_strategy="rewrite_scene",
                    blocks_commit=mode in {"assist", "enforce"},
                    evidence={"character": display_name, "cannot_know": claim_text},
                ))
                if len(violations) >= 3:
                    return violations
        return violations

    def _check_plausibility(self, text: str, mode: str) -> list[dict]:
        violations: list[dict] = []
        for match in _IMPOSSIBLE_MEMORY_RE.finditer(text):
            span = match.group(0)
            violations.append(make_violation(
                "memory_plausibility_break",
                "high",
                "角色出现无触发的过度完整记忆或复盘，削弱心理可信度。",
                source="scene_credibility",
                target_span=_clip(span),
                expected_behavior="改为由物件、环境、对话、身体反应触发的片段式、不完整记忆。",
                suggested_strategy="rewrite_scene",
                blocks_commit=mode in {"assist", "enforce"},
                evidence={"matched_pattern": "complete_memory", "span": span},
            ))
            break

        for match in _UNSUPPORTED_CERTAINTY_RE.finditer(text):
            span = match.group(0)
            violations.append(make_violation(
                "plausibility_break",
                "high",
                "正文承认缺少证据，却让角色直接获得确定结论。",
                source="scene_credibility",
                target_span=_clip(span),
                expected_behavior="补充观察、线索或推理桥，或将确定结论降级为怀疑。",
                suggested_strategy="rewrite_scene",
                blocks_commit=mode in {"assist", "enforce"},
                evidence={"matched_pattern": "unsupported_certainty", "span": span},
            ))
            break
        return violations

    def _check_narration(self, text: str, mode: str) -> dict:
        violations: list[dict] = []
        advisories: list[dict] = []
        matches: list[tuple[str, str]] = []
        for pattern_name, pattern in (
            ("explanatory_dash", _EXPLANATORY_DASH_RE),
            ("not_a_but_c", _NOT_A_BUT_C_RE),
            ("conclusion_verb", _CONCLUSION_VERB_RE),
        ):
            found = pattern.findall(text)
            for item in found[:3]:
                matches.append((pattern_name, item if isinstance(item, str) else str(item)))

        emdash_density = dash_artifact_density_per_1000(text)
        if matches or emdash_density >= 5:
            representative = matches[0][1] if matches else "——"
            severity = "high" if len(matches) >= 2 or emdash_density >= 6 else "medium"
            violation = make_violation(
                "explanatory_punctuation_artifact" if emdash_density >= 5 else "narration_explanation_artifact",
                severity,
                "叙述存在解释腔或模型式心理拆解，容易让正文像在说明推理过程。",
                source="scene_credibility",
                target_span=_clip(representative),
                expected_behavior="用动作、停顿、视线、感官、对话压力或选择后果承载判断，减少直接解释。",
                suggested_strategy="patch_text",
                blocks_commit=False,
                evidence={
                    "matches": [{"pattern": name, "span": _clip(span)} for name, span in matches[:5]],
                    "emdash_density_per_1000_chars": round(emdash_density, 2),
                },
            )
            if violation["blocks_commit"]:
                violations.append(violation)
            else:
                advisories.append(_violation_to_advisory(violation))
        return {"violations": violations, "advisories": advisories}

    @staticmethod
    def _report(violations: list[dict], advisories: list[dict], metrics: dict, contract: dict) -> dict:
        return {
            "schema_version": 1,
            "status": "ok",
            "violations": violations,
            "advisories": advisories,
            "metrics": metrics,
            "contract": contract,
        }


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean_claim(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("claim") or value.get("reason") or value.get("event_type") or ""
    return " ".join(str(value or "").strip().split())


def _clip(text: str, limit: int = 100) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _clip_around(text: str, needle: str, window: int = 50) -> str:
    pos = text.find(needle)
    if pos < 0:
        return _clip(needle)
    start = max(0, pos - window)
    end = min(len(text), pos + len(needle) + window)
    return _clip(text[start:end], limit=window * 2 + len(needle))


def _violation_to_advisory(violation: dict) -> dict:
    return {
        "type": violation.get("type", "scene_credibility_advisory"),
        "severity": violation.get("severity", "medium"),
        "detail": violation.get("detail", ""),
        "target_span": violation.get("target_span"),
        "expected_behavior": violation.get("expected_behavior", ""),
        "detector": "scene_credibility_checker",
        "confidence": 0.72,
    }
