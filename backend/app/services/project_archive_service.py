"""Versioned, checksum-verified, lossless project archive and restore."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


ARCHIVE_FORMAT = "loreweft.project-archive"
ARCHIVE_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DERIVED_TABLES = {"fts_detail_seeds"}
_FTS_SUFFIXES = ("_data", "_idx", "_content", "_docsize", "_config")


def _quote(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQLite identifier: {value!r}")
    return f'"{value}"'


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$binary": base64.b64encode(value).decode("ascii")}
    return value


def _db_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$binary"}:
        return base64.b64decode(str(value["$binary"]))
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(tables: dict[str, list[dict[str, Any]]]) -> str:
    return hashlib.sha256(_canonical_json(tables).encode("utf-8")).hexdigest()


async def _schema(db: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    ).all()
    result: dict[str, dict[str, Any]] = {}
    for name, ddl in rows:
        table = str(name)
        ddl_text = str(ddl or "")
        if table in _DERIVED_TABLES or "VIRTUAL TABLE" in ddl_text.upper():
            continue
        if any(table.endswith(suffix) for suffix in _FTS_SUFFIXES):
            continue
        column_rows = (await db.execute(text(f"PRAGMA table_info({_quote(table)})"))).all()
        columns = [str(row[1]) for row in column_rows]
        primary_keys = [
            str(row[1])
            for row in column_rows
            if int(row[5] or 0) > 0
        ]
        required_columns = [str(row[1]) for row in column_rows if int(row[3] or 0) > 0]
        foreign_keys = [tuple(row) for row in (await db.execute(text(f"PRAGMA foreign_key_list({_quote(table)})"))).all()]
        result[table] = {
            "columns": columns,
            "primary_keys": primary_keys,
            "required_columns": required_columns,
            "foreign_keys": foreign_keys,
        }
    return result


async def _select_rows(
    db: AsyncSession,
    table: str,
    *,
    where: str,
    params: dict[str, Any],
    columns: list[str],
    primary_keys: list[str],
) -> list[dict[str, Any]]:
    order = ", ".join(_quote(item) for item in primary_keys) if primary_keys else "rowid"
    result = await db.execute(text(f"SELECT * FROM {_quote(table)} WHERE {where} ORDER BY {order}"), params)
    return [
        {column: _json_value(value) for column, value in zip(columns, row)}
        for row in result.fetchall()
    ]


def _merge_rows(
    current: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    primary_keys: list[str],
) -> list[dict[str, Any]]:
    if not incoming:
        return current
    keys = primary_keys or list(incoming[0].keys())
    seen = {tuple(_canonical_json(row.get(key)) for key in keys) for row in current}
    for row in incoming:
        identity = tuple(_canonical_json(row.get(key)) for key in keys)
        if identity not in seen:
            current.append(row)
            seen.add(identity)
    current.sort(key=lambda row: tuple(str(row.get(key) or "") for key in keys))
    return current


class ProjectArchiveService:
    @staticmethod
    def _validate_restore_rows(
        schema: dict[str, dict[str, Any]],
        tables: dict[str, Any],
        project_id: str,
    ) -> None:
        """Reject malformed or cross-project rows before deleting live data."""
        project_rows = tables.get("projects")
        if not isinstance(project_rows, list) or len(project_rows) != 1:
            raise ValueError("项目归档必须且只能包含一条项目记录")
        if str(project_rows[0].get("id") or "") != project_id:
            raise ValueError("归档项目记录与当前项目不一致")

        for table, rows in tables.items():
            info = schema[table]
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"归档表 {table} 必须是非空记录列表")
            allowed = set(info["columns"])
            primary_keys = list(info["primary_keys"])
            required = set(info["required_columns"]) | set(primary_keys)
            identities: set[tuple[str, ...]] = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) - allowed:
                    raise ValueError(f"归档表 {table} 包含未知字段")
                missing = [column for column in required if column not in row or row[column] is None]
                if missing:
                    raise ValueError(f"归档表 {table} 缺少必填字段：{', '.join(sorted(missing))}")
                if "project_id" in allowed and str(row.get("project_id") or "") != project_id:
                    raise ValueError(f"归档表 {table} 包含其他项目的记录")
                if primary_keys:
                    identity = tuple(_canonical_json(row.get(key)) for key in primary_keys)
                    if identity in identities:
                        raise ValueError(f"归档表 {table} 包含重复主键")
                    identities.add(identity)

        # If a referenced parent table is part of the archive, every child FK
        # must resolve inside that same archive. This prevents a hand-edited,
        # re-checksummed file from attaching target-project rows to another
        # project's chapter/session/etc. already present in the database.
        for table, rows in tables.items():
            for fk in schema[table]["foreign_keys"]:
                parent = str(fk[2])
                child_column = str(fk[3])
                parent_column = str(fk[4])
                if parent not in tables:
                    continue
                parent_values = {
                    _canonical_json(row.get(parent_column))
                    for row in tables[parent]
                    if row.get(parent_column) is not None
                }
                for row in rows:
                    value = row.get(child_column)
                    if value is not None and _canonical_json(value) not in parent_values:
                        raise ValueError(f"归档表 {table} 存在未包含的父记录引用")

    async def export_project(self, db: AsyncSession, project_id: str) -> dict[str, Any]:
        schema = await _schema(db)
        if "projects" not in schema:
            raise ValueError("projects table is missing")
        project_rows = await _select_rows(
            db,
            "projects",
            where="id = :project_id",
            params={"project_id": project_id},
            **{key: schema["projects"][key] for key in ("columns", "primary_keys")},
        )
        if not project_rows:
            raise ValueError("项目不存在")

        selected: dict[str, list[dict[str, Any]]] = {"projects": project_rows}
        for table, info in schema.items():
            if table == "projects" or "project_id" not in info["columns"]:
                continue
            rows = await _select_rows(
                db,
                table,
                where="project_id = :project_id",
                params={"project_id": project_id},
                columns=info["columns"],
                primary_keys=info["primary_keys"],
            )
            if rows:
                selected[table] = rows

        # Include dependent rows without project_id (workflow steps, chat
        # messages, FBI issue details, etc.) by following declared FKs.
        changed = True
        while changed:
            changed = False
            for table, info in schema.items():
                for fk in info["foreign_keys"]:
                    parent = str(fk[2])
                    child_column = str(fk[3])
                    parent_column = str(fk[4])
                    parent_rows = selected.get(parent, [])
                    if not parent_rows or child_column not in info["columns"]:
                        continue
                    values = [row.get(parent_column) for row in parent_rows if row.get(parent_column) is not None]
                    for offset in range(0, len(values), 400):
                        batch = values[offset:offset + 400]
                        if not batch:
                            continue
                        params = {f"v{index}": _db_value(value) for index, value in enumerate(batch)}
                        placeholders = ",".join(f":v{index}" for index in range(len(batch)))
                        rows = await _select_rows(
                            db,
                            table,
                            where=f"{_quote(child_column)} IN ({placeholders})",
                            params=params,
                            columns=info["columns"],
                            primary_keys=info["primary_keys"],
                        )
                        if rows or table in selected:
                            before = len(selected.get(table, []))
                            selected[table] = _merge_rows(
                                selected.get(table, []),
                                rows,
                                info["primary_keys"],
                            )
                            if len(selected[table]) > before:
                                changed = True

        ordered_tables = {name: selected[name] for name in sorted(selected)}
        project_name = str(project_rows[0].get("name") or "")
        return {
            "format": ARCHIVE_FORMAT,
            "version": ARCHIVE_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": settings.app_version,
            "project": {"id": project_id, "name": project_name},
            "checksum": {"algorithm": "sha256", "tables": _checksum(ordered_tables)},
            "counts": {name: len(rows) for name, rows in ordered_tables.items()},
            "tables": ordered_tables,
        }

    @staticmethod
    def validate_archive(archive: dict[str, Any], project_id: str | None = None) -> None:
        if archive.get("format") != ARCHIVE_FORMAT or int(archive.get("version", 0) or 0) != ARCHIVE_VERSION:
            raise ValueError("不支持的项目归档格式或版本")
        tables = archive.get("tables")
        if not isinstance(tables, dict) or not isinstance(tables.get("projects"), list):
            raise ValueError("项目归档缺少关系数据")
        expected = str((archive.get("checksum") or {}).get("tables") or "")
        if not expected or _checksum(tables) != expected:
            raise ValueError("项目归档校验和不匹配，文件可能损坏或被修改")
        archived_id = str((archive.get("project") or {}).get("id") or "")
        if project_id is not None and archived_id != str(project_id):
            raise ValueError("归档项目与当前项目不一致")

    async def preview_restore(
        self,
        db: AsyncSession,
        *,
        project_id: str,
        archive: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_archive(archive, project_id)
        current = await self.export_project(db, project_id)
        archive_counts = archive.get("counts") or {}
        current_counts = current.get("counts") or {}
        table_names = sorted(set(archive_counts) | set(current_counts))
        return {
            "valid": True,
            "project_id": project_id,
            "project_name": (archive.get("project") or {}).get("name", ""),
            "checksum": (archive.get("checksum") or {}).get("tables", ""),
            "current_checksum": (current.get("checksum") or {}).get("tables", ""),
            "content_changed": (archive.get("checksum") or {}).get("tables")
            != (current.get("checksum") or {}).get("tables"),
            "differences": [
                {
                    "table": name,
                    "current": int(current_counts.get(name, 0) or 0),
                    "archive": int(archive_counts.get(name, 0) or 0),
                }
                for name in table_names
                if int(current_counts.get(name, 0) or 0) != int(archive_counts.get(name, 0) or 0)
            ],
            "requires_confirmation": True,
        }

    async def restore_project(
        self,
        db: AsyncSession,
        *,
        project_id: str,
        archive: dict[str, Any],
        confirmation: str,
        backup_directory: Path | None = None,
    ) -> dict[str, Any]:
        self.validate_archive(archive, project_id)
        project_name = str((archive.get("project") or {}).get("name") or "")
        if confirmation not in {project_id, project_name}:
            raise ValueError("恢复确认不匹配；请输入项目名称或项目 ID")
        schema = await _schema(db)
        tables = archive["tables"]
        unknown = sorted(set(tables) - set(schema))
        if unknown:
            raise ValueError("当前版本缺少归档数据表：" + ", ".join(unknown))
        self._validate_restore_rows(schema, tables, project_id)

        current = await self.export_project(db, project_id)
        backup_dir = backup_directory or (Path(settings.data_dir) / "backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_dir / f"project-{project_id}-pre-restore-{stamp}.json"
        backup_path.write_text(_canonical_json(current), encoding="utf-8")

        try:
            await db.execute(text("PRAGMA defer_foreign_keys = ON"))
            await db.execute(text("DELETE FROM projects WHERE id = :project_id"), {"project_id": project_id})
            for table in sorted(tables, key=lambda name: (name != "projects", name)):
                info = schema[table]
                columns = info["columns"]
                for row in tables[table]:
                    row_columns = [column for column in columns if column in row]
                    column_sql = ",".join(_quote(column) for column in row_columns)
                    value_sql = ",".join(f":p{index}" for index in range(len(row_columns)))
                    params = {f"p{index}": _db_value(row[column]) for index, column in enumerate(row_columns)}
                    await db.execute(
                        text(f"INSERT INTO {_quote(table)} ({column_sql}) VALUES ({value_sql})"),
                        params,
                    )

            violations = [tuple(row) for row in (await db.execute(text("PRAGMA foreign_key_check"))).all()]
            if violations:
                raise ValueError(f"恢复后外键校验失败，共 {len(violations)} 条")
            await db.commit()
        except Exception as exc:
            await db.rollback()
            if isinstance(exc, ValueError):
                raise
            raise ValueError("项目归档恢复失败，数据库已回滚") from exc
        return {
            "restored": True,
            "project_id": project_id,
            "checksum": (archive.get("checksum") or {}).get("tables", ""),
            "backup_path": str(backup_path),
            "counts": archive.get("counts") or {},
        }
