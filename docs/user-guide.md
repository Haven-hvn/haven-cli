# Haven CLI User Guide

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Basic Usage](#basic-usage)
4. [Pipeline Steps](#pipeline-steps)
5. [Plugins](#plugins)
6. [Scheduling](#scheduling)
7. [Troubleshooting](#troubleshooting)

## Installation

### Prerequisites

- Python 3.11+
- FFmpeg (for video processing)
- Deno (for JS runtime)
- yt-dlp (for YouTube plugin)

### Install via pip

```bash
pip install haven-cli
```

### Install from Source

```bash
git clone https://github.com/haven/haven-cli
cd haven-cli
pip install -e .
```

## Configuration

### Initialize Configuration

```bash
haven config init
```

This creates `~/.config/haven/config.toml` with default settings.

### Configuration Options

| Section | Key | Description | Default |
|---------|-----|-------------|---------|
| pipeline | vlm_enabled | Enable AI analysis | true |
| pipeline | encryption_enabled | Enable Lit encryption | true |
| pipeline | upload_enabled | Enable Filecoin upload | true |
| pipeline | sync_enabled | Enable Arkiv sync | true |
| scheduler | enabled | Enable job scheduler | true |
| js_runtime | runtime | JS runtime (deno/node/bun) | auto-detect |

### Environment Variables

All configuration can be overridden via environment variables:

```bash
export HAVEN_VLM_ENABLED=true
export HAVEN_SYNAPSE_API_KEY=your-key
export HAVEN_LOG_LEVEL=DEBUG
```

## Basic Usage

### Upload a Video

```bash
# Basic upload
haven upload video.mp4

# With encryption
haven upload video.mp4 --encrypt

# Skip analysis
haven upload video.mp4 --no-vlm

# Skip blockchain sync
haven upload video.mp4 --no-arkiv

# Specify dataset
haven upload video.mp4 --dataset 123
```

### Download from Filecoin

```bash
# Download by CID
haven download bafybeig... --output video.mp4

# With decryption
haven download bafybeig... --output video.mp4 --decrypt

# Force overwrite
haven download bafybeig... --output video.mp4 --force
```

### Check File Status

```bash
haven download info bafybeig...
haven download info bafybeig... --json
```

### Decrypt a Local File

```bash
haven download decrypt-file encrypted.mp4 --output decrypted.mp4
haven download decrypt-file encrypted.mp4 --cid bafybeig...
```

## Pipeline Steps

The Haven pipeline processes videos through these steps:

```
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌────────┐   ┌──────┐
│ Ingest  │──▶│ Analyze │──▶│ Encrypt │──▶│ Upload │──▶│ Sync │
└─────────┘   └─────────┘   └─────────┘   └────────┘   └──────┘
```

1. **Ingest**: Extract metadata, calculate pHash, check duplicates
2. **Analyze**: Run VLM to generate timestamps and tags (optional)
3. **Encrypt**: Encrypt with Lit Protocol (optional)
4. **Upload**: Store on Filecoin via Synapse (optional)
5. **Sync**: Record metadata on Arkiv blockchain (optional)

### Pipeline Options

Each step can be enabled or disabled:

```bash
# Full pipeline (default)
haven upload video.mp4

# Skip VLM analysis
haven upload video.mp4 --no-vlm

# Skip encryption
haven upload video.mp4  # encryption enabled by config

# Skip upload (dry run)
# Set upload_enabled = false in config

# Skip blockchain sync
haven upload video.mp4 --no-arkiv
```

## Plugins

### List Available Plugins

```bash
haven plugins list
haven plugins list --all  # include disabled
```

### Configure a Plugin

```bash
# Show current config
haven plugins config YouTubePlugin --show

# Set a configuration value
haven plugins config YouTubePlugin --set channel_ids=UCxxx,UCyyy
haven plugins config YouTubePlugin --set api_key=YOUR_API_KEY
```

### Test a Plugin

```bash
haven plugins test YouTubePlugin
```

### Enable/Disable Plugins

```bash
haven plugins enable YouTubePlugin
haven plugins disable YouTubePlugin
```

### View Plugin Information

```bash
haven plugins info YouTubePlugin
```

### Available Plugins

| Plugin | Description |
|--------|-------------|
| YouTubePlugin | Archive videos from YouTube channels/playlists |
| BitTorrentPlugin | Archive torrents from feeds/DHT |
| PumpFunPlugin | Record PumpFun live streams |
| OpenRingPlugin | Capture WebRTC streams |

## Scheduling

### Create a Job

```bash
# Create a job with cron schedule
haven jobs create --plugin YouTubePlugin --schedule "0 * * * *"

# With custom name and action
haven jobs create --plugin YouTubePlugin --schedule "*/30 * * * *" \
  --name "Hourly YouTube Check" --on-success archive_new
```

### List Jobs

```bash
haven jobs list
haven jobs list --status active
haven jobs list --status paused
```

### Run Job Manually

```bash
haven jobs run <job-id>
```

### Pause and Resume Jobs

```bash
haven jobs pause <job-id>
haven jobs resume <job-id>
```

### Delete a Job

```bash
haven jobs delete <job-id>
haven jobs delete <job-id> --force  # skip confirmation
```

### View Job History

```bash
haven jobs history
haven jobs history <job-id> --limit 20
```

### Cron Schedule Format

The scheduler uses standard cron expressions:

| Expression | Description |
|------------|-------------|
| `0 * * * *` | Every hour |
| `*/30 * * * *` | Every 30 minutes |
| `0 */6 * * *` | Every 6 hours |
| `0 0 * * *` | Daily at midnight |
| `@daily` | Daily at midnight |
| `@hourly` | Every hour |

## Daemon Management

### Start the Daemon

```bash
# Run in foreground
haven run

# Run in background
haven run --daemon

# With verbose logging
haven run --verbose

# Limit concurrent pipelines
haven run --max-concurrent 8
```

### Check Daemon Status

```bash
haven run status
```

### Stop the Daemon

```bash
# Graceful shutdown
haven run stop

# Force kill
haven run stop --force
```

### Restart the Daemon

```bash
haven run restart
haven run restart --daemon
```

## Configuration Management

### View Configuration

```bash
# Show all config
haven config show

# Show specific section
haven config show pipeline

# Output as YAML
haven config show --format yaml

# Show unmasked secrets (use with caution)
haven config show --unmask
```

### Set Configuration Values

```bash
haven config set pipeline.vlm_model zai-org/glm-4.6v-flash
haven config set pipeline.max_concurrent_videos 8
haven config set scheduler.enabled false
```

### Validate Configuration

```bash
haven config validate
```

### Edit Configuration

```bash
haven config edit  # Opens in $EDITOR
```

### Show Environment Variables

```bash
haven config env
```

## Troubleshooting

### Common Issues

**JS Runtime not starting**
- Ensure Deno is installed: `deno --version`
- Check JS services path in config
- Try setting `HAVEN_JS_RUNTIME=deno`

**Upload failing**
- Verify Synapse API key is set
- Check network connectivity
- Check `haven config validate` output

**VLM analysis slow**
- Consider using local model
- Reduce frame sampling count in config

**Database errors**
- Ensure data directory is writable
- Check `HAVEN_DATA_DIR` permissions

### Getting Help

```bash
# Show help for any command
haven --help
haven upload --help
haven jobs --help
```

### Debug Logging

```bash
export HAVEN_LOG_LEVEL=DEBUG
haven run --verbose
```

For more troubleshooting tips, see [Troubleshooting Guide](troubleshooting.md).
