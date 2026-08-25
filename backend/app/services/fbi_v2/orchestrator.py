"""FBI V2 orchestrator.

Quality findings are converted into RepairOrder objects, then executed by
deterministic tools or FBI specialists. After every round the candidate text is
rechecked; resolved finding keys are frozen and only unresolved findings are
carried into the next round. After three unresolved rounds the caller should
open the review workbench instead of falling back to the legacy repair path.
"""
from __future__ import annotations

import hashlib
import logging
import time

from app.models.repair_order import RepairOrder
from app.models.review_finding_v2 import ReviewFindingV2
from app.services.deterministic_repair_engine import TimeAnchorRepairer, get_deterministic_repair_engine
from app.services.llm_gateway import get_llm_gateway
from app.services.quality_finding_hub import get_quality_finding_hub
from app.services.repair_order_compiler import RepairOrderCompiler, get_repair_order_compiler
from app.utils.json_safety import to_json_safe

logger = logging.getLogger(__name__)


class FBIV2RunState:
    """Mutable state for one FBI V2 run."""

    def __init__(self) -> None:
        self.current_text: str = ""
        self.round_num: int = 0
        self.max_rounds: int = 3
        self.orders: list[RepairOrder] = []
        self.completed_orders: list[RepairOrder] = []
        self.all_patches: list[dict] = []
        self.round_results: list[dict] = []
        self.recheck_reports: list[dict] = []
        self.dispatch_reports: list[dict] = []
        self.resolved_finding_ids: list[str] = []
        self.protected_finding_keys: set[str] = set()
        self.unresolved_findings: list[ReviewFindingV2] = []
        self.empty_response_count: int = 0
        self.no_op_count: int = 0
        self.repair_failures: dict[str, int] = {}
        self.protected_spans: list[dict] = []
        self.escalations: list[dict] = []
        self.total_duration_ms: int = 0
        self.last_failure_reason: str = ""


class FBIV2Orchestrator:
    """Three-round automatic repair loop for actionable review findings."""

    async def run(
        self,
        *,
        project_id: str,
        candidate_text: str,
        findings: list[ReviewFindingV2],
        context: dict | None = None,
        max_rounds: int = 3,
    ) -> dict:
        start = time.monotonic()
        context = context or {}

        state = FBIV2RunState()
        state.current_text = candidate_text
        state.max_rounds = max_rounds
        state.unresolved_findings = self._filter_repairable(findings)

        logger.info(
            "[FBI V2] start project=%s findings=%d actionable=%d max_rounds=%d",
            project_id,
            len(findings),
            len(state.unresolved_findings),
            max_rounds,
        )

        if not state.unresolved_findings:
            state.last_failure_reason = "no actionable findings"
            state.total_duration_ms = int((time.monotonic() - start) * 1000)
            return self._build_result(state)

        for round_num in range(1, max_rounds + 1):
            state.round_num = round_num
            before_text = state.current_text

            orders = await self._compile_orders(state.unresolved_findings, state, context)
            if not orders:
                state.last_failure_reason = "no executable repair orders"
                break

            round_result = await self._execute_round(state, orders, context)

            recheck = await self._run_recheck(
                text=state.current_text,
                context=context,
                round_num=round_num,
            )
            state.recheck_reports.append(recheck)

            next_unresolved = self._next_unresolved_findings(
                previous=state.unresolved_findings,
                rechecked_findings=recheck["findings"],
                state=state,
                round_patches=round_result["patches"],
            )
            round_result["recheck_passed"] = not next_unresolved
            round_result["unresolved_after"] = len(next_unresolved)
            round_result["text_hash_before"] = self._hash(before_text)
            round_result["text_hash_after"] = self._hash(state.current_text)
            state.round_results.append(round_result)

            logger.info(
                "[FBI V2] round=%d orders=%d ok=%d failed=%d skipped=%d changed=%s unresolved=%d",
                round_num,
                round_result["orders_total"],
                round_result["orders_succeeded"],
                round_result["orders_failed"],
                round_result["orders_skipped"],
                round_result["changed"],
                len(next_unresolved),
            )

            self._record_repair_effectiveness(
                state=state,
                round_result=round_result,
                next_unresolved=next_unresolved,
            )

            state.unresolved_findings = next_unresolved
            if not state.unresolved_findings:
                break

            if state.current_text == before_text and not round_result["patches"]:
                state.no_op_count += 1
            else:
                state.no_op_count = 0

        if state.unresolved_findings and not state.last_failure_reason:
            state.last_failure_reason = "unresolved findings after max rounds"

        state.total_duration_ms = int((time.monotonic() - start) * 1000)
        return self._build_result(state)

    async def _compile_orders(
        self,
        findings: list[ReviewFindingV2],
        state: FBIV2RunState,
        context: dict,
    ) -> list[RepairOrder]:
        findings = await self._dispatch_findings(findings, state, context)
        state.unresolved_findings = findings
        compiler = get_repair_order_compiler()
        escalated = [self._escalate_finding(finding, state) for finding in findings]
        orders = compiler.compile_batch(escalated)
        return compiler.sort_orders([order for order in orders if order.status == "pending"])

    async def _dispatch_findings(
        self,
        findings: list[ReviewFindingV2],
        state: FBIV2RunState,
        context: dict,
    ) -> list[ReviewFindingV2]:
        from app.services.repair_intent_dispatcher import get_repair_intent_dispatcher

        dispatcher = get_repair_intent_dispatcher()
        dispatched, reports = await dispatcher.dispatch_batch(
            findings,
            candidate_text=state.current_text,
            context=context,
        )
        if reports:
            state.dispatch_reports.extend(reports)
            logger.info(
                "[FBI V2] dispatch reports=%d routed=%d",
                len(reports),
                len([r for r in reports if str(r.get("status", "")).endswith("dispatched")]),
            )
        return dispatched

    async def _execute_round(
        self,
        state: FBIV2RunState,
        orders: list[RepairOrder],
        context: dict,
    ) -> dict:
        round_patches: list[dict] = []
        changed = False
        attempted_issue_ids: set[str] = set()
        patched_issue_ids: set[str] = set()
        no_op_issue_ids: set[str] = set()
        state.orders.extend(orders)

        for order in orders:
            if order.status not in ("pending", "failed"):
                continue
            order.protected_spans = list(state.protected_spans)
            attempted_issue_ids.update(order.issue_ids)

            try:
                if order.is_deterministic():
                    result = await self._execute_deterministic(state, order, context)
                elif order.needs_llm():
                    result = await self._execute_llm(state, order, context)
                else:
                    order.mark_skipped("not automatically repairable")
                    continue
            except Exception as exc:
                logger.warning("[FBI V2] order %s failed: %s", order.id, exc)
                order.mark_failed(str(exc))
                continue

            if result.get("changed"):
                next_text = result.get("text", state.current_text)
                if next_text and next_text != state.current_text:
                    state.current_text = next_text
                    changed = True
                applied = self._enrich_patches(result.get("patches", []), order)
                patched_issue_ids.update(order.issue_ids)
                round_patches.extend(applied)
            elif result.get("acknowledged") or result.get("contract_adjusted"):
                round_patches.extend(self._enrich_patches(result.get("patches", []), order))
            else:
                no_op_issue_ids.update(order.issue_ids)

        state.all_patches.extend(round_patches)
        state.completed_orders.extend(
            [order for order in orders if order.status in ("succeeded", "skipped", "needs_human")]
        )

        return {
            "round": state.round_num,
            "orders_total": len(orders),
            "orders_succeeded": len([o for o in orders if o.status == "succeeded"]),
            "orders_failed": len([o for o in orders if o.status in ("failed", "needs_human")]),
            "orders_skipped": len([o for o in orders if o.status == "skipped"]),
            "patches": round_patches,
            "changed": changed,
            "attempted_issue_ids": sorted(attempted_issue_ids),
            "patched_issue_ids": sorted(patched_issue_ids),
            "no_op_issue_ids": sorted(no_op_issue_ids),
        }

    async def _execute_deterministic(self, state: FBIV2RunState, order: RepairOrder, context: dict) -> dict:
        engine = get_deterministic_repair_engine()
        if not engine.can_handle(order):
            order.mark_skipped("deterministic engine cannot handle order")
            return {"text": state.current_text, "patches": [], "changed": False}
        return await engine.execute(order, state.current_text, context)

    async def _execute_llm(
        self,
        state: FBIV2RunState,
        order: RepairOrder,
        context: dict,
    ) -> dict:
        gateway = get_llm_gateway()
        order.mark_running()

        result = await gateway.generate_patch(
            prompt=self._build_repair_prompt(order, state.current_text, context),
            system=self._build_system_prompt(order),
        )

        if not result.ok:
            if result.error_type == "empty_content":
                state.empty_response_count += 1
            order.mark_failed(result.error_message or "LLM call failed")
            return {"changed": False}

        parsed = result.parsed_json or {}
        status = parsed.get("status", "no_patch")
        patches = parsed.get("patches", [])

        if status == "patched" and patches:
            new_text, applied_patches, skipped = self._apply_patches(
                state.current_text,
                patches,
                order=order,
                protected_spans=state.protected_spans,
            )
            if applied_patches and new_text != state.current_text:
                order.mark_succeeded(applied_patches)
                return {
                    "text": new_text,
                    "patches": applied_patches,
                    "changed": True,
                    "skipped_patches": skipped,
                }
            order.mark_failed("patches did not apply to current text")
            return {"changed": False, "skipped_patches": skipped}

        if status == "no_patch":
            order.mark_skipped(parsed.get("reason", "LLM returned no patch"))
            return {"changed": False}
        if status == "needs_restructure":
            order.mark_failed("scene restructure required")
            return {"changed": False}
        if status == "unsafe":
            order.mark_failed("LLM marked repair unsafe")
            return {"changed": False}

        order.mark_failed(f"unknown patch status: {status}")
        return {"changed": False}

    def _build_system_prompt(self, order: RepairOrder) -> str:
        return (
            "You are an FBI repair specialist inside the Loreweft novel engine.\n"
            f"lane: {order.lane}\n"
            f"scope: {order.target_scope}\n"
            f"operation: {order.operation}\n"
            f"max_delta_chars: {order.allowed_delta_chars}\n\n"
            "Rules:\n"
            "1. Fix only the issue named by this repair order.\n"
            "2. Do not polish the whole scene.\n"
            "3. Preserve facts, identity, timeline, location, and ownership unless the order asks to correct them.\n"
            "4. Return JSON only: status plus patches.\n"
            "5. Every replace_span patch must use target_text that exists verbatim in the current text.\n"
            "6. For rewrite_window, replace only the provided target/window text; do not rewrite the whole scene.\n"
            "7. For insert_bridge_action or insert_bridge_hint, insert a short bridge after the anchor_text.\n"
            "8. A repair is successful only when the patch changes the prose; never return advice without a patch.\n"
            "9. Use rewrite_scene only for POV, forbidden-event chains, or scene-level restructuring.\n"
        )

    def _build_repair_prompt(self, order: RepairOrder, text: str, context: dict) -> str:
        text_preview = text[:2800] if len(text) > 2800 else text
        fact_notes = []
        scene_contract = context.get("scene_contract")
        if isinstance(scene_contract, dict):
            for key in ("goal", "conflict", "ending_state", "pov_lock", "temporal_anchor"):
                value = scene_contract.get(key)
                if value:
                    fact_notes.append(f"{key}: {value}")

        notes = "\n".join(fact_notes[:8])
        return (
            f"repair_order_id: {order.id}\n"
            f"issue_ids: {', '.join(order.issue_ids)}\n"
            f"instruction: {order.instruction}\n"
            f"validator: {order.validator}\n"
            f"operation: {order.operation}\n\n"
            f"target_region:\n{order.target_region or {}}\n\n"
            f"scene_contract_notes:\n{notes or '(none)'}\n\n"
            f"current_text:\n{text_preview}\n\n"
            "Return JSON in this shape:\n"
            "{\n"
            '  "status": "patched",\n'
            '  "reason": "short reason",\n'
            '  "patches": [\n'
            '    {"operation": "replace_span", "target_text": "existing text", "replacement": "new text"}\n'
            "  ]\n"
            "}\n"
            "For rewrite_window, return:\n"
            '{"operation":"rewrite_window","target_text":"existing window text","replacement":"rewritten window"}\n'
            "For bridge insertion, return:\n"
            '{"operation":"insert_after","target_text":"anchor text","insert_text":"bridge sentence"}\n'
            "Allowed patch operations:\n"
            "- replace_span: local repair; target_text must exist verbatim.\n"
            "- rewrite_window: scoped repair; target_text/window_text must exist verbatim.\n"
            "- insert_after: insert bridge prose after target_text/anchor_text.\n"
            "- append_tail: only for missing ending/contract completion.\n"
            "- rewrite_scene: only when the whole scene action chain must be rebuilt.\n"
            "If you cannot produce an applicable patch, return {\"status\":\"no_patch\",\"reason\":\"...\"}.\n"
        )

    def _apply_patches(
        self,
        text: str,
        patches: list[dict],
        *,
        order: RepairOrder | None = None,
        protected_spans: list[dict] | None = None,
    ) -> tuple[str, list[dict], list[dict]]:
        applied: list[dict] = []
        skipped: list[dict] = []
        result_text = text
        protected_spans = protected_spans or []

        for patch in patches:
            operation = patch.get("operation", "")
            if operation in ("replace_all", "rewrite_scene"):
                replacement = (
                    patch.get("replacement")
                    or patch.get("replacement_text")
                    or patch.get("text")
                    or patch.get("new_text")
                    or ""
                )
                if replacement and replacement.strip() and replacement != result_text:
                    applied_patch = dict(patch)
                    applied_patch["operation"] = "rewrite_scene"
                    applied_patch["base_text_hash"] = self._hash(result_text)
                    result_text = replacement
                    applied.append(applied_patch)
                else:
                    skipped.append({**patch, "reason": "empty or unchanged rewrite"})
                continue

            if operation in ("replace_span", "rewrite_window"):
                target = (
                    patch.get("target_text")
                    or patch.get("window_text")
                    or patch.get("before")
                    or patch.get("original_text")
                    or ""
                )
                replacement = patch.get("replacement", patch.get("replacement_text", patch.get("after", "")))
                start = patch.get("start")
                end = patch.get("end")
                span_start = -1
                span_end = -1
                if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(result_text):
                    if result_text[start:end] == target:
                        span_start, span_end = start, end
                if span_start < 0 and target and target in result_text:
                    span_start = result_text.index(target)
                    span_end = span_start + len(target)
                if (
                    target
                    and span_start >= 0
                    and replacement != target
                    and not self._overlaps_protected(
                        span_start,
                        span_end,
                        protected_spans,
                        owner_issue_ids=order.issue_ids if order else [],
                    )
                ):
                    result_text = result_text[:span_start] + replacement + result_text[span_end:]
                    applied_patch = dict(patch)
                    applied_patch["operation"] = operation
                    applied_patch.setdefault("target_text", target)
                    applied_patch.setdefault("replacement", replacement)
                    applied_patch["start"] = span_start
                    applied_patch["end"] = span_end
                    applied_patch.setdefault("base_text_hash", self._hash(text))
                    applied.append(applied_patch)
                else:
                    skipped.append({**patch, "reason": "target not found or unchanged"})
                continue

            if operation == "delete_span":
                target = patch.get("target_text") or patch.get("before") or patch.get("deleted") or ""
                start = patch.get("start")
                end = patch.get("end")
                span_start = -1
                span_end = -1
                if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(result_text):
                    if not target or result_text[start:end] == target:
                        span_start, span_end = start, end
                        target = result_text[span_start:span_end]
                if span_start < 0 and target and target in result_text:
                    span_start = result_text.index(target)
                    span_end = span_start + len(target)
                if (
                    target
                    and span_start >= 0
                    and not self._overlaps_protected(
                        span_start,
                        span_end,
                        protected_spans,
                        owner_issue_ids=order.issue_ids if order else [],
                    )
                ):
                    result_text = result_text[:span_start] + result_text[span_end:]
                    applied_patch = dict(patch)
                    applied_patch.setdefault("target_text", target)
                    applied_patch.setdefault("replacement", "")
                    applied_patch.setdefault("before", target)
                    applied_patch.setdefault("after", "")
                    applied_patch["start"] = span_start
                    applied_patch["end"] = span_end
                    applied_patch.setdefault("base_text_hash", self._hash(text))
                    applied.append(applied_patch)
                else:
                    skipped.append({**patch, "reason": "delete target not found or protected"})
                continue

            if operation in ("insert_after", "insert_bridge_action", "insert_bridge_hint", "insert_bridge"):
                target = patch.get("target_text") or patch.get("anchor_text") or ""
                insert = patch.get("insert_text", patch.get("replacement", patch.get("bridge_text", "")))
                if target and target in result_text and insert:
                    index = result_text.index(target) + len(target)
                    if not self._overlaps_protected(
                        index,
                        index,
                        protected_spans,
                        owner_issue_ids=order.issue_ids if order else [],
                    ):
                        result_text = result_text[:index] + insert + result_text[index:]
                        applied_patch = dict(patch)
                        applied_patch["operation"] = "insert_after"
                        applied_patch.setdefault("target_text", target)
                        applied_patch.setdefault("insert_text", insert)
                        applied_patch.setdefault("replacement", insert)
                        applied_patch.setdefault("before", "")
                        applied_patch.setdefault("after", insert)
                        applied_patch["start"] = index
                        applied_patch["end"] = index
                        applied_patch.setdefault("base_text_hash", self._hash(text))
                        applied.append(applied_patch)
                    else:
                        skipped.append({**patch, "reason": "insert target protected"})
                else:
                    skipped.append({**patch, "reason": "insert target not found"})
                continue

            if operation == "append_tail":
                append = patch.get("append_text", patch.get("replacement", ""))
                if append:
                    insert_at = len(result_text.rstrip())
                    result_text = result_text.rstrip() + "\n\n" + append.strip()
                    applied_patch = dict(patch)
                    applied_patch.setdefault("append_text", append)
                    applied_patch.setdefault("replacement", append)
                    applied_patch.setdefault("before", "")
                    applied_patch.setdefault("after", append)
                    applied_patch["start"] = insert_at
                    applied_patch["end"] = insert_at
                    applied.append(applied_patch)
                else:
                    skipped.append({**patch, "reason": "empty append"})
                continue

            skipped.append({**patch, "reason": f"unsupported operation: {operation}"})

        return result_text, applied, skipped

    def _enrich_patches(self, patches: list[dict], order: RepairOrder) -> list[dict]:
        enriched: list[dict] = []
        for patch in patches or []:
            if not isinstance(patch, dict):
                continue
            item = dict(patch)
            item.setdefault("order_id", order.id)
            item.setdefault("issue_ids", list(order.issue_ids))
            item.setdefault("affected_issue_ids", list(order.issue_ids))
            item.setdefault("requires_recheck", True)
            enriched.append(item)
        return enriched

    @staticmethod
    def _overlaps_protected(
        start: int,
        end: int,
        protected_spans: list[dict],
        *,
        owner_issue_ids: list[str] | None = None,
    ) -> bool:
        owner_set = set(owner_issue_ids or [])
        for protected in protected_spans:
            if not isinstance(protected, dict):
                continue
            protected_owners = set(protected.get("owner_issue_ids") or protected.get("issue_ids") or [])
            if owner_set and protected_owners & owner_set:
                continue
            p_start = protected.get("start")
            p_end = protected.get("end")
            if isinstance(p_start, int) and isinstance(p_end, int) and start < p_end and p_start < end:
                return True
        return False

    def _record_repair_effectiveness(
        self,
        *,
        state: FBIV2RunState,
        round_result: dict,
        next_unresolved: list[ReviewFindingV2],
    ) -> None:
        unresolved_ids = {finding.id for finding in next_unresolved}
        attempted_ids = set(round_result.get("attempted_issue_ids") or [])
        no_op_ids = set(round_result.get("no_op_issue_ids") or [])

        for issue_id in attempted_ids:
            if issue_id not in unresolved_ids:
                state.repair_failures.pop(issue_id, None)
                continue
            state.repair_failures[issue_id] = state.repair_failures.get(issue_id, 0) + 1

    def _escalate_finding(self, finding: ReviewFindingV2, state: FBIV2RunState) -> ReviewFindingV2:
        failures = state.repair_failures.get(finding.id, 0)
        if failures <= 0:
            return finding

        if (
            failures == 1
            and finding.repair_lane in {"fbi_fact", "fbi_prose", "fbi_ending", "fbi_scene_restructure"}
            and finding.type in self._deterministic_preflight_types()
            and RepairOrderCompiler.PREFLIGHT_SKIP_MARKER not in (finding.description or "")
        ):
            reason = "deterministic preflight produced no effective patch; retry original specialist"
            escalation = {
                "finding_id": finding.id,
                "type": finding.type,
                "from_lane": "deterministic_preflight",
                "to_lane": finding.repair_lane,
                "failures": failures,
                "round": state.round_num,
                "reason": reason,
            }
            if escalation not in state.escalations:
                state.escalations.append(escalation)
            return finding.model_copy(update={
                "max_attempts": max(finding.max_attempts, 2),
                "description": (
                    f"{finding.description}\n"
                    f"{RepairOrderCompiler.PREFLIGHT_SKIP_MARKER} {reason}"
                ),
            })

        target_lane = finding.repair_lane
        target_scope = finding.repair_scope
        reason = ""
        if finding.repair_lane == "deterministic":
            if finding.type in {
                "ai_punctuation_artifact",
                "explanatory_punctuation_artifact",
                "narration_explanation_artifact",
                "enforced_ai_flavor",
                "emotion_expression_monotone",
                "ai_style_artifact",
                "explanation_monotone",
            }:
                target_lane = "fbi_prose"
            elif finding.type in {"ending_state_not_reached", "missing_must_show"}:
                target_lane = "fbi_ending"
            elif finding.type in {"timeline_conflict", "temporal_conflict"}:
                target_lane = "fbi_fact" if failures == 1 else "fbi_scene_restructure"
            else:
                target_lane = "fbi_fact"
            reason = "deterministic repair produced no effective patch"
        elif finding.repair_lane in {"fbi_fact", "fbi_prose", "fbi_ending", "fbi_pacing"} and failures >= 1:
            target_lane = "fbi_scene_restructure"
            reason = f"{finding.repair_lane} did not clear the finding"

        if target_lane == finding.repair_lane and target_scope == finding.repair_scope:
            return finding

        escalation = {
            "finding_id": finding.id,
            "type": finding.type,
            "from_lane": finding.repair_lane,
            "to_lane": target_lane,
            "failures": failures,
            "round": state.round_num,
            "reason": reason,
        }
        if escalation not in state.escalations:
            state.escalations.append(escalation)
        return finding.model_copy(update={
            "repair_lane": target_lane,
            "repair_scope": target_scope,
            "max_attempts": max(finding.max_attempts, 2),
            "description": f"{finding.description}\n[repair_escalation] {reason}",
        })

    @staticmethod
    def _deterministic_preflight_types() -> set[str]:
        return {
            "forbidden_assertion_triggered",
            "forbidden_recap_violation",
            "forbidden_content",
            "forbidden_event",
            "ending_state_not_reached",
            "missing_must_show",
            "fact_conflict",
            "internal_conflict",
            "identity_conflict",
            "setting_conflict",
            "spatial_conflict",
            "ownership_conflict",
            "causal_chain_error",
            "fact_boundary_conflict",
            "knowledge_boundary_violation",
            "truth_layer_conflict",
            "responsibility_polarity_conflict",
            "narration_explanation_artifact",
            "enforced_ai_flavor",
            "emotion_expression_monotone",
            "ai_style_artifact",
            "explanation_monotone",
        }

    async def _run_recheck(self, *, text: str, context: dict, round_num: int) -> dict:
        from app.services.quality_gate import QualityGate

        qg_context = dict(context)
        qg_context["generated_text"] = text

        report = await QualityGate().evaluate(qg_context, level="full")
        violations = self._pre_gate_violations(text, context) + list(report.get("violations", []) or [])

        deslop_reports = (report.get("reports") or {}).get("deslop_gate", {})
        deslop_findings = (
            deslop_reports.get("review_findings", [])
            if isinstance(deslop_reports, dict)
            else []
        )

        hub = get_quality_finding_hub()
        findings = hub.normalize_all(violations=violations, deslop_findings=deslop_findings)
        actionable = self._filter_repairable(findings)

        blocking_count = len([v for v in violations if isinstance(v, dict) and v.get("blocks_commit")])
        merged_report = dict(report)
        merged_report["violations"] = violations
        merged_report["passed"] = blocking_count == 0 and report.get("passed", False)
        merged_report["commit_blocked"] = blocking_count > 0

        return {
            "round": round_num,
            "passed": len(actionable) == 0,
            "violations": violations,
            "findings": actionable,
            "report": merged_report,
        }

    def _pre_gate_violations(self, text: str, context: dict) -> list[dict]:
        violations: list[dict] = []
        violations.extend(TimeAnchorRepairer.detect_conflicts(text, context))

        hard_max = 0
        word_budget = context.get("word_budget")
        if isinstance(word_budget, dict):
            hard_max = int(word_budget.get("hard_max_chars") or 0)
        if hard_max > 0 and len(text) > hard_max:
            detail = f"scene length {len(text)} exceeds hard_max_chars {hard_max}"
            violations.append({
                "violation_id": self._violation_id("scene_too_long", detail),
                "source": "deterministic",
                "type": "scene_too_long",
                "severity": "high",
                "detail": detail,
                "suggested_strategy": "patch_text",
                "blocks_commit": True,
                "scope": "prose_text",
                "current_length": len(text),
                "hard_max_chars": hard_max,
            })

        scene_contract = context.get("scene_contract")
        forbidden = scene_contract.get("forbidden", []) if isinstance(scene_contract, dict) else []
        if isinstance(forbidden, list):
            for item in forbidden:
                if isinstance(item, str) and item and item in text:
                    detail = f"text contains forbidden content: {item[:80]}"
                    violations.append({
                        "violation_id": self._violation_id("forbidden_triggered", detail),
                        "source": "narrative_contract",
                        "type": "forbidden_triggered",
                        "severity": "high",
                        "detail": detail,
                        "target_span": item,
                        "suggested_strategy": "patch_text",
                        "blocks_commit": True,
                        "scope": "prose_text",
                    })

        return violations

    def _next_unresolved_findings(
        self,
        *,
        previous: list[ReviewFindingV2],
        rechecked_findings: list[ReviewFindingV2],
        state: FBIV2RunState,
        round_patches: list[dict],
    ) -> list[ReviewFindingV2]:
        rechecked_keys = {self._finding_key(finding) for finding in rechecked_findings}

        for finding in previous:
            key = self._finding_key(finding)
            if key not in rechecked_keys:
                if finding.id not in state.resolved_finding_ids:
                    state.resolved_finding_ids.append(finding.id)
                state.protected_finding_keys.add(key)
                self._freeze_patch_spans_for_finding(state, finding, round_patches)

        return rechecked_findings

    def _freeze_patch_spans_for_finding(
        self,
        state: FBIV2RunState,
        finding: ReviewFindingV2,
        patches: list[dict],
    ) -> None:
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            affected_ids = set(patch.get("affected_issue_ids") or patch.get("issue_ids") or [])
            if affected_ids and finding.id not in affected_ids:
                continue
            start = patch.get("start")
            end = patch.get("end")
            after = patch.get("after") or patch.get("replacement") or patch.get("replacement_text") or ""
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            frozen = {
                "span_id": f"v2ps_{finding.id}_{len(state.protected_spans)}",
                "finding_id": finding.id,
                "owner_issue_ids": [finding.id],
                "start": start,
                "end": start + len(str(after)) if after else end,
                "repair_lane": finding.repair_lane,
                "type": finding.type,
                "reason": "resolved_issue",
                "round": state.round_num,
            }
            if frozen not in state.protected_spans:
                state.protected_spans.append(frozen)

    @staticmethod
    def _filter_repairable(findings: list[ReviewFindingV2]) -> list[ReviewFindingV2]:
        return [
            finding for finding in findings
            if finding.blocks_commit
            and (
                finding.is_actionable()
                or (
                    finding.repair_scope == "prose_text"
                    and finding.repair_lane in {"none", "manual_only"}
                    and finding.user_visible
                )
            )
        ]

    @staticmethod
    def _finding_key(finding: ReviewFindingV2) -> str:
        return f"{finding.type}:{finding.repair_scope}"

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5((text or "").encode()).hexdigest()[:12]

    @staticmethod
    def _violation_id(vtype: str, detail: str) -> str:
        return hashlib.md5(f"{vtype}:{detail}".encode()).hexdigest()[:12]

    def _build_result(self, state: FBIV2RunState) -> dict:
        succeeded = [order for order in state.orders if order.status == "succeeded"]
        failed = [order for order in state.orders if order.status in ("failed", "needs_human")]
        skipped = [order for order in state.orders if order.status == "skipped"]
        unresolved = state.unresolved_findings

        if not state.orders and unresolved:
            status = "failed"
        elif unresolved:
            status = "needs_workbench"
        else:
            status = "resolved"

        return to_json_safe({
            "status": status,
            "text": state.current_text,
            "patches": state.all_patches,
            "round_results": state.round_results,
            "recheck_reports": state.recheck_reports,
            "dispatch_reports": state.dispatch_reports,
            "resolved_finding_ids": state.resolved_finding_ids,
            "unresolved_findings": [finding.model_dump() for finding in unresolved],
            "protected_finding_keys": sorted(state.protected_finding_keys),
            "protected_spans": state.protected_spans,
            "escalations": state.escalations,
            "summary": {
                "total_orders": len(state.orders),
                "succeeded": len(succeeded),
                "failed": len(failed),
                "skipped": len(skipped),
                "rounds_used": state.round_num,
                "empty_responses": state.empty_response_count,
                "no_op_rounds": state.no_op_count,
                "repair_failures": state.repair_failures,
                "dispatch_count": len(state.dispatch_reports),
                "duration_ms": state.total_duration_ms,
            },
            "needs_human": [finding.id for finding in unresolved],
            "failure_reasons": [state.last_failure_reason] if state.last_failure_reason else [],
        })


_orchestrator: FBIV2Orchestrator | None = None


def get_fbi_v2_orchestrator() -> FBIV2Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = FBIV2Orchestrator()
    return _orchestrator
