"""Post-repair structural and quality protection for skill-owned repairs."""
from __future__ import annotations

import inspect


class AgentSkillProtection:
    async def audit(
        self,
        *,
        original_text: str,
        candidate_text: str,
        context: dict,
        structural_check=None,
    ) -> dict:
        structural = await self._run_structural_check(candidate_text, structural_check)
        if not structural.get("ok", True):
            return {"ok": False, "structural": structural, "quality_gate": {"status": "skipped"}}

        quality_gate = await self._quality_gate_delta(original_text, candidate_text, context)
        return {
            "ok": not quality_gate.get("new_blocking_violations"),
            "structural": structural,
            "quality_gate": quality_gate,
        }

    @staticmethod
    async def _run_structural_check(candidate_text: str, structural_check) -> dict:
        if structural_check is None:
            return {"ok": True, "status": "not_configured"}
        result = structural_check(candidate_text)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {"ok": bool(result)}

    @staticmethod
    async def _quality_gate_delta(original_text: str, candidate_text: str, context: dict) -> dict:
        if not context.get("scene_contract"):
            return {"status": "skipped", "reason": "missing_scene_contract"}
        try:
            from app.services.quality_gate import QualityGate

            gate = QualityGate()
            base_context = dict(context)
            original = await gate.evaluate(
                {**base_context, "generated_text": original_text},
                level="fast",
            )
            candidate = await gate.evaluate(
                {**base_context, "generated_text": candidate_text},
                level="fast",
            )
            original_blocking = AgentSkillProtection._blocking_signatures(original)
            candidate_blocking = AgentSkillProtection._blocking_signatures(candidate)
            added = sorted(candidate_blocking - original_blocking)
            return {
                "status": "ok",
                "original_blocking_count": len(original_blocking),
                "candidate_blocking_count": len(candidate_blocking),
                "new_blocking_violations": added,
            }
        except Exception as exc:
            return {
                "status": "degraded",
                "reason": "quality_gate_unavailable",
                "error": str(exc),
                "new_blocking_violations": [],
            }

    @staticmethod
    def _blocking_signatures(report: dict) -> set[str]:
        signatures: set[str] = set()
        for violation in report.get("violations") or []:
            if not violation.get("blocks_commit"):
                continue
            key = (
                violation.get("type")
                or violation.get("issue_type")
                or violation.get("category")
                or violation.get("message")
                or "unknown"
            )
            signatures.add(str(key))
        return signatures


_protection: AgentSkillProtection | None = None


def get_skill_protection() -> AgentSkillProtection:
    global _protection
    if _protection is None:
        _protection = AgentSkillProtection()
    return _protection
