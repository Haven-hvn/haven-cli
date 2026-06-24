"""End-to-end tests for IngestStep's Tier 1 pre-upload dedup behaviour.

These tests pin the contract:
* When ``original_hash`` matches an existing catalog row, ``IngestStep``
  returns ``StepResult.skip(...)`` and sets the four ``skip_*`` flags
  appropriately.
* When the prior row has no ``arkiv_entity_key``, ``skip_sync`` stays
  ``False`` so the unfinished prior run can finish syncing.
* When ``--no-dedup`` (i.e. ``options['dedup_enabled'] = False``) is set,
  no hash is computed, no flags are set, and the normal ingest path runs.

See ``docs/BATCH_SYNC_TIER1_PREUPLOAD_DEDUP.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from haven_cli.database.models import Video
from haven_cli.pipeline.context import PipelineContext
from haven_cli.pipeline.results import StepStatus
from haven_cli.pipeline.steps.ingest_step import IngestStep


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    """A small file that passes the ``is_video_file`` MIME check.

    ``is_video_file`` checks the extension first, so ``.mp4`` is enough
    for the rest of the step to proceed under mocks.
    """
    f = tmp_path / "tier1_test.mp4"
    f.write_bytes(b"some bytes that hash deterministically")
    return f


def _patch_metadata_extraction():
    """Mocks ffprobe / mime detection so the test runs without real video."""
    return patch.multiple(
        "haven_cli.pipeline.steps.ingest_step",
        detect_mime_type=MagicMock(return_value="video/mp4"),
        extract_video_metadata=AsyncMock(
            return_value=MagicMock(
                duration=60.0, width=1920, height=1080, fps=30.0,
                codec="h264", bitrate=5_000_000,
                audio_codec="aac", audio_channels=2,
                container="mp4", has_audio=True,
            )
        ),
    )


@pytest.mark.asyncio
async def test_dedup_hit_returns_skip_and_sets_flags(video_file: Path) -> None:
    """When ``original_hash`` matches an entity-keyed row, all skip_* fire.

    The prior row has both ``cid`` and ``arkiv_entity_key``, so encrypt /
    upload / sync / cleanup all skip — there is no useful work to do.
    """
    step = IngestStep()
    context = PipelineContext(source_path=video_file)

    # Build a fake "prior archive" with arkiv_entity_key already set.
    prior = Video(
        id=42,
        source_path="/old/path.mp4",
        title="old",
        cid="bafyprior",
        arkiv_entity_key="key-prior",
        original_hash="z" * 64,
    )

    with _patch_metadata_extraction(), \
         patch.object(step, "_calculate_phash", new_callable=AsyncMock,
                      return_value="phash"), \
         patch.object(step, "_check_duplicate", new_callable=AsyncMock,
                      return_value=False), \
         patch(
             "haven_cli.pipeline.steps.ingest_step.hash_file_sha256_with_progress",
             return_value="z" * 64,
         ), \
         patch.object(step, "_check_original_hash_dedup",
                      new_callable=AsyncMock, return_value=prior):
        result = await step.process(context)

    # The step skips, with a Tier 1 reason in metadata.
    assert result.status == StepStatus.SKIPPED
    skip_reason = result.metadata.get("skip_reason", "") if result.metadata else ""
    assert "Tier 1" in skip_reason

    # All four downstream-skip flags fire.
    assert context.skip_encrypt is True
    assert context.skip_upload is True
    assert context.skip_sync is True
    assert context.skip_cleanup is True

    # Context picks up the prior row's id so downstream observability
    # can attach to the existing catalog entry.
    assert context.video_id == 42
    assert context.original_hash == "z" * 64

    # Step data carries the dedup match details for the TUI/event log.
    assert context.get_step_data("ingest", "dedup_match_video_id") == 42
    assert context.get_step_data("ingest", "dedup_match_cid") == "bafyprior"
    assert context.get_step_data("ingest", "dedup_match_arkiv_key") == "key-prior"


@pytest.mark.asyncio
async def test_dedup_hit_without_arkiv_key_lets_sync_run(
    video_file: Path,
) -> None:
    """If the prior row was uploaded but never synced, sync still runs.

    Why: a crash mid-pipeline can leave a row with ``cid`` set but no
    ``arkiv_entity_key``. The next ingest of the same content must let
    ``SyncStep`` finish the job, otherwise the entity never lands on
    Arkiv.
    """
    step = IngestStep()
    context = PipelineContext(source_path=video_file)

    prior = Video(
        id=99,
        source_path="/old/path.mp4",
        title="old",
        cid="bafyprior",
        arkiv_entity_key=None,
        original_hash="y" * 64,
    )

    with _patch_metadata_extraction(), \
         patch.object(step, "_calculate_phash", new_callable=AsyncMock,
                      return_value="phash"), \
         patch.object(step, "_check_duplicate", new_callable=AsyncMock,
                      return_value=False), \
         patch(
             "haven_cli.pipeline.steps.ingest_step.hash_file_sha256_with_progress",
             return_value="y" * 64,
         ), \
         patch.object(step, "_check_original_hash_dedup",
                      new_callable=AsyncMock, return_value=prior):
        result = await step.process(context)

    assert result.status == StepStatus.SKIPPED
    # Encrypt + upload still skip — the file's already on Filecoin.
    assert context.skip_encrypt is True
    assert context.skip_upload is True
    # But sync MUST run, so its flag stays False.
    assert context.skip_sync is False
    # Cleanup still skips so we don't delete the user's input file.
    assert context.skip_cleanup is True


@pytest.mark.asyncio
async def test_no_dedup_flag_skips_hash_and_lookup(video_file: Path) -> None:
    """``options['dedup_enabled'] = False`` bypasses Tier 1 entirely.

    In this case we expect:
    * No call to the SHA-256 hash helper.
    * No call to ``_check_original_hash_dedup``.
    * ``context.original_hash`` remains None.
    * No skip_* flag is set.
    * The step runs through normal save-to-database and returns success.
    """
    step = IngestStep()
    context = PipelineContext(
        source_path=video_file,
        options={"dedup_enabled": False},
    )

    hash_mock = MagicMock(return_value="should-not-be-called" * 4)

    save_mock = AsyncMock(return_value=7)

    with _patch_metadata_extraction(), \
         patch.object(step, "_calculate_phash", new_callable=AsyncMock,
                      return_value="phash"), \
         patch.object(step, "_check_duplicate", new_callable=AsyncMock,
                      return_value=False), \
         patch(
             "haven_cli.pipeline.steps.ingest_step.hash_file_sha256_with_progress",
             hash_mock,
         ), \
         patch.object(step, "_check_original_hash_dedup",
                      new_callable=AsyncMock) as dedup_mock, \
         patch.object(step, "_save_to_database", save_mock):
        result = await step.process(context)

    # Hash was not computed.
    hash_mock.assert_not_called()
    # Dedup lookup was not invoked.
    dedup_mock.assert_not_awaited()

    # No flags were flipped.
    assert context.original_hash is None
    assert context.skip_encrypt is False
    assert context.skip_upload is False
    assert context.skip_sync is False
    assert context.skip_cleanup is False

    # Normal ingest succeeds.
    assert result.success is True
    assert context.video_id == 7


@pytest.mark.asyncio
async def test_hash_failure_falls_through_to_normal_path(
    video_file: Path,
) -> None:
    """If ``hash_file_sha256_with_progress`` raises, ingest does not abort.

    The slow-hardware target prefers "degrade to normal upload" over
    "fail loudly because the hash pass crashed." A real I/O failure will
    re-surface in the encrypt step's own hash pass; the dedup check is a
    best-effort optimization.
    """
    step = IngestStep()
    context = PipelineContext(source_path=video_file)

    save_mock = AsyncMock(return_value=12)

    with _patch_metadata_extraction(), \
         patch.object(step, "_calculate_phash", new_callable=AsyncMock,
                      return_value="phash"), \
         patch.object(step, "_check_duplicate", new_callable=AsyncMock,
                      return_value=False), \
         patch(
             "haven_cli.pipeline.steps.ingest_step.hash_file_sha256_with_progress",
             side_effect=OSError("disk gremlin"),
         ), \
         patch.object(step, "_check_original_hash_dedup",
                      new_callable=AsyncMock) as dedup_mock, \
         patch.object(step, "_save_to_database", save_mock):
        result = await step.process(context)

    # Hash failed → no dedup lookup attempted.
    dedup_mock.assert_not_awaited()
    # No flags set.
    assert context.skip_encrypt is False
    assert context.skip_upload is False
    # Normal ingest path completed successfully.
    assert result.success is True
    assert context.video_id == 12


@pytest.mark.asyncio
async def test_dedup_miss_persists_original_hash(video_file: Path) -> None:
    """When there's no prior match, the new hash is written to the DB.

    Persistence is what enables future ingests to deduplicate against
    this row. Without it, a re-archive would re-upload every time.
    """
    step = IngestStep()
    context = PipelineContext(source_path=video_file)

    captured_kwargs: dict = {}

    async def fake_save(metadata, ctx, plugin_name=None,
                        plugin_source_id=None, source_uri=None):
        captured_kwargs["original_hash"] = ctx.original_hash
        return 17

    with _patch_metadata_extraction(), \
         patch.object(step, "_calculate_phash", new_callable=AsyncMock,
                      return_value="phash"), \
         patch.object(step, "_check_duplicate", new_callable=AsyncMock,
                      return_value=False), \
         patch(
             "haven_cli.pipeline.steps.ingest_step.hash_file_sha256_with_progress",
             return_value="x" * 64,
         ), \
         patch.object(step, "_check_original_hash_dedup",
                      new_callable=AsyncMock, return_value=None), \
         patch.object(step, "_save_to_database", side_effect=fake_save):
        result = await step.process(context)

    assert result.success is True
    assert captured_kwargs["original_hash"] == "x" * 64, (
        "On a dedup miss the new ``original_hash`` must reach the DB save "
        "path so future ingests of the same file can short-circuit."
    )
    # And it lives on the context for downstream steps that want it.
    assert context.original_hash == "x" * 64
