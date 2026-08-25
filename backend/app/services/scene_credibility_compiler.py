from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from app.models.scene_credibility import (
    CharacterKnowledgeSnapshot,
    ContractSourceRef,
    FactBoundaryItem,
    NarrationCredibilityRule,
    PlausibilityRule,
    SceneCredibilityCompiled,
    SceneCredibilityContract,
)


_MAX_FACTS = 10
_MAX_KNOWLEDGE = 8
_MAX_RULES = 6


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, tuple):
        return [item for item in value if item not in (None, "")]
    return [value]


def _clean_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


class SceneCredibilityCompiler:
    """Compile a compact scene credibility contract from existing context.

    The compiler is deliberately additive: it does not require the editor agent
    to emit a new schema. It reads the current scene contract and existing fact
    contracts, then emits a small SCP payload plus prompt guidance.
    """

    def compile(self, context: dict) -> SceneCredibilityCompiled:
        scene_contract = _as_dict(context.get("scene_contract") or context.get("raw_scene_contract"))
        fact_contract = _as_dict(context.get("fact_contract"))
        scene_provenance = _as_dict(
            context.get("scene_provenance")
            or scene_contract.get("scene_provenance")
        )
        chapter_state = _as_dict(context.get("chapter_state"))
        story_state = _as_dict(context.get("story_state") or context.get("current_state"))
        character_cards = _as_list(context.get("character_cards"))
        quality_memory = _as_dict(context.get("quality_memory"))

        project_id = str(context.get("project_id") or "")
        chapter_number = int(context.get("chapter_number") or scene_contract.get("chapter_number") or 0)
        scene_index = int(context.get("scene_index") or scene_contract.get("scene_index") or 0)

        warnings: list[str] = []
        facts = self._compile_fact_boundaries(scene_contract, fact_contract, scene_provenance)
        if not facts:
            warnings.append("未能提炼硬事实边界，可信度层将仅提供通用表达约束。")

        knowledge = self._compile_knowledge_snapshots(
            scene_contract,
            scene_provenance,
            chapter_state,
            story_state,
            character_cards,
        )
        plausibility = self._compile_plausibility_rules(scene_contract, quality_memory)
        narration = self._compile_narration_rules(quality_memory)
        acceptance = [
            "正文不得写出与事实边界互斥的身份、地点、对象归属、时间或状态。",
            "角色判断必须来自观察、对话、记忆片段、身体反应或既有信息，不能直接获得未公开真相。",
            "重要心理变化必须有可感知触发，避免用抽象总结替代场景过程。",
            "叙事表达应优先呈现动作、停顿、视线、感官和选择，减少解释腔。",
        ]

        contract = SceneCredibilityContract(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            fact_boundaries=facts[:_MAX_FACTS],
            knowledge_snapshots=knowledge[:_MAX_KNOWLEDGE],
            plausibility_rules=plausibility[:_MAX_RULES],
            narration_rules=narration[:_MAX_RULES],
            acceptance_criteria=acceptance,
            compiler_warnings=warnings,
            source_refs=[ContractSourceRef(source_type="scene_contract", source_id=str(scene_contract.get("scene_id", "")), confidence="high")],
        )

        return SceneCredibilityCompiled(
            contract=contract,
            quality_extensions_patch=self._build_quality_extensions_patch(contract),
            compiler_warnings=warnings,
        )

    def _compile_fact_boundaries(
        self,
        scene_contract: dict,
        fact_contract: dict,
        scene_provenance: dict,
    ) -> list[FactBoundaryItem]:
        facts: list[FactBoundaryItem] = []

        def add_fact(label: str, allowed: list[str] | None = None, forbidden: list[str] | None = None, source: str = "scene_contract") -> None:
            allowed_clean = [_clean_text(item) for item in _as_list(allowed) if _clean_text(item)]
            forbidden_clean = [_clean_text(item) for item in _as_list(forbidden) if _clean_text(item)]
            if not allowed_clean and not forbidden_clean:
                return
            seed = f"{label}:{allowed_clean}:{forbidden_clean}:{source}"
            facts.append(FactBoundaryItem(
                fact_id=_stable_id("fact", seed),
                label=label,
                allowed_claims=allowed_clean[:4],
                forbidden_claims=forbidden_clean[:4],
                source_refs=[ContractSourceRef(source_type=source, confidence="high")],
            ))

        source_of_truth = _as_dict(scene_contract.get("source_of_truth"))
        editor_enrichment = _as_dict(scene_contract.get("editor_enrichment"))

        add_fact(
            "场景目标与冲突",
            allowed=[
                source_of_truth.get("goal") or scene_contract.get("goal"),
                source_of_truth.get("conflict") or scene_contract.get("conflict"),
            ],
            source="scene_contract",
        )
        add_fact(
            "禁止项",
            forbidden=[
                *_as_list(scene_contract.get("forbidden")),
                *_as_list(source_of_truth.get("forbidden_outline")),
                *_as_list(editor_enrichment.get("additional_forbidden")),
            ],
            source="scene_contract",
        )

        provenance_facts = _as_dict(scene_provenance.get("current_facts"))
        add_fact(
            "当前已确立事实",
            allowed=[
                *_as_list(provenance_facts.get("established_facts")),
                *_as_list(provenance_facts.get("completed_events")),
                *_as_list(provenance_facts.get("active_constraints")),
            ],
            source="scene_provenance",
        )
        character_states = _as_dict(provenance_facts.get("character_states"))
        for name, state in list(character_states.items())[:5]:
            add_fact(
                f"角色状态：{_clean_text(name, 40)}",
                allowed=[f"{name}：{_clean_text(state)}"],
                source="scene_provenance",
            )

        spatial_anchor = _as_dict(scene_provenance.get("spatial_anchor"))
        add_fact(
            "空间锚点",
            allowed=[
                spatial_anchor.get("current_location") or scene_contract.get("location_anchor"),
                spatial_anchor.get("destination_location"),
            ],
            source="scene_provenance",
        )
        timeline_anchor = _as_dict(scene_provenance.get("timeline_anchor"))
        add_fact(
            "时间锚点",
            allowed=[
                timeline_anchor.get("current_time") or scene_contract.get("temporal_anchor"),
                timeline_anchor.get("opening_state") or scene_contract.get("opening_state"),
                timeline_anchor.get("ending_state") or scene_contract.get("ending_state"),
            ],
            forbidden=timeline_anchor.get("forbidden_recap_events"),
            source="scene_provenance",
        )

        for item in _as_list(fact_contract.get("current_facts"))[:6]:
            add_fact("命题层当前事实", allowed=[item], source="fact_contract")
        for item in _as_list(fact_contract.get("forbidden_assertions"))[:6]:
            if isinstance(item, Mapping):
                forbidden = item.get("event_type") or item.get("reason")
            else:
                forbidden = item
            add_fact("命题层禁止确认", forbidden=[forbidden], source="fact_contract")

        deduped: dict[str, FactBoundaryItem] = {}
        for item in facts:
            key = f"{item.label}:{item.allowed_claims}:{item.forbidden_claims}"
            deduped.setdefault(key, item)
        return list(deduped.values())

    def _compile_knowledge_snapshots(
        self,
        scene_contract: dict,
        scene_provenance: dict,
        chapter_state: dict,
        story_state: dict,
        character_cards: list,
    ) -> list[CharacterKnowledgeSnapshot]:
        pov_name = (
            scene_contract.get("pov_character")
            or _as_dict(scene_contract.get("pov")).get("character")
            or chapter_state.get("pov_character")
            or story_state.get("pov_character")
            or ""
        )
        if not pov_name and character_cards:
            first = character_cards[0]
            if isinstance(first, Mapping):
                pov_name = str(first.get("name", ""))

        if not pov_name:
            return []

        provenance_facts = _as_dict(scene_provenance.get("current_facts"))
        timeline_anchor = _as_dict(scene_provenance.get("timeline_anchor"))
        spatial_anchor = _as_dict(scene_provenance.get("spatial_anchor"))
        knows = [
            timeline_anchor.get("opening_state"),
            spatial_anchor.get("current_location"),
            *_as_list(provenance_facts.get("established_facts"))[:3],
        ]
        cannot_know = [
            "未在当前视角、前文、对话、线索或场景合同中公开的信息。",
            "尚未发生或尚未揭示的完整因果链。",
            "其他角色未表达的真实动机和隐秘计划。",
        ]
        return [CharacterKnowledgeSnapshot(
            character_id=_stable_id("char", pov_name),
            display_name=pov_name,
            knows=[_clean_text(item) for item in knows if _clean_text(item)][:5],
            can_infer=[
                "可根据现场异常、物件状态、对话压力、身体反应和既有记忆片段进行有限推断。"
            ],
            cannot_know=cannot_know,
            evidence_required_for=[
                "确认对象归属、身份变化、幕后责任、远处事件或未来结果时，正文必须提供信息来源。"
            ],
        )]

    def _compile_plausibility_rules(self, scene_contract: dict, quality_memory: dict) -> list[PlausibilityRule]:
        rules = [
            PlausibilityRule(
                rule_id="memory_fragment_rule",
                description="记忆、推理和判断必须有触发物或信息来源，不能无触发地完整展开大量细节。",
                avoid_patterns=["无触发完整回忆", "瞬间知道全部真相", "闭眼完整复盘"],
                preferred_methods=["物件触发", "对话触发", "身体反应触发", "不完整记忆片段"],
            ),
            PlausibilityRule(
                rule_id="state_action_rule",
                description="角色行动必须与身体状态、情绪状态、所处空间和可用资源匹配。",
                avoid_patterns=["虚弱状态下高强度行动无代价", "环境条件与行动不匹配"],
                preferred_methods=["动作代价", "犹豫停顿", "失败尝试", "借助外部条件"],
            ),
            PlausibilityRule(
                rule_id="causal_evidence_rule",
                description="重要结论必须由观察、对话、既有事实或线索支撑。",
                avoid_patterns=["没有证据直接确定", "没有过渡突然转念"],
                preferred_methods=["先观察后判断", "先误判后修正", "保留不确定性"],
            ),
        ]
        patterns = _as_list(quality_memory.get("recent_quality_patterns"))
        if any(isinstance(item, Mapping) and "explanatory" in str(item.get("type", "")) for item in patterns):
            rules.append(PlausibilityRule(
                rule_id="quality_memory_explanation_guard",
                description="项目近期出现解释性叙述偏多，本场景应加强场景化证据。",
                avoid_patterns=["用抽象总结替代体验"],
                preferred_methods=["动作", "视线", "停顿", "对白潜台词"],
            ))
        return rules

    def _compile_narration_rules(self, quality_memory: dict) -> list[NarrationCredibilityRule]:
        rules = [
            NarrationCredibilityRule(
                rule_id="explanatory_dash_rule",
                description="避免用连续破折号拆解角色意图或替读者解释判断过程。",
                avoid_patterns=["——不是", "——并非", "——而是", "连续解释性破折号"],
                preferred_methods=["改用动作承接", "改用短句停顿", "改用对话压力"],
            ),
            NarrationCredibilityRule(
                rule_id="not_a_but_c_rule",
                description="避免密集使用“不是A，不是B，而是C”式心理解释。",
                avoid_patterns=["不是A，不是B，而是C", "不是……而是……"],
                preferred_methods=["让角色通过选择和反应体现判断"],
            ),
            NarrationCredibilityRule(
                rule_id="conclusion_verb_rule",
                description="减少“意识到、确认、明白、这意味着”后接大段抽象结论。",
                avoid_patterns=["显式认知动词后接总结", "模型式复盘"],
                preferred_methods=["把结论拆进可见动作、物件细节和对话潜台词"],
            ),
        ]
        patterns = _as_list(quality_memory.get("recent_quality_patterns"))
        if any(isinstance(item, Mapping) and "rhythm" in str(item.get("type", "")) for item in patterns):
            rules.append(NarrationCredibilityRule(
                rule_id="quality_memory_rhythm_guard",
                description="项目近期出现节奏过均，本场景应避免句段长度和承接方式过于整齐。",
                avoid_patterns=["均匀长句", "机械承接"],
                preferred_methods=["长短句变化", "段落呼吸", "动作与对白交替"],
            ))
        return rules

    def _build_quality_extensions_patch(self, contract: SceneCredibilityContract) -> dict:
        fact_guidance = []
        for item in contract.fact_boundaries[:6]:
            if item.allowed_claims:
                fact_guidance.append(f"{item.label}：必须遵守 {'；'.join(item.allowed_claims[:3])}")
            if item.forbidden_claims:
                fact_guidance.append(f"{item.label}：不得写成 {'；'.join(item.forbidden_claims[:3])}")

        knowledge_guidance = []
        for item in contract.knowledge_snapshots[:3]:
            if item.display_name:
                knowledge_guidance.append(
                    f"{item.display_name} 的判断必须来自已知信息、观察、对话、线索或身体反应；不得直接知道未公开真相。"
                )

        plausibility_guidance = [
            rule.description
            for rule in contract.plausibility_rules[:4]
            if rule.description
        ]
        narration_guidance = [
            rule.description
            for rule in contract.narration_rules[:4]
            if rule.description
        ]

        return {
            "schema_version": 1,
            "scene_credibility_guidance": [
                *fact_guidance[:6],
                *knowledge_guidance[:3],
                *plausibility_guidance[:4],
                *narration_guidance[:4],
            ],
            "fact_boundary_guidance": fact_guidance[:6],
            "knowledge_boundary_guidance": knowledge_guidance[:3],
            "plausibility_guidance": plausibility_guidance[:4],
            "narration_credibility_guidance": narration_guidance[:4],
        }
