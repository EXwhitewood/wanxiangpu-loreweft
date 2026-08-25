import os
import asyncio
import uuid
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_USING_DEFAULT_TEST_DATABASE = "TEST_DATABASE_URL" not in os.environ
_DEFAULT_SQLITE_TEST_DB = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "test"
    / f"loreweft_{os.getpid()}_{uuid.uuid4().hex}_test.db"
)
_DEFAULT_SQLITE_TEST_URL = f"sqlite+aiosqlite:///{_DEFAULT_SQLITE_TEST_DB.as_posix()}"

SQLALCHEMY_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    _DEFAULT_SQLITE_TEST_URL,
)

_TEST_DB_URL = make_url(SQLALCHEMY_DATABASE_URL)
_IS_SQLITE_TEST = _TEST_DB_URL.get_backend_name() == "sqlite"

# Services such as StateManager and the FTS adapter open their own connection
# instead of using the fixture session. Point every SQLite access path at the
# same isolated test database before importing application database modules.
os.environ["DATABASE_URL"] = SQLALCHEMY_DATABASE_URL
if _IS_SQLITE_TEST and _TEST_DB_URL.database:
    os.environ["SQLITE_PATH"] = _TEST_DB_URL.database
    os.environ["SQLITE_FTS_PATH"] = _TEST_DB_URL.database

from app.db.db_models import Base


def _is_safe_test_database() -> bool:
    database = _TEST_DB_URL.database or ""
    if _IS_SQLITE_TEST:
        if database == ":memory:":
            return True
        return Path(database).stem.endswith("_test")
    return database.endswith("_test")


if not _is_safe_test_database():
    raise RuntimeError("Refusing to run destructive tests outside an isolated *_test database")


async def _reset_schema(conn):
    if _IS_SQLITE_TEST:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        return

    await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
    await conn.execute(text("CREATE SCHEMA public"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


async def _close_service_sqlite_connections() -> None:
    """Release singleton connections opened outside SQLAlchemy fixtures."""
    from app.services.sqlite_fts_service import SqliteFtsService
    from app.services.state_manager import StateManager

    await StateManager._sqlite_manager.close()
    await SqliteFtsService().close()


async def _remove_default_sqlite_test_files(db_path: Path) -> None:
    candidates = [Path(f"{db_path}{suffix}") for suffix in ("", "-shm", "-wal", "-journal")]
    remaining: list[Path] = []
    for _attempt in range(20):
        remaining = []
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                candidate.unlink()
            except PermissionError:
                remaining.append(candidate)
        if not remaining:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        "SQLite test artifacts remained locked after teardown: "
        + ", ".join(str(path) for path in remaining)
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    if _IS_SQLITE_TEST and _TEST_DB_URL.database and _TEST_DB_URL.database != ":memory:":
        db_path = Path(_TEST_DB_URL.database)
        db_path.parent.mkdir(parents=True, exist_ok=True)

    e = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 30} if _IS_SQLITE_TEST else {},
    )
    yield e
    await _close_service_sqlite_connections()
    async with e.begin() as conn:
        await _reset_schema(conn)
    await e.dispose()
    await _close_service_sqlite_connections()
    if _USING_DEFAULT_TEST_DATABASE and _IS_SQLITE_TEST and _TEST_DB_URL.database and _TEST_DB_URL.database != ":memory:":
        db_path = Path(_TEST_DB_URL.database)
        await _remove_default_sqlite_test_files(db_path)


@pytest_asyncio.fixture
async def db(engine) -> AsyncGenerator[AsyncSession, None]:
    async with engine.begin() as conn:
        await _reset_schema(conn)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def project_in_db(db, project_id) -> uuid.UUID:
    from app.db.db_models import Project
    p = Project(id=project_id, name="测试项目", description="测试用")
    db.add(p)
    await db.commit()
    return project_id
