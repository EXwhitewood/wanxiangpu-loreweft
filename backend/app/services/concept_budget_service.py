from __future__ import annotations

import copy
import re
from collections.abc import Mapping


DEFAULT_REVEAL_CONTROL = {
    "new_concept_budget": 2,
    "allowed_new_concepts": [],
    "deepen_existing_concepts": [],
    "forbidden_future_concepts": [],
    "preferred_carriers": ["action", "dialogue", "observation"],
}


class ConceptBudgetService:
    def normalize_reveal_control(self, value: Mapping | None) -> dict:
        result = copy.deepcopy(DEFAULT_REVEAL_CONTROL)
        if not isinstance(value, Mapping):
            return result
        for key in result:
            raw = value.get(key)
            if key == "new_concept_budget":
                try:
                    result[key] = max(0, min(5, int(raw)))
                except (TypeError, ValueError):
                    pass
            elif isinstance(raw, list):
                result[key] = [str(item).strip() for item in raw if str(item).strip()]
        return result

    def suggest_for_contract(self, scene_contract: dict | None, chapter_context: dict | None = None) -> dict:
        contract = scene_contract if isinstance(scene_contract, dict) else {}
        must_show = contract.get("must_show", [])
        chapter_number = int(contract.get("chapter_number") or 0)
        scene_index = int(contract.get("scene_index") or 0)
        conflict = str(contract.get("conflict") or contract.get("source_of_truth", {}).get("conflict") or "")

        budget = 2
        if chapter_number == 1 and scene_index == 0:
            budget = 1
        if isinstance(must_show, list) and len(must_show) >= 5:
            budget = max(1, budget - 1)
        if len(conflict) > 60:
            budget = min(3, budget + 1)

        return {
            "schema_version": 1,
            "reveal_control": {
                **copy.deepcopy(DEFAULT_REVEAL_CONTROL),
                "new_concept_budget": budget,
            },
            "reason": "根据章节位置、must_show 数量和冲突复杂度给出建议预算。",
        }

    def with_quality_extensions(self, scene_contract: dict | None, reveal_control: Mapping | None = None) -> dict:
        contract = copy.deepcopy(scene_contract) if isinstance(scene_contract, dict) else {}
        existing = contract.get("quality_extensions")
        if not isinstance(existing, dict):
            existing = {"schema_version": 1}
        existing["reveal_control"] = self.normalize_reveal_control(
            reveal_control or existing.get("reveal_control")
        )
        contract["quality_extensions"] = existing
        return contract

    @staticmethod
    def extract_definition_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        patterns = [
            r"(?:所谓|名为|名叫|叫做?)\s*([一-龥A-Za-z0-9·]{2,12})",
            r"([一-龥A-Za-z0-9·]{2,12})(?:是一种|乃是|指的是|代表着)",
            r"([一-龥A-Za-z0-9·]{2,12})(?:体系|法则|规矩|制度|等级)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                value = match.group(1).strip("，。！？；：: ")
                if value and value not in candidates:
                    candidates.append(value)
        return candidates
