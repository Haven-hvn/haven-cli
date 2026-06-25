"""Regression tests for the event payload ↔ TUI handler contract.

The TUI's ``StateManager`` (``haven_tui/core/state_manager.py``) handles
pipeline events by keying on ``video_id`` in the payload.  Every handler
in that file starts with::

    video_id = payload.get('video_id')
    if not video_id:
        return

This means *any* event emitted without a ``video_id`` is silently dropped
on the event path — the TUI then only catches up on the next ~2s polling
tick, which manifests as "completion ticks only appear after pressing r"
type symptoms.

These tests lock the contract: every emit site that the TUI subscribes
to must include ``video_id`` in its payload.  See:
    - ``haven_cli/pipeline/manager.py``  (PIPELINE_STARTED/COMPLETE/FAILED)
    - ``haven_cli/pipeline/step.py``     (STEP_COMPLETE/FAILED/SKIPPED)
    - ``haven_cli/pipeline/steps/upload_step.py``  (UPLOAD_COMPLETE)
    - ``haven_cli/pipeline/steps/sync_step.py``    (SYNC_COMPLETE)
    - ``haven_cli/pipeline/steps/analyze_step.py`` (ANALYSIS_COMPLETE)
    - ``haven_cli/pipeline/steps/encrypt_step.py`` (ENCRYPT_COMPLETE)
    - ``haven_cli/pipeline/steps/ingest_step.py``  (VIDEO_INGESTED)

If you're adding a new ``_emit_event(EventType.*_COMPLETE, ...)`` (or
any other event that ``StateManager`` subscribes to) and this test fails,
add ``"video_id": context.video_id`` to your payload — do **not** weaken
the test.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Iterable, List, Set, Tuple

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Event types whose handlers in ``StateManager`` require ``video_id``.
#
# Keep this in sync with the ``subscribe`` block in
# ``haven_tui/core/state_manager.py`` (~line 564).  The handlers that bail
# on missing ``video_id`` are:
#   _on_download_progress, _on_upload_progress, _on_encrypt_progress,
#   _on_encrypt_complete, _on_upload_complete, _on_sync_complete,
#   _on_analysis_complete, _on_pipeline_complete, _on_stage_complete,
#   _on_pipeline_failed, _on_step_failed, _on_pipeline_started,
#   _on_video_ingested, _on_step_skipped.
# ---------------------------------------------------------------------------
REQUIRED_VIDEO_ID_EVENTS: Set[str] = {
    "DOWNLOAD_PROGRESS",
    "UPLOAD_PROGRESS",
    "ENCRYPT_PROGRESS",
    "ENCRYPT_COMPLETE",
    "UPLOAD_COMPLETE",
    "SYNC_COMPLETE",
    "ANALYSIS_COMPLETE",
    "PIPELINE_COMPLETE",
    "PIPELINE_FAILED",
    "PIPELINE_STARTED",
    "STEP_COMPLETE",
    "STEP_FAILED",
    "STEP_SKIPPED",
    "VIDEO_INGESTED",
}

# Files that emit events the TUI listens to.  Every ``_emit_event`` call
# in these files whose first arg is one of REQUIRED_VIDEO_ID_EVENTS must
# include a ``video_id`` key in its payload dict literal.
EMIT_FILES: List[pathlib.Path] = [
    REPO_ROOT / "haven_cli" / "pipeline" / "manager.py",
    REPO_ROOT / "haven_cli" / "pipeline" / "step.py",
    REPO_ROOT / "haven_cli" / "pipeline" / "steps" / "ingest_step.py",
    REPO_ROOT / "haven_cli" / "pipeline" / "steps" / "encrypt_step.py",
    REPO_ROOT / "haven_cli" / "pipeline" / "steps" / "upload_step.py",
    REPO_ROOT / "haven_cli" / "pipeline" / "steps" / "sync_step.py",
    REPO_ROOT / "haven_cli" / "pipeline" / "steps" / "analyze_step.py",
]


def _event_type_name(node: ast.expr) -> str | None:
    """Return ``"STEP_COMPLETE"`` for ``EventType.STEP_COMPLETE``, else None."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "EventType"
    ):
        return node.attr
    return None


def _payload_keys(node: ast.expr) -> Set[str] | None:
    """Return the set of string keys in a dict literal, or None if dynamic.

    We only support ``{"k": ...}``-style literals here because that's what
    every emit site uses today.  If a future change passes a ``**kwargs``
    dict (or builds the payload elsewhere), we return ``None`` and skip
    the assertion rather than producing a false positive — but the comment
    in the affected file should explain *why* and reference this test.
    """
    if not isinstance(node, ast.Dict):
        return None
    keys: Set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        else:
            # Dynamic key (e.g. ``**other_dict`` adds ``key=None``).  Bail
            # rather than risk a false positive.
            return None
    return keys


def _collect_emit_sites(path: pathlib.Path) -> Iterable[Tuple[int, str, Set[str] | None]]:
    """Yield ``(lineno, event_type_name, payload_keys_or_None)`` for each
    ``self._emit_event(EventType.X, context, {...})`` call in *path*.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match ``self._emit_event(...)`` and ``await self._emit_event(...)``
        if not (
            isinstance(func, ast.Attribute) and func.attr == "_emit_event"
        ):
            continue
        if len(node.args) < 3:
            continue
        event_name = _event_type_name(node.args[0])
        if event_name is None:
            continue
        keys = _payload_keys(node.args[2])
        yield (node.lineno, event_name, keys)


@pytest.mark.parametrize("path", EMIT_FILES, ids=lambda p: p.name)
def test_emit_sites_include_video_id_for_tui_events(path: pathlib.Path) -> None:
    """Every ``_emit_event`` for a TUI-subscribed event must include video_id.

    The TUI's ``StateManager`` silently drops events without ``video_id``
    (see module docstring).  This test walks the AST of each pipeline file
    and asserts that every ``_emit_event(EventType.X, ctx, {...})`` call
    where ``X`` is a TUI-subscribed event has a ``"video_id"`` key in its
    payload literal.
    """
    failures: List[str] = []
    for lineno, event_name, keys in _collect_emit_sites(path):
        if event_name not in REQUIRED_VIDEO_ID_EVENTS:
            continue
        if keys is None:
            # Non-literal payload — skip (caller is expected to document why).
            continue
        if "video_id" not in keys:
            failures.append(
                f"{path.name}:{lineno} emits EventType.{event_name} without "
                f"`video_id` in its payload (keys present: {sorted(keys) or '∅'})"
            )

    assert not failures, (
        "TUI event contract violation — the following emit sites must add "
        '`"video_id": context.video_id` to their payload, otherwise '
        "haven_tui/core/state_manager.py silently drops the event and the "
        "TUI only catches up on the next ~2s poll:\n  - "
        + "\n  - ".join(failures)
    )


# ---------------------------------------------------------------------------
# Canonical payload key audit for the event_log view.
#
# The event_log view formats event payloads for the TUI's scrollback panel.
# After the canonical-key migration the payload uses ``progress_percent``,
# not the legacy ``progress`` field.  If the view reads the wrong key it
# silently shows ``0.0%`` for every encrypt/upload progress event.
# ---------------------------------------------------------------------------


def test_event_log_view_uses_progress_percent_for_encrypt_upload() -> None:
    """``event_log.py`` must read ``progress_percent`` for ENCRYPT/UPLOAD.

    Regression for the bug where ``p.get("progress", 0)`` was used for
    ENCRYPT_PROGRESS and UPLOAD_PROGRESS rows — these payloads never carry
    a ``progress`` key (the canonical name is ``progress_percent``), so the
    event log always rendered ``0.0%`` for every encrypt/upload event.
    """
    path = REPO_ROOT / "haven_tui" / "ui" / "views" / "event_log.py"
    src = path.read_text()

    # The two specific call sites we care about live inside the
    # ENCRYPT_PROGRESS / UPLOAD_PROGRESS branches.  We do a substring check
    # on the relevant lines rather than a full AST traversal because the
    # surrounding context is stable and the test should fail loudly if a
    # future refactor accidentally re-introduces the legacy key.
    assert 'p.get("progress_percent"' in src, (
        "event_log.py no longer reads `progress_percent` — the canonical "
        "progress key.  See haven_tui/ui/views/event_log.py "
        "_format_event_message for the ENCRYPT_PROGRESS / UPLOAD_PROGRESS "
        "branches."
    )
    # Make sure the legacy ``p.get("progress", ...)`` pattern is gone from
    # the encrypt/upload branches.  We grep the whole file because the two
    # branches are right next to each other in the source.
    assert 'p.get("progress", 0)' not in src, (
        "event_log.py still uses the legacy `progress` payload key in at "
        "least one branch.  Replace it with `progress_percent`; the legacy "
        "key is no longer produced by haven_cli pipeline steps."
    )
