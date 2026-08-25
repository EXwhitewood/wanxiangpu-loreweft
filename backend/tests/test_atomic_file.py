from pathlib import Path

import pytest

from app.utils import atomic_file


def test_atomic_write_replaces_complete_file(tmp_path: Path):
    target = tmp_path / "settings.json"
    target.write_text("old", encoding="utf-8")

    atomic_file.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_failure_preserves_previous_file(tmp_path: Path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_file.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_file.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.tmp"))
