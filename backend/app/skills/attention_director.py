import json

from app.services.state_manager import StateManager
from app.skills.state_query import StateQuerySkill


class AttentionDirectorSkill:
    def __init__(self):
        self.state_query = StateQuerySkill()

    async def build_prompt(
        self,
        project_id: str,
        scene_beat: dict,
        pov_character: str | None = None,
        foreshadowing_instructions: list[dict] | None = None,
        style_context: dict | None = None,
        style_prompt: str | None = None,
        style_samples: list[dict] | None = None,
        chapter_summaries: str = "",
        forward_constraints: str = "",
        relevant_rules: dict | None = None,
        genre_profile: dict | None = None,
    ) -> str:
        state_data = await self.state_query.get_snapshot(project_id)
        state = state_data

        pov = pov_character or state.get("pov_character")

        sections = []

        sections.append(self._build_scene_focus(scene_beat))

        if pov:
            pov_knowledge = await self.state_query.get_pov_knowledge(project_id, pov)
            sections.append(self._build_pov_section(pov, pov_knowledge, state))
        else:
            sections.append(self._build_objective_section(state))

        sections.append(self._build_attention_rules(scene_beat))

        if foreshadowing_instructions:
            sections.append(self._build_foreshadowing_section(foreshadowing_instructions))

        if style_context or style_prompt:
            sections.append(self._build_style_section(style_context, style_prompt, style_samples))

        if chapter_summaries:
            sections.append(self._build_chapter_summaries_section(chapter_summaries))
        if forward_constraints:
            sections.append(self._build_forward_constraints_section(forward_constraints))
        if relevant_rules and relevant_rules.get("relevant_details"):
            sections.append(self._build_relevant_rules_section(relevant_rules))

        sections.append(self._build_activation_reminder(scene_beat, relevant_rules, genre_profile))

        return "\n\n".join(sections)

    def _build_scene_focus(self, scene_beat: dict) -> str:
        goal = scene_beat.get("goal", "")
        conflict = scene_beat.get("conflict", "")
        outcome = scene_beat.get("outcome", "")
        value_shift = scene_beat.get("value_shift", "")

        parts = ["## 场景焦点"]
        if goal:
            parts.append(f"- 场景目标：{goal}")
        if conflict:
            parts.append(f"- 核心冲突：{conflict}")
        if outcome:
            parts.append(f"- 预期结果：{outcome}")
        if value_shift:
            parts.append(f"- 价值转变：{value_shift}")
        if not any([goal, conflict, outcome, value_shift]):
            parts.append("- 自由创作，注意保持叙事连贯性")
        return "\n".join(parts)

    def _build_chapter_summaries_section(self, chapter_summaries: str) -> str:
        parts = ["## 前文章节摘要"]
        parts.append("以下是前几章的关键信息摘要，确保当前场景与前文保持叙事连续性：")
        parts.append(chapter_summaries)
        return "\n".join(parts)

    def _build_forward_constraints_section(self, forward_constraints: str) -> str:
        parts = ["## 前瞻约束"]
        parts.append("以下是后续章节对当前场景的约束，当前场景不得违反：")
        parts.append(forward_constraints)
        return "\n".join(parts)

    def _build_relevant_rules_section(self, relevant_rules: dict) -> str:
        parts = ["## 相关世界观铁则"]
        critical_index = relevant_rules.get("critical_index", [])
        if critical_index:
            parts.append("### 铁则索引（不可违背）")
            for r in critical_index:
                parts.append(f"- **{r.get('name', '')}**：{r.get('core', '')}")
        relevant_details = relevant_rules.get("relevant_details", [])
        if relevant_details:
            parts.append("### 与当前场景相关的规则详情")
            for r in relevant_details[:10]:
                name = r.get("name", "")
                desc = r.get("description", "")[:80]
                constraints = r.get("constraints", [])
                parts.append(f"- **{name}**：{desc}")
                if constraints:
                    parts.append(f"  - 约束：{constraints[0]}")
        return "\n".join(parts)

    def _build_activation_reminder(self, scene_beat: dict, relevant_rules: dict | None = None, genre_profile: dict | None = None) -> str:
        parts = ["【规则激活——本章重新加载】"]
        if relevant_rules:
            critical_index = relevant_rules.get("critical_index", [])
            if critical_index:
                names = "、".join(r.get("name", "") for r in critical_index[:5])
                parts.append(f"- 世界观铁则：{names}")
        pov = scene_beat.get("pov_character", scene_beat.get("pov", ""))
        if pov:
            parts.append(f"- POV角色：{pov}")
        # 从 Genre Profile 读取禁忌，无 Profile 时使用通用 fallback
        from app.services.genre_profile_service import GenreProfileService
        genre_svc = GenreProfileService()
        prohibitions = genre_svc.get_activation_prohibitions(genre_profile)
        parts.append(f"- 禁止：{'、'.join(prohibitions)}")
        return "\n".join(parts)

    def _build_pov_section(self, pov_character: str, pov_knowledge: dict, state: dict) -> str:
        parts = [f"## POV 角色：{pov_character}"]

        believed = pov_knowledge.get("believed_state", {})
        last_known = pov_knowledge.get("last_known", {})

        if believed:
            parts.append("### 角色主观认知（此角色知道/相信的）")
            for entity_id, info in believed.items():
                if isinstance(info, dict):
                    parts.append(f"- {entity_id}：{json.dumps(info, ensure_ascii=False)}")
                else:
                    parts.append(f"- {entity_id}：{info}")

        if last_known:
            parts.append("### 角色最后已知信息")
            for entity_id, info in last_known.items():
                if isinstance(info, dict):
                    parts.append(f"- {entity_id}：{json.dumps(info, ensure_ascii=False)}")
                else:
                    parts.append(f"- {entity_id}：{info}")

        obj_state = state.get("objective_state", {})
        if obj_state:
            hidden = []
            for entity_id, entity_data in obj_state.items():
                if entity_id not in believed and entity_id not in last_known:
                    if isinstance(entity_data, dict):
                        hidden.append(f"- {entity_id}：{json.dumps(entity_data, ensure_ascii=False)}")
            if hidden:
                parts.append("### 角色不知道的客观事实（禁止角色直接引用，但可用于制造信息差）")
                parts.extend(hidden)

        return "\n".join(parts)

    def _build_objective_section(self, state: dict) -> str:
        parts = ["## 当前世界状态（客观视角）"]
        obj_state = state.get("objective_state", {})
        if obj_state:
            for entity_id, entity_data in obj_state.items():
                if isinstance(entity_data, dict):
                    parts.append(f"- {entity_id}：{json.dumps(entity_data, ensure_ascii=False)}")
                else:
                    parts.append(f"- {entity_id}：{entity_data}")
        else:
            parts.append("- 暂无状态记录")
        return "\n".join(parts)

    def _build_attention_rules(self, scene_beat: dict) -> str:
        parts = ["## 注意力规则"]
        parts.append("- 严格遵循 POV 角色的认知边界，角色不能知道其主观认知之外的信息")
        parts.append("- 利用信息差时只能通过可感知细节呈现，不能让角色直接知晓")
        parts.append("- 场景描写必须通过 POV 角色的感官来呈现")
        parts.append("- 对话中角色的言辞必须符合其已知信息范围")
        conflict = scene_beat.get("conflict", "")
        if conflict:
            parts.append(f"- 重点聚焦冲突「{conflict}」的推进和升级")
        return "\n".join(parts)

    def _build_foreshadowing_section(self, foreshadowing_instructions: list[dict]) -> str:
        parts = ["## 场景细节约束"]

        for item in foreshadowing_instructions:
            compiled = item.get("compiled_hint")
            if compiled:
                parts.append(compiled)
                continue

            if item.get("action") == "reveal":
                parts.append("- 让相关信息通过外部事件、证物、对话停顿或人物行动进入场景。")
            else:
                parts.append("- 将相关内容改写为角色当下可感知的环境、动作或对话细节。")
            parts.append("- 不要直接写出原始说明中的结论性身份、动机、因果或世界规则。")

        if len(parts) == 1:
            parts.append("- 本次场景无特定隐藏信息需要处理")

        return "\n".join(parts)

    def _sanitize_foreshadowing_text(self, text: str) -> str:
        replacements = {
            "伏笔": "后续信息",
            "埋设": "加入",
            "线索": "细节",
            "暗示": "含蓄呈现",
            "读者": "阅读体验",
            "叙事需求": "场景要求",
            "回收": "回应",
            "揭示任务": "信息进入场景",
        }
        sanitized = text
        for src, dst in replacements.items():
            sanitized = sanitized.replace(src, dst)
        return sanitized

    def _build_style_section(
        self,
        style_context: dict | None = None,
        style_prompt: str | None = None,
        style_samples: list[dict] | None = None,
    ) -> str:
        parts = ["## 文笔风格约束"]
        context = style_context or {}
        style_prompt = style_prompt or context.get("style_prompt", "")
        style_embedding = context.get("style_embedding", {})
        persona_card = context.get("persona_card", {})
        style_statistics = context.get("style_statistics", {})
        evolution_report = context.get("evolution_report", {})

        if style_prompt:
            parts.append("### 纹理层")
            parts.append(style_prompt)

        if style_embedding:
            parts.append("### 骨架层")
            embedding_labels = {
                "emotionality": "情感外显度",
                "sentence_complexity": "句式复杂度",
                "narrative_distance": "叙事距离",
                "info_density": "信息密度",
                "dialogue_ratio": "对话占比",
                "description_density": "描写密度",
                "rhythm_steepness": "节奏陡峭度",
                "narrator_intrusion": "叙事者介入度",
            }
            for key, label in embedding_labels.items():
                value = style_embedding.get(key)
                if value is None:
                    continue
                parts.append(f"- {label}：{float(value):.2f}")

        if style_statistics:
            global_stats = style_statistics.get("global", {})
            if global_stats:
                parts.append("### 统计层")
                parts.append(
                    f"- 章节数：{global_stats.get('chapter_count', 0)}，段落数：{global_stats.get('paragraph_count', 0)}，总字数：{global_stats.get('total_chars', 0)}"
                )
                top_words = global_stats.get("word_freq_top", [])[:5]
                if top_words:
                    formatted = "、".join(f"{item.get('word', '')}×{item.get('count', 0)}" for item in top_words if item.get("word"))
                    if formatted:
                        parts.append(f"- 高频词：{formatted}")

        if persona_card:
            parts.append("### 人格层")
            if persona_card.get("identity"):
                parts.append(f"- 身份：{persona_card['identity']}")
            if persona_card.get("decision_pattern"):
                parts.append(f"- 决策模式：{persona_card['decision_pattern']}")
            if persona_card.get("expression_style"):
                parts.append(f"- 表达风格：{persona_card['expression_style']}")
            if persona_card.get("interpersonal_behavior"):
                parts.append(f"- 人际行为：{persona_card['interpersonal_behavior']}")
            hard_rules = persona_card.get("hard_rules", [])
            if hard_rules:
                parts.append(f"- 硬规则：{'；'.join(hard_rules[:3])}")

        if evolution_report:
            parts.append("### 演变提示")
            if evolution_report.get("is_multi_style"):
                parts.append("- 该文本存在多风格簇，注意按章节范围切换风格画像。")
            if evolution_report.get("recommended_usage"):
                parts.append(f"- {evolution_report['recommended_usage']}")

        if style_samples:
            parts.append("\n### 风格范例")
            for sample in style_samples[:3]:
                text = sample.get("text", "")
                category = sample.get("category", "")
                if text:
                    label = f"（{category}）" if category else ""
                    parts.append(f"【范例{label}】{text}")

        return "\n".join(parts)
