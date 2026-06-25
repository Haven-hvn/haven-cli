"""Regression tests for ``VideoListWidget.get_selected_video_id``.

BUG: Pressing ``d`` on a row in the video list opened the details for a
*different* video than the one highlighted by the cursor.  Root cause:
``get_selected_video_id`` was indexing ``self._video_rows`` (the model
list) by ``self.cursor_row`` (the **visible** row index in the rendered
table).  Those two orderings drift apart whenever ``_update_table``
materializes rows in a different order than they appear in the model —
which happens routinely because the diff path adds new rows via
``set difference`` ordering, not insertion order.

Fix: ``get_selected_video_id`` now consults
``DataTable.coordinate_to_cell_key`` first (the authoritative
position → key mapping Textual provides for us), and falls back to
``self._row_order`` (which tracks the actual rendered order updated by
``_update_table``).  Only as a last resort does it index ``_video_rows``
by position.

This test exercises the **fallback path** because spinning up a full
Textual ``App`` to drive a real ``DataTable`` would be overkill here.
The contract we're locking is "if ``_row_order`` says row N is video X,
then ``get_selected_video_id`` at ``cursor_row = N`` returns X — even if
``_video_rows[N]`` is a different video".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import patch

import pytest

from haven_tui.ui.views.video_list import VideoListWidget, VideoRow


def _make_row(video_id: int, title: str) -> VideoRow:
    """Build a minimal VideoRow.  The widget only reads ``.video_id`` in
    the path we're testing, but VideoRow is a frozen-ish dataclass so we
    have to supply every field."""
    return VideoRow(
        index=0,
        video_id=video_id,
        title=title,
        stage="download",
        progress=0.0,
        speed="-",
        plugin="test",
        size="-",
        eta="--:--",
        status="pending",
        started_at="-",
        skip_reason="",
    )


class _StubWidget:
    """Minimal stand-in for VideoListWidget that only carries the state
    ``get_selected_video_id`` reads.  We bind the real method to this
    instance so we test the actual production code, not a re-implementation.
    """

    def __init__(
        self,
        cursor_row: Optional[int],
        video_rows: List[VideoRow],
        row_order: Optional[List[str]] = None,
        coordinate_raises: bool = True,
    ) -> None:
        self.cursor_row = cursor_row
        self._video_rows = video_rows
        if row_order is not None:
            self._row_order = row_order
        self._coordinate_raises = coordinate_raises

    def coordinate_to_cell_key(self, coord):  # noqa: D401, ANN001
        """Mimic Textual's API.  When ``_coordinate_raises`` is True we
        force the fallback path; when False we return a row_key shaped
        like Textual would.
        """
        if self._coordinate_raises:
            raise RuntimeError("coordinate_to_cell_key unavailable in test")
        # Return shape: ``CellKey`` with a ``.row_key`` attr that itself
        # has a ``.value`` attr (Textual's actual API).
        # ``cursor_row`` is the visible row, and we trust ``_row_order``
        # to be the authoritative rendered order.
        row_idx = coord.row
        if row_idx < 0 or row_idx >= len(self._row_order):
            raise IndexError(row_idx)
        return SimpleNamespace(
            row_key=SimpleNamespace(value=self._row_order[row_idx])
        )


# Bind the real method to the stub so we test the production code path.
_get_selected_video_id = VideoListWidget.get_selected_video_id


# ---------------------------------------------------------------------------
# Authoritative path: coordinate_to_cell_key returns the row key directly.
# ---------------------------------------------------------------------------


def test_uses_coordinate_to_cell_key_when_available() -> None:
    """When Textual's API works, we should return its answer verbatim —
    independent of ``_video_rows`` insertion order.
    """
    # Model order: [10, 20, 30] but rendered order is [30, 10, 20]
    # (e.g. video 30 was added first by ``_update_table``'s set diff,
    # then 10 and 20 followed).  Cursor on visible row 0 must yield 30,
    # not 10.
    stub = _StubWidget(
        cursor_row=0,
        video_rows=[_make_row(10, "a"), _make_row(20, "b"), _make_row(30, "c")],
        row_order=["30", "10", "20"],
        coordinate_raises=False,
    )

    result = _get_selected_video_id(stub)
    assert result == 30


def test_coordinate_path_handles_each_visible_row() -> None:
    """Walk the cursor through every visible row and check we get the
    *rendered* row's video, not the model row at that index."""
    rendered_order_to_video = {
        0: 99,
        1: 7,
        2: 42,
    }
    row_order = [str(rendered_order_to_video[i]) for i in range(3)]

    # Deliberately reverse the model so model[i] != rendered[i] for every i.
    model = [_make_row(rendered_order_to_video[2 - i], f"v{i}") for i in range(3)]

    for cursor in range(3):
        stub = _StubWidget(
            cursor_row=cursor,
            video_rows=model,
            row_order=row_order,
            coordinate_raises=False,
        )
        assert _get_selected_video_id(stub) == rendered_order_to_video[cursor]


# ---------------------------------------------------------------------------
# Fallback path: coordinate_to_cell_key raises (e.g. older Textual).
# We then consult ``_row_order`` before falling back to ``_video_rows``.
# ---------------------------------------------------------------------------


def test_fallback_uses_row_order_not_video_rows_index() -> None:
    """When Textual's API is unavailable, ``_row_order`` is the next
    most reliable source of truth — it's set by ``_update_table`` to
    match whatever the rendered table actually contains.
    """
    stub = _StubWidget(
        cursor_row=1,
        video_rows=[_make_row(10, "a"), _make_row(20, "b"), _make_row(30, "c")],
        row_order=["30", "10", "20"],
        coordinate_raises=True,
    )
    # Row 1 in the rendered table is video 10, not video 20.
    assert _get_selected_video_id(stub) == 10


def test_fallback_handles_unparseable_row_key() -> None:
    """``_row_order`` should always contain numeric video IDs as strings,
    but if it ever has garbage we shouldn't crash — we silently fall
    through to ``_video_rows``."""
    stub = _StubWidget(
        cursor_row=0,
        video_rows=[_make_row(10, "a"), _make_row(20, "b")],
        row_order=["not-an-int"],
        coordinate_raises=True,
    )
    # ``int("not-an-int")`` raises, so we fall back to _video_rows[0].
    assert _get_selected_video_id(stub) == 10


def test_fallback_to_video_rows_when_no_row_order() -> None:
    """Before the first ``_update_table`` runs, ``_row_order`` doesn't
    exist.  We should still return *something* sensible (this is what
    the old code did, modulo the bug)."""
    stub = _StubWidget(
        cursor_row=0,
        video_rows=[_make_row(10, "a"), _make_row(20, "b")],
        row_order=None,
        coordinate_raises=True,
    )
    assert _get_selected_video_id(stub) == 10


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


def test_returns_none_when_no_cursor() -> None:
    """``cursor_row`` is ``None`` before the user has interacted with the
    table or when the table is empty."""
    stub = _StubWidget(
        cursor_row=None,
        video_rows=[_make_row(10, "a")],
        row_order=["10"],
        coordinate_raises=False,
    )
    assert _get_selected_video_id(stub) is None


def test_returns_none_when_cursor_negative() -> None:
    """Some Textual versions return -1 for "no selection"; guard for it."""
    stub = _StubWidget(
        cursor_row=-1,
        video_rows=[_make_row(10, "a")],
        row_order=["10"],
        coordinate_raises=False,
    )
    assert _get_selected_video_id(stub) is None


def test_returns_none_when_cursor_past_end() -> None:
    """Cursor past the last row (e.g. a row was just removed) — don't
    IndexError; return ``None`` so the caller's "No video selected"
    notify path triggers."""
    stub = _StubWidget(
        cursor_row=99,
        video_rows=[_make_row(10, "a")],
        row_order=["10"],
        coordinate_raises=True,  # force fallback
    )
    assert _get_selected_video_id(stub) is None


def test_returns_none_when_row_key_value_is_none() -> None:
    """If Textual's row_key.value is None (shouldn't happen but…) we
    should bail rather than ``int(None)``."""
    stub = _StubWidget(
        cursor_row=0,
        video_rows=[_make_row(10, "a")],
        row_order=["10"],
        coordinate_raises=False,
    )

    def fake_lookup(coord):  # noqa: ANN001
        return SimpleNamespace(row_key=SimpleNamespace(value=None))

    stub.coordinate_to_cell_key = fake_lookup  # type: ignore[assignment]
    assert _get_selected_video_id(stub) is None
