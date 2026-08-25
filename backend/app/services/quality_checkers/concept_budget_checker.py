from __future__ import annotations

from app.models.quality_advisory import make_advisory
from app.services.concept_budget_service import ConceptBudgetService


class ConceptBudgetChecker:
    def __init__(self):
        self.service = ConceptBudgetService()

    def check(self, text: str, scene_contract: dict | None, chapter_state: dict | None = None) -> dict:
        contract = scene_contract if isinstance(scene_contract, dict) else {}
        extensions = contract.get("quality_extensions")
        reveal_control = {}
        if isinstance(extensions, dict):
            reveal_control = extensions.get("reveal_control") or {}
        reveal_control = self.service.normalize_reveal_control(reveal_control)

        candidates = self.service.extract_definition_candidates(text or "")
        allowed = set(reveal_control.get("allowed_new_concepts", []))
        forbidden = set(reveal_control.get("forbidden_future_concepts", []))
        budget = int(reveal_control.get("new_concept_budget", 2))
        unplanned = [c for c in candidates if allowed and c not in allowed]
        forbidden_hits = [
            candidate
            for candidate in candidates
            if self._matches_forbidden(candidate, forbidden)
        ]
        advisories = []

        if len(candidates) > budget:
            advisories.append(make_advisory(
                "concept_budget_exceeded",
                "medium",
                f"本场景疑似首次定义 {len(candidates)} 个概念，超过建议预算 {budget}。",
                target_span="、".join(candidates[:5]),
                expected_behavior="减少首次定义，把设定分散到动作、对话和后续场景。",
                detector="concept_budget_checker",
                confidence=0.72,
            ))

        if unplanned:
            advisories.append(make_advisory(
                "unplanned_new_concept",
                "medium",
                f"正文出现未在 allowed_new_concepts 中声明的新概念：{'、'.join(unplanned[:5])}。",
                target_span="、".join(unplanned[:5]),
                expected_behavior="生成前声明新概念，或把该概念延后。",
                detector="concept_budget_checker",
                confidence=0.68,
            ))

        if forbidden_hits:
            advisories.append(make_advisory(
                "forbidden_future_concept",
                "high",
                f"正文疑似提前暴露未来概念：{'、'.join(forbidden_hits[:5])}。",
                target_span="、".join(forbidden_hits[:5]),
                expected_behavior="避免提前揭示未来设定，改用读者当前可知的信息。",
                detector="concept_budget_checker",
                confidence=0.82,
            ))

        return {
            "schema_version": 1,
            "status": "ok",
            "reveal_control": reveal_control,
            "detected_concepts": candidates,
            "advisories": advisories,
        }

    @staticmethod
    def _matches_forbidden(candidate: str, forbidden: set[str]) -> bool:
        normalized_candidate = candidate.strip()
        if not normalized_candidate:
            return False
        return any(
            normalized_candidate == item
            or (len(item) >= 2 and normalized_candidate.startswith(item))
            for raw in forbidden
            if (item := str(raw).strip())
        )
