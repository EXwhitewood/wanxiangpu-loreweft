"""方案 30：语义判定 LLM agent。

被 LlmSemanticChecker 调用，对 B 类语义指标做 LLM 精判。
判定与修复共享 evidence（文本片段），消除"判定用列表 A、修复用列表 B"的错配。

核心职责：
- 接收场景文本 + 待判定的 metric 列表 + scene_contract
- 调用 LLM（temperature=0）做语义判定
- 返回结构化结果：每项 metric 的 passed / score / evidence / issues

复用 LLMGateway 基础设施，遵循项目 LLM 调用规范：
- task_type=FBI_REPAIR（max_tokens=65536, timeout=120s）
- reasoning_content 字段由 LLMClient 内部处理
- 失败时返回结构化错误，不抛异常
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.base import BaseAgent
from app.services.llm_gateway import LLMGatewayResult, get_llm_gateway
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)


# B 类 metric 的判定标准描述（供 prompt 使用）
_METRIC_CRITERIA: dict[str, dict[str, str]] = {
    "low_conflict_density": {
        "name": "冲突密度",
        "standard": (
            "场景是否有足够的压力升级和对抗。\n"
            "- 识别所有冲突元素（直接对抗、威胁、压力、时限、代价等）\n"
            "- 评估冲突是否推动情节推进\n"
            "- 注意：题材词（如玄幻的'魔气/杀意/剑气'、都市的'对峙/枪/挟持'）应被识别为冲突元素\n"
            "- 非直接对抗的隐性冲突（如权力博弈、利益冲突）也算冲突"
        ),
    },
    "weak_curiosity_engine": {
        "name": "悬念驱动",
        "standard": (
            "场景是否有勾住读者的未解问题。\n"
            "- 识别所有悬念钩子（疑问句、唯一性钩子、时限钩子、代价钩子、身份钩子、承诺钩子等）\n"
            "- 非疑问句式的钩子应被识别：\n"
            "  * 唯一性钩子：'她只有这一次炼丹的机会'\n"
            "  * 时限钩子：'天亮前必须离开'、'再无退路'\n"
            "  * 代价钩子：'她将失去最后的庇护'\n"
            "  * 身份钩子：'她不知道自己是谁的孩子'\n"
            "  * 承诺钩子：'她还欠那人一个答案'\n"
            "- 评估钩子是否能驱动读者继续阅读"
        ),
    },
    "weak_opening_hook": {
        "name": "开头钩子",
        "standard": (
            "场景开头（前 500 字）是否有钩住读者的元素。\n"
            "- 识别开头 500 字内的钩子（疑问、未解问题、即将到来的危机、悬念、冲突）\n"
            "- 非疑问句式的钩子有效（见悬念驱动指标）\n"
            "- 评估开头是否能吸引读者继续阅读"
        ),
    },
    "weak_chapter_end_hook": {
        "name": "章末钩子",
        "standard": (
            "场景结尾（后 500 字）是否有钩住读者的元素。\n"
            "- 识别结尾 500 字内的钩子（悬念、伏笔、下一步行动、未解问题）\n"
            "- 非疑问句式的钩子有效\n"
            "- 评估结尾是否能驱动读者翻到下一章"
        ),
    },
    "abstraction_over_budget": {
        "name": "抽象解释",
        "standard": (
            "场景是否有未具象化的抽象论断。\n"
            "- 识别抽象论断（没有具体证据支撑的抽象陈述）\n"
            "- 含具体动作/感官描写的句子不算抽象：\n"
            "  * '她攥紧袖口，感到恐惧' 不算（有具体动作'攥紧袖口'）\n"
            "  * '门外的脚步声仿佛越来越近' 不算（听觉感官描写）\n"
            "- '仿佛/似乎'用于感官描写时允许，用于抽象比喻时才算抽象\n"
            "- 世界观设定的必要抽象（如'修炼到金丹期'）允许"
        ),
    },
    "standalone_abstract_claims": {
        "name": "裸抽象论断",
        "standard": (
            "场景是否有'裸'的抽象论断（既无具体证据，也无上下文支撑）。\n"
            "- 句子同时含抽象词且不含具体词/感官词/对话才算裸抽象\n"
            "- 含具体动作、感官、对话的句子不算\n"
            "- 评估是否有'她心里五味杂陈'这种纯抽象表达"
        ),
    },
    "missing_specific_detail_anchor": {
        "name": "具象化锚点",
        "standard": (
            "场景是否缺少具体的细节锚点。\n"
            "- 识别是否有具体的感官描写、动作描写、物品描写\n"
            "- 抽象论断附近是否有具象化锚点支撑\n"
            "- 评估场景的'画面感'是否足够"
        ),
    },
    "voice_phrase_drift": {
        "name": "声线偏移",
        "standard": (
            "场景是否有角色声线偏移。\n"
            "- 识别角色的语言风格是否前后一致\n"
            "- 识别是否有'出戏'的表达（如古代角色说现代话）\n"
            "- 评估对话和内心独白是否符合角色设定"
        ),
    },
}


class LlmSemanticJudgeAgent(BaseAgent):
    """方案 30：语义判定 LLM agent。

    被 LlmSemanticChecker 调用，对 B 类语义指标做 LLM 精判。
    判定产出的 evidence 直接供 FBI 修复使用，消除词表错配。
    """

    name = "llm_semantic_judge"

    async def execute(self, context: dict) -> dict:
        """BaseAgent 抽象方法实现，委托给 judge_metrics。"""
        return await self.judge_metrics(
            text=context.get("text", ""),
            metrics_to_judge=context.get("metrics_to_judge", []),
            scene_contract=context.get("scene_contract", {}),
        )

    async def judge_metrics(
        self,
        text: str,
        metrics_to_judge: list[str],
        scene_contract: dict | None = None,
    ) -> dict[str, Any]:
        """对多个 B 类 metric 做 LLM 语义判定。

        Args:
            text: 场景正文
            metrics_to_judge: 待判定的 metric 名称列表
            scene_contract: 场景合同（含 commercial_pacing_contract 等）

        Returns:
            {
                "metrics": {
                    "low_conflict_density": {
                        "passed": bool,
                        "score": float,  # 0-10
                        "evidence": [{"span": "原文片段", "role": "角色说明"}],
                        "issues": [{"type": "issue_type", "detail": "问题描述"}]
                    },
                    ...
                },
                "degraded": bool,  # LLM 失败时为 True
                "error": str | None,
            }
        """
        scene_contract = scene_contract or {}

        if not text or not metrics_to_judge:
            return {"metrics": {}, "degraded": False, "error": None}

        # 过滤出有判定标准的 metric
        valid_metrics = [m for m in metrics_to_judge if m in _METRIC_CRITERIA]
        if not valid_metrics:
            return {"metrics": {}, "degraded": False, "error": None}

        # 构建 prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(text, valid_metrics, scene_contract)

        # 调用 LLM
        gateway = get_llm_gateway()
        result: LLMGatewayResult = await gateway.generate_json(
            prompt=user_prompt,
            system=system_prompt,
            temperature=0,  # 方案 30：保证判定确定性
            task_type=LLMTaskType.FBI_REPAIR,  # 复用 FBI 预设：max_tokens=65536, timeout=120s
        )

        if not result.ok or not result.parsed_json:
            logger.warning(
                "LlmSemanticJudgeAgent LLM 调用失败: error_type=%s, message=%s",
                result.error_type,
                result.error_message,
            )
            return {
                "metrics": {},
                "degraded": True,
                "error": result.error_message or "llm_failed",
            }

        # 解析 LLM 输出
        return self._parse_llm_output(result.parsed_json, valid_metrics)

    async def judge_single_metric(
        self,
        metric: str,
        text: str,
        scene_contract: dict | None = None,
    ) -> dict[str, Any]:
        """对单个 metric 做 LLM 判定（供修复侧后置校验使用）。

        Returns:
            {"passed": bool, "score": float, "evidence": [...], "issues": [...]}
        """
        result = await self.judge_metrics(text, [metric], scene_contract)
        if result.get("degraded"):
            return {
                "passed": True,  # LLM 失败时不阻断，降级为通过
                "score": 0.0,
                "evidence": [],
                "issues": [],
                "reason": "llm_degraded",
            }
        metric_result = result.get("metrics", {}).get(metric, {})
        return {
            "passed": metric_result.get("passed", True),
            "score": metric_result.get("score", 0.0),
            "evidence": metric_result.get("evidence", []),
            "issues": metric_result.get("issues", []),
        }

    def _build_system_prompt(self) -> str:
        """构建 system prompt。"""
        return (
            "你是一位资深网文编辑，负责判定场景文本的语义质量。\n\n"
            "## 判定原则\n"
            "1. 必须基于文本实际内容判定，不能凭空假设\n"
            "2. evidence 必须是从原文截取的真实片段（逐字引用，不能改写）\n"
            "3. 题材词应被正确识别（玄幻的'魔气/杀意/剑气'是冲突元素）\n"
            "4. 非疑问句式的钩子应被识别（'她只有这一次机会'是有效钩子）\n"
            "5. 含具体动作/感官描写的句子不算抽象\n\n"
            "## 输出格式\n"
            "返回严格的 JSON，结构如下：\n"
            "{\n"
            '  "metrics": {\n'
            '    "<metric_name>": {\n'
            '      "passed": true/false,\n'
            '      "score": 0-10的数字,\n'
            '      "evidence": [{"span": "原文片段", "role": "角色说明"}],\n'
            '      "issues": [{"type": "issue_type", "detail": "问题描述"}]\n'
            "    }\n"
            "  }\n"
            "}\n\n"
            "注意：evidence 的 span 必须是原文的逐字引用，不能改写或概括。"
        )

    def _build_user_prompt(
        self,
        text: str,
        metrics_to_judge: list[str],
        scene_contract: dict,
    ) -> str:
        """构建 user prompt。"""
        # 提取场景合同信息
        pacing_contract = scene_contract.get("commercial_pacing_contract", {}) or {}
        scene_position = pacing_contract.get("scene_position", "未知")
        conflict_driver = pacing_contract.get("conflict_driver", "未指定")
        reader_hook = pacing_contract.get("reader_hook", "未指定")
        chapter_end_hook = pacing_contract.get("chapter_end_hook", "未指定")

        # 构建 metric 判定标准说明
        metric_sections = []
        for idx, metric in enumerate(metrics_to_judge, 1):
            criteria = _METRIC_CRITERIA.get(metric, {})
            name = criteria.get("name", metric)
            standard = criteria.get("standard", "未定义判定标准")
            metric_sections.append(
                f"### 指标 {idx}：{name}（{metric}）\n"
                f"判定标准：\n{standard}"
            )

        metrics_text = "\n\n".join(metric_sections)

        return (
            f"## 场景文本\n{text}\n\n"
            f"## 场景合同\n"
            f"- 场景位置：{scene_position}\n"
            f"- 冲突驱动：{conflict_driver}\n"
            f"- 读者钩子：{reader_hook}\n"
            f"- 章末钩子：{chapter_end_hook}\n\n"
            f"## 待判定指标\n\n"
            f"{metrics_text}\n\n"
            f"## 输出要求\n"
            f"请逐项判定上述 {len(metrics_to_judge)} 个指标，每项必须：\n"
            f"1. 给出 passed (bool) 和 score (0-10)\n"
            f"2. 引用文本具体片段作为 evidence（必须是从原文截取的真实片段）\n"
            f"3. 如不通过，给出具体的 issues（指明问题类型和位置）\n\n"
            f"请以 JSON 格式输出判定结果。"
        )

    def _parse_llm_output(
        self,
        parsed_json: dict | list,
        valid_metrics: list[str],
    ) -> dict[str, Any]:
        """解析 LLM 输出，规范化结果结构。"""
        metrics_result: dict[str, Any] = {}

        # LLM 输出应该是 {"metrics": {...}} 结构
        raw_metrics = {}
        if isinstance(parsed_json, dict):
            raw_metrics = parsed_json.get("metrics", parsed_json)
        elif isinstance(parsed_json, list):
            # 兼容 list 格式
            for item in parsed_json:
                if isinstance(item, dict) and "metric" in item:
                    raw_metrics[item["metric"]] = item

        for metric in valid_metrics:
            metric_data = raw_metrics.get(metric, {})
            if not isinstance(metric_data, dict):
                metric_data = {}

            # 规范化字段
            passed = bool(metric_data.get("passed", True))
            score = float(metric_data.get("score", 0.0))
            # score 限制在 0-10
            score = max(0.0, min(10.0, score))

            evidence = metric_data.get("evidence", [])
            if not isinstance(evidence, list):
                evidence = []
            # 规范化 evidence 项
            normalized_evidence = []
            for ev in evidence:
                if isinstance(ev, dict):
                    normalized_evidence.append({
                        "span": str(ev.get("span", "")),
                        "role": str(ev.get("role", "")),
                    })
                elif isinstance(ev, str):
                    normalized_evidence.append({"span": ev, "role": ""})

            issues = metric_data.get("issues", [])
            if not isinstance(issues, list):
                issues = []
            # 规范化 issues 项
            normalized_issues = []
            for issue in issues:
                if isinstance(issue, dict):
                    normalized_issues.append({
                        "type": str(issue.get("type", "unknown")),
                        "detail": str(issue.get("detail", "")),
                    })
                elif isinstance(issue, str):
                    normalized_issues.append({"type": "unknown", "detail": issue})

            metrics_result[metric] = {
                "passed": passed,
                "score": score,
                "evidence": normalized_evidence,
                "issues": normalized_issues,
            }

        return {
            "metrics": metrics_result,
            "degraded": False,
            "error": None,
        }
