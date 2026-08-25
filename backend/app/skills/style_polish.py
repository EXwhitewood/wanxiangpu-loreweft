import re


# ----------------------------------------------------------------------
# P2-20：方案 26 Part E2 style_polish 拆分 TODO（状态：拆分未完成，待方案27落地，非过时标记）
# ----------------------------------------------------------------------
# 文档《方案26 Part E2》要求将本模块拆为 4 个职责清晰的组件，当前
# StylePolishSkill 仍是单类承担"检测 + 改正文 + 验收 + 修复建议"全部职责，
# 拆分不完整。完整拆分需要同步迁移所有调用方（scene_generation_pipeline.py 等），
# 工作量较大，本文件先标注拆分蓝图，待方案 27 旧路径清理时一并落地。
#
# 拆分目标（详见 docs/04-主编系统/主编系统大型重构方案.md 方案26 Part E2）：
#
#   1. StyleProfileReader（读取风格画像）
#      - 职责：从 memory_core 读取 style_profile，归一化 style_features /
#        style_embedding / persona_card，提供统一的访问入口
#      - 替代：旧 polish_skill 内部对各字段的散落读取
#      TODO（待方案27落地）：从 editor_in_chief.py / scene_generation_pipeline.py 抽取 profile 读取逻辑
#
#   2. StyleReviewAdapter（把风格问题转成 ReviewCaseIssue）
#      - 职责：复用本 Skill 的只读检测方法（check_forbidden_words /
#        check_repetition / check_style_consistency / _infer_style_embedding /
#        _detect_dimension_failures / _detect_contract_style_conflict），
#        产出 ReviewCaseIssue dict 列表
#      - 现状：已实现在 app/services/style_profile_schema.py 的
#        StyleReviewAdapter 类（P2-17 已修复为只调用只读方法）
#      - TODO：本文件这些检测方法后续应迁移到 StyleReviewAdapter，
#        StylePolishSkill 只保留作为底层的检测能力提供者
#
#   3. StyleToolPlanner（把风格问题转成局部工具命令）
#      - 职责：根据 ReviewCaseIssue 生成 FBI 局部工具工单（如
#        normalize_structure_words / vary_sentence_shape / split_paragraph 等），
#        经 FBI 蓝图路由表（P2-18 已补全 3 个 LLM 工具）执行
#      - 替代：旧 polish_skill.polish() 的直接改正文（polish_transitions）
#      TODO（待方案27落地）：新建 StyleToolPlanner 类，承接 _build_repair_suggestions /
#        _generate_conflict_repair_hint 的规划逻辑，输出工具蓝图而非 polished_text
#
#   4. StyleValidationAdapter（给 Final Acceptance 提供风格验收结果）
#      - 职责：在 Final Acceptance 阶段提供风格验收（style_score、
#        requires_human_review、dimension_failures 等），不改正文
#      - 替代：旧 polish_skill 的 _compute_style_score / requires_human_review
#      TODO（待方案27落地）：新建 StyleValidationAdapter 类，承接 _compute_style_score 验收逻辑
#
# 关键禁止：polish_transitions / polish() 内部的 polished_text 生成是
# 写入副作用，禁止在 review 路径调用（P2-17 已在 StyleReviewAdapter 修复）。
# 后续拆分落地后，polish() 应仅保留为兼容入口，不进入主流程提交。
# ----------------------------------------------------------------------


class StylePolishSkill:
    FORBIDDEN_WORDS = [
        "不禁", "竟然", "居然", "赫然", "陡然", "蓦然",
        "居然是", "竟然是", "实在是", "真的是",
        "不由得", "忍不住", "情不自禁",
        "宛如", "犹如", "好似", "仿佛",
        "一时间", "刹那间", "霎时间",
    ]

    TRANSITION_PATTERNS = [
        (r"(\n\n[^\n]{0,5})突然", r"\1忽然"),
        (r"(\n\n[^\n]{0,5})忽然", r"\1骤然"),
        (r"却没想到", "谁知"),
        (r"没想到", "不料"),
        (r"与此同时", "同一时刻"),
    ]

    REPETITION_WINDOW = 500

    def check_forbidden_words(self, text: str) -> list[dict]:
        # Part E2 拆分归属：StyleReviewAdapter（只读检测）
        findings = []
        for word in self.FORBIDDEN_WORDS:
            for match in re.finditer(re.escape(word), text):
                start = max(0, match.start() - 20)
                end = min(len(text), match.end() + 20)
                context = text[start:end]
                findings.append({
                    "type": "forbidden_word",
                    "word": word,
                    "position": match.start(),
                    "context": context,
                    "suggestion": f"避免使用「{word}」，尝试更具体的描写",
                })
        return findings

    def check_repetition(self, text: str) -> list[dict]:
        # Part E2 拆分归属：StyleReviewAdapter（只读检测）
        findings = []
        sentences = re.split(r'[。！？\n]', text)
        seen_phrases: dict[str, list[int]] = {}

        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if len(sentence) < 4:
                continue

            for length in range(4, min(len(sentence) + 1, 12)):
                for start in range(len(sentence) - length + 1):
                    phrase = sentence[start:start + length]
                    if phrase in seen_phrases:
                        seen_phrases[phrase].append(i)
                    else:
                        seen_phrases[phrase] = [i]

        for phrase, positions in seen_phrases.items():
            if len(positions) >= 3:
                findings.append({
                    "type": "repetition",
                    "phrase": phrase,
                    "count": len(positions),
                    "suggestion": f"短语「{phrase}」重复出现{len(positions)}次，建议变换表达",
                })

        return findings[:10]

    def polish_transitions(self, text: str) -> tuple[str, list[dict]]:
        # Part E2 拆分归属：StyleToolPlanner（写入副作用，禁止在 review 路径调用）
        # TODO（待方案27落地）：拆分后此方法应迁移到 StyleToolPlanner 作为"过渡词替换"工具的
        # 执行器，由 FBI 蓝图路由触发，而非由 polish() 直接调用生成 polished_text。
        changes = []
        result = text

        for pattern, replacement in self.TRANSITION_PATTERNS:
            matches = list(re.finditer(pattern, result))
            for match in reversed(matches):
                original = match.group(0)
                new_text = match.expand(replacement)
                if original != new_text:
                    changes.append({
                        "type": "transition",
                        "original": original,
                        "replaced": new_text,
                        "position": match.start(),
                    })
                    result = result[:match.start()] + new_text + result[match.end():]

        return result, changes

    # 风格维度名称到中文名的映射
    DIMENSION_LABELS = {
        "emotionality": "情感外显度",
        "sentence_complexity": "句式复杂度",
        "narrative_distance": "叙事距离",
        "info_density": "信息密度",
        "dialogue_ratio": "对白占比",
        "description_density": "描写密度",
        "rhythm_steepness": "节奏陡度",
        "narrator_intrusion": "叙述者介入度",
    }

    # 维度偏离阈值：超过此值视为该维度失败
    DIMENSION_DELTA_THRESHOLD = 0.25

    def polish(
        self,
        text: str,
        style_features: dict | None = None,
        style_embedding: dict | None = None,
        persona_card: dict | None = None,
        style_directive: dict | None = None,
    ) -> dict:
        # Part E2 拆分归属：编排入口（含写入副作用 polish_transitions）
        # TODO（待方案27落地）：拆分后此方法应拆为三部分：
        #   - 检测部分 → StyleReviewAdapter.detect_style_issues（已实现，只读）
        #   - 写入部分（polish_transitions）→ StyleToolPlanner 工具执行
        #   - 验收部分（style_score/requires_human_review）→ StyleValidationAdapter
        # 主流程不再调用本方法直接改正文（方案26 Part E1 已禁止）。
        # 本方法保留为兼容入口，仅供历史调用方/测试使用。
        forbidden = self.check_forbidden_words(text)
        repetition = self.check_repetition(text)
        polished_text, transition_changes = self.polish_transitions(text)

        all_issues = forbidden + repetition
        has_issues = len(all_issues) > 0 or len(transition_changes) > 0

        style_violations = []
        if style_features:
            style_violations = self.check_style_consistency(text, style_features)
            if style_violations:
                has_issues = True

        inferred_embedding = self._infer_style_embedding(text)
        embedding_delta = {}
        if style_embedding:
            for key, target in style_embedding.items():
                if not isinstance(target, (int, float)):
                    continue
                embedding_delta[key] = round(abs(inferred_embedding.get(key, 0.0) - float(target)), 3)

        # 结构化风格维度失败标记
        dimension_failures = self._detect_dimension_failures(
            embedding_delta, style_directive, inferred_embedding,
        )
        if dimension_failures:
            has_issues = True

        style_score = self._compute_style_score(forbidden, repetition, style_violations, embedding_delta)
        repair_suggestions = self._build_repair_suggestions(forbidden, repetition, style_violations, persona_card)
        requires_human_review = style_score < 55 or len(style_violations) >= 6

        # 判断是否属于合同-风格冲突（应回流主编）
        contract_style_conflict = self._detect_contract_style_conflict(
            dimension_failures, style_directive,
        )

        if style_score < 70 or requires_human_review:
            has_issues = True

        return {
            "polished_text": polished_text,
            "issues": all_issues,
            "transition_changes": transition_changes,
            "style_violations": style_violations,
            "violations": all_issues + style_violations,
            "style_score": style_score,
            "embedding_delta": embedding_delta,
            "dimension_failures": dimension_failures,
            "contract_style_conflict": contract_style_conflict,
            "repair_suggestions": repair_suggestions,
            "requires_human_review": requires_human_review,
            "has_issues": has_issues,
            "summary": {
                "forbidden_words": len(forbidden),
                "repetitions": len(repetition),
                "transitions_polished": len(transition_changes),
                "style_violations": len(style_violations),
                "style_score": style_score,
                "dimension_failures": len(dimension_failures),
                "contract_style_conflict": contract_style_conflict is not None,
            },
        }

    def check_style_consistency(self, text: str, style_features: dict) -> list[dict]:
        # Part E2 拆分归属：StyleReviewAdapter（只读检测）
        findings = []
        avoid_patterns = style_features.get("avoid_patterns", [])
        if not avoid_patterns:
            return findings

        for pattern in avoid_patterns:
            if not pattern:
                continue
            if pattern in text:
                start = text.index(pattern)
                context_start = max(0, start - 20)
                context_end = min(len(text), start + len(pattern) + 20)
                context = text[context_start:context_end]
                findings.append({
                    "type": "style_violation",
                    "pattern": pattern,
                    "position": start,
                    "context": context,
                    "suggestion": f"当前风格应避免「{pattern}」，请替换为更符合风格的写法",
                })

        return findings

    def _infer_style_embedding(self, text: str) -> dict:
        # Part E2 拆分归属：StyleReviewAdapter（只读检测，被维度失败检测复用）
        total_chars = len(text)
        if total_chars == 0:
            return {
                "emotionality": 0.0,
                "sentence_complexity": 0.0,
                "narrative_distance": 0.0,
                "info_density": 0.0,
                "dialogue_ratio": 0.0,
                "description_density": 0.0,
                "rhythm_steepness": 0.0,
                "narrator_intrusion": 0.0,
            }

        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        sentence_lengths = []
        for paragraph in paragraphs:
            for sentence in re.split(r"[。！？；]", paragraph):
                sentence = sentence.strip()
                if len(sentence) > 1:
                    sentence_lengths.append(len(sentence))

        emotion_keywords = ["悲", "喜", "怒", "哀", "爱", "恨", "痛", "哭", "笑", "泪", "怕", "惊", "愁", "怨"]
        first_person_words = ["我", "我们", "自己", "内心", "感觉", "觉得", "认为", "想到", "想起"]
        action_words = ["走", "跑", "打", "杀", "冲", "追", "推", "拉", "握", "拔", "闪", "砍", "刺"]
        desc_keywords = ["如", "像", "仿佛", "宛如", "似", "苍", "暗", "静", "微", "深", "冷", "亮", "风", "雨", "雪"]
        narrator_words = ["显然", "事实上", "总之", "也许", "大概", "无疑", "看来"]

        emotion_count = sum(text.count(kw) for kw in emotion_keywords)
        fp_count = sum(text.count(w) for w in first_person_words)
        act_count = sum(text.count(w) for w in action_words)
        desc_count = sum(text.count(kw) for kw in desc_keywords)
        narrator_count = sum(text.count(w) for w in narrator_words)
        dialogue_chars = sum(len(p) for p in paragraphs if p.count("“") + p.count("”") + p.count("\"") >= 2)
        para_lengths = [len(p) for p in paragraphs]

        avg_sent_len = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0
        avg_para_len = sum(para_lengths) / len(para_lengths) if para_lengths else 0
        variance = sum((l - avg_para_len) ** 2 for l in para_lengths) / len(para_lengths) if para_lengths else 0

        return {
            "emotionality": round(min(1.0, emotion_count / max(total_chars / 100, 1)), 3),
            "sentence_complexity": round(min(1.0, avg_sent_len / 40.0), 3),
            "narrative_distance": round(fp_count / max(fp_count + act_count, 1), 3),
            "info_density": round(min(1.0, len(set(re.findall(r'[\u4e00-\u9fff]{2,4}', text))) / max(total_chars / 10, 1)), 3),
            "dialogue_ratio": round(min(1.0, dialogue_chars / max(total_chars, 1)), 3),
            "description_density": round(min(1.0, desc_count / max(total_chars / 200, 1)), 3),
            "rhythm_steepness": round(min(1.0, (variance ** 0.5) / 200.0), 3) if variance else 0.0,
            "narrator_intrusion": round(min(1.0, narrator_count / max(total_chars / 500, 1)), 3),
        }

    def _compute_style_score(
        # Part E2 拆分归属：StyleValidationAdapter（验收打分，只读）
        # TODO（待方案27落地）：迁移到 StyleValidationAdapter，作为 Final Acceptance 风格验收依据。
        self,
        forbidden: list[dict],
        repetition: list[dict],
        style_violations: list[dict],
        embedding_delta: dict,
    ) -> int:
        score = 100
        score -= len(forbidden) * 4
        score -= len(repetition) * 3
        score -= len(style_violations) * 6
        if embedding_delta:
            score -= int(sum(embedding_delta.values()) / max(len(embedding_delta), 1) * 35)
        return max(0, min(100, score))

    def _build_repair_suggestions(
        # Part E2 拆分归属：StyleToolPlanner（修复建议规划，只读）
        # TODO（待方案27落地）：迁移到 StyleToolPlanner，输出 FBI 工具蓝图而非 suggestion 字符串。
        self,
        forbidden: list[dict],
        repetition: list[dict],
        style_violations: list[dict],
        persona_card: dict | None,
    ) -> list[str]:
        suggestions = []
        for item in forbidden[:3]:
            if item.get("suggestion"):
                suggestions.append(item["suggestion"])
        for item in repetition[:2]:
            if item.get("suggestion"):
                suggestions.append(item["suggestion"])
        for item in style_violations[:3]:
            if item.get("suggestion"):
                suggestions.append(item["suggestion"])
        if persona_card and persona_card.get("hard_rules"):
            suggestions.append("再次核对叙述者硬规则，避免越界。")
        return suggestions[:8]

    def _detect_dimension_failures(
        # Part E2 拆分归属：StyleReviewAdapter（维度偏离检测，只读）
        self,
        embedding_delta: dict,
        style_directive: dict | None,
        inferred_embedding: dict,
    ) -> list[dict]:
        """检测风格维度偏离，输出结构化失败标记。

        每个失败标记包含：
        - dimension: 维度英文名
        - label: 维度中文名
        - delta: 偏离幅度
        - direction: 偏离方向（偏高/偏低）
        - severity: 严重程度（high/medium/low）
        - source: 偏离来源（embedding_delta 或 style_directive）
        """
        failures: list[dict] = []

        # 1. 基于 embedding_delta 检测偏离
        for dim, delta in embedding_delta.items():
            if delta < self.DIMENSION_DELTA_THRESHOLD:
                continue
            label = self.DIMENSION_LABELS.get(dim, dim)
            target_val = 0.0
            actual_val = inferred_embedding.get(dim, 0.0)
            direction = "偏高" if actual_val > target_val else "偏低"

            # 根据 style_directive 的 skeleton/persona 判断方向
            if style_directive:
                direction = self._infer_dimension_direction(
                    dim, style_directive, actual_val,
                )

            severity = "high" if delta >= 0.4 else "medium" if delta >= 0.3 else "low"
            failures.append({
                "dimension": dim,
                "label": label,
                "delta": delta,
                "direction": direction,
                "severity": severity,
                "source": "embedding_delta",
                "actual": actual_val,
            })

        # 2. 基于 style_directive 的 skeleton/persona 检测偏离
        if style_directive:
            failures.extend(
                self._check_directive_skeleton_failures(
                    style_directive, inferred_embedding,
                )
            )

        return failures

    def _infer_dimension_direction(
        self, dim: str, style_directive: dict, actual_val: float,
    ) -> str:
        """根据 style_directive 推断维度偏离方向。"""
        skeleton = style_directive.get("skeleton", {})
        persona = style_directive.get("persona", {})

        # skeleton 维度映射
        skeleton_map = {
            "pacing": "rhythm_steepness",
            "dialogue_ratio": "dialogue_ratio",
            "description_density": "description_density",
            "info_density": "info_density",
        }
        # persona 维度映射
        persona_map = {
            "narrative_distance": "narrative_distance",
            "narrator_intrusion": "narrator_intrusion",
            "emotionality": "emotionality",
        }

        target_level = None
        if dim in skeleton_map.values():
            for key, mapped_dim in skeleton_map.items():
                if mapped_dim == dim:
                    target_level = skeleton.get(key)
                    break
        elif dim in persona_map.values():
            for key, mapped_dim in persona_map.items():
                if mapped_dim == dim:
                    target_level = persona.get(key)
                    break

        if not target_level:
            return "偏离"

        # 将中文等级映射为数值
        level_to_value = {
            "极低": 0.1, "低": 0.25, "中低": 0.35,
            "中": 0.5, "中高": 0.65, "高": 0.75, "极高": 0.9,
            "快": 0.75, "慢": 0.25, "远": 0.75, "近": 0.25,
        }
        target_val = level_to_value.get(target_level, 0.5)
        return "偏高" if actual_val > target_val else "偏低"

    def _check_directive_skeleton_failures(
        self, style_directive: dict, inferred_embedding: dict,
    ) -> list[dict]:
        """检查 style_directive 中 skeleton/persona 约束与实际推断的偏离。"""
        failures: list[dict] = []
        skeleton = style_directive.get("skeleton", {})
        persona = style_directive.get("persona", {})

        # skeleton 维度检查
        skeleton_checks = {
            "pacing": ("rhythm_steepness", {"快": 0.6, "慢": 0.3}),
            "dialogue_ratio": ("dialogue_ratio", {"高": 0.6, "中": 0.4, "低": 0.2}),
            "description_density": ("description_density", {"高": 0.6, "中": 0.4, "低": 0.2}),
            "info_density": ("info_density", {"高": 0.6, "中高": 0.5, "中": 0.4, "低": 0.2}),
        }

        for directive_key, (dim, level_map) in skeleton_checks.items():
            level = skeleton.get(directive_key)
            if not level or level not in level_map:
                continue
            target_val = level_map[level]
            actual_val = inferred_embedding.get(dim, 0.0)
            delta = round(abs(actual_val - target_val), 3)
            if delta >= self.DIMENSION_DELTA_THRESHOLD:
                label = self.DIMENSION_LABELS.get(dim, dim)
                direction = "偏高" if actual_val > target_val else "偏低"
                severity = "high" if delta >= 0.4 else "medium" if delta >= 0.3 else "low"
                failures.append({
                    "dimension": dim,
                    "label": label,
                    "delta": delta,
                    "direction": direction,
                    "severity": severity,
                    "source": "style_directive_skeleton",
                    "directive_key": directive_key,
                    "directive_level": level,
                    "actual": actual_val,
                })

        # persona 维度检查
        persona_checks = {
            "narrative_distance": ("narrative_distance", {"远": 0.6, "近": 0.3}),
            "narrator_intrusion": ("narrator_intrusion", {"高": 0.5, "低": 0.2}),
            "emotionality": ("emotionality", {"高": 0.6, "低": 0.2}),
        }

        for directive_key, (dim, level_map) in persona_checks.items():
            level = persona.get(directive_key)
            if not level or level not in level_map:
                continue
            target_val = level_map[level]
            actual_val = inferred_embedding.get(dim, 0.0)
            delta = round(abs(actual_val - target_val), 3)
            if delta >= self.DIMENSION_DELTA_THRESHOLD:
                label = self.DIMENSION_LABELS.get(dim, dim)
                direction = "偏高" if actual_val > target_val else "偏低"
                severity = "high" if delta >= 0.4 else "medium" if delta >= 0.3 else "low"
                failures.append({
                    "dimension": dim,
                    "label": label,
                    "delta": delta,
                    "direction": direction,
                    "severity": severity,
                    "source": "style_directive_persona",
                    "directive_key": directive_key,
                    "directive_level": level,
                    "actual": actual_val,
                })

        return failures

    def _detect_contract_style_conflict(
        # Part E2 拆分归属：StyleReviewAdapter（合同-风格冲突检测，只读）
        self,
        dimension_failures: list[dict],
        style_directive: dict | None,
    ) -> dict | None:
        """判断风格失败是否属于合同-风格冲突（应回流主编而非仅润色）。

        返回 None 表示无合同冲突，返回 dict 表示检测到合同-风格冲突。
        """
        if not dimension_failures or not style_directive:
            return None

        # 如果有高严重度的维度失败且来源是 style_directive，
        # 说明 Writer 试图遵守 style_directive 但实际产出严重偏离，
        # 可能是合同约束与风格约束矛盾导致
        high_severity_failures = [
            f for f in dimension_failures
            if f.get("severity") == "high" and f.get("source", "").startswith("style_directive")
        ]
        if not high_severity_failures:
            return None

        # 检查是否有 avoid 列表中的条目与维度失败对应
        avoid_list = style_directive.get("avoid", [])
        if not isinstance(avoid_list, list):
            avoid_list = []

        conflict_dimensions = []
        for failure in high_severity_failures:
            dim = failure["dimension"]
            direction = failure["direction"]
            conflict_dimensions.append({
                "dimension": dim,
                "label": failure.get("label", dim),
                "direction": direction,
                "delta": failure["delta"],
                "repair_hint": self._generate_conflict_repair_hint(dim, direction, avoid_list),
            })

        if not conflict_dimensions:
            return None

        return {
            "conflict_type": "style_contract_conflict",
            "conflict_dimensions": conflict_dimensions,
            "style_directive_id": style_directive.get("profile_id", ""),
            "scene_style_role": style_directive.get("scene_style_role", "inherit"),
            "repair_path": "editor_review",
            "suggested_action": "回流主编修订 style_directive 或 editor_enrichment",
        }

    def _generate_conflict_repair_hint(
        # Part E2 拆分归属：StyleToolPlanner（冲突修复建议，只读）
        # TODO（待方案27落地）：迁移到 StyleToolPlanner，作为回流主编的修复提示生成器。
        self, dimension: str, direction: str, avoid_list: list[str],
    ) -> str:
        """根据维度和方向生成修复建议。"""
        hints = {
            "narrative_distance": {
                "偏高": "降低叙事距离要求，允许更多内心呈现",
                "偏低": "提高叙事距离要求，减少内心独白",
            },
            "emotionality": {
                "偏高": "降低情感外显要求，改为克制表达",
                "偏低": "允许更多情感表达空间",
            },
            "description_density": {
                "偏高": "减少环境描写要求，改为关键细节点染",
                "偏低": "增加描写空间，允许氛围铺陈",
            },
            "info_density": {
                "偏高": "降低信息密度要求，分散信息揭示",
                "偏低": "允许更多直接信息传达",
            },
            "dialogue_ratio": {
                "偏高": "减少对白要求，增加叙述空间",
                "偏低": "增加对白场景，减少叙述负担",
            },
        }
        dim_hints = hints.get(dimension, {})
        return dim_hints.get(direction, f"调整 {self.DIMENSION_LABELS.get(dimension, dimension)} 的合同要求")
