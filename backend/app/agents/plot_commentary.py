from __future__ import annotations

import json

from app.agents.base import BaseAgent


class PlotCommentaryAgent(BaseAgent):
    name = "plot_commentator"

    async def execute(self, context: dict) -> dict:
        project_id = context.get("project_id", "")
        chapter_number = context.get("chapter_number")
        chapter_content = context.get("chapter_content", "")
        outline = context.get("outline", {})
        story_state = context.get("story_state", {})
        core_facts = context.get("core_facts", {})
        foreshadowing = context.get("foreshadowing", [])

        system_prompt = (
            "你是「万象谱」情节评论家，一位资深文学评论家和叙事结构分析师。"
            "你的任务是对小说章节进行深度分析，生成专业的情节评论报告。\n\n"
            "分析维度：\n"
            "1. **叙事节奏**：场景推进速度是否合理，张弛有度还是单调乏味\n"
            "2. **人物弧光**：角色是否有心理转变，动机是否可信\n"
            "3. **伏笔与呼应**：是否有效埋设/回收伏笔，有无遗漏\n"
            "4. **冲突设计**：核心冲突是否清晰，对抗力量是否对等\n"
            "5. **信息释放**：读者知情权与悬念的平衡是否得当\n"
            "6. **情感曲线**：读者情绪是否被有效引导，高潮是否有力\n"
            "7. **视角运用**：POV选择是否最佳，信息差是否利用得当\n"
            "8. **文字质量**：描写是否生动，对话是否自然，有无冗余\n\n"
            "输出 JSON 格式：\n"
            "- overall_score: 1-10 总体评分\n"
            "- dimensions: 各维度评分和评语\n"
            "- highlights: 亮点列表\n"
            "- issues: 问题列表（含严重程度和建议）\n"
            "- foreshadowing_analysis: 伏笔分析\n"
            "- suggestions: 改进建议\n"
            "- emotional_arc: 情感曲线描述"
        )

        chapter_info = f"第{chapter_number}章" if chapter_number else "整部作品"
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

        foreshadowing_info = ""
        if foreshadowing:
            foreshadowing_info = "\n\n已知伏笔：\n"
            for f in foreshadowing[:10]:
                foreshadowing_info += f"- {f.get('name', '')}: {f.get('description', '')} (状态: {f.get('status', '')})\n"

        structured_foreshadowing = await self._build_structured_foreshadowing(
            project_id, chapter_number, db,
        )
        if structured_foreshadowing:
            foreshadowing_info += "\n结构化伏笔数据（来自伏笔管理系统）：\n"
            foreshadowing_info += structured_foreshadowing

        user_prompt = (
            f"项目ID: {project_id}\n"
            f"分析范围: {chapter_info}\n\n"
            f"章节内容:\n{chapter_content[:6000]}\n\n"
            f"大纲信息:\n{outline_summary[:2000]}\n\n"
            f"当前状态:\n{json.dumps(story_state, ensure_ascii=False)[:1500]}\n\n"
            f"核心事实:\n{json.dumps(core_facts, ensure_ascii=False)[:1500]}\n"
            f"{foreshadowing_info}\n\n"
            f"请生成深度情节评论报告："
        )

        llm = await self.get_llm_client()
        response = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=4096,
        )

        try:
            report = json.loads(response)
            if not isinstance(report, dict):
                report = self._default_report(chapter_info)
        except json.JSONDecodeError:
            report = self._default_report(chapter_info)
            report["raw_response"] = response

        return {"report": report, "chapter_number": chapter_number}

    def _default_report(self, scope: str) -> dict:
        return {
            "overall_score": 0,
            "dimensions": {
                "narrative_rhythm": {"score": 0, "comment": "分析生成失败"},
                "character_arc": {"score": 0, "comment": "分析生成失败"},
                "foreshadowing": {"score": 0, "comment": "分析生成失败"},
                "conflict_design": {"score": 0, "comment": "分析生成失败"},
                "information_release": {"score": 0, "comment": "分析生成失败"},
                "emotional_curve": {"score": 0, "comment": "分析生成失败"},
                "perspective": {"score": 0, "comment": "分析生成失败"},
                "writing_quality": {"score": 0, "comment": "分析生成失败"},
            },
            "highlights": [],
            "issues": [],
            "foreshadowing_analysis": "分析生成失败",
            "suggestions": [],
            "emotional_arc": "分析生成失败",
            "scope": scope,
        }

    async def _build_structured_foreshadowing(
        self, project_id, chapter_number, db,
    ) -> str:
        if not project_id or not db:
            return ""

        from app.services.foreshadowing_service import ForeshadowingService
        from app.services.foreshadowing_clue_service import ForeshadowingClueService

        fs_service = ForeshadowingService()
        clue_service = ForeshadowingClueService()

        try:
            pid = project_id
            if isinstance(pid, str):
                import uuid
                pid = uuid.UUID(pid)
        except (ValueError, TypeError):
            return ""

        try:
            lines = await fs_service.list_foreshadowing_lines(pid, db)
        except Exception:
            return ""

        if not lines:
            return ""

        parts = []
        for line in lines[:15]:
            status = line.get("status", "")
            name = line.get("name", "")
            parts.append(f"【{name}】状态={status}")

            if chapter_number:
                try:
                    states = await fs_service.list_cognitive_states_for_foreshadowing(
                        pid, line["id"], db, chapter_number=chapter_number,
                    )
                    if states:
                        level_map = {}
                        for s in states:
                            level = s.get("cognitive_level", "unknown")
                            level_map[level] = level_map.get(level, 0) + 1
                        parts.append(f"  角色认知分布: {dict(level_map)}")
                except Exception:
                    pass

            try:
                pool = await clue_service.get_evidence_pool(line["id"], db)
                supp = pool.get("supportive", 0)
                dist = pool.get("distractive", 0)
                contra = pool.get("contradictory", 0)
                if supp + dist + contra > 0:
                    parts.append(f"  证据池: 支持={supp} 干扰={dist} 矛盾={contra}")
            except Exception:
                pass

            readiness = line.get("reveal_readiness_score")
            if readiness is not None:
                parts.append(f"  揭示准备度: {readiness:.2f}")

        return "\n".join(parts)
