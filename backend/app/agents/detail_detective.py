from __future__ import annotations

import json

from app.agents.base import BaseAgent


class DetailDetectiveAgent(BaseAgent):
    name = "detail_detective"

    async def execute(self, context: dict) -> dict:
        project_id = context.get("project_id", "")
        chapter_number = context.get("chapter_number")
        chapter_content = context.get("chapter_content", "")
        core_facts = context.get("core_facts", {})
        story_state = context.get("story_state", {})
        shell_seeds = context.get("shell_seeds", [])
        scan_scope = context.get("scan_scope", "chapter")

        system_prompt = (
            "你是「万象谱」细节侦探，一位以显微镜般精确度审查叙事细节的专家。"
            "你的任务是回溯 Shell 层的细节种子，发现文本中的微观矛盾和逻辑漏洞。\n\n"
            "检查维度：\n"
            "1. **时间线矛盾**：事件发生顺序是否自洽，时间跨度是否合理\n"
            "2. **空间逻辑**：角色移动是否可能，场景转换是否连贯\n"
            "3. **物理细节**：天气、光照、物品位置等是否前后一致\n"
            "4. **角色行为**：角色的反应是否符合已建立的性格和动机\n"
            "5. **对话一致性**：角色说话方式是否前后统一\n"
            "6. **称谓一致性**：人名、地名、组织名等是否统一\n"
            "7. **数字逻辑**：年龄、距离、数量等数字是否自洽\n"
            "8. **因果链**：事件之间的因果关系是否成立\n\n"
            "输出 JSON 格式：\n"
            "- total_issues: 发现的问题总数\n"
            "- severity_breakdown: {critical: n, major: n, minor: n, suggestion: n}\n"
            "- issues: 问题列表，每项包含：\n"
            "  - category: 问题类别\n"
            "  - severity: 严重程度(critical/major/minor/suggestion)\n"
            "  - description: 问题描述\n"
            "  - evidence: 具体证据（引用原文）\n"
            "  - core_fact_violated: 违反的核心事实（如有）\n"
            "  - suggestion: 修正建议\n"
            "- consistency_score: 一致性评分(1-100)\n"
            "- summary: 总体评估"
        )

        scope_desc = f"第{chapter_number}章" if chapter_number and scan_scope == "chapter" else "全本"

        shell_info = ""
        if shell_seeds:
            shell_info = "\n\nShell层细节种子：\n"
            for seed in shell_seeds[:20]:
                shell_info += f"- [{seed.get('tier', '')}] {seed.get('fact', '')[:100]}\n"

        user_prompt = (
            f"项目ID: {project_id}\n"
            f"扫描范围: {scope_desc}\n\n"
            f"待检查文本:\n{chapter_content[:6000]}\n\n"
            f"核心事实:\n{json.dumps(core_facts, ensure_ascii=False)[:2000]}\n\n"
            f"当前状态:\n{json.dumps(story_state, ensure_ascii=False)[:1500]}\n"
            f"{shell_info}\n\n"
            f"请进行微观矛盾排查："
        )

        llm = await self.get_llm_client()
        response = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=4096,
        )

        try:
            report = json.loads(response)
            if not isinstance(report, dict):
                report = self._default_report()
        except json.JSONDecodeError:
            report = self._default_report()
            report["raw_response"] = response

        return {"report": report, "chapter_number": chapter_number, "scan_scope": scan_scope}

    def _default_report(self) -> dict:
        return {
            "total_issues": 0,
            "severity_breakdown": {"critical": 0, "major": 0, "minor": 0, "suggestion": 0},
            "issues": [],
            "consistency_score": 0,
            "summary": "分析生成失败，请重试",
        }
