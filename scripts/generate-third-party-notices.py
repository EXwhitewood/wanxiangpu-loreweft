from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTICE_PATH = ROOT / "THIRD_PARTY_NOTICES.md"
MARKER = "<!-- GENERATED-DEPENDENCIES: scripts/generate-third-party-notices.py replaces this marker. -->"
PYTHON_LICENSE_OVERRIDES = {
    # These locked releases publish their license through PyPI's
    # license_expression/classifier or bundled license file, but older local
    # metadata readers may not surface it consistently.
    "jieba": "MIT",
    "joblib": "BSD-3-Clause",
    "narwhals": "MIT",
    "ruptures": "BSD-2-Clause",
    "scikit-learn": "BSD-3-Clause",
    "scipy": "BSD-3-Clause",
    "threadpoolctl": "BSD-3-Clause",
}


def compact(value: str | None) -> str:
    if not value:
        return "UNKNOWN"
    first_line = next((line.strip() for line in value.splitlines() if line.strip()), "UNKNOWN")
    return first_line.replace("|", "\\|")[:180]


def python_dependencies() -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for raw in (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if not match:
            raise RuntimeError(f"Unsupported requirement line: {line}")
        name, locked_version = match.groups()
        try:
            metadata = importlib.metadata.metadata(name)
            installed_version = importlib.metadata.version(name)
            if installed_version != locked_version:
                raise RuntimeError(
                    f"Installed {name} version {installed_version} does not match locked {locked_version}. "
                    "Install backend/requirements.txt in an isolated environment before generating notices."
                )
            license_name = (
                PYTHON_LICENSE_OVERRIDES.get(name.lower())
                or metadata.get("License-Expression")
                or metadata.get("License")
                or next(
                    (
                        classifier.rsplit("::", 1)[-1].strip()
                        for classifier in metadata.get_all("Classifier", [])
                        if classifier.startswith("License ::")
                    ),
                    None,
                )
            )
        except importlib.metadata.PackageNotFoundError:
            license_name = PYTHON_LICENSE_OVERRIDES.get(name.lower())
        result.append((name, locked_version, compact(license_name)))
    return sorted(result, key=lambda item: item[0].lower())


def npm_dependencies() -> list[tuple[str, str, str]]:
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    result: dict[tuple[str, str], str] = {}
    for package_path, info in lock.get("packages", {}).items():
        if "node_modules/" not in package_path:
            continue
        name = package_path.rsplit("node_modules/", 1)[-1]
        version = str(info.get("version", "UNKNOWN"))
        result[(name, version)] = compact(info.get("license"))
    return [(name, version, license_name) for (name, version), license_name in sorted(result.items())]


def rust_dependencies() -> list[tuple[str, str, str]]:
    completed = subprocess.run(
        [
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--locked",
            "--manifest-path",
            str(ROOT / "src-tauri" / "Cargo.toml"),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    metadata = json.loads(completed.stdout)
    result = []
    for package in metadata["packages"]:
        if package["name"] == "loreweft" and package.get("source") is None:
            continue
        result.append((package["name"], package["version"], compact(package.get("license"))))
    return sorted(set(result), key=lambda item: (item[0].lower(), item[1]))


def render_table(title: str, rows: list[tuple[str, str, str]]) -> str:
    lines = [f"## {title}", "", "| Package | Version | Declared license |", "|---|---:|---|"]
    lines.extend(f"| `{name}` | `{version}` | {license_name} |" for name, version, license_name in rows)
    return "\n".join(lines)


def main() -> None:
    base = NOTICE_PATH.read_text(encoding="utf-8")
    if MARKER not in base:
        raise RuntimeError(f"Generation marker is missing from {NOTICE_PATH}")
    generated = "\n\n".join(
        [
            render_table("Python", python_dependencies()),
            render_table("JavaScript / npm", npm_dependencies()),
            render_table("Rust / Cargo", rust_dependencies()),
        ]
    )
    NOTICE_PATH.write_text(base.split(MARKER, 1)[0] + MARKER + "\n\n" + generated + "\n", encoding="utf-8")

    unknown = [line for line in generated.splitlines() if line.endswith("| UNKNOWN |")]
    print(f"Generated {NOTICE_PATH} with {len(unknown)} UNKNOWN license entries.")
    if unknown:
        for line in unknown:
            print(line)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
