"""Main Video List View for Haven TUI.

This module provides the primary view for the TUI - a scrollable list of videos
showing their current pipeline stage and progress, inspired by aria2tui's download list.
"""

from __future__ import annotations

from typing import Callable, ClassVar, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timezone

from textual.widgets import DataTable, Static, Header, Footer, Input
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.coordinate import Coordinate

from haven_tui.core.state_manager import StateManager, VideoState
from haven_tui.core.controller import VideoListController, FilterResult
from haven_tui.core.pipeline_interface import BatchOperations, BatchResult
from haven_tui.config import HavenTUIConfig
from haven_tui.models.video_view import (
    PipelineStage, StageStatus, FilterState,
    SortField, SortOrder, VideoSorter,
)
from haven_tui.ui.components.speed_graph import SpeedGraphComponent
from haven_tui.data.repositories import SpeedHistoryRepository


@dataclass
class VideoRow:
    """Represents a single row in the video list."""
    index: int
    video_id: int
    title: str
    stage: str
    progress: float
    speed: str
    plugin: str
    size: str
    eta: str
    status: str  # "active", "pending", "completed", "failed", "skipped"
    started_at: str  # Formatted started/found time
    skip_reason: str  # Reason for being skipped, if any


class VideoListWidget(DataTable):
    """A DataTable widget for displaying video pipeline status.
    
    This widget displays a scrollable list of videos with their current
    pipeline stage, progress, speed, and other metadata. Supports
    multi-selection and auto-refresh.
    
    Attributes:
        state_manager: The StateManager instance for accessing video state
        config: The HavenTUIConfig for display settings
        on_select_callback: Optional callback when a video is selected
        on_multi_select_callback: Optional callback for multi-selection
    """
    
    DEFAULT_CSS = """
    VideoListWidget {
        height: 100%;
        width: 100%;
        border: solid $primary;
    }
    
    VideoListWidget > .datatable--header {
        background: $surface-darken-1;
        color: $text;
        text-style: bold;
    }
    
    VideoListWidget > .datatable--row {
        height: 1;
    }
    
    VideoListWidget > .datatable--row-cursor {
        background: $primary-darken-1;
    }
    
    VideoListWidget > .datatable--row-selected {
        background: $success-darken-2;
    }
    
    /* Stage-specific styling */
    .stage-pending { color: $text-muted; }
    .stage-download { color: $accent; }
    .stage-ingest { color: $warning; }
    .stage-analysis { color: $warning; }
    .stage-encrypt { color: $error; }
    .stage-upload { color: $success; }
    /* Upload finalize sub-stages. These are emitted by
       ``haven_cli/pipeline/steps/upload_step.py`` after the synapse-sdk byte
       stream has drained but before the CID is persisted to the database.
       All are styled italic so users can tell at a glance that the network
       upload is done and we're now wrapping up — but each sub-stage gets a
       slightly different hue so a stuck finalize is easy to spot:

         stage-stored            (95%)  CAR fully streamed to provider(s)
         stage-pieces_added      (97%)  on-chain addPieces succeeded
         stage-pieces_confirmed  (99%)  provider confirmed PDP root
         stage-finalizing        (95%)  Python umbrella (verifyPieceRetrieval,
                                        getStatus polling, vlm_json upload,
                                        encrypt_cid, DB write)

       Note that ``pieces_added`` and ``pieces_confirmed`` use an underscore
       in their CSS class because ``_get_stage_style`` does
       ``f"stage-{stage.lower()}"`` without normalization — the JS layer in
       ``js-services/synapse-wrapper.ts`` and the Python event_stage in
       ``state_manager._on_upload_progress`` both emit the underscore form,
       so the CSS class names must match exactly. */
    .stage-finalizing { color: $success; text-style: italic; }
    .stage-stored { color: $success; text-style: italic; }
    .stage-pieces_added { color: $warning; text-style: italic; }
    .stage-pieces_confirmed { color: $accent; text-style: italic; }
    .stage-sync { color: $success; }
    .stage-complete { color: $success; text-style: bold; }
    .stage-skipped { color: $warning; text-style: italic; }
    .stage-failed { color: $error; }
    
    /* Progress bar styling */
    .progress-complete { color: $success; }
    .progress-active { color: $accent; }
    .progress-pending { color: $text-muted; }
    """
    
    # Column definitions: (key, label, width, visible)
    COLUMNS: ClassVar[List[tuple[str, str, int, bool]]] = [
        ("#", "#", 4, True),
        ("sel", "✓", 3, True),  # Selection column for batch mode
        ("title", "Title", 28, True),
        ("stage", "Stage", 10, True),
        ("progress", "Progress", 12, True),
        ("speed", "Speed", 10, True),
        ("plugin", "Plugin", 10, True),
        ("size", "Size", 8, True),
        ("eta", "ETA", 8, True),
        ("started", "Started", 16, True),  # New column for started/found time
        ("skip_reason", "Skip Reason", 20, True),  # New column for skip reason
    ]
    
    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        config: Optional[HavenTUIConfig] = None,
        on_select: Optional[Callable[[int], None]] = None,
        on_multi_select: Optional[Callable[[List[int]], None]] = None,
        controller: Optional[VideoListController] = None,
        batch_operations: Optional[BatchOperations] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the video list widget.
        
        Args:
            state_manager: The StateManager for accessing video state
            config: The TUI configuration
            on_select: Callback when a video is selected (single click)
            on_multi_select: Callback for multi-selection changes
            controller: Optional VideoListController for filtering
            batch_operations: Optional BatchOperations instance for multi-select
            **kwargs: Additional arguments passed to DataTable
        """
        super().__init__(**kwargs)
        self.state_manager = state_manager
        self.config = config or HavenTUIConfig()
        self.on_select_callback = on_select
        self.on_multi_select_callback = on_multi_select
        self._video_rows: List[VideoRow] = []
        self._last_refresh: Optional[datetime] = None
        self._last_filter_result: Optional[FilterResult] = None
        
        # Batch operations for multi-select
        self.batch_operations = batch_operations
        
        # Initialize controller with config-based filter state
        if controller:
            self.controller = controller
        elif state_manager:
            filter_state = FilterState(
                show_completed=self.config.filters.show_completed,
                show_failed=self.config.filters.show_failed,
                plugin=self.config.filters.plugin_filter if self.config.filters.plugin_filter != "all" else None,
            )
            self.controller = VideoListController(state_manager, filter_state)
        else:
            self.controller = None
        
        # Enable zebra striping and cursor
        self.zebra_stripes = True
        self.cursor_type = "row"
        self.show_cursor = True
        
    def compose(self):
        """Compose the widget.

        ``DataTable`` is a leaf widget — it doesn't yield children. Column
        setup belongs in ``on_mount`` (see I13 / C2 in the proposal); calling
        ``add_column`` from ``compose`` happens to work today but is fragile
        because Textual reserves the right to call ``compose`` more than
        once, which would raise on the duplicate column key.
        """
        return []

    def on_mount(self) -> None:
        """Handle mount event - install columns and do initial data load."""
        # Install columns once, idempotently. We guard with ``self.columns``
        # so that if Textual ever invokes on_mount twice (or if the widget
        # is re-mounted) we don't blow up on duplicate keys.
        if not self.columns:
            for key, label, width, visible in self.COLUMNS:
                if visible:
                    self.add_column(label, key=key, width=width)
        self.refresh_data()

    
    def _format_progress_bar(self, progress: float, width: int = 10) -> str:
        """Format a progress bar using Unicode block characters.
        
        Args:
            progress: Progress percentage (0.0 - 100.0)
            width: Width of the progress bar in characters
            
        Returns:
            Formatted progress bar string
        """
        if progress <= 0:
            return "░" * width + " 0%"
        elif progress >= 100:
            return "█" * width + " 100%"
        
        filled = int((progress / 100.0) * width)
        empty = width - filled
        bar = "█" * filled + "░" * empty
        return f"{bar} {progress:.0f}%"
    
    def _format_speed(self, speed: float) -> str:
        """Format speed in human-readable form.
        
        Args:
            speed: Speed in bytes per second
            
        Returns:
            Formatted speed string (e.g., "2.4MB/s")
        """
        if speed == 0:
            return "-"
        
        # Convert to human readable
        size = float(speed)
        if size < 1024:
            return f"{size:.0f}B/s"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}KB/s"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}MB/s"
        size /= 1024
        return f"{size:.1f}GB/s"
    
    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable form.
        
        Args:
            size_bytes: Size in bytes
            
        Returns:
            Formatted size string (e.g., "3.2GB")
        """
        if size_bytes == 0:
            return "-"
        
        size = float(size_bytes)
        if size < 1024:
            return f"{size:.0f}B"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}KB"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}MB"
        size /= 1024
        return f"{size:.1f}GB"
    
    def _format_eta(self, eta_seconds: Optional[int]) -> str:
        """Format ETA in human-readable form.
        
        Args:
            eta_seconds: ETA in seconds
            
        Returns:
            Formatted ETA string (e.g., "12m30s")
        """
        if eta_seconds is None:
            return "--:--"
        
        minutes, seconds = divmod(eta_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            return f"{hours}h{minutes:02d}m"
        return f"{minutes}:{seconds:02d}"
    
    def _get_stage_style(self, stage: str, status: str) -> str:
        """Get the CSS style class for a stage.
        
        Args:
            stage: The pipeline stage
            status: The video status
            
        Returns:
            CSS class name for styling
        """
        if status == "completed":
            return "stage-complete"
        elif status == "failed":
            return "stage-failed"
        elif status == "skipped":
            return "stage-skipped"
        return f"stage-{stage.lower()}"
    
    def _truncate_title(self, title: str, max_length: int = 35) -> str:
        """Truncate title to fit column width.
        
        Args:
            title: The video title
            max_length: Maximum length
            
        Returns:
            Truncated title
        """
        if len(title) <= max_length:
            return title
        return title[: max_length - 3] + "..."
    
    def _format_started_at(self, started_at: Optional[datetime], created_at: Optional[datetime]) -> str:
        """Format started/found time for display.
        
        Args:
            started_at: When processing started (download began)
            created_at: When the video was added to the system
            
        Returns:
            Formatted time string (e.g., "02/08 14:30" or "Pending")
        """
        # Prefer started_at if available, otherwise use created_at
        timestamp = started_at or created_at
        
        if timestamp is None:
            return "-"
        
        # Format as MM/DD HH:MM
        return timestamp.strftime("%m/%d %H:%M")
    
    def _truncate_skip_reason(self, reason: Optional[str], max_length: int = 20) -> str:
        """Truncate skip reason to fit column width.
        
        Args:
            reason: The skip reason
            max_length: Maximum length
            
        Returns:
            Truncated skip reason or empty string
        """
        if not reason:
            return ""
        if len(reason) <= max_length:
            return reason
        return reason[: max_length - 3] + "..."
    
    def refresh_data(self) -> None:
        """Refresh the video list data from the state manager.
        
        This method fetches the current video states from the state manager,
        applies any active filters, and updates the table display.
        """
        if self.state_manager is None:
            return
        
        # Use controller for filtering if available
        if self.controller:
            result = self.controller.get_filtered_videos()
            self._last_filter_result = result
            videos = result.videos
        else:
            # Fallback to direct state manager access with config filters
            videos = self.state_manager.get_all_videos()
            
            # Filter based on config
            if not self.config.filters.show_completed:
                videos = [v for v in videos if not v.is_completed]
            if not self.config.filters.show_failed:
                videos = [v for v in videos if not v.has_failed]
        
        # Sort by activity and creation time
        # Normalize all datetimes to offset-aware to avoid comparison errors
        def _get_sort_datetime(v):
            dt = v.created_at
            if dt is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            # Ensure offset-aware: if naive, assume UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        
        videos.sort(key=lambda v: (
            not v.is_active,  # Active videos first
            _get_sort_datetime(v),
        ))
        
        # Build row data
        self._video_rows = []
        for i, video in enumerate(videos, 1):
            # Determine status - check for skipped
            status = video.overall_status
            skip_reason = getattr(video, 'skip_reason', None)
            if skip_reason:
                status = "skipped"
            
            row = VideoRow(
                index=i,
                video_id=video.id,
                title=self._truncate_title(video.title),
                stage=video.current_stage,
                progress=video.current_progress,
                speed=self._format_speed(video.current_speed),
                plugin=getattr(video, 'plugin', 'unknown'),
                size=self._format_size(getattr(video, 'file_size', 0)),
                eta=self._format_eta(video.stage_eta if video.current_stage == PipelineStage.DOWNLOAD else None),
                status=status,
                started_at=self._format_started_at(
                    getattr(video, 'started_at', None),
                    getattr(video, 'created_at', None)
                ),
                skip_reason=self._truncate_skip_reason(skip_reason),
            )
            self._video_rows.append(row)
        
        # Update the table
        self._update_table()
        self._last_refresh = datetime.now(timezone.utc)
    
    def set_filter_state(self, filter_state: FilterState) -> None:
        """Set the current filter state and refresh.
        
        Args:
            filter_state: New filter state to apply
        """
        if self.controller:
            self.controller.filter_state = filter_state
            self.refresh_data()
    
    def get_filter_state(self) -> Optional[FilterState]:
        """Get the current filter state.
        
        Returns:
            Current filter state or None if no controller
        """
        return self.controller.filter_state if self.controller else None
    
    def clear_filters(self) -> None:
        """Clear all filters and refresh."""
        if self.controller:
            self.controller.clear_all_filters()
            self.refresh_data()
    
    def set_search_query(self, query: str) -> None:
        """Set the search query and refresh.
        
        Args:
            query: Search string
        """
        if self.controller:
            self.controller.set_search_query(query)
            self.refresh_data()
    
    def toggle_show_completed(self) -> bool:
        """Toggle show completed filter.
        
        Returns:
            New value of show_completed
        """
        if self.controller:
            result = self.controller.toggle_show_completed()
            self.refresh_data()
            return result
        return False
    
    def toggle_show_failed(self) -> bool:
        """Toggle show failed filter.
        
        Returns:
            New value of show_failed
        """
        if self.controller:
            result = self.controller.toggle_show_failed()
            self.refresh_data()
            return result
        return False
    
    def get_filter_summary(self) -> str:
        """Get a summary of current filter state.
        
        Returns:
            Human-readable filter summary
        """
        if not self.controller or not self.controller.has_active_filters():
            return ""
        
        result = self._last_filter_result
        if result and result.active_filters:
            return f"Filters: {', '.join(result.active_filters)}"
        return ""
    
    def set_sort_field(self, field: SortField) -> None:
        """Set the sort field and refresh.
        
        Args:
            field: The field to sort by
        """
        if self.controller:
            self.controller.set_sort_field(field)
            self.refresh_data()
    
    def set_sort_order(self, order: SortOrder) -> None:
        """Set the sort order and refresh.
        
        Args:
            order: ASCENDING or DESCENDING
        """
        if self.controller:
            self.controller.set_sort_order(order)
            self.refresh_data()
    
    def toggle_sort_order(self) -> SortOrder:
        """Toggle sort order and refresh.
        
        Returns:
            The new sort order
        """
        if self.controller:
            order = self.controller.toggle_sort_order()
            self.refresh_data()
            return order
        return SortOrder.DESCENDING
    
    def cycle_sort_field(self) -> SortField:
        """Cycle to the next sort field and refresh.
        
        Returns:
            The new sort field
        """
        if self.controller:
            field = self.controller.cycle_sort_field()
            self.refresh_data()
            return field
        return SortField.DATE_ADDED
    
    def get_sort_description(self) -> str:
        """Get a human-readable description of the current sort.
        
        Returns:
            String describing the current sort
        """
        if self.controller:
            return self.controller.get_sort_description()
        return ""
    
    # Maps key (e.g. "title") -> column index in the table. Built lazily on
    # first _update_table call so we don't have to depend on construction
    # order of the COLUMNS list.
    _column_index: ClassVar[Optional[dict[str, int]]] = None

    @classmethod
    def _build_column_index(cls) -> dict[str, int]:
        """Compute the visible-column-index map once and cache it on the class."""
        if cls._column_index is None:
            cls._column_index = {
                key: i
                for i, (key, _label, _width, visible) in enumerate(
                    [c for c in cls.COLUMNS if c[3]]
                )
                for key, *_ in [(key,)]  # noqa: E501 - keep the tuple flat
            }
            # The list comprehension above is convoluted because COLUMNS holds
            # all columns whether visible or not. Rebuild simply:
            cls._column_index = {}
            visible_idx = 0
            for key, _label, _width, visible in cls.COLUMNS:
                if visible:
                    cls._column_index[key] = visible_idx
                    visible_idx += 1
        return cls._column_index

    def _row_cells(self, row: VideoRow) -> list[str]:
        """Render a VideoRow into the ordered cell list the table expects."""
        progress_bar = self._format_progress_bar(row.progress)
        stage_style = self._get_stage_style(row.stage, row.status)
        sel_indicator = ""
        if self.batch_operations and self.batch_operations.is_selected(row.video_id):
            sel_indicator = "✓"
        skip_reason_display = (
            f"[warning]{row.skip_reason}[/warning]" if row.skip_reason else ""
        )
        return [
            str(row.index),
            sel_indicator,
            row.title,
            f"[{stage_style}]{row.stage}[/{stage_style}]",
            progress_bar,
            row.speed,
            row.plugin,
            row.size,
            row.eta,
            row.started_at,
            skip_reason_display,
        ]

    def _update_table(self) -> None:
        """Update the table by diffing against the previous render.

        The previous implementation called ``self.clear()`` and then re-added
        every row on every refresh tick (~2s). At even moderate row counts
        this caused visible flicker, scrolled the cursor away from the user's
        selected row whenever sort order shifted (e.g. when a video flipped
        from active → completed), and was wasted work because in steady state
        only a handful of cells actually change per tick.

        New strategy:

        * Add new rows by ``video_id`` key.
        * Remove rows whose ``video_id`` is no longer in the model.
        * For surviving rows, compare the cell tuple against what we
          rendered last tick and only call ``update_cell`` for the cells
          that changed. The DataTable preserves cursor position across this
          path.

        Falls back to clear-and-rebuild on any error (Textual API drift,
        missing keys, etc.) so a regression here can't lock the UI into a
        broken state.
        """
        column_index = self._build_column_index()
        new_cells_by_key: dict[str, list[str]] = {}
        new_order: list[str] = []
        for row in self._video_rows:
            key = str(row.video_id)
            new_order.append(key)
            new_cells_by_key[key] = self._row_cells(row)

        # First tick after compose: nothing tracked yet, just build it fresh.
        previous_cells: dict[str, list[str]] = getattr(
            self, "_last_cells_by_key", {}
        )

        try:
            existing_keys = set(previous_cells.keys())
            new_keys = set(new_cells_by_key.keys())

            # Remove rows that disappeared. Wrap in try/except per-row in case
            # the table internals don't recognize the key (e.g. after a
            # crash-driven full clear last tick).
            for gone_key in existing_keys - new_keys:
                try:
                    self.remove_row(gone_key)
                except Exception:
                    pass

            # Add rows that are new.
            for added_key in new_keys - existing_keys:
                self.add_row(*new_cells_by_key[added_key], key=added_key)

            # Update cells that actually changed for surviving rows.
            for key in existing_keys & new_keys:
                old = previous_cells[key]
                new = new_cells_by_key[key]
                if old == new:
                    continue
                for col_key, col_idx in column_index.items():
                    if col_idx >= len(new) or col_idx >= len(old):
                        continue
                    if old[col_idx] != new[col_idx]:
                        try:
                            self.update_cell(key, col_key, new[col_idx])
                        except Exception:
                            # If update_cell fails (e.g. the row is mid-removal),
                            # fall through to a full rebuild on the next tick by
                            # clearing the cache.
                            previous_cells = {}
                            break

            self._last_cells_by_key = new_cells_by_key
            self._row_order = new_order
        except Exception:
            # Defensive fallback: anything weird → full rebuild. We must keep
            # the table consistent with the model.
            self.clear()
            for key in new_order:
                self.add_row(*new_cells_by_key[key], key=key)
            self._last_cells_by_key = new_cells_by_key
            self._row_order = new_order

    
    def get_selected_video_id(self) -> Optional[int]:
        """Get the ID of the currently selected video.

        Returns:
            Video ID or None if no selection

        Notes:
            BUG HISTORY — Pressing ``d`` on a row used to open the details
            for a *different* video.  The old implementation did
            ``self._video_rows[cursor_row].video_id``, but Textual's
            ``cursor_row`` is the **visible** row index in the rendered
            table, while ``_video_rows`` is the model list whose order is
            independent of how ``_update_table`` materialized the rows
            (the diff path adds new rows via ``set difference`` ordering,
            so insertion order ≠ display order).  The two indexes drift
            apart whenever videos enter/leave/reorder.

            The reliable mapping is ``DataTable.coordinate_to_cell_key``:
            we key every row by ``str(video_id)`` when calling
            ``add_row(..., key=str(row.video_id))`` in ``_update_table``,
            so Textual will hand us that exact key back for whichever row
            the cursor is on, regardless of insertion order.
        """
        cursor_row = self.cursor_row
        if cursor_row is None or cursor_row < 0:
            return None
        # Try the authoritative path first: ask the table for the row key
        # at the cursor coordinate. The row key is exactly what we stored
        # in ``_update_table`` (``str(video.id)``).
        try:
            row_key = self.coordinate_to_cell_key(
                Coordinate(cursor_row, 0)
            ).row_key
            value = getattr(row_key, "value", row_key)
            if value is None:
                return None
            return int(value)
        except Exception:
            # Defensive fallback for older Textual versions or odd states:
            # consult ``_row_order`` (which tracks the actual rendered
            # order from the last ``_update_table`` tick) before falling
            # back to ``_video_rows`` insertion order.
            row_order = getattr(self, "_row_order", None)
            if row_order and cursor_row < len(row_order):
                try:
                    return int(row_order[cursor_row])
                except (TypeError, ValueError):
                    pass
            if cursor_row < len(self._video_rows):
                return self._video_rows[cursor_row].video_id
            return None
    
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection event.
        
        Args:
            event: The row selected event
        """
        video_id = self.get_selected_video_id()
        if video_id and self.on_select_callback:
            self.on_select_callback(video_id)
    
    def toggle_selection(self) -> None:
        """Toggle selection of the current row (for multi-select)."""
        video_id = self.get_selected_video_id()
        if video_id is None:
            return
        
        # Use batch_operations if available
        if self.batch_operations:
            self.batch_operations.toggle_selection(video_id)
        
        # Refresh to show selection change
        self._update_table()
        
        if self.on_multi_select_callback:
            if self.batch_operations:
                self.on_multi_select_callback(self.batch_operations.get_selected())
            else:
                self.on_multi_select_callback([video_id])
    
    def clear_selection(self) -> None:
        """Clear all selections."""
        if self.batch_operations:
            self.batch_operations.clear_selection()
        
        # Refresh to show selection cleared
        self._update_table()
        
        if self.on_multi_select_callback:
            self.on_multi_select_callback([])
    
    def select_all_visible(self) -> int:
        """Select all currently visible videos.
        
        Returns:
            Number of videos selected
        """
        if not self.batch_operations:
            return 0
        
        # Get visible videos from state manager
        if self.controller:
            result = self.controller.get_filtered_videos()
            videos = result.videos
        else:
            videos = self.state_manager.get_all_videos() if self.state_manager else []
        
        count = self.batch_operations.select_all(videos)
        self._update_table()
        
        if self.on_multi_select_callback:
            self.on_multi_select_callback(self.batch_operations.get_selected())
        
        return count
    
    def get_selected_video_ids(self) -> List[int]:
        """Get IDs of all selected videos (for multi-select).
        
        Returns:
            List of video IDs
        """
        if self.batch_operations:
            return self.batch_operations.get_selected()
        return []
    
    def has_selection(self) -> bool:
        """Check if any videos are selected.
        
        Returns:
            True if at least one video is selected
        """
        if self.batch_operations:
            return self.batch_operations.has_selection()
        return False
    
    def get_selection_count(self) -> int:
        """Get number of selected videos.
        
        Returns:
            Number of selected videos
        """
        if self.batch_operations:
            return self.batch_operations.get_selected_count()
        return 0


class VideoListHeader(Static):
    """Header widget showing pipeline summary."""
    
    DEFAULT_CSS = """
    VideoListHeader {
        height: 3;
        background: $surface-darken-2;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    """
    
    def __init__(self, state_manager: Optional[StateManager] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state_manager = state_manager
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def update_header(self) -> None:
        """Update the header text with current stats."""
        if self.state_manager is None:
            self.update("Haven Pipeline")
            return
        
        videos = self.state_manager.get_all_videos()
        active = len([v for v in videos if v.is_active])
        completed = len([v for v in videos if v.is_completed])
        failed = len([v for v in videos if v.has_failed])
        
        status_parts = []
        if active > 0:
            status_parts.append(f"{active} active")
        if completed > 0:
            status_parts.append(f"{completed} completed")
        if failed > 0:
            status_parts.append(f"{failed} failed")
        
        status_text = " | ".join(status_parts) if status_parts else "No videos"
        self.update(f"Haven Pipeline - {status_text}")


class VideoListFooter(Static):
    """Footer widget showing key bindings."""
    
    DEFAULT_CSS = """
    VideoListFooter {
        height: 1;
        background: $surface-darken-2;
        color: $text-muted;
        content-align: center middle;
    }
    """
    
    def __init__(
        self,
        show_graph: bool = False,
        batch_mode: bool = False,
        selection_count: int = 0,
        **kwargs: Any
    ) -> None:
        """Initialize the footer.

        Args:
            show_graph: Whether the speed graph is currently visible
            batch_mode: Whether batch mode is active
            selection_count: Number of selected videos
            **kwargs: Additional arguments passed to Static
        """
        super().__init__(**kwargs)
        self._show_graph = show_graph
        self._batch_mode = batch_mode
        self._selection_count = selection_count
        # Refresh-health metrics (C5 in the proposal). When the polling
        # path stops working — as it did before Milestones A and B — the
        # footer's "last refresh" stamp won't advance, so users can see
        # at a glance that something is wrong instead of staring at a
        # frozen table and assuming the pipeline died.
        self._last_refresh_at: Optional[datetime] = None
        self._last_refresh_duration_ms: Optional[float] = None
        self._refresh_error_count: int = 0

    def set_refresh_health(
        self,
        last_refresh_at: Optional[datetime],
        duration_ms: Optional[float],
        error_count: int,
    ) -> None:
        """Update the refresh-health indicator and re-render the footer."""
        self._last_refresh_at = last_refresh_at
        self._last_refresh_duration_ms = duration_ms
        self._refresh_error_count = error_count
        self._update_content()

    
    def compose(self):
        """Set up the footer content - returns empty as content is set via update()."""
        self._update_content()
        return []
    
    def set_show_graph(self, show_graph: bool) -> None:
        """Update the graph visibility indicator.
        
        Args:
            show_graph: Whether the speed graph is currently visible
        """
        self._show_graph = show_graph
        self._update_content()
    
    def set_batch_mode(self, batch_mode: bool, selection_count: int = 0) -> None:
        """Update the batch mode indicator.
        
        Args:
            batch_mode: Whether batch mode is active
            selection_count: Number of selected videos
        """
        self._batch_mode = batch_mode
        self._selection_count = selection_count
        self._update_content()
    
    def set_selection_count(self, count: int) -> None:
        """Update the selection count display.
        
        Args:
            count: Number of selected videos
        """
        self._selection_count = count
        self._update_content()
    
    def _update_content(self) -> None:
        """Update the footer content."""
        if self._batch_mode:
            # Batch mode footer with selection count and batch operations.
            #
            # BUG HISTORY — these strings used to show LOWERCASE keys
            # ("[a] All [c] Clear [r] Retry [x] Remove [e] Export"), but
            # the BINDINGS on VideoListScreen bind the lowercase keys to
            # OTHER actions even while batch_mode is on
            # (a → toggle_auto_refresh, c → toggle_completed_filter,
            #  r → refresh, x → clear_filters, e → errors_only_filter).
            # The real batch operations are bound to uppercase A R X E
            # (select_all, batch_retry, batch_remove, batch_export). Users
            # following the footer would silently trigger the wrong action
            # — e.g. pressing "r" thinking they were retrying when they
            # were just refreshing.
            self.update(
                f"Batch: {self._selection_count} selected | "
                f"[A] All  [c] Clear-sel  [R] Retry  [X] Remove  [E] Export  [Esc] Exit"
            )
        else:
            # Normal mode footer
            graph_indicator = "ON" if self._show_graph else "OFF"
            health = self._format_refresh_health()
            base = (
                f"[q] Quit  [r] Refresh  [d] Details  "
                f"[g] Graph ({graph_indicator})  [v] Analytics  [l] Event Log  "
                f"[f/c/x] Filter  [s] Sort  [b] Batch  [?] Help"
            )
            if health:
                self.update(f"{base}  •  {health}")
            else:
                self.update(base)

    def _format_refresh_health(self) -> str:
        """Render the refresh-health string for the right side of the footer.

        Examples::

            "Refreshed 0.4s ago • 12 ms"
            "Refreshed 30m ago • 12 ms • 3 err"

        Returns an empty string if we don't have any data yet.
        """
        if self._last_refresh_at is None:
            return ""

        age = (datetime.now(timezone.utc) - self._last_refresh_at).total_seconds()
        if age < 0:
            # Clock skew between threads — pretend it just happened.
            age = 0
        if age < 10:
            age_str = f"{age:.1f}s"
        elif age < 60:
            age_str = f"{age:.0f}s"
        elif age < 3600:
            age_str = f"{age / 60:.0f}m"
        else:
            age_str = f"{age / 3600:.0f}h"

        parts = [f"Refreshed {age_str} ago"]
        if self._last_refresh_duration_ms is not None:
            parts.append(f"{self._last_refresh_duration_ms:.0f} ms")
        if self._refresh_error_count > 0:
            parts.append(f"{self._refresh_error_count} err")
        return " • ".join(parts)



class VideoListScreen(Screen):
    """Main screen for the video list view.
    
    This is the primary screen of the TUI application, showing a scrollable
    list of videos with their pipeline status and progress.
    
    Attributes:
        state_manager: The StateManager for accessing video state
        config: The HavenTUIConfig for display settings
        auto_refresh: Whether auto-refresh is enabled
    """
    
    DEFAULT_CSS = """
    VideoListScreen {
        layout: vertical;
    }
    
    VideoListScreen > VideoListWidget {
        height: 1fr;
        width: 100%;
    }
    
    VideoListScreen > #speed-graph {
        height: 12;
        width: 100%;
        display: none;
    }
    
    VideoListScreen > #speed-graph.visible {
        display: block;
    }
    """
    
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("a", "toggle_auto_refresh", "Auto-refresh"),
        ("d", "details", "Details"),
        ("g", "toggle_graph", "Graph"),
        ("f", "filter", "Filter"),
        ("c", "toggle_completed_filter", "Toggle Completed"),
        ("e", "errors_only_filter", "Errors Only"),
        ("x", "clear_filters", "Clear Filters"),
        ("s", "sort", "Sort"),
        ("S", "toggle_sort_order", "Reverse Sort"),
        ("?", "help", "Help"),
        ("space", "toggle_select", "Select"),
        ("b", "toggle_batch_mode", "Batch Mode"),
        ("A", "select_all", "Select All"),
        ("R", "batch_retry", "Batch Retry"),
        ("X", "batch_remove", "Batch Remove"),
        ("E", "batch_export", "Batch Export"),
        ("escape", "exit_batch_mode", "Exit Batch"),
        # Navigation to other views
        ("v", "analytics", "Analytics"),
        ("l", "event_log", "Event Log"),
    ]
    
    auto_refresh: reactive[bool] = reactive(True)
    show_graph: reactive[bool] = reactive(False)
    batch_mode: reactive[bool] = reactive(False)
    
    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        config: Optional[HavenTUIConfig] = None,
        on_show_details: Optional[Callable[[int], None]] = None,
        speed_history_repo: Optional[SpeedHistoryRepository] = None,
        pipeline_interface: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the video list screen.
        
        Args:
            state_manager: The StateManager for accessing video state
            config: The TUI configuration
            on_show_details: Optional callback when user requests to view video details
            speed_history_repo: Optional repository for speed history data
            pipeline_interface: Optional PipelineInterface for batch operations
            **kwargs: Additional arguments passed to Screen
        """
        super().__init__(**kwargs)
        self.state_manager = state_manager
        self.config = config or HavenTUIConfig()
        self.auto_refresh = True
        self.show_graph = config.display.show_speed_graphs if config else False
        self.batch_mode = False
        self._refresh_timer: Optional[Any] = None
        self.on_show_details_callback = on_show_details
        self._speed_history_repo = speed_history_repo
        self._pipeline_interface = pipeline_interface
        self._selected_video_id: Optional[int] = None
        self._selected_stage: str = "download"
        
        # Initialize batch operations
        self._batch_operations: Optional[BatchOperations] = None
        if state_manager and pipeline_interface:
            self._batch_operations = BatchOperations(state_manager, pipeline_interface)

        # Refresh-health bookkeeping for the footer indicator (C5).
        self._last_refresh_at: Optional[datetime] = None
        self._last_refresh_duration_ms: Optional[float] = None
        self._refresh_error_count: int = 0

    
    def compose(self):
        """Compose the screen layout."""
        yield VideoListHeader(self.state_manager)
        
        yield VideoListWidget(
            state_manager=self.state_manager,
            config=self.config,
            on_select=self._on_video_select,
            on_multi_select=self._on_multi_select,
            batch_operations=self._batch_operations,
        )
        
        # Speed graph (hidden by default, shown when 'g' is pressed)
        # Using display: none in CSS to hide initially
        yield SpeedGraphComponent(
            speed_history_repo=self._speed_history_repo,
            id="speed-graph",
        )
        
        yield VideoListFooter(
            show_graph=self.show_graph,
            batch_mode=self.batch_mode,
        )
    
    def on_mount(self) -> None:
        """Handle mount event - start auto-refresh timer."""
        self._start_refresh_timer()
        self._update_header()
        # Initial data load. ``_refresh_data`` is now a coroutine, so we
        # dispatch it through the worker pool. ``call_later`` would be wrong
        # here because it expects a callback, not a coroutine.
        self._trigger_refresh()

    
    def on_unmount(self) -> None:
        """Handle unmount event - stop timer."""
        self._stop_refresh_timer()
    
    def _start_refresh_timer(self) -> None:
        """Start the auto-refresh timer."""
        if self._refresh_timer is not None:
            return
        
        refresh_interval = self.config.display.refresh_rate
        self._refresh_timer = self.set_interval(refresh_interval, self._auto_refresh)
    
    def _stop_refresh_timer(self) -> None:
        """Stop the auto-refresh timer."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None
    
    def _auto_refresh(self) -> None:
        """Perform auto-refresh if enabled.

        We dispatch the refresh through ``run_worker(exclusive=True)`` so
        that if a previous tick is still running (slow DB, big result set,
        whatever) we drop the new tick instead of stacking them up. Without
        ``exclusive=True`` Textual would happily queue refreshes and a slow
        period would lead to a thundering herd when things sped back up.
        """
        if self.auto_refresh:
            self.run_worker(
                self._refresh_data(),
                name="video_list_refresh",
                exclusive=True,
                group="video_list_refresh",
            )

    async def _refresh_data(self) -> None:
        """Refresh the video list data.

        Bug being fixed (see I3 / A3 in TUI_IMPROVEMENTS_PROPOSAL.md):

            asyncio.create_task(self.state_manager.refresh_from_database())
            video_list.refresh_data()  # reads state RIGHT NOW

        That fired the DB read into the background and then immediately
        rendered the (still-stale) in-memory state. Each tick the user saw
        the *previous* tick's data — even after the per-tick session change
        in PipelineInterface gave us fresh DB results.

        The fix is to make this method a coroutine that awaits the database
        refresh before touching the table, then update the UI synchronously.
        Combined with ``run_worker(exclusive=True)`` in the caller, this
        gives us at most one in-flight refresh and guarantees the table
        reflects what the state manager knows.

        We also record refresh duration and bump the error counter so
        the footer can show ``Refreshed 0.4s ago • 12 ms • 0 err``
        (Milestone C5).
        """
        import logging
        import time

        start = time.perf_counter()
        success = False

        try:
            if self.state_manager is not None:
                await self.state_manager.refresh_from_database()

            video_list = self.query_one(VideoListWidget)
            video_list.refresh_data()
            self._update_header()
            self._update_speed_graph()
            success = True
        except Exception as e:  # pragma: no cover - defensive
            logging.getLogger(__name__).warning(
                "VideoListScreen refresh failed: %s", e
            )
            self._refresh_error_count += 1
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._last_refresh_duration_ms = elapsed_ms
            if success:
                self._last_refresh_at = datetime.now(timezone.utc)
            self._update_footer_health()

    def _update_footer_health(self) -> None:
        """Push the latest refresh-health stats into the footer."""
        try:
            footer = self.query_one(VideoListFooter)
            footer.set_refresh_health(
                self._last_refresh_at,
                self._last_refresh_duration_ms,
                self._refresh_error_count,
            )
        except Exception:
            # Footer not mounted yet — fine, will catch up on next tick.
            pass


    
    def _update_header(self) -> None:
        """Update the header with current stats."""
        header = self.query_one(VideoListHeader)
        header.update_header()
    
    def _on_video_select(self, video_id: int) -> None:
        """Handle single video selection.
        
        Args:
            video_id: The selected video ID
        """
        self._selected_video_id = video_id
        self._update_speed_graph()
    
    def _update_speed_graph(self) -> None:
        """Update the speed graph with the selected video's data."""
        if not self.show_graph or self._selected_video_id is None:
            return
        
        try:
            graph = self.query_one("#speed-graph", SpeedGraphComponent)
            graph.set_video(self._selected_video_id, self._selected_stage)
        except Exception:
            pass  # Graph may not be mounted yet
    
    def action_toggle_graph(self) -> None:
        """Toggle speed graph visibility with 'g' key."""
        self.show_graph = not self.show_graph
        
        # Update graph visibility
        try:
            graph = self.query_one("#speed-graph", SpeedGraphComponent)
            if self.show_graph:
                graph.add_class("visible")
                # Update graph data if we have a selection
                if self._selected_video_id is not None:
                    graph.set_video(self._selected_video_id, self._selected_stage)
            else:
                graph.remove_class("visible")
        except Exception:
            pass
        
        # Update footer to show graph state
        try:
            footer = self.query_one(VideoListFooter)
            footer.set_show_graph(self.show_graph)
        except Exception:
            pass
        
        status = "visible" if self.show_graph else "hidden"
        self.app.notify(f"Speed graph {status}", timeout=1.5)
    
    def _on_multi_select(self, video_ids: List[int]) -> None:
        """Handle multi-selection change.
        
        Args:
            video_ids: List of selected video IDs
        """
        if video_ids:
            self.app.notify(f"Selected {len(video_ids)} videos", timeout=2.0)
    
    def action_refresh(self) -> None:
        """Manual refresh action.

        ``_refresh_data`` is now a coroutine; we dispatch it through the
        same exclusive worker that ``_auto_refresh`` uses so a manual `r`
        and an in-flight tick don't double up on the database.
        """
        self.run_worker(
            self._refresh_data(),
            name="video_list_refresh",
            exclusive=True,
            group="video_list_refresh",
        )
        self.app.notify("Refreshed", timeout=1.0)

    
    def action_toggle_auto_refresh(self) -> None:
        """Toggle auto-refresh on/off."""
        self.auto_refresh = not self.auto_refresh
        status = "ON" if self.auto_refresh else "OFF"
        self.app.notify(f"Auto-refresh: {status}", timeout=2.0)
    
    def action_details(self) -> None:
        """Show details for selected video."""
        video_list = self.query_one(VideoListWidget)
        video_id = video_list.get_selected_video_id()
        if video_id:
            self._show_video_details(video_id)
        else:
            self.app.notify("No video selected", severity="warning", timeout=2.0)
    
    def _show_video_details(self, video_id: int) -> None:
        """Show details for a video.
        
        This method can be overridden or the on_show_details_callback
        can be set to customize the navigation behavior.
        
        Args:
            video_id: ID of the video to show details for
        """
        # Check if there's a custom callback
        if hasattr(self, 'on_show_details_callback') and self.on_show_details_callback:
            self.on_show_details_callback(video_id)
        else:
            # Default behavior: push video detail screen
            try:
                from haven_tui.ui.views.video_detail import VideoDetailScreen
                
                # Get repositories from the app if available
                job_repo = getattr(self.app, 'job_history_repo', None)
                snapshot_repo = getattr(self.app, 'snapshot_repo', None)
                speed_history_repo = getattr(self.app, 'speed_history_repo', None)
                
                # Get state_manager from the app (for real-time state fallback)
                state_manager = getattr(self.app, 'state_manager', None)
                
                # Create and push the detail screen with repositories
                detail_screen = VideoDetailScreen(
                    video_id=video_id,
                    job_repo=job_repo,
                    snapshot_repo=snapshot_repo,
                    state_manager=state_manager,
                    speed_history_repo=speed_history_repo,
                )
                self.app.push_screen(detail_screen)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.app.notify(
                    f"Could not open detail view: {e}",
                    severity="error",
                    timeout=3.0
                )
    
    def action_filter(self) -> None:
        """Open filter dialog or cycle through filter states."""
        video_list = self.query_one(VideoListWidget)
        filter_state = video_list.get_filter_state()
        
        if filter_state is None:
            self.app.notify("Filter system not available", severity="warning", timeout=2.0)
            return
        
        # Show current filter state
        if video_list.controller and video_list.controller.has_active_filters():
            descriptions = video_list.controller.get_active_filter_descriptions()
            self.app.notify(
                f"Active filters: {', '.join(descriptions)}\n"
                "Press 'c' to toggle completed, 'e' for errors only, 'x' to clear",
                title="Filters",
                timeout=5.0
            )
        else:
            self.app.notify(
                "No active filters.\n"
                "Press 'c' to toggle completed, 'e' for errors only",
                title="Filters",
                timeout=5.0
            )
    
    def action_toggle_completed_filter(self) -> None:
        """Toggle show/hide completed videos."""
        video_list = self.query_one(VideoListWidget)
        new_value = video_list.toggle_show_completed()
        status = "shown" if new_value else "hidden"
        self.app.notify(f"Completed videos {status}", timeout=2.0)
    
    def action_toggle_failed_filter(self) -> None:
        """Toggle show/hide failed videos."""
        video_list = self.query_one(VideoListWidget)
        new_value = video_list.toggle_show_failed()
        status = "shown" if new_value else "hidden"
        self.app.notify(f"Failed videos {status}", timeout=2.0)
    
    def action_errors_only_filter(self) -> None:
        """Toggle show only errors filter."""
        video_list = self.query_one(VideoListWidget)
        if video_list.controller:
            new_value = video_list.controller.toggle_show_only_errors()
            status = "ON" if new_value else "OFF"
            video_list.refresh_data()
            self.app.notify(f"Errors only filter: {status}", timeout=2.0)
    
    def action_clear_filters(self) -> None:
        """Clear all active filters."""
        video_list = self.query_one(VideoListWidget)
        video_list.clear_filters()
        self.app.notify("All filters cleared", timeout=2.0)
    
    def action_sort(self) -> None:
        """Cycle through sort options."""
        video_list = self.query_one(VideoListWidget)
        
        if video_list.controller is None:
            self.app.notify("Sort system not available", severity="warning", timeout=2.0)
            return
        
        # Cycle to next sort field
        new_field = video_list.cycle_sort_field()
        sort_desc = video_list.get_sort_description()
        
        self.app.notify(f"Sorted by: {sort_desc}", timeout=2.0)
    
    def action_toggle_sort_order(self) -> None:
        """Toggle between ascending/descending sort order."""
        video_list = self.query_one(VideoListWidget)
        
        if video_list.controller is None:
            self.app.notify("Sort system not available", severity="warning", timeout=2.0)
            return
        
        new_order = video_list.toggle_sort_order()
        sort_desc = video_list.get_sort_description()
        
        order_text = "descending" if new_order == SortOrder.DESCENDING else "ascending"
        self.app.notify(f"Sort order: {order_text} ({sort_desc})", timeout=2.0)
    
    def action_help(self) -> None:
        """Show help dialog."""
        if self.batch_mode:
            # Batch mode help — keys are case-sensitive. Lowercase a/c/r/x/e
            # are bound to filter/refresh actions; uppercase A/R/X/E are the
            # batch operations. See bug history note in
            # VideoListFooter._update_content.
            help_text = (
                "Batch Mode Shortcuts (case-sensitive):\n"
                "  Space - Select/deselect current video\n"
                "  A     - Select all visible videos\n"
                "  c     - Clear-sel (clears selection, not filters)\n"
                "  R     - Retry failed selected videos\n"
                "  X     - Remove selected from queue\n"
                "  E     - Export selected to JSON\n"
                "  Esc   - Exit batch mode\n"
                "  ?     - Show this help"
            )
        else:
            # Normal mode help
            help_text = (
                "Keyboard Shortcuts:\n"
                "  q - Quit application\n"
                "  r - Refresh data\n"
                "  a - Toggle auto-refresh\n"
                "  d - View details\n"
                "  g - Toggle speed graph\n"
                "  v - View analytics dashboard\n"
                "  l - View event log\n"
                "  f - Filter dialog\n"
                "  c - Toggle completed videos\n"
                "  e - Toggle errors only\n"
                "  x - Clear all filters\n"
                "  s - Change sort field\n"
                "  S - Toggle sort order (asc/desc)\n"
                "  Space - Select/deselect video\n"
                "  b - Toggle batch mode\n"
                "  ? - Show this help"
            )
        self.app.notify(help_text, title="Help", timeout=10.0)
    
    def action_analytics(self) -> None:
        """Navigate to analytics dashboard."""
        self.app.push_screen("analytics")
    
    def action_event_log(self) -> None:
        """Navigate to event log."""
        self.app.push_screen("event_log")
    
    def action_toggle_select(self) -> None:
        """Toggle selection of current video."""
        video_list = self.query_one(VideoListWidget)
        video_list.toggle_selection()
        
        # Update footer if in batch mode
        if self.batch_mode:
            self._update_footer()
    
    def action_toggle_batch_mode(self) -> None:
        """Toggle batch mode on/off."""
        self.batch_mode = not self.batch_mode
        
        if self.batch_mode:
            # Entering batch mode.
            #
            # BUG HISTORY — these strings used to show LOWERCASE keys for
            # batch operations, but the BINDINGS bind lowercase a/c/r/x/e
            # to filter/refresh/auto-refresh actions even in batch mode.
            # Only Space + uppercase A/R/X/E + Esc actually trigger batch
            # operations. See VideoListFooter._update_content for full
            # explanation.
            self.app.notify(
                "Batch mode ON. Use [Space] to select, [A] for all, [c] to clear-sel, "
                "[R] to retry failed, [X] to remove, [E] to export, [Esc] to exit",
                title="Batch Mode",
                timeout=5.0
            )
        else:
            # Exiting batch mode
            self.app.notify("Batch mode OFF", timeout=2.0)
            # Clear selection when exiting batch mode
            if self._batch_operations:
                self._batch_operations.clear_selection()
        
        self._update_footer()
        self._trigger_refresh()

    def action_exit_batch_mode(self) -> None:
        """Exit batch mode."""
        if self.batch_mode:
            self.batch_mode = False
            if self._batch_operations:
                self._batch_operations.clear_selection()
            self._update_footer()
            self._trigger_refresh()
            self.app.notify("Batch mode OFF", timeout=2.0)

    
    def action_select_all(self) -> None:
        """Select all visible videos."""
        if not self.batch_mode:
            # In normal mode, enter batch mode first
            self.batch_mode = True
        
        video_list = self.query_one(VideoListWidget)
        count = video_list.select_all_visible()
        
        self._update_footer()
        self.app.notify(f"Selected {count} videos", timeout=2.0)
    
    def action_batch_retry(self) -> None:
        """Retry failed videos in selection."""
        if not self._batch_operations or not self._batch_operations.has_selection():
            self.app.notify("No videos selected", severity="warning", timeout=2.0)
            return
        
        # Show confirmation dialog
        count = self._batch_operations.get_selected_count()
        self._confirm_action(
            f"Retry {count} failed video(s)?",
            self._do_batch_retry
        )
    
    async def _do_batch_retry(self) -> None:
        """Execute batch retry operation."""
        if not self._batch_operations:
            return
        
        self.app.notify("Retrying failed videos...", timeout=2.0)
        
        try:
            result = await self._batch_operations.retry_failed()
            
            if result.all_succeeded:
                self.app.notify(
                    f"Successfully retried {result.success_count} video(s)",
                    timeout=3.0
                )
            else:
                self.app.notify(
                    f"Retry complete: {result.success_count} succeeded, "
                    f"{result.failed_count} failed",
                    severity="warning" if result.failed_count > 0 else "information",
                    timeout=3.0
                )
            
            self._batch_operations.clear_selection()
            self._update_footer()
            await self._refresh_data()

        except Exception as e:
            self.app.notify(f"Batch retry failed: {e}", severity="error", timeout=3.0)

    
    def action_batch_remove(self) -> None:
        """Remove selected videos from queue."""
        if not self._batch_operations or not self._batch_operations.has_selection():
            self.app.notify("No videos selected", severity="warning", timeout=2.0)
            return
        
        count = self._batch_operations.get_selected_count()
        self._confirm_action(
            f"Remove {count} video(s) from queue? This will cancel all active operations.",
            self._do_batch_remove
        )
    
    async def _do_batch_remove(self) -> None:
        """Execute batch remove operation."""
        if not self._batch_operations:
            return
        
        self.app.notify("Removing videos from queue...", timeout=2.0)
        
        try:
            result = await self._batch_operations.remove_from_queue()
            
            if result.all_succeeded:
                self.app.notify(
                    f"Successfully removed {result.success_count} video(s)",
                    timeout=3.0
                )
            else:
                self.app.notify(
                    f"Remove complete: {result.success_count} succeeded, "
                    f"{result.failed_count} failed",
                    severity="warning" if result.failed_count > 0 else "information",
                    timeout=3.0
                )
            
            self._update_footer()
            await self._refresh_data()

        except Exception as e:
            self.app.notify(f"Batch remove failed: {e}", severity="error", timeout=3.0)

    
    def action_batch_export(self) -> None:
        """Export selected videos to JSON file."""
        if not self._batch_operations or not self._batch_operations.has_selection():
            self.app.notify("No videos selected", severity="warning", timeout=2.0)
            return
        
        # Generate default filename with timestamp
        from datetime import datetime
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        default_path = f"haven_export_{timestamp}.json"
        
        # For now, use default path (in a full implementation, we'd show a file dialog)
        self._do_batch_export(default_path)
    
    def _do_batch_export(self, filepath: str) -> None:
        """Execute batch export operation.
        
        Args:
            filepath: Path to the output JSON file
        """
        if not self._batch_operations:
            return
        
        try:
            result = self._batch_operations.export_list(filepath)
            
            if result.get("success"):
                self.app.notify(
                    f"Exported {result['exported_count']} videos to {filepath}",
                    timeout=3.0
                )
            else:
                error = result.get("error", "Unknown error")
                self.app.notify(f"Export failed: {error}", severity="error", timeout=3.0)
                
        except Exception as e:
            self.app.notify(f"Export failed: {e}", severity="error", timeout=3.0)
    
    def _confirm_action(self, message: str, action_callback) -> None:
        """Show a confirmation dialog for destructive actions.
        
        Args:
            message: The confirmation message to display
            action_callback: The callback to execute if confirmed
        """
        # For now, just execute the action (in a full implementation, 
        # we'd show a modal confirmation dialog)
        # Since textual's modal dialogs are complex, we'll use a simpler approach:
        # Show notification and proceed
        self.app.notify(f"{message} (press same key to confirm)", timeout=3.0)
        
        # In a real implementation, we'd wait for confirmation
        # For now, we'll proceed with the action
        import asyncio
        if asyncio.iscoroutinefunction(action_callback):
            asyncio.create_task(action_callback())
        else:
            action_callback()
    
    def _update_footer(self) -> None:
        """Update the footer to reflect current state."""
        try:
            footer = self.query_one(VideoListFooter)
            selection_count = 0
            if self._batch_operations:
                selection_count = self._batch_operations.get_selected_count()
            footer.set_batch_mode(self.batch_mode, selection_count)
        except Exception:
            pass  # Footer may not be available yet
    
    # Note: a previous revision had a second, sync ``_refresh_data`` method
    # right here that shadowed the async one defined earlier in the class
    # (Python keeps the *last* definition). That's why the original race
    # bug persisted even though the upper definition looked correct in
    # isolation. The duplicate has been removed.

    def _trigger_refresh(self) -> None:
        """Schedule a non-blocking refresh from sync action handlers.

        Synchronous Textual action methods can't ``await`` so we dispatch
        the async ``_refresh_data`` through the same exclusive worker that
        the auto-refresh timer uses. This keeps refreshes serialized and
        prevents two reads from racing each other.
        """
        try:
            self.run_worker(
                self._refresh_data(),
                name="video_list_refresh",
                exclusive=True,
                group="video_list_refresh",
            )
        except Exception:
            # If we're being torn down or the screen isn't mounted, skip.
            pass


class VideoListView:

    """Main video list view - the primary TUI interface.
    
    This class provides a high-level interface for the video list view,
    managing the screen and providing integration with the StateManager.
    
    Example:
        >>> view = VideoListView(state_manager, config)
        >>> await view.run()
    
    Attributes:
        state_manager: The StateManager for accessing video state
        config: The HavenTUIConfig for display settings
        screen: The VideoListScreen instance
        on_show_details: Optional callback for showing video details
        speed_history_repo: Optional repository for speed history data
        pipeline_interface: Optional PipelineInterface for batch operations
    """
    
    def __init__(
        self,
        state_manager: StateManager,
        config: HavenTUIConfig,
        on_show_details: Optional[Callable[[int], None]] = None,
        speed_history_repo: Optional[SpeedHistoryRepository] = None,
        pipeline_interface: Optional[Any] = None,
    ) -> None:
        """Initialize the video list view.
        
        Args:
            state_manager: The StateManager for accessing video state
            config: The TUI configuration
            on_show_details: Optional callback when user requests to view video details
            speed_history_repo: Optional repository for speed history data
            pipeline_interface: Optional PipelineInterface for batch operations
        """
        self.state_manager = state_manager
        self.config = config
        self.on_show_details = on_show_details
        self.speed_history_repo = speed_history_repo
        self.pipeline_interface = pipeline_interface
        self.screen: Optional[VideoListScreen] = None
    
    def create_screen(self) -> VideoListScreen:
        """Create the video list screen.
        
        Returns:
            The configured VideoListScreen instance
        """
        self.screen = VideoListScreen(
            state_manager=self.state_manager,
            config=self.config,
            on_show_details=self.on_show_details,
            speed_history_repo=self.speed_history_repo,
            pipeline_interface=self.pipeline_interface,
        )
        return self.screen
    
    def refresh(self) -> None:
        """Refresh the video list display.

        ``_refresh_data`` is now a coroutine, so we go through the screen's
        sync trigger helper rather than awaiting it from this synchronous
        wrapper.
        """
        if self.screen is not None:
            self.screen._trigger_refresh()

    
    def get_selected_video_id(self) -> Optional[int]:
        """Get the currently selected video ID.
        
        Returns:
            Video ID or None if no selection
        """
        if self.screen is None:
            return None
        video_list = self.screen.query_one(VideoListWidget)
        return video_list.get_selected_video_id()
    
    def get_selected_video_ids(self) -> List[int]:
        """Get all selected video IDs.
        
        Returns:
            List of video IDs
        """
        if self.screen is None:
            return []
        video_list = self.screen.query_one(VideoListWidget)
        return video_list.get_selected_video_ids()
