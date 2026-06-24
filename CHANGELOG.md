# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed
- **Batch sync singleton dedup parity** (`BATCH_SYNC_REMEDIATION_PLAN.md`
  Phases 1–3). The accumulator and arkiv-sync code paths now produce the
  correct entity for `len == 1` flushes. Previously a daemon flushing a
  single re-archived item created a duplicate Arkiv entity instead of
  updating the existing one.
  - **Phase 1**: `BatchAccumulator.add()` now always sets `_ready`. The
    previous implementation only signaled when the buffer reached
    `batch_size`, so a `flush()` waiting on an empty buffer for a single
    incoming `add()` would time out instead of returning the item. Worst-
    case singleton latency was ~2× `flush_timeout`.
  - **Phase 2**: `haven upload file` no longer routes through the batched
    pipeline. The CLI is one-shot by construction, so a 1-item batch
    served no amortization purpose and additionally lost
    `find_existing_entity()` dedup. The default `SyncStep` (which calls
    `sync_context()`) handles N=1 correctly.
  - **Phase 3**: `ArkivSyncClient.batch_sync_contexts()` now short-
    circuits to `sync_context()` when called with exactly one context,
    restoring dedup parity for daemon flush-timeout boundaries and
    shutdown drains.

### Changed
- **`batch_sync_flush_timeout` default raised from 30 s → 18000 s (30 min)**
  (`BATCH_SYNC_REMEDIATION_PLAN.md` Phase 4b). Typical Haven ingest cadence
  is 1–6 minutes per item (yt-dlp + VLM + encryption + Filecoin upload),
  so the old 30 s default forced the accumulator to time out into
  singletons every cycle and the size-based trigger never fired. New
  default lets a steady-ingest daemon actually amortize entity creation
  over real batches; the timeout becomes a latency cap rather than the
  primary flush mechanism. **If you depend on near-realtime Arkiv
  visibility, set `HAVEN_BATCH_SYNC_FLUSH_TIMEOUT=30` (or any other
  value) explicitly.** See `docs/BATCH_SYNC_TUNING.md` for per-workload
  presets.
- **`batch_sync_enabled` no longer affects `haven upload file`.** The
  setting now only controls the daemon (`haven daemon`) and scheduled
  jobs (`haven jobs run`). One-shot CLI uploads always sync inline. The
  `haven config init` wizard prompt has been updated to reflect this.

### Added
- **Configurable `flush_timeout` and `max_pending` end-to-end**
  (`BATCH_SYNC_REMEDIATION_PLAN.md` Phase 4). `create_batched_pipeline()`
  now accepts `flush_timeout` and `max_pending` parameters; daemon and
  `haven jobs` call sites now read all three batch-sync knobs from
  `PipelineConfig` and thread them into the accumulator. Previously the
  declared `HAVEN_BATCH_SYNC_FLUSH_TIMEOUT` and
  `HAVEN_BATCH_SYNC_MAX_PENDING` env vars were silently ignored.
- **`haven config init` now prompts for `batch_sync_max_pending`** and
  links to `docs/BATCH_SYNC_TUNING.md` from the batch-sync section.
- **Tier 1 pre-upload deduplication.** `IngestStep` now hashes
  the source file with SHA-256 and looks the result up in the local
  catalog before any expensive downstream work (encrypt, VLM analyze,
  Filecoin upload, Arkiv sync). On a hit, every downstream step
  short-circuits via context skip flags, avoiding 2–15 minutes of
  redundant Filecoin upload (and possibly hours of VLM analysis) per
  re-archive on the slow-hardware target.
  - New indexed `videos.original_hash` column (SHA-256 hex).
  - In-place `ALTER TABLE` shim in `database/connection.py:create_tables()`
    so existing databases pick up the new column on next startup.
  - New `VideoRepository.get_by_original_hash()` lookup.
  - New `--no-dedup` / `--force` flag on `haven upload` to bypass
    dedup intentionally.
  - See `docs/BATCH_SYNC_TIER1_PREUPLOAD_DEDUP.md` for the design.

## [0.1.0] - 2026-02-08

### Features

#### TUI (Terminal User Interface)
- Real-time pipeline visualization with live updates
- Support for YouTube and BitTorrent downloads
- Stage-specific progress indicators (download, analyze, encrypt, upload, sync)
- ASCII speed graphs showing download/upload speeds over time
- Video detail view with full pipeline history
- Filter and search capabilities for videos
- Batch operations (retry, remove, export)
- Pipeline analytics dashboard with statistics
- Event log viewer for monitoring system events
- Vim-style keyboard navigation (j/k, g/G, / for search, etc.)

#### Core Functionality
- Event-driven real-time updates using SSE
- Unified download progress interface
- Repository pattern for data access
- Table-based database design
- Thread-safe state management
- Metrics collection and aggregation

### Technical

#### Architecture
- Modular plugin system for archivers
- Configuration system with TOML support
- Repository pattern for database access
- Event-driven architecture for real-time updates
- Refresh strategy with configurable intervals

#### Database
- SQLAlchemy ORM for database operations
- Repository pattern implementation
- Support for SQLite and PostgreSQL

#### Testing
- Comprehensive test suite with pytest
- Unit tests for all core components
- Integration tests for TUI components
- E2E tests for critical user flows

### Dependencies
- Python 3.11+
- Textual >= 0.40.0 (TUI framework)
- Plotille >= 4.0.0 (ASCII graphs)
- Typer >= 0.21.0 (CLI framework)
- Rich >= 14.0.0 (terminal formatting)
- SQLAlchemy >= 2.0.0 (ORM)

---

## Release Notes

### Installation

```bash
pip install haven-cli
```

To use the TUI:
```bash
pip install "haven-cli[tui]"
haven-tui
```

### Documentation

- [User Guide](docs/user-guide.md)
- [TUI User Guide](docs/tui-user-guide.md)
- [Configuration](docs/configuration.md)
- [Keyboard Shortcuts](docs/keyboard-shortcuts.md)
