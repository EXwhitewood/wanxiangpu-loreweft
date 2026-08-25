from pathlib import Path

from pydantic_settings import BaseSettings


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SQLITE_FILE = (_BACKEND_ROOT / "data" / "loreweft.db").resolve()


class Settings(BaseSettings):
    app_version: str = "0.3.5"
    database_url: str = f"sqlite+aiosqlite:///{_DEFAULT_SQLITE_FILE.as_posix()}"
    secret_key: str = "change-me-in-production"
    debug: bool = False
    # Browser development and the packaged Tauri v2 WebView are distinct
    # origins.  Keep this allow-list explicit because the API also exposes
    # local model credentials.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://tauri.localhost",
    ]

    sqlite_path: str = str(_DEFAULT_SQLITE_FILE)
    # Derived FTS tables live beside the canonical SQLite tables.  This avoids
    # an empty side database silently diverging from detail_seeds.
    sqlite_fts_path: str = str(_DEFAULT_SQLITE_FILE)
    data_dir: str = str((_BACKEND_ROOT / "data").resolve())
    cache_dir: str = str((_BACKEND_ROOT / "data" / "cache").resolve())
    app_runtime: str = "browser"

    llm_trust_env_proxy: bool = False
    reader_corpus_mode: str = "off"  # "off" | "on" | "guidance_only"

    model_config = {
        "env_file": str(_BACKEND_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()


def _absolute_backend_path(value: str) -> str:
    if not value or value == ":memory:":
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((_BACKEND_ROOT / path).resolve())


settings.sqlite_path = _absolute_backend_path(settings.sqlite_path)
settings.sqlite_fts_path = _absolute_backend_path(settings.sqlite_fts_path)
settings.data_dir = _absolute_backend_path(settings.data_dir)
settings.cache_dir = _absolute_backend_path(settings.cache_dir)
for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
    if settings.database_url.startswith(prefix):
        raw_path = settings.database_url[len(prefix):]
        if raw_path != ":memory:":
            settings.database_url = prefix + Path(_absolute_backend_path(raw_path)).as_posix()
        break
