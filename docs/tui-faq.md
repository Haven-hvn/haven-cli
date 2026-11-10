# Haven TUI Frequently Asked Questions (FAQ)

Common questions about the Haven Terminal User Interface.

## General Questions

### What is Haven TUI?

Haven TUI is a Terminal User Interface for monitoring and managing the Haven video archival pipeline. It provides real-time visualization of video processing stages, download progress, speed graphs, and batch operations - all within your terminal.

### Do I need to use the TUI?

No. The TUI is optional. Haven CLI provides full functionality through command-line commands. The TUI is for users who prefer a visual interface and real-time monitoring.

### Is TUI available on all platforms?

Yes. Haven TUI works on:
- Linux
- macOS
- Windows (with Windows Terminal or WSL)
- Any platform that supports Python 3.12+ and a compatible terminal

### What's the difference between TUI and CLI?

| Feature | CLI | TUI |
|---------|-----|-----|
| Interface | Command-based | Interactive visual |
| Real-time monitoring | No | Yes |
| Speed graphs | No | Yes |
| Batch operations | Commands only | Visual selection |
| Best for | Scripts, automation | Interactive monitoring |

---

## Installation & Setup

### How do I install the TUI?

The TUI is included with Haven CLI:

```bash
pip install "haven-cli[tui]"
```

Or if you already have Haven CLI:

```bash
pip install textual plotille
```

### What are the system requirements?

- Python 3.12 or higher
- Terminal with Unicode support (for progress bars)
- Minimum 80x24 terminal size (100x30 recommended)
- Haven CLI installed and configured

### What terminal should I use?

**Recommended:**
- macOS: iTerm2, Terminal.app
- Windows: Windows Terminal, WSL
- Linux: GNOME Terminal, Konsole, Alacritty, kitty

**Not Recommended:**
- Windows CMD (limited Unicode support)
- Old xterm versions

### How do I start the TUI?

```bash
# Basic usage
haven-tui

# With specific database
haven-tui --database ~/.local/share/haven/haven.db

# With custom refresh rate
haven-tui --refresh 10
```

---

## Basic Usage

### How do I navigate the TUI?

Use keyboard shortcuts:
- `↑/↓` or `j/k` - Move up/down
- `Enter` - View details
- `b` or `Esc` - Go back
- `q` - Quit
- `?` - Show help

### Why don't I see any videos?

Possible reasons:
1. Haven daemon isn't running: `haven run status`
2. Videos are filtered out - press `x` to clear filters
3. Completed videos are hidden - press `c` to show them
4. Database path is wrong - use `--database` flag

### How do I refresh the display?

- Press `r` for manual refresh
- Press `a` to toggle auto-refresh (default: ON)

### Can I use mouse?

Limited mouse support:
- Click to select rows
- Scroll wheel works
- Most actions are keyboard-based for efficiency

---

## Filters & Search

### How do I search for a video?

1. Press `/`
2. Type your search term
3. Press `Enter`

Search matches video titles and IDs.

### How do I filter by stage?

Press `f` to open the filter dialog, then:
- Select stage: download, encrypt, upload, etc.
- Press `Enter` to apply

Or use quick filters:
- `c` - Toggle completed videos
- `e` - Show only errors
- `x` - Clear all filters

### Why are some videos hidden?

Default filters hide completed videos. Press `c` to show them, or `x` to clear all filters.

### Can I save my filter settings?

Yes, edit `~/.config/haven/config.toml`:

```toml
[tui.filters]
show_completed = true
show_failed = true
```

---

## Sorting

### How do I change the sort order?

- Press `s` to cycle through sort fields (date, title, progress, speed, size, stage)
- Press `S` (Shift+s) to toggle ascending/descending

### What fields can I sort by?

1. Date Added (default)
2. Title (alphabetical)
3. Progress (completion %)
4. Speed (transfer rate)
5. Size (file size)
6. Stage (pipeline stage)

### Why is the sort not working?

- Check the footer for current sort indicator
- Sort applies after filters - you may have few results
- Press `S` to reverse the order

---

## Speed Graphs

### How do I view speed graphs?

- Press `g` in the main view to toggle the speed graph pane
- Select a video to see its speed history
- In detail view, press `g` to show/hide graph

### Why is my speed graph empty?

Possible reasons:
1. No video selected - use `↑/↓` to select one
2. Video isn't in a tracked stage (only download/encrypt/upload show speed)
3. Not enough data yet - wait 10-30 seconds
4. `plotille` not installed: `pip install plotille`

### What does the speed graph show?

- Current speed (real-time)
- Average speed over the displayed period
- Peak speed achieved
- Last 60 seconds of history (configurable)

### Can I see speed for all stages?

The main graph shows the current active stage. In detail view, you can see all tracked stages.

---

## Batch Operations

### How do I select multiple videos?

1. Press `b` to enter batch mode
2. Use `↑/↓` and `Space` to select/deselect videos
3. Or press `a` to select all visible
4. Press `Esc` to exit batch mode

### What can I do with batch selection?

- `r` - Retry failed videos
- `x` - Remove from queue
- `e` - Export to JSON

### How do I select all failed videos?

1. Press `e` to show only errors
2. Press `b` for batch mode
3. Press `a` to select all
4. Press `r` to retry

### Can I undo batch operations?

No, there's no undo. Be careful with:
- Remove (`x`) - permanently removes from queue
- Retry (`r`) - restarts processing

---

## Analytics Dashboard

### How do I view analytics?

Press `A` (Shift+a) in the main view to open the analytics dashboard.

### What metrics are available?

- Total/completed/failed/active video counts
- Daily processing volume (last 7 days)
- Average time per pipeline stage
- Success rates by stage
- Plugin usage distribution

### Why are my analytics empty?

- No processing history yet
- Data only includes completed videos
- Try changing date range (press `1`, `2`, or `3`)

### How often do analytics update?

- Auto-refresh every 30 seconds (configurable)
- Press `r` for manual refresh
- Press `a` to toggle auto-refresh

---

## Configuration

### Where are TUI settings stored?

`~/.config/haven/config.toml` (along with other Haven settings)

### How do I change the refresh rate?

**Temporary:** Press `+` or `-` in TUI

**Permanent:** Edit config:
```toml
[tui]
refresh_rate = 10.0  # seconds
```

### Can I hide certain columns?

Yes, in config:
```toml
[tui.display]
show_size_column = false
show_eta_column = false
```

### How do I make TUI start with my preferred filters?

```toml
[tui.filters]
show_completed = true   # Show completed by default
show_failed = true      # Show failed by default
plugin_filter = "youtube"  # Filter to specific plugin
```

---

## Troubleshooting

### TUI crashes on startup

Try:
1. `haven-tui --no-graphs` - Disable graphs
2. `haven-tui --compact` - Use compact mode
3. `haven-tui --database /path/to/db` - Specify database
4. Check Python version: `python --version` (need 3.12+)

### Garbled characters / display issues

1. Check terminal supports Unicode: `echo -e "\u2588\u2591"`
2. Set UTF-8: `export LANG=en_US.UTF-8`
3. Try different terminal
4. Use compact mode: `haven-tui --compact`

### High CPU usage

1. Increase refresh rate: `haven-tui --refresh 30`
2. Disable auto-refresh: Press `a`
3. Close speed graph: Press `g`
4. Use filters to show fewer videos

### Database errors

1. Check daemon is running: `haven run status`
2. Verify database path: `haven config path`
3. Check permissions: `ls -la ~/.local/share/haven/`
4. Check for locks: `lsof ~/.local/share/haven/haven.db`

---

## Advanced Usage

### Can I run TUI on a remote server?

Yes, over SSH. Ensure:
- Terminal supports Unicode
- Forward terminal capabilities: `ssh -t user@host "haven-tui"`
- Or use terminal multiplexer (tmux/screen)

### Can I use TUI with tmux?

Yes:
```bash
tmux new-session -d -s haven-tui 'haven-tui'
tmux attach -t haven-tui
```

Set `TERM=screen-256color` or `TERM=tmux-256color` for best results.

### How do I integrate TUI with my workflow?

**With systemd:**
Create a user service that runs TUI when you log in.

**With aliases:**
```bash
alias ht='haven-tui --refresh 10'
alias htc='haven-tui --compact'
```

**With tmux:**
```bash
# Auto-start TUI in a tmux window
if [ -z "$TMUX" ]; then
    tmux new-session -d -s haven 'haven-tui'
fi
```

### Can I customize colors?

Yes, by editing CSS in the source code or using environment variables:

```bash
# Override specific colors
HAVEN_TUI_COLOR_PRIMARY="blue"
HAVEN_TUI_COLOR_SUCCESS="green"
```

---

## Performance

### Is TUI resource-intensive?

By default, no. It uses:
- ~50-100MB RAM
- Minimal CPU when idle
- More resources with many videos and high refresh rate

### How many videos can TUI handle?

Tested with:
- 1,000 videos: Excellent performance
- 10,000 videos: Good with filters
- 100,000+ videos: Use filters and reduce refresh rate

### How can I improve performance?

1. **Reduce refresh rate**: `--refresh 30`
2. **Use filters**: Show only relevant videos
3. **Disable graphs**: `--no-graphs`
4. **Compact mode**: `--compact`
5. **Hide completed**: Press `c`

---

## Development

### Can I extend TUI?

Yes. TUI is built on Textual and is extensible:
- Custom screens
- Custom widgets
- Custom key bindings
- Custom data sources

See [TUI Architecture](tui-architecture.md) for details.

### Where can I find the TUI source code?

```
haven_tui/
├── core/         # Business logic
├── data/         # Data access
├── models/       # View models
└── ui/           # UI components and views
```

### How do I debug TUI?

```bash
# Debug mode
HAVEN_LOG_LEVEL=DEBUG haven-tui

# Dev mode with inspector
textual run --dev -c haven-tui

# Log to file
haven-tui --log-file tui.log --debug
```

---

## Comparison

### TUI vs Web UI

| Feature | TUI | Web UI (future) |
|---------|-----|-----------------|
| Runs in | Terminal | Browser |
| Setup | None | Server required |
| Remote access | SSH | HTTP |
| Performance | Fast | Depends on connection |
| Mobile friendly | Limited | Yes |

### TUI vs Other Tools

**vs htop/top:**
- TUI is specialized for Haven pipeline
- Shows video-specific data
- Has batch operations

**vs aria2tui:**
- Similar interface inspiration
- Haven has more pipeline stages
- Integrated with Haven ecosystem

---

## Getting Help

### Where can I get help?

1. **In-app help**: Press `?` in any view
2. **Documentation**: See `docs/tui-*.md` files
3. **CLI help**: `haven-tui --help`
4. **GitHub Issues**: Report bugs and feature requests
5. **Community**: Discord/Slack channels

### How do I report a bug?

Include:
- Haven version: `haven --version`
- Python version: `python --version`
- Operating system
- Terminal emulator
- Steps to reproduce
- Debug logs

### Can I request features?

Yes! Feature requests are welcome:
1. Check existing issues first
2. Describe the use case
3. Explain expected behavior

---

## Tips & Tricks

### Keyboard Shortcuts Cheat Sheet

Print this and keep it handy:

```
Navigation:  ↑/↓ or j/k  |  Enter  |  b/Esc  |  q
Display:     r (refresh) |  a (auto) | g (graph) | ? (help)
Filters:     f (dialog)  |  / (search) | c (completed) | e (errors) | x (clear)
Sorting:     s (field)   |  S (order)
Batch:       b (mode)    |  Space (select) | a (all) | r (retry) | x (remove)
```

### Quick Workflows

**Find and retry failed downloads:**
```
e → b → a → r → Esc
```

**Check a specific video:**
```
/ → type name → Enter → d → review → b
```

**Export completed videos:**
```
c → b → a → e → choose file → Esc
```

### Pro Tips

1. **Learn vim navigation** (`j/k`) - faster than arrow keys
2. **Use filters liberally** - reduces noise
3. **Exit with `q`** - works from any screen
4. **Back with `b`** - like a browser back button
5. **Press `?`** - context-sensitive help anywhere
