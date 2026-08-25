import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.story_plan_service import StoryPlanService
from app.services.thread_plan_service import ThreadPlanService
from app.services.chapter_spine_service import ChapterSpineService
from app.services.text_coercion import to_search_text

logger = logging.getLogger(__name__)


class AuditFinding:
    def __init__(self, severity: str, scope: str, message: str, suggestion: str = ""):
        self.severity = severity
        self.scope = scope
        self.message = message
        self.suggestion = suggestion

    def to_dict(self):
        return {
            "severity": self.severity,
            "scope": self.scope,
            "message": self.message,
            "suggestion": self.suggestion,
        }


class AuditReport:
    def __init__(self, auditor_name: str):
        self.auditor = auditor_name
        self.verdict = "pass"
        self.findings: list[AuditFinding] = []
        self.summary = ""

    def add_finding(self, finding: AuditFinding):
        self.findings.append(finding)
        if finding.severity == "error" and self.verdict != "blocking":
            self.verdict = "blocking"
        elif finding.severity == "warning" and self.verdict == "pass":
            self.verdict = "warning"

    def to_dict(self):
        return {
            "auditor": self.auditor,
            "verdict": self.verdict,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
        }


class StoryPlanAuditService:

    def __init__(self):
        self._plan_service = StoryPlanService()
        self._thread_service = ThreadPlanService()
        self._spine_service = ChapterSpineService()

    async def full_audit(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        plan = await self._plan_service.get_story_plan(project_id, db)
        if "error" in plan:
            return {"error": plan["error"]}

        reports = {}

        reports["rule_validation"] = (await self._rule_validation(plan)).to_dict()
        reports["thread_graph"] = (await self._thread_graph_audit(project_id, plan, db)).to_dict()
        reports["dependency_graph"] = (await self._dependency_graph_audit(plan)).to_dict()

        overall_verdict = "pass"
        for r in reports.values():
            if r.get("verdict") == "blocking":
                overall_verdict = "blocking"
                break
            if r.get("verdict") == "warning" and overall_verdict == "pass":
                overall_verdict = "warning"

        return {
            "overall_verdict": overall_verdict,
            "reports": reports,
            "audited_at": datetime.now(timezone.utc).isoformat(),
        }

    async def rule_validation_only(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        plan = await self._plan_service.get_story_plan(project_id, db)
        if "error" in plan:
            return {"error": plan["error"]}
        return (await self._rule_validation(plan)).to_dict()

    async def thread_audit_only(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        plan = await self._plan_service.get_story_plan(project_id, db)
        if "error" in plan:
            return {"error": plan["error"]}
        return (await self._thread_graph_audit(project_id, plan, db)).to_dict()

    async def dependency_audit_only(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> dict:
        plan = await self._plan_service.get_story_plan(project_id, db)
        if "error" in plan:
            return {"error": plan["error"]}
        return (await self._dependency_graph_audit(plan)).to_dict()

    async def _rule_validation(self, plan: dict) -> AuditReport:
        report = AuditReport("rule_validator")
        spine = plan.get("chapter_spine", [])

        if not spine:
            report.add_finding(AuditFinding(
                "warning", "chapter_spine", "章节脊柱为空，无法进行规则校验"
            ))
            report.summary = "章节脊柱为空"
            return report

        chapter_numbers = []
        for ch in spine:
            ch_num = ch.get("chapter_number", 0)
            chapter_numbers.append(ch_num)

            conflict_text = self._plan_service.get_conflict_text(ch)
            if not conflict_text:
                report.add_finding(AuditFinding(
                    "warning",
                    f"chapter:{ch_num}",
                    f"第{ch_num}章缺少核心冲突描述",
                    "建议补充 conflict_text 或 core_conflict 四元组",
                ))

            core_conflict = ch.get("core_conflict")
            if isinstance(core_conflict, dict):
                for field in ["desire", "obstacle", "action", "turn"]:
                    if not core_conflict.get(field):
                        report.add_finding(AuditFinding(
                            "info",
                            f"chapter:{ch_num}",
                            f"第{ch_num}章 core_conflict.{field} 为空",
                        ))

            value_shift = ch.get("value_shift")
            if isinstance(value_shift, dict):
                from_v = value_shift.get("from", "")
                to_v = value_shift.get("to", "")
                if from_v and to_v and from_v == to_v:
                    report.add_finding(AuditFinding(
                        "warning",
                        f"chapter:{ch_num}",
                        f"第{ch_num}章价值转变 from 和 to 相同：{from_v}",
                        "价值转变应有方向性变化",
                    ))
                elif not from_v and not to_v:
                    report.add_finding(AuditFinding(
                        "info",
                        f"chapter:{ch_num}",
                        f"第{ch_num}章缺少价值转变",
                    ))

            hook = ch.get("hook", "")
            if not hook:
                report.add_finding(AuditFinding(
                    "info",
                    f"chapter:{ch_num}",
                    f"第{ch_num}章缺少钩子",
                ))

            pov = ch.get("pov_character", "")
            if not pov:
                report.add_finding(AuditFinding(
                    "info",
                    f"chapter:{ch_num}",
                    f"第{ch_num}章缺少POV角色",
                ))

        sorted_nums = sorted(chapter_numbers)
        for i in range(1, len(sorted_nums)):
            if sorted_nums[i] - sorted_nums[i - 1] > 1:
                report.add_finding(AuditFinding(
                    "error",
                    "chapter_spine",
                    f"章节号不连续：{sorted_nums[i - 1]} 和 {sorted_nums[i]} 之间有缺口",
                    "建议补充缺失章节或调整章节号",
                ))

        consecutive_no_hook = 0
        for ch in sorted(spine, key=lambda x: x.get("chapter_number", 0)):
            if not ch.get("hook", ""):
                consecutive_no_hook += 1
                if consecutive_no_hook >= 3:
                    report.add_finding(AuditFinding(
                        "warning",
                        f"chapter:{ch.get('chapter_number')}",
                        f"连续{consecutive_no_hook}章无钩子",
                        "建议在章节末尾添加钩子保持读者兴趣",
                    ))
            else:
                consecutive_no_hook = 0

        constitution = plan.get("story_constitution")
        if constitution and isinstance(constitution, dict):
            if not constitution.get("logline"):
                report.add_finding(AuditFinding(
                    "info", "story_constitution", "故事宪法缺少一句话梗概"
                ))
            if not constitution.get("controlling_idea"):
                report.add_finding(AuditFinding(
                    "info", "story_constitution", "故事宪法缺少主题论证"
                ))

        thread_plan = plan.get("thread_plan")
        if thread_plan and isinstance(thread_plan, dict):
            threads = thread_plan.get("threads", [])
            for t in threads:
                tid = t.get("thread_id", t.get("name", ""))
                status = t.get("status", "")
                if status in ("active", "planned"):
                    plant = t.get("plant_chapters", [])
                    reveal = t.get("reveal_chapters", [])
                    if plant and not reveal:
                        report.add_finding(AuditFinding(
                            "warning",
                            f"thread:{tid}",
                            f"线索「{t.get('name', tid)}」已埋设但无回收计划",
                            "建议规划回收章节或标记为废弃",
                        ))

        if not report.findings:
            report.summary = "所有规则校验通过"
        else:
            errors = sum(1 for f in report.findings if f.severity == "error")
            warnings = sum(1 for f in report.findings if f.severity == "warning")
            infos = sum(1 for f in report.findings if f.severity == "info")
            report.summary = f"发现 {errors} 个错误、{warnings} 个警告、{infos} 个提示"

        return report

    async def _thread_graph_audit(
        self, project_id: uuid.UUID, plan: dict, db: AsyncSession
    ) -> AuditReport:
        report = AuditReport("thread_graph")

        thread_plan = plan.get("thread_plan")
        if not thread_plan or not isinstance(thread_plan, dict):
            report.summary = "无线索计划数据"
            return report

        threads = thread_plan.get("threads", [])
        if not threads:
            report.summary = "线索计划为空"
            return report

        thread_map = {}
        for t in threads:
            tid = t.get("thread_id", t.get("name", ""))
            thread_map[tid] = t

        for tid, t in thread_map.items():
            status = t.get("status", "")
            deps = t.get("depends_on_threads", [])

            for dep_id in deps:
                if dep_id not in thread_map:
                    report.add_finding(AuditFinding(
                        "error",
                        f"thread:{tid}",
                        f"线索「{t.get('name', tid)}」依赖的线索「{dep_id}」不存在",
                        "检查线索ID是否正确",
                    ))

            if status == "resolved":
                reveal = t.get("reveal_chapters", [])
                if not reveal:
                    report.add_finding(AuditFinding(
                        "warning",
                        f"thread:{tid}",
                        f"线索「{t.get('name', tid)}」已标记为已解决但无回收章节",
                    ))

            if status == "abandoned":
                plant = t.get("plant_chapters", [])
                if plant:
                    report.add_finding(AuditFinding(
                        "info",
                        f"thread:{tid}",
                        f"线索「{t.get('name', tid)}」已废弃但曾在第{plant}章埋设",
                        "确认是否需要在正文中处理已废弃的伏笔",
                    ))

        spine = plan.get("chapter_spine", [])
        if spine:
            spine_thread_ops = {}
            for ch in spine:
                for op in ch.get("thread_ops", []):
                    ref_tid = op.get("thread_id", "")
                    if ref_tid and ref_tid not in thread_map:
                        report.add_finding(AuditFinding(
                            "warning",
                            f"chapter:{ch.get('chapter_number')}",
                            f"第{ch.get('chapter_number')}章引用了不存在的线索「{ref_tid}」",
                            "检查线索ID是否正确或在线索计划中添加该线索",
                        ))

        if not report.findings:
            report.summary = "线索图校验通过，无悬空或断裂"
        else:
            errors = sum(1 for f in report.findings if f.severity == "error")
            warnings = sum(1 for f in report.findings if f.severity == "warning")
            report.summary = f"线索图发现 {errors} 个错误、{warnings} 个警告"

        return report

    async def _dependency_graph_audit(self, plan: dict) -> AuditReport:
        report = AuditReport("dependency_graph")

        spine = plan.get("chapter_spine", [])
        if not spine:
            report.summary = "章节脊柱为空"
            return report

        ch_map = {ch.get("chapter_number"): ch for ch in spine}

        for ch in spine:
            ch_num = ch.get("chapter_number", 0)
            depends_on = ch.get("depends_on", [])

            for dep in depends_on:
                dep_num = self._parse_chapter_ref(dep, ch_map)
                if dep_num is None:
                    report.add_finding(AuditFinding(
                        "warning",
                        f"chapter:{ch_num}",
                        f"第{ch_num}章依赖的章节「{dep}」不存在",
                    ))
                elif dep_num >= ch_num:
                    report.add_finding(AuditFinding(
                        "error",
                        f"chapter:{ch_num}",
                        f"第{ch_num}章依赖了后续章节{dep_num}，可能造成循环依赖",
                    ))

            must_not = ch.get("must_not", [])
            for mn in must_not:
                mn_text = to_search_text(mn)
                if "提前揭示" in mn_text or "reveal" in mn_text:
                    pass

        visited = set()
        for ch in spine:
            ch_num = ch.get("chapter_number", 0)
            depends_on = ch.get("depends_on", [])
            if not depends_on and ch_num > 1:
                if ch_num not in [1, 2]:
                    pass

        if not report.findings:
            report.summary = "章节依赖图校验通过"
        else:
            errors = sum(1 for f in report.findings if f.severity == "error")
            warnings = sum(1 for f in report.findings if f.severity == "warning")
            report.summary = f"依赖图发现 {errors} 个错误、{warnings} 个警告"

        return report

    def _parse_chapter_ref(self, ref: str, ch_map: dict) -> int | None:
        try:
            return int(ref)
        except (ValueError, TypeError):
            pass

        if ref.startswith("ch_"):
            try:
                return int(ref.replace("ch_", "").lstrip("0"))
            except (ValueError, TypeError):
                pass

        for ch_num, ch in ch_map.items():
            if ch.get("chapter_id") == ref:
                return ch_num

        return None
