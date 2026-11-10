"""Tests for B6 — stale 'uploading' rows and jobs list filtering."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from haven_cli.database.models import Base, UploadJob
from haven_cli.pipeline.steps.upload_step import sweep_stale_upload_jobs


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    import haven_cli.database.connection as conn
    monkeypatch.setattr(conn, "get_db_session", _make_ctx(Session))
    # upload_step imported get_db_session at module import time.
    import haven_cli.pipeline.steps.upload_step as us
    monkeypatch.setattr(us, "get_db_session", _make_ctx(Session))
    yield Session
    engine.dispose()


def _make_ctx(Session):
    from contextlib import contextmanager

    @contextmanager
    def ctx():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
    return ctx


def _add(Session, status, created_at):
    s = Session()
    job = UploadJob(video_id=1, target="filecoin", status=status)
    job.created_at = created_at
    s.add(job)
    s.commit()
    jid = job.id
    s.close()
    return jid


class TestStartupSweep:
    def test_old_uploading_rows_marked_failed(self, db):
        boot = datetime.now(timezone.utc)
        old = boot - timedelta(hours=1)
        jid = _add(db, "uploading", old)

        swept = sweep_stale_upload_jobs(boot)
        assert swept == 1

        s = db()
        job = s.query(UploadJob).filter(UploadJob.id == jid).one()
        assert job.status == "failed"
        assert "orphaned" in (job.error_message or "")
        assert job.completed_at is not None
        s.close()

    def test_recent_uploading_rows_left_alone(self, db):
        boot = datetime.now(timezone.utc)
        jid = _add(db, "uploading", boot + timedelta(seconds=1))

        swept = sweep_stale_upload_jobs(boot)
        assert swept == 0
        s = db()
        assert s.query(UploadJob).filter(UploadJob.id == jid).one().status == "uploading"
        s.close()

    def test_completed_rows_untouched(self, db):
        boot = datetime.now(timezone.utc)
        jid = _add(db, "completed", boot - timedelta(days=1))
        sweep_stale_upload_jobs(boot)
        s = db()
        assert s.query(UploadJob).filter(UploadJob.id == jid).one().status == "completed"
        s.close()


class TestJobsListStatus:
    def test_failed_status_shows_upload_jobs(self, db, monkeypatch):
        from haven_cli.cli.jobs import app

        boot = datetime.now(timezone.utc)
        jid = _add(db, "failed", boot - timedelta(minutes=1))
        s = db()
        j = s.query(UploadJob).filter(UploadJob.id == jid).one()
        j.error_message = "Abandoned: superseded by a new upload attempt"
        s.commit()
        s.close()

        runner = CliRunner()
        result = runner.invoke(app, ["list", "--status", "failed"])
        assert result.exit_code == 0, result.output
        assert "Upload Jobs" in result.output
        assert "Abandoned" in result.output

    def test_unknown_status_is_an_error(self, db):
        from haven_cli.cli.jobs import app

        runner = CliRunner()
        result = runner.invoke(app, ["list", "--status", "bogus"])
        assert result.exit_code == 1
        assert "Unknown status" in result.output

    def test_json_output_has_full_ids(self, db, monkeypatch):
        import json
        from unittest.mock import MagicMock, patch

        from haven_cli.cli.jobs import app

        full_id = "cf118882-1234-5678-9abc-def012345678"
        fake_job = MagicMock()
        fake_job.job_id = full_id
        fake_job.enabled = True
        fake_job.plugin_name = "bittorrent"
        fake_job.schedule = "0 6 * * *"
        fake_job.on_success.value = "archive"
        fake_job.last_run = None
        fake_job.next_run = None

        fake_scheduler = MagicMock()
        fake_scheduler.jobs = [fake_job]

        with patch("haven_cli.scheduler.job_scheduler.get_scheduler",
                   return_value=fake_scheduler):
            result = CliRunner().invoke(app, ["list", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data[0]["job_id"] == full_id, "B14: full job ID, no truncation"
