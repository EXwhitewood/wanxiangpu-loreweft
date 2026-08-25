"""方案18：主编-Writer 实时反馈回路服务。

存储 Writer 执行报告和 FBI rewrite 偏离原因，供主编在规划下一章时参考。

数据以 JSON 文件持久化到 data/editor_feedback/{project_id}.json，
每个项目一个文件，按章节号索引。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.utils.atomic_file import atomic_write_text

logger = logging.getLogger(__name__)

_FEEDBACK_DIR = os.path.join(
    os.path.dirname(getattr(settings, "sqlite_path", "data/loreweft.db")),
    "editor_feedback",
)


def _ensure_dir() -> None:
    if _FEEDBACK_DIR:
        os.makedirs(_FEEDBACK_DIR, exist_ok=True)


def _validate_project_id(project_id: str) -> str:
    """校验 project_id 防止路径遍历攻击。

    P2-27 修复：白名单字符 + os.path.basename 双重防护。
    """
    if not project_id or not isinstance(project_id, str):
        raise ValueError("Invalid project_id")
    # 白名单：仅允许字母、数字、下划线、连字符
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
    if not all(c in allowed for c in project_id):
        raise ValueError("Invalid project_id: contains forbidden characters")
    # 双重防护：取 basename 防止任何路径分隔符绕过
    safe_id = os.path.basename(project_id)
    if safe_id != project_id:
        raise ValueError("Invalid project_id: path separator detected")
    return safe_id


def _project_file(project_id: str) -> str:
    safe_id = _validate_project_id(project_id)
    return os.path.join(_FEEDBACK_DIR, f"{safe_id}.json")


class EditorFeedbackService:
    """主编反馈服务：存储和查询 Writer 执行报告、FBI rewrite 偏离原因。

    方案18 步骤1~3 的数据层：
    - 步骤1：Writer 执行报告 → record_execution_report / get_execution_report
    - 步骤2：FBI rewrite 偏离原因 → record_rewrite_feedback / get_rewrite_feedback
    - 步骤3：主编规划下一章时读取上一章报告 → get_chapter_feedback_summary
    """

    @staticmethod
    def record_execution_report(
        project_id: str,
        chapter_number: int,
        report: dict[str, Any],
    ) -> None:
        """记录 Writer 执行报告。

        Args:
            project_id: 项目 ID
            chapter_number: 章节号
            report: 执行报告，包含 must_show_coverage / forbidden_violations /
                    ending_state_achieved / deviations 等
        """
        try:
            _ensure_dir()
            data = _load_project(project_id)
            data.setdefault("execution_reports", {})[str(chapter_number)] = {
                "chapter_number": chapter_number,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **report,
            }
            _save_project(project_id, data)
        except Exception as e:
            logger.warning(f"failed to record execution report for ch{chapter_number}: {e}")

    @staticmethod
    def get_execution_report(
        project_id: str,
        chapter_number: int,
    ) -> dict[str, Any] | None:
        """获取指定章节的 Writer 执行报告。"""
        try:
            data = _load_project(project_id)
            return data.get("execution_reports", {}).get(str(chapter_number))
        except Exception as e:
            logger.warning(f"failed to get execution report for ch{chapter_number}: {e}")
            return None

    @staticmethod
    def record_rewrite_feedback(
        project_id: str,
        chapter_number: int,
        feedback: dict[str, Any],
    ) -> None:
        """记录 FBI rewrite_scene 触发的偏离原因。

        Args:
            project_id: 项目 ID
            chapter_number: 章节号
            feedback: 偏离原因，包含 scene_index / violation_type / detail /
                      original_contract / deviation 等
        """
        try:
            _ensure_dir()
            data = _load_project(project_id)
            data.setdefault("rewrite_feedbacks", {}).setdefault(str(chapter_number), []).append({
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **feedback,
            })
            _save_project(project_id, data)
        except Exception as e:
            logger.warning(f"failed to record rewrite feedback for ch{chapter_number}: {e}")

    @staticmethod
    def get_rewrite_feedback(
        project_id: str,
        chapter_number: int,
    ) -> list[dict[str, Any]]:
        """获取指定章节的 FBI rewrite 偏离原因列表。"""
        try:
            data = _load_project(project_id)
            return data.get("rewrite_feedbacks", {}).get(str(chapter_number), [])
        except Exception as e:
            logger.warning(f"failed to get rewrite feedback for ch{chapter_number}: {e}")
            return []

    @staticmethod
    def record_style_conflict(
        project_id: str,
        chapter_number: int,
        conflict: dict[str, Any],
    ) -> None:
        """记录 contract_style_conflict 信号（方案18 步骤4）。

        Args:
            project_id: 项目 ID
            chapter_number: 章节号
            conflict: 冲突详情，包含 dimension / style_directive / scene_index 等
        """
        try:
            _ensure_dir()
            data = _load_project(project_id)
            data.setdefault("style_conflicts", {}).setdefault(str(chapter_number), []).append({
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **conflict,
            })
            _save_project(project_id, data)
        except Exception as e:
            logger.warning(f"failed to record style conflict for ch{chapter_number}: {e}")

    @staticmethod
    def trigger_editor_review(
        project_id: str,
        chapter_number: int,
        *,
        reason: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """方案18 步骤4：触发主动 editor_review，通知主编重新审视合同与风格的冲突。

        将 review 请求持久化到 editor_review_requests 队列，供主编在下一轮规划
        或异步监听时消费。该方法不阻塞当前章节生成流程。

        Args:
            project_id: 项目 ID
            chapter_number: 章节号
            reason: 触发原因，如 "contract_style_conflict"
            detail: 触发详情，例如冲突维度、修复建议等
        """
        try:
            _ensure_dir()
            data = _load_project(project_id)
            data.setdefault("editor_review_requests", {}).setdefault(
                str(chapter_number), []
            ).append({
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "detail": detail or {},
                "status": "pending",
            })
            _save_project(project_id, data)
            logger.info(
                "[EditorFeedback] editor_review triggered for project=%s ch=%s reason=%s",
                project_id, chapter_number, reason,
            )
        except Exception as e:
            logger.warning(
                "failed to trigger editor review for ch%s: %s", chapter_number, e
            )

    @staticmethod
    def get_pending_editor_reviews(
        project_id: str,
        chapter_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """获取待处理的 editor_review 请求。

        Args:
            project_id: 项目 ID
            chapter_number: 可选章节号；None 时返回所有章节的待处理请求
        """
        try:
            data = _load_project(project_id)
            requests_map = data.get("editor_review_requests", {})
        except Exception as e:
            logger.warning("failed to load editor review requests: %s", e)
            return []

        if chapter_number is not None:
            return list(requests_map.get(str(chapter_number), []))
        result: list[dict[str, Any]] = []
        for items in requests_map.values():
            result.extend(items)
        return result

    @staticmethod
    def get_chapter_feedback_summary(
        project_id: str,
        chapter_number: int,
    ) -> dict[str, Any] | None:
        """获取指定章节的完整反馈摘要（执行报告 + rewrite 偏离 + 风格冲突）。

        供主编在规划下一章时参考上一章的反馈。
        """
        report = EditorFeedbackService.get_execution_report(project_id, chapter_number)
        rewrites = EditorFeedbackService.get_rewrite_feedback(project_id, chapter_number)
        try:
            data = _load_project(project_id)
            conflicts = data.get("style_conflicts", {}).get(str(chapter_number), [])
        except Exception:
            conflicts = []

        if not report and not rewrites and not conflicts:
            return None

        return {
            "chapter_number": chapter_number,
            "execution_report": report,
            "rewrite_feedbacks": rewrites,
            "style_conflicts": conflicts,
        }

    @staticmethod
    def build_feedback_prompt_section(
        project_id: str,
        chapter_number: int,
    ) -> str:
        """构建反馈 prompt 片段，供主编规划下一章时注入。

        从上一章的反馈中提取关键偏离信息，形成简洁的 prompt 文本。
        """
        # 查找最近的上一章反馈
        prev_summary = None
        for prev_ch in range(chapter_number - 1, max(0, chapter_number - 5) - 1, -1):
            prev_summary = EditorFeedbackService.get_chapter_feedback_summary(project_id, prev_ch)
            if prev_summary:
                break

        if not prev_summary:
            return ""

        parts = [f"\n\n### 上一章（第{prev_summary['chapter_number']}章）执行反馈"]
        report = prev_summary.get("execution_report")
        if report:
            coverage = report.get("must_show_coverage", {})
            if coverage:
                missed = [k for k, v in coverage.items() if not v]
                if missed:
                    parts.append(f"- must_show 未覆盖：{', '.join(missed[:5])}")
                else:
                    parts.append("- must_show 全部覆盖")

            forbidden = report.get("forbidden_violations", [])
            if forbidden:
                parts.append(f"- forbidden 违规：{'; '.join(str(f)[:80] for f in forbidden[:3])}")

            ending = report.get("ending_state_achieved")
            if ending is False:
                parts.append(f"- ending_state 未达成：{report.get('ending_state_detail', '未指定')}")

            deviations = report.get("deviations", [])
            if deviations:
                parts.append(f"- 偏离原因：{'; '.join(str(d)[:80] for d in deviations[:3])}")

        rewrites = prev_summary.get("rewrite_feedbacks", [])
        if rewrites:
            parts.append(f"- FBI 触发场景重写 {len(rewrites)} 次")
            for rw in rewrites[:2]:
                detail = rw.get("detail", "")
                parts.append(f"  · {str(detail)[:80]}")

        conflicts = prev_summary.get("style_conflicts", [])
        if conflicts:
            parts.append(f"- 合同-风格冲突 {len(conflicts)} 次")

        parts.append("\n请在规划本章时参考上一章的反馈，避免重复偏离。")
        return "\n".join(parts)


def _load_project(project_id: str) -> dict:
    """加载项目的反馈数据。"""
    path = _project_file(project_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_project(project_id: str, data: dict) -> None:
    """保存项目的反馈数据。"""
    path = _project_file(project_id)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, default=str))


def purge_editor_feedback(project_id: str, chapter_number: int | None = None) -> int:
    path = _project_file(project_id)
    if not os.path.exists(path):
        return 0
    if chapter_number is None:
        os.unlink(path)
        return 1
    data = _load_project(project_id)
    removed = 0
    key = str(int(chapter_number))
    for bucket in (
        "execution_reports",
        "rewrite_feedbacks",
        "style_conflicts",
        "editor_review_requests",
    ):
        values = data.get(bucket)
        if isinstance(values, dict) and key in values:
            values.pop(key, None)
            removed += 1
    if removed:
        _save_project(project_id, data)
    return removed
