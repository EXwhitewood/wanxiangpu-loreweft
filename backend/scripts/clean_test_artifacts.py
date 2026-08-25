"""Safely report or remove SQLite databases created by backend tests."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


TEST_DIRECTORY = (Path(__file__).resolve().parents[1] / "data" / "test").resolve()
ARTIFACT_PATTERN = re.compile(
    r"^(?:dreamweaver_test|(?:dreamweaver|loreweft)_\d+_[0-9a-f]+_test)\.db"
    r"(?:-(?:wal|shm|journal))?$"
)
SIDECAR_PATTERN = re.compile(
    r"^(?:dreamweaver_test|(?:dreamweaver|loreweft)_\d+_[0-9a-f]+_test)"
    r"\.db-(?:wal|shm|journal)$"
)


def test_artifacts() -> list[Path]:
    """Return only files that match the test database naming contract."""

    if not TEST_DIRECTORY.exists():
        return []
    candidates: list[Path] = []
    for path in TEST_DIRECTORY.iterdir():
        if not path.is_file() or not ARTIFACT_PATTERN.fullmatch(path.name):
            continue
        resolved = path.resolve()
        if resolved.parent != TEST_DIRECTORY:
            raise RuntimeError(f"unsafe test artifact path: {resolved}")
        candidates.append(resolved)
    # Remove SQLite sidecars before their main database. This also makes a lock
    # failure visible before the main file can be removed.
    return sorted(
        candidates,
        key=lambda item: (not SIDECAR_PATTERN.fullmatch(item.name), item.name),
    )


def orphaned_sidecars() -> list[Path]:
    if not TEST_DIRECTORY.exists():
        return []
    candidates: list[Path] = []
    for path in TEST_DIRECTORY.iterdir():
        if not path.is_file() or not SIDECAR_PATTERN.fullmatch(path.name):
            continue
        resolved = path.resolve()
        if resolved.parent != TEST_DIRECTORY:
            raise RuntimeError(f"unsafe test artifact path: {resolved}")
        main_database = Path(str(resolved).rsplit("-", 1)[0])
        if main_database.exists():
            continue
        candidates.append(resolved)
    return sorted(candidates, key=lambda item: item.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete verified orphan sidecars; without this flag the command is read-only",
    )
    args = parser.parse_args()
    candidates = test_artifacts()
    removed: list[str] = []
    if args.apply:
        for path in candidates:
            path.unlink()
            removed.append(path.name)
    print(
        json.dumps(
            {
                "test_directory": str(TEST_DIRECTORY),
                "mode": "apply" if args.apply else "dry-run",
                "verified_artifacts": len(candidates),
                "verified_orphan_sidecars": len(orphaned_sidecars()),
                "removed": len(removed),
                "remaining_artifacts": len(test_artifacts()),
                "remaining_orphan_sidecars": len(orphaned_sidecars()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
