"""Regression tests for B2 (Tier-1 dedup must not wedge a failed upload).

A failed/cancelled encrypt or upload leaves a ``Video`` row with
``original_hash`` set but no completed upload. The old Tier-1 dedup matched
that row and set ``skip_upload``, so the next scheduled run (no ``--force``)
permanently skipped the video. These tests pin the new contract: a dedup
match must prove BOTH a non-null CID/piece CID AND a completed upload_jobs
row.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from haven_cli.database.models import Base, UploadJob, Video
from haven_cli.pipeline.steps.ingest_step import IngestStep


@pytest.fixture
def db():
    """In-memory SQLite DB with the full schema, patching get_db_session."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def fake_session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ingest_step imports get_db_session inside the method.
    import haven_cli.database.connection as conn
    original = conn.get_db_session
    conn.get_db_session = fake_session
    try:
        yield Session
    finally:
        conn.get_db_session = original
        engine.dispose()


def _video(Session, *, original_hash, cid=None, piece_cid=None, arkiv_key=None):
    s = Session()
    v = Video(
        source_path="/tmp/x.mp4",
        title="x",
        original_hash=original_hash,
        cid=cid,
        piece_cid=piece_cid,
        arkiv_entity_key=arkiv_key,
    )
    s.add(v)
    s.commit()
    vid = v.id
    s.close()
    return vid


def _job(Session, video_id, status):
    s = Session()
    s.add(UploadJob(video_id=video_id, target="filecoin", status=status))
    s.commit()
    s.close()


class TestDedupRequiresCompletedUpload:
    @pytest.mark.asyncio
    async def test_failed_upload_row_does_not_match(self, db):
        """A row with a failed upload job and no CID must NOT dedup-skip."""
        vid = _video(db, original_hash="a" * 64)
        _job(db, vid, "failed")

        step = IngestStep()
        match = await step._check_original_hash_dedup("a" * 64)
        assert match is None, "failed upload must fall through to retry"

    @pytest.mark.asyncio
    async def test_uploading_row_does_not_match(self, db):
        """A stale 'uploading' row (cancelled run) must NOT dedup-skip."""
        vid = _video(db, original_hash="b" * 64)
        _job(db, vid, "uploading")

        step = IngestStep()
        match = await step._check_original_hash_dedup("b" * 64)
        assert match is None

    @pytest.mark.asyncio
    async def test_cid_but_no_completed_job_does_not_match(self, db):
        """A CID alone (e.g. legacy/failed state) is not proof of upload."""
        _video(db, original_hash="c" * 64, cid="bafysomething")
        # no upload_jobs row at all

        step = IngestStep()
        match = await step._check_original_hash_dedup("c" * 64)
        assert match is None

    @pytest.mark.asyncio
    async def test_completed_job_but_no_cid_does_not_match(self, db):
        """A completed job without a persisted CID is not proof either."""
        vid = _video(db, original_hash="d" * 64, cid=None)
        _job(db, vid, "completed")

        step = IngestStep()
        match = await step._check_original_hash_dedup("d" * 64)
        assert match is None

    @pytest.mark.asyncio
    async def test_completed_upload_does_match(self, db):
        """A genuine completed upload (CID + completed job) still dedups."""
        vid = _video(
            db, original_hash="e" * 64,
            cid="bafyreal", piece_cid="bafkpiece",
            arkiv_key="key-e",
        )
        _job(db, vid, "completed")

        step = IngestStep()
        match = await step._check_original_hash_dedup("e" * 64)
        assert match is not None
        assert match.cid == "bafyreal"
        assert match.arkiv_entity_key == "key-e"

    @pytest.mark.asyncio
    async def test_failed_newest_row_does_not_hide_older_success(self, db):
        """A failed retry (newest) must not mask an older completed upload."""
        ok = _video(
            db, original_hash="f" * 64,
            cid="bafyok", piece_cid="bafkpiece",
        )
        _job(db, ok, "completed")
        failed = _video(db, original_hash="f" * 64, cid=None)
        _job(db, failed, "failed")
        assert ok != failed

        step = IngestStep()
        match = await step._check_original_hash_dedup("f" * 64)
        assert match is not None
        assert match.id == ok
