from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_, select

_logger = logging.getLogger(__name__)


class ProjectHealthService:
    async def build(self, db, project) -> dict:
        core_data = getattr(project, "core_data", None) or {}
        history = core_data.get("generation_history", []) if isinstance(core_data, dict) else []
        report_summaries = [
            item.get("report_summary", {})
            for item in history
            if isinstance(item, dict)
        ]
        degraded = sum(1 for item in report_summaries if item.get("degraded"))
        blocked_like = sum(1 for item in report_summaries if item.get("pass") is False)
        auto_repairs = sum(int(item.get("auto_repairs", 0) or 0) for item in report_summaries)
        trace_count = 0
        progression_count = 0
        experience_report_count = 0
        storage_error = False
        try:
            from app.db.db_models import EntityProgression, ExperienceQualityReport, GenerationTrace

            tr = await db.execute(
                select(func.count()).select_from(GenerationTrace).where(
                    GenerationTrace.project_id == project.id,
                    or_(GenerationTrace.expires_at.is_(None), GenerationTrace.expires_at > datetime.now(timezone.utc)),
                )
            )
            trace_count = tr.scalar() or 0
            pr = await db.execute(
                select(func.count()).select_from(EntityProgression).where(
                    EntityProgression.project_id == project.id
                )
            )
            progression_count = pr.scalar() or 0
            qr = await db.execute(
                select(func.count()).select_from(ExperienceQualityReport).where(
                    ExperienceQualityReport.project_id == project.id
                )
            )
            experience_report_count = qr.scalar() or 0
        except Exception as exc:
            storage_error = True
            _logger.warning("Project health storage query failed: %s", exc)
        quality_memory = {}
        try:
            from app.services.quality_memory_service import QualityMemoryService
            quality_memory = QualityMemoryService().get_project_quality_memory(project)
        except Exception:
            quality_memory = {}
        if storage_error or blocked_like:
            status = "degraded"
        elif degraded:
            status = "warning"
        else:
            status = "ok"

        return {
            "schema_version": 1,
            "project_id": str(project.id),
            "status": status,
            "summary": {
                "generation_runs": len(report_summaries),
                "degraded_runs": degraded,
                "blocked_or_failed_runs": blocked_like,
                "auto_repairs": auto_repairs,
                "trace_count": trace_count,
                "progression_count": progression_count,
                "experience_quality_report_count": experience_report_count,
                "quality_memory_pattern_count": len(quality_memory.get("recent_quality_patterns", [])),
            },
            "quality_memory": quality_memory,
            "signals": [
                {
                    "name": "阻断或失败",
                    "value": blocked_like,
                    "level": "error" if blocked_like else "ok",
                },
                {
                    "name": "生成降级",
                    "value": degraded,
                    "level": "warning" if degraded else "ok",
                },
                {
                    "name": "自动修复次数",
                    "value": auto_repairs,
                    "level": "info",
                },
                {
                    "name": "轨迹记录",
                    "value": trace_count,
                    "level": "ok",
                },
                {
                    "name": "状态演进候选",
                    "value": progression_count,
                    "level": "ok",
                },
                {
                    "name": "体验质量报告",
                    "value": experience_report_count,
                    "level": "ok",
                },
                *([{
                    "name": "健康数据查询异常",
                    "value": 1,
                    "level": "warning",
                }] if storage_error else []),
            ],
        }
