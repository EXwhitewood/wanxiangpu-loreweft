from __future__ import annotations

from sqlalchemy import text

from app.services.database_integrity_service import (
    inspect_sqlite_foreign_keys,
    repair_sqlite_foreign_keys,
)


async def test_repairs_only_declared_cascade_orphans_and_reports_clean(engine, db):
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys = OFF"))
        await conn.execute(
            text(
                "INSERT INTO chapters(id, project_id, chapter_number, title, content, status) "
                "VALUES('orphan-chapter', 'missing-project', 1, 'orphan', 'body', 'draft')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO detail_seeds(id, project_id, chapter_id, entity_id, fact) "
                "VALUES('orphan-seed', 'missing-project', 'orphan-chapter', 'x', 'fact')"
            )
        )

    before = await inspect_sqlite_foreign_keys(engine)
    assert before["ok"] is False
    assert before["violations"] >= 2

    report = await repair_sqlite_foreign_keys(engine, create_backup=False)

    assert report.ok is True
    assert report.before_violations >= 2
    assert report.remaining_violations == 0
    assert report.cascaded_rows >= 1
    async with engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT COUNT(*) FROM chapters WHERE id='orphan-chapter'"))
        ).scalar_one() == 0
        assert (
            await conn.execute(text("SELECT COUNT(*) FROM detail_seeds WHERE id='orphan-seed'"))
        ).scalar_one() == 0


async def test_clean_database_is_a_noop_without_backup(engine, db):
    report = await repair_sqlite_foreign_keys(engine, create_backup=True)

    assert report.ok is True
    assert report.before_violations == 0
    assert report.backup_path == ""
