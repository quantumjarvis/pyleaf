"""Fixtures for the pyleaf-data tests (builders live in ``data_ctx``)."""

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pyleaf.core import ApplicationContext
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_ctx import build_async_ctx, build_sync_ctx
from data_models import Base


@pytest.fixture()
def sqlite_sessionmaker(tmp_path: Path) -> Iterator[sessionmaker]:  # type: ignore[type-arg]
    """A sessionmaker on a file-backed sqlite DB (tables pre-created)."""
    engine = create_engine(f"sqlite:///{tmp_path}/data.db")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture()
def aiosqlite(tmp_path: Path) -> Iterator[tuple[Any, Any]]:
    """(async engine, async_sessionmaker) on a file-backed aiosqlite DB.

    Tables are NOT pre-created — async tests create them inside the
    event loop via ``engine.begin() + run_sync(Base.metadata.create_all)``.
    """
    pytest.importorskip("aiosqlite")
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/data.db")
    yield engine, async_sessionmaker(bind=engine)
    asyncio.run(engine.dispose())


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """Session-scoped postgres testcontainer; skips cleanly without docker.

    Yields ``user:pass@host:port/db`` so callers pick the driver
    (``+psycopg`` for sync, ``+asyncpg`` for async). Mirrors the fixture
    in the pyleaf-starter-sqlalchemy tests.
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
def ctx(sqlite_sessionmaker: sessionmaker) -> Iterator[ApplicationContext]:  # type: ignore[type-arg]
    """Sync application context with ``ItemRepo`` registered."""
    context = build_sync_ctx(sqlite_sessionmaker)
    yield context
    context.close()


@pytest.fixture()
def actx(aiosqlite: tuple[Any, Any]) -> Iterator[ApplicationContext]:
    """Async application context with ``AsyncItemRepo`` registered."""
    _engine, sm = aiosqlite
    context = build_async_ctx(sm)
    yield context
    context.close()
