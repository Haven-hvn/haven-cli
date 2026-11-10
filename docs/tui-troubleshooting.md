# Haven TUI Troubleshooting Guide

Common issues and solutions specific to the Haven Terminal User Interface.

## Table of Contents

1. [Installation Issues](#installation-issues)
2. [Display Issues](#display-issues)
3. [Performance Problems](#performance-problems)
4. [Database Connection](#database-connection)
5. [Feature-Specific Issues](#feature-specific-issues)
6. [Debug Mode](#debug-mode)
7. [FAQ](#faq)

---

## Installation Issues

### TUI Won't Start

**Problem**: Running `haven-tui` produces an error or returns to shell immediately.

**Symptoms**:
```
$ haven-tui
Command not found: haven-tui
```
or
```
$ haven-tui
Error: No module named 'textual'
```

**Solutions**:

1. **Verify Haven CLI installation**:
   ```bash
   pip show haven-cli
   # Should show version and installation path
   ```

2. **Check Python version** (requires 3.12+):
   ```bash
   python --version
   # Python 3.12.x or higher required
   ```

3. **Install TUI dependencies**:
   ```bash
   pip install "haven-cli[tui]"
   # Or manually:
   pip install textual plotille rich
   ```

4. **Try module execution**:
   ```bash
   python -m haven_tui
   ```

5. **Check for conflicting packages**:
   ```bash
   pip list | grep -i textual
   # Ensure only one version installed
   ```

### Import Errors

**Problem**: ImportError when starting TUI.

**Symptoms**:
```
ImportError: cannot import name 'VideoListScreen' from 'haven_tui'
```

**Solutions**:

1. **Reinstall haven-cli**:
   ```bash
   pip uninstall haven-cli
   pip install --no-cache-dir haven-cli
   ```

2. **Check for stale .pyc files**:
   ```bash
   find . -name "*.pyc" -delete
   find . -name "__pycache__" -type d -exec rm -rf {} +
   ```

3. **Verify installation from source**:
   ```bash
   pip install -e ".[dev]"
   ```

---

## Display Issues

### Garbled Characters

**Problem**: Progress bars, borders, or special characters display incorrectly.

**Symptoms**:
- Progress bars show as "????" or boxes
- Borders are misaligned
- "No speed data available" box is broken

**Solutions**:

1. **Check terminal Unicode support**:
   ```bash
   echo -e "\u2588\u2591"  # Should show filled and empty blocks
   ```

2. **Set UTF-8 locale**:
   ```bash
   export LANG=en_US.UTF-8
   export LC_ALL=en_US.UTF-8
   ```

3. **Use compatible terminal**:
   - Recommended: iTerm2 (macOS), Windows Terminal (Windows), gnome-terminal/konsole (Linux)
   - Avoid: Default Windows CMD, old xterm

4. **Check font support**:
   - Use a font with Unicode block character support
   - Recommended: JetBrains Mono, Fira Code, Cascadia Code

5. **Enable compact mode** (reduces special characters):
   ```bash
   haven-tui --compact
   ```

### Misaligned Columns

**Problem**: Table columns don't line up properly.

**Symptoms**:
- Columns overlap
- Progress bars extend past boundaries
- Text is cut off unexpectedly

**Solutions**:

1. **Increase terminal width**:
   ```bash
   # Minimum recommended: 100 columns
   stty cols 120
   ```

2. **Check terminal size detection**:
   ```bash
   stty size  # Shows rows and columns
   ```

3. **Use compact mode**:
   ```bash
   haven-tui --compact
   ```

4. **Disable certain columns** (via config):
   ```toml
   [tui.display]
   show_size_column = false
   show_eta_column = false
   ```

### Color Issues

**Problem**: Colors not displaying correctly or causing readability issues.

**Solutions**:

1. **Check terminal color support**:
   ```bash
   echo $TERM
   # Should show xterm-256color or similar
   ```

2. **Force color mode**:
   ```bash
   TERM=xterm-256color haven-tui
   ```

3. **Disable colors** (accessibility):
   ```bash
   haven-tui --no-color
   ```

4. **High contrast mode**:
   ```bash
   haven-tui --high-contrast
   ```

### Screen Flickering

**Problem**: Display flickers or redraws excessively.

**Solutions**:

1. **Reduce refresh rate**:
   ```bash
   haven-tui --refresh 10
   ```

2. **Disable auto-refresh** (press `a` in TUI)

3. **Check terminal emulator**:
   - Some terminals have poor double-buffering
   - Try a different terminal (iTerm2, Alacritty, Windows Terminal)

4. **Reduce animations**:
   ```toml
   [tui.display]
   animations_enabled = false
   ```

---

## Performance Problems

### High CPU Usage

**Problem**: TUI consumes excessive CPU.

**Symptoms**:
- Laptop fans spin up
- Battery drains quickly
- `top` shows high Python CPU usage

**Solutions**:

1. **Increase refresh interval**:
   ```bash
   # Default is 5 seconds
   haven-tui --refresh 10  # 10 seconds
   # or
   haven-tui --refresh 30  # 30 seconds
   ```

2. **Disable speed graphs**:
   ```bash
   haven-tui --no-graphs
   ```

3. **Disable auto-refresh** (press `a` in TUI, then refresh manually with `r`)

4. **Reduce max displayed videos**:
   ```toml
   [tui.performance]
   max_displayed_videos = 100
   ```

5. **Profile to find bottleneck**:
   ```bash
   python -m cProfile -o profile.stats -m haven_tui
   ```

### Slow Response to Input

**Problem**: Keystrokes have delayed effect.

**Solutions**:

1. **Check database connection**:
   - Slow DB queries can block UI
   - See [Database Connection](#database-connection)

2. **Reduce number of videos**:
   - Use filters to show fewer videos
   - Hide completed: press `c`

3. **Close speed graph**:
   - Press `g` to hide graph pane

4. **Check system resources**:
   ```bash
   free -h  # Memory
   df -h    # Disk space
   ```

### Memory Usage

**Problem**: TUI uses too much memory.

**Solutions**:

1. **Limit video cache size**:
   ```toml
   [tui.performance]
   max_cached_videos = 500
   ```

2. **Reduce speed history**:
   ```toml
   [tui]
   graph_history_seconds = 30  # Down from 60
   ```

3. **Disable analytics preloading**:
   ```toml
   [tui.performance]
   preload_analytics = false
   ```

4. **Restart TUI periodically** (if running for days)

---

## Database Connection

### Cannot Connect to Database

**Problem**: TUI can't connect to Haven database.

**Symptoms**:
```
Error: Database file not found: ~/.local/share/haven/haven.db
```
or
```
Error: unable to open database file
```

**Solutions**:

1. **Verify database file exists**:
   ```bash
   ls -la ~/.local/share/haven/haven.db
   ```

2. **Check database path**:
   ```bash
   haven config show  # Check data_dir setting
   ```

3. **Specify correct database path**:
   ```bash
   haven-tui --database /path/to/haven.db
   ```

4. **Check file permissions**:
   ```bash
   ls -la ~/.local/share/haven/
   # Should be readable by current user
   chmod 644 ~/.local/share/haven/haven.db
   ```

5. **Verify Haven has been initialized**:
   ```bash
   haven config init
   ```

### Database Locked

**Problem**: "database is locked" error.

**Symptoms**:
```
sqlite3.OperationalError: database is locked
```

**Solutions**:

1. **Check for multiple Haven processes**:
   ```bash
   ps aux | grep haven
   # If multiple found, stop extras
   pkill -f "haven run"
   ```

2. **Check for zombie processes**:
   ```bash
   lsof ~/.local/share/haven/haven.db
   # Kill any processes holding the lock
   ```

3. **Wait and retry**:
   - Database may be temporarily locked during writes
   - TUI will retry automatically

4. **Use WAL mode** (if not already enabled):
   ```bash
   sqlite3 ~/.local/share/haven/haven.db "PRAGMA journal_mode=WAL;"
   ```

### No Videos Displayed

**Problem**: TUI starts but shows empty list.

**Solutions**:

1. **Check if videos exist in database**:
   ```bash
   sqlite3 ~/.local/share/haven/haven.db "SELECT COUNT(*) FROM videos;"
   ```

2. **Show all videos (including completed)**:
   - Press `c` in TUI to toggle completed filter

3. **Clear all filters**:
   - Press `x` in TUI

4. **Check filter state**:
   - Press `f` to see active filters

5. **Verify Haven daemon is running**:
   ```bash
   haven run status
   ```

---

## Feature-Specific Issues

### Speed Graph Not Working

**Problem**: Speed graph shows "No speed data available".

**Solutions**:

1. **Install plotille** (optional but recommended):
   ```bash
   pip install plotille
   ```

2. **Select an active video**:
   - Speed graph only shows data for selected video
   - Navigate to a video with active download/upload

3. **Wait for data accumulation**:
   - Speed data is recorded every few seconds
   - New videos may take 10-30 seconds to show data

4. **Check speed_history table**:
   ```bash
   sqlite3 ~/.local/share/haven/haven.db \
     "SELECT COUNT(*) FROM speed_history;"
   ```

5. **Verify stage is tracked**:
   - Only download, encrypt, and upload stages have speed tracking
   - Analysis and sync don't show speed graphs

### Batch Operations Not Working

**Problem**: Can't select multiple videos or batch actions fail.

**Solutions**:

1. **Enter batch mode first**:
   - Press `b` to enter batch mode
   - Selection column (✓) should appear

2. **Check selection**:
   - Use `Space` to toggle selection
   - Footer should show "Batch: N selected"

3. **Verify permissions**:
   - Some batch operations require write access to database
   - Ensure database is not read-only

4. **Check pipeline interface**:
   ```bash
   # Verify haven daemon is running
   haven run status
   ```

### Filters Not Applying

**Problem**: Filters don't seem to affect the displayed list.

**Solutions**:

1. **Wait for refresh**:
   - Filters apply on next data refresh
   - Press `r` to refresh immediately

2. **Check filter combination**:
   - Multiple filters use AND logic
   - Very restrictive combinations may show no results

3. **Clear and reapply**:
   - Press `x` to clear all filters
   - Reapply filters one at a time

4. **Verify controller is connected**:
   - Check log for "Filter system not available" message
   - May indicate initialization problem

### Sort Not Working

**Problem**: Sorting doesn't change video order.

**Solutions**:

1. **Check current sort**:
   - Footer shows current sort field
   - Press `s` multiple times to cycle fields

2. **Verify sort order**:
   - Press `S` to toggle ascending/descending
   - Some sorts may look similar in one direction

3. **Combined with filters**:
   - Sort applies after filters
   - Filtered list may be small

4. **Restart TUI**:
   - Sort state may be corrupted
   - Restart to reset

### Analytics Dashboard Empty

**Problem**: Analytics shows no data or "No data available".

**Solutions**:

1. **Need processing history**:
   - Analytics requires completed videos
   - Process some videos first

2. **Check date range**:
   - Default shows last 7 days
   - If no videos processed recently, charts are empty

3. **Verify analytics repository**:
   ```bash
   # Check if pipeline_snapshots has data
   sqlite3 ~/.local/share/haven/haven.db \
     "SELECT COUNT(*) FROM pipeline_snapshots;"
   ```

4. **Refresh analytics**:
   - Press `r` in analytics view to refresh

---

## Debug Mode

### Enabling Debug Logging

Run TUI with debug output:

```bash
# Method 1: Environment variable
HAVEN_LOG_LEVEL=DEBUG haven-tui

# Method 2: Log to file
haven-tui --log-file tui.log --debug

# Method 3: Use textual dev mode
textual console --port 8080
# In another terminal:
textual run --dev -c haven-tui
```

### Debug Key Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+Shift+D` | Toggle debug overlay |
| `F12` | Show DOM inspector (dev mode) |
| `Ctrl+Shift+R` | Force full refresh |

### Common Debug Output

**Database query timing**:
```
DEBUG:haven_tui.data.repositories:Query get_all took 45ms
```

**State manager updates**:
```
DEBUG:haven_tui.core.state_manager:Refreshing 156 videos
DEBUG:haven_tui.core.state_manager:State changed, notifying 3 listeners
```

**Event consumption**:
```
DEBUG:haven_tui.data.event_consumer:Checking for changes
DEBUG:haven_tui.data.event_consumer:No changes detected
```

### Getting System Information

For bug reports, gather:

```bash
# Haven version
haven --version

# Python version
python --version

# TUI dependencies
pip show textual plotille rich

# Terminal info
echo $TERM
echo $LANG

# System info
uname -a

# Database size
ls -lh ~/.local/share/haven/haven.db
```

---

## FAQ

### General Questions

**Q: Can I run TUI on a different machine than the Haven daemon?**

A: Yes, copy the database file or use a network-accessible SQLite database. Note that TUI requires direct database access, not just the daemon API.

**Q: Does TUI work over SSH?**

A: Yes, provided the terminal supports Unicode and the SSH client forwards the terminal capabilities correctly. Use `ssh -t` to force TTY allocation.

**Q: Can I use TUI with tmux/screen?**

A: Yes. TUI works well with terminal multiplexers. You may need to set `TERM=screen-256color` or `TERM=tmux-256color`.

### Display Questions

**Q: Why do I see boxes instead of progress bars?**

A: Your terminal font doesn't support Unicode block characters (U+2588, U+2591). Change to a font like JetBrains Mono, Fira Code, or Cascadia Code.

**Q: Can I use TUI with a white/light background?**

A: Yes, but you may want to enable high contrast mode: `haven-tui --high-contrast`. The default color scheme is optimized for dark backgrounds.

**Q: Why is the display cut off at the bottom?**

A: Your terminal window is too small. Resize to at least 80x24, preferably 100x30 or larger.

### Performance Questions

**Q: Why is TUI using so much CPU?**

A: The default 5-second refresh rate can be CPU-intensive with many videos. Increase the refresh rate with `--refresh 30` or disable auto-refresh with `a` key.

**Q: Can I run TUI for days at a time?**

A: Yes, but memory usage may grow slowly due to Python's memory management. Restart periodically if running for extended periods.

**Q: Why does the TUI freeze occasionally?**

A: Brief freezes during database writes are normal. If freezing persists, check for database locks or slow queries.

### Feature Questions

**Q: Can I customize the columns shown?**

A: Partially. Column visibility can be configured in `config.toml`. Full column customization requires code changes.

**Q: Can I export the video list?**

A: Yes, use batch mode (`b`), select all (`a`), then export (`e`) to save as JSON.

**Q: Can I view TUI on mobile (iOS/Android)?**

A: Yes, using a terminal app like Termius or Blink Shell. Ensure the app supports Unicode and 256 colors.

**Q: Can I have multiple TUI instances open?**

A: Yes, but they will compete for database access. SQLite handles this, but performance may degrade.

### Troubleshooting Questions

**Q: Where are TUI log files?**

A: By default, TUI logs to the same location as Haven CLI: `~/.local/share/haven/daemon.log`. Specify a custom log file with `--log-file`.

**Q: How do I reset TUI settings?**

A: Delete or edit the TUI section in `~/.config/haven/config.toml`:
```bash
# Remove just TUI config
sed -i '/^\[tui\]/,/^\[/{/^\[/!d}' ~/.config/haven/config.toml
```

**Q: TUI crashes on startup, what should I do?**

A: Try these steps:
1. Run with `--no-graphs` flag
2. Run with `--compact` flag
3. Specify database explicitly: `--database /path/to/haven.db`
4. Check Python version (must be 3.12+)
5. Reinstall: `pip install --force-reinstall "haven-cli[tui]"`

---

## Getting Help

If the above solutions don't resolve your issue:

1. **Check logs**:
   ```bash
   tail -f ~/.local/share/haven/daemon.log
   ```

2. **Run with debug mode** and capture output

3. **Gather system information** (see Debug Mode section)

4. **Create an issue** with:
   - Haven version (`haven --version`)
   - Python version
   - Operating system
   - Terminal emulator
   - Steps to reproduce
   - Debug log output

5. **Community support**:
   - GitHub Discussions
   - Discord/Slack community channels
