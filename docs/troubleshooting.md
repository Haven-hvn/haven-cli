# Troubleshooting Guide

Common issues and solutions for Haven CLI.

## Table of Contents

1. [Installation Issues](#installation-issues)
2. [Configuration Issues](#configuration-issues)
3. [Plugin Issues](#plugin-issues)
4. [Pipeline Issues](#pipeline-issues)
5. [Upload/Download Issues](#uploaddownload-issues)
6. [Daemon Issues](#daemon-issues)
7. [JavaScript Runtime Issues](#javascript-runtime-issues)
8. [Database Issues](#database-issues)
9. [Getting Help](#getting-help)

---

## Installation Issues

### pip install fails

**Problem**: Installation fails with compilation errors or dependency conflicts.

**Solutions**:

1. **Upgrade pip**:
   ```bash
   pip install --upgrade pip
   pip install haven-cli
   ```

2. **Use virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/Mac
   # or
   .venv\Scripts\activate  # Windows
   pip install haven-cli
   ```

3. **Install system dependencies**:
   
   On Ubuntu/Debian:
   ```bash
   sudo apt-get install python3-dev build-essential
   ```
   
   On macOS:
   ```bash
   xcode-select --install
   ```

### FFmpeg not found

**Problem**: `FFmpeg not found in PATH` error.

**Solutions**:

**macOS**:
```bash
brew install ffmpeg
```

**Ubuntu/Debian**:
```bash
sudo apt-get install ffmpeg
```

**Windows**:
1. Download from https://ffmpeg.org/download.html
2. Extract to `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to PATH

**Verify installation**:
```bash
ffmpeg -version
```

### yt-dlp not found

**Problem**: YouTube plugin reports `yt-dlp not found`.

**Solutions**:

```bash
# macOS
brew install yt-dlp

# Ubuntu/Debian
sudo apt-get install yt-dlp

# Or via pip
pip install yt-dlp

# For latest version
pip install -U yt-dlp
```

**Verify installation**:
```bash
yt-dlp --version
```

---

## Configuration Issues

### Configuration file not found

**Problem**: `Configuration file not found` error.

**Solutions**:

1. **Initialize configuration**:
   ```bash
   haven config init
   ```

2. **Check config location**:
   ```bash
   haven config path
   ```

3. **Set config directory via environment variable**:
   ```bash
   export HAVEN_CONFIG_DIR=/path/to/config
   haven config init
   ```

### Invalid configuration format

**Problem**: `Invalid TOML format` error.

**Solutions**:

1. **Validate configuration**:
   ```bash
   haven config validate
   ```

2. **Regenerate configuration**:
   ```bash
   haven config init --force
   ```

3. **Check for syntax errors**:
   ```bash
   # Edit with built-in editor
   haven config edit
   
   # Or use toml validation tool
   pip install toml
   python -c "import toml; toml.load('~/.config/haven/config.toml')"
   ```

### Permission denied on config file

**Problem**: Cannot read/write configuration file.

**Solutions**:

```bash
# Fix permissions
chmod 600 ~/.config/haven/config.toml

# Or recreate with correct permissions
rm ~/.config/haven/config.toml
haven config init
```

---

## Plugin Issues

### Plugin not found

**Problem**: `Plugin not found: YouTubePlugin`

**Solutions**:

1. **List available plugins**:
   ```bash
   haven plugins list --all
   ```

2. **Enable the plugin**:
   ```bash
   haven plugins enable YouTubePlugin
   ```

3. **Check plugin configuration**:
   ```bash
   haven plugins configure YouTubePlugin --show
   ```

4. **Verify plugin directories**:
   ```toml
   [plugins]
   plugin_dirs = ["/path/to/custom/plugins"]
   ```

### Plugin health check fails

**Problem**: `Plugin health check failed`

**Solutions**:

1. **Test the plugin**:
   ```bash
   haven plugins test YouTubePlugin
   ```

2. **Check dependencies**:
   - For YouTube: Verify `yt-dlp` is installed
   - For other plugins: Check their specific requirements

3. **Check configuration**:
   ```bash
   haven plugins info YouTubePlugin
   ```

4. **Reinitialize the plugin**:
   ```bash
   haven plugins disable YouTubePlugin
   haven plugins enable YouTubePlugin
   ```

### YouTube plugin: Age-restricted content

**Problem**: Cannot download age-restricted videos.

**Solutions**:

1. **Export YouTube cookies**:
   - Install "Get cookies.txt LOCALLY" browser extension
   - Sign in to YouTube
   - Export cookies to `~/.config/haven/youtube_cookies.txt`

2. **Configure cookies**:
   ```bash
   haven plugins configure YouTubePlugin --set cookies_file=~/.config/haven/youtube_cookies.txt
   ```

3. **Alternative: Use yt-dlp to export cookies**:
   ```bash
   yt-dlp --cookies-from-browser chrome --cookies ~/.config/haven/youtube_cookies.txt
   ```

### YouTube plugin: Rate limiting

**Problem**: `HTTP Error 429: Too Many Requests`

**Solutions**:

1. **Reduce max_videos setting**:
   ```bash
   haven plugins configure YouTubePlugin --set max_videos=5
   ```

2. **Add delays between requests** (in custom config):
   ```toml
   [plugins.settings.YouTubePlugin]
   max_videos = 5
   sleep_interval = 5  # seconds between requests
   ```

3. **Use authenticated cookies** (see above)

4. **Use a different IP address** (VPN/proxy)

### Plugin discovery returns no sources

**Problem**: `Discovered 0 sources`

**Solutions**:

1. **Check configuration**:
   ```bash
   haven plugins configure YouTubePlugin --show
   ```

2. **Verify channel/playlist IDs**:
   - Channel ID format: `UCxxxxxxxxxxxxxxxxxxx`
   - Playlist ID format: `PLxxxxxxxxxxxxxxxxxxx`

3. **Test discovery**:
   ```bash
   haven plugins test YouTubePlugin --discover
   ```

4. **Check for duplicates**:
   - Already archived videos are filtered out
   - Check database for existing entries

---

## Pipeline Issues

### Pipeline step fails

**Problem**: One or more pipeline steps fail.

**Solutions**:

1. **Enable verbose logging**:
   ```bash
   haven upload video.mp4 --verbose
   ```

2. **Skip problematic steps**:
   ```bash
   # Skip VLM analysis
   haven upload video.mp4 --no-vlm
   
   # Skip blockchain sync
   haven upload video.mp4 --no-arkiv
   ```

3. **Check step-specific issues** (see below)

### VLM analysis slow or fails

**Problem**: VLM analysis takes too long or times out.

**Solutions**:

1. **Reduce frame sampling**:
   ```toml
   [pipeline]
   vlm_frame_sample_count = 5  # Default might be higher
   ```

2. **Use local model** (if available):
   ```toml
   [pipeline]
   vlm_model = "local-model-name"
   ```

3. **Skip VLM for specific uploads**:
   ```bash
   haven upload video.mp4 --no-vlm
   ```

4. **Check API key**:
   ```bash
   haven config show pipeline --unmask
   ```

### Encryption fails

**Problem**: Lit Protocol encryption fails.

**Solutions**:

1. **Check network connectivity** to Lit nodes

2. **Verify Lit network configuration**:
   ```toml
   [pipeline]
   lit_network = "datil-dev"  # or "datil-test", "datil"
   ```

3. **Skip encryption**:
   ```bash
   haven upload video.mp4  # without --encrypt
   ```

4. **Check JS runtime** (see [JavaScript Runtime Issues](#javascript-runtime-issues))

### Upload to Filecoin fails

**Problem**: Synapse upload fails.

**Solutions**:

1. **Check Synapse configuration**:
   ```bash
   haven config show pipeline
   ```

2. **Verify API key**:
   ```toml
   [pipeline]
   synapse_api_key = "your-api-key"
   ```

3. **Test connectivity**:
   ```bash
   haven config validate
   ```

4. **Skip upload**:
   ```toml
   [pipeline]
   upload_enabled = false
   ```

---

## Upload/Download Issues

### Upload fails with "Invalid file"

**Problem**: File is not recognized as a valid video.

**Solutions**:

1. **Check file format**:
   ```bash
   ffmpeg -i video.mp4
   ```

2. **Convert to supported format**:
   ```bash
   ffmpeg -i input.avi -c:v libx264 -c:a aac output.mp4
   ```

3. **Check file permissions**:
   ```bash
   ls -la video.mp4
   chmod 644 video.mp4
   ```

### Download fails

**Problem**: Cannot download file from Filecoin.

**Solutions**:

1. **Verify CID format**:
   - CIDv0: Starts with `Qm...`
   - CIDv1: Starts with `baf...`

2. **Check file status**:
   ```bash
   haven download info <cid>
   ```

3. **Check Synapse configuration**:
   ```bash
   haven config show pipeline
   ```

4. **Verify file exists on Filecoin**:
   - File may not be pinned
   - Storage deal may have expired

### Decryption fails

**Problem**: Cannot decrypt downloaded file.

**Solutions**:

1. **Verify CID matches**:
   ```bash
   haven download decrypt-file encrypted.mp4 --cid <original-cid>
   ```

2. **Check for sidecar file**:
   - Look for `.lit` file alongside encrypted file
   - Contains encryption metadata

3. **Check Lit network**:
   ```toml
   [pipeline]
   lit_network = "datil-dev"  # Must match upload network
   ```

4. **Verify access control conditions**:
   - Must meet the conditions set during encryption
   - Check wallet/chain requirements

---

## Daemon Issues

### Daemon won't start

**Problem**: `haven run` fails or exits immediately.

**Solutions**:

1. **Check if already running**:
   ```bash
   haven run status
   ```

2. **Check for stale PID file**:
   ```bash
   rm ~/.local/share/haven/haven.pid
   haven run
   ```

3. **Check logs**:
   ```bash
   cat ~/.local/share/haven/daemon.log
   ```

4. **Run in foreground with verbose logging**:
   ```bash
   haven run --verbose
   ```

### Daemon stops unexpectedly

**Problem**: Daemon process terminates.

**Solutions**:

1. **Check system resources**:
   ```bash
   free -h  # Memory
   df -h    # Disk space
   ```

2. **Check logs for errors**:
   ```bash
   tail -f ~/.local/share/haven/daemon.log
   ```

3. **Reduce concurrent pipelines**:
   ```bash
   haven run --max-concurrent 2
   ```

4. **Check for OOM killer**:
   ```bash
   dmesg | grep -i "killed process"
   ```

### Jobs not running

**Problem**: Scheduled jobs don't execute.

**Solutions**:

1. **Check daemon status**:
   ```bash
   haven run status
   ```

2. **Check job status**:
   ```bash
   haven jobs list
   ```

3. **Resume paused jobs**:
   ```bash
   haven jobs resume <job-id>
   ```

4. **Check job history**:
   ```bash
   haven jobs history
   ```

5. **Verify scheduler is enabled**:
   ```toml
   [scheduler]
   enabled = true
   ```

---

## JavaScript Runtime Issues

### JS runtime not detected

**Problem**: `No JavaScript runtime detected`

**Solutions**:

1. **Install Deno** (recommended):
   ```bash
   # macOS/Linux
   curl -fsSL https://deno.land/install.sh | sh
   
   # Add to PATH
   export PATH="$HOME/.deno/bin:$PATH"
   ```

2. **Or install Node.js**:
   ```bash
   # macOS
   brew install node
   
   # Ubuntu/Debian
   sudo apt-get install nodejs
   ```

3. **Verify installation**:
   ```bash
   deno --version
   # or
   node --version
   ```

4. **Specify runtime in config**:
   ```toml
   [js_runtime]
   runtime = "deno"  # or "node", "bun"
   ```

### JS runtime fails to start

**Problem**: JavaScript runtime cannot be started.

**Solutions**:

1. **Check JS runtime path**:
   ```bash
   which deno
   which node
   ```

2. **Increase startup timeout**:
   ```toml
   [js_runtime]
   startup_timeout = 60.0
   ```

3. **Enable debug mode**:
   ```toml
   [js_runtime]
   debug = true
   ```

4. **Check for port conflicts**:
   ```bash
   # Check if default port is in use
   lsof -i :8765
   ```

### JS bridge connection fails

**Problem**: Cannot connect to JS runtime.

**Solutions**:

1. **Restart the daemon**:
   ```bash
   haven run stop
   haven run
   ```

2. **Check for firewall issues**:
   ```bash
   # Allow localhost connections
   ```

3. **Increase request timeout**:
   ```toml
   [js_runtime]
   request_timeout = 120.0
   ```

---

## Database Issues

### Database locked

**Problem**: `database is locked` error.

**Solutions**:

1. **Check for multiple processes**:
   ```bash
   ps aux | grep haven
   ```

2. **Kill stale processes**:
   ```bash
   pkill -f "haven run"
   ```

3. **Wait and retry**:
   - Another process may be holding the lock
   - Wait a few seconds and try again

### Database corrupt

**Problem**: Database errors or corruption.

**Solutions**:

1. **Backup database**:
   ```bash
   cp ~/.local/share/haven/haven.db ~/.local/share/haven/haven.db.backup
   ```

2. **Check database integrity**:
   ```bash
   sqlite3 ~/.local/share/haven/haven.db "PRAGMA integrity_check;"
   ```

3. **Recreate database** (WARNING: data loss):
   ```bash
   rm ~/.local/share/haven/haven.db
   haven config init
   ```

### Data directory not writable

**Problem**: Cannot write to data directory.

**Solutions**:

1. **Check permissions**:
   ```bash
   ls -la ~/.local/share/haven
   ```

2. **Fix permissions**:
   ```bash
   chmod 755 ~/.local/share/haven
   chown $USER:$USER ~/.local/share/haven
   ```

3. **Change data directory**:
   ```bash
   export HAVEN_DATA_DIR=/path/to/writable/dir
   ```

---

## Getting Help

### Debug Mode

Enable debug logging for detailed information:

```bash
# CLI commands
haven upload video.mp4 --debug
haven run --verbose

# Environment variable
export HAVEN_LOG_LEVEL=DEBUG
```

### Log Files

Check log files for errors:

```bash
# Daemon logs
cat ~/.local/share/haven/daemon.log

# Real-time log monitoring
tail -f ~/.local/share/haven/daemon.log
```

### System Information

Gather system information for bug reports:

```bash
# Haven version
haven --version

# Python version
python --version

# System info
uname -a

# Disk space
df -h

# Memory
free -h
```

### Getting Support

1. **Check documentation**:
   - [User Guide](user-guide.md)
   - [CLI Reference](cli-reference.md)
   - [Configuration Reference](configuration.md)

2. **Search existing issues**:
   - [GitHub Issues](https://github.com/haven/haven-cli/issues)
   - [GitHub Discussions](https://github.com/haven/haven-cli/discussions)

3. **Create a bug report**:
   Include:
   - Haven version (`haven --version`)
   - Python version (`python --version`)
   - Operating system
   - Steps to reproduce
   - Error messages
   - Relevant log output

4. **Community support**:
   - Join our Discord/Slack
   - Ask in GitHub Discussions

### Common Error Messages

| Error | Solution |
|-------|----------|
| `ModuleNotFoundError` | Reinstall: `pip install --force-reinstall haven-cli` |
| `Permission denied` | Check file permissions, use `chmod` |
| `Connection refused` | Check if daemon is running |
| `TimeoutError` | Increase timeout in config or check network |
| `No such file or directory` | Check paths in configuration |
| `Address already in use` | Another process using the port, restart |

### Recovery Procedures

#### Full Reset

WARNING: This will delete all configuration and data!

```bash
# Stop daemon
haven run stop

# Backup data (optional)
cp -r ~/.local/share/haven ~/haven-backup

# Remove data
rm -rf ~/.local/share/haven
rm -rf ~/.config/haven

# Reinitialize
haven config init
```

#### Reset Configuration Only

```bash
# Backup config
cp ~/.config/haven/config.toml ~/.config/haven/config.toml.backup

# Regenerate
haven config init --force
```

#### Reset Database Only

```bash
# Stop daemon
haven run stop

# Backup database
cp ~/.local/share/haven/haven.db ~/.local/share/haven/haven.db.backup

# Remove database
rm ~/.local/share/haven/haven.db

# Database will be recreated on next run
```
