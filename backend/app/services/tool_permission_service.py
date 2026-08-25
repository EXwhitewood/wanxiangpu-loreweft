from __future__ import annotations

from app.models.file_skill import ToolPermissionDecision


class ToolPermissionService:
    ORDER = ["readonly", "safe_write", "project_write", "admin"]

    TOOL_MIN_PERMISSION = {
        "read_project": "readonly",
        "read_context": "readonly",
        "write_draft": "safe_write",
        "write_report": "safe_write",
        "write_candidate_patch": "safe_write",
        "update_project_state": "project_write",
        "confirm_progression": "project_write",
        "modify_workflow": "admin",
        "update_global_settings": "admin",
    }

    def can_use(
        self,
        granted: str,
        tool_name: str,
        required_override: str | None = None,
    ) -> ToolPermissionDecision:
        required = self._max_required(
            self.TOOL_MIN_PERMISSION.get(tool_name, "admin"),
            required_override,
        )
        allowed = self._rank(granted) >= self._rank(required)
        return ToolPermissionDecision(
            allowed=allowed,
            permission=granted,
            reason="" if allowed else f"tool '{tool_name}' requires {required}",
        )

    def _max_required(self, default_required: str, override: str | None) -> str:
        if override is None:
            return default_required
        return default_required if self._rank(default_required) >= self._rank(override) else override

    def _rank(self, value: str) -> int:
        try:
            return self.ORDER.index(value)
        except ValueError:
            return -1
