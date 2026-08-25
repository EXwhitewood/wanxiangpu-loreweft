"""File-backed context ledger for editor context compilation."""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from app.config import settings as app_settings
from app.utils.atomic_file import atomic_write_text


class ContextLedgerService:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or (Path(app_settings.data_dir) / "context_ledger")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        project_id: str,
        chapter_number: int,
        task_id: str,
        agent: str,
        envelope: dict,
        compaction_report: dict | None = None,
        extra: dict | None = None,
    ) -> dict:
        ledger_id = f"ctxledger_ch{chapter_number}_{agent}_{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        token_report = envelope.get("token_report") or {}
        entry = {
            "ledger_id": ledger_id,
            "project_id": project_id,
            "chapter_number": chapter_number,
            "task_id": task_id,
            "agent": agent,
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "model_profile": (envelope.get("model_context_profile") or {}).get("profile_id", "default_200k"),
            "token_before": (compaction_report or {}).get("before_tokens", token_report.get("estimated_input_tokens", 0)),
            "token_after": token_report.get("estimated_input_tokens", 0),
            "soft_input_limit_tokens": token_report.get("soft_input_limit_tokens", 0),
            "hard_input_limit_tokens": token_report.get("hard_input_limit_tokens", 0),
            "compactions": [compaction_report] if compaction_report else [],
            "pinned_blocks": [
                block.get("block_id")
                for block in envelope.get("blocks", [])
                if isinstance(block, dict) and block.get("priority") in {"P0", "P1"}
            ],
            "blocks": [
                {
                    "block_id": block.get("block_id"),
                    "block_type": block.get("block_type") or block.get("type"),
                    "priority": block.get("priority"),
                    "compressible": bool(block.get("compressible")),
                    "compacted": bool(block.get("compacted")),
                    "estimated_tokens": block.get("estimated_tokens", 0),
                    "source_refs": block.get("source_refs", []),
                }
                for block in envelope.get("blocks", [])
                if isinstance(block, dict)
            ],
            "validation": (compaction_report or {}).get("validation", {}),
            "extra": extra or {},
        }
        path = self._entry_path(project_id, ledger_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(entry, ensure_ascii=False, indent=2, default=str))
        self._update_latest(project_id, chapter_number, agent, ledger_id)
        return entry

    def latest(self, project_id: str, chapter_number: int | None = None, agent: str | None = None) -> dict | None:
        candidates = []
        project_dir = self.base_dir / str(project_id)
        if not project_dir.exists():
            return None
        for path in project_dir.glob("*.json"):
            if path.name.startswith("_latest"):
                continue
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if chapter_number is not None and int(item.get("chapter_number") or 0) != int(chapter_number):
                continue
            if agent and item.get("agent") != agent:
                continue
            candidates.append(item)
        if not candidates:
            return None
        return sorted(candidates, key=lambda x: x.get("created_at", ""), reverse=True)[0]

    def get(self, project_id: str, ledger_id: str) -> dict | None:
        path = self._entry_path(project_id, ledger_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def purge(self, project_id: str, chapter_number: int | None = None) -> int:
        """Delete ledger artifacts for one chapter or the whole project."""
        project_dir = self.base_dir / str(project_id)
        if not project_dir.is_dir():
            return 0
        removed = 0
        for path in project_dir.glob("*.json"):
            should_remove = chapter_number is None
            if chapter_number is not None:
                if path.name.startswith(f"_latest_ch{int(chapter_number)}_"):
                    should_remove = True
                elif not path.name.startswith("_latest_"):
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        should_remove = int(payload.get("chapter_number") or 0) == int(chapter_number)
                    except Exception:
                        should_remove = False
            if should_remove:
                path.unlink(missing_ok=True)
                removed += 1
        try:
            project_dir.rmdir()
        except OSError:
            pass
        return removed

    def _entry_path(self, project_id: str, ledger_id: str) -> Path:
        safe_id = "".join(ch for ch in ledger_id if ch.isalnum() or ch in "_-")
        return self.base_dir / str(project_id) / f"{safe_id}.json"

    def _update_latest(self, project_id: str, chapter_number: int, agent: str, ledger_id: str) -> None:
        latest_path = self.base_dir / str(project_id) / f"_latest_ch{chapter_number}_{agent}.json"
        atomic_write_text(latest_path, json.dumps({"ledger_id": ledger_id}, ensure_ascii=False))


_ledger_service: ContextLedgerService | None = None


def get_context_ledger_service() -> ContextLedgerService:
    global _ledger_service
    if _ledger_service is None:
        _ledger_service = ContextLedgerService()
    return _ledger_service
