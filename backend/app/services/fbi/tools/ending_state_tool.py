"""结尾状态工具——判断结尾合同是否达成。

纯计算工具，不含 LLM 调用。
"""

from __future__ import annotations

import re


class EndingStateTool:
    """结尾状态工具——判断结尾合同是否达成。"""

    # 状态达成的信号词
    _FULFILLMENT_SIGNALS = {
        "action": [
            r"拿[了着]?", r"走[了着]?", r"跑[了着]?", r"打开", r"关上",
            r"推[了开]?", r"拉[了开]?", r"扔[了掉]?", r"放[了下]?",
            r"写[了下]?", r"签[了下]?", r"点[了头]?", r"摇[了头]?",
        ],
        "cognition": [
            r"明白了?", r"懂了?", r"意识到", r"发现", r"看清",
            r"终于明白", r"这才明白", r"忽然明白", r"突然明白",
        ],
        "decision": [
            r"决定", r"选择", r"下定决心", r"下定决心", r"做出了?决定",
            r"咬了咬牙", r"不再犹豫", r"终于",
        ],
        "aftermath": [
            r"之后", r"后来", r"从那以后", r"此后", r"再也没",
            r"终于", r"已经", r"再也",
        ],
    }

    # 抽象句模式（不应作为结尾达成的证据）
    _ABSTRACT_PATTERNS = [
        r"她决定了",
        r"她明白了",
        r"他知道了",
        r"她懂了",
        r"他意识到了",
        r"她选择了",
    ]

    def check_ending_contract(self, text: str, ending_contract: dict) -> dict:
        """判断结尾状态是否出现。

        Args:
            text: 正文
            ending_contract: 结尾合同，格式如：
                {
                    "required_states": ["主角做出决定", "冲突得到解决"],
                    "optional_states": ["余波描写"],
                }

        Returns:
            {
                "fulfilled": [{"state": str, "evidence": str, "position": int}],
                "missing": [str],
                "partial": [{"state": str, "evidence": str, "position": int, "note": str}],
            }
        """
        required_states = ending_contract.get("required_states", [])
        optional_states = ending_contract.get("optional_states", [])

        fulfilled: list[dict] = []
        missing: list[str] = []
        partial: list[dict] = []

        # Check last 30% of text for ending states
        text_len = len(text)
        tail_start = max(0, int(text_len * 0.7))
        tail_text = text[tail_start:]

        for state in required_states:
            state_result = self._check_single_state(state, text, tail_text, tail_start)
            if state_result["status"] == "fulfilled":
                fulfilled.append({
                    "state": state,
                    "evidence": state_result["evidence"],
                    "position": state_result["position"],
                })
            elif state_result["status"] == "partial":
                partial.append({
                    "state": state,
                    "evidence": state_result["evidence"],
                    "position": state_result["position"],
                    "note": state_result.get("note", "仅有抽象表达，缺少可见动作"),
                })
            else:
                missing.append(state)

        # Check optional states (only report fulfilled, don't report missing)
        for state in optional_states:
            state_result = self._check_single_state(state, text, tail_text, tail_start)
            if state_result["status"] == "fulfilled":
                fulfilled.append({
                    "state": state,
                    "evidence": state_result["evidence"],
                    "position": state_result["position"],
                })

        return {
            "fulfilled": fulfilled,
            "missing": missing,
            "partial": partial,
        }

    def tail_slot_finder(self, text: str) -> dict:
        """判断是追加尾段还是改写最后一段。

        Returns:
            {
                "action": "append"|"rewrite_last"|"rewrite_last_two",
                "insert_position": int,
                "max_append_chars": int,
            }
        """
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return {
                "action": "append",
                "insert_position": 0,
                "max_append_chars": 300,
            }

        last_para = paragraphs[-1]
        last_para_len = len(last_para)

        # Find position of last paragraph
        insert_position = len(text)
        for i in range(len(text) - 1, -1, -1):
            if text[i:].strip() == last_para.strip():
                insert_position = i
                break

        # Decision logic:
        # - If last paragraph is very short (< 30 chars), rewrite it
        # - If last paragraph ends with a clear sentence ending, append after it
        # - If last paragraph is mid-action, rewrite last two paragraphs
        if last_para_len < 30:
            return {
                "action": "rewrite_last",
                "insert_position": insert_position,
                "max_append_chars": 200,
            }

        # Check if last paragraph ends cleanly
        last_para_stripped = last_para.rstrip()
        if last_para_stripped and last_para_stripped[-1] in "。！？…—":
            # Clean ending - append
            return {
                "action": "append",
                "insert_position": insert_position + last_para_len,
                "max_append_chars": 300,
            }

        # Check if last paragraph seems mid-action (ends with comma or no punctuation)
        if last_para_stripped and last_para_stripped[-1] in "，、；":
            if len(paragraphs) >= 2:
                return {
                    "action": "rewrite_last_two",
                    "insert_position": insert_position,
                    "max_append_chars": 400,
                }
            return {
                "action": "rewrite_last",
                "insert_position": insert_position,
                "max_append_chars": 300,
            }

        # Default: append
        return {
            "action": "append",
            "insert_position": insert_position + last_para_len,
            "max_append_chars": 300,
        }

    def ending_bridge_planner(self, missing_states: list[str]) -> list[dict]:
        """生成补写计划：动作 -> 认知 -> 决定/余波。

        Args:
            missing_states: 缺失的结尾状态列表

        Returns:
            [{
                "state": str,
                "bridge_type": "action"|"cognition"|"decision"|"aftermath",
                "suggested_text_length": int,
            }]
        """
        plan: list[dict] = []

        for state in missing_states:
            bridge_type = self._classify_state_type(state)
            suggested_length = self._estimate_bridge_length(bridge_type)

            plan.append({
                "state": state,
                "bridge_type": bridge_type,
                "suggested_text_length": suggested_length,
            })

        # Sort by bridge_type order: action -> cognition -> decision -> aftermath
        type_order = {"action": 0, "cognition": 1, "decision": 2, "aftermath": 3}
        plan.sort(key=lambda p: type_order.get(p["bridge_type"], 99))

        return plan

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_single_state(
        self,
        state: str,
        full_text: str,
        tail_text: str,
        tail_start: int,
    ) -> dict:
        """检查单个结尾状态是否在文本中出现。

        Returns:
            {"status": "fulfilled"|"partial"|"missing", "evidence": str, "position": int, "note": str}
        """
        # Extract key terms from the state description
        key_terms = self._extract_key_terms(state)

        if not key_terms:
            return {"status": "missing", "evidence": "", "position": -1}

        # Search in tail text first (most relevant)
        best_match = None
        best_position = -1

        for term in key_terms:
            # Search in tail text
            pos = tail_text.find(term)
            if pos >= 0:
                actual_pos = tail_start + pos
                # Get surrounding context as evidence
                context_start = max(0, pos - 20)
                context_end = min(len(tail_text), pos + len(term) + 20)
                evidence = tail_text[context_start:context_end]

                if best_match is None or pos < best_position:
                    best_match = evidence
                    best_position = actual_pos

        if best_match is not None:
            # Check if it's an abstract expression
            is_abstract = False
            for pattern in self._ABSTRACT_PATTERNS:
                if re.search(pattern, best_match):
                    is_abstract = True
                    break

            if is_abstract:
                return {
                    "status": "partial",
                    "evidence": best_match,
                    "position": best_position,
                    "note": "仅有抽象表达，缺少可见动作",
                }
            return {
                "status": "fulfilled",
                "evidence": best_match,
                "position": best_position,
            }

        # Also check full text (state might be set up earlier)
        for term in key_terms:
            pos = full_text.find(term)
            if pos >= 0:
                context_start = max(0, pos - 20)
                context_end = min(len(full_text), pos + len(term) + 20)
                evidence = full_text[context_start:context_end]
                return {
                    "status": "partial",
                    "evidence": evidence,
                    "position": pos,
                    "note": "状态在正文前部出现，但结尾未呼应",
                }

        return {"status": "missing", "evidence": "", "position": -1}

    @staticmethod
    def _extract_key_terms(state: str) -> list[str]:
        """从状态描述中提取关键术语。"""
        # Remove common prefixes/suffixes
        cleaned = state
        for prefix in ["主角", "主人公", "她", "他", "角色"]:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]

        # Split by common conjunctions
        terms = re.split(r"[，、和与以及或者]", cleaned)
        result = []
        for term in terms:
            term = term.strip()
            if len(term) >= 2:
                result.append(term)

        # Also add the full state as a search term
        if len(state) >= 2:
            result.append(state)

        return result

    @staticmethod
    def _classify_state_type(state: str) -> str:
        """将状态描述分类为动作/认知/决定/余波。"""
        action_keywords = ["做", "行动", "拿", "走", "跑", "打开", "关上", "离开", "到达"]
        cognition_keywords = ["明白", "理解", "意识到", "发现", "看清", "知道", "懂"]
        decision_keywords = ["决定", "选择", "下定决心", "不再", "拒绝", "接受"]
        aftermath_keywords = ["之后", "后来", "结果", "最终", "结局", "余波"]

        for kw in action_keywords:
            if kw in state:
                return "action"
        for kw in decision_keywords:
            if kw in state:
                return "decision"
        for kw in cognition_keywords:
            if kw in state:
                return "cognition"
        for kw in aftermath_keywords:
            if kw in state:
                return "aftermath"

        # Default to action (most concrete)
        return "action"

    @staticmethod
    def _estimate_bridge_length(bridge_type: str) -> int:
        """根据桥接类型估计建议文本长度。"""
        lengths = {
            "action": 60,
            "cognition": 40,
            "decision": 50,
            "aftermath": 80,
        }
        return lengths.get(bridge_type, 60)
