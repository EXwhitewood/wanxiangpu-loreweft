"""SceneValidator — merged consistency + contract + reader-experience validator."""
import json, logging, re, hashlib
from app.agents.base import BaseAgent
from app.services.json_response import parse_json_response
from app.services.scene_provenance import render_scene_provenance
from app.models.violation import make_violation
from app.models.reader_experience import ReaderExperienceReport, ReaderExperienceScores
from app.models.quality_advisory import make_advisory
from app.services.recovery_metrics import RecoveryMetrics
from app.services.temporal_layer_policy import detect_temporal_layer_confusion
from app.services.llm_task_profiles import LLMTaskType
from app.services.llm_client import build_prompt_cache_policy

_logger = logging.getLogger(__name__)

_CRITIC_TYPE_ALIASES = {
    "plot_logic_error": "causal_chain_error", "plot_hole": "causal_chain_error",
    "logic_error": "causal_chain_error", "timeline_confusion": "canon_timeline_confusion",
    "spatial_error": "spatial_consistency_error", "clue_source_error": "clue_provenance_error",
}
_KNOWN_CRITIC_TYPES = {
    "missing_must_show", "forbidden_triggered", "ending_state_not_reached",
    "fact_conflict", "clue_provenance_error", "causal_chain_error",
    "spatial_consistency_error", "canon_timeline_confusion",
    "timeline_conflict", "setting_conflict", "pov_conflict",
    "identity_conflict", "naming_conflict", "internal_conflict",
}

# Sentinel for truncated LLM result
_LLM_FALLBACK = "__LLM_FALLBACK__"

class SceneValidator(BaseAgent):
    name = "scene_validator"

    async def execute(self, context: dict) -> dict:
        generated_text = context.get("generated_text", "")
        scene_contract = context.get("scene_contract", {})
        core_facts = context.get("core_facts", {})
        current_state = context.get("current_state", {})
        chapter_state = context.get("chapter_state", {})
        character_names = context.get("character_names", [])
        character_cards = context.get("character_cards", [])
        project = context.get("project")
        project_id = context.get("project_id")
        chapter_number = scene_contract.get("chapter_number") or context.get("chapter_number") or 0
        scene_index = scene_contract.get("scene_index") or context.get("scene_index") or 0
        genre = context.get("genre", "")
        target_reader = context.get("target_reader", "中文网文读者")
        previous_scene_ending = str(context.get("previous_scene_ending") or "")
        previous_scenes_summary = str(context.get("previous_scenes_summary") or "")

        if not generated_text:
            return self._build_report(
                [make_violation("empty_text", "critical", "生成文本为空",
                    source="deterministic", expected_behavior="Writer 应产出非空正文")],
                None, self._text_hash(generated_text))
        if not scene_contract:
            return self._build_report(
                [make_violation("missing_contract", "critical", "场景合同缺失，无法校验",
                    source="deterministic", expected_behavior="编译器应产出有效合同")],
                None, self._text_hash(generated_text))

        # Phase 1
        det_violations = self._deterministic_check(
            generated_text, scene_contract, chapter_state,
            character_names=character_names,
            is_book_first_scene=(chapter_number == 1 and scene_index == 0))

        signal_types = {"ghost_character", "insufficient_sensory_in_opening", "unprovenanced_clue"}
        signal_v = [v for v in det_violations if v.get("type") not in signal_types]
        signal_crit = sum(1 for v in signal_v if v.get("severity") == "critical")
        if signal_crit >= 2 and any(v.get("blocks_commit") for v in signal_v if v.get("severity") == "critical"):
            return self._build_report(det_violations, None, self._text_hash(generated_text))

        # Phase 2
        llm_result = await self._unified_llm_check(
            generated_text=generated_text, scene_contract=scene_contract,
            core_facts=core_facts, current_state=current_state,
            chapter_state=chapter_state, character_cards=character_cards,
            character_names=character_names, chapter_number=chapter_number,
            scene_index=scene_index, genre=genre, target_reader=target_reader,
            previous_scene_ending=previous_scene_ending,
            previous_scenes_summary=previous_scenes_summary)

        return self._build_report(det_violations, llm_result, self._text_hash(generated_text))

    # ------------------------------------------------------------------
    # Phase 1: 确定性检查
    # ------------------------------------------------------------------
    def _deterministic_check(self, text: str, contract: dict, chapter_state: dict,
                             character_names: list | None = None,
                             is_book_first_scene: bool = False) -> list:
        violations: list = []

        # ---- forbidden 字面检查 ----
        forbidden = contract.get("forbidden", [])
        for item in forbidden:
            if not item:
                continue
            item_str = str(item).strip()
            if not item_str:
                continue
            if len(item_str) <= 4:
                keywords = [item_str]
            else:
                keywords = [kw for kw in item_str.replace("，", ",").split(",") if len(kw.strip()) >= 4]
            for kw in keywords:
                kw = kw.strip()
                if len(kw) >= 4 and kw in text:
                    violations.append(make_violation(
                        "forbidden_triggered", "high",
                        f"禁止内容被触发：正文包含「{kw}」（来自禁止项「{item_str}」）",
                        source="deterministic", target_span=kw,
                        expected_behavior=f"正文不得包含「{kw}」"))
                    break

        # ---- must_show 字面锚点 ----
        for anchor in self._literal_must_show_anchors(contract):
            if anchor not in text:
                violations.append(make_violation(
                    "missing_must_show", "high",
                    f"必须出现的字面锚点未体现：「{anchor}」",
                    source="deterministic",
                    expected_behavior=f"正文应明确出现字面锚点「{anchor}」"))

        # ---- 已完成事件重复描述 ----
        completed_events = chapter_state.get("completed_events", [])
        for evt in completed_events:
            if not evt:
                continue
            evt_keywords = evt[:20]
            if evt_keywords and evt_keywords in text:
                violations.append(make_violation(
                    "event_repeated", "high",
                    f"已完成事件被重复描述：「{evt_keywords}」",
                    source="deterministic", target_span=evt_keywords,
                    expected_behavior="已完成事件只能一句话提及，不得重写过程"))

        # ---- forbidden_recap_events ----
        forbidden_recap = contract.get("forbidden_recap_events", [])
        for evt in forbidden_recap:
            if not evt:
                continue
            evt_kw = evt[:15]
            if evt_kw and evt_kw in text:
                context_window = 80
                pos = text.find(evt_kw)
                while pos != -1:
                    start = max(0, pos - context_window)
                    end = min(len(text), pos + len(evt_kw) + context_window)
                    snippet = text[start:end]
                    narrative_verbs = ["想起", "回忆", "记得", "那天", "当时", "之前", "曾经"]
                    is_recap = not any(v in snippet for v in narrative_verbs)
                    if is_recap:
                        violations.append(make_violation(
                            "forbidden_recap_violation", "high",
                            f"禁止重写的事件被完整重述：「{evt_kw}」。已完成事件只能一句话提及，不得重写过程。",
                            source="deterministic", target_span=evt_kw,
                            expected_behavior="已完成事件只能一句话提及，不得重写过程"))
                        break
                    pos = text.find(evt_kw, pos + 1)

        # ---- temporal_layer_confusion ----
        opening_state = contract.get("opening_state", "")
        if opening_state:
            temporal_finding = detect_temporal_layer_confusion(text, contract)
            if temporal_finding:
                violations.append(make_violation(
                    "temporal_layer_confusion", "high",
                    f"正文中出现{temporal_finding['count']}处闪回标记，可能存在时间层级混乱：当前场景和前史回忆混在一起。opening_state已指定场景起点，不得大段回溯前史。",
                    source="deterministic",
                    target_span=temporal_finding.get("target_span") or "",
                    expected_behavior="当前场景和前史回忆应分层处理，不得混在一起"))

        # ---- ghost_character ----
        if character_names:
            whitelist = set()
            for name in character_names:
                if isinstance(name, str):
                    whitelist.add(name)
                    if len(name) > 2:
                        whitelist.add(name[:2])
            non_character_words = {
                "此时", "此刻", "此间", "此地", "此物", "此事",
                "什么", "怎么", "为什么", "那么", "那么大", "这么",
                "自己", "别人", "对方", "一人", "两人", "三人",
                "师兄", "师姐", "师弟", "师妹", "师尊", "师父", "师傅",
                "管事", "长老", "掌门", "弟子", "杂役", "散修",
                "内门", "外门", "宗门",
                "储物", "灵石", "灵根", "灵气", "灵药", "灵兽",
                "丹药", "法器", "符箓", "剑意", "剑心", "剑道",
                "炼气", "筑基", "金丹", "元婴", "化神",
                "疗伤", "解毒",
                "角落", "窗外", "门外", "房间", "床上", "桌上",
                "牛车", "坊市", "官道", "山门", "角门",
                "干粮", "饼子", "匕首", "令牌", "身份",
                "脚步", "声音", "目光", "呼吸", "心跳",
                "一天", "两天", "三天", "拂晓", "黄昏", "清晨",
                "原主", "原书", "穿越", "穿书", "炮灰", "重生",
                "第一", "第二", "第三", "最后", "唯一"}
            potential_names = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
            seen: set[str] = set()
            for name in potential_names:
                if name in seen or len(name) < 2 or name in whitelist:
                    seen.add(name); continue
                if any(w in name or name in w for w in whitelist):
                    seen.add(name); continue
                if name in non_character_words:
                    seen.add(name); continue
                count = text.count(name)
                if count >= 3:
                    action_verbs = ["说", "问", "喊", "道", "笑", "叹", "怒", "惊"]
                    has_dialogue = any(f'{name}{v}' in text for v in action_verbs) or f'「{name}' in text
                    has_action = any(f'{name}{v}' in text for v in ["站", "走", "跑", "坐", "拿", "推", "拉", "抓", "握", "翻", "跳"])
                    severity = "high" if (has_dialogue or has_action) else "medium"
                    violations.append(make_violation(
                        "ghost_character", severity,
                        f"正文中出现未注册角色名「{name}」（出现{count}次），该角色不在当前场景角色卡中{'，且有对话/动作' if (has_dialogue or has_action) else ''}",
                        source="deterministic", target_span=name,
                        expected_behavior="未注册角色不应有对话或动作"))
                seen.add(name)

        # ---- omniscient_in_opening ----
        if is_book_first_scene:
            first_500 = text[:500] if len(text) >= 500 else text
            first_200 = text[:200] if len(text) >= 200 else text
            omniscient_patterns = ["她不知道的是", "他不知道的是", "殊不知", "而在另一个地方", "其实.*?早已"]
            for pattern in omniscient_patterns:
                matches = re.findall(pattern, first_500)
                if matches:
                    violations.append(make_violation(
                        "omniscient_in_opening", "high",
                        f"全书开篇前500字出现全知视角表述：「{matches[0]}」。开篇应锁定POV角色感知，不得使用全知叙述。",
                        source="deterministic", target_span=matches[0],
                        expected_behavior="开篇应锁定POV角色感知，不得使用全知叙述"))
                    break

            # ---- insufficient_sensory_in_opening ----
            senses = []
            sense_keywords = [
                ("视觉", ["看见", "看到", "望向", "映入", "眼前", "光"]),
                ("听觉", ["声音", "响", "听", "喊", "叫", "风声", "脚步声"]),
                ("触觉", ["冷", "热", "痛", "硬", "软", "冰凉", "粗糙"]),
                ("嗅觉", ["气味", "香", "臭", "腥", "药味"]),
                ("味觉", ["苦", "甜", "咸", "涩"])]
            for _sense_type, kws in sense_keywords:
                if any(kw in first_200 for kw in kws):
                    senses.append(_sense_type)
            if len(senses) < 2:
                violations.append(make_violation(
                    "insufficient_sensory_in_opening", "medium",
                    f"全书开篇前200字仅包含{len(senses)}种感官描写（{', '.join(senses) if senses else '无'}），建议至少包含视觉+另一种感官以建立沉浸感。",
                    source="deterministic",
                    expected_behavior="开篇应至少包含视觉+另一种感官描写"))

        # ---- clue_missing_source ----
        clues = contract.get("clues", [])
        if clues:
            for clue in clues:
                desc = clue.get("description", "")
                source_actor = clue.get("source_actor", "")
                if not desc:
                    continue
                desc_kw = desc[:10]
                if desc_kw and desc_kw in text:
                    if source_actor and source_actor not in text:
                        context_window = 200
                        pos = text.find(desc_kw)
                        while pos != -1:
                            start = max(0, pos - context_window)
                            end = min(len(text), pos + len(desc_kw) + context_window)
                            snippet = text[start:end]
                            source_hints = ["留下", "放置", "刻", "写", "留", "布置", "安排", source_actor[:2]]
                            has_source = any(h in snippet for h in source_hints)
                            if not has_source:
                                violations.append(make_violation(
                                    "clue_missing_source", "high",
                                    f"线索「{desc_kw}」在正文中出现，但放置者「{source_actor}」未被提及或暗示。线索必须有来源。",
                                    source="deterministic", target_span=desc_kw,
                                    expected_behavior=f"线索「{desc_kw}」必须有来源「{source_actor}」"))
                                break
                            pos = text.find(desc_kw, pos + 1)

        # ---- unprovenanced_clue ----
        if not clues:
            clue_patterns = [
                r"刻[着了].*?字", r"写[着了].*?字", r"留[着了].*?字",
                r"暗号", r"标记", r"记号", r"符号",
                r"纸条", r"信[件封]", r"留言", r"遗书"]
            for pattern in clue_patterns:
                matches = re.findall(pattern, text)
                if matches:
                    violations.append(make_violation(
                        "unprovenanced_clue", "medium",
                        f"正文中出现疑似线索「{matches[0][:20]}」，但场景合同未声明该线索的来源。建议在合同clues字段中补充来源信息。",
                        source="deterministic", target_span=matches[0][:20],
                        expected_behavior="线索应在合同clues字段中声明来源"))
                    break

        # ---- missing_genre_extension ----
        genre_enrichment = contract.get("genre_enrichment", {})
        if not isinstance(genre_enrichment, dict):
            genre_enrichment = {}
        genre_profile = contract.get("_genre_profile")
        if genre_profile and isinstance(genre_profile, dict):
            extensions = genre_profile.get("extension_validation", [])
            if isinstance(extensions, list):
                for ext in extensions:
                    if not isinstance(ext, dict):
                        continue
                    field_name = ext.get("field", "")
                    required = ext.get("required", False)
                    if not field_name:
                        continue
                    if required and not genre_enrichment.get(field_name):
                        violations.append(make_violation(
                            "missing_genre_extension", "medium",
                            f"题材扩展字段「{field_name}」为必填但未填写。{ext.get('validation', '')}",
                            source="deterministic",
                            expected_behavior=ext.get("validation", f"应填写 {field_name}")))

        return violations
    # ------------------------------------------------------------------
    # must_show 字面锚点提取
    # ------------------------------------------------------------------
    @staticmethod
    def _literal_must_show_anchors(contract: dict) -> list[str]:
        anchors: list[str] = []
        def _append(value):
            if isinstance(value, str):
                value = value.strip()
                if value and value not in anchors:
                    anchors.append(value)
        literal_anchors = contract.get("must_show_literal_anchors", [])
        if not isinstance(literal_anchors, list):
            literal_anchors = [literal_anchors]
        for value in literal_anchors:
            _append(value)
        must_show = contract.get("must_show", [])
        if not isinstance(must_show, list):
            must_show = [must_show]
        for item in must_show:
            if isinstance(item, dict):
                mode = item.get("type") or item.get("check_mode") or item.get("mode")
                if mode == "literal_anchor":
                    _append(item.get("value") or item.get("text") or item.get("anchor"))
            elif isinstance(item, str) and item.startswith("literal:"):
                _append(item.removeprefix("literal:"))
        return anchors

    # ------------------------------------------------------------------
    # 核心事实摘要（保留历史一致性摘要格式）
    # ------------------------------------------------------------------
    @staticmethod
    def _summarize_facts(core_facts: dict) -> str:
        if not core_facts:
            return "（无已知事实）"
        parts: list[str] = []
        characters = {k: v for k, v in core_facts.items()
                      if isinstance(v, dict) and v.get("type") == "character"}
        if characters:
            parts.append("### 角色事实")
            for key, char in characters.items():
                line = f"- {char.get('name', key)}"
                if char.get("original_fate"):
                    fate = char["original_fate"]
                    if isinstance(fate, dict):
                        line += f" | 参考设定（非当前事实）：{fate.get('cause', '')}"
                        if fate.get("timeline"):
                            line += f"（{fate['timeline']}）"
                        if fate.get("antagonist"):
                            line += f"（加害者：{fate['antagonist']}）"
                    else:
                        line += f" | 参考设定（非当前事实）：{fate}"
                # P1-3 修复：fallback 模式，避免字段名错配导致读取为空
                external_goal = char.get("external_goal") or char.get("desire")
                if external_goal:
                    line += f" | 目标：{external_goal}"
                parts.append(line)
        rules = {k: v for k, v in core_facts.items()
                 if isinstance(v, dict) and v.get("type") == "world_rule"}
        if rules:
            parts.append("\n### 世界观铁则")
            for key, rule in rules.items():
                parts.append(f"- {rule.get('name', key)}：{rule.get('description', '')}")
        outlines = {k: v for k, v in core_facts.items()
                    if isinstance(v, dict) and v.get("type") == "outline"}
        if outlines:
            parts.append("\n### 大纲关键设定")
            for key, outline in outlines.items():
                parts.append(f"- 第{outline.get('chapter', '?')}章「{outline.get('title', '')}」：{outline.get('core_conflict', '')}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Phase 2: 统一 LLM 调用
    # ------------------------------------------------------------------
    async def _unified_llm_check(
        self, generated_text: str, scene_contract: dict,
        core_facts: dict, current_state: dict, chapter_state: dict,
        character_cards: list | None, character_names: list | None,
        chapter_number: int, scene_index: int,
        genre: str, target_reader: str,
        previous_scene_ending: str = "",
        previous_scenes_summary: str = "",
    ) -> dict | None:
        must_show = scene_contract.get("must_show", [])
        forbidden = scene_contract.get("forbidden", [])
        ending_state = scene_contract.get("ending_state", "")
        facts_summary = self._summarize_facts(core_facts)

        system_prompt = (
            "你是一位场景综合审稿员，同时负责三个维度的校验。你必须输出严格的 JSON 格式，不要输出任何其他内容。\n\n"
            "## 维度一：一致性检查\n"
            "检查文本是否存在：\n"
            "1. 外部矛盾：文本描述与已知核心事实或世界状态不一致\n"
            "2. 内部矛盾：文本自身前后矛盾（同一角色命运两种版本、时间线不一致、同一角色不同名字、同一事实矛盾陈述）\n"
            "冲突分类必须是以下之一：fact_conflict / timeline_conflict / identity_conflict / "
            "pov_conflict / naming_conflict / internal_conflict / setting_conflict\n\n"
            "CONSISTENCY EVIDENCE RULE: every hard consistency conflict must declare authority_source and "
            "evidence_spans made of continuous verbatim substrings copied from the generated prose. A conflict "
            "against core_facts/current_state needs at least one prose span; a conflict whose authority_source is "
            "current_text needs at least two distinct prose spans. A summary, inference, or diagnostic description "
            "is not evidence. For a duplicate-passage claim, "
            "provide both full passages (not merely their start/end anchors) and report it only when they narrate "
            "the same event without causal progression. Sequential phases that reuse motifs such as cold, blurred "
            "vision, light, or repeated character presence are not duplicates. If two verbatim spans cannot be "
            "provided, do not report an internal conflict.\n\n"
            "CAUSAL STATE-TRANSITION RULE: two different states at different moments are not contradictory when "
            "the prose explicitly shows a cause or transition between them, including loss or recovery of memory, "
            "transformation, disguise, injury, death/revival, relocation, or passage of time. Before reporting an "
            "identity/timeline/internal conflict, scan the prose between both evidence spans. If a verbatim transition "
            "sentence explains the change, do not report the conflict. Use transition_evidence to record the checked "
            "sentence during self-audit; a conflict carrying valid transition_evidence will be rejected.\n\n"
            "OPEN-WORLD EVIDENCE RULE: compressed context is incomplete. A fact, item, alias, or state "
            "not appearing in the supplied context does NOT prove it does not exist. Never use "
            "'not mentioned', 'not established', or lack of a record as an established fact. "
            "An external conflict requires an explicit positive or negative authority fact.\n\n"
            "CHRONOLOGY PRECEDENCE RULE: the persisted current_state is the state at the chapter "
            "opening. Supplied previous-scene text occurs later than that baseline. If it explicitly "
            "shows a causal state transition, that transition supersedes the older baseline for the "
            "scene being reviewed. Report a conflict only when the current scene contradicts the "
            "latest explicit state; never demand restoration of a state that an earlier scene in the "
            "same chapter has already changed.\n\n"
            "## 维度二：合同合规检查\n"
            "1. must_show 中的每一项是否在正文中被体现（语义层面，不要求字面匹配）\n"
            "2. forbidden 中的每一项是否在正文中出现（语义层面，不要求字面匹配）\n"
            "3. ending_state 是否达成\n"
            "4. 线索来源（clue_provenance）：线索/遗留物/暗号/刻字/留言是否有明确放置者和来源\n"
            "5. 因果链（causal_chain）：事件之间是否有因果关系、角色行动是否有动机支撑\n"
            "6. 空间一致性（spatial_consistency）：人物/物品/地点是否空间自洽\n"
            "7. 参考设定与当前时间线：参考设定是否被误写成当前已发生事实\n"
            "8. 伏笔操作：若合同含 foreshadowing_ops，检查正文是否按 narrative_instruction 自然完成；内部 thread_id 不应直接出现在正文中\n\n"
            "## 维度三：读者体验评估\n"
            "你是冷读者体验评审，只能根据读者已经看到的信息评价小说片段，不要使用未来剧情、隐藏设定或作者意图。\n"
            "评分维度（0-10分）：\n"
            "- continue_reading：继续阅读欲望\n"
            "- clarity：清晰度（冷读者能否理解当前场景）\n"
            "- cognitive_load：认知负担（越低越好，10分=极度烧脑）\n"
            "- emotional_engagement：情绪投入\n"
            "- pacing：节奏评价\n"
            "如有明显体验问题，在 advisories 中给出建议。\n\n"
            "## 输出格式\n"
            '{"consistency": {"pass": bool, "conflicts": [{"fact": "已知事实", "text_claim": "文本矛盾描述", "authority_source": "core_facts/current_state/current_text/scene_contract", "evidence_spans": ["正文逐字证据A", "正文逐字证据B"], "transition_evidence": "若存在则填写正文逐字状态转换句，否则为空", "category": "冲突分类", "severity": "critical/warning", "scope": "local/scene", "suggestion": "修正建议"}]},'
            ' "contract_compliance": {"violations": [{"type": "violation_type", "severity": "high/medium", "detail": "描述", "target_span": "正文片段", "expected_behavior": "正确行为"}]},'
            ' "reader_experience": {"scores": {"continue_reading": N, "clarity": N, "cognitive_load": N, "emotional_engagement": N, "pacing": N}, "advisories": [{"type": "advisory_type", "severity": "high/medium/low", "detail": "描述", "expected_behavior": "建议"}]}}\n\n'
            "如果某维度完全通过，对应列表为空。severity 定义：\n"
            "- critical/high：forbidden 被触发、must_show 缺失、ending_state 未达成、线索无来源、参考设定被当成当前事实、空间矛盾明显\n"
            "- medium/low：轻微不一致或体验建议\n"
        )

        user_prompt = "### 场景合同\n"
        source_of_truth = scene_contract.get("source_of_truth")
        if isinstance(source_of_truth, dict) and source_of_truth:
            user_prompt += "\n#### 大纲指定（不可修改）\n"
            user_prompt += f"目标：{source_of_truth.get('goal', scene_contract.get('goal', ''))}\n"
            user_prompt += f"冲突：{source_of_truth.get('conflict', scene_contract.get('conflict', ''))}\n"
            if source_of_truth.get("outline_outcome"):
                user_prompt += f"大纲结果方向：{source_of_truth['outline_outcome']}\n"
            for label, key in [("大纲要求展示", "must_show_outline"), ("大纲禁止", "forbidden_outline"), ("伏笔操作", "foreshadowing_ops")]:
                val = source_of_truth.get(key, [])
                if val:
                    user_prompt += f"{label}：{json.dumps(val, ensure_ascii=False)}\n"
            editor_enrichment = scene_contract.get("editor_enrichment", {})
            if isinstance(editor_enrichment, dict) and editor_enrichment:
                user_prompt += "\n#### 主编补充\n"
                if editor_enrichment.get("ending_state"):
                    user_prompt += f"场景结束状态：{editor_enrichment['ending_state']}\n"
                for label, key in [("额外必须展示", "additional_must_show"), ("额外禁止", "additional_forbidden")]:
                    val = editor_enrichment.get(key, [])
                    if val:
                        user_prompt += f"{label}：{json.dumps(val, ensure_ascii=False)}\n"
        else:
            user_prompt += f"目标：{scene_contract.get('goal', '')}\n"
            user_prompt += f"冲突：{scene_contract.get('conflict', '')}\n"
            if must_show:
                user_prompt += f"必须展示：{json.dumps(must_show, ensure_ascii=False)}\n"
            if forbidden:
                user_prompt += f"绝对禁止：{json.dumps(forbidden, ensure_ascii=False)}\n"
            if ending_state:
                user_prompt += f"场景结束状态：{ending_state}\n"

        scene_provenance = scene_contract.get("scene_provenance", {})
        if scene_provenance:
            rendered = render_scene_provenance(scene_provenance)
            if rendered:
                user_prompt += "\n### 统一场景事实层（scene_provenance）\n" + rendered + "\n"

        user_prompt += f"\n### 核心事实\n{facts_summary}\n"
        user_prompt += f"\n### 当前状态\n{json.dumps(current_state, ensure_ascii=False, indent=2)[:2000]}\n"
        if previous_scenes_summary or previous_scene_ending:
            user_prompt += (
                "\n### 本章前序场景（时间上晚于上述章节开场状态）\n"
                "若这里明确发生状态变化，以变化后的最新状态审校当前场景；"
                "不得要求正文恢复较早基线。\n"
            )
            if previous_scenes_summary:
                user_prompt += f"前序场景压缩片段：\n{previous_scenes_summary[-1800:]}\n"
            if previous_scene_ending:
                user_prompt += f"紧邻上一场景结尾：\n{previous_scene_ending[-1800:]}\n"
        user_prompt += f"\n### 读者上下文\n题材：{genre}\n目标读者：{target_reader}\n章节：{chapter_number} 场景：{scene_index}\n"

        max_chars = 8000
        if len(generated_text) > max_chars:
            user_prompt += f"\n### 生成的正文（前半部分）\n{generated_text[:max_chars//2]}\n"
            user_prompt += f"\n### 生成的正文（后半部分）\n{generated_text[len(generated_text)-max_chars//2:]}\n"
        else:
            user_prompt += f"\n### 生成的正文\n{generated_text}\n"

        user_prompt += "\n请从一致性、合同合规、读者体验三个维度逐项检查，输出 JSON："

        response = ""
        try:
            llm = await self.get_llm_client()
            # 费用优化：system_prompt 完全固定，启用 prompt cache
            _cache_policy, _stable_hash = build_prompt_cache_policy(system_prompt)
            response = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1,
                task_type=LLMTaskType.JSON_AUDIT,
                response_format={"type": "json_object"},
                cache_policy=_cache_policy,
                stable_prefix_hash=_stable_hash)
            result = parse_json_response(response)
            RecoveryMetrics().record_parse_attempt(success=True)
            if not isinstance(result, dict):
                result = {}
            return result
        except Exception as exc:
            RecoveryMetrics().record_parse_attempt(success=False)
            _logger.warning("SceneValidator LLM parse failed", extra={
                "error": str(exc),
                "response_preview": (response if isinstance(response, str) else str(response))[:500]})
            return None

    # ------------------------------------------------------------------
    # 构建统一报告
    # ------------------------------------------------------------------
    def _build_report(
        self,
        deterministic_violations: list | None,
        llm_result: dict | None,
        text_hash: str,
    ) -> dict:
        det_violations = deterministic_violations or []

        # ---- 解析 LLM 结果 ----
        consistency_section: dict = {"pass": True, "conflicts": [], "suggestions": []}
        contract_llm_violations: list = []
        reader_experience_section: dict = {
            "scores": {"continue_reading": 0, "clarity": 0, "cognitive_load": 0,
                       "emotional_engagement": 0, "pacing": 0},
            "advisories": []}

        if llm_result and isinstance(llm_result, dict):
            # 一致性
            raw_consistency = llm_result.get("consistency", {})
            raw_consistency = llm_result.get("consistency", {})
            if isinstance(raw_consistency, dict):
                consistency_section["pass"] = raw_consistency.get("pass", True)
                raw_conflicts = raw_consistency.get("conflicts", [])
                if isinstance(raw_conflicts, list):
                    for c in raw_conflicts:
                        if isinstance(c, dict):
                            c.setdefault("category", "internal_conflict")
                            c.setdefault("severity", "warning")
                            c.setdefault("scope", "local")
                            consistency_section["conflicts"].append(c)
                raw_suggestions = raw_consistency.get("suggestions", [])
                if isinstance(raw_suggestions, list):
                    consistency_section["suggestions"] = raw_suggestions

            # 合同合规
            raw_contract = llm_result.get("contract_compliance", {})
            if isinstance(raw_contract, dict):
                raw_cv = raw_contract.get("violations", [])
                if isinstance(raw_cv, list):
                    for rv in raw_cv:
                        if not isinstance(rv, dict):
                            continue
                        raw_type = str(rv.get("type", "semantic_quality_error"))
                        vtype = _CRITIC_TYPE_ALIASES.get(raw_type, raw_type)
                        suggested_strategy = None
                        if vtype not in _KNOWN_CRITIC_TYPES:
                            vtype = "semantic_quality_error"
                            suggested_strategy = "rewrite_scene"
                        contract_llm_violations.append(make_violation(
                            vtype=vtype,
                            severity=rv.get("severity", "high"),
                            detail=f"[{raw_type}] {rv.get('detail', '')}" if vtype == "semantic_quality_error" else rv.get("detail", ""),
                            source="critic",
                            target_span=rv.get("target_span"),
                            expected_behavior=rv.get("expected_behavior", ""),
                            suggested_strategy=suggested_strategy,
                            text_hash=text_hash))

            # 读者体验
            raw_re = llm_result.get("reader_experience", {})
            raw_re = llm_result.get("reader_experience", {})
            if isinstance(raw_re, dict):
                raw_scores = raw_re.get("scores", {})
                if isinstance(raw_scores, dict):
                    for key in ("continue_reading", "clarity", "cognitive_load", "emotional_engagement", "pacing"):
                        val = raw_scores.get(key)
                        if isinstance(val, (int, float)):
                            reader_experience_section["scores"][key] = max(0, min(10, int(val)))
                raw_advisories = raw_re.get("advisories", [])
                if isinstance(raw_advisories, list):
                    for item in raw_advisories:
                        if isinstance(item, dict):
                            reader_experience_section["advisories"].append(make_advisory(
                                atype=item.get("type", "reader_experience_advice"),
                                severity=item.get("severity", "low"),
                                detail=item.get("detail", ""),
                                expected_behavior=item.get("expected_behavior", ""),
                                detector="scene_validator",
                                confidence=0.6))
                        elif isinstance(item, str) and item.strip():
                            reader_experience_section["advisories"].append(make_advisory(
                                atype="reader_experience_advice",
                                severity="low",
                                detail=item.strip(),
                                expected_behavior="按冷读者建议改善清晰度、节奏或情绪投入。",
                                detector="scene_validator",
                                confidence=0.5))

        elif llm_result is None and det_violations:
            # LLM 调用失败但有确定性结果，添加 parse error violation
            contract_llm_violations.append(make_violation(
                "critic_parse_error", "high",
                "SceneValidator LLM 返回无法解析，仅使用确定性检查结果",
                source="critic",
                expected_behavior="SceneValidator 应返回可解析的 JSON",
                text_hash=text_hash))

        # ---- 合并所有 violations ----
        all_violations: list = list(det_violations) + contract_llm_violations

        # 从一致性冲突中提取 violation
        for conflict in consistency_section.get("conflicts", []):
            if not isinstance(conflict, dict):
                continue
            sev = conflict.get("severity", "warning")
            v_sev = "critical" if sev == "critical" else "high" if sev == "warning" else "medium"
            all_violations.append(make_violation(
                vtype=conflict.get("category", "internal_conflict"),
                severity=v_sev,
                detail=f"已知事实：{conflict.get('fact', '')}；文本描述：{conflict.get('text_claim', '')}",
                source="consistency",
                target_span=conflict.get("text_claim", "")[:80] if conflict.get("text_claim") else None,
                expected_behavior=conflict.get("suggestion", ""),
                scope="scene" if conflict.get("scope") == "scene" else "prose_text",
                text_hash=text_hash))

        # 从读者体验 advisories 中提取 violation（仅 high 级别）
        for adv in reader_experience_section.get("advisories", []):
            if isinstance(adv, dict) and adv.get("severity") == "high":
                all_violations.append(make_violation(
                    vtype=adv.get("type", "reader_experience_advice"),
                    severity="medium",
                    detail=adv.get("detail", ""),
                    source="reader_experience",
                    expected_behavior=adv.get("expected_behavior", ""),
                    scope="advisory",
                    text_hash=text_hash))

        # ---- 计算总体通过 ----
        has_blocking = any(v.get("blocks_commit") for v in all_violations)
        has_critical = any(v.get("severity") == "critical" for v in all_violations)
        consistency_pass = consistency_section.get("pass", True) and len(consistency_section.get("conflicts", [])) == 0
        overall_pass = not has_blocking and consistency_pass

        return {
            "passed": not has_blocking,
            "consistency": consistency_section,
            "contract_compliance": {
                "deterministic": det_violations,
                "llm_assessment": llm_result.get("contract_compliance", {}) if llm_result else {},
            },
            "reader_experience": reader_experience_section,
            "violations": all_violations,
            "overall_pass": overall_pass,
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16] if text else ""
