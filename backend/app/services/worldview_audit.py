import json
import logging
from datetime import datetime, timezone

from app.services.memory_core import CoreMemoryService
from app.services.memory_shell import ShellMemoryService

logger = logging.getLogger(__name__)


class WorldviewAuditService:
    async def audit(self, project_id: str, db=None) -> dict:
        core_service = CoreMemoryService()
        shell_service = ShellMemoryService()
        issues = []

        seeds = await shell_service.get_seeds_by_tier(project_id)
        auto_seeds = [s for s in seeds if s.get("metadata", {}).get("auto_extracted")]
        char_seeds = [s for s in auto_seeds if s.get("type") == "character"]
        loc_seeds = [s for s in auto_seeds if s.get("type") == "location"]
        rule_seeds = [s for s in auto_seeds if s.get("type") == "rule_hint"]

        if char_seeds:
            issues.append({
                "type": "unregistered_character",
                "severity": "medium",
                "message": f"发现 {len(char_seeds)} 个章节中提及但未录入的人物",
                "items": [
                    {"name": s.get("metadata", {}).get("name", ""), "content": s.get("content", "")[:80]}
                    for s in char_seeds[:10]
                ],
            })

        if loc_seeds:
            issues.append({
                "type": "unregistered_location",
                "severity": "medium",
                "message": f"发现 {len(loc_seeds)} 个章节中提及但未录入的地点",
                "items": [
                    {"name": s.get("metadata", {}).get("name", ""), "content": s.get("content", "")[:80]}
                    for s in loc_seeds[:10]
                ],
            })

        if rule_seeds:
            issues.append({
                "type": "implied_rule",
                "severity": "low",
                "message": f"发现 {len(rule_seeds)} 条章节中暗示但未确立的规则",
                "items": [
                    {"hint": s.get("content", "")[:80]}
                    for s in rule_seeds[:10]
                ],
            })

        foreshadowing = await core_service.list_foreshadowing(project_id)
        overdue_foreshadowing = []
        for fs in foreshadowing:
            if fs.get("status") == "active" and fs.get("reveal_window_start"):
                overdue_foreshadowing.append(fs)

        if overdue_foreshadowing:
            issues.append({
                "type": "overdue_foreshadowing",
                "severity": "high",
                "message": f"发现 {len(overdue_foreshadowing)} 条已过揭示章节但未揭示的伏笔",
                "items": [
                    {
                        "name": fs.get("name", ""),
                        "reveal_window_start": fs.get("reveal_window_start"),
                        "status": fs.get("status"),
                    }
                    for fs in overdue_foreshadowing[:10]
                ],
            })

        rules = await core_service.list_world_rules(project_id)
        critical_rules = [r for r in rules if r.get("priority") == "critical"]
        if not critical_rules:
            issues.append({
                "type": "no_critical_rules",
                "severity": "low",
                "message": "尚未设定任何宪法级规则，建议为世界观设定不可违背的核心规则",
                "items": [],
            })

        report = {
            "project_id": project_id,
            "audit_time": datetime.now(timezone.utc).isoformat(),
            "total_issues": len(issues),
            "high_severity": len([i for i in issues if i.get("severity") == "high"]),
            "medium_severity": len([i for i in issues if i.get("severity") == "medium"]),
            "low_severity": len([i for i in issues if i.get("severity") == "low"]),
            "issues": issues,
            "stats": {
                "total_rules": len(rules),
                "total_characters": len(await core_service.list_characters(project_id)),
                "total_locations": len(await core_service.list_locations(project_id)),
                "total_foreshadowing": len(foreshadowing),
                "auto_extracted_seeds": len(auto_seeds),
            },
        }

        if db:
            try:
                from app.db.db_models import Project

                project = await db.get(Project, project_id)
                if project:
                    core_data = dict(project.core_data or {})
                    core_data["worldview_audit"] = report
                    project.core_data = core_data
                    await db.commit()
            except Exception as e:
                logger.warning(f"[WorldviewAudit] failed to save report: {e}")

        return report

    async def get_audit_report(self, project_id: str, db=None) -> dict | None:
        if db:
            try:
                from app.db.db_models import Project

                project = await db.get(Project, project_id)
                if project and project.core_data:
                    return project.core_data.get("worldview_audit")
            except Exception:
                pass
        return None
