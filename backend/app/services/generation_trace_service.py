from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone


class GenerationTraceService:
    def build_metadata(
        self,
        *,
        project_id: str,
        chapter_number: int,
        scene_index: int,
        context: dict,
        generated_text: str = "",
        quality_report: dict | None = None,
        recovery: dict | None = None,
        mode: str = "metadata",
        proposition_extraction: dict | None = None,
        proposition_audit: dict | None = None,
        fact_contract: dict | None = None,
    ) -> dict:
        manifest = self._manifest_context(context)

        # full_debug_when_failed: 仅在生成失败时记录完整调试信息
        effective_mode = mode
        if mode == "full_debug_when_failed":
            is_failed = quality_report and (
                quality_report.get("commit_blocked")
                or not quality_report.get("passed")
            )
            effective_mode = "full" if is_failed else "metadata"

        trace = {
            "id": str(uuid.uuid4()),
            "project_id": str(project_id),
            "chapter_number": chapter_number,
            "scene_index": scene_index,
            "trace_version": 2,
            "mode": mode,
            "effective_mode": effective_mode,
            "status": "completed",
            "context_manifest": manifest,
            "prompt_preview": self._prompt_preview(context, effective_mode),
            "quality_summary": self._quality_summary(quality_report or {}),
            "recovery_summary": self._recovery_summary(recovery or {}),
            "generated_text_hash": self._hash(generated_text),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        }

        # 命题层摘要（shadow/report/enforce 模式均记录）
        if proposition_extraction or proposition_audit or fact_contract:
            trace["proposition_summary"] = self._proposition_summary(
                proposition_extraction or {},
                proposition_audit or {},
                fact_contract or {},
            )

        # 失败时记录额外调试信息
        if effective_mode == "full" and quality_report and not quality_report.get("passed"):
            trace["debug_info"] = self._build_debug_info(
                context, quality_report, recovery or {},
                proposition_extraction or {},
                proposition_audit or {},
                fact_contract or {},
            )

        return trace

    async def record_in_transaction(self, db, trace: dict) -> str | None:
        """Record a trace in the caller's chapter transaction.

        Generation traces describe accepted chapter output.  Keeping them in
        the same transaction as the chapter effects prevents cancelled drafts
        from leaving durable trace rows behind.
        """
        if not trace:
            return None
        from app.db.db_models import GenerationTrace

        trace_id = trace.get("id") or str(uuid.uuid4())
        obj = GenerationTrace(
            id=uuid.UUID(str(trace_id)) if self._is_uuid(str(trace_id)) else trace_id,
            project_id=(
                uuid.UUID(str(trace["project_id"]))
                if self._is_uuid(str(trace["project_id"]))
                else trace["project_id"]
            ),
            chapter_number=trace.get("chapter_number", 0),
            scene_index=trace.get("scene_index", 0),
            trace_version=trace.get("trace_version", 1),
            mode=trace.get("mode", "metadata"),
            status=trace.get("status", "completed"),
            context_manifest=trace.get("context_manifest", {}),
            prompt_preview=trace.get("prompt_preview", {}),
            quality_summary=trace.get("quality_summary", {}),
            recovery_summary=trace.get("recovery_summary", {}),
            expires_at=datetime.fromisoformat(trace["expires_at"]) if trace.get("expires_at") else None,
        )
        db.add(obj)
        await db.flush()
        return str(trace_id)

    @staticmethod
    def _is_uuid(value: str) -> bool:
        try:
            uuid.UUID(value)
            return True
        except Exception:
            return False

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]

    def _manifest_context(self, context: dict) -> dict:
        manifest = {}
        for key, value in (context or {}).items():
            if key in {"api_key", "headers", "authorization"}:
                continue
            manifest[key] = {
                "type": type(value).__name__,
                "size": self._size(value),
                "hash": self._hash(str(value)[:4000]),
            }
        return manifest

    @staticmethod
    def _size(value) -> int:
        if isinstance(value, (str, list, tuple, dict, set)):
            return len(value)
        return 1 if value is not None else 0

    def _prompt_preview(self, context: dict, mode: str) -> dict:
        if mode != "full":
            return {}
        allowed = {}
        for key in ("custom_instructions", "chapter_summaries", "forward_constraints"):
            if context.get(key):
                allowed[key] = str(context[key])[:1200]
        quality_extensions = self._quality_extensions_preview(context)
        if quality_extensions:
            allowed["quality_extensions"] = quality_extensions
        return allowed

    @staticmethod
    def _quality_extensions_preview(context: dict) -> dict:
        """Expose only the quality constraints actually sent to the writer prompt."""
        scene_contract = (context or {}).get("scene_contract")
        if not isinstance(scene_contract, dict):
            return {}
        quality_extensions = scene_contract.get("quality_extensions")
        if not isinstance(quality_extensions, dict):
            return {}

        preview: dict = {}
        anti_ai_guidance = quality_extensions.get("anti_ai_guidance")
        if isinstance(anti_ai_guidance, list):
            preview["anti_ai_guidance"] = [
                str(item)[:160]
                for item in anti_ai_guidance[:5]
                if item
            ]

        reveal_control = quality_extensions.get("reveal_control")
        if isinstance(reveal_control, dict):
            rc_preview: dict = {}
            if "new_concept_budget" in reveal_control:
                rc_preview["new_concept_budget"] = reveal_control.get("new_concept_budget")
            for key in ("forbidden_future_concepts", "preferred_carriers"):
                value = reveal_control.get(key)
                if isinstance(value, list):
                    rc_preview[key] = [str(item)[:120] for item in value[:5] if item]
            if rc_preview:
                preview["reveal_control"] = rc_preview

        character_voice_guidance = quality_extensions.get("character_voice_guidance")
        if isinstance(character_voice_guidance, dict):
            cv_preview = {}
            for name, guidance in list(character_voice_guidance.items())[:5]:
                if isinstance(guidance, list):
                    cv_preview[str(name)[:80]] = [
                        str(item)[:160]
                        for item in guidance[:3]
                        if item
                    ]
            if cv_preview:
                preview["character_voice_guidance"] = cv_preview

        for key in (
            "experience_guidance",
            "literary_quality_guidance",
            "commercial_pacing_guidance",
            "scene_credibility_guidance",
            "fact_boundary_guidance",
            "knowledge_boundary_guidance",
            "plausibility_guidance",
            "narration_credibility_guidance",
            "style_experience_resolution",
        ):
            value = quality_extensions.get(key)
            if isinstance(value, list):
                preview[key] = [
                    str(item)[:160]
                    for item in value[:6]
                    if item
                ]

        mode_guardrails = quality_extensions.get("mode_guardrails")
        if isinstance(mode_guardrails, dict):
            preview["mode_guardrails"] = {
                "writing_mode_id": str(mode_guardrails.get("writing_mode_id", ""))[:80],
                "label": str(mode_guardrails.get("label", ""))[:80],
                "target_reader": str(mode_guardrails.get("target_reader", ""))[:120],
            }

        return preview

    @staticmethod
    def _quality_summary(report: dict) -> dict:
        return {
            "passed": report.get("passed"),
            "commit_blocked": report.get("commit_blocked"),
            "violation_count": len(report.get("violations", []) or []),
            "experimental": GenerationTraceService._experimental_summary(
                report.get("reports", {}).get("experimental", {})
            ),
            "experience_quality": GenerationTraceService._experience_quality_summary(
                report.get("reports", {})
            ),
        }

    @staticmethod
    def _experimental_summary(experimental: dict) -> dict:
        if not isinstance(experimental, dict):
            return {}
        summary = {}
        for name, report in experimental.items():
            if name == "advisories" or not isinstance(report, dict):
                continue
            advisories = report.get("advisories", []) or []
            item = {
                "status": report.get("status"),
                "advisory_count": len(advisories),
                "advisory_types": sorted({
                    advisory.get("type")
                    for advisory in advisories
                    if isinstance(advisory, dict) and advisory.get("type")
                }),
            }
            for key in ("score", "overall_level", "dialogue_count", "speaker_count", "degraded"):
                if key in report:
                    item[key] = report[key]
            summary[name] = item
        return summary

    @staticmethod
    def _experience_quality_summary(reports: dict) -> dict:
        if not isinstance(reports, dict):
            return {}
        summary = {}
        for name in ("narrative_experience", "literary_quality", "commercial_pacing", "scene_credibility", "mode_fit", "style_experience_conflict"):
            report = reports.get(name)
            if not isinstance(report, dict):
                continue
            item = {
                "status": report.get("status"),
                "mode": report.get("mode"),
            }
            advisories = report.get("advisories", [])
            if isinstance(advisories, list):
                item["advisory_count"] = len(advisories)
                item["advisory_types"] = sorted({
                    adv.get("type")
                    for adv in advisories
                    if isinstance(adv, dict) and adv.get("type")
                })
            scores = report.get("scores")
            if isinstance(scores, dict):
                item["scores"] = {
                    key: scores.get(key)
                    for key in list(scores.keys())[:8]
                }
            if name == "mode_fit":
                for key in ("writing_mode_id", "fit_score", "weighted_score"):
                    if key in report:
                        item[key] = report[key]
            if name == "style_experience_conflict":
                conflicts = report.get("conflicts", [])
                item["conflict_count"] = len(conflicts) if isinstance(conflicts, list) else 0
            summary[name] = item
        return summary

    @staticmethod
    def _recovery_summary(recovery: dict) -> dict:
        summary = {
            "status": recovery.get("status", ""),
            "recovery_mode": recovery.get("recovery_mode", "none"),
            "attempt_count": len(recovery.get("attempts", []) or []),
        }
        inline_revision = recovery.get("ai_quality_inline_revision")
        if isinstance(inline_revision, dict):
            summary["ai_quality_inline_revision"] = {
                "status": inline_revision.get("status", ""),
                "mode": inline_revision.get("mode", ""),
                "applied_patch_count": inline_revision.get("applied_patch_count", 0),
                "eligible_hint_count": inline_revision.get("eligible_hint_count", 0),
                "draft_hash": inline_revision.get("draft_hash", ""),
                "final_hash": inline_revision.get("final_hash", ""),
            }
            failure_reason = inline_revision.get("failure_reason")
            if failure_reason:
                summary["ai_quality_inline_revision"]["failure_reason"] = failure_reason
            skipped_reason_counts = inline_revision.get("skipped_reason_counts")
            if isinstance(skipped_reason_counts, dict) and skipped_reason_counts:
                summary["ai_quality_inline_revision"]["skipped_reason_counts"] = skipped_reason_counts
        return summary

    @staticmethod
    def _proposition_summary(
        extraction: dict,
        audit: dict,
        fact_contract: dict,
    ) -> dict:
        """命题层摘要：记录抽取、审计、事实合同的关键指标。"""
        propositions = extraction.get("propositions", [])
        return {
            "extraction": {
                "proposition_count": len(propositions),
                "entity_mention_count": len(extraction.get("entity_mentions", [])),
                "ambiguous_claim_count": len(extraction.get("ambiguous_claims", [])),
                "warning_count": len(extraction.get("extractor_warnings", [])),
                "truth_layer_distribution": GenerationTraceService._truth_layer_distribution(propositions),
            },
            "audit": {
                "passed": audit.get("passed"),
                "commit_blocked": audit.get("commit_blocked"),
                "violation_count": len(audit.get("violations", [])),
                "violation_types": sorted({
                    v.get("type") for v in audit.get("violations", [])
                    if isinstance(v, dict) and v.get("type")
                }),
            },
            "fact_contract": {
                "current_facts_count": len(fact_contract.get("current_facts", [])),
                "reference_facts_count": len(fact_contract.get("reference_facts", [])),
                "forbidden_assertions_count": len(fact_contract.get("forbidden_assertions", [])),
                "required_ambiguities_count": len(fact_contract.get("required_ambiguities", [])),
            },
        }

    @staticmethod
    def _truth_layer_distribution(propositions: list) -> dict:
        """统计命题的 truth_layer 分布。"""
        dist: dict[str, int] = {}
        for p in propositions:
            if isinstance(p, dict):
                layer = p.get("truth_layer", "unknown")
            else:
                layer = getattr(p, "truth_layer", "unknown")
            dist[str(layer)] = dist.get(str(layer), 0) + 1
        return dist

    @staticmethod
    def _build_debug_info(
        context: dict,
        quality_report: dict,
        recovery: dict,
        extraction: dict,
        audit: dict,
        fact_contract: dict,
    ) -> dict:
        """失败时记录完整调试信息，便于复盘。"""
        debug: dict = {}

        # scene_contract 摘要
        scene_contract = context.get("scene_contract", {})
        if isinstance(scene_contract, dict):
            debug["scene_contract_preview"] = {
                "scene_id": scene_contract.get("scene_id", ""),
                "pov_character": scene_contract.get("pov_character", ""),
                "goal": str(scene_contract.get("goal", ""))[:200],
                "must_show_count": len(scene_contract.get("must_show", [])),
                "forbidden_count": len(scene_contract.get("forbidden", [])),
            }

        # scene_provenance 摘要
        provenance = scene_contract.get("scene_provenance", {}) if isinstance(scene_contract, dict) else {}
        if isinstance(provenance, dict):
            current_facts = provenance.get("current_facts", {})
            if isinstance(current_facts, dict):
                debug["scene_provenance_preview"] = {
                    "established_facts_count": len(current_facts.get("established_facts", [])),
                    "completed_events_count": len(current_facts.get("completed_events", [])),
                }

        # fact_contract 摘要
        if fact_contract:
            debug["fact_contract_preview"] = {
                "current_facts": fact_contract.get("current_facts", [])[:5],
                "reference_facts": fact_contract.get("reference_facts", [])[:5],
                "forbidden_assertions_count": len(fact_contract.get("forbidden_assertions", [])),
                "required_ambiguities_count": len(fact_contract.get("required_ambiguities", [])),
            }

        # 命题摘要
        propositions = extraction.get("propositions", [])
        if propositions:
            debug["proposition_summary"] = [
                {
                    "subject": p.get("subject", {}).get("name", "") if isinstance(p.get("subject"), dict) else "",
                    "predicate": p.get("predicate", {}).get("name", "") if isinstance(p.get("predicate"), dict) else "",
                    "truth_layer": p.get("truth_layer", ""),
                    "certainty": p.get("certainty", ""),
                    "source_text": str(p.get("source_text", ""))[:100],
                }
                for p in propositions[:10]
                if isinstance(p, dict)
            ]

        # 审计违规摘要
        violations = audit.get("violations", [])
        if violations:
            debug["audit_violation_summary"] = [
                {
                    "type": v.get("type", ""),
                    "severity": v.get("severity", ""),
                    "blocks_commit": v.get("blocks_commit", False),
                    "target_span": str(v.get("target_span", ""))[:100],
                }
                for v in violations[:10]
                if isinstance(v, dict)
            ]

        # recovery 前后 violation diff
        attempts = recovery.get("attempts", [])
        if attempts:
            debug["recovery_attempt_summary"] = [
                {
                    "round": a.get("round", 0),
                    "strategy": a.get("strategy", ""),
                    "status": a.get("status", ""),
                    "repaired_count": len(a.get("repaired_violation_ids", [])),
                    "introduced_count": len(a.get("introduced_violation_ids", [])),
                }
                for a in attempts[:8]
                if isinstance(a, dict)
            ]

        return debug
