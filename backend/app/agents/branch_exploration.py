from __future__ import annotations

import json

from app.agents.base import BaseAgent


class BranchExplorationAgent(BaseAgent):
    name = "branch_explorer"

    async def execute(self, context: dict) -> dict:
        project_id = context.get("project_id", "")
        chapter_number = context.get("chapter_number", 1)
        branch_point = context.get("branch_point", "")
        current_content = context.get("current_content", "")
        outline = context.get("outline", {})
        story_state = context.get("story_state", {})
        core_facts = context.get("core_facts", {})
        branch_count = context.get("branch_count", 3)
        branch_count = max(2, min(5, branch_count))

        system_prompt = (
            "你是「万象谱」分支探索员，一位擅长推演平行剧情的叙事架构师。"
            "你的任务是在给定的分支点，生成多个逻辑自洽但走向不同的平行分支。\n\n"
            "分支生成原则：\n"
            "1. 每个分支必须从同一分支点出发，但做出不同的关键选择\n"
            "2. 分支之间应有明显差异，避免微调式变化\n"
            "3. 每个分支需保持与核心事实的一致性\n"
            "4. 至少一个分支应包含惊喜元素——读者预料之外但逻辑之内\n"
            "5. 每个分支需评估：惊喜感(1-10)、一致性(1-10)、叙事潜力(1-10)\n\n"
            "输出 JSON 格式：\n"
            "- branch_point: 分支点描述\n"
            "- branches: 分支列表，每项包含：\n"
            "  - name: 分支名称\n"
            "  - key_decision: 关键决策/转折\n"
            "  - summary: 分支剧情概述(200-300字)\n"
            "  - character_impact: 对主要角色的影响\n"
            "  - surprise_score: 惊喜感评分(1-10)\n"
            "  - consistency_score: 一致性评分(1-10)\n"
            "  - narrative_potential: 叙事潜力评分(1-10)\n"
            "  - foreshadowing_opportunities: 可埋设的伏笔\n"
            "  - risks: 潜在风险\n"
            "- recommendation: 推荐分支及理由"
        )

        outline_summary = ""
        if outline:
            chapters = outline.get("chapters", [])
            if chapter_number:
                for ch in chapters:
                    if ch.get("chapter_number") == chapter_number:
                        outline_summary = json.dumps(ch, ensure_ascii=False, indent=2)
                        break
            if not outline_summary:
                outline_summary = json.dumps(outline, ensure_ascii=False)[:500]

        user_prompt = (
            f"项目ID: {project_id}\n"
            f"当前章节: 第{chapter_number}章\n"
            f"分支点: {branch_point or f'第{chapter_number}章末尾'}\n\n"
            f"当前内容:\n{current_content[:4000]}\n\n"
            f"大纲信息:\n{outline_summary[:2000]}\n\n"
            f"当前状态:\n{json.dumps(story_state, ensure_ascii=False)[:1500]}\n\n"
            f"核心事实:\n{json.dumps(core_facts, ensure_ascii=False)[:1500]}\n\n"
            f"请生成 {branch_count} 个平行分支："
        )

        llm = await self.get_llm_client()
        response = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8,
            max_tokens=4096,
        )

        try:
            result = json.loads(response)
            if not isinstance(result, dict):
                result = self._default_result(branch_point, branch_count)
        except json.JSONDecodeError:
            result = self._default_result(branch_point, branch_count)
            result["raw_response"] = response

        return {"branches": result, "chapter_number": chapter_number}

    def _default_result(self, branch_point: str, count: int) -> dict:
        return {
            "branch_point": branch_point,
            "branches": [
                {
                    "name": f"分支{i+1}",
                    "key_decision": "生成失败",
                    "summary": "分支生成失败，请重试",
                    "character_impact": {},
                    "surprise_score": 0,
                    "consistency_score": 0,
                    "narrative_potential": 0,
                    "foreshadowing_opportunities": [],
                    "risks": [],
                }
                for i in range(count)
            ],
            "recommendation": "生成失败，请重试",
        }
