import json
import logging

from app.agents.base import BaseAgent
from app.services.scene_provenance import render_scene_provenance
from app.services.json_response import parse_json_response
from app.models.violation import make_violation
from app.services.recovery_metrics import RecoveryMetrics
from app.services.temporal_layer_policy import detect_temporal_layer_confusion
from app.services.llm_task_profiles import LLMTaskType
from app.services.llm_client import build_prompt_cache_policy

_logger = logging.getLogger(__name__)


class SceneCriticAgent(BaseAgent):
    name = "scene_critic"
    _CRITIC_TYPE_ALIASES = {
        "plot_logic_error": "causal_chain_error",
        "plot_hole": "causal_chain_error",
        "logic_error": "causal_chain_error",
        "timeline_confusion": "canon_timeline_confusion",
        "spatial_error": "spatial_consistency_error",
        "clue_source_error": "clue_provenance_error",
    }
    _KNOWN_CRITIC_TYPES = {
        "missing_must_show",
        "forbidden_triggered",
        "ending_state_not_reached",
        "fact_conflict",
        "clue_provenance_error",
        "causal_chain_error",
        "spatial_consistency_error",
        "canon_timeline_confusion",
    }

    async def execute(self, context: dict) -> dict:
        generated_text = context.get("generated_text", "")
        scene_contract = context.get("scene_contract", {})
        chapter_state = context.get("chapter_state", {})
        character_names = context.get("character_names", [])
        character_cards = context.get("character_cards", [])
        chapter_number = scene_contract.get("chapter_number") or 0
        scene_index = scene_contract.get("scene_index", 0)

        if not generated_text:
            return {
                "passed": False,
                "repairable": False,
                "violations": [make_violation(
                    "empty_text", "critical", "生成文本为空",
                    source="deterministic", expected_behavior="Writer 应产出非空正文",
                )],
                "has_critical": True,
            }

        if not scene_contract:
            return {
                "passed": False,
                "repairable": False,
                "violations": [make_violation(
                    "missing_contract", "critical", "场景合同缺失，无法校验",
                    source="deterministic", expected_behavior="编译器应产出有效合同",
                )],
                "has_critical": True,
            }

        violations = []

        deterministic_violations = self._deterministic_check(
            generated_text, scene_contract, chapter_state,
            character_names=character_names,
            is_book_first_scene=(chapter_number == 1 and scene_index == 0),
        )
        violations.extend(deterministic_violations)

        critical_count = sum(1 for v in violations if v.get("severity") == "critical")
        noise_types = {"ghost_character", "insufficient_sensory_in_opening", "unprovenanced_clue"}
        signal_violations = [v for v in violations if v.get("type") not in noise_types]
        signal_critical = sum(1 for v in signal_violations if v.get("severity") == "critical")
        if signal_critical >= 2 and any(v.get("blocks_commit") for v in signal_violations if v.get("severity") == "critical"):
            return {
                "passed": False,
                "repairable": True,
                "violations": violations,
                "has_critical": True,
            }

        llm_violations = await self._llm_check(generated_text, scene_contract, chapter_state, character_cards)
        violations.extend(llm_violations)

        if not violations:
            return {"passed": True, "violations": [], "repairable": False, "has_critical": False}

        has_critical_or_high = any(v.get("severity") in ("critical", "high") for v in violations)
        has_critical = any(v.get("severity") == "critical" for v in violations)
        return {
            "passed": False,
            "repairable": has_critical_or_high,
            "violations": violations,
            "has_critical": has_critical,
        }

    def _deterministic_check(self, text: str, contract: dict, chapter_state: dict,
                               character_names: list | None = None,
                               is_book_first_scene: bool = False) -> list:
        violations = []
        forbidden = contract.get("forbidden", [])
        must_show = contract.get("must_show", [])

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
                        source="deterministic",
                        target_span=kw,
                        expected_behavior=f"正文不得包含「{kw}」",
                    ))
                    break

        # Natural-language must_show items describe semantic beats. Requiring their
        # wording to appear verbatim rejects valid prose and creates repair loops.
        # Only explicitly declared literal anchors belong in deterministic checks.
        for anchor in self._literal_must_show_anchors(contract):
            if anchor not in text:
                violations.append(make_violation(
                    "missing_must_show", "high",
                    f"必须出现的字面锚点未体现：「{anchor}」",
                    source="deterministic",
                    expected_behavior=f"正文应明确出现字面锚点「{anchor}」",
                ))

        completed_events = chapter_state.get("completed_events", [])
        for evt in completed_events:
            if not evt:
                continue
            evt_keywords = evt[:20]
            if evt_keywords and evt_keywords in text:
                violations.append(make_violation(
                    "event_repeated", "high",
                    f"已完成事件被重复描述：「{evt_keywords}」",
                    source="deterministic",
                    target_span=evt_keywords,
                    expected_behavior="已完成事件只能一句话提及，不得重写过程",
                ))

        forbidden_recap = contract.get("forbidden_recap_events", [])
        for evt in forbidden_recap:
            if not evt:
                continue
            evt_kw = evt[:15]
            if evt_kw and evt_kw in text:
                import re as _re
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
                            source="deterministic",
                            target_span=evt_kw,
                            expected_behavior="已完成事件只能一句话提及，不得重写过程",
                        ))
                        break
                    pos = text.find(evt_kw, pos + 1)

        opening_state = contract.get("opening_state", "")
        if opening_state:
            temporal_finding = detect_temporal_layer_confusion(text, contract)
            if temporal_finding:
                violations.append(make_violation(
                    "temporal_layer_confusion", "high",
                    f"正文中出现{temporal_finding['count']}处闪回标记，可能存在时间层级混乱：当前场景和前史回忆混在一起。opening_state已指定场景起点，不得大段回溯前史。",
                    source="deterministic",
                    target_span=temporal_finding.get("target_span") or "",
                    expected_behavior="当前场景和前史回忆应分层处理，不得混在一起",
                ))

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
                "第一", "第二", "第三", "最后", "唯一",
            }

            import re
            potential_names = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
            seen = set()
            for name in potential_names:
                if name in seen or len(name) < 2 or name in whitelist:
                    seen.add(name)
                    continue
                if any(w in name or name in w for w in whitelist):
                    seen.add(name)
                    continue
                if name in non_character_words:
                    seen.add(name)
                    continue
                count = text.count(name)
                if count >= 3:
                    action_verbs = ["说", "问", "喊", "道", "笑", "叹", "怒", "惊"]
                    has_dialogue = any(f'{name}{v}' in text for v in action_verbs) or f'「{name}' in text
                    has_action = any(f'{name}{v}' in text for v in ["站", "走", "跑", "坐", "拿", "推", "拉", "抓", "握", "翻", "跳"])
                    severity = "high" if (has_dialogue or has_action) else "medium"
                    violations.append(make_violation(
                        "ghost_character", severity,
                        f"正文中出现未注册角色名「{name}」（出现{count}次）"
                        f"，该角色不在当前场景角色卡中{'，且有对话/动作' if (has_dialogue or has_action) else ''}",
                        source="deterministic",
                        target_span=name,
                        expected_behavior="未注册角色不应有对话或动作",
                    ))
                seen.add(name)

        if is_book_first_scene:
            first_500 = text[:500] if len(text) >= 500 else text
            first_200 = text[:200] if len(text) >= 200 else text

            omniscient_patterns = ["她不知道的是", "他不知道的是", "殊不知", "而在另一个地方", "其实.*?早已"]
            import re as re_module
            for pattern in omniscient_patterns:
                matches = re_module.findall(pattern, first_500)
                if matches:
                    violations.append(make_violation(
                        "omniscient_in_opening", "high",
                        f"全书开篇前500字出现全知视角表述：「{matches[0]}」。开篇应锁定POV角色感知，不得使用全知叙述。",
                        source="deterministic",
                        target_span=matches[0],
                        expected_behavior="开篇应锁定POV角色感知，不得使用全知叙述",
                    ))
                    break

            senses = []
            sense_keywords = [
                ("视觉", ["看见", "看到", "望向", "映入", "眼前", "光"]),
                ("听觉", ["声音", "响", "听", "喊", "叫", "风声", "脚步声"]),
                ("触觉", ["冷", "热", "痛", "硬", "软", "冰凉", "粗糙"]),
                ("嗅觉", ["气味", "香", "臭", "腥", "药味"]),
                ("味觉", ["苦", "甜", "咸", "涩"]),
            ]
            for _sense_type, kws in sense_keywords:
                if any(kw in first_200 for kw in kws):
                    senses.append(_sense_type)
            if len(senses) < 2:
                violations.append(make_violation(
                    "insufficient_sensory_in_opening", "medium",
                    f"全书开篇前200字仅包含{len(senses)}种感官描写（{', '.join(senses) if senses else '无'}），建议至少包含视觉+另一种感官以建立沉浸感。",
                    source="deterministic",
                    expected_behavior="开篇应至少包含视觉+另一种感官描写",
                ))

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
                                    source="deterministic",
                                    target_span=desc_kw,
                                    expected_behavior=f"线索「{desc_kw}」必须有来源「{source_actor}」",
                                ))
                                break
                            pos = text.find(desc_kw, pos + 1)

        if not clues:
            import re as _re4
            clue_patterns = [
                r"刻[着了].*?字", r"写[着了].*?字", r"留[着了].*?字",
                r"暗号", r"标记", r"记号", r"符号",
                r"纸条", r"信[件封]", r"留言", r"遗书",
            ]
            for pattern in clue_patterns:
                matches = _re4.findall(pattern, text)
                if matches:
                    violations.append(make_violation(
                        "unprovenanced_clue", "medium",
                        f"正文中出现疑似线索「{matches[0][:20]}」，但场景合同未声明该线索的来源。建议在合同clues字段中补充来源信息。",
                        source="deterministic",
                        target_span=matches[0][:20],
                        expected_behavior="线索应在合同clues字段中声明来源",
                    ))
                    break

        # Genre Profile 扩展字段校验
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
                            expected_behavior=ext.get("validation", f"应填写 {field_name}"),
                        ))

        return violations

    @staticmethod
    def _literal_must_show_anchors(contract: dict) -> list[str]:
        anchors = []

        def append_anchor(value):
            if isinstance(value, str):
                value = value.strip()
                if value and value not in anchors:
                    anchors.append(value)

        literal_anchors = contract.get("must_show_literal_anchors", [])
        if not isinstance(literal_anchors, list):
            literal_anchors = [literal_anchors]
        for value in literal_anchors:
            append_anchor(value)

        must_show = contract.get("must_show", [])
        if not isinstance(must_show, list):
            must_show = [must_show]
        for item in must_show:
            if isinstance(item, dict):
                mode = item.get("type") or item.get("check_mode") or item.get("mode")
                if mode == "literal_anchor":
                    append_anchor(item.get("value") or item.get("text") or item.get("anchor"))
            elif isinstance(item, str) and item.startswith("literal:"):
                append_anchor(item.removeprefix("literal:"))

        return anchors

    async def _llm_check(self, text: str, contract: dict, chapter_state: dict, character_cards: list | None = None) -> list:
        must_show = contract.get("must_show", [])
        forbidden = contract.get("forbidden", [])
        ending_state = contract.get("ending_state", "")

        system_prompt = (
            "你是一个场景审稿员。你的唯一职责是检查生成的正文是否违反场景合同。\n"
            "你必须输出严格的 JSON 格式，不要输出任何其他内容。\n\n"
            "检查项：\n"
            "1. must_show 中的每一项是否在正文中被体现（语义层面，不要求字面匹配）\n"
            "2. forbidden 中的每一项是否在正文中出现（语义层面，不要求字面匹配）\n"
            "3. ending_state 是否达成\n"
            "4. 是否与 scene_provenance 中的当前事实层矛盾\n"
            "5. 线索来源（clue_provenance）：正文中出现的线索/遗留物/暗号/刻字/留言是否有明确的放置者和来源。如果出现无来源的线索，标记为 clue_provenance_error\n"
            "6. 因果链（causal_chain）：事件之间是否有因果关系、角色行动是否有动机支撑、是否出现凭空冒出的推进\n"
            "7. 空间一致性（spatial_consistency）：人物/物品/地点是否空间自洽、是否出现不可能观察到的细节、距离描写是否与地理位置锚点矛盾\n"
            "8. 参考设定与当前时间线：对照下方提供的「参考设定层」与「当前事实层」以及 scene_provenance，检查参考设定是否被误写成当前已发生事实、是否把可能会发生写成了已经发生\n"
            "9. 伏笔操作：若合同含 foreshadowing_ops，检查正文是否按 narrative_instruction 自然完成操作；内部 thread_id 不应直接出现在正文中\n\n"
            "输出格式：\n"
            '{"violations": [{"type": "missing_must_show/forbidden_triggered/ending_state_not_reached/fact_conflict/clue_provenance_error/causal_chain_error/spatial_consistency_error/canon_timeline_confusion", "severity": "high/medium", "detail": "具体描述", "target_span": "正文中的相关片段", "expected_behavior": "正确行为描述"}]}\n\n'
            "严重级别定义：\n"
            "- high：forbidden 被触发、must_show 缺失、ending_state 未达成、线索无来源、参考设定被当成当前事实、空间矛盾明显\n"
            "- medium：轻微不一致，可接受\n\n"
            "如果正文完全符合合同，输出空数组：{\"violations\": []}"
        )

        system_prompt += (
            "\n\nAUTHORITATIVE CLUE RULE: Only emit clue_provenance_error for a clue "
            "that matches one of the scene contract's explicit clue constraints. "
            "A previously established symbol, object, or body mark may reappear "
            "without replaying its origin. Never invent a placer or source."
        )

        user_prompt = f"### 场景合同\n"

        clue_constraints = contract.get("clues", [])
        if not isinstance(clue_constraints, list):
            clue_constraints = []
        if clue_constraints:
            user_prompt += "\n### AUTHORITATIVE CLUE CONSTRAINTS\n"
            user_prompt += json.dumps(clue_constraints[:24], ensure_ascii=False) + "\n"

        source_of_truth = contract.get("source_of_truth")
        if isinstance(source_of_truth, dict) and source_of_truth:
            user_prompt += f"\n#### 大纲指定（不可修改）\n"
            user_prompt += f"目标：{source_of_truth.get('goal', contract.get('goal', ''))}\n"
            user_prompt += f"冲突：{source_of_truth.get('conflict', contract.get('conflict', ''))}\n"
            if source_of_truth.get("outline_outcome"):
                user_prompt += f"大纲结果方向：{source_of_truth['outline_outcome']}\n"
            outline_must_show = source_of_truth.get("must_show_outline", [])
            if outline_must_show:
                user_prompt += f"大纲要求展示：{json.dumps(outline_must_show, ensure_ascii=False)}\n"
            outline_forbidden = source_of_truth.get("forbidden_outline", [])
            if outline_forbidden:
                user_prompt += f"大纲禁止：{json.dumps(outline_forbidden, ensure_ascii=False)}\n"
            foreshadowing_ops = source_of_truth.get("foreshadowing_ops", [])
            if foreshadowing_ops:
                user_prompt += f"伏笔操作：{json.dumps(foreshadowing_ops, ensure_ascii=False)}\n"

            editor_enrichment = contract.get("editor_enrichment", {})
            if isinstance(editor_enrichment, dict) and editor_enrichment:
                user_prompt += f"\n#### 主编补充\n"
                if editor_enrichment.get("ending_state"):
                    user_prompt += f"场景结束状态：{editor_enrichment['ending_state']}\n"
                additional_must_show = editor_enrichment.get("additional_must_show", [])
                if additional_must_show:
                    user_prompt += f"额外必须展示：{json.dumps(additional_must_show, ensure_ascii=False)}\n"
                additional_forbidden = editor_enrichment.get("additional_forbidden", [])
                if additional_forbidden:
                    user_prompt += f"额外禁止：{json.dumps(additional_forbidden, ensure_ascii=False)}\n"
        else:
            user_prompt += f"目标：{contract.get('goal', '')}\n"
            user_prompt += f"冲突：{contract.get('conflict', '')}\n"
            if must_show:
                user_prompt += f"必须展示：{json.dumps(must_show, ensure_ascii=False)}\n"
            if forbidden:
                user_prompt += f"绝对禁止：{json.dumps(forbidden, ensure_ascii=False)}\n"
            if ending_state:
                user_prompt += f"场景结束状态：{ending_state}\n"

        scene_provenance = contract.get("scene_provenance", {})
        if scene_provenance:
            rendered_provenance = render_scene_provenance(scene_provenance)
            if rendered_provenance:
                user_prompt += "\n### 统一场景事实层（scene_provenance）\n"
                user_prompt += f"{rendered_provenance}\n"

        max_chars = 8000
        if len(text) > max_chars:
            user_prompt += f"\n### 生成的正文（前半部分）\n{text[:max_chars//2]}\n"
            user_prompt += f"\n### 生成的正文（后半部分）\n{text[len(text)-max_chars//2:]}\n"
        else:
            user_prompt += f"\n### 生成的正文\n{text}\n"

        user_prompt += "\n请逐项检查正文，特别关注：线索是否有来源、因果是否成立、空间是否自洽、参考设定是否被误写成当前事实、伏笔操作是否自然完成。输出 JSON："

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
                stable_prefix_hash=_stable_hash,
            )
            result = self._parse_json_response(response)
            RecoveryMetrics().record_parse_attempt(success=True)
            raw_violations = []
            if isinstance(result, dict):
                raw_violations = result.get("violations", [])
            elif isinstance(result, list):
                raw_violations = result

            normalized = []
            for rv in raw_violations:
                if not isinstance(rv, dict):
                    continue
                raw_type = str(rv.get("type", "semantic_quality_error"))
                vtype = self._CRITIC_TYPE_ALIASES.get(raw_type, raw_type)
                suggested_strategy = None
                evidence = {}
                expected_behavior = rv.get("expected_behavior", "")
                if vtype == "clue_provenance_error":
                    matched_clue = self._match_clue_constraint(rv, clue_constraints)
                    if matched_clue is None:
                        # Contract-free provenance guesses are handled by the
                        # proposition auditor, which has long-term fact authority.
                        continue
                    known_parts = [f"clue={matched_clue.get('description', '')}"]
                    for key in ("source_actor", "placement_time", "discovery_condition"):
                        if matched_clue.get(key):
                            known_parts.append(f"{key}={matched_clue[key]}")
                    authority_status = (
                        "confirmed"
                        if any(matched_clue.get(key) for key in (
                            "source_actor", "placement_time", "discovery_condition",
                        ))
                        else "missing_provenance"
                    )
                    authority_fact = "; ".join(known_parts)
                    evidence = {
                        "authority_fact": authority_fact,
                        "authority_status": authority_status,
                        "clue_description": matched_clue.get("description", ""),
                        "text_claim": rv.get("target_span") or rv.get("detail", ""),
                    }
                    expected_behavior = (
                        f"Use the confirmed clue authority: {authority_fact}"
                        if authority_status == "confirmed"
                        else (
                            "No verified clue origin exists. Do not invent a placer; "
                            "show only the supported discovery condition or remove the clue."
                        )
                    )
                if vtype not in self._KNOWN_CRITIC_TYPES:
                    vtype = "semantic_quality_error"
                    suggested_strategy = "rewrite_scene"
                normalized.append(make_violation(
                    vtype=vtype,
                    severity=rv.get("severity", "high"),
                    detail=(
                        f"[{raw_type}] {rv.get('detail', '')}"
                        if vtype == "semantic_quality_error"
                        else rv.get("detail", "")
                    ),
                    source="critic",
                    target_span=rv.get("target_span"),
                    expected_behavior=expected_behavior,
                    suggested_strategy=suggested_strategy,
                    evidence=evidence,
                ))
            return normalized
        except Exception as exc:
            RecoveryMetrics().record_parse_attempt(success=False)
            _logger.warning(
                "Critic response parse failed",
                extra={
                    "error": str(exc),
                    "response_preview": (response if isinstance(response, str) else str(response))[:500],
                },
            )
            return [make_violation(
                "critic_parse_error", "high",
                f"Critic LLM 返回无法解析：{exc}",
                source="critic",
                expected_behavior="Critic 应返回可解析的 JSON",
            )]

    @staticmethod
    def _match_clue_constraint(raw_violation: dict, clue_constraints: list[dict]) -> dict | None:
        claim = str(
            raw_violation.get("target_span")
            or raw_violation.get("detail")
            or ""
        )

        def normalize(value: str) -> str:
            return "".join(char.casefold() for char in str(value or "") if char.isalnum())

        claim_normalized = normalize(claim)
        if not claim_normalized:
            return None
        for clue in clue_constraints:
            if not isinstance(clue, dict):
                continue
            description = normalize(clue.get("description", ""))
            if not description:
                continue
            if description in claim_normalized or claim_normalized in description:
                return clue
            if len(description) >= 4 and any(
                description[index:index + 4] in claim_normalized
                for index in range(len(description) - 3)
            ):
                return clue
        return None

    @staticmethod
    def _parse_json_response(response: str):
        return parse_json_response(response)
