"""主编系统诊断追踪收集器。

核心原则：不改变生成行为，只记录关键运行数据。
每次章节生成都记录完整 trace，能定位失败到底发生在哪一层。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings as app_settings
from app.utils.atomic_file import atomic_write_text
from app.models.editor_trace import (
    EditorTrace,
    EditorTraceQuery,
    LayerTrace,
    PostGenerationMetrics,
    PreGenerationMetrics,
)

logger = logging.getLogger(__name__)


_STORAGE_DIR = Path(app_settings.data_dir) / "editor_traces"


class EditorTraceCollector:
    """主编系统诊断追踪收集器"""

    def __init__(self, storage_dir: str | Path | None = None):
        self._storage_dir = Path(storage_dir) if storage_dir is not None else _STORAGE_DIR
        self._traces: dict[str, EditorTrace] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        for path in self._storage_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                trace = EditorTrace(**data)
                self._traces[trace.trace_id] = trace
            except Exception as e:
                logger.debug(f"加载追踪记录失败: {e}")

    def _save_trace(self, trace: EditorTrace):
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        path = self._storage_dir / f"{trace.trace_id}.json"
        try:
            atomic_write_text(
                path,
                json.dumps(trace.model_dump(), ensure_ascii=False, indent=2),
            )
        except Exception as e:
            logger.warning(f"保存追踪记录失败: {e}")

    def start_trace(
        self,
        project_id: str,
        chapter_number: int,
        execution_id: str = "",
    ) -> EditorTrace:
        """开始一次追踪"""
        trace = EditorTrace(
            trace_id=f"etr_{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            chapter_number=chapter_number,
            execution_id=execution_id,
            created_at=datetime.now().isoformat(),
        )
        self._traces[trace.trace_id] = trace
        return trace

    def record_pre_metrics(self, trace_id: str, metrics: PreGenerationMetrics):
        """记录生成前指标"""
        trace = self._traces.get(trace_id)
        if trace:
            trace.pre_metrics = metrics

    def record_post_metrics(self, trace_id: str, metrics: PostGenerationMetrics):
        """记录生成后指标"""
        trace = self._traces.get(trace_id)
        if trace:
            trace.post_metrics = metrics

    def record_layer(
        self,
        trace_id: str,
        layer: str,
        status: str = "ok",
        duration_ms: int = 0,
        detail: str = "",
        error: str = "",
    ):
        """记录一层执行结果"""
        trace = self._traces.get(trace_id)
        if trace:
            trace.layers.append(LayerTrace(
                layer=layer,
                status=status,
                duration_ms=duration_ms,
                detail=detail,
                error=error,
            ))

    def finish_trace(
        self,
        trace_id: str,
        final_status: str = "completed",
        final_error: str = "",
    ):
        """结束一次追踪并保存"""
        trace = self._traces.get(trace_id)
        if trace:
            trace.final_status = final_status
            trace.final_error = final_error
            self._save_trace(trace)

    def save_trace(self, trace_id: str):
        """中途保存追踪记录（不结束追踪）。

        用于 writer_input_snapshots / repair_outcomes 等中间写入后
        立即落盘，避免进程崩溃导致数据丢失。
        """
        trace = self._traces.get(trace_id)
        if trace:
            self._save_trace(trace)

    def get_trace(self, trace_id: str) -> Optional[EditorTrace]:
        return self._traces.get(trace_id)

    def purge(self, project_id: str, chapter_number: int | None = None) -> int:
        trace_ids = [
            trace_id
            for trace_id, trace in self._traces.items()
            if trace.project_id == project_id
            and (chapter_number is None or trace.chapter_number == chapter_number)
        ]
        for trace_id in trace_ids:
            self._traces.pop(trace_id, None)
            (self._storage_dir / f"{trace_id}.json").unlink(missing_ok=True)
        return len(trace_ids)

    def query_traces(self, query: EditorTraceQuery) -> list[EditorTrace]:
        """查询追踪记录"""
        results = list(self._traces.values())

        if query.project_id:
            results = [t for t in results if t.project_id == query.project_id]
        if query.chapter_number is not None:
            results = [t for t in results if t.chapter_number == query.chapter_number]
        if query.execution_id:
            results = [t for t in results if t.execution_id == query.execution_id]
        if query.final_status:
            results = [t for t in results if t.final_status == query.final_status]

        results.sort(key=lambda t: t.created_at, reverse=True)
        return results[:query.limit]

    def get_project_summary(self, project_id: str, last_n: int = 10) -> dict:
        """获取项目级诊断摘要"""
        traces = [t for t in self._traces.values() if t.project_id == project_id]
        traces.sort(key=lambda t: t.created_at, reverse=True)
        recent = traces[:last_n]

        if not recent:
            return {"project_id": project_id, "trace_count": 0}

        completed = [t for t in recent if t.final_status == "completed"]
        degraded = [t for t in recent if t.final_status == "degraded"]
        failed = [t for t in recent if t.final_status == "failed"]

        avg_must_show = 0.0
        avg_findings = 0.0
        avg_fbi_rounds = 0.0
        avg_explanation_density = 0.0

        if recent:
            all_must_shows = []
            all_findings = []
            all_fbi_rounds = []
            all_exp_density = []

            for t in recent:
                if t.pre_metrics.per_scene_must_show_counts:
                    all_must_shows.append(sum(t.pre_metrics.per_scene_must_show_counts) / len(t.pre_metrics.per_scene_must_show_counts))
                all_findings.append(t.post_metrics.quality_finding_count)
                all_fbi_rounds.append(t.post_metrics.fbi_auto_repair_rounds)
                all_exp_density.append(t.post_metrics.explanation_density)

            avg_must_show = sum(all_must_shows) / len(all_must_shows) if all_must_shows else 0
            avg_findings = sum(all_findings) / len(all_findings) if all_findings else 0
            avg_fbi_rounds = sum(all_fbi_rounds) / len(all_fbi_rounds) if all_fbi_rounds else 0
            avg_explanation_density = sum(all_exp_density) / len(all_exp_density) if all_exp_density else 0

        return {
            "project_id": project_id,
            "trace_count": len(recent),
            "completed": len(completed),
            "degraded": len(degraded),
            "failed": len(failed),
            "avg_must_show_per_scene": round(avg_must_show, 2),
            "avg_quality_findings": round(avg_findings, 2),
            "avg_fbi_rounds": round(avg_fbi_rounds, 2),
            "avg_explanation_density": round(avg_explanation_density, 4),
        }


# 全局单例
_collector: EditorTraceCollector | None = None


def get_editor_trace_collector() -> EditorTraceCollector:
    global _collector
    if _collector is None:
        _collector = EditorTraceCollector()
    return _collector
