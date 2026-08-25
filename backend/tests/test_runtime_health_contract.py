import os

import pytest
from fastapi import Response

from app import main


class _SessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _query):
        return None


@pytest.mark.asyncio
async def test_health_is_ok_only_when_startup_recovery_and_database_are_ok(monkeypatch):
    main.app.state.startup_checks = {
        "database_integrity": {"ok": True, "remaining_violations": 0},
        "validator_retry_recovery": {"ok": True},
        "chapter_commit_recovery": {"ok": True},
        "workflow_interruption_recovery": {"ok": True},
    }
    monkeypatch.setattr("app.db.db_models.async_session", lambda: _SessionContext())
    response = Response()

    payload = await main.health_check(response)

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["process_id"] == os.getpid()
    assert payload["checks"]["database"] == {"ok": True}


@pytest.mark.asyncio
async def test_health_reports_degraded_instead_of_hiding_recovery_failure(monkeypatch):
    main.app.state.startup_checks = {
        "validator_retry_recovery": {"ok": False, "error": "recovery failed"},
    }
    monkeypatch.setattr("app.db.db_models.async_session", lambda: _SessionContext())
    response = Response()

    payload = await main.health_check(response)

    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["process_id"] == os.getpid()
    assert payload["checks"]["validator_retry_recovery"]["ok"] is False
