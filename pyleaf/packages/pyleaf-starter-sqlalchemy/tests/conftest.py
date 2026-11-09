import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sa_models import Base


@pytest.fixture()
def sqlite_sessionmaker(tmp_path: Path) -> Iterator[sessionmaker]:  # type: ignore[type-arg]
    """A sessionmaker on a file-backed sqlite DB (sees committed rows)."""
    engine = create_engine(f"sqlite:///{tmp_path}/tx.db")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """Session-scoped postgres testcontainer; skips cleanly without docker.

    Yields ``user:pass@host:port/db`` so callers pick the driver
    (``+psycopg`` for sync, ``+asyncpg`` for async).
    """
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"postgres container unavailable (docker running?): {exc}")
    # get_connection_url() embeds a driver tag (e.g. +psycopg2); strip the
    # scheme so callers choose their own.
    dsn = container.get_connection_url().split("://", 1)[1]
    yield dsn
    container.stop()


@pytest.fixture()
def pg_engine(postgres_dsn: str) -> Iterator[Engine]:
    """Fresh-schema sync engine on the container (QueuePool)."""
    engine = create_engine(f"postgresql+psycopg://{postgres_dsn}")
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"postgres unreachable: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_sessionmaker(pg_engine: Engine) -> sessionmaker:  # type: ignore[type-arg]
    return sessionmaker(bind=pg_engine)


@pytest.fixture()
def pg_async_sessionmaker(postgres_dsn: str, pg_engine: Engine) -> Any:
    """async_sessionmaker on the same postgres DB (tables pre-created)."""
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"postgresql+asyncpg://{postgres_dsn}")
    return async_sessionmaker(bind=engine)


@pytest.fixture()
def aiosqlite_sessionmaker(tmp_path: Path) -> Any:
    """async_sessionmaker on a file-backed aiosqlite DB (no tables — tests
    create them inside the event loop via ``_create_tables``)."""
    pytest.importorskip("aiosqlite")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/tx.db")
    return async_sessionmaker(bind=engine)
