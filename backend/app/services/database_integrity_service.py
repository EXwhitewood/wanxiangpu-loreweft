"""SQLite integrity checks and conservative historical orphan repair.

The application used to open some SQLite connections without enabling
``PRAGMA foreign_keys``.  Rows whose parent was deleted during that period are
therefore possible even though the current ORM declares cascading foreign
keys.  Repair follows the declared SQLite constraint action instead of
hard-coding product tables:

* ``ON DELETE CASCADE`` rows are removed;
* ``ON DELETE SET NULL`` columns are cleared;
* every other violation is left untouched and keeps health degraded.

A verified online backup is created before the first production repair.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(value: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe SQLite identifier: {value!r}")
    return f'"{value}"'


@dataclass(slots=True)
class IntegrityRepairReport:
    ok: bool
    before_violations: int = 0
    repaired_violations: int = 0
    remaining_violations: int = 0
    cascaded_rows: int = 0
    nulled_rows: int = 0
    derived_index_rows: int = 0
    backup_path: str = ""
    backup_sha256: str = ""
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _backup_sqlite_database(source: Path) -> tuple[str, str]:
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"pre-integrity-repair-{stamp}.db"

    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(target, timeout=30)
    try:
        src.backup(dst, pages=2048)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"backup integrity_check failed: {integrity!r}")
    finally:
        dst.close()
        src.close()

    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return str(target), digest.hexdigest()


async def _foreign_key_rows(engine: AsyncEngine) -> list[tuple[Any, ...]]:
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA foreign_key_check"))
        return [tuple(row) for row in result.fetchall()]


async def inspect_sqlite_foreign_keys(engine: AsyncEngine) -> dict[str, Any]:
    if engine.url.get_backend_name() != "sqlite":
        return {"ok": True, "violations": 0, "backend": engine.url.get_backend_name()}
    try:
        rows = await _foreign_key_rows(engine)
        return {
            "ok": not rows,
            "violations": len(rows),
            "sample": [
                {
                    "table": str(row[0]),
                    "rowid": row[1],
                    "parent": str(row[2]),
                    "foreign_key_id": int(row[3]),
                }
                for row in rows[:20]
            ],
        }
    except Exception as exc:
        return {
            "ok": False,
            "violations": -1,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


async def repair_sqlite_foreign_keys(
    engine: AsyncEngine,
    *,
    create_backup: bool = True,
) -> IntegrityRepairReport:
    """Repair only violations whose SQLite constraint declares a safe action."""
    if engine.url.get_backend_name() != "sqlite":
        return IntegrityRepairReport(ok=True)

    try:
        violations = await _foreign_key_rows(engine)
        report = IntegrityRepairReport(ok=not violations, before_violations=len(violations))
        if not violations:
            return report

        database = engine.url.database or ""
        if create_backup:
            if not database or database == ":memory:":
                report.ok = False
                report.error = "refusing automatic repair without a file-backed SQLite backup"
                return report
            backup_path, digest = await asyncio.to_thread(
                _backup_sqlite_database,
                Path(database).resolve(),
            )
            report.backup_path = backup_path
            report.backup_sha256 = digest

        handled_rows: set[tuple[str, int]] = set()
        async with engine.begin() as conn:
            for table, rowid, parent, foreign_key_id in violations:
                table_name = str(table)
                row_key = (table_name, int(rowid))
                if row_key in handled_rows:
                    continue
                quoted_table = _quote_identifier(table_name)
                fk_rows = (
                    await conn.execute(text(f"PRAGMA foreign_key_list({quoted_table})"))
                ).fetchall()
                fk = next((row for row in fk_rows if int(row[0]) == int(foreign_key_id)), None)
                if fk is None:
                    report.unresolved.append(
                        {"table": table_name, "rowid": rowid, "parent": str(parent), "reason": "constraint_missing"}
                    )
                    continue

                child_column = str(fk[3])
                on_delete = str(fk[6] or "NO ACTION").upper()
                if on_delete == "CASCADE":
                    result = await conn.execute(
                        text(f"DELETE FROM {quoted_table} WHERE rowid = :rowid"),
                        {"rowid": int(rowid)},
                    )
                    report.cascaded_rows += max(int(result.rowcount or 0), 0)
                    handled_rows.add(row_key)
                elif on_delete == "SET NULL":
                    quoted_column = _quote_identifier(child_column)
                    result = await conn.execute(
                        text(f"UPDATE {quoted_table} SET {quoted_column} = NULL WHERE rowid = :rowid"),
                        {"rowid": int(rowid)},
                    )
                    report.nulled_rows += max(int(result.rowcount or 0), 0)
                    handled_rows.add(row_key)
                else:
                    report.unresolved.append(
                        {
                            "table": table_name,
                            "rowid": rowid,
                            "parent": str(parent),
                            "column": child_column,
                            "on_delete": on_delete,
                            "reason": "unsafe_delete_action",
                        }
                    )

            # fts_detail_seeds is a rebuildable mirror without an FK.  Keep it
            # aligned after canonical cascades so search cannot surface stale
            # facts from deleted chapter revisions.
            has_fts = (
                await conn.execute(
                    text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fts_detail_seeds'")
                )
            ).scalar_one_or_none()
            if has_fts:
                result = await conn.execute(
                    text(
                        "DELETE FROM fts_detail_seeds "
                        "WHERE NOT EXISTS (SELECT 1 FROM detail_seeds WHERE detail_seeds.id = fts_detail_seeds.id)"
                    )
                )
                report.derived_index_rows = max(int(result.rowcount or 0), 0)

        remaining = await _foreign_key_rows(engine)
        report.remaining_violations = len(remaining)
        report.repaired_violations = max(report.before_violations - report.remaining_violations, 0)
        if remaining:
            known = {(item["table"], item["rowid"]) for item in report.unresolved}
            for table, rowid, parent, foreign_key_id in remaining:
                if (str(table), rowid) not in known:
                    report.unresolved.append(
                        {
                            "table": str(table),
                            "rowid": rowid,
                            "parent": str(parent),
                            "foreign_key_id": int(foreign_key_id),
                            "reason": "remaining_after_repair",
                        }
                    )
        report.ok = report.remaining_violations == 0
        return report
    except Exception as exc:
        return IntegrityRepairReport(
            ok=False,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
