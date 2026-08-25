import hashlib
import json
import time

from app.agents.base import BaseAgent
from app.services.evidence_provenance_contract import (
    build_evidence_provenance_contract,
    render_evidence_provenance_rules,
)
from app.services.llm_task_profiles import LLMTaskType
from app.services.scene_contract_protocol import (
    build_scene_contract_protocol,
    render_scene_contract_protocol_rules,
)
from app.services.chapter_rhythm_context import render_chapter_rhythm_context
from app.services.scene_span_aligner import SceneSpanAligner
from app.services.scene_provenance import build_scene_provenance, render_scene_provenance
from app.services.scene_truth_snapshot import render_truth_snapshot, chars_to_max_tokens
from app.services.text_coercion import (
    ensure_complete_sentence_ending as _ensure_complete_scene_ending,
)
# 通用修复（循环 #1）：引用统一抽象词表，让生成层 prompt 与质检器用同一套词表
from app.services.agent_skill_validator import ABSTRACT_DETECTION_WORDS


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _record_timing(events: list, totals: dict, phase: str, start: float, **details) -> dict:
    duration_ms = _duration_ms(start)
    event = {"phase": phase, "duration_ms": duration_ms}
    event.update({key: value for key, value in details.items() if value is not None})
    events.append(event)
    totals[phase] = int(totals.get(phase, 0)) + duration_ms
    return event


def _build_core_cache_policy(system_prompt: str):
    """费用优化：为 core_generation 构建 prompt cache policy。

    system_prompt 仅含固定规则 + persona_text（同一项目内跨场景/章节一致），
    作为稳定前缀传入 PromptCachePolicy，让 DeepSeek 自动缓存命中。
    返回 (cache_policy, stable_prefix_hash)。
    """
    from app.services.llm_client import PromptCachePolicy
    stable_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    policy = PromptCachePolicy(
        enabled=True,
        provider="openai_compatible",
        stable_prefix_blocks=[system_prompt],
        dynamic_blocks=[],
        cache_hint_strategy="provider_default",
    )
    return policy, stable_hash


class CoreGenerationAgent(BaseAgent):
    name = "core_generation"

    async def execute(self, context: dict) -> dict:
        chapter_writer_packet = context.get("chapter_writer_packet")
        if isinstance(chapter_writer_packet, dict):
            return await self._execute_chapter_writer(chapter_writer_packet)

        scene_context_package = context.get("scene_context_package", {})
        scene_contract = context.get("scene_contract") or scene_context_package.get("scene_contract")
        focus_prompt = scene_context_package.get("focus_prompt", "")
        scene_beat = scene_context_package.get("scene_beat", {})
        chapter_number = scene_context_package.get("chapter_number", 1)
        scene_index = scene_context_package.get("scene_index", 0)
        character_cards = scene_context_package.get("character_cards", [])
        location_cards = scene_context_package.get("location_cards", [])
        relevant_rules = scene_context_package.get("relevant_rules", {})
        historical_details = scene_context_package.get("historical_details", [])
        chapter_state = scene_context_package.get("chapter_state", {})
        previous_scenes_summary = scene_context_package.get("previous_scenes_summary", "")
        previous_scene_ending = scene_context_package.get("previous_scene_ending", "")
        chapter_rhythm_context = scene_context_package.get("chapter_rhythm_context", {})
        style_prompt = scene_context_package.get("style_prompt", "")
        style_samples = scene_context_package.get("style_sample_passages", [])
        style_embedding = scene_context_package.get("style_embedding", {})
        persona_card = scene_context_package.get("persona_card", {})
        style_statistics = scene_context_package.get("style_statistics", {})
        evolution_report = scene_context_package.get("evolution_report", {})
        style_directive = scene_contract.get("style_directive") if scene_contract else {}
        worldview_context = context.get("worldview_context", "")
        project_info = context.get("project_info")
        custom_instructions = context.get("custom_instructions")
        truth_snapshot = context.get("scene_truth_snapshot")
        word_budget = context.get("word_budget")

        # 方案2接入：当 scene_context_package 缺失关键 Core 数据时，从 UnifiedContextBuilder 补充
        # 不改变 writer 的"消费编译产物"数据流模型，仅在数据缺失时增强
        if (not character_cards or not relevant_rules) and project_info and isinstance(project_info, dict) and project_info.get("id"):
            try:
                from app.services.unified_context_builder import UnifiedContextBuilder
                _ucb = UnifiedContextBuilder()
                _db = context.get("db")
                _writer_ctx = await _ucb.build_writer_context(
                    str(project_info["id"]), int(chapter_number), db=_db
                )
                _core = _writer_ctx.core or {}
                if not character_cards:
                    from app.services.core_entity_resolver import select_relevant_character_cards
                    character_cards = select_relevant_character_cards(
                        _core.get("characters") or [],
                        {
                            "scene_contract": scene_contract or {},
                            "scene_beat": scene_beat or {},
                            "chapter_state": chapter_state or {},
                        },
                        limit=8,
                    )
                if not relevant_rules:
                    relevant_rules = {"relevant_details": _core.get("world_rules") or []}
                if not worldview_context:
                    worldview_context = _core.get("worldview_digest") or ""
            except Exception as _e:
                import logging as _logging
                _logging.getLogger(__name__).debug(
                    "[CoreGeneration] UnifiedContextBuilder 补充失败，使用原始数据: %s", _e
                )

        # ---- Persona (from agent_config) ----
        persona_text = ""
        try:
            detail = await self.get_agent_runtime_detail()
            persona_text = str(detail.get("active_persona") or detail.get("persona") or "").strip()
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning("[CoreGeneration] 加载 persona 失败: %s", exc)

        system_prompt = ""
        if persona_text:
            system_prompt += f"{persona_text}\n\n"
        system_prompt += (
            "你是「万象谱」场景级 Writer，根据场景合同和上下文创作正文。\n\n"
            "硬规则：\n"
            "1. 只输出小说正文，禁止标题标记（#）和场景分隔标记（场景N：）\n"
            "2. 严格遵守场景合同：must_show 必须体现，forbidden 绝对禁止，ending_state 必须达成\n"
            "3. 严格遵守视角锁定：只描写 POV 角色能感知的内容。"
            "禁止 POV 跳跃（head hopping）——不得在同一段落或相邻段落切换到其他角色的内心，"
            "所有非 POV 角色的心理状态只能通过外部行为（表情、动作、语气）推断\n"
            "4. 若场景合同指定了时间锚点，正文开头必须与其一致\n"
            "5. 不得写 ending_state 之后的内容\n"
            "6. 严格遵守事实约束（与 must_show/forbidden 同级硬规则）：\n"
            "   - 当前事实层（已确立事实、角色状态、已完成事件）是当前时间线的不可变事实，"
            "正文必须与之完全一致，不得违反、改写或忽略\n"
            "   - 已完成事件只能一句话提及，不得重写过程\n"
            "   - 参考设定层（角色命运预设）是未来时间线的预设，不是当前事实，"
            "禁止在正文中写成已发生——除非当前场景的 ending_state/must_show 明确要求揭示\n"
            "   - 若 must_show 含有事实性要求（如「凤溪通过传讯符逐一联系」），"
            "正文必须用自然叙事体现该事实的过程，不得跳过或替换为矛盾行为\n"
            "7. 同一情绪不得在单场景内用完全相同的身体语言重复表达——"
            "如已用「攥紧」表达决心，下次应换用「指节泛白」「指甲掐进掌心」等不同外化\n"
            "8. 本场景必须以完整句子结束，禁止截断对白或把下一场景的时间/空间锚点黏在同一句里\n"
            "9. 信件、纸条、遗书、碑文、药方等证据只能提供碎片线索和疑问，"
            "不得替叙事一次性说明完整阴谋、完整同谋关系或完整答案\n"
            "10. 线索必须有来源：正文中出现的任何线索物——包括气味、痕迹、遗留物、"
            "信件、字条、纸条、便签、纸片、碎片、碑文、标记，"
            "以及突然出现的异常物品（如凭空出现的符纸/标记/光芒/声音等）——"
            "必须通过角色台词、记忆或观察明确其放置者或来源——"
            "例如「她想起昨晚师兄曾来过此处」「桌上的茶碗还带着余温，是有人刚走」"
            "「她认出这是宗门的传讯符，却想不通谁会在此处点燃」"
            "「地上躺着一张字条——她认出纸角印着师姐的暗记」——"
            "不得只写线索或异常物品本身而不交代来源\n"
            "11. 场景目标必须呈现：正文中必须明确体现场景合同中的 goal/scene_goal"
            "（角色的意图、渴望或行动方向），通过角色台词、内心独白或行动呈现，"
            "不得只写铺垫而无目标驱动\n"
            "12. 空间一致性：正文中的物品和场景元素必须与场景合同中的地点/环境设定一致，"
            "不得出现与场景环境不符的物品。生成前请确认场景地点（如岩洞/荒野/密室/街市等），"
            "只描写该环境中合理存在的物品——如在岩洞中不得出现枕/床等室内家具，"
            "在荒野中不得出现茶/碗等器皿，除非场景合同明确包含或角色随身携带\n\n"
            "去 AI 味硬约束：\n"
            "- 禁用词：不禁、竟然、居然、恍然、蓦然、陡然、宛如、犹如、仿佛、恰似、令人、让人、使人\n"
            "- 禁用短语：心中涌起、一股暖流、五味杂陈、百感交集、心潮澎湃、深吸一口气、眼中闪过一丝、嘴角勾起\n"
            "- 禁用夸大词：标志着、见证了、至关重要、里程碑、划时代、令人叹为观止、美轮美奂、博大精深\n"
            "- 禁用句式：不是A而是B的否定排比、不仅…而且…的三段递进、从X到Y的虚假范围\n"
            "- 禁用填充词：此外、毋庸置疑、综上所述、为了实现这一目标、在这一过程中\n"
            "- 禁用章末升华：未来可期、光明就在前方、新的篇章即将开启、这只是一个开始\n"
            "- 禁用身体语言陈词滥调：握紧拳头、咬唇、皱眉、叹气、心跳加速、呼吸急促、"
            "眼中闪过、嘴角上扬、苦笑、冷笑、身体一僵、浑身发抖、瞳孔一缩、喉结滚动、"
            "指节泛白、脸色苍白——改用具体的、可观察的动作细节或环境互动替代\n"
            "- 用动作和细节替代情绪标签，不要写「他感到愤怒」「她觉得悲伤」，让读者自己感受到\n"
            "- 严格限制明喻：每个场景最多 1 处比喻，全文「像」字总数不超过 3 次——"
            "禁用「像…一样」「像…似的」「像…般」等模板化明喻结构，优先用直接描写（动作/感官/环境细节）替代比喻，"
            "如必须使用比喻须确保独特且不可替代\n\n"
            "方案9 事实填写单要求：\n"
            "你必须在正文之后输出结构化事实清单。格式：\n"
            "1. 先输出小说正文\n"
            "2. 然后输出 ```json``` 包裹的事实清单\n\n"
            "事实清单必须包含：\n"
            "- established_facts：本场景明确确立的事实\n"
            "- character_states：每个出场角色的位置、时间、知识状态、情绪、身体状态、关系变化\n"
            "- location_states：地点的变化和状态\n"
            "- timeline_events：本场景发生的时间事件\n"
            "- item_states：物品的持有者、状态、变化\n"
            "- foreshadowing_operations：本场景的伏笔操作（bury/escalate/reveal），含正文原句\n\n"
            "注意：\n"
            "- 事实清单是「作者视角」——写你知道但读者不知道的（如角色内心状态、伏笔意图）\n"
            "- 不要写「读者能看到的外在行为」——那是正文的事\n"
            "- 伏笔操作必须写 evidence_text（正文原句），不能只写「埋了伏笔」\n\n"
            "方案 26 style_compliance 要求：\n"
            "你在正文后的 JSON 块中必须同时输出 style_compliance 字段（与 scene_facts 同级），\n"
            "声明对本场景风格指令的遵守情况。格式：\n"
            "```json\n"
            "{\"scene_facts\": {...}, \"style_compliance\": {\"directives_received\": {\"pacing\": \"tight\", \"avoid\": [\"\"]}, \"compliance_status\": \"full|partial\", \"deviations\": [{\"directive\": \"\", \"expected\": \"\", \"actual\": \"\", \"reason\": \"\", \"scene_index\": 0}], \"avoid_checked\": true, \"avoid_violations\": []}}\n"
            "```\n"
            "注意：\n"
            "- compliance_status=full 表示完全遵守；partial 表示有偏离但已在 deviations 中说明\n"
            "- avoid_violations 列出实际违反的 avoid 表达（如有）\n"
            "- deviations 中的 reason 必须具体（如「场景为情感沉淀段，过紧会破坏情绪节奏」）\n"
            "\n"
            "方案 30 具象化写作要求：\n"
            f"- 禁止堆砌抽象词（{'/'.join(ABSTRACT_DETECTION_WORDS[:18])}等），每千字不超过 2 处\n"
            "- 抽象论断必须搭配具体证据（感官描写、动作描写、对话），不得只有抽象陈述\n"
            "- 含抽象词的句子必须搭配具体证据（门/窗/桌/椅/刀/纸/信/木/石/血/雨/雪/火/影/光，"
            "或推/拉/抓/按/握/抬/放/摔/砸/撞等动作，或看/听/声/脚步/冷/热/疼/痛/湿等感官），"
            "不得出现「只有抽象词而无具体证据」的裸抽象句\n"
            "- 「仿佛/似乎」用于感官描写时允许，用于抽象比喻时禁止\n"
            "- 思想动词（感到/觉得/意识到/明白/知道）必须搭配具体动作或感官，不得单独使用\n"
            "- 设定词（如「金丹期」「灵根」「修炼」）是世界观必要抽象，不计入抽象词预算\n"
            "\n"
            "方案 30 时态与闪回闭合要求：\n"
            "- 叙事级闪回（用「曾经/当年/多年前/X年前/回忆起/想起那年/记得那年」进入的回忆段落）"
            "必须有返回信号（如「眼前」「此刻」「现在」「声音把他拉回」），不得悬在回忆中不返回\n"
            "- 点缀式回忆（一句话带过，如「她想起他说过的话」「我记得那天也是下雨」）"
            "不需要返回信号，但必须在一句话内完成，不得展开成多句回忆段落\n"
            "- 时间跳跃（如「三天后」「次日」「片刻后」）必须用过渡词明示，不得让读者自己推断时间变化\n"
            "- 同一段落内不得混用不同时间线的叙事，如需切换时间线必须用段落分隔或明确过渡词\n"
            "\n"
            "方案 30 冲突与悬念要求：\n"
            "- 每个场景必须有至少 1 个压力升级点（直接对抗、威胁、时限、代价等），不得纯铺垫\n"
            "- 场景开头（前 500 字）必须有 1 个钩子（疑问、未解问题、即将到来的危机、悬念、冲突）\n"
            "- 场景结尾（后 500 字）必须有 1 个钩子（悬念、伏笔、下一步行动、代价、身份变化）\n"
            "- 钩子不限于疑问句，「唯一性钩子」（她只有这一次机会）、「时限钩子」（天亮前必须）、「代价钩子」（她将失去最后的庇护）等都有效\n"
            "- 题材冲突词（玄幻的魔气/杀意/剑气/毒蛛/兽吼，都市的对峙/枪/挟持）应被主动使用\n"
        )

        # 费用优化：动态风格内容移到 user_prompt，保持 system_prompt 跨场景稳定，
        # 最大化 DeepSeek prompt cache 命中率（system_prompt 是缓存前缀的第一部分）。
        # 根因：style_directive / persona_card / style_embedding 等每场景不同，
        #   拼到 system_prompt 末尾导致前缀变化，cache miss。移到 user_prompt 后，
        #   system_prompt 仅含 persona + 固定规则，跨场景完全一致，cache 命中。
        style_context = ""

        if style_directive:
            style_context += "\n### 场景风格策略（本场景优先，覆盖全局风格约束）"
            role = style_directive.get("scene_style_role")
            if role:
                style_context += f"\n- 风格角色：{role}"
            reason = style_directive.get("reason")
            if reason:
                style_context += f"\n- 原因：{reason}"
            skeleton = style_directive.get("skeleton")
            if skeleton and isinstance(skeleton, dict):
                for key, label in [
                    ("pacing", "节奏"), ("dialogue_ratio", "对白比例"),
                    ("description_density", "描写密度"), ("info_density", "信息密度"),
                ]:
                    value = skeleton.get(key)
                    if value:
                        style_context += f"\n- {label}：{value}"
            avoid = style_directive.get("avoid")
            if avoid and isinstance(avoid, list):
                style_context += f"\n- 额外禁用表达（硬规则，与 forbidden 同级）：{'、'.join(avoid)}"
            texture = style_directive.get("texture")
            if texture and isinstance(texture, dict):
                for key, label in [
                    ("diction", "措辞"), ("sentence", "句式"), ("imagery", "意象"),
                ]:
                    value = texture.get(key)
                    if value:
                        style_context += f"\n- {label}：{value}"

        if style_prompt:
            style_context += f"\n\n### 风格约束\n{style_prompt}"

        if style_embedding:
            style_context += "\n\n### 风格骨架"
            for key, label in [
                ("emotionality", "情感外显度"),
                ("sentence_complexity", "句式复杂度"),
                ("narrative_distance", "叙事距离"),
                ("dialogue_ratio", "对话占比"),
            ]:
                value = style_embedding.get(key)
                if value is not None:
                    style_context += f"\n- {label}：{float(value):.2f}"

        if persona_card:
            style_context += "\n\n### 叙述人格"
            if persona_card.get("identity"):
                style_context += f"\n- 身份：{persona_card['identity']}"
            if persona_card.get("decision_pattern"):
                style_context += f"\n- 决策：{persona_card['decision_pattern']}"
            if persona_card.get("interpersonal_behavior"):
                style_context += f"\n- 人际：{persona_card['interpersonal_behavior']}"

        if style_statistics:
            global_stats = style_statistics.get("global", {})
            if global_stats:
                style_context += (
                    f"\n\n### 统计提示\n章节数 {global_stats.get('chapter_count', 0)}，"
                    f"段落数 {global_stats.get('paragraph_count', 0)}，"
                    f"总字数 {global_stats.get('total_chars', 0)}"
                )

        if evolution_report and evolution_report.get("is_multi_style"):
            style_context += "\n\n### 演变提示\n该文本存在多风格簇，生成时需遵守当前章节所在风格区间。"

        user_prompt = style_context + "\n\n" if style_context else ""

        if truth_snapshot:
            rendered = render_truth_snapshot(truth_snapshot)
            if rendered:
                user_prompt += (
                    "### 当前不可变事实快照（必须严格遵守，不得违反）\n"
                    f"{rendered}\n\n"
                )

        # 注入 scene_provenance：让 writer 看到"当前事实层 vs 参考设定层"的明确区分，
        # 避免将角色命运预设误写为已发生，并补全 truth_snapshot 可能未覆盖的事实维度。
        # 之前仅 scene_critic/scene_validator 调用 render_scene_provenance，writer 看不到，
        # 导致 writer 违规后 critic 才发现，已来不及。此处补齐生成侧的事实可见性。
        try:
            provenance = build_scene_provenance(
                scene_contract,
                chapter_state=chapter_state,
                character_cards=character_cards,
            )
            provenance_text = render_scene_provenance(provenance)
            if provenance_text:
                user_prompt += (
                    "### 场景事实溯源（当前事实层必须遵守，参考设定层禁止写成已发生）\n"
                    f"{provenance_text}\n\n"
                )
        except Exception:
            pass

        if word_budget and isinstance(word_budget, dict):
            target = word_budget.get("target_chars", 0)
            soft_min = word_budget.get("soft_min_chars", 0)
            soft_max = word_budget.get("soft_max_chars", 0)
            if target:
                user_prompt += f"### 篇幅要求\n目标字数约{target}字"
                if soft_min and soft_max:
                    user_prompt += f"（可接受范围{soft_min}-{soft_max}字）"
                user_prompt += "，不要大幅超出或不足。\n\n"

        rendered_rhythm_context = render_chapter_rhythm_context(chapter_rhythm_context)
        if rendered_rhythm_context:
            user_prompt += f"{rendered_rhythm_context}\n\n"

        # WriterInputPacket is the only contract input accepted by the scene writer.
        active_capabilities = []
        writer_input_enrichment = {}
        writer_input_packet = scene_context_package.get("writer_input_packet")
        if not isinstance(writer_input_packet, dict) or not writer_input_packet:
            raise ValueError("scene_context_package.writer_input_packet is required")
        # V2 路径：使用编译后的 WriterInputPacket
        user_prompt += f"### 场景任务（必须严格遵守）\n"
        if writer_input_packet.get("scene_task"):
            user_prompt += f"{writer_input_packet['scene_task']}\n\n"

        if writer_input_packet.get("visible_facts"):
            user_prompt += f"#### 当前不可违反事实（硬规则，必须与正文一致）\n"
            for fact in writer_input_packet["visible_facts"]:
                user_prompt += f"- {fact}\n"
            user_prompt += "\n"

        if writer_input_packet.get("must_include"):
            user_prompt += f"#### 本场必须自然出现\n"
            for i, item in enumerate(writer_input_packet["must_include"], 1):
                user_prompt += f"{i}. {item}\n"
            user_prompt += "\n"

        if writer_input_packet.get("soft_suggestions"):
            user_prompt += f"#### 可以轻轻带到\n"
            for i, item in enumerate(writer_input_packet["soft_suggestions"], 1):
                user_prompt += f"{i}. {item}\n"
            user_prompt += "\n"

        if writer_input_packet.get("must_avoid"):
            user_prompt += f"#### 禁止\n"
            for i, item in enumerate(writer_input_packet["must_avoid"], 1):
                user_prompt += f"{i}. {item}\n"
            user_prompt += "\n"

        if writer_input_packet.get("ending_state"):
            user_prompt += f"#### 结尾状态\n{writer_input_packet['ending_state']}\n\n"

        if writer_input_packet.get("style_instruction"):
            user_prompt += f"#### 风格要求\n{writer_input_packet['style_instruction']}\n\n"

        if writer_input_packet.get("pacing_instruction"):
            user_prompt += f"#### 节奏要求\n{writer_input_packet['pacing_instruction']}\n\n"

        active_capabilities = writer_input_packet.get("active_capabilities", [])
        if active_capabilities:
            user_prompt += "#### 输入增强\n启用能力：\n"
            activation_reasons = writer_input_packet.get("activation_reasons", {})
            for capability in active_capabilities:
                reason = ""
                if isinstance(activation_reasons, dict):
                    reason = str(activation_reasons.get(capability, "") or "").strip()
                if reason:
                    user_prompt += f"- {capability}：{reason}\n"
                else:
                    user_prompt += f"- {capability}\n"
            user_prompt += "\n"

        source_trace = writer_input_packet.get("source_trace", {})
        if source_trace:
            user_prompt += "#### 来源追踪\n"
            if isinstance(source_trace, dict):
                for capability in active_capabilities:
                    sources = source_trace.get(capability, [])
                    if isinstance(sources, list) and sources:
                        user_prompt += f"- {capability}：{'、'.join(str(src) for src in sources[:6])}\n"
            user_prompt += "\n"

        writer_input_enrichment = writer_input_packet.get("writer_input_enrichment", {})
        if writer_input_enrichment and isinstance(writer_input_enrichment, dict):
            user_prompt += (
                "#### 输入增强详情\n"
                f"{json.dumps(writer_input_enrichment, ensure_ascii=False, indent=2)}\n\n"
            )

        output_constraints = writer_input_packet.get("output_constraints", {})
        if output_constraints:
            target_chars = output_constraints.get("target_chars", 0)
            if target_chars:
                hard_max = output_constraints.get("hard_max_chars", target_chars + 2000)
                user_prompt += f"### 篇幅要求\n目标字数约{target_chars}字（硬上限{hard_max}字），不要大幅超出或不足。\n\n"

        system_rules = writer_input_packet.get("system_rules", [])
        if system_rules:
            user_prompt += "\n### 系统规则\n" + "\n".join(system_rules) + "\n\n"

        if previous_scene_ending:
            user_prompt += f"\n### 上一场景结尾（必须从此处接续）\n{previous_scene_ending}\n"

        if character_cards:
            user_prompt += "\n### 角色设定（必须严格遵守，不得编造矛盾内容）\n"
            for card in character_cards:
                if isinstance(card, dict):
                    card_summary = self._summarize_character_card(card)
                    user_prompt += f"{card_summary}\n"

        if location_cards:
            user_prompt += "\n### 场景地点\n"
            for loc in location_cards:
                if isinstance(loc, dict):
                    user_prompt += f"- {loc.get('name', '')}：{loc.get('description', loc.get('atmosphere', ''))}\n"

        if relevant_rules:
            critical_index = relevant_rules.get("critical_index", [])
            relevant_details = relevant_rules.get("relevant_details", [])
            if critical_index:
                user_prompt += "\n### 世界观铁则（不可违反）\n"
                for rule in critical_index:
                    user_prompt += f"- {rule.get('name', '')}：{rule.get('core', '')}\n"
            if relevant_details:
                user_prompt += "\n### 相关世界观规则\n"
                for rule in relevant_details[:5]:
                    user_prompt += f"- {rule.get('name', '')}：{rule.get('description', '')}\n"

        if previous_scenes_summary and not previous_scene_ending:
            user_prompt += f"\n### 前文场景摘要（保持事实一致）\n{previous_scenes_summary}\n"

        if style_samples:
            user_prompt += "\n### 风格范例\n"
            for sample in style_samples[:3]:
                if isinstance(sample, dict) and sample.get("text"):
                    user_prompt += f"- {sample.get('category', 'narrative')}：{sample['text'][:220]}\n"

        if historical_details:
            user_prompt += "\n### 历史细节参考\n"
            for detail in historical_details[:5]:
                if isinstance(detail, dict):
                    user_prompt += f"- {detail.get('content', detail.get('summary', ''))}\n"

        if worldview_context:
            user_prompt += f"\n### 世界观上下文\n{worldview_context}\n"

        if focus_prompt:
            user_prompt += f"\n### 焦点提示\n{focus_prompt}\n"

        if custom_instructions:
            user_prompt += f"\n### 额外指令\n{custom_instructions}\n"

        # --- Agent Skill Runtime injection ---
        skill_trace = {}
        skill_packet = None
        try:
            skill_packet = await self.prepare_skill_packet({
                "project_id": str(project_info.get("id", "")) if isinstance(project_info, dict) and project_info.get("id") else None,
                "db": context.get("db"),
                "scene_contract": scene_contract or {},
                "scene_context_package": scene_context_package or {},
                "writing_mode_profile": (scene_context_package or {}).get("writing_mode_profile", {}),
                "quality_extensions": scene_contract.get("quality_extensions", {}) if scene_contract else {},
                "previous_quality_reports": context.get("previous_quality_reports", {}),
                "feature_policy": context.get("feature_policy", {}),
            })
            service_outputs = await self._execute_skill_services(
                skill_packet,
                {
                    "project_id": str(project_info.get("id", "")) if isinstance(project_info, dict) and project_info.get("id") else None,
                    "db": context.get("db"),
                    "scene_beat": scene_beat or {},
                    "story_state": chapter_state or {},
                    "chapter_state": chapter_state or {},
                    "pov_character": scene_contract.get("pov") if isinstance(scene_contract, dict) else None,
                    "chapter_number": chapter_number,
                    "style_context": {"style_embedding": style_embedding, "style_statistics": style_statistics},
                    "style_prompt": style_prompt,
                    "style_samples": style_samples,
                    "relevant_rules": relevant_rules,
                },
            )
            # 费用优化：skill_packet.render_system() 移到 user_prompt，
            # 保持 system_prompt 仅含固定规则 + persona_text，跨场景稳定。
            user_prompt += skill_packet.render_system()
            user_prompt += skill_packet.render_user()
            user_prompt += self._render_skill_service_outputs(service_outputs)
            skill_trace = {
                **skill_packet.trace,
                "active_skills": skill_packet.active_skills,
                "activation_reasons": skill_packet.activation_reasons,
                "service_outputs": service_outputs,
            }
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Skill runtime injection failed", exc_info=True)

        llm = await self.get_llm_client()
        # 方案 29：task_type 提供基础预设（max_tokens=65536, timeout=300s）；
        # word_budget 的 hard_max 动态覆盖 max_tokens
        max_tokens = None
        if word_budget and isinstance(word_budget, dict):
            hard_max = word_budget.get("hard_max_chars", 0)
            if hard_max > 0:
                max_tokens = chars_to_max_tokens(hard_max)
        # 费用优化：system_prompt 仅含固定规则 + persona_text，跨场景稳定，启用 prompt cache
        _cache_policy, _stable_hash = _build_core_cache_policy(system_prompt)
        generated_text = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8,
            task_type=LLMTaskType.CHAPTER_GENERATION,
            max_tokens=max_tokens,
            cache_policy=_cache_policy,
            stable_prefix_hash=_stable_hash,
        )

        # 方案9：分离正文和事实清单；方案 26：同时提取 style_compliance
        from app.services.writer_output_parser import parse_writer_output
        from app.services.style_profile_schema import parse_style_compliance
        raw_output = generated_text
        generated_text, scene_facts = parse_writer_output(raw_output)
        _, style_compliance = parse_style_compliance(raw_output)

        generated_text = _ensure_complete_scene_ending(generated_text)
        if skill_packet is not None:
            skill_trace["post_service_outputs"] = await self._execute_skill_services(
                skill_packet,
                {
                    "execution_phase": "post_generation",
                    "text": generated_text,
                    "style_features": style_statistics or {},
                    "style_embedding": style_embedding or {},
                    "persona_card": persona_card or {},
                    "style_directive": style_directive or {},
                },
            )
            generated_text = await self._validate_and_repair_skill_output(
                llm=llm,
                skill_packet=skill_packet,
                skill_trace=skill_trace,
                generated_text=generated_text,
                validation_context={
                    "scene_contract": scene_contract or {},
                    "chapter_state": chapter_state or {},
                },
                max_tokens=max_tokens,
                protection_check=None,
            )
        # 方案18 步骤1：构建 writer 执行报告（must_show 覆盖 / forbidden 违规 / ending_state 达成）
        execution_report = self._build_execution_report(
            generated_text=generated_text,
            writer_input_packet=writer_input_packet if isinstance(writer_input_packet, dict) else {},
        )

        return {
            "generated_text": generated_text,
            "skill_trace": skill_trace,
            "scene_facts": scene_facts,  # 方案9：writer 事实填写单
            "style_compliance": style_compliance,  # 方案 26：writer 风格遵守情况
            "execution_report": execution_report,  # 方案18：执行报告，供主编参考
            "prompt_debug": {
                "system_chars": len(system_prompt),
                "user_chars": len(user_prompt),
                "max_tokens": max_tokens,
                "active_capabilities": active_capabilities,
                "enrichment_keys": list(writer_input_enrichment.keys()) if isinstance(writer_input_enrichment, dict) else [],
            },
        }

    def _build_execution_report(
        self,
        generated_text: str,
        writer_input_packet: dict,
    ) -> dict:
        """方案18 步骤1：构建 writer 执行报告。

        基于 writer_input_packet 的 must_include/must_avoid/ending_state，
        对生成正文做简单的字符串包含性自检。

        注意：这是 writer 自身的快速自检，不做语义判断。深度校验由 FBI/Validator 负责。
        本报告的目的是给主编一个早期信号，让主编知道 writer 偏离了什么。
        """
        # 统一从 WriterInputPacket 读取，场景合同不得在 Agent 内二次编译。
        must_include = writer_input_packet.get("must_include") or []
        must_avoid = writer_input_packet.get("must_avoid") or []
        ending_state = writer_input_packet.get("ending_state") or ""

        # must_show 覆盖检查（字符串包含）
        must_show_coverage: dict[str, bool] = {}
        if isinstance(must_include, list):
            for item in must_include:
                key = str(item)[:80]
                # 简单的字符串包含检查；item 可能是短语或句子
                # 取 item 的关键词（前 6 字）做包含判断，避免过严
                probe = str(item)[:6] if len(str(item)) > 6 else str(item)
                must_show_coverage[key] = probe in generated_text

        # forbidden 违规检查（字符串包含）
        forbidden_violations: list[str] = []
        if isinstance(must_avoid, list):
            for item in must_avoid:
                item_str = str(item)
                # 短词（<=4字）直接包含判断；长词取前 4 字
                probe = item_str if len(item_str) <= 4 else item_str[:4]
                if probe and probe in generated_text:
                    forbidden_violations.append(f"触发了禁止的元素：{item_str[:80]}")

        # ending_state 达成检查（字符串包含）
        ending_state_achieved = True
        ending_state_detail = ""
        if isinstance(ending_state, str) and ending_state.strip():
            # 取 ending_state 的关键词做包含判断
            probe = ending_state[:6] if len(ending_state) > 6 else ending_state
            ending_state_achieved = probe in generated_text
            if not ending_state_achieved:
                ending_state_detail = ending_state[:80]
        elif isinstance(ending_state, dict):
            # ending_state 可能是结构化的 {"character": ..., "state": ...}
            state_text = str(ending_state.get("state", "")) if ending_state else ""
            if state_text:
                probe = state_text[:6] if len(state_text) > 6 else state_text
                ending_state_achieved = probe in generated_text
                if not ending_state_achieved:
                    ending_state_detail = state_text[:80]

        # 偏离汇总
        deviations: list[str] = []
        missed_must = [k for k, v in must_show_coverage.items() if not v]
        if missed_must:
            deviations.append(f"must_show 未覆盖：{', '.join(missed_must[:5])}")
        if forbidden_violations:
            deviations.append(f"forbidden 违规 {len(forbidden_violations)} 项")
        if not ending_state_achieved:
            deviations.append(f"ending_state 未达成：{ending_state_detail}")

        # R4-5：补全 scene_count / expected_word_count / word_count_delta。
        # 本报告为单场景执行报告，预期字数只从 WriterInputPacket 读取。
        output_constraints = writer_input_packet.get("output_constraints") or {}
        expected_word_count = (
            output_constraints.get("target_chars") or 0
            if isinstance(output_constraints, dict)
            else 0
        )
        actual_word_count = len(generated_text)
        word_count_delta = actual_word_count - expected_word_count

        return {
            "must_show_coverage": must_show_coverage,
            "forbidden_violations": forbidden_violations,
            "ending_state_achieved": ending_state_achieved,
            "ending_state_detail": ending_state_detail,
            "word_count": actual_word_count,
            "scene_count": 1,
            "expected_word_count": expected_word_count,
            "word_count_delta": word_count_delta,
            "deviations": deviations,
        }

    async def _execute_chapter_writer(self, packet: dict) -> dict:
        """Generate a whole chapter in one model call.

        The output keeps temporary scene markers so the orchestration layer can
        align the chapter back to scene spans, then strip markers before saving.

        System prompt is assembled from:
        1. Persona (from agent_config / AGENT_REGISTRY)
        2. Hard rules (万象谱章节级 Writer)
        3. De-AI constraints
        4. Skill packet (from prepare_skill_packet)
        5. Persona card (from context, optional)
        """
        writer_total_start = time.perf_counter()
        timing_events: list[dict] = []
        timing_totals: dict[str, int] = {}
        llm_calls: list[dict] = []
        user_prompt = str(packet.get("user_prompt") or "")
        hard_max_chars = int(packet.get("hard_max_chars") or packet.get("target_chars") or 8000)
        token_report = packet.get("token_report") if isinstance(packet.get("token_report"), dict) else {}
        reserve = int(token_report.get("reserve_output_tokens") or 0)
        max_tokens = chars_to_max_tokens(hard_max_chars)
        if reserve:
            max_tokens = max(max_tokens, min(reserve, 32000))
        max_tokens = max(4096, min(max_tokens, 32000))

        # ---- 1. Persona ----
        persona_text = ""
        phase_start = time.perf_counter()
        try:
            detail = await self.get_agent_runtime_detail()
            persona_text = str(detail.get("active_persona") or detail.get("persona") or "").strip()
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning("[CoreGeneration] 加载 persona 失败: %s", exc)
        _record_timing(
            timing_events,
            timing_totals,
            "persona_runtime_detail",
            phase_start,
            persona_chars=len(persona_text),
            persona_injected=bool(persona_text),
        )

        # ---- 2. Hard rules ----
        system_prompt = ""
        if persona_text:
            system_prompt += f"{persona_text}\n\n"
        system_prompt += (
            "你是「万象谱」章节级 Writer，一次性写完整一章中文小说正文。\n\n"
            "硬规则：\n"
            "1. 先理解全章 scene_map，再写正文，保证场景之间自然承接。\n"
            "2. 每个场景开头必须单独输出一行 marker，格式严格为 [[SCENE:scene_id]]；scene_id 必须与输入完全一致。\n"
            "3. marker 之外只能输出小说正文，不得输出标题、解释、道歉、提问、列表、Markdown 或元评论。\n"
            "4. 严格遵守 must_show、forbidden、ending_state、do_not_reveal_yet 和人物认知边界。\n"
            "5. 禁止英文和拼音，除非输入中的专有名词本身就是英文。\n"
            "6. 降低 AI 味：少用解释句、定义句、总结句、模板化转折；用动作、对白、细节和场景压力推进。\n"
            "7. 证据、信件、物件只能给碎片线索和新的疑问，不得一次性解释完整阴谋或答案。\n"
            "8. 不得引入合同（scene_map）和不可变事实快照中未声明的角色、线索、事件或实体。\n"
            "   - 只能使用 scene_map 和事实快照中明确出现的角色、地点、物品\n"
            "   - 不得凭空创造新角色（如合同未提及的敌人、NPC、神秘人物）\n"
            "   - 不得引入未在事实快照中登记的新威胁类型（如事实层是妖兽，不得写成魔化修士）\n"
            "   - 不得新增与已确立事实矛盾的身份信息或事件转折\n"
            "9. 必须遵守不可变事实快照中的角色位置、状态、已完成事件，不得将角色写回已离开的地点，不得重演已完成事件。\n"
            "   - 涉及任何角色的动作、位置或状态变化时，必须先核对不可变事实快照中该角色的当前状态\n"
            "   - 角色当前状态为「卧床/重伤/被困/缺席」等受限状态时，不得写其执行超出该状态允许的动作\n"
            "   - 不得让角色做事实快照中未记录的事情（如角色在快照中是「养伤」，不得写其「引开追兵/被抓/参战」）\n"
            "   - 如需推进角色状态变化，必须通过本章场景合同中明确声明的情节事件，不得自行创造角色行动线\n"
            "   - 角色身体部位的受伤/状态必须与事实快照完全一致——如快照记录「右腿受伤」，不得写「左腿」；"
            "快照记录「左手缺失」，不得写「右手」。涉及左右/上下/前后等具体方位的身体描述，必须逐字核对\n"
            "10. 场景必须以 ending_state 描述的状态结束，不得停在中间状态或偏离到其他结局。\n\n"
            "去 AI 味硬约束：\n"
            "- 禁用词：不禁、竟然、居然、恍然、蓦然、陡然、宛如、犹如、仿佛、恰似、令人、让人、使人\n"
            "- 禁用短语：心中涌起、一股暖流、五味杂陈、百感交集、心潮澎湃、深吸一口气、眼中闪过一丝、嘴角勾起\n"
            "- 禁用夸大词：标志着、见证了、至关重要、里程碑、划时代、令人叹为观止、美轮美奂、博大精深\n"
            "- 禁用句式：不是A而是B的否定排比、不仅…而且…的三段递进、从X到Y的虚假范围\n"
            "- 禁用填充词：此外、毋庸置疑、综上所述、为了实现这一目标、在这一过程中\n"
            "- 禁用章末升华：未来可期、光明就在前方、新的篇章即将开启、这只是一个开始\n"
            "- 禁用身体语言陈词滥调：握紧拳头、咬唇、皱眉、叹气、心跳加速、呼吸急促、"
            "眼中闪过、嘴角上扬、苦笑、冷笑、身体一僵、浑身发抖、瞳孔一缩、喉结滚动、"
            "指节泛白、脸色苍白——改用具体的、可观察的动作细节或环境互动替代\n"
            "- 用动作和细节替代情绪标签，不要写「他感到愤怒」「她觉得悲伤」，让读者自己感受到\n"
            "- 严格限制明喻：每个场景最多 1 处比喻，全文「像」字总数不超过 3 次——"
            "禁用「像…一样」「像…似的」「像…般」等模板化明喻结构，优先用直接描写（动作/感官/环境细节）替代比喻，"
            "如必须使用比喻须确保独特且不可替代\n\n"
            "方案9 事实填写单要求：\n"
            "你必须在正文之后输出结构化事实清单（覆盖全章所有场景）。格式：\n"
            "1. 先输出小说正文（含 [[SCENE:scene_id]] markers）\n"
            "2. 然后输出 ```json``` 包裹的事实清单\n\n"
            "事实清单必须包含：\n"
            "- established_facts：本章明确确立的事实\n"
            "- character_states：每个出场角色的位置、时间、知识状态、情绪、身体状态、关系变化\n"
            "- location_states：地点的变化和状态\n"
            "- timeline_events：本章发生的时间事件\n"
            "- item_states：物品的持有者、状态、变化\n"
            "- foreshadowing_operations：本章的伏笔操作（bury/escalate/reveal），含正文原句\n\n"
            "注意：\n"
            "- 事实清单是「作者视角」——写你知道但读者不知道的（如角色内心状态、伏笔意图）\n"
            "- 不要写「读者能看到的外在行为」——那是正文的事\n"
            "- 伏笔操作必须写 evidence_text（正文原句），不能只写「埋了伏笔」\n"
            "\n"
            "方案 30 具象化与冲突悬念要求：\n"
            f"- 禁止堆砌抽象词（{'/'.join(ABSTRACT_DETECTION_WORDS[:18])}等），每千字不超过 2 处\n"
            "- 抽象论断必须搭配具体证据（感官/动作/对话），不得只有抽象陈述\n"
            "- 含抽象词的句子必须搭配具体证据（门/窗/桌/椅/刀/纸/信/木/石/血/雨/雪/火/影/光，"
            "或推/拉/抓/按/握/抬/放/摔/砸/撞等动作，或看/听/声/脚步/冷/热/疼/痛/湿等感官），"
            "不得出现裸抽象句\n"
            "- 思想动词（感到/觉得/意识到）必须搭配具体动作或感官\n"
            "- 叙事级闪回（曾经/当年/多年前/X年前/回忆起/想起那年/记得那年）必须有返回信号"
            "（眼前/此刻/现在/声音把他拉回），点缀式回忆一句话带过不需返回信号\n"
            "- 时间跳跃必须用过渡词明示，同一段落内不得混用不同时间线\n"
            "- 每个场景至少 1 个压力升级点（对抗/威胁/时限/代价），不得纯铺垫\n"
            "- 场景开头 500 字内必须有钩子，结尾 500 字内必须有钩子\n"
            "- 钩子不限于疑问句，唯一性钩子/时限钩子/代价钩子等均有效\n"
            "- 主动使用题材冲突词（玄幻：魔气/杀意/剑气；都市：对峙/枪/挟持）\n"
        )
        packet_rules = packet.get("system_rules") if isinstance(packet.get("system_rules"), list) else []
        if packet_rules:
            # 费用优化：packet_rules 移到 user_prompt，保持 system_prompt 稳定
            user_prompt += "\n\n" + "\n".join(str(rule) for rule in packet_rules if str(rule or "").strip())

        # ---- 3. Skill packet ----
        skill_trace = {}
        skill_context = packet.get("skill_context") or {}
        skill_packet = None
        phase_start = time.perf_counter()
        try:
            skill_packet = await self.prepare_skill_packet({
                "project_id": skill_context.get("project_id"),
                "db": skill_context.get("db"),
                "scene_contract": skill_context.get("scene_contract") or (packet.get("scene_map") or [{}])[0] or {},
                "scene_context_package": skill_context.get("scene_context_package", {}),
                "writing_mode_profile": skill_context.get("writing_mode_profile", {}),
                "quality_extensions": skill_context.get("quality_extensions", {}),
                "previous_quality_reports": skill_context.get("previous_quality_reports", {}),
                "feature_policy": skill_context.get("feature_policy", {}),
            })
            service_outputs = await self._execute_skill_services(
                skill_packet,
                {
                    "project_id": skill_context.get("project_id"),
                    "db": skill_context.get("db"),
                    "scene_beat": (packet.get("scene_map") or [{}])[0] or {},
                    "story_state": skill_context.get("chapter_state", {}),
                    "chapter_state": skill_context.get("chapter_state", {}),
                    "pov_character": skill_context.get("pov_character"),
                    "chapter_number": skill_context.get("chapter_number"),
                    "style_context": skill_context.get("style_context", {}),
                    "style_prompt": skill_context.get("style_prompt", ""),
                    "style_samples": skill_context.get("style_samples", []),
                    "relevant_rules": skill_context.get("relevant_rules", {}),
                },
            )
            # 费用优化：skill_packet.render_system() 移到 user_prompt，
            # 保持 system_prompt 仅含固定规则 + persona_text，跨场景稳定。
            user_prompt += skill_packet.render_system()
            user_prompt += skill_packet.render_user()
            user_prompt += self._render_skill_service_outputs(service_outputs)
            skill_trace = {
                **skill_packet.trace,
                "active_skills": skill_packet.active_skills,
                "activation_reasons": skill_packet.activation_reasons,
                "service_outputs": service_outputs,
            }
            _record_timing(
                timing_events,
                timing_totals,
                "skill_prepare_and_inject",
                phase_start,
                status="ok",
                active_skill_count=len(skill_packet.active_skills),
                validator_count=len(getattr(skill_packet, "validators", []) or []),
                service_count=len(service_outputs) if isinstance(service_outputs, dict) else 0,
                system_chars=len(system_prompt),
                user_chars=len(user_prompt),
            )
        except Exception as exc:
            _record_timing(
                timing_events,
                timing_totals,
                "skill_prepare_and_inject",
                phase_start,
                status="failed",
                error=str(exc)[:200],
                system_chars=len(system_prompt),
                user_chars=len(user_prompt),
            )
            import logging
            logging.getLogger(__name__).warning("Skill runtime injection failed in chapter writer", exc_info=True)

        # ---- 4. Persona card ----
        # 费用优化：persona_card 移到 user_prompt，保持 system_prompt 稳定
        persona_card = packet.get("persona_card") or {}
        if persona_card and isinstance(persona_card, dict):
            user_prompt += "\n\n叙述人格："
            if persona_card.get("identity"):
                user_prompt += f"\n- 身份：{persona_card['identity']}"
            if persona_card.get("decision_pattern"):
                user_prompt += f"\n- 决策：{persona_card['decision_pattern']}"
            if persona_card.get("interpersonal_behavior"):
                user_prompt += f"\n- 人际：{persona_card['interpersonal_behavior']}"

        # ---- LLM call ----
        phase_start = time.perf_counter()
        llm = await self.get_llm_client()
        _record_timing(
            timing_events,
            timing_totals,
            "llm_client_create",
            phase_start,
            api_format=getattr(llm, "api_format", None),
            model=getattr(llm, "model", None),
        )
        scene_map = [item for item in (packet.get("scene_map") or []) if isinstance(item, dict)]
        phase_start = time.perf_counter()
        llm_debug: dict = {}
        # 费用优化：system_prompt 仅含固定规则 + persona_text，跨场景稳定，启用 prompt cache
        _cache_policy, _stable_hash = _build_core_cache_policy(system_prompt)
        generated_text = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.82,
            task_type=LLMTaskType.CHAPTER_GENERATION,
            max_tokens=max_tokens,
            debug_info=llm_debug,
            cache_policy=_cache_policy,
            stable_prefix_hash=_stable_hash,
        )
        llm_event = _record_timing(
            timing_events,
            timing_totals,
            "llm_initial_generate",
            phase_start,
            api_format=getattr(llm, "api_format", None),
            model=getattr(llm, "model", None),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            max_tokens=max_tokens,
            timeout_seconds=300.0,
            output_chars=len(generated_text or ""),
            provider_usage=llm_debug.get("usage"),
            provider_response_chars=llm_debug.get("response_chars"),
        )
        llm_calls.append(llm_event)
        # 方案9：分离正文和事实清单；方案 26：同时提取 style_compliance
        from app.services.writer_output_parser import parse_writer_output
        from app.services.style_profile_schema import parse_style_compliance
        raw_output = generated_text
        generated_text, scene_facts = parse_writer_output(raw_output)
        _, style_compliance = parse_style_compliance(raw_output)
        generated_text = _ensure_complete_scene_ending(generated_text)
        phase_start = time.perf_counter()
        marker_check = await self._check_chapter_markers(generated_text, scene_map)
        _record_timing(
            timing_events,
            timing_totals,
            "marker_check_initial",
            phase_start,
            ok=bool(marker_check.get("ok")),
            score=marker_check.get("score"),
            method=marker_check.get("method"),
            span_count=marker_check.get("span_count"),
        )
        retry_used = False
        if not marker_check["ok"] and scene_map:
            retry_used = True
            marker_lines = "\n".join(
                f"[[SCENE:{item.get('scene_id')}]]"
                for item in scene_map
                if item.get("scene_id")
            )
            retry_system = (
                system_prompt
                + "\n\n格式返工硬规则：上一版没有生成可切分的章节正文。"
                "你现在必须从头重写正文，严格使用下列 marker，每个 marker 独占一行；"
                "marker 之外只能写中文小说正文，禁止解释、道歉、提问、列表和英文。"
            )
            retry_user = (
                "上一版章节 Writer 输出不合格，原因如下：\n"
                f"{marker_check}\n\n"
                "必须按这个 marker 顺序输出，每个 marker 后写对应场景正文：\n"
                f"{marker_lines}\n\n"
                "原始任务如下，请直接完成，不要向用户索要更多信息：\n"
                f"{user_prompt}\n\n"
                "上一版输出仅供你避免重复错误，不要照抄：\n"
                f"{generated_text[:3000]}"
            )
            phase_start = time.perf_counter()
            retry_debug: dict = {}
            # 费用优化：retry_system = system_prompt + 返工规则，同一项目内稳定，启用 prompt cache
            _retry_cache_policy, _retry_stable_hash = _build_core_cache_policy(retry_system)
            retry_text = await llm.generate(
                system_prompt=retry_system,
                user_prompt=retry_user,
                temperature=0.68,
                task_type=LLMTaskType.CHAPTER_GENERATION,
                max_tokens=max_tokens,
                debug_info=retry_debug,
                cache_policy=_retry_cache_policy,
                stable_prefix_hash=_retry_stable_hash,
            )
            retry_event = _record_timing(
                timing_events,
                timing_totals,
                "llm_marker_retry_generate",
                phase_start,
                api_format=getattr(llm, "api_format", None),
                model=getattr(llm, "model", None),
                system_chars=len(retry_system),
                user_chars=len(retry_user),
                max_tokens=max_tokens,
                timeout_seconds=300.0,
                output_chars=len(retry_text or ""),
                provider_usage=retry_debug.get("usage"),
                provider_response_chars=retry_debug.get("response_chars"),
            )
            llm_calls.append(retry_event)
            # 方案9：重试输出也需解析事实清单
            retry_text, retry_facts = parse_writer_output(retry_text)
            if retry_facts:
                scene_facts = retry_facts
            retry_text = _ensure_complete_scene_ending(retry_text)
            phase_start = time.perf_counter()
            retry_check = await self._check_chapter_markers(retry_text, scene_map)
            _record_timing(
                timing_events,
                timing_totals,
                "marker_check_retry",
                phase_start,
                ok=bool(retry_check.get("ok")),
                score=retry_check.get("score"),
                method=retry_check.get("method"),
                span_count=retry_check.get("span_count"),
            )
            if retry_check["score"] >= marker_check["score"]:
                generated_text = retry_text
                marker_check = retry_check

        if skill_packet is not None:
            skill_trace["post_service_outputs"] = await self._execute_skill_services(
                skill_packet,
                {
                    "execution_phase": "post_generation",
                    "text": generated_text,
                    "style_features": skill_context.get("style_features", {}),
                    "style_embedding": skill_context.get("style_embedding", {}),
                    "persona_card": packet.get("persona_card") or {},
                    "style_directive": skill_context.get("style_directive", {}),
                },
            )
            _record_timing(
                timing_events,
                timing_totals,
                "skill_post_generation_services",
                phase_start,
                service_count=len(skill_trace.get("post_service_outputs") or {}),
            )
            phase_start = time.perf_counter()
            generated_text = await self._validate_and_repair_skill_output(
                llm=llm,
                skill_packet=skill_packet,
                skill_trace=skill_trace,
                generated_text=generated_text,
                validation_context={
                    "scene_contract": skill_context.get("scene_contract") or (scene_map[0] if scene_map else {}),
                    "chapter_state": skill_context.get("chapter_state", {}),
                    "style_context": (
                        skill_context.get("style_context")
                        or packet.get("style_context")
                        or {}
                    ),
                    "style_profile": skill_context.get("style_profile") or {},
                    "style_prompt": skill_context.get("style_prompt", ""),
                    "style_embedding": skill_context.get("style_embedding", {}),
                    "persona_card": packet.get("persona_card") or {},
                    "style_directive": skill_context.get("style_directive", {}),
                },
                max_tokens=max_tokens,
                protection_check=lambda text: self._check_chapter_markers(text, scene_map),
                timing_events=timing_events,
                timing_totals=timing_totals,
                allow_repair=False,
            )
            _record_timing(
                timing_events,
                timing_totals,
                "skill_validate_only_total",
                phase_start,
                output_chars=len(generated_text or ""),
            )

        timing_totals["chapter_writer_total"] = _duration_ms(writer_total_start)

        return {
            "generated_text": generated_text,
            "chapter_writer": True,
            "scene_count": len(scene_map),
            "context_ledger_id": packet.get("context_ledger_id", ""),
            "scene_facts": scene_facts,  # 方案9：writer 事实填写单
            "style_compliance": style_compliance,  # 方案 26：writer 风格遵守情况
            "prompt_debug": {
                "system_chars": len(system_prompt),
                "user_chars": len(user_prompt),
                "max_tokens": max_tokens,
                "target_chars": packet.get("target_chars"),
                "hard_max_chars": packet.get("hard_max_chars"),
                "scene_ids": [item.get("scene_id") for item in scene_map],
                "marker_check": marker_check,
                "marker_retry_used": retry_used,
                "persona_injected": bool(persona_text),
                "output_chars": len(generated_text or ""),
                "timing_ms": timing_totals,
                "timing_events": timing_events,
                "llm_calls": llm_calls,
                "skill_trace": skill_trace,
            },
        }

    async def _validate_and_repair_skill_output(
        self,
        *,
        llm,
        skill_packet,
        skill_trace: dict,
        generated_text: str,
        validation_context: dict,
        max_tokens: int,
        protection_check=None,
        timing_events: list | None = None,
        timing_totals: dict | None = None,
        allow_repair: bool = True,
    ) -> str:
        try:
            from app.services.agent_skill_runtime import get_skill_runtime

            runtime = get_skill_runtime()
            phase_start = time.perf_counter()
            validation = await runtime.validate_output(
                skill_packet,
                generated_text=generated_text,
                context=validation_context,
            )
            if timing_events is not None and timing_totals is not None:
                _record_timing(
                    timing_events,
                    timing_totals,
                    "skill_output_validation_initial",
                    phase_start,
                    passed=bool(validation.get("passed")),
                    failure_count=len(validation.get("failures") or []),
                )
            skill_trace["validation"] = validation
            if validation.get("passed"):
                return generated_text

            if not allow_repair:
                skill_trace["repair"] = {
                    "attempted": False,
                    "disabled": True,
                    "reason": "writer_stage_validation_only",
                    "deferred_to": "parallel_review_and_fbi_repair",
                }
                return generated_text

            retry_policy = self._skill_retry_policy(validation)
            if int(retry_policy.get("max_retries") or 0) < 1:
                return generated_text

            from app.services.agent_skill_protection import get_skill_protection
            from app.services.agent_skill_repair_dispatcher import get_skill_repair_dispatcher

            phase_start = time.perf_counter()
            dispatch = await get_skill_repair_dispatcher().dispatch(
                skill_packet,
                validation=validation,
                generated_text=generated_text,
                context=validation_context,
                llm=llm,
                max_tokens=max_tokens,
                validate_candidate=lambda text: runtime.validate_output(
                    skill_packet,
                    generated_text=text,
                    context=validation_context,
                ),
            )
            if timing_events is not None and timing_totals is not None:
                dispatch_attempts = [
                    {
                        "hook": item.get("hook"),
                        "status": item.get("status"),
                        "duration_ms": item.get("duration_ms"),
                        "metrics": item.get("metrics", []),
                        "executor": item.get("executor"),
                        "deterministic": item.get("deterministic"),
                    }
                    for item in (dispatch.get("attempts") or [])[:12]
                    if isinstance(item, dict)
                ]
                _record_timing(
                    timing_events,
                    timing_totals,
                    "skill_repair_dispatch",
                    phase_start,
                    completed=bool(dispatch.get("completed")),
                    output_chars=len(str(dispatch.get("text") or "")),
                    attempt_count=len(dispatch.get("attempts") or []),
                    dispatch_timing_ms=dispatch.get("timing_ms"),
                    attempts=dispatch_attempts,
                )
            if not dispatch.get("completed"):
                skill_trace["repair"] = {
                    "attempted": True,
                    "action": retry_policy.get("action", "repair"),
                    "replaced_output": False,
                    "dispatch": dispatch,
                }
                return generated_text

            repair_text = dispatch.get("text") or generated_text
            repair_text = _ensure_complete_scene_ending(repair_text)
            phase_start = time.perf_counter()
            repair_validation = await runtime.validate_output(
                skill_packet,
                generated_text=repair_text,
                context=validation_context,
            )
            if timing_events is not None and timing_totals is not None:
                _record_timing(
                    timing_events,
                    timing_totals,
                    "skill_output_validation_repaired",
                    phase_start,
                    passed=bool(repair_validation.get("passed")),
                    failure_count=len(repair_validation.get("failures") or []),
                )
            phase_start = time.perf_counter()
            protection_result = await get_skill_protection().audit(
                original_text=generated_text,
                candidate_text=repair_text,
                context=validation_context,
                structural_check=protection_check,
            )
            if timing_events is not None and timing_totals is not None:
                _record_timing(
                    timing_events,
                    timing_totals,
                    "skill_repair_protection",
                    phase_start,
                    ok=bool(protection_result.get("ok", True)),
                )
            original_failures = len(validation.get("failures") or [])
            repaired_failures = len(repair_validation.get("failures") or [])
            protection_ok = bool(protection_result.get("ok", True))
            should_replace = protection_ok and (
                bool(repair_validation.get("passed")) or repaired_failures < original_failures
            )
            skill_trace["repair"] = {
                "attempted": True,
                "action": retry_policy.get("action", "repair"),
                "replaced_output": should_replace,
                "dispatch": dispatch,
                "validation": repair_validation,
                "protection_check": protection_result,
            }
            if should_replace:
                skill_trace["validation"] = repair_validation
                return repair_text
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Skill output validation/repair failed", exc_info=True)
        return generated_text

    async def _execute_skill_services(self, skill_packet, context: dict) -> dict:
        try:
            from app.services.agent_skill_runtime import get_skill_runtime

            service_context = {key: value for key, value in context.items() if value is not None}
            service_context.setdefault("execution_phase", "pre_generation")
            return await get_skill_runtime().execute_services(skill_packet, service_context)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Skill service execution failed", exc_info=True)
            return {}

    @staticmethod
    def _render_skill_service_outputs(service_outputs: dict) -> str:
        if not service_outputs:
            return ""
        lines = ["\n\n### Skill Service Outputs"]
        for skill_id, output in service_outputs.items():
            if not isinstance(output, dict):
                continue
            if output.get("status") == "skipped":
                lines.append(f"- {skill_id}: skipped ({output.get('reason', 'unknown')})")
                continue
            rendered = json.dumps(output, ensure_ascii=False, default=str)
            if len(rendered) > 1800:
                rendered = rendered[:1800] + "...(truncated)"
            lines.append(f"#### {skill_id}\n{rendered}")
        return "\n".join(lines)

    @staticmethod
    def _skill_retry_policy(validation: dict) -> dict:
        for finding in validation.get("failures") or []:
            policy = finding.get("retry_policy")
            if isinstance(policy, dict) and policy:
                return policy
        for result in (validation.get("validators") or {}).values():
            constraints = result.get("constraints") if isinstance(result, dict) else {}
            policy = constraints.get("retry_policy") if isinstance(constraints, dict) else {}
            if isinstance(policy, dict) and policy:
                return policy
        return {}

    async def _llm_semantic_scene_split(
        self, text: str, scene_ids: list[str]
    ) -> list[str] | None:
        """方案20：调用 LLM 按场景语义切分正文。

        返回切分后的文本列表（与 scene_ids 一一对应），失败时返回 None。
        LLM 切分失败时由 SceneSpanAligner 内置降级为 paragraph_fallback。
        """
        if not text or not scene_ids or len(scene_ids) < 2:
            return None
        try:
            llm = await self.get_llm_client()
            prompt = (
                f"请将以下正文切分为 {len(scene_ids)} 个场景。\n\n"
                f"要求：\n"
                f"1. 按场景边界切分（时间跳转、地点转换、视角切换等）\n"
                f"2. 不要在句子中间切分\n"
                f"3. 每个场景应该是一个完整的叙事单元\n"
                f"4. 用 [[SCENE_SPLIT]] 标记切分点，只输出切分点标记\n\n"
                f"正文：\n{text}"
            )
            # 费用优化：场景切分 system_prompt 完全固定，启用 prompt cache
            _split_sys = "你是一个场景切分助手，负责将章节正文按场景边界切分。"
            _split_cache_policy, _split_stable_hash = _build_core_cache_policy(_split_sys)
            result = await llm.generate(
                system_prompt=_split_sys,
                user_prompt=prompt,
                temperature=0.3,
                task_type=LLMTaskType.JSON_DETECTION,
                cache_policy=_split_cache_policy,
                stable_prefix_hash=_split_stable_hash,
            )
            parts = result.split("[[SCENE_SPLIT]]")
            scenes = [p.strip() for p in parts if p.strip()]
            if len(scenes) == len(scene_ids):
                return scenes
            return None
        except Exception:
            return None

    async def _check_chapter_markers(self, text: str, scene_map: list[dict]) -> dict:
        scene_ids = [str(item.get("scene_id") or "") for item in scene_map if item.get("scene_id")]
        if not scene_ids:
            return {"ok": True, "score": 1, "reason": "no_scene_map"}

        # P1-C2 修复：标记缺失时启用 LLM 语义切分（方案20），而非直接降级为机械均分
        import re as _re_mod
        _marker_spans = list(_re_mod.finditer(
            r"^\s*(?:\[\[SCENE:[^\]]+\]\]|<<<SCENE_ID:[^>]+>>>)\s*$",
            text or "",
            _re_mod.MULTILINE,
        )) if text else []
        _needs_semantic = not _marker_spans or len(_marker_spans) != len(scene_ids)

        _semantic_cache: dict[str, list[str] | None] = {}
        if _needs_semantic and (text or "").strip():
            _fallback_text = SceneSpanAligner().strip_markers(text or "")
            if _fallback_text.strip():
                _semantic_cache["_result"] = await self._llm_semantic_scene_split(
                    _fallback_text, scene_ids
                )

        def _semantic_split_fn(_text: str, _scene_ids: list[str]) -> list[str] | None:
            return _semantic_cache.get("_result")

        aligned = SceneSpanAligner().align(
            text or "", scene_map, semantic_split_fn=_semantic_split_fn
        )
        spans = aligned.get("spans") or []
        empty = [
            span.get("scene_id") or span.get("scene_index")
            for span in spans
            if not str(span.get("text") or "").strip()
        ]
        method = aligned.get("method")
        span_ids = [str(span.get("scene_id") or "") for span in spans]
        has_all_ids = set(scene_ids).issubset(set(span_ids))
        ok = method == "explicit_markers" and len(spans) == len(scene_ids) and not empty and has_all_ids
        score = 0
        if method == "explicit_markers":
            score += 2
        score += min(len(spans), len(scene_ids))
        if not empty:
            score += 1
        if has_all_ids:
            score += 1
        return {
            "ok": ok,
            "score": score,
            "method": method,
            "span_count": len(spans),
            "expected_scene_count": len(scene_ids),
            "empty_spans": empty,
            "has_all_scene_ids": has_all_ids,
            "warnings": aligned.get("warnings", []),
        }

    def _summarize_character_card(self, card: dict) -> str:
        parts = []
        name = card.get("name", "未知")
        parts.append(f"**{name}**")

        if card.get("personality"):
            parts.append(f"  性格：{card['personality']}")
        if card.get("appearance"):
            parts.append(f"  外貌：{card['appearance']}")
        # 方案 3 A1：模型字段实际是 desire/deep_need，旧代码读 external_goal/internal_desire 永远为空
        # P1-2 修复：fallback 模式，避免两个字段都有值时重复输出
        external_goal = card.get("external_goal") or card.get("desire")
        if external_goal:
            parts.append(f"  外在目标：{external_goal}")
        internal_desire = card.get("internal_desire") or card.get("deep_need")
        if internal_desire:
            parts.append(f"  内在渴望：{internal_desire}")
        # 方案 3 A3：补全新字段
        if card.get("role"):
            parts.append(f"  角色定位：{card['role']}")
        if card.get("faction"):
            parts.append(f"  所属阵营：{card['faction']}")
        if card.get("status") and card.get("status") != "active":
            parts.append(f"  当前状态：{card['status']}")
        if card.get("arc"):
            parts.append(f"  人物弧光：{card['arc']}")
        if card.get("relationships"):
            parts.append(f"  关系：{card['relationships']}")

        return "\n".join(parts)
