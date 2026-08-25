from __future__ import annotations

import hashlib
import re

from app.agents.base import BaseAgent
from app.services.json_response import parse_json_response
from app.services.scene_provenance import render_scene_provenance
from app.services.scene_truth_snapshot import render_truth_snapshot, chars_to_max_tokens
from app.services.text_coercion import to_text
from app.services.llm_task_profiles import LLMTaskType
from app.services.llm_client import build_prompt_cache_policy


# ---------------------------------------------------------------------------
# Violation types that are lightweight enough for inline patching
# ---------------------------------------------------------------------------
_INLINE_VIOLATION_TYPES = frozenset({
    "ai_flavor",
    "ai_taste",
    "style",
    "repetition",
    "pacing",
    "expression_quality",
    "cliché",
    "purple_prose",
    "telling_not_showing",
})

_INLINE_SEVERITY = frozenset({"low", "info"})

# Maximum number of inline patches per call
_MAX_INLINE_PATCHES = 2
_MAX_CHANGED_RATIO = 0.5


class SceneRepairer(BaseAgent):
    """Merged repair agent that replaces auto_repair + scene_rewrite + ai_quality_inline_reviser.

    Automatically selects a repair strategy based on violation severity:

    - ``inline``  : lightweight AI-flavor / style issues (severity low/info or
      violation type in _INLINE_VIOLATION_TYPES).  Applies small local patches
      without rewriting the full text.
    - ``patch``   : medium-severity violations.  Sends the full text to the LLM
      with targeted repair instructions and gets back a repaired version.
    - ``rewrite`` : high / critical severity, or when the caller explicitly
      requests a full scene rewrite.  Regenerates the scene from the contract.
    """

    name = "scene_repairer"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(self, context: dict) -> dict:
        generated_text = context.get("generated_text", "")
        violations = context.get("violations", [])
        conflicts = context.get("conflicts", [])
        scene_contract = context.get("scene_contract", {})
        rejected_text = context.get("rejected_text")

        # Explicit strategy override from caller
        explicit_strategy = context.get("repair_strategy")

        # Determine strategy automatically when not overridden
        if explicit_strategy:
            strategy = explicit_strategy
        elif rejected_text:
            # When there is a rejected text the caller wants a full rewrite
            strategy = "rewrite"
        elif conflicts:
            strategy = "patch"
        elif violations:
            strategy = self._classify_strategy(violations)
        else:
            # Nothing to repair
            return self._success_result(generated_text)

        if strategy == "inline":
            return await self._inline_repair(context)
        if strategy == "patch":
            if conflicts:
                return await self._patch_from_conflicts(generated_text, conflicts, context)
            return await self._patch_from_violations(generated_text, violations, scene_contract, context)
        # strategy == "rewrite"
        return await self._full_rewrite(context)

    # ------------------------------------------------------------------
    # Strategy classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_strategy(violations: list) -> str:
        """Decide *inline* vs *patch* based on the worst violation."""
        has_medium_or_high = False
        all_inline = True

        for v in violations:
            if not isinstance(v, dict):
                continue
            severity = (v.get("severity") or "").lower()
            vtype = (v.get("type") or "").lower()

            if severity in ("high", "critical"):
                return "patch"
            if severity in _INLINE_SEVERITY and vtype in _INLINE_VIOLATION_TYPES:
                continue
            if vtype in _INLINE_VIOLATION_TYPES:
                continue
            # Any non-inline violation at medium severity → patch
            if severity == "medium" or severity not in _INLINE_SEVERITY:
                all_inline = False
                has_medium_or_high = True

        if has_medium_or_high:
            return "patch"
        if all_inline and violations:
            return "inline"
        return "patch"

    # ------------------------------------------------------------------
    # Inline repair  (from ai_quality_inline_reviser)
    # ------------------------------------------------------------------

    async def _inline_repair(self, context: dict) -> dict:
        draft_text = context.get("generated_text") or context.get("draft_text") or ""
        hints = [
            hint for hint in context.get("revision_hints", [])
            if isinstance(hint, dict) and hint.get("auto_revise_allowed")
        ][:_MAX_INLINE_PATCHES]

        # Also build hints from violations when revision_hints are absent
        if not hints:
            hints = self._violations_to_hints(context.get("violations", []))
        if not draft_text or not hints:
            return self._success_result(draft_text, strategy="inline")

        try:
            llm = await self.get_llm_client()
            _inline_sys = self._inline_system_prompt()
            _cache_policy, _stable_hash = build_prompt_cache_policy(_inline_sys)
            response = await llm.generate(
                system_prompt=_inline_sys,
                user_prompt=self._inline_user_prompt(context, hints),
                temperature=0.25,
                # This returns creative replacement prose, and reasoning
                # models can consume the 2K SHORT_EXTRACTION ceiling before
                # emitting JSON content.  Keep the small local input window,
                # but use the existing FBI repair output budget.
                task_type=LLMTaskType.FBI_REPAIR,
                cache_policy=_cache_policy,
                stable_prefix_hash=_stable_hash,
            )
            payload = parse_json_response(response)
        except Exception as exc:
            return self._error_result(draft_text, f"inline_llm_error: {exc}", strategy="inline")

        patches = payload.get("patches", []) if isinstance(payload, dict) else []
        revised_text, applied, skipped = self.apply_patches(
            draft_text,
            patches,
            forbidden_terms=self._forbidden_terms(context.get("scene_contract", {})),
            allowed_short_originals=[context.get("bounded_target_span", "")]
            if context.get("bounded_semantic_repair") else None,
        )
        success = bool(applied) and revised_text != draft_text
        return {
            "success": success,
            "repaired_text": revised_text if success else draft_text,
            "repairs": applied,
            "applied_patches": applied,
            "skipped_patches": skipped,
            "strategy": "inline",
            "error": "" if success else "no_valid_local_patch",
        }

    # -- inline helpers --

    @staticmethod
    def _inline_system_prompt() -> str:
        return (
            "You are a local fiction prose revision agent. Return JSON only. "
            "Do not rewrite the whole scene. Produce at most two local patches. "
            "Each patch must contain original, replacement, and reason. "
            "The original value must be a continuous substring from CURRENT TEXT. "
            "Prefer copying the target_span/location text exactly into original. "
            "The replacement may add the minimum causal or transitional bridge explicitly requested by the issue, "
            "but it must not change plot facts, foreshadowing, character outcomes, or settings. "
            "Do not introduce new similes, metaphors, abstract summaries, or unrelated embellishment."
        )

    @staticmethod
    def _inline_user_prompt(context: dict, hints: list[dict]) -> str:
        scene_contract = context.get("scene_contract", {})
        contract_lines = []
        if isinstance(scene_contract, dict):
            for key in ("goal", "conflict", "ending_state"):
                if scene_contract.get(key):
                    contract_lines.append(f"{key}: {scene_contract.get(key)}")
            for key in ("must_show", "forbidden"):
                value = scene_contract.get(key)
                if value:
                    contract_lines.append(f"{key}: {value}")

        hint_lines = []
        for hint in hints:
            hint_lines.append(
                f"- 问题：{hint.get('problem', '')}；位置：{hint.get('target_span', '')}；"
                f"策略：{hint.get('strategy', '')}；必须保留：{hint.get('preserve', [])}"
            )

        draft = (
            context.get("inline_window_text")
            or context.get("generated_text")
            or context.get("draft_text")
            or ""
        )
        draft_label = "当前局部正文窗口" if context.get("inline_window_text") else "当前正文"
        bounded_rule = ""
        read_only_context = ""
        bounded_target = str(context.get("bounded_target_span") or "").strip()
        if context.get("bounded_semantic_repair") and bounded_target:
            bounded_rule = (
                "\n\n### 局部语义修订硬约束\n"
                f"patch.original 必须逐字等于目标锚点：{bounded_target}\n"
                "patch.replacement 必须保留该锚点原文，只允许在它之前或之后增加最多两句必要的因果/过渡文字。"
                "不得改写窗口中的其他句子。"
            )
        elif context.get("bounded_cross_scene_consolidation"):
            protected = context.get("external_protected_span") or {}
            protected_span = str(protected.get("span") or "").strip()
            protected_scene = protected.get("scene_index")
            bounded_rule = (
                "\n\n### 跨场景重复内容收束硬约束\n"
                "- 只有「当前局部正文窗口」可写；后文只读上下文不得修改。\n"
                "- patch.original 必须是可写窗口中逐字存在的连续片段，且不得来自只读上下文。\n"
                "- 只删除或压缩可写场景中过早出现、与后文重复的事件；"
                "replacement 可为空字符串，或仅保留必要的衔接句。\n"
                "- 不得重写整个场景，不得修改窗口外文字，不得把只读后文复制进可写场景。\n"
                f"- 必须保留只读场景 {protected_scene} 中的目标原文：{protected_span}"
            )
            external_text = str(context.get("external_protected_context_text") or "").strip()
            if external_text:
                read_only_context = (
                    "\n\n### 只读后文上下文（仅用于判断重复，禁止返回对它的补丁）\n"
                    + external_text
                )
        skill_contract = SceneRepairer._skill_contract_prompt(context)
        return (
            skill_contract
            +
            "### 场景合同摘要\n"
            + ("\n".join(contract_lines) or "无")
            + "\n\n### 需要局部处理的问题\n"
            + "\n".join(hint_lines)
            + bounded_rule
            + read_only_context
            + f"\n\n### {draft_label}\n"
            + draft
            + "\n\n只输出 JSON："
            + '{"patches":[{"original":"正文中逐字存在的片段","replacement":"替换后的片段","reason":"修改原因"}]}'
        )

    @staticmethod
    def _violations_to_hints(violations: list) -> list[dict]:
        hints = []
        for v in violations:
            if not isinstance(v, dict):
                continue
            vtype = v.get("type", "")
            severity = v.get("severity", "")
            if severity not in _INLINE_SEVERITY and vtype not in _INLINE_VIOLATION_TYPES:
                continue
            hints.append({
                "problem": v.get("detail", vtype),
                "target_span": v.get("target_span", ""),
                "strategy": v.get("expected_behavior", "替换为更自然的表达"),
                "preserve": [],
                "auto_revise_allowed": True,
            })
            if len(hints) >= _MAX_INLINE_PATCHES:
                break
        return hints

    # ------------------------------------------------------------------
    # Patch repair  (from auto_repair)
    # ------------------------------------------------------------------

    async def _patch_from_violations(
        self,
        generated_text: str,
        violations: list,
        scene_contract: dict,
        context: dict,
    ) -> dict:
        if not violations or not generated_text:
            return self._success_result(generated_text, strategy="patch")

        violation_descriptions = []
        for v in violations:
            if not isinstance(v, dict):
                continue
            vtype = v.get("type", "unknown")
            severity = v.get("severity", "high")
            detail = v.get("detail", "")
            target_span = v.get("target_span", "")
            expected = v.get("expected_behavior", "")

            desc = f"- [{severity.upper()}] {vtype}：{detail}"
            if target_span:
                desc += f"\n  正文相关片段：「{target_span}」"
            if expected:
                desc += f"\n  期望行为：{expected}"
            # 通用修复 S-1：密度类 / 多位置类问题携带 evidence_samples，
            # 让 LLM 看到所有需要处理的位置（而非只 target_span 1 处），
            # 配合 fbi_prose lane 指令一次性系统性降低密度。
            # evidence_samples 可能存在三个位置：
            #   1. violation 顶层（advisory 直接转化时）
            #   2. violation["evidence"]["evidence_samples"]（quality_gate enforcement 路径）
            #   3. violation["evidence"]["advisory"]["evidence_samples"]（quality_memory_candidate 路径，
            #      advisory 原样嵌套在 evidence.advisory 下）
            evidence_samples = v.get("evidence_samples")
            if not evidence_samples:
                ev = v.get("evidence")
                if isinstance(ev, dict):
                    evidence_samples = ev.get("evidence_samples")
                    if not evidence_samples:
                        advisory = ev.get("advisory")
                        if isinstance(advisory, dict):
                            evidence_samples = advisory.get("evidence_samples")
            if isinstance(evidence_samples, list) and evidence_samples:
                desc += "\n  命中位置清单（需系统性处理，禁止只改 target_span 一处）："
                for idx, sample in enumerate(evidence_samples[:15], 1):
                    desc += f"\n    {idx}. 「{sample}」"
            violation_descriptions.append(desc)

        forbidden = scene_contract.get("forbidden", [])
        must_show = scene_contract.get("must_show", [])

        source_of_truth = scene_contract.get("source_of_truth", {})
        if isinstance(source_of_truth, dict):
            outline_forbidden = source_of_truth.get("forbidden_outline", [])
            outline_must_show = source_of_truth.get("must_show_outline", [])
            if outline_forbidden:
                forbidden = list(set(forbidden + outline_forbidden))
            if outline_must_show:
                must_show = list(set(must_show + outline_must_show))

        force_change = bool(context.get("force_change"))
        force_change_rule = (
            "\n9. 本轮是用户明确触发的智能修订；如果问题属于文风、节奏、体验、标点、重复或表达质量，"
            "必须在不改变事实和事件的前提下至少完成一处最小局部改写。"
            "缺少正文相关片段时，请自行从原文中选择最相关的1-3句处理，不要直接返回原文。"
            if force_change
            else ""
        )

        system_prompt = (
            "你是一个小说文本定向修复专家。你的任务是精准修正正文中的违规问题。\n\n"
            "规则：\n"
            "1. 只修复下方列出的违规，不要改动其他内容。"
            "但密度类问题（如 ai_simile_overuse / ai_punctuation_artifact / ai_emotion_label 等含 evidence_samples 的违规）除外——"
            "对此类问题，必须系统性处理 evidence_samples 中列出的所有命中位置，"
            "每处改为直接动作描写或感官描写，保持原句叙事信息不丢失，禁止只改 target_span 一处。\n"
            "2. 修复必须自然流畅，不能留下修改痕迹\n"
            "3. 不得改变场景合同中的 source_of_truth（目标、冲突、大纲结果方向等不可变字段）\n"
            "4. 不得新增与已确立事实无关的额外角色、线索和事件；但补充已确立事实中明确要求的必要内容（如缺失的人物状态描述、线索来源说明、时间锚点、法器属性等）不算违规\n"
            "5. 必须遵守当前不可变事实快照中的位置、时间、角色状态和已完成事件\n"
            "6. 不得将角色写回已经离开的地点，不得重演已完成事件\n"
            "7. 对于'未提及/未说明/未明确/缺失'类违规（即正文缺少已确立事实要求的必要内容），请在正文最自然的位置补充 expected_behavior 中建议的最小必要内容（如一句简短内心活动、一句对话、一个细节描写），使正文与已确立事实一致；不得用原样返回代替修复。仅在完全无法理解违规含义或 expected_behavior 完全未提供修复方向时，才返回原文不做修改\n"
            "8. 修复时不得引入新的违规——这是铁律。常见错误：\n"
            "   - 为抽象陈述补充证据时，不得新增非 POV 角色的内心直写（head hopping）\n"
            "   - 为情绪标签改写时，不得新增孤立抽象论断（如「这只能说明一件事」「她心里明白」）\n"
            "   - 为身体语言改写时，不得新增情绪标签或抽象总结\n"
            "   - 补充内容时必须保持 POV 锁定，只描写 POV 角色能感知的内容\n"
            "   - 补充的句子必须搭配具体证据（感官/动作/对话），不得只有抽象陈述\n"
            "9. 严格遵守 POV 锁定：只描写 POV 角色能感知的内容，不得直写非 POV 角色的内心。\n"
            "   修复时新增的内心活动、记忆、推断必须属于 POV 角色。把非 POV 心理改为外部证据时，"
            "必须保留原句中的动作对象、感官结果和因果信息，不能只换成『动作顿了一下』之类空壳。"
            "改写完成后逐句通读，确保每个替换片段都是语法完整的现代汉语句子；"
            "禁止出现『顿了一下了』『停了一下了』等助词叠加，也不得留下被截断句子的程度补语。\n"
            "10. 严格遵守抽象-证据配对：修复时新增的句子若含抽象词（说明/意味着/意识到/明白/知道/重要/特殊等），"
            "必须搭配具体证据（感官描写/动作描写/对话），不得出现孤立抽象论断。\n"
            '11. 返回 JSON 格式：{"repaired_text": "修复后的完整文本", "repairs": [{"violation_type": "违规类型", "violation_id": "违规ID", "original": "原文片段", "repaired": "修复后片段", "reason": "修复原因"}]}'
            f"{force_change_rule}"
            f"{self._lane_system_instruction(context)}"
        )
        system_prompt += self._skill_contract_system_instruction(context)

        user_prompt = self._build_patch_user_prompt(
            context, generated_text, violation_descriptions,
            forbidden, must_show, force_change, source_of_truth,
        )

        return await self._call_llm_and_parse(
            generated_text, system_prompt, user_prompt, strategy="patch",
        )

    @staticmethod
    def _lane_system_instruction(context: dict) -> str:
        lane = str(context.get("repair_lane") or "").strip()
        strength = str(context.get("repair_strength") or "").strip()
        if not lane:
            return ""
        lane_rules = {
            "deterministic_surface_cleanup": (
                "专科修复：只处理标点、破折号、重复符号和表层 AI 味，不改事实与句群结构。"
            ),
            # 通用修复（循环 #1）：prose_local_patch 补充"补充证据"语义
            # 根因：原指令只说"降低抽象总结"（删减导向），但 abstract_bare_count 的
            #   action 是 pair_abstract_with_evidence（补充证据），两者语义冲突。
            #   LLM 按删减指令操作后可能删除抽象句，触发 missing_must_show 等新违规。
            # 修复：补充"为抽象陈述补充感官/动作/对话证据"语义，与删减语义并存。
            # 通用性：适用于所有题材——具象化要求在所有题材中一致。
            "prose_local_patch": (
                "专科修复：做词句级局部改写，降低句壳、解释性套话和抽象总结，不重排段落。"
                "若修复目标是 abstract_bare_count，必须为抽象陈述补充感官描写、动作描写或对话证据，"
                "不得删除抽象句本身——让抽象词与具体证据共存。"
                # 循环#12：standalone_abstract_claims 的专科处理指令
                # 根因：prose_local_patch lane 指令只提到 abstract_bare_count/body_language_cliche_count/
                #   emotion_label_count，未提及 standalone_abstract_claims。LLM 收到该 violation 后
                #   无具体修复指导，只收到泛泛的"做词句级局部改写"，导致修复后 recheck 仍判定不通过。
                # 修复策略：为含抽象词的孤立句子补充具体证据（感官/动作/对话），使抽象词与具体证据共存。
                # 通用性：所有题材的孤立抽象论断都需要补充具体证据。
                "若修复目标是 standalone_abstract_claims（孤立抽象论断——句子含抽象词如"
                "「意义/命运/本质/灵魂/复杂/关系/气氛/局势/压抑/尴尬/重要/特殊/象征/代表/体现/"
                "说明/意味着/预示」但缺少具体感官词、动作词或对话），"
                "必须为该句补充具体证据——在句中或紧邻位置插入感官描写（看见/听见/触摸到等）、"
                "动作描写（推/拉/放下/抬起/按住/攥住/退后/转身等）或对话引号内容，"
                "使抽象词与具体证据共存。不得删除抽象词本身，也不得只改 target_span 一处。"
                "若违规含 evidence_samples（命中句子清单），必须系统性处理清单中的所有句子。"
                "若修复目标是 body_language_cliche_count，必须将身体语言陈词滥调"
                "（握紧拳头/咬唇/皱眉/叹气/心跳加速/呼吸急促/眼中闪过/嘴角上扬/"
                "苦笑/冷笑/身体一僵/浑身发抖/瞳孔一缩/喉结滚动/指节泛白/脸色苍白等模板化短语）"
                "改写为具体的、可观察的外部行为描写——用特定的动作细节或环境互动替代模板化身体反应。"
                # 循环#12：增强 body_language_cliche_count 的自检要求
                # 根因：LLM 改写了 target_span 位置的陈词滥调，但在修复文本的其他位置
                #   保留了或新引入了陈词滥调词，导致 recheck 仍检测到 body_language_cliche_count > 0。
                "修复后必须自检：修复文本中不得出现任何身体语言陈词滥调词"
                "（包括但不限于上述列表中的所有短语），如有则继续改写直至完全消除。"
                "若修复目标是 emotion_label_count，必须将直接情绪命名"
                "（如「愤怒」「悲伤」「恐惧」「惊讶」「厌恶」等情绪标签）"
                "改写为可观察的外部行为描写——用具体的身体反应、动作细节或环境互动替代直接情绪命名。"
                "若违规含 evidence_samples，必须系统性处理所有列出的命中位置，禁止只改 target_span 一处。"
                # 循环#13：ai_simile_overuse 的专科处理指令
                # 根因：review_minister 将 ai_simile_overuse 路由到 prose_local_patch lane（而非
                #   fbi_prose），但 prose_local_patch lane 缺少 ai_simile_overuse 专用指令，LLM 只改
                #   target_span 一处，无法系统性降低全文明喻密度。recheck 仍检测到明喻超标。
                # 循环#13 迭代2：LLM 误解"禁止只改 target_span 一处"为"不要改 target_span"，
                #   导致 target_span 计数未降（before=1, after=1），target_span_check 失败。
                #   且指令只提"像"字，但 target_span 可能含"仿佛/似的/一样/如同"等其他明喻标记。
                # 修复策略：①明确要求改写 target_span 使其不再出现；②覆盖所有明喻标记；
                #   ③给出具体数量目标；④"禁止只改 target_span 一处"改为"必须改 target_span + 其他位置"。
                # 通用性：所有题材的明喻密度问题都需要系统性降低密度。
                "若修复目标是 ai_simile_overuse（明喻句式过度），必须系统性降低全文明喻密度。"
                "关键要求：必须改写 target_span 指定的明喻句——将其中的明喻标记"
                "（像/仿佛/似的/一样/如同/宛如/犹如/好似/好像/恍若）改写为直接陈述或感官细节，"
                "使 target_span 在修复后的文本中完全不再出现（计数从 1 降为 0）。"
                "同时必须改写 evidence_samples 中列出的所有其他明喻位置——"
                "将每处的明喻结构改为直接动作描写或感官描写，保持原句叙事信息不丢失。"
                "必须将全文完整明喻结构数量降至 5 个以下，将'像'字数量降至修复前的一半以下。"
                "不得在修复文本中引入新的明喻结构或'像'字。"
                "注意：不是禁止改 target_span，而是必须改 target_span 并且也改其他位置。"
                "修复后必须自检：①target_span 在修复文本中不再出现；"
                "②'像'字数量不高于修复前的一半；③完整明喻结构数量低于 6 个。"
            ),
            # 通用修复 S-2 + 循环#11 + 循环#13 合并：fbi_prose lane 专用指令
            # 根因：fbi_prose 是默认 lane，处理密度类 AI 痕迹问题。原走默认指令太泛泛，
            #   LLM 每次只改 target_span 一处，无法降低全文密度。
            # 修复：fbi_prose lane 明确指示 LLM 系统性改动所有 evidence_samples 位置。
            # 通用性：适用于所有题材、所有密度类 AI 痕迹问题。
            # 循环#13：合并原 L401 和 L541 两处重复定义（后者覆盖前者），消除死代码。
            "fbi_prose": (
                "专科修复：系统性降低 AI 痕迹密度。"
                "必须改写 target_span 使其在修复后文本中不再出现，同时改写 evidence_samples 中的所有其他位置——"
                "每处改为直接动作描写、感官描写或物件状态，保持原句叙事信息不丢失。"
                "改写要求：将'像X一样/似的/仿佛X'的明喻包装改为直接陈述或感官细节；"
                "将重复的破折号停顿改为句读或动作承接；将直接情绪命名改为可观察的身体反应。"
                "若修复目标是 ai_simile_overuse（明喻句式过度），必须将全文完整明喻结构数量"
                "降至 5 个以下，将'像'字数量降至修复前的一半以下，"
                "且不得在修复文本中引入新的明喻结构或'像'字。"
                "注意：必须改 target_span 并且也改其他位置，不是禁止改 target_span。"
                "密度类问题的修复必须使检测指标降到阈值以下，不得只做局部修饰。"
                "禁止删除原文的叙事信息，禁止改变事实、POV、时间线和场景顺序。"
            ),
            "paragraph_reconstruction": (
                "专科修复：允许拆分、合并或重组相邻句子，重点降低段落同构、重复开头和句群复述。"
            ),
            "pacing_repair": (
                "专科修复：调整句长、段落呼吸、动作-对话桥和小转折，提升节奏与读者推进感。"
            ),
            "voice_repair": (
                "专科修复：移除非 POV 内心直写，把越界心理改为可观察动作、对话或 POV 推断。"
                "若违规含 evidence_samples（如 head_hopping_count），必须系统性处理所有列出的越界位置，"
                "禁止只改 target_span 一处。每处改写必须保留原句的非心理叙事载荷：谁在做什么、"
                "作用于哪个对象、产生什么可观察结果以及前后因果。不得把带具体信息的句子压缩成"
                "『某人的动作顿了一下』之类无信息模板。替换后逐句检查主谓宾、补语和助词衔接，"
                "不得出现『一下了』、残留程度补语或前后分句语义断裂。"
            ),
            # 通用修复（循环 #1）：新增 tense_repair lane
            # 根因：原 correct_tense 路由到 voice_repair，但 voice_repair 的 lane rule
            #   是 POV 修复文本，与闪回/时间线无关，LLM 收到的专科指令与实际问题语义脱节。
            # 修复：新建 tense_repair lane，专门处理时态漂移和闪回闭合问题。
            # 通用性：适用于所有题材——闪回闭合和时序一致性规则在所有题材中一致。
            "tense_repair": (
                "专科修复：处理时态漂移和闪回闭合问题。"
                "为未闭合的叙事闪回补充返回信号（如「眼前」「此刻」「现在」「声音把他拉回」），"
                "或将多句闪回压缩为点缀式一句话回忆；不得删除闪回内容本身。"
                "为缺少过渡信号的时间跳跃补充过渡词（如「片刻后」「次日」「不久后」）。"
                "若修复目标是 temporal_anchor_count（场景缺少可观察的时间锚点），"
                "必须在正文中补充可观察的时间锚点——通过环境细节（如晨光/暮色/月色/日头偏西）、"
                "角色台词（如「天快亮了」「该用晚饭了」）、或动作细节（如「掌灯时分」「烛火燃尽」）"
                "暗示当前时间，使读者能感知场景发生的时间段。"
                # 循环#10：timeline_conflict 的专科处理指令。
                # 根因：检测器用正则匹配弱记忆标记（前世记忆/记忆中/上辈子/那时候/当时她他等），
                #   blocking_count >= 2 即触发。原 tense_repair 指令只说"不得删除闪回内容本身"，
                #   LLM 保留所有标记短语 → recheck 确定性复检仍触发。
                # 修复策略：减少标记数量 + 改写标记表达方式（通用，适用所有题材）。
                "若修复目标是 timeline_conflict / temporal_conflict（时间层级混乱，"
                "正文中闪回/记忆标记过多），必须减少正文中的弱记忆标记数量，"
                "将多处回忆压缩为一处点缀式回忆，使标记数量低于检测阈值。"
                "不得删除回忆内容本身，但必须改写标记短语的表达方式——"
                "将直白的记忆标记词（如「前世记忆」「记忆中」「上辈子」「那时候」「当时她/他」）"
                "改写为不含触发词的间接表达（如「这是她曾经历过的片段」「从前」「旧事」），"
                "使检测器不再触发。"
                "保持时间线一致性，不得引入新的时序矛盾。"
            ),
            "voice_reconstruction": (
                "专科修复：恢复叙述/角色声纹，压低通用模型腔，但不得随机加错字、口癖或廉价混乱。"
            ),
            "contract_completion": (
                "专科修复：补齐合同义务或结尾状态，优先追加动作结果或改写最后段落，不泛泛扩写。"
                # 循环#12：增强 goal_present 的修复指令
                # 根因：原指令说"通过角色台词、内心独白或行动呈现场景目标"，但检测逻辑是
                #   _contains_any(text, goal_terms) or _has_intent_signal(text)。
                #   goal_terms 来自场景合同 goal 字段的关键词，_has_intent_signal 检测正则词
                #   （想要/必须/要去/试图/决定/打算/不能/只剩/为了/得先）。
                #   LLM 添加的台词/独白/行动可能不含这些确切词，导致 recheck 仍判定 goal_present=false。
                # 修复策略：明确要求 LLM 在正文中包含意图信号词，确保通过检测。
                # 通用性：所有题材的场景目标都需要可检测的意图表达。
                "若修复目标是 goal_present（场景目标缺失），必须在正文最自然的位置注入场景目标"
                "（角色的意图、渴望或行动方向），通过角色台词、内心独白或行动呈现。"
                "关键要求：注入的内容必须包含以下意图信号词中的至少一个——"
                "「想要/必须/要去/试图/决定/打算/不能/只剩/为了/得先」，"
                "或包含场景合同 goal 字段中的确切关键词。"
                "示例：①台词——「我必须找到她」「为了活下来，得先离开这里」；"
                "②内心独白——「她决定不再退让」「他想要一个答案」；"
                "③行动叙述——「她试图推开石门」「为了赶到谷口，他只剩最后一段路」。"
                "不得只写含蓄的、不含意图信号词的目标表达。"
                # 循环#10：pure_exposition_block_chars 的专科处理指令
                # 根因：contract_completion lane 指令只提到 goal_present，LLM 不知道
                #   pure_exposition_block_chars 需要拆分纯陈述段落。LLM 按默认"补齐合同义务"
                #   指令操作，可能追加更多陈述内容，反而加剧纯陈述长度。
                # 修复策略：将纯陈述段落拆分为含动作/对话/感官的场景化文本。
                # 通用性：所有题材的纯陈述大段都需要场景化拆分。
                # 循环#12：增强 pure_exposition_block_chars 的修复指令
                # 根因：原指令说"将纯陈述段落拆分"，但未明确检测逻辑。检测逻辑是：
                #   长度 >= 80 字的段落，且动作词（说/问/喊/走/退/抓/按/看/听/抬/落/砸/撞/握/拔/伸）
                #   出现 <= 1 次，且无对话引号（""或""）。
                #   LLM 可能只在段落末尾加了一个动作词就以为修复完成，但 80+ 字段落仍被检测到。
                # 修复策略：明确检测逻辑，要求 LLM 确保每段纯陈述 < 80 字或动作词 >= 2 个。
                "若修复目标是 pure_exposition_block_chars（纯陈述段落超过阈值），"
                "必须将纯陈述段落拆分——在陈述中插入角色的动作反应、对话回应或感官细节，"
                "使连续的说明性文字被场景化节拍打断。"
                "检测逻辑：长度 >= 80 字且动作词（说/问/喊/走/退/抓/按/看/听/抬/落/砸/撞/握/拔/伸）"
                "出现 <= 1 次且无对话引号的段落会被判定为纯陈述。"
                "修复要求：确保修复后每个段落要么长度 < 80 字，要么包含至少 2 个上述动作词，"
                "要么包含对话引号（「」或""）。不得只在一个长段落末尾加一个动作词。"
                "不得删除陈述中的关键信息，但必须用角色的具体行动或对话包裹说明性内容，"
                "使最大连续纯陈述长度降至阈值以下。"
                "拆分方法：①在陈述中插入角色动作（如「她接过绷带，手指在布料上停了一下」）；"
                "②在陈述后插入对话反应（如「『还能撑住吗？』她问」）；"
                "③在陈述中插入感官细节（如「空气里弥漫着药草的苦味」）。"
                # 循环#11：missing_must_show 的专科处理指令
                # 根因：contract_completion lane 指令只提到 goal_present 和 pure_exposition_block_chars，
                #   LLM 不知道 missing_must_show 需要精准补充 must_show 中指定的具体情节内容。
                #   LLM 按默认"补齐合同义务"指令操作，可能追加泛化内容而非 must_show 缺失项，
                #   导致 recheck 仍判定缺失。
                # 修复策略：根据 must_show 清单和 expected_behavior 精准补充缺失的情节内容。
                # 通用性：所有题材的 missing_must_show 都需要精准补充 must_show 指定内容。
                "若修复目标是 missing_must_show（正文缺少必须展现的情节），"
                "必须在正文中补充场景合同 must_show 清单中指定但正文缺失的具体情节内容。"
                "补充方法：①查看 violation 的 detail/expected_behavior 了解缺失的具体内容；"
                "②在正文最自然的位置（通常是对话、行动或观察中）用场景化叙事展现该情节；"
                "③补充的内容必须是具体的场景化描写（动作/对话/感官），不得只写一句抽象陈述。"
                "不得泛泛扩写或追加无关内容，必须精准对应 must_show 缺失项。"
            ),
            "fact_local_patch": (
                "专科修复：只修事实、时间线或因果矛盾，不做文风润色和场景重排。"
                "如果 repair_brief 提供 fact_repair_goal，必须围绕 old_text_claim、required_entities、"
                "forbidden_claims 和 required_relation 做最小可观察修改；不得用原样返回代替修复。"
                "若修复目标是 clue_provenance_error，必须在正文中补充线索的来源——"
                "先读取 fact_repair_goal.authority_fact 和 authority_status。"
                "authority_status=confirmed 时，只能使用该权威事实补足来源；"
                "authority_status=unverified/missing_provenance 时严禁猜测或编造放置者，"
                "只能补足可由当前正文支持的发现条件并保持来源未知，"
                "或删除未受场景合同支持的新线索。"
                "线索来源补充方法（按线索类型选择）："
                "①字条/纸条/便签/纸片/碎片等纸类线索——通过角色辨认字迹/印章/暗记/纸材质推断来源，"
                "如「她认出纸角印着师姐的暗记」「字迹陌生，但纸张是宗门专用笺」；"
                "②气味/痕迹/遗留物等环境线索——通过角色观察推断放置者，"
                "如「茶碗还带着余温，是有人刚走」「灰烬里有未烧尽的符角，是传讯符的残片」；"
                "③符纸/标记/光芒/声音等异常物品——通过角色辨认类型/用途推断来源，"
                "如「她认出这是宗门的传讯符，却想不通谁会在此处点燃」「印记是她熟悉的阵法，是师兄的手笔」；"
                "④信件/遗书/碑文/药方等文本线索——通过角色记忆或观察交代来源，"
                "如「她想起昨晚师兄曾来过此处」。"
                "若修复目标是 spatial_conflict，必须将 target_span 中与环境不符的物品"
                "改为符合场景环境的物品（参照 expected_behavior 的建议），不得保留矛盾描写。"
            ),
            "fact_bridge_patch": (
                "专科修复：补齐事实链条中缺失的必要组件、因果桥或过程步骤；允许重写相邻段落，"
                "但必须保留既有事件顺序、POV、合同结果和受保护事实。"
                "如果 repair_brief 提供 fact_repair_goal，必须明确处理 required_relation；"
                "可以补一句解释桥、替换矛盾表述，或重写相邻句段来消除旧错误。"
            ),
            "scene_restructure": (
                "专科修复：可做有限场景结构调整，但必须保留事实、视角、因果、时间线和结尾合同。"
            ),
            # 循环#13：原循环#11 的 fbi_prose 定义已合并到上方 L412 的统一定义，
            # 消除 dict key 重复定义（后者覆盖前者造成的死代码）。
        }
        rule = lane_rules.get(lane, "专科修复：按 repair_brief 的目标和保护边界执行。")
        strength_rule = ""
        if strength in {"S4", "S5"}:
            strength_rule = (
                "\n强度要求：本轮允许比普通局部修补更主动地重组相邻句段，"
                "但仍不得改变事实、POV、因果、时间线、伏笔释放和合同结果。"
            )
        elif strength in {"S1", "S2"}:
            strength_rule = "\n强度要求：保持最小改动，优先词句级修补。"
        # 通用修复（循环 #8 DD-2 / 循环 #9 BLC-1）：FBI 修复不得引入新违规
        # 根因：FBI 修复时为了补充证据/来源/改写，可能引入多种新质量问题：
        #   - 大段纯陈述（pure_exposition_block_chars）
        #   - 身体语言陈词滥调（body_language_cliche_count）
        #   - 无证据抽象句（abstract_bare_count）
        #   - 情绪标签（emotion_label_count）
        # 修复：在所有 lane 的通用约束中全面覆盖常见副作用类型。
        # 通用性：适用于所有题材——修复不得引入新违规是普适原则。
        quality_guard = (
            "\n质量约束：修复时保持原文的叙事节奏，不得引入以下任何新质量问题：\n"
            "1. 不得引入大段纯陈述（连续超过 80 字的无对话、无动作的陈述性文字）\n"
            "2. 不得引入身体语言陈词滥调（握紧拳头/咬唇/皱眉/叹气/心跳加速/"
            "呼吸急促/眼中闪过/嘴角上扬/苦笑/冷笑/身体一僵/浑身发抖/瞳孔一缩/"
            "喉结滚动/指节泛白/脸色苍白等模板化短语）——改用具体可观察动作\n"
            "3. 不得引入无证据抽象句（如「她知道一件事」「他明白了一个道理」"
            "等无具体感官/动作/对话证据的抽象论断）\n"
            "4. 不得引入情绪标签（如「他感到愤怒」「她觉得悲伤」）——用动作和细节呈现\n"
            "5. 不得引入新的明喻句式——禁用「像…一样」「像…似的」「像…般」等模板化比喻，"
            "也不得单独使用「像」字作比喻。修复时用直接描写替代比喻\n"
            "补充证据或来源时，优先通过角色台词、对话或动作细节呈现，"
            "而非大段叙述性说明。修改后确保不引入新的质量问题。"
        )
        return f"\n\nFBI repair lane: {lane} / {strength}\n{rule}{strength_rule}{quality_guard}"

    async def _patch_from_conflicts(
        self,
        generated_text: str,
        conflicts: list,
        context: dict,
    ) -> dict:
        if not conflicts or not generated_text:
            return self._success_result(generated_text, strategy="patch")

        conflict_descriptions = []
        for c in conflicts:
            if isinstance(c, dict):
                fact = c.get("fact", "")
                claim = c.get("text_claim", "")
                suggestion = c.get("suggestion", "")
                conflict_descriptions.append(
                    f"- 文本描述「{claim}」与已知事实「{fact}」矛盾"
                    + (f"，建议修改为「{suggestion}」" if suggestion else "")
                )

        system_prompt = (
            "你是一个文本修复专家。你的任务是精准修正小说文本中的事实矛盾。\n\n"
            "规则：\n"
            "1. 只修改与已知事实矛盾的部分，不要改动其他内容；但补充已确立事实中明确要求的必要内容不算违规\n"
            "2. 修复必须自然流畅，不能留下修改痕迹\n"
            "3. 必须遵守当前不可变事实快照中的位置、时间、角色状态\n"
            "4. 对于'未提及/未说明/未明确/缺失'类矛盾（即正文缺少已确立事实要求的必要内容），请在最自然的位置补充最小必要内容；不得用原样返回代替修复。仅在完全无法理解矛盾含义时，才返回原文不做修改\n"
            '5. 返回 JSON 格式：{"repaired_text": "修复后的完整文本", "repairs": [{"original": "原文片段", "repaired": "修复后片段", "reason": "修复原因"}]}'
        )
        system_prompt += self._skill_contract_system_instruction(context)

        user_prompt = self._skill_contract_prompt(context)
        if context.get("scene_truth_snapshot"):
            rendered = render_truth_snapshot(context["scene_truth_snapshot"])
            if rendered:
                user_prompt += (
                    "## 当前不可变事实快照（必须严格遵守）\n"
                    f"{rendered}\n\n"
                )

        user_prompt += (
            f"请修复以下文本中的事实矛盾：\n\n"
            f"## 原文\n{generated_text}\n\n"
            f"## 发现的矛盾\n{chr(10).join(conflict_descriptions)}\n\n"
            f"请返回修复后的完整文本和修复详情。"
        )

        related_scene_context = context.get("related_scene_context")
        if related_scene_context:
            user_prompt += (
                "\n\n## Related scene text that must remain consistent\n"
                f"{str(related_scene_context)}\n"
            )
        chapter_scene_context = context.get("chapter_scene_context")
        if chapter_scene_context:
            user_prompt += (
                "\n\n## Full chapter scene context\n"
                f"{str(chapter_scene_context)}\n"
            )

        return await self._call_llm_and_parse(
            generated_text, system_prompt, user_prompt, strategy="patch",
        )

    # ------------------------------------------------------------------
    # Full rewrite  (from scene_rewrite)
    # ------------------------------------------------------------------

    async def _full_rewrite(self, context: dict) -> dict:
        scene_contract = context.get("scene_contract", {})
        chapter_state = context.get("chapter_state", {})
        previous_scene_ending = context.get("previous_scene_ending", "")
        previous_scenes_summary = context.get("previous_scenes_summary", "")
        violations = context.get("violations", [])
        rejected_text = context.get("rejected_text")
        truth_snapshot = context.get("scene_truth_snapshot")
        word_budget = context.get("word_budget")
        rewrite_requirements = context.get("rewrite_requirements", [])
        repair_plan = context.get("repair_plan")

        system_prompt = self._build_rewrite_system_prompt(scene_contract, violations, rewrite_requirements)
        system_prompt += self._skill_contract_system_instruction(context)
        user_prompt = self._build_rewrite_user_prompt(
            scene_contract, chapter_state, previous_scene_ending,
            previous_scenes_summary, violations, rejected_text,
            truth_snapshot, word_budget, repair_plan,
        )
        user_prompt = self._skill_contract_prompt(context) + user_prompt

        llm = await self.get_llm_client()
        try:
            _cache_policy, _stable_hash = build_prompt_cache_policy(system_prompt)
            response = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.35,
                task_type=LLMTaskType.FBI_REPAIR,
                cache_policy=_cache_policy,
                stable_prefix_hash=_stable_hash,
            )
        except Exception as exc:
            original = context.get("generated_text", "")
            return {
                "success": False,
                "repaired_text": original,
                "repairs": [],
                "strategy": "rewrite",
                "error": str(exc),
            }

        repaired_text = response.strip()
        # 通用修复（循环 #13）：检测 LLM 空响应，避免 success=True + repaired_text="" 的矛盾状态
        # 根因：_full_rewrite 在 LLM 返回空字符串时仍返回 success=True，
        #   chapter_repair_executor.py:4981 检测到 not generated 后抛出
        #   "SceneRepairer rewrite failed: unknown"（因成功路径无 error 键），
        #   导致 order hard_failed 且错误信息不可读。
        #   通用性：所有题材的 scene_rewrite 都可能遇到 LLM 空响应，需要正确处理。
        if not repaired_text:
            original = context.get("generated_text", "")
            return {
                "success": False,
                "repaired_text": original,
                "repairs": [],
                "strategy": "rewrite",
                "error": "LLM returned empty response for scene rewrite",
            }
        # 后置自检：检查重写结果是否达成 ending_state、是否引入新实体
        # 之前 rewrite 直接返回 response，缺少自检环节，导致 LLM 可能偏离合同
        self_check = self._post_rewrite_self_check(
            repaired_text, scene_contract, truth_snapshot,
        )
        return {
            "success": True,
            "repaired_text": repaired_text,
            "repairs": [],
            "strategy": "rewrite",
            "self_check": self_check,
        }

    # ------------------------------------------------------------------
    # Shared LLM call + JSON parse  (from auto_repair)
    # ------------------------------------------------------------------

    async def _call_llm_and_parse(
        self,
        original_text: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
        *,
        strategy: str = "patch",
    ) -> dict:
        llm = await self.get_llm_client()
        _cache_policy, _stable_hash = build_prompt_cache_policy(system_prompt)
        response = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            task_type=LLMTaskType.FBI_REPAIR,
            cache_policy=_cache_policy,
            stable_prefix_hash=_stable_hash,
        )

        parse_error = ""
        try:
            result = parse_json_response(response)
            if not isinstance(result, dict):
                raise ValueError("repair response must be a JSON object")
            recovered = self._normalize_repair_payload(result, original_text)
            if recovered:
                output = {
                    "success": True,
                    "repaired_text": recovered["repaired_text"],
                    "repairs": recovered.get("repairs", []),
                    "strategy": strategy,
                }
                if recovered.get("parse_recovery"):
                    output["parse_recovery"] = recovered["parse_recovery"]
                return output
            raise ValueError("repair response missing repaired text")
        except Exception as exc:
            parse_error = str(exc)

        recovered = self._recover_repair_text(response, original_text)
        if recovered:
            return {
                "success": True,
                "repaired_text": recovered["repaired_text"],
                "repairs": recovered.get("repairs", []),
                "strategy": strategy,
                "parse_recovery": recovered.get("parse_recovery", "malformed_response"),
                "parse_error": parse_error,
            }

        retry_result = await self._retry_format_repair(
            llm,
            original_text,
            response,
            parse_error,
            max_tokens,
        )
        if retry_result:
            return {
                "success": True,
                "repaired_text": retry_result["repaired_text"],
                "repairs": retry_result.get("repairs", []),
                "strategy": strategy,
                "parse_recovery": retry_result.get("parse_recovery", "format_retry"),
                "parse_error": parse_error,
            }

        return {
            "success": False,
            "repaired_text": original_text,
            "repairs": [],
            "strategy": strategy,
            "error": "repair_result_parse_failed",
            "parse_error": parse_error,
            "raw_response_preview": (response or "")[:300],
        }

    @classmethod
    def _normalize_repair_payload(cls, payload: dict, original_text: str) -> dict | None:
        text_keys = (
            "repaired_text",
            "revised_text",
            "fixed_text",
            "replacement_text",
            "final_text",
            "scene_text",
            "text",
            "content",
            "body",
        )
        repaired_text = ""
        parse_recovery = ""
        for key in text_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                repaired_text = value.strip()
                if key != "repaired_text":
                    parse_recovery = f"field_alias:{key}"
                break

        repairs = payload.get("repairs")
        if not isinstance(repairs, list):
            repairs = payload.get("changes")
        if not isinstance(repairs, list):
            repairs = []

        patches = payload.get("patches")
        if not repaired_text and isinstance(patches, list):
            patched, applied, skipped = cls.apply_patches(original_text, patches)
            if applied and patched != original_text:
                return {
                    "repaired_text": patched,
                    "repairs": applied,
                    "skipped_patches": skipped,
                    "parse_recovery": "patches_only_payload",
                }

        if not repaired_text:
            return None
        return {
            "repaired_text": repaired_text,
            "repairs": repairs,
            "parse_recovery": parse_recovery,
        }

    @classmethod
    def _recover_repair_text(cls, response: str, original_text: str) -> dict | None:
        repaired_text = cls._extract_malformed_repaired_text(response)
        if repaired_text:
            return {
                "repaired_text": repaired_text,
                "repairs": [],
                "parse_recovery": "malformed_repaired_text_field",
            }

        plain_text = cls._extract_plain_repair_text(response, original_text)
        if plain_text:
            return {
                "repaired_text": plain_text,
                "repairs": [],
                "parse_recovery": "plain_text_fallback",
            }
        return None

    async def _retry_format_repair(
        self,
        llm,
        original_text: str,
        malformed_response: str,
        parse_error: str,
        max_tokens: int,
    ) -> dict | None:
        try:
            _retry_sys = (
                "You convert a malformed fiction repair response into strict JSON only. "
                "Do not rewrite or improve the prose. Preserve the repaired prose exactly if it exists. "
                'Return {"repaired_text":"...","repairs":[]} and nothing else. '
                "If no repaired prose exists, return the original text."
            )
            _cache_policy, _stable_hash = build_prompt_cache_policy(_retry_sys)
            retry_response = await llm.generate(
                system_prompt=_retry_sys,
                user_prompt=(
                    f"Parse error: {parse_error}\n\n"
                    f"## Original text\n{original_text}\n\n"
                    f"## Malformed repair response\n{(malformed_response or '')[:12000]}"
                ),
                temperature=0.0,
                max_tokens=min((max_tokens or 4096) + 256, 8192),
                cache_policy=_cache_policy,
                stable_prefix_hash=_stable_hash,
            )
            payload = parse_json_response(retry_response)
            if isinstance(payload, dict):
                recovered = self._normalize_repair_payload(payload, original_text)
                if recovered:
                    recovered["parse_recovery"] = "format_retry"
                    return recovered
            return self._recover_repair_text(retry_response, original_text)
        except Exception:
            return None

    @staticmethod
    def _extract_plain_repair_text(response: str, original_text: str) -> str:
        text = (response or "").strip()
        if not text or text == (original_text or "").strip():
            return ""
        if text.startswith(("{", "[")):
            return ""

        fenced = re.search(r"```(?:text|markdown|md)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fenced and "{" not in fenced.group(1)[:20]:
            text = fenced.group(1).strip()

        lines = [line.rstrip() for line in text.splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and len(lines[0].strip()) <= 40 and re.search(
            r"(repaired|revised|final|text|content).*[：:]\s*$",
            lines[0],
            re.IGNORECASE,
        ):
            lines.pop(0)

        joined = "\n".join(lines).strip()
        split_match = re.search(r"\n\s*(?:repairs?|changes?)\s*[：:]", joined, re.IGNORECASE)
        if split_match:
            joined = joined[:split_match.start()].strip()

        if not joined or joined == (original_text or "").strip():
            return ""
        lowered = joined[:300].lower()
        reject_markers = (
            "as an ai",
            "i cannot",
            "json",
            "repaired_text",
        )
        if any(marker in lowered for marker in reject_markers):
            return ""

        original_len = len(original_text or "")
        min_len = min(200, max(40, int(original_len * 0.1))) if original_len else 40
        if len(joined) < min_len:
            return ""
        return joined

    # ------------------------------------------------------------------
    # Inline patch application  (from ai_quality_inline_reviser)
    # ------------------------------------------------------------------

    @classmethod
    def apply_patches(
        cls,
        draft_text: str,
        patches: list,
        *,
        forbidden_terms: list[str] | None = None,
        allowed_short_originals: list[str] | None = None,
    ) -> tuple[str, list[dict], list[dict]]:
        current = draft_text or ""
        applied: list[dict] = []
        skipped: list[dict] = []
        changed_source_chars = 0
        forbidden_terms = [term for term in (forbidden_terms or []) if term]
        allowed_short_originals = {
            str(item).strip()
            for item in (allowed_short_originals or [])
            if str(item).strip()
        }

        for raw_patch in patches[:_MAX_INLINE_PATCHES]:
            if not isinstance(raw_patch, dict):
                skipped.append({"reason": "patch_not_object"})
                continue

            original = str(raw_patch.get("original", "")).strip()
            replacement = str(raw_patch.get("replacement", "")).strip()
            reason = str(raw_patch.get("reason", "")).strip()

            short_original_is_unique_anchor = bool(
                original in allowed_short_originals
                and len(original) >= 2
                and current.count(original) == 1
            )
            if len(original) < 6 and not short_original_is_unique_anchor:
                skipped.append({"reason": "original_too_short", "original_preview": original[:40]})
                continue
            matched_original = original
            if matched_original not in current:
                matched_original = cls._find_matching_span(current, original) or ""
            if not matched_original:
                skipped.append({
                    "reason": "original_not_found",
                    "original_hash": cls._hash(original),
                    "original_preview": original[:80],
                })
                continue
            if replacement == matched_original:
                skipped.append({"reason": "no_change", "original_hash": cls._hash(matched_original)})
                continue
            if len(replacement) > max(len(matched_original) * 3, len(matched_original) + 240):
                skipped.append({"reason": "replacement_too_large", "original_hash": cls._hash(matched_original)})
                continue
            if any(term in replacement and term not in matched_original for term in forbidden_terms):
                skipped.append({"reason": "replacement_introduces_forbidden_term", "original_hash": cls._hash(matched_original)})
                continue

            next_changed = changed_source_chars + len(matched_original)
            if (
                len(draft_text) >= 120
                and current
                and next_changed / max(len(draft_text), 1) > _MAX_CHANGED_RATIO
            ):
                skipped.append({"reason": "changed_ratio_too_large", "original_hash": cls._hash(matched_original)})
                continue

            current = current.replace(matched_original, replacement, 1)
            changed_source_chars = next_changed
            applied.append({
                "original_hash": cls._hash(matched_original),
                "replacement_hash": cls._hash(replacement),
                "original_length": len(matched_original),
                "replacement_length": len(replacement),
                "reason": reason,
                "matched_by": "exact" if matched_original == original else "normalized_whitespace",
            })

        return current, applied, skipped

    @classmethod
    def _find_matching_span(cls, text: str, original: str) -> str | None:
        """Find the exact source span when the model only changed whitespace."""
        if not text or not original:
            return None
        compact_original = re.sub(r"\s+", "", original)
        if len(compact_original) < 6:
            return None

        compact_chars: list[str] = []
        source_indexes: list[int] = []
        for index, char in enumerate(text):
            if char.isspace():
                continue
            compact_chars.append(char)
            source_indexes.append(index)
        compact_text = "".join(compact_chars)
        compact_pos = compact_text.find(compact_original)
        if compact_pos < 0:
            return None

        start = source_indexes[compact_pos]
        end = source_indexes[compact_pos + len(compact_original) - 1] + 1
        candidate = text[start:end]
        if len(candidate) > max(len(original) * 2, len(original) + 80):
            return None
        return candidate

    @staticmethod
    def _skill_contract_prompt(context: dict) -> str:
        envelope = context.get("skill_contract_envelope") if isinstance(context, dict) else None
        if not isinstance(envelope, dict):
            return SceneRepairer._standalone_style_prompt(context)
        contracts = envelope.get("skill_contracts")
        if not isinstance(contracts, list) or not contracts:
            return SceneRepairer._standalone_style_prompt(context)

        lines = [
            "## Active Skill Contracts",
            "These are product constraints for the repair candidate. They are not repair tools and must not replace the assigned FBI repair task.",
        ]
        for contract in contracts[:4]:
            if not isinstance(contract, dict):
                continue
            skill_id = contract.get("skill_id") or contract.get("name") or "unknown_skill"
            role = contract.get("role") or "quality_contract"
            lines.append(f"- skill: {skill_id}; role: {role}; not_repairer: {bool(contract.get('not_repairer', True))}")
            constraints = contract.get("constraints")
            if isinstance(constraints, dict):
                ai_flavor = constraints.get("ai_flavor")
                if isinstance(ai_flavor, dict):
                    compact = ", ".join(f"{key}={value}" for key, value in list(ai_flavor.items())[:8])
                    if compact:
                        lines.append(f"  ai_flavor: {compact}")
                ai_discourse = constraints.get("ai_discourse")
                if isinstance(ai_discourse, dict):
                    compact = ", ".join(f"{key}={value}" for key, value in list(ai_discourse.items())[:8])
                    if compact:
                        lines.append(f"  ai_discourse: {compact}")
            guidance = contract.get("repair_guidance")
            if isinstance(guidance, list):
                for item in guidance[:4]:
                    if item:
                        lines.append(f"  guidance: {item}")
        lines.append(
            "Acceptance reminder: fix the assigned issue without introducing AI-flavor artifacts, generic explanatory prose, mirrored paragraph openings, or dash overuse."
        )
        lines.extend(SceneRepairer._style_guidance_lines(context))
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _standalone_style_prompt(context: dict) -> str:
        """Render style guidance even for direct SceneRepairer fallback calls."""

        lines = SceneRepairer._style_guidance_lines(context)
        if not lines:
            return ""
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _style_guidance_lines(context: dict) -> list[str]:
        """Render the compact but complete style portrait available to FBI."""

        if not isinstance(context, dict):
            return []
        style_context = context.get("style_context") or context.get("style_profile") or {}
        if not isinstance(style_context, dict):
            style_context = {}
        nested_profile = style_context.get("style_profile")
        profile = (
            nested_profile
            if isinstance(nested_profile, dict) and nested_profile
            else style_context
        )
        style_directive = (context.get("scene_contract") or {}).get("style_directive")
        if not profile and not isinstance(style_directive, dict):
            return []

        lines = [
            "",
            "## Active Writing Style (advisory, never a commit blocker)",
            "Preserve this voice when it does not conflict with facts, contracts, POV, chronology, or the assigned repair goal.",
        ]
        style_prompt = str(
            context.get("style_prompt")
            or style_context.get("style_prompt")
            or profile.get("style_prompt")
            or ""
        ).strip()
        if style_prompt:
            lines.append(f"- style_prompt: {style_prompt[:1600]}")

        style_features = profile.get("style_features") or style_context.get("style_features") or {}
        if isinstance(style_features, dict):
            for key in (
                "vocabulary",
                "sentence_structure",
                "tone",
                "pacing",
                "description_style",
                "dialogue_style",
                "narrative_voice",
            ):
                value = str(style_features.get(key) or "").strip()
                if value:
                    lines.append(f"- {key}: {value[:360]}")
            for key, label in (
                ("signature_phrases", "signature phrases"),
                ("avoid_patterns", "style avoid preferences"),
                ("hard_rules", "style hard preferences"),
            ):
                values = style_features.get(key) or []
                if isinstance(values, list) and values:
                    lines.append(
                        f"- {label}: "
                        + " / ".join(str(item)[:120] for item in values[:10])
                    )

        embedding = context.get("style_embedding") or profile.get("style_embedding") or {}
        if isinstance(embedding, dict) and embedding:
            compact_embedding = ", ".join(
                f"{key}={value}"
                for key, value in list(embedding.items())[:12]
                if isinstance(value, (int, float, bool, str))
            )
            if compact_embedding:
                lines.append(f"- style_embedding: {compact_embedding[:800]}")

        persona = context.get("persona_card") or profile.get("persona_card") or {}
        if isinstance(persona, dict):
            for key in (
                "identity",
                "decision_pattern",
                "expression_style",
                "interpersonal_behavior",
            ):
                value = str(persona.get(key) or "").strip()
                if value:
                    lines.append(f"- narrator_{key}: {value[:300]}")
            hard_rules = persona.get("hard_rules") or []
            if isinstance(hard_rules, list) and hard_rules:
                lines.append(
                    "- narrator_hard_rules: "
                    + " / ".join(str(item)[:120] for item in hard_rules[:10])
                )

        samples = (
            style_context.get("style_sample_passages")
            or profile.get("style_sample_passages")
            or profile.get("sample_passages")
            or []
        )
        if isinstance(samples, list):
            for index, sample in enumerate(samples[:3], start=1):
                sample_text = (
                    sample.get("text") if isinstance(sample, dict) else sample
                )
                sample_text = str(sample_text or "").strip()
                if sample_text:
                    lines.append(f"- style_sample_{index}: {sample_text[:360]}")

        statistics = profile.get("style_statistics") or style_context.get("style_statistics") or {}
        if isinstance(statistics, dict):
            global_statistics = statistics.get("global") or {}
            if isinstance(global_statistics, dict) and global_statistics:
                lines.append(f"- style_statistics: {str(global_statistics)[:900]}")
        evolution = profile.get("evolution_report") or style_context.get("evolution_report") or {}
        if isinstance(evolution, dict):
            usage = str(evolution.get("recommended_usage") or "").strip()
            if usage:
                lines.append(f"- style_recommended_usage: {usage[:500]}")

        if isinstance(style_directive, dict) and style_directive:
            lines.append(f"- scene_style_directive: {str(style_directive)[:1200]}")
        return lines

    @classmethod
    def _skill_contract_system_instruction(cls, context: dict) -> str:
        if not cls._skill_contract_prompt(context):
            return ""
        return (
            "\n\nActive Skill contract: anti_ai_prose must remain satisfied while repairing. "
            "The Skill is a constraint source, not an agent. Do not introduce generic explanatory narration, "
            "false ranges, body-language cliches, abstract trait statements, mirrored paragraph openings, or dash overuse."
        )

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_patch_user_prompt(
        self,
        context: dict,
        generated_text: str,
        violation_descriptions: list[str],
        forbidden: list,
        must_show: list,
        force_change: bool,
        source_of_truth: dict,
    ) -> str:
        user_prompt = self._skill_contract_prompt(context)

        truth_snapshot = context.get("scene_truth_snapshot")
        if truth_snapshot:
            rendered = render_truth_snapshot(truth_snapshot)
            if rendered:
                user_prompt += (
                    "## 当前不可变事实快照（必须严格遵守）\n"
                    f"{rendered}\n\n"
                )

        repair_plan = context.get("repair_plan")
        if repair_plan and isinstance(repair_plan, dict):
            user_prompt += "## 修复计划\n"
            if repair_plan.get("root_cause"):
                user_prompt += f"根因：{repair_plan['root_cause']}\n"
            if repair_plan.get("preserve"):
                user_prompt += "必须保留的内容：\n"
                for item in repair_plan["preserve"]:
                    user_prompt += f"  ✓ {item}\n"
            if repair_plan.get("remove"):
                user_prompt += "必须删除的内容：\n"
                for item in repair_plan["remove"]:
                    user_prompt += f"  ❌ {item}\n"
            user_prompt += "\n"

        repair_brief = context.get("repair_brief")
        if repair_brief and isinstance(repair_brief, dict):
            user_prompt += "## FBI 结构化修复任务\n"
            if repair_brief.get("repair_lane") or context.get("repair_lane"):
                user_prompt += (
                    f"- lane: {repair_brief.get('repair_lane') or context.get('repair_lane')}\n"
                    f"- strength: {repair_brief.get('repair_strength') or context.get('repair_strength')}\n"
                )
            repair_goals = repair_brief.get("repair_goals") or []
            placement = repair_brief.get("placement") or {}
            if repair_goals:
                user_prompt += "- repair_goals (one candidate must satisfy every listed goal):\n"
                for goal in repair_goals:
                    if not isinstance(goal, dict):
                        continue
                    user_prompt += (
                        f"  - goal_id: {goal.get('goal_id', '')}\n"
                        f"    authority_ref: {goal.get('authority_ref', '')}\n"
                        f"    mutation_kind: {goal.get('mutation_kind', '')}\n"
                        f"    desired_state: {goal.get('desired_state', '')}\n"
                        f"    prohibited_state: {goal.get('prohibited_state', '')}\n"
                    )
            if isinstance(placement, dict) and placement:
                user_prompt += f"- placement_status: {placement.get('status', '')}\n"
                write_anchor = placement.get("write_anchor") or {}
                if write_anchor:
                    user_prompt += f"- validated_write_anchor: {write_anchor}\n"
                else:
                    user_prompt += (
                        "- placement_instruction: read the complete chapter context, "
                        "locate the most natural write position yourself, and do not "
                        "treat diagnostic evidence spans as insertion anchors.\n"
                    )
            for metric in (repair_brief.get("target_metrics") or []):
                if isinstance(metric, dict):
                    user_prompt += (
                        "- target_metric: "
                        f"{metric.get('validator', '')}:{metric.get('metric', '')} "
                        f"actual={metric.get('actual', '')} "
                        f"expected={metric.get('expected', metric.get('expected_max', ''))} "
                        f"direction={metric.get('direction', '')}\n"
                    )
            if repair_brief.get("target_behavior"):
                user_prompt += f"- target_behavior: {repair_brief.get('target_behavior')}\n"
            fact_goal = repair_brief.get("fact_repair_goal") or {}
            if isinstance(fact_goal, dict) and fact_goal:
                user_prompt += "- fact_repair_goal:\n"
                if fact_goal.get("conflict_type"):
                    user_prompt += f"  conflict_type: {fact_goal.get('conflict_type')}\n"
                if fact_goal.get("authority_fact"):
                    user_prompt += f"  authority_fact: {fact_goal.get('authority_fact')}\n"
                if fact_goal.get("text_claim"):
                    user_prompt += f"  old_text_claim: {fact_goal.get('text_claim')}\n"
                if fact_goal.get("required_entities"):
                    user_prompt += (
                        "  required_entities: "
                        f"{', '.join(str(item) for item in fact_goal.get('required_entities'))}\n"
                    )
                if fact_goal.get("forbidden_claims"):
                    user_prompt += "  old_claims_to_remove_or_rewrite:\n"
                    for item in fact_goal.get("forbidden_claims", []):
                        user_prompt += f"    - {item}\n"
                if fact_goal.get("required_relation"):
                    user_prompt += f"  required_relation: {fact_goal.get('required_relation')}\n"
                user_prompt += (
                    "  acceptance_checks:\n"
                    "    - 删除或改写 old_text_claim / old_claims_to_remove_or_rewrite 中的旧错误表述\n"
                    "    - 正文必须满足 required_entities 与 required_relation\n"
                    "    - 正文不得继续保留 forbidden_claims 的事实含义\n"
                    "    - repairs 至少记录一处 original / repaired / reason\n"
                )
            edit_scope = repair_brief.get("edit_scope") or {}
            if isinstance(edit_scope, dict) and edit_scope.get("allowed_operations"):
                user_prompt += (
                    "- allowed_operations: "
                    f"{', '.join(str(item) for item in edit_scope.get('allowed_operations')[:8])}\n"
                )
            preserve = repair_brief.get("preserve") or {}
            if isinstance(preserve, dict) and preserve.get("protected_spans"):
                user_prompt += "- protected_spans:\n"
                for item in preserve.get("protected_spans", []):
                    user_prompt += f"  ✓ {item}\n"
            repair_attempt = context.get("repair_attempt") or {}
            if isinstance(repair_attempt, dict) and repair_attempt.get("attempt", 1) > 1:
                user_prompt += (
                    "- retry_reason: previous candidate did not improve the target metric; "
                    "use the stronger allowed operations while preserving all protected content.\n"
                )
            user_prompt += "\n"

        user_prompt += (
            f"请修复以下文本中的违规问题：\n\n"
            f"## 原文\n{generated_text}\n\n"
            f"## 发现的违规\n{chr(10).join(violation_descriptions)}\n\n"
        )
        chapter_scene_context = context.get("chapter_scene_context")
        if chapter_scene_context:
            user_prompt += (
                "## Chapter scene context for consistency\n"
                f"{str(chapter_scene_context)}\n\n"
            )

        if force_change:
            user_prompt += (
                "## 本轮修订要求\n"
                "- 不要只解释问题，必须返回改写后的完整正文。\n"
                "- 如果没有明确正文相关片段，请根据问题描述自行定位最相关的局部句段。\n"
                "- 优先做小范围替换、删减、合并或节奏调整，保持事实、人物、事件和场景顺序不变。\n"
                "- repairs 中必须记录至少一处 original/repaired/reason。\n\n"
            )
        if forbidden:
            user_prompt += f"## 绝对禁止项\n{chr(10).join(f'- {f}' for f in forbidden if f)}\n\n"
        if must_show:
            user_prompt += f"## 必须展示项\n{chr(10).join(f'- {m}' for m in must_show if m)}\n\n"

        scene_credibility_contract = context.get("scene_credibility_contract", {})
        if scene_credibility_contract and isinstance(scene_credibility_contract, dict):
            fact_items = scene_credibility_contract.get("fact_boundaries", [])
            knowledge_items = scene_credibility_contract.get("knowledge_snapshots", [])
            narration_rules = scene_credibility_contract.get("narration_rules", [])
            user_prompt += "## 场景可信度合同（修复时必须遵守）\n"
            for item in fact_items[:6] if isinstance(fact_items, list) else []:
                if not isinstance(item, dict):
                    continue
                label = item.get("label", "事实边界")
                allowed = item.get("allowed_claims", [])
                forbidden_claims = item.get("forbidden_claims", [])
                if allowed:
                    user_prompt += f"- {label}：必须保持 {'；'.join(str(x) for x in allowed[:3])}\n"
                if forbidden_claims:
                    user_prompt += f"- {label}：不得写成 {'；'.join(str(x) for x in forbidden_claims[:3])}\n"
            for item in knowledge_items[:3] if isinstance(knowledge_items, list) else []:
                if isinstance(item, dict) and item.get("display_name"):
                    user_prompt += f"- {item['display_name']} 不能直接知道未公开信息；判断必须有观察、对话、线索或身体反应支撑。\n"
            for rule in narration_rules[:4] if isinstance(narration_rules, list) else []:
                if isinstance(rule, dict) and rule.get("description"):
                    user_prompt += f"- {rule['description']}\n"
            user_prompt += "\n"

        foreshadowing_ops = source_of_truth.get("foreshadowing_ops", []) if isinstance(source_of_truth, dict) else []
        if foreshadowing_ops:
            user_prompt += "## 伏笔操作（内部ID不得直接写入正文，请按叙事指令自然呈现）\n"
            for operation in foreshadowing_ops:
                if isinstance(operation, dict):
                    user_prompt += (
                        f"- {operation.get('op', 'plant')}："
                        f"{operation.get('narrative_instruction') or operation.get('thread_name', '')}\n"
                    )
            user_prompt += "\n"

        user_prompt += "请返回修复后的完整文本和修复详情。"
        return user_prompt

    def _build_rewrite_system_prompt(
        self,
        scene_contract: dict,
        violations: list,
        rewrite_requirements: list | None = None,
    ) -> str:
        prompt = (
            "你是一个小说场景重写专家。你需要基于场景合同从头重新生成一个场景。\n\n"
            "关键规则：\n"
            "1. 你必须从场景合同出发重新组织叙事，不要沿用任何被拒绝正文的叙事结构\n"
            "2. source_of_truth（大纲指定）中的字段不可修改：目标、冲突、大纲结果方向、大纲要求展示、大纲禁止\n"
            "3. 你必须达成场景目标（goal），处理冲突（conflict），并朝大纲结果方向（outline_outcome）推进\n"
            "4. 不得新增合同未声明的角色、线索和事件\n"
            "5. 必须遵守当前不可变事实快照中的位置、时间、角色状态和已完成事件\n"
            "6. 不得将角色写回已经离开的地点，不得重演已完成事件\n"
            "7. 必须避免下方列出的违规问题\n"
            "8. 场景必须以 ending_state 描述的状态结束——这是硬约束，不得停在中间状态或偏离到其他结局\n"
            "9. 不得引入与不可变事实快照中已登记威胁类型不同的新威胁（如事实层是妖兽，不得写成魔化修士）\n"
        )

        if violations:
            prompt += "\n需要避免的问题：\n"
            for v in violations:
                if not isinstance(v, dict):
                    continue
                vtype = v.get("type", "unknown")
                detail = v.get("detail", "")
                expected = v.get("expected_behavior", "")
                prompt += f"- [{vtype}] {detail}"
                if expected:
                    prompt += f"（期望：{expected}）"
                prompt += "\n"

            # 检测身份/事实冲突类违规，追加身份一致性硬约束，
            # 避免整段重写时引入新的身份不一致（如同角色多名、身份混淆）。
            violation_types = {
                str(v.get("type") or v.get("violation_type") or "").lower()
                for v in violations
                if isinstance(v, dict)
            }
            identity_conflict_types = {
                "identity_conflict", "naming_conflict", "internal_conflict",
                "fact_conflict", "timeline_conflict",
            }
            if violation_types & identity_conflict_types:
                prompt += (
                    "\n身份一致性硬约束（必须满足，避免重写引入新的身份冲突）：\n"
                    "- 同一角色在本场景中只能使用一个名字，不得中途换名或混用称呼\n"
                    "- 不得将不同角色合并为同一人，也不得将同一角色拆分为多人\n"
                    "- 角色的身份、关系、来历必须与不可变事实快照一致，不得改写\n"
                    "- 不得引入与已发生事实矛盾的身份信息（如已死亡角色重新出现）\n"
                )

        if rewrite_requirements:
            prompt += "\n重写硬约束（必须满足）：\n"
            for req in rewrite_requirements:
                prompt += f"- {req}\n"

        return prompt

    @staticmethod
    def _post_rewrite_self_check(
        repaired_text: str,
        scene_contract: dict,
        truth_snapshot: dict | None,
    ) -> dict:
        """重写后置自检：检查 ending_state 关键词是否出现、是否引入新实体。

        之前 rewrite 直接返回 response，缺少自检环节，导致 LLM 可能偏离合同。
        本方法只做轻量级字符串检查，不做语义判断（语义判断由后续 recheck 负责）。
        """
        issues: list[str] = []

        # 1. 检查 ending_state 关键词是否在正文中出现
        ending_state = ""
        editor_enrichment = scene_contract.get("editor_enrichment") or {}
        if isinstance(editor_enrichment, dict):
            ending_state = str(editor_enrichment.get("ending_state") or "")
        if not ending_state:
            ending_state = str(scene_contract.get("ending_state") or "")
        if ending_state:
            # 提取 ending_state 中的关键词（去除标点和虚词，取长度 >=2 的片段）
            keywords = [
                w.strip() for w in re.split(r"[，。；、\s]+", ending_state)
                if w.strip() and len(w.strip()) >= 2
            ]
            missing_keywords = [kw for kw in keywords if kw not in repaired_text]
            if missing_keywords and len(missing_keywords) / max(len(keywords), 1) > 0.5:
                issues.append(
                    f"ending_state 关键词覆盖不足：缺失 {missing_keywords[:5]}"
                )

        # 2. 检查是否引入 truth_snapshot 中未登记的新角色
        if truth_snapshot and isinstance(truth_snapshot, dict):
            character_states = truth_snapshot.get("character_states") or {}
            # 提取正文中所有引号内的称呼和"X道/X说/X想"模式中的角色名
            mentioned_names = set(re.findall(r"[一-龥]{2,4}(?:道|说|想|看|笑|叹)", repaired_text))
            known_names = set(character_states.keys())
            # 加入一些常见代词，不算新角色
            known_names.update({"凤溪", "她", "他", "我", "你"})
            new_names = mentioned_names - known_names
            if new_names:
                issues.append(f"可能引入了未登记的新角色：{list(new_names)[:5]}")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    def _build_rewrite_user_prompt(
        self,
        scene_contract: dict,
        chapter_state: dict,
        previous_scene_ending: str,
        previous_scenes_summary: str,
        violations: list,
        rejected_text: str | None,
        truth_snapshot: dict | None = None,
        word_budget: dict | None = None,
        repair_plan: dict | None = None,
    ) -> str:
        source_of_truth = scene_contract.get("source_of_truth", {})
        editor_enrichment = scene_contract.get("editor_enrichment", {})

        prompt = ""

        if truth_snapshot:
            rendered = render_truth_snapshot(truth_snapshot)
            if rendered:
                prompt += (
                    "### 当前不可变事实快照（必须严格遵守，不得违反）\n"
                    f"{rendered}\n\n"
                )

        if word_budget and isinstance(word_budget, dict):
            target = word_budget.get("target_chars", 0)
            soft_min = word_budget.get("soft_min_chars", 0)
            soft_max = word_budget.get("soft_max_chars", 0)
            hard_max = word_budget.get("hard_max_chars", 0)
            if target:
                prompt += f"### 篇幅要求\n目标字数约{target}字"
                if soft_min and soft_max:
                    prompt += f"（可接受范围{soft_min}-{soft_max}字）"
                if hard_max:
                    prompt += f"，绝对不得超过{hard_max}字"
                prompt += "。\n\n"

        scene_provenance = scene_contract.get("scene_provenance")
        if scene_provenance and isinstance(scene_provenance, dict):
            rendered_prov = render_scene_provenance(scene_provenance)
            if rendered_prov:
                prompt += (
                    "### 统一场景事实层（scene_provenance）\n"
                    "注意：参考设定层只用于对照，不是当前时间线已发生的事实。"
                    "当前事实层与参考设定层冲突时，以当前事实层为准。\n"
                    f"{rendered_prov}\n\n"
                )

        if chapter_state and isinstance(chapter_state, dict):
            prompt += "### 章节已确立事实\n"
            for key, value in chapter_state.items():
                if value and isinstance(value, (str, list)):
                    if isinstance(value, list):
                        prompt += f"- {key}：{'、'.join(str(v) for v in value)}\n"
                    else:
                        prompt += f"- {key}：{value}\n"
            prompt += "\n"

        prompt += "### 场景合同\n\n"

        if source_of_truth:
            prompt += "#### 大纲指定（不可修改）\n"
            if source_of_truth.get("goal"):
                prompt += f"目标：{source_of_truth['goal']}\n"
            if source_of_truth.get("conflict"):
                prompt += f"冲突：{source_of_truth['conflict']}\n"
            if source_of_truth.get("outline_outcome"):
                prompt += f"大纲结果方向：{to_text(source_of_truth['outline_outcome'])}\n"
            if source_of_truth.get("must_show_outline"):
                prompt += f"大纲要求展示：{to_text(source_of_truth['must_show_outline'])}\n"
            if source_of_truth.get("forbidden_outline"):
                prompt += f"大纲禁止：{to_text(source_of_truth['forbidden_outline'])}\n"
            if source_of_truth.get("foreshadowing_ops"):
                prompt += "伏笔操作（内部ID不得直接写入正文，请按叙事指令自然呈现）：\n"
                for operation in source_of_truth["foreshadowing_ops"]:
                    if isinstance(operation, dict):
                        prompt += (
                            f"- {operation.get('op', 'plant')}："
                            f"{operation.get('narrative_instruction') or operation.get('thread_name', '')}\n"
                        )

        if editor_enrichment and isinstance(editor_enrichment, dict):
            prompt += "\n#### 主编补充\n"
            if editor_enrichment.get("ending_state"):
                prompt += f"场景结束状态：{editor_enrichment['ending_state']}\n"
            if editor_enrichment.get("additional_must_show"):
                prompt += f"额外必须展示：{to_text(editor_enrichment['additional_must_show'])}\n"
            if editor_enrichment.get("additional_forbidden"):
                prompt += f"额外禁止：{to_text(editor_enrichment['additional_forbidden'])}\n"

        if not source_of_truth:
            goal = scene_contract.get("goal", "")
            conflict = scene_contract.get("conflict", "")
            must_show = scene_contract.get("must_show", [])
            forbidden = scene_contract.get("forbidden", [])
            ending_state = scene_contract.get("ending_state", "")
            if goal:
                prompt += f"目标：{goal}\n"
            if conflict:
                prompt += f"冲突：{conflict}\n"
            if must_show:
                prompt += f"必须展示：{to_text(must_show)}\n"
            if forbidden:
                prompt += f"绝对禁止：{to_text(forbidden)}\n"
            if ending_state:
                prompt += f"场景结束状态：{ending_state}\n"

        if previous_scene_ending:
            prompt += f"\n上一场景结尾：{previous_scene_ending}\n"
        if previous_scenes_summary:
            prompt += f"前序场景摘要：{previous_scenes_summary}\n"

        if repair_plan and isinstance(repair_plan, dict):
            prompt += "\n### 修复计划\n"
            if repair_plan.get("root_cause"):
                prompt += f"根因：{repair_plan['root_cause']}\n"
            if repair_plan.get("preserve"):
                prompt += "必须保留的内容：\n"
                for item in repair_plan["preserve"]:
                    prompt += f"  ✓ {item}\n"
            if repair_plan.get("remove"):
                prompt += "必须删除的内容：\n"
                for item in repair_plan["remove"]:
                    prompt += f"  ❌ {item}\n"

        if rejected_text:
            prompt += (
                "\n### 上一版被拒绝正文（只用于避免重复问题，不得照抄叙事结构）\n"
                f"{rejected_text}\n"
            )

        prompt += "\n请基于以上合同从头生成完整场景正文。"
        return prompt

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _forbidden_terms(scene_contract: dict) -> list[str]:
        if not isinstance(scene_contract, dict):
            return []
        terms: list[str] = []
        forbidden = scene_contract.get("forbidden")
        if isinstance(forbidden, list):
            terms.extend(str(item) for item in forbidden if item)
        quality_extensions = scene_contract.get("quality_extensions")
        if isinstance(quality_extensions, dict):
            reveal_control = quality_extensions.get("reveal_control")
            if isinstance(reveal_control, dict):
                future = reveal_control.get("forbidden_future_concepts")
                if isinstance(future, list):
                    terms.extend(str(item) for item in future if item)
        experience_contract = scene_contract.get("experience_contract")
        if isinstance(experience_contract, dict):
            withheld = experience_contract.get("withheld_information")
            if isinstance(withheld, list):
                terms.extend(str(item) for item in withheld if item)
        return terms

    @staticmethod
    def _extract_malformed_repaired_text(response: str) -> str:
        """Recover only an explicit repaired_text field from malformed JSON."""
        text = (response or "").strip()
        match = re.search(
            r'"repaired_text"\s*:\s*"(.*)"\s*,\s*"repairs"\s*:',
            text,
            re.DOTALL,
        )
        if not match:
            return ""
        repaired = match.group(1)
        return (
            repaired
            .replace(r"\\", "\\")
            .replace(r"\"", '"')
            .replace(r"\n", "\n")
            .strip()
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _success_result(text: str, *, strategy: str = "none") -> dict:
        return {
            "success": True,
            "repaired_text": text,
            "repairs": [],
            "strategy": strategy,
        }

    @staticmethod
    def _error_result(text: str, error: str, *, strategy: str = "none") -> dict:
        return {
            "success": False,
            "repaired_text": text,
            "repairs": [],
            "strategy": strategy,
            "error": error,
        }
