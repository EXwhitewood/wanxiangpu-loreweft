from app.config import settings
from app.api.settings import _system_info_payload


def test_tauri_v2_and_browser_dev_origins_are_allowed_explicitly():
    assert "http://tauri.localhost" in settings.cors_origins
    assert "http://localhost:5173" in settings.cors_origins
    assert "http://127.0.0.1:5173" in settings.cors_origins
    assert "*" not in settings.cors_origins


def test_system_info_reports_the_actual_runtime(monkeypatch):
    monkeypatch.setattr("app.api.settings.app_settings.app_runtime", "tauri")
    assert _system_info_payload()["is_tauri"] is True

    monkeypatch.setattr("app.api.settings.app_settings.app_runtime", "browser")
    assert _system_info_payload()["is_tauri"] is False
