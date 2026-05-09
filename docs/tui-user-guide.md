# Haven TUI User Guide

Complete guide to using the Haven Terminal User Interface (TUI) for monitoring and managing video pipeline processing.

## Table of Contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [Main Pipeline View](#main-pipeline-view)
5. [Video Detail View](#video-detail-view)
6. [Speed Graphs](#speed-graphs)
7. [Filters and Search](#filters-and-search)
8. [Sorting Options](#sorting-options)
9. [Batch Operations](#batch-operations)
10. [Analytics Dashboard](#analytics-dashboard)
11. [Event Log View](#event-log-view)
12. [Configuration](#configuration)
13. [Keyboard Shortcuts](#keyboard-shortcuts)
14. [Troubleshooting](#troubleshooting)

---

## Introduction

Haven TUI provides a real-time, terminal-based interface for monitoring video archival pipelines. It displays:

- Active video downloads and processing status
- Pipeline stage progress (download → ingest → analysis → encrypt → upload → sync)
- Real-time speed graphs
- Batch operations for managing multiple videos
- Analytics and performance metrics

### Features

- **Real-time Updates**: Auto-refreshing display with configurable refresh rate
- **Pipeline Visualization**: Visual progress bars for each processing stage
- **Speed Monitoring**: ASCII-based speed graphs showing download/upload rates
- **Batch Operations**: Select and operate on multiple videos simultaneously
- **Filtering & Search**: Filter by stage, status, or search by title
- **Sorting**: Sort videos by date, title, progress, speed, size, or stage
- **Analytics Dashboard**: Performance metrics and success rates

---

## Installation

### Prerequisites

- Python 3.11+
- Haven CLI installed (`pip install haven-cli`)
- Terminal with Unicode support (for progress bars and graphs)

### Install Haven TUI

The TUI is included with Haven CLI. No separate installation required.

```bash
pip install haven-cli
```

### From Source

```bash
git clone https://github.com/haven/haven-cli
cd haven-cli
pip install -e ".[tui]"
```

### Dependencies

The TUI requires the following Python packages (automatically installed):

- `textual` - Terminal UI framework
- `plotille` - ASCII plotting (optional, for enhanced graphs)
- `rich` - Rich text and formatting

---

## Quick Start

### Starting the TUI

```bash
# Start the TUI with default configuration
haven-tui

# Or with explicit database path
haven-tui --database ~/.local/share/haven/haven.db

# With custom configuration
haven-tui --config ~/.config/haven/config.toml
```

### First Run

1. **Ensure the Haven daemon is running**:
   ```bash
   haven run status
   # If not running:
   haven run
   ```

2. **Start the TUI**:
   ```bash
   haven-tui
   ```

3. **Navigate the interface**:
   - Use `↑/↓` or `j/k` to move between videos
   - Press `Enter` to view video details
   - Press `q` to quit

### Basic Navigation

| Key | Action |
|-----|--------|
| `↑/↓` or `j/k` | Move up/down in list |
| `Enter` | View video details |
| `b` or `Esc` | Back to previous view |
| `q` | Quit application |
| `?` | Show help |

---

## Main Pipeline View

The main view displays a scrollable list of all videos in the pipeline.

### Screen Layout

```
┌──────────────────────────────────────────────────────────────┐
│ Haven Pipeline - 3 active | 12 completed | 1 failed          │  ← Header
├──────────────────────────────────────────────────────────────┤
│ # │ ✓ │ Title        │ Stage    │ Progress   │ Speed       │  ← Column Headers
├───┼───┼──────────────┼──────────┼────────────┼─────────────┤
│ 1 │   │ Video 1      │ download │ ████░░ 40% │ 2.5 MB/s    │  ← Video rows
│ 2 │ ✓ │ Video 2      │ encrypt  │ ████████ 80% │ 1.2 MB/s  │
│ 3 │   │ Video 3      │ upload   │ ██░░░░ 20% │ 800 KB/s    │
└───┴───┴──────────────┴──────────┴────────────┴─────────────┘
[q] Quit  [r] Refresh  [a] Auto  [d] Details  [g] Graph  [?] Help  ← Footer
```

### Column Descriptions

| Column | Description |
|--------|-------------|
| `#` | Row number |
| `✓` | Selection indicator (batch mode) |
| `Title` | Video title (truncated if too long) |
| `Stage` | Current pipeline stage |
| `Progress` | Visual progress bar with percentage |
| `Speed` | Current processing speed |
| `Plugin` | Source plugin (YouTube, BitTorrent, etc.) |
| `Size` | File size |
| `ETA` | Estimated time to completion |

### Stage Colors

Each pipeline stage has a distinct color:

| Stage | Color | Description |
|-------|-------|-------------|
| `pending` | Gray | Waiting to start |
| `download` | Blue | Downloading from source |
| `ingest` | Yellow | Extracting metadata |
| `analysis` | Yellow | AI analysis (VLM) |
| `encrypt` | Red | Haven-AOL encryption |
| `upload` | Green | Uploading to Filecoin |
| `sync` | Green | Blockchain sync |
| `complete` | Bold Green | All stages finished |
| `failed` | Red | Error occurred |

### Refresh Controls

| Key | Action |
|-----|--------|
| `r` | Manual refresh |
| `a` | Toggle auto-refresh (default: ON) |

---

## Video Detail View

The detail view shows comprehensive information about a single video.

### Accessing Detail View

1. Navigate to a video in the main list
2. Press `Enter` or `d` to open details

### Detail View Layout

```
┌──────────────────────────────────────────────────────────────┐
│ Video: Big Buck Bunny                                        │
├──────────────────────────────────────────────────────────────┤
│ [Video Information]                                          │
│ Title:     Big Buck Bunny                                    │
│ Source:    https://youtube.com/watch?v=...                   │
│ Size:      45.2 MB                                           │
│ Plugin:    YouTube                                           │
│ Status:    active                                            │
├──────────────────────────────────────────────────────────────┤
│ [Pipeline Progress]                                          │
│ ◉ download   ████████████ 100% Done in 2m30s                 │
│ ◉ ingest     ████████████ 100% Done in 15s                   │
│ ◉ analysis   ████████████ 100% Done in 45s                   │
│ ◐ encrypt    ████████░░░░ 80% 1.2 MB/s                       │
│ ○ upload     ░░░░░░░░░░░░ 0% Pending                         │
│ ○ sync       ░░░░░░░░░░░░ 0% Pending                         │
├──────────────────────────────────────────────────────────────┤
│ [Results]                                                    │
│ IPFS CID:    bafybeig... (truncated)                         │
│ Encrypted:   Yes (Haven-AOL)                                 │
│ AI Analysis: Complete                                        │
├──────────────────────────────────────────────────────────────┤
│ [b] Back  [r] Retry  [l] Logs  [g] Graph  [q] Quit            │
└──────────────────────────────────────────────────────────────┘
```

### Detail View Controls

| Key | Action |
|-----|--------|
| `b` or `Esc` | Back to list view |
| `r` | Retry failed stages |
| `l` | View event logs for this video |
| `g` | Toggle speed graph |
| `q` | Quit application |

### Pipeline Stage Symbols

| Symbol | Meaning |
|--------|---------|
| `○` | Pending (not started) |
| `◐` | Active (in progress) |
| `●` | Completed |
| `✗` | Failed |
| `⊘` | Skipped |

---

## Speed Graphs

Real-time speed visualization for monitoring download/upload rates.

### Main View Speed Graph

In the main pipeline view:

1. Press `g` to toggle the speed graph pane
2. Select a video (using `↑/↓`) to display its speed history
3. The graph shows the last 60 seconds of speed data

```
┌─────────────────────────────────┐
│ Speed History - Download        │
│                                 │
│ 2.5 ┤         ╭─╮               │
│ 2.0 ┤    ╭────╯ ╰──╮            │
│ 1.5 ┤────╯         ╰────╮       │
│ 1.0 ┤                   ╰────   │
│ 0.5 ┤                           │
│   0 ┤────────────────────────   │
│     └────────────────────────   │
│       60s ago            now    │
│                                 │
│ Current: 2.1 MB/s  Avg: 1.8 MB/s  Peak: 2.5 MB/s │
└─────────────────────────────────┘
```

### Detail View Speed Graph

In the detail view:

1. Press `g` to toggle the speed graph
2. Shows speed history for the current active stage
3. Supports multi-stage comparison (when available)

### Graph Features

- **Current Speed**: Real-time transfer rate
- **Average Speed**: Mean speed over the displayed period
- **Peak Speed**: Maximum speed achieved
- **Timeline**: Shows last 60 seconds (configurable)

### Supported Stages

Speed graphs are available for:
- `download` - Download from source
- `encrypt` - Encryption processing
- `upload` - Upload to Filecoin

---

## Filters and Search

Filter the video list to focus on specific videos.

### Available Filters

| Filter | Key | Description |
|--------|-----|-------------|
| Show/Hide Completed | `c` | Toggle visibility of completed videos |
| Show/Hide Failed | `f` | Toggle visibility of failed videos |
| Errors Only | `e` | Show only videos with errors |
| Clear All | `x` | Remove all filters |

### Search

Press `/` to activate search mode:

1. Type your search query
2. Press `Enter` to apply
3. The list updates to show only matching videos

Search matches:
- Video titles (case-insensitive)
- Video IDs (exact match for numeric queries)

### Filter by Stage

Press `f` to access filter dialog:

- Filter by pipeline stage (download, encrypt, upload, etc.)
- Filter by status (active, pending, completed, failed)
- Filter by plugin (YouTube, BitTorrent, etc.)

### Active Filter Display

When filters are active, a summary appears showing:
- Number of filtered videos
- Active filter descriptions

Example:
```
Filters: stage=download, hide_completed, search='bunny'
Showing 3 of 16 videos
```

---

## Sorting Options

Change the order of videos in the list.

### Sort Fields

| Key | Field | Description |
|-----|-------|-------------|
| `s` | Cycle | Cycle through sort fields |
| `S` | Order | Toggle ascending/descending |

### Available Sort Fields

1. **Date Added** (default) - When video entered the pipeline
2. **Title** - Alphabetical by title
3. **Progress** - By completion percentage
4. **Speed** - By current transfer speed
5. **Size** - By file size
6. **Stage** - By pipeline stage

### Sort Order

- **Descending** (default) - Newest/highest first
- **Ascending** - Oldest/lowest first

The current sort is displayed in the footer:
```
Sorted by: Date added ↓
```

---

## Batch Operations

Perform actions on multiple videos simultaneously.

### Entering Batch Mode

1. Press `b` to enter batch mode
2. The footer changes to show batch controls
3. A selection column (`✓`) appears in the table

### Selection Controls

| Key | Action |
|-----|--------|
| `Space` | Select/deselect current video |
| `a` or `Ctrl+a` | Select all visible videos |
| `c` | Clear all selections |
| `Esc` or `b` | Exit batch mode |

### Batch Actions

| Key | Action | Description |
|-----|--------|-------------|
| `r` or `Ctrl+r` | Retry | Retry failed videos in selection |
| `x` or `Delete` | Remove | Remove selected from queue |
| `e` | Export | Export selected videos to JSON |

### Batch Mode Footer

```
Batch: 5 selected | [a] All  [c] Clear  [r] Retry  [x] Remove  [e] Export  [Esc] Exit
```

### Example Workflow

1. Press `b` to enter batch mode
2. Navigate with `↑/↓` and press `Space` to select videos
3. Or press `a` to select all visible videos
4. Press `r` to retry failed videos in selection
5. Press `Esc` to exit batch mode

---

## Analytics Dashboard

View pipeline performance metrics and statistics.

### Accessing Analytics

From the main view:
- Press `A` (Shift+a) to open analytics dashboard

### Analytics Display

```
┌──────────────────────────────────────────────────────────────┐
│ Pipeline Analytics Dashboard                                 │
├──────────────────────────────────────────────────────────────┤
│ Total Videos: 156    Completed: 142    Failed: 8    Active: 6│
├──────────────────────────────────────────────────────────────┤
│ Videos Processed (Last 7 Days)                               │
│ Mon ████████ 12    Tue ████████ 15    Wed ████ 8             │
│ Thu ██████████ 20  Fri ██████ 10      Sat ████ 6             │
│ Sun ██████ 9                                                 │
├──────────────────────────────────────────────────────────────┤
│ Average Time Per Stage                                       │
│ Download   ████████████████ 4m 32s                           │
│ Encrypt    ██████████ 3m 15s                                 │
│ Upload     ████████████████████ 5m 45s                       │
│ Analyze    ████ 1m 20s                                       │
│ Sync       ██ 45s                                            │
├──────────────────────────────────────────────────────────────┤
│ Success Rates                                                │
│ Download   ████████████████████ 98%                          │
│ Encrypt    ████████████████ 95%                              │
│ Upload     ██████████████ 92%                                │
│ Analyze    ████████████████████ 99%                          │
│ Sync       ████████████████████████ 100%                     │
├──────────────────────────────────────────────────────────────┤
│ [q] Quit  [r] Refresh  [a] Toggle Auto-refresh  [Esc] Back   │
└──────────────────────────────────────────────────────────────┘
```

### Analytics Controls

| Key | Action |
|-----|--------|
| `r` | Refresh data |
| `a` | Toggle auto-refresh |
| `Esc` or `q` | Back to main view |

### Available Metrics

- **Summary Cards**: Total, completed, failed, active videos
- **Daily Processing**: Videos processed per day (last 7 days)
- **Stage Timing**: Average time spent in each pipeline stage
- **Success Rates**: Percentage of successful completions per stage
- **Plugin Usage**: Distribution of videos by source plugin

---

## Event Log View

View system events and video-specific logs.

### Accessing Event Logs

From main view:
- Press `l` to view logs for selected video
- Or press `L` (Shift+l) to view system-wide event log

### Event Log Display

```
┌──────────────────────────────────────────────────────────────┐
│ Event Log - Video: Big Buck Bunny                            │
├──────────────────────────────────────────────────────────────┤
│ 2026-02-08 14:32:15 [INFO] Download started                  │
│ 2026-02-08 14:34:45 [INFO] Download completed (2m30s)        │
│ 2026-02-08 14:34:46 [INFO] Ingest started                    │
│ 2026-02-08 14:35:01 [INFO] Ingest completed                  │
│ 2026-02-08 14:35:02 [INFO] Analysis started                  │
│ 2026-02-08 14:35:47 [INFO] Analysis completed                │
│ 2026-02-08 14:35:48 [INFO] Encryption started                │
│ 2026-02-08 14:36:15 [ERROR] Encryption failed: timeout       │
│ 2026-02-08 14:36:16 [INFO] Retry scheduled                   │
└──────────────────────────────────────────────────────────────┘
```

### Log Controls

| Key | Action |
|-----|--------|
| `↑/↓` or `j/k` | Scroll through logs |
| `r` | Refresh logs |
| `Esc` or `b` | Back to previous view |

---

## Configuration

Configure TUI behavior and display options.

### Configuration File

TUI settings are stored in `~/.config/haven/config.toml`:

```toml
[tui]
# Display settings
refresh_rate = 5.0              # Seconds between auto-refreshes
show_speed_graphs = true        # Show speed graph by default
graph_history_seconds = 60      # Speed graph time window

# Filter defaults
show_completed = true           # Show completed videos by default
show_failed = true              # Show failed videos by default

# Layout
compact_mode = false            # Use compact display
show_zebra_stripes = true       # Alternating row colors
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HAVEN_TUI_REFRESH_RATE` | Refresh interval in seconds | 5.0 |
| `HAVEN_TUI_SHOW_GRAPHS` | Enable speed graphs | true |
| `HAVEN_TUI_GRAPH_HISTORY` | Graph history window (seconds) | 60 |

### Command-Line Options

```bash
haven-tui [OPTIONS]

Options:
  --database PATH     Path to haven database file
  --config PATH       Path to configuration file
  --refresh RATE      Refresh rate in seconds
  --no-graphs         Disable speed graphs
  --compact           Use compact display mode
  --help              Show help message
```

---

## Keyboard Shortcuts

### Global Shortcuts

| Key | Action |
|-----|--------|
| `q` | Quit application |
| `?` | Show help |

### Navigation

| Key | Action |
|-----|--------|
| `↑/↓` | Move up/down in list |
| `j/k` | Move up/down (vim-style) |
| `Home` | Go to first item |
| `End` | Go to last item |
| `Page Up` | Scroll up one page |
| `Page Down` | Scroll down one page |
| `Enter` | Select / View details |
| `b` or `Esc` | Back / Cancel |

### Display Controls

| Key | Action |
|-----|--------|
| `r` | Refresh data |
| `a` | Toggle auto-refresh |
| `g` | Toggle speed graph pane |
| `A` | Open analytics dashboard |
| `l` | View logs for selected video |
| `L` | View system event log |

### Filters & Search

| Key | Action |
|-----|--------|
| `f` | Open filter dialog |
| `/` | Search videos |
| `c` | Toggle completed filter |
| `e` | Toggle errors-only filter |
| `x` | Clear all filters |

### Sorting

| Key | Action |
|-----|--------|
| `s` | Cycle sort field |
| `S` | Toggle sort order (asc/desc) |

### Batch Operations

| Key | Action |
|-----|--------|
| `b` | Toggle batch mode |
| `Space` | Select/deselect video |
| `a` / `Ctrl+a` | Select all visible |
| `c` | Clear selection |
| `r` / `Ctrl+r` | Retry selected |
| `x` / `Delete` | Remove selected |
| `e` | Export selected to JSON |
| `Esc` | Exit batch mode |

---

## Troubleshooting

### Common Issues

#### TUI Won't Start

**Problem**: `haven-tui` command not found or fails to start.

**Solutions**:
1. Verify Haven CLI is installed: `pip show haven-cli`
2. Check Python version: `python --version` (needs 3.11+)
3. Try running with Python: `python -m haven_tui`

#### No Videos Displayed

**Problem**: TUI starts but shows no videos.

**Solutions**:
1. Verify the database path is correct
2. Check if Haven daemon is running: `haven run status`
3. Try showing completed videos: Press `c`
4. Clear filters: Press `x`

#### Display Issues

**Problem**: Garbled text or misaligned columns.

**Solutions**:
1. Ensure terminal supports Unicode
2. Try compact mode: `haven-tui --compact`
3. Increase terminal width (minimum 100 characters recommended)
4. Check terminal font supports box-drawing characters

#### Slow Performance

**Problem**: TUI is sluggish or unresponsive.

**Solutions**:
1. Reduce refresh rate: `--refresh 10`
2. Disable speed graphs: `--no-graphs`
3. Close other terminal applications
4. Check system resources: `top` or `htop`

#### Speed Graph Not Showing

**Problem**: Speed graph pane is empty.

**Solutions**:
1. Ensure `plotille` is installed: `pip install plotille`
2. Select a video that is actively downloading/uploading
3. Wait a few seconds for data to accumulate
4. Check if speed history is being recorded in database

### Debug Mode

Run TUI with debug logging:

```bash
HAVEN_LOG_LEVEL=DEBUG haven-tui
```

### Getting Help

1. In-app help: Press `?` in any view
2. CLI help: `haven-tui --help`
3. Log files: `~/.local/share/haven/daemon.log`

---

## Tips and Best Practices

### Performance Tips

- **Use filters** to reduce the number of displayed videos
- **Disable auto-refresh** when viewing details (`a`)
- **Close speed graph** when not needed (`g`)
- **Use compact mode** on smaller terminals

### Workflow Tips

- **Monitor downloads**: Filter by `download` stage
- **Review failures**: Use `e` key to show errors only
- **Batch retry**: Select multiple failed videos and retry
- **Track progress**: Use analytics dashboard for trends

### Keyboard Efficiency

- Learn vim-style navigation (`j/k`)
- Use `c` to quickly toggle completed visibility
- Batch mode (`b`) for managing multiple videos
- `Esc` always takes you back

---

## See Also

- [CLI Reference](cli-reference.md) - Command-line documentation
- [Configuration](configuration.md) - Configuration options
- [Troubleshooting](troubleshooting.md) - General Haven troubleshooting
- [API Reference](api.md) - Python API documentation
