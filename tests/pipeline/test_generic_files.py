"""Generic (non audio/video) files through ingest, analyze and Arkiv sync."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from haven_cli.pipeline.context import PipelineContext, UploadResult, VideoMetadata
from haven_cli.pipeline.steps.analyze_step import AnalyzeStep
from haven_cli.pipeline.steps.ingest_step import IngestStep
from haven_cli.services.arkiv_sync import (
    ARKIV_GROUP_FILE_FULL,
    ARKIV_GROUP_IMAGE_FULL,
    ARKIV_GROUP_TEXT_FULL,
    ARKIV_GROUP_VIDEO_FULL,
    PAYLOAD_EXTRA_MAX_BYTES,
    _build_attributes,
    _build_payload,
    arkiv_group_for,
    expires_in_for,
)
from tests.prowlarr.conftest import no_network  # noqa: F401 - offline guard

pytestmark = pytest.mark.usefixtures("no_network")

PDF = b"%PDF-1.4\n" + b"x" * 64


@contextmanager
def fake_db(video_id=7):
    session = MagicMock()
    repo = MagicMock()
    repo.get_by_source_path.return_value = None
    repo.get_by_original_hash.return_value = None
    repo.create.return_value = MagicMock(id=video_id)
    with patch("haven_cli.database.connection.get_db_session") as get_session:
        get_session.return_value.__enter__ = MagicMock(return_value=session)
        get_session.return_value.__exit__ = MagicMock(return_value=None)
        with patch("haven_cli.database.repositories.VideoRepository", return_value=repo):
            yield repo


class TestIngestGeneric:
    async def test_unsupported_file_still_skipped_by_default(self, tmp_path):
        doc = tmp_path / "paper.pdf"
        doc.write_bytes(PDF)
        result = await IngestStep().process(PipelineContext(source_path=doc))
        assert result.skipped and "not a supported media format" in result.data.get("reason", str(result))

    async def test_generic_file_admitted_without_ffprobe(self, tmp_path):
        doc = tmp_path / "paper.bin"
        doc.write_bytes(PDF)
        context = PipelineContext(
            source_path=doc,
            options={"generic_files_enabled": True, "title": "A Paper", "source_uri": "https://docs.example/1"},
        )
        step = IngestStep()
        with fake_db() as repo, patch(
            "haven_cli.pipeline.steps.ingest_step.extract_video_metadata"
        ) as ffprobe, patch.object(step, "_calculate_phash") as phash:
            result = await step.process(context)
        assert result.success, result
        ffprobe.assert_not_called()
        phash.assert_not_called()
        assert context.media_kind == "document"
        assert context.video_metadata.mime_type == "application/pdf"
        assert context.video_metadata.title == "A Paper"
        assert context.original_hash  # sha256 dedup still runs
        created = repo.create.call_args.kwargs
        assert created["mime_type"] == "application/pdf" and created["duration"] == 0.0

    async def test_accept_reject_and_pointer_files(self, tmp_path):
        doc = tmp_path / "paper.pdf"
        doc.write_bytes(PDF)
        opts = {"generic_files_enabled": True, "generic_file_accept": ["image/*"]}
        result = await IngestStep().process(PipelineContext(source_path=doc, options=opts))
        assert result.skipped
        opts = {"generic_files_enabled": True, "generic_file_reject": ["document"]}
        result = await IngestStep().process(PipelineContext(source_path=doc, options=opts))
        assert result.skipped
        torrent = tmp_path / "x.torrent"
        torrent.write_bytes(b"d8:announce3:urle")
        result = await IngestStep().process(PipelineContext(source_path=torrent, options={"generic_files_enabled": True}))
        assert result.skipped

    async def test_generic_dedup_hit_short_circuits(self, tmp_path):
        doc = tmp_path / "paper.pdf"
        doc.write_bytes(PDF)
        context = PipelineContext(source_path=doc, options={"generic_files_enabled": True})
        existing = MagicMock(id=3, cid="bafy", arkiv_entity_key="0xabc")
        with fake_db() as repo:
            repo.get_by_original_hash.return_value = existing
            result = await IngestStep().process(context)
        assert result.skipped and context.skip_upload and context.skip_sync


class TestAnalyzeSkip:
    async def test_skips_generic_even_when_enabled(self, tmp_path):
        context = PipelineContext(source_path=tmp_path / "a.pdf", options={"vlm_enabled": True})
        context.media_kind = "document"
        step = AnalyzeStep()
        assert await step.should_skip(context)
        assert "video only" in await step._get_skip_reason(context)

    async def test_video_behavior_unchanged(self, tmp_path):
        context = PipelineContext(source_path=tmp_path / "a.mp4", options={"vlm_enabled": False})
        step = AnalyzeStep()
        assert await step.should_skip(context)
        assert await step._get_skip_reason(context) == "vlm_enabled is disabled"
        context.options["vlm_enabled"] = True
        assert not await step.should_skip(context)


def generic_context(tmp_path, kind="document", mime="application/pdf", **options):
    context = PipelineContext(source_path=tmp_path / "paper.pdf", options=options)
    context.media_kind = kind
    context.video_metadata = VideoMetadata(
        path=str(context.source_path), title="Paper", mime_type=mime, file_size=10, source_uri="https://docs.example/1"
    )
    context.upload_result = UploadResult(video_path=str(context.source_path), root_cid="bafyroot", piece_cid="bagapiece")
    return context


class TestArkiv:
    @pytest.mark.parametrize(
        "kind,group",
        [(None, ARKIV_GROUP_VIDEO_FULL), ("document", ARKIV_GROUP_TEXT_FULL), ("text", ARKIV_GROUP_TEXT_FULL), ("image", ARKIV_GROUP_IMAGE_FULL), ("archive", ARKIV_GROUP_FILE_FULL), ("other", ARKIV_GROUP_FILE_FULL)],
    )
    def test_group_by_kind(self, tmp_path, kind, group):
        context = generic_context(tmp_path, kind=kind)
        assert arkiv_group_for(context) == group

    def test_group_override_validated(self, tmp_path):
        assert arkiv_group_for(generic_context(tmp_path, arkiv_grp="acme.papers.full")) == "acme.papers.full"
        assert arkiv_group_for(generic_context(tmp_path, arkiv_grp="Bad Group!")) == ARKIV_GROUP_TEXT_FULL

    def test_attributes_and_payload(self, tmp_path):
        context = generic_context(tmp_path, arkiv_payload_extra={"idx": "Docs", "pub": "2026-09-01T00:00:00+00:00"})
        attrs = _build_attributes(context)
        assert attrs["grp"] == ARKIV_GROUP_TEXT_FULL and attrs["mime"] == 14 and "dur_s" not in attrs
        payload = _build_payload(context)
        assert payload["fcid"] == "bafyroot" and payload["src"] == "https://docs.example/1"
        assert payload["name"] == "paper.pdf" and "ct" not in payload  # pdf has an enum code
        assert payload["x"] == {"idx": "Docs", "pub": "2026-09-01T00:00:00+00:00"}

    def test_unmapped_mime_goes_to_payload(self, tmp_path):
        context = generic_context(tmp_path, kind="document", mime="application/epub+zip")
        assert "mime" not in _build_attributes(context)
        assert _build_payload(context)["ct"] == "application/epub+zip"

    def test_video_payload_unchanged(self, tmp_path):
        context = generic_context(tmp_path, kind=None, mime="video/mp4")
        payload = _build_payload(context)
        assert "name" not in payload and "ct" not in payload and "x" not in payload

    def test_payload_extra_size_cap(self, tmp_path):
        context = generic_context(tmp_path, arkiv_payload_extra={"blob": "x" * (PAYLOAD_EXTRA_MAX_BYTES + 1)})
        assert "x" not in _build_payload(context)
        json.dumps(_build_payload(context))

    @pytest.mark.parametrize(
        "options,expected",
        [({}, 100), ({"arkiv_expires_in": 3600}, 3600), ({"arkiv_expiration_weeks": 2}, 2 * 604800), ({"arkiv_expires_in": "bad"}, 100), ({"arkiv_expires_in": -5}, 100)],
    )
    def test_expiry_override(self, tmp_path, options, expected):
        assert expires_in_for(generic_context(tmp_path, **options), 100) == expected

    def test_sync_context_uses_override(self, tmp_path):
        from haven_cli.services.arkiv_sync import ArkivSyncClient, ArkivSyncConfig

        client = ArkivSyncClient(ArkivSyncConfig(enabled=True, private_key="0x1", rpc_url="http://rpc"))
        fake = MagicMock()
        fake.arkiv.create_entity.return_value = ("0xkey", MagicMock())
        with patch.object(client, "_get_client", return_value=fake), patch.object(client, "find_existing_entity", return_value=None):
            client.sync_context(generic_context(tmp_path, arkiv_expires_in=31536000))
        assert fake.arkiv.create_entity.call_args.kwargs["expires_in"] == 31536000
