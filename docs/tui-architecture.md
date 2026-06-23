# Haven TUI Architecture Documentation

Technical documentation of the Haven TUI architecture, data flow, and component design.

> **Recent changes (Milestones A & B from
> [`TUI_IMPROVEMENTS_PROPOSAL.md`](./TUI_IMPROVEMENTS_PROPOSAL.md))**
>
> The architecture described below was significantly reworked to fix the
> "TUI looks frozen until I close and reopen it" bug. The two structural
> changes are:
>
> 1. **No more long-lived SQLAlchemy session.** `PipelineInterface` now
>    holds a `sessionmaker` factory and every public method opens a
>    short-lived session, so each refresh tick sees the latest committed
>    data instead of a frozen snapshot. The SQLite engine also runs in
>    WAL mode now (`haven_cli/database/connection.py`).
> 2. **Cross-process events via a tail table.** The daemon writes every
>    event it publishes to a new `pipeline_events` SQLite table; the TUI
>    runs a `SqliteEventConsumer` that tails that table and replays
>    events onto its in-process bus. `StateManager` is subscribed to
>    that bus, so cross-process progress now flows end-to-end.
>
> If you're updating this document, treat the rest of it as the
> *current* architecture; the two notes above just summarise the
> transition.

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagrams](#architecture-diagrams)
3. [Component Structure](#component-structure)
4. [Data Flow](#data-flow)
5. [State Management](#state-management)
6. [Event System](#event-system)
7. [UI Components](#ui-components)
8. [Data Access Layer](#data-access-layer)
9. [Plugin Integration](#plugin-integration)
10. [Configuration System](#configuration-system)
11. [Cross-process Event Flow (Milestone B)](#cross-process-event-flow-milestone-b)

---

## System Overview

Haven TUI is a Terminal User Interface built on the [Textual](https://textual.textualize.io/) framework. It provides real-time visualization of the Haven video archival pipeline.

### Key Design Principles

- **Reactive UI**: Components update automatically when underlying data changes.
- **Repository Pattern**: Data access abstracted through repository interfaces. Repositories are stateless w.r.t. session lifetime — the app passes them a session factory, not a session.
- **Cross-process event delivery**: The daemon's in-process `EventBus` is mirrored to a `pipeline_events` SQLite table that the TUI tails. `StateManager` is subscribed to the TUI-side bus and only sees events after the consumer replays them.
- **Modular Views**: Separate screens for different views (list, detail, analytics).
- **Short-lived database sessions**: Every read or write opens its own session and closes it before returning. We do **not** hold a long-lived `Session` because under SQLite that pins a frozen reader snapshot — see Milestone A in the proposal.

### Technology Stack

| Layer | Technology |
|-------|------------|
| UI Framework | Textual (Python) |
| Database | SQLite (WAL mode) |
| Event Consumption | `pipeline_events` table tail + in-process `EventBus` |
| Graphing | plotille (ASCII) / fallback |
| Styling | CSS (Textual flavor) |
| Logging | rotating file handler at `~/.local/share/haven/tui.log` (`HAVEN_TUI_DEBUG=1` for DEBUG) |


---

## Architecture Diagrams

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         HAVEN TUI                                    │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │   Video List    │  │  Video Detail   │  │ Analytics Dashboard │  │
│  │     Screen      │  │     Screen      │  │      Screen         │  │
│  └────────┬────────┘  └────────┬────────┘  └──────────┬──────────┘  │
│           │                    │                      │             │
│           └────────────────────┼──────────────────────┘             │
│                                │                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                     UI Components                            │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │   │
│  │  │DataTable │  │  Static  │  │  Input   │  │ SpeedGraph   │ │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                │                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   Core Controllers                           │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │   │
│  │  │VideoList     │  │   State      │  │  BatchOperations │   │   │
│  │  │Controller    │  │   Manager    │  │                  │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                │                                    │
└────────────────────────────────┼────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Data Access Layer                               │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │Repositories  │  │  Event       │  │   Speed      │              │
│  │  (Models)    │  │  Consumer    │  │  Aggregator  │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└────────────────────────────────┼────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Haven Database                                 │
│                                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │  videos  │ │ downloads│ │upload_jobs│ │sync_jobs │ │ pipeline │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ │snapshots │  │
│                                                      └──────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Haven CLI / Daemon                               │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │   Pipeline   │  │   Plugins    │  │  Scheduler   │              │
│  │   Manager    │  │   (YouTube,  │  │              │              │
│  │              │  │  BitTorrent) │  │              │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Interaction Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                           User Input                                  │
│                    (Keyboard / Actions)                               │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Screen Classes                                │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────────┐  │
│  │VideoListScreen │  │VideoDetailScreen│  │AnalyticsDashboardScreen│  │
│  │                │  │                │  │                        │  │
│  │ • BINDINGS[]   │  │ • BINDINGS[]   │  │ • BINDINGS[]           │  │
│  │ • compose()    │  │ • compose()    │  │ • compose()            │  │
│  │ • action_*()   │  │ • action_*()   │  │ • action_*()           │  │
│  └────────┬───────┘  └────────┬───────┘  └────────────┬───────────┘  │
└───────────┼───────────────────┼───────────────────────┼──────────────┘
            │                   │                       │
            └───────────────────┼───────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        Core Controllers                               │
│  ┌──────────────────────┐  ┌──────────────────────┐                  │
│  │  VideoListController │  │     StateManager     │                  │
│  │                      │  │                      │                  │
│  │ • Filter/Sort logic  │  │ • Video state cache  │                  │
│  │ • Search functionality│ │ • Change callbacks   │                  │
│  └──────────┬───────────┘  └──────────┬───────────┘                  │
└─────────────┼─────────────────────────┼────────────────────────────────┘
              │                         │
              │    ┌────────────────────┘
              │    │
              ▼    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Data Repositories                                │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐              │
│  │ VideoSummary  │ │ JobHistory    │ │  Analytics    │              │
│  │  Repository   │ │  Repository   │ │  Repository   │              │
│  │               │ │               │ │               │              │
│  │ • get_all()   │ │ • get_history()│ │ • get_summary()│             │
│  │ • get_by_id() │ │ • get_cid()   │ │ • get_daily() │              │
│  └───────────────┘ └───────────────┘ └───────────────┘              │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Event Consumer                                   │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                        EventConsumer                            │  │
│  │                                                                 │  │
│  │  • Polls database for changes                                   │  │
│  │  • Triggers StateManager refresh                                │  │
│  │  • Rate-limited updates (configurable)                          │  │
│  │                                                                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Component Structure

### Package Organization

```
haven_tui/
├── __init__.py                 # Package initialization
├── config.py                   # TUI configuration
├── config_editor.py            # Configuration editor UI
│
├── core/                       # Core business logic
│   ├── __init__.py
│   ├── controller.py           # Video list controller (filter/sort)
│   ├── metrics.py              # Metrics collection
│   ├── pipeline_interface.py   # Pipeline interaction
│   └── state_manager.py        # State management
│
├── data/                       # Data access layer
│   ├── __init__.py
│   ├── download_tracker.py     # Download progress tracking
│   ├── event_consumer.py       # Database event consumption
│   ├── refresher.py            # Auto-refresh logic
│   ├── repositories.py         # Repository pattern implementation
│   ├── speed_aggregator.py     # Speed data aggregation
│   └── torrent_bridge.py       # BitTorrent integration
│
├── models/                     # View models
│   ├── __init__.py
│   └── video_view.py           # Video view models, enums
│
└── ui/                         # User interface
    ├── __init__.py
    ├── layout.py               # Layout utilities
    │
    ├── components/             # Reusable UI components
    │   ├── __init__.py
    │   └── speed_graph.py      # Speed graph component
    │
    └── views/                  # Screen/view implementations
        ├── __init__.py
        ├── analytics.py        # Analytics dashboard
        ├── event_log.py        # Event log view
        ├── video_detail.py     # Video detail view
        └── video_list.py       # Main video list view
```

### Core Classes

#### StateManager

Central cache of video pipeline state.

```python
class StateManager:
    """Manages video state from PipelineSnapshot table.
    
    Responsibilities:
    - Cache video states in memory
    - Notify listeners of changes
    - Aggregate data from multiple job tables
    """
    
    def get_all_videos() -> List[VideoState]
    def get_video(video_id: int) -> Optional[VideoState]
    def refresh() -> None
    def on_change(callback: Callable) -> None
```

#### VideoListController

Handles filtering, sorting, and search.

```python
class VideoListController:
    """Controller for video list with filtering/sorting.
    
    Responsibilities:
    - Apply filters to video list
    - Sort videos by criteria
    - Search across video fields
    """
    
    filter_state: FilterState
    sorter: VideoSorter
    
    def get_filtered_videos() -> FilterResult
    def set_filter_stage(stage: PipelineStage)
    def set_search_query(query: str)
    def cycle_sort_field()
```

#### VideoListScreen

Main screen showing video list.

```python
class VideoListScreen(Screen):
    """Main screen for video list view.
    
    Responsibilities:
    - Display video list table
    - Handle user input (keyboard)
    - Manage child widgets
    """
    
    BINDINGS = [...]
    
    def compose() -> ComposeResult
    def action_refresh()
    def action_toggle_graph()
    def action_toggle_batch_mode()
```

---

## Data Flow

### Initial Load Flow

```
┌──────────┐     ┌─────────────┐     ┌──────────────┐     ┌─────────┐
│  TUI     │────▶│  Repositories│────▶│ StateManager │────▶│  Views  │
│  Starts  │     │   (query)   │     │  (populate)  │     │ (render)│
└──────────┘     └─────────────┘     └──────────────┘     └─────────┘
```

1. TUI application starts
2. Repositories query database for initial state
3. StateManager populates its cache
4. Views render initial data

### Update Flow (Real-time)

```
┌──────────┐     ┌─────────────┐     ┌──────────────┐     ┌─────────┐
│ Database │────▶│EventConsumer│────▶│ StateManager │────▶│  Views  │
│ Changes  │     │  (detects)  │     │  (updates)   │     │ (refresh)│
└──────────┘     └─────────────┘     └──────────────┘     └─────────┘
```

1. Haven daemon updates database
2. EventConsumer polls for changes
3. StateManager updates its cache
4. Views refresh display

### User Action Flow

```
┌──────────┐     ┌─────────────┐     ┌──────────────┐     ┌─────────┐
│  User    │────▶│    View     │────▶│   Controller │────▶│  TUI    │
│  Input   │     │  (handles)  │     │   (process)  │     │ (update)│
└──────────┘     └─────────────┘     └──────────────┘     └─────────┘
       │                                                   │
       └───────────────────────────────────────────────────┘
                           (feedback)
```

1. User presses key (e.g., `r` to refresh)
2. View's `action_*` method handles input
3. Controller processes business logic
4. TUI updates display

---

## State Management

### VideoState

Aggregated state for a single video.

```python
@dataclass
class VideoState:
    """Aggregated video state from multiple job tables."""
    
    # Identity
    id: int
    title: str
    source_path: str
    
    # Pipeline state
    current_stage: str
    current_progress: float
    current_speed: float
    download_eta: Optional[int]
    
    # Status flags
    overall_status: str  # "active", "pending", "completed", "failed"
    is_completed: bool
    is_active: bool
    has_failed: bool
    
    # Metadata
    plugin: str
    created_at: Optional[datetime]
```

### State Aggregation

State is aggregated from multiple database tables:

| Table | Fields Used |
|-------|-------------|
| `videos` | id, title, file_path, created_at |
| `downloads` | progress, speed, status, eta |
| `upload_jobs` | progress, speed, status |
| `encryption_jobs` | progress, status |
| `analysis_jobs` | progress, status |
| `pipeline_snapshots` | cached aggregated state |

### Change Notification

```python
class StateManager:
    def __init__(self):
        self._change_callbacks: List[Callable] = []
    
    def on_change(self, callback: Callable):
        """Register for change notifications."""
        self._change_callbacks.append(callback)
    
    def _notify_change(self):
        """Notify all listeners of state change."""
        for callback in self._change_callbacks:
            callback()
```

---

## Event System

### EventConsumer

Polls database for changes at configurable intervals.

```python
class EventConsumer:
    """Consumes database events for real-time updates."""
    
    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.poll_interval = 5.0  # seconds
        self._last_check = datetime.min
    
    async def start(self):
        """Start polling loop."""
        while self._running:
            changes = self._check_for_changes()
            if changes:
                self.state_manager.refresh()
            await asyncio.sleep(self.poll_interval)
    
    def _check_for_changes(self) -> bool:
        """Check if any relevant tables have been modified."""
        # Query for recent changes since last check
        # Return True if changes detected
```

### Event Types

| Event | Source | Action |
|-------|--------|--------|
| `video_added` | New video inserted | Add to state cache |
| `video_updated` | Video modified | Update state cache |
| `video_completed` | All stages done | Mark completed |
| `video_failed` | Stage error | Mark failed |
| `progress_update` | Progress changed | Update progress bar |

---

## UI Components

### Widget Hierarchy

```
Screen (VideoListScreen)
└── Container (#video-list-container)
    ├── Container (#header-container)
    │   └── VideoListHeader
    ├── Container (#main-content)
    │   ├── Container (#list-container)
    │   │   └── VideoListWidget (DataTable)
    │   └── Container (#graph-container) [optional]
    │       └── SpeedGraphComponent
    └── Container (#footer-container)
        └── VideoListFooter
```

### Key Components

#### VideoListWidget

DataTable-based widget showing video list.

```python
class VideoListWidget(DataTable):
    """Scrollable table of videos with progress bars."""
    
    def refresh_data():
        """Fetch from StateManager and update table."""
        videos = self.controller.get_filtered_videos()
        self._update_table(videos)
    
    def _format_progress_bar(progress: float) -> str:
        """Create Unicode block progress bar."""
        filled = int((progress / 100) * width)
        return "█" * filled + "░" * (width - filled)
```

#### SpeedGraphComponent

ASCII speed visualization.

```python
class SpeedGraphComponent(Static):
    """Real-time speed graph using plotille or ASCII fallback."""
    
    video_id: reactive[Optional[int]]
    current_stage: reactive[str]
    
    def refresh_graph():
        """Load speed history and render graph."""
        history = self.repo.get_speed_history(video_id, stage)
        self.update(self._render_graph(history))
```

#### Analytics Dashboard Widgets

| Widget | Purpose |
|--------|---------|
| `ASCIIBarChart` | Generic bar chart (horizontal) |
| `HorizontalBarChart` | Daily video counts |
| `StageTimingChart` | Average time per stage |
| `SuccessRateChart` | Success/failure rates |
| `PluginUsageChart` | Plugin distribution |

---

## Data Access Layer

### Repository Pattern

All database access goes through repositories:

```python
class VideoSummaryRepository:
    """Repository for video summary queries."""
    
    def get_all(self) -> List[VideoSummary]:
        """Get all videos from PipelineSnapshot."""
        
    def get_by_id(self, video_id: int) -> Optional[VideoSummary]:
        """Get single video summary."""

class JobHistoryRepository:
    """Repository for job history queries."""
    
    def get_video_pipeline_history(self, video_id: int) -> Dict:
        """Get complete pipeline history from all job tables."""

class AnalyticsRepository:
    """Repository for analytics queries."""
    
    def get_pipeline_summary(self) -> Dict:
        """Get summary statistics."""
        
    def get_avg_time_per_stage(self, days: int) -> Dict[str, float]:
        """Get average processing time per stage."""
```

### Database Schema Reference

#### Key Tables

| Table | Purpose |
|-------|---------|
| `videos` | Core video metadata |
| `downloads` | Download job status |
| `upload_jobs` | Filecoin upload status |
| `encryption_jobs` | Haven-AOL encryption status |
| `analysis_jobs` | VLM analysis status |
| `sync_jobs` | Blockchain sync status |
| `pipeline_snapshots` | Aggregated state for TUI |
| `speed_history` | Speed data for graphs |
| `events` | System event log |

#### PipelineSnapshot Table

Pre-aggregated state optimized for TUI queries:

```sql
CREATE TABLE pipeline_snapshots (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL,
    title TEXT,
    current_stage TEXT,
    current_progress REAL,
    current_speed REAL,
    overall_status TEXT,
    is_completed BOOLEAN,
    is_active BOOLEAN,
    has_failed BOOLEAN,
    updated_at TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES videos(id)
);
```

---

## Plugin Integration

### Plugin Data Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Plugin    │────▶│   Haven     │────▶│  Database   │
│  (YouTube,  │     │   Daemon    │     │             │
│ BitTorrent) │     │             │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                                                ▼
                                         ┌─────────────┐
                                         │    TUI      │
                                         │  (read-only)│
                                         └─────────────┘
```

### Unified Download Interface

The TUI uses a unified interface for all download sources:

```python
class DownloadProgress:
    """Unified progress for any download type."""
    
    source_type: str  # "youtube", "bittorrent", "file"
    source_id: str
    
    # Progress
    bytes_downloaded: int
    bytes_total: int
    bytes_per_second: float
    
    # Status
    status: str  # "pending", "active", "paused", "completed", "failed"
    error_message: Optional[str]
```

### Speed Aggregation

```python
class SpeedAggregator:
    """Aggregates speeds from multiple download sources."""
    
    def get_total_speed(self) -> float:
        """Get combined speed of all active downloads."""
        
    def get_speed_history(self, seconds: int) -> List[SpeedDataPoint]:
        """Get speed history for graphing."""
```

---

## Configuration System

### Configuration Hierarchy

Configuration is loaded in priority order:

1. Command-line arguments (highest priority)
2. Environment variables (`HAVEN_TUI_*`)
3. User config file (`~/.config/haven/config.toml`)
4. Default values (lowest priority)

### Configuration Sections

```toml
[tui]
refresh_rate = 5.0              # Auto-refresh interval
show_speed_graphs = true        # Show graphs by default
graph_history_seconds = 60      # Graph time window

[tui.filters]
show_completed = true           # Default filter for completed
show_failed = true              # Default filter for failed
plugin_filter = "all"           # Default plugin filter

[tui.display]
compact_mode = false            # Compact display
zebra_stripes = true            # Alternating row colors
show_eta = true                 # Show ETA column
```

### Runtime Configuration

Some settings can be changed at runtime:

| Setting | Runtime Change | Persistence |
|---------|----------------|-------------|
| Refresh rate | Yes (keys `+`/`-`) | No |
| Show graphs | Yes (key `g`) | No |
| Filters | Yes (keys `c`, `f`, `e`) | No |
| Sort order | Yes (keys `s`, `S`) | No |
| Compact mode | Yes (key `Z`) | Yes |

---

## Threading Model

### Async Architecture

Haven TUI uses Python's `asyncio` for concurrency:

```
Main Thread
├── Textual App (async)
│   ├── UI Event Loop
│   ├── Keyboard Input
│   └── Screen Updates
│
└── Background Tasks
    ├── Event Consumer (polling)
    ├── Auto-refresh Timer
    └── Speed Graph Updates
```

### Database Access

- All database queries run in the main async loop
- Long queries should use `run_in_executor` to avoid blocking
- Connection pooling via SQLAlchemy

### UI Updates

- UI updates must happen on the main thread
- Background tasks use `app.call_from_thread()` for UI updates
- Reactive properties automatically trigger UI refresh

---

## Extension Points

### Custom Views

Create new screens by extending `Screen`:

```python
class CustomScreen(Screen):
    """Custom TUI screen."""
    
    BINDINGS = [
        ("q", "quit", "Quit"),
    ]
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield CustomWidget()
        yield Footer()
```

### Custom Components

Create reusable widgets:

```python
class CustomWidget(Static):
    """Custom widget with reactive properties."""
    
    value: reactive[str] = reactive("")
    
    def watch_value(self, new_value: str):
        """Auto-called when value changes."""
        self.update(f"Value: {new_value}")
```

### Custom Repositories

Add new data access patterns:

```python
class CustomRepository:
    """Custom data repository."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def custom_query(self) -> List[Model]:
        """Custom database query."""
        return self.session.query(Model).filter(...).all()
```

---

## Performance Considerations

### Optimization Strategies

| Strategy | Implementation |
|----------|----------------|
| Pagination | Query only visible rows |
| Caching | StateManager caches video state |
| Debouncing | Batch rapid updates |
| Lazy Loading | Load detail data on demand |
| Connection Pooling | Reuse database connections |

### Bottlenecks

| Component | Potential Issue | Mitigation |
|-----------|-----------------|------------|
| Database polling | CPU usage | Configurable poll interval |
| Large lists | Memory usage | Pagination, virtual scrolling |
| Speed graphs | Render time | Reduce history window |
| Analytics | Query time | Cache results, background refresh |

### Monitoring

Built-in metrics collection:

```python
# Track refresh latency
metrics.record_refresh_duration(duration_ms)

# Track query performance
metrics.record_query_time(query_name, duration_ms)

# Track memory usage
metrics.record_memory_usage(bytes_used)
```

---

## Cross-process Event Flow (Milestone B)

The TUI is normally launched as ``haven tui`` while the pipeline runs
as ``haven run daemon``. These are separate Python processes. The
in-process ``EventBus`` defined in ``haven_cli.pipeline.events`` is a
process-local singleton, so events emitted on the daemon's bus do not
naturally reach the TUI.

To bridge the gap we use a small SQLite tail table:

```
                              ┌──────────────────────────┐
                              │     SQLite database      │
  ┌───────────────────────┐   │                          │   ┌───────────────────────────┐
  │   haven run daemon    │   │   pipeline_events table  │   │        haven tui          │
  │                       │   │                          │   │                           │
  │  EventBus.publish(e)  │──▶│  INSERT (id auto-incr)   │──▶│  SqliteEventConsumer      │
  │   ├─ in-proc handlers │   │                          │   │   ├─ tail by last_id      │
  │   └─ persistent_sink  │   │  PRAGMA data_version     │   │   ├─ data_version pragma  │
  │      (make_sqlite_…)  │   │  bumps on every commit   │   │   └─ EventBus.publish(e)  │
  │                       │   │                          │   │                           │
  └───────────────────────┘   └──────────────────────────┘   │       ↓                   │
                                                              │  StateManager handlers    │
                                                              │  (pre-existing)           │
                                                              └───────────────────────────┘
```

### Writer side

* ``EventBus.attach_persistent_sink(make_sqlite_event_sink())`` is
  called once during ``HavenDaemon.start()``. The sink is invoked
  inside ``EventBus.publish`` after the in-process fan-out, runs in
  the default thread executor (so the writer's event loop isn't
  blocked on disk I/O), and writes one row per event with
  ``event_type``, ``correlation_id``, ``source`` and a JSON-encoded
  payload.
* ``Base.metadata.create_all`` in the daemon ensures the table exists
  even on a fresh database. The table is also created on demand by
  the consumer if the daemon hasn't run yet.

### Reader side

* The TUI app constructs ``SqliteEventConsumer`` with the
  pipeline-interface session factory and the local event bus, then
  ``await consumer.start()``.
* The consumer polls every 250 ms by default. On each tick it asks
  ``PRAGMA data_version`` first; if nothing has changed since last
  tick it returns immediately without doing a SELECT, keeping idle
  CPU near zero.
* Otherwise it does ``SELECT … WHERE id > last_id ORDER BY id LIMIT
  500``, parses each row back into an ``Event`` (including unknown
  ``event_type`` values, which are skipped but still advance the
  cursor), and ``await bus.publish(event)`` for each. The local bus is
  the same singleton ``StateManager`` subscribed to via
  ``PipelineInterface.on_event``, so existing TUI state-update
  handlers fire as if the events had been emitted in-process.
* On startup ``last_id`` is set to ``MAX(id)`` so the TUI doesn't
  replay yesterday's progress events. Pass ``catch_up_seconds`` to
  the consumer constructor if a short lookback is desired.

### WAL is required

This whole flow assumes WAL (`journal_mode=WAL`). Without WAL the
daemon's writer transaction would block the TUI's reads (or
vice-versa), and a long-running TUI read could starve the daemon's
event commits. ``haven_cli/database/connection.py`` enables WAL on
every new connection alongside ``synchronous=NORMAL`` and a 5-second
busy timeout.

### Failure modes and mitigations

| Failure | Effect | Mitigation |
|---------|--------|------------|
| Sink write fails | One event row missing | Sink logs warning; bus subscribers already received the event in the daemon. |
| Consumer cannot create the table | Cross-process events silently disabled | TUI falls back to polling; user still sees fresh data within `display.refresh_rate`. |
| TUI starts before daemon | `pipeline_events` doesn't exist yet | Consumer's `_fetch_batch` swallows `OperationalError` and runs `Base.metadata.create_all`. |
| TUI is older than daemon | Unknown `event_type` names appear | Consumer logs and skips, but advances `last_id` so it doesn't replay forever. |
| Database wiped while TUI is running | Cursor stale | Consumer detects table going missing on next poll, re-creates it, resets cursor to 0. |

### Tuning knobs

* ``poll_interval_seconds`` – default 0.25. Smaller is more CPU; larger
  is more latency.
* ``max_batch`` – default 500. Caps rows fetched per tick so a backlog
  doesn't stall the UI.
* ``HAVEN_TUI_DEBUG=1`` – enables DEBUG-level logging in
  ``~/.local/share/haven/tui.log`` so you can see exactly which
  events are being replayed.

### Observability

The footer of the video list screen now shows a refresh-health
indicator (Milestone C5):

```
[q] Quit  [r] Refresh  …  •  Refreshed 0.4s ago • 12 ms
```

If the polling path stops working — for instance because someone
re-introduces the long-lived session bug — the "ago" stamp will stop
advancing and the user will see the symptom immediately rather than
staring at frozen progress bars and assuming the pipeline died.

