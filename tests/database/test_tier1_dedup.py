"""Tests for Tier 1 pre-upload deduplication primitives.

Covers:
* ``Video.original_hash`` schema column (presence + index lookup behaviour)
* ``VideoRepository.get_by_original_hash``
* ``haven_cli.database.connection._apply_inplace_migrations`` idempotence

The Tier 1 dedup design is documented in
``docs/BATCH_SYNC_TIER1_PREUPLOAD_DEDUP.md``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haven_cli.database.connection import _apply_inplace_migrations
from haven_cli.database.models import Base, Video
from haven_cli.database.repositories import VideoRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    """Fresh in-memory SQLite engine with the full schema applied."""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Schema / column presence
# ---------------------------------------------------------------------------


class TestSchema:
    """The ``original_hash`` column must exist and be indexed."""

    def test_original_hash_column_exists(self, engine: Engine) -> None:
        cols = {c["name"] for c in inspect(engine).get_columns("videos")}
        assert "original_hash" in cols, (
            "Tier 1 dedup requires `videos.original_hash`. "
            "See docs/BATCH_SYNC_TIER1_PREUPLOAD_DEDUP.md."
        )

    def test_original_hash_column_is_indexed(self, engine: Engine) -> None:
        # The index lets ``get_by_original_hash`` stay O(log n).
        indexed_cols: set[str] = set()
        for idx in inspect(engine).get_indexes("videos"):
            indexed_cols.update(idx["column_names"])
        assert "original_hash" in indexed_cols, (
            "Tier 1 dedup requires an index on `videos.original_hash` so "
            "the IngestStep lookup stays sub-millisecond on large catalogs."
        )

    def test_original_hash_in_to_dict(self, session: Session) -> None:
        video = Video(
            source_path="/t/sample.mp4",
            title="t",
            original_hash="a" * 64,
        )
        session.add(video)
        session.commit()
        session.refresh(video)
        d = video.to_dict()
        assert d["original_hash"] == "a" * 64


# ---------------------------------------------------------------------------
# VideoRepository.get_by_original_hash
# ---------------------------------------------------------------------------


class TestGetByOriginalHash:
    """The Tier 1 dedup lookup must behave correctly across edge cases."""

    def test_returns_none_for_empty_hash(self, session: Session) -> None:
        assert VideoRepository(session).get_by_original_hash("") is None

    def test_returns_none_for_no_match(self, session: Session) -> None:
        # Insert a row with a different hash.
        session.add(
            Video(source_path="/a.mp4", title="a", original_hash="b" * 64)
        )
        session.commit()
        assert (
            VideoRepository(session).get_by_original_hash("c" * 64) is None
        )

    def test_returns_match(self, session: Session) -> None:
        target = "d" * 64
        session.add(Video(source_path="/d.mp4", title="d", original_hash=target))
        session.commit()
        result = VideoRepository(session).get_by_original_hash(target)
        assert result is not None
        assert result.original_hash == target

    def test_returns_most_recent_when_multiple_match(
        self, session: Session
    ) -> None:
        """When two rows share a hash, return the most recent one.

        Why: re-archives that get duplicated due to legacy NULL rows
        should resolve to the newest entry, which has the most
        up-to-date ``arkiv_entity_key``.
        """
        target = "e" * 64
        old = Video(
            source_path="/old.mp4",
            title="old",
            original_hash=target,
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        new = Video(
            source_path="/new.mp4",
            title="new",
            original_hash=target,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        session.add_all([old, new])
        session.commit()

        result = VideoRepository(session).get_by_original_hash(target)
        assert result is not None
        assert result.source_path == "/new.mp4", (
            "get_by_original_hash should return the most recent matching "
            "row so the dedup match reflects current Arkiv state."
        )

    def test_null_rows_excluded(self, session: Session) -> None:
        """Legacy rows with NULL ``original_hash`` must not match.

        This preserves backward compat: pre-Tier-1 rows simply fall
        through to the normal upload path.
        """
        session.add(
            Video(source_path="/legacy.mp4", title="legacy", original_hash=None)
        )
        session.commit()
        # Even with empty string, NULL must not be returned.
        assert VideoRepository(session).get_by_original_hash("") is None


# ---------------------------------------------------------------------------
# In-place migration shim
# ---------------------------------------------------------------------------


class TestInPlaceMigrationShim:
    """The ``_apply_inplace_migrations`` helper must be idempotent.

    The shim runs every ``create_tables`` call, so it must tolerate
    being executed against a database that already has the column.
    """

    def test_shim_is_idempotent(self, engine: Engine) -> None:
        """Running the shim twice must not raise."""
        _apply_inplace_migrations(engine)  # first call (column exists)
        _apply_inplace_migrations(engine)  # second call (no-op)
        # If we got here without raising, idempotence holds.

    def test_shim_adds_column_to_legacy_database(self) -> None:
        """A database without ``original_hash`` should get the column added."""
        # Create a Video table without the new column, simulating an old DB.
        eng = create_engine("sqlite:///:memory:")
        with eng.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE videos (
                    id INTEGER PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        # Pre-condition: column does not exist yet.
        cols_before = {
            c["name"] for c in inspect(eng).get_columns("videos")
        }
        assert "original_hash" not in cols_before

        # Apply the shim.
        _apply_inplace_migrations(eng)

        # Post-condition: column exists and is indexed.
        cols_after = {c["name"] for c in inspect(eng).get_columns("videos")}
        assert "original_hash" in cols_after

        indexed_cols: set[str] = set()
        for idx in inspect(eng).get_indexes("videos"):
            indexed_cols.update(idx["column_names"])
        assert "original_hash" in indexed_cols

        eng.dispose()
