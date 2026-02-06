# CLI Reference

Complete reference for all Haven CLI commands.

## Global Options

These options are available for all commands:

| Option | Short | Description |
|--------|-------|-------------|
| `--version` | `-v` | Show version and exit |
| `--verbose` | `-V` | Enable verbose output (INFO level logging) |
| `--debug` | | Enable debug mode (DEBUG level logging with full tracebacks) |
| `--json` | | Output in JSON format where applicable |
| `--quiet` | `-q` | Suppress non-error output |
| `--log-file` | | Log to file (logs DEBUG level regardless of console settings) |

## Command Overview

```
haven [GLOBAL OPTIONS] <command> [COMMAND OPTIONS] [ARGS]
```

| Command | Description |
|---------|-------------|
| `upload` | Upload files to Filecoin |
| `download` | Download and decrypt files from Filecoin |
| `jobs` | Manage scheduled jobs |
| `plugins` | Manage archiver plugins |
| `run` | Start and manage the daemon |
| `config` | Manage configuration |

---

## haven upload

Upload a file to Filecoin network.

```bash
haven upload <file> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `file` | Path to the file to upload |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--encrypt` | `-e` | Encrypt file with Lit Protocol before upload |
| `--no-vlm` | | Skip VLM analysis step |
| `--dataset` | `-d` | Dataset ID for Filecoin upload |
| `--no-arkiv` | | Skip Arkiv blockchain sync |
| `--config` | `-c` | Path to configuration file |

### Description

This command processes a single file through the pipeline:

1. **Ingest** - Calculate pHash, create database entry
2. **Analyze** - VLM analysis (optional, skip with `--no-vlm`)
3. **Encrypt** - Lit Protocol encryption (optional, enable with `--encrypt`)
4. **Upload** - Upload to Filecoin network
5. **Sync** - Sync metadata to Arkiv blockchain (optional, skip with `--no-arkiv`)

### Examples

```bash
# Basic upload
haven upload video.mp4

# Upload with encryption
haven upload video.mp4 --encrypt

# Upload without VLM analysis
haven upload video.mp4 --no-vlm

# Upload to specific dataset
haven upload video.mp4 --encrypt --dataset 123

# Upload without blockchain sync
haven upload video.mp4 --no-arkiv

# Upload with custom config
haven upload video.mp4 --config /path/to/config.toml
```

---

## haven download

Download and decrypt files from Filecoin network.

### Usage

```bash
haven download <cid> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `cid` | Content ID (CID) of the file to download |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--output` | `-o` | Output path for downloaded file (required) |
| `--decrypt` | `-d` | Decrypt file after download using Lit Protocol |
| `--force` | `-f` | Overwrite existing file if it exists |
| `--config` | `-c` | Path to configuration file |

### Description

Retrieves a file by its CID and optionally decrypts it using Lit Protocol if it was encrypted during upload.

### Examples

```bash
# Basic download
haven download bafybeig... --output video.mp4

# Download with decryption
haven download bafybeig... --output video.mp4 --decrypt

# Force overwrite existing file
haven download bafybeig... --output video.mp4 --force
```

---

## haven download info

Get information about a file stored on Filecoin.

```bash
haven download info <cid> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `cid` | Content ID (CID) to get information about |

### Options

| Option | Description |
|--------|-------------|
| `--config` | Path to configuration file |
| `--json` | Output in JSON format |

### Examples

```bash
# Show file info
haven download info bafybeig...

# Output as JSON
haven download info bafybeig... --json
```

---

## haven download decrypt-file

Decrypt a local file using Lit Protocol.

```bash
haven download decrypt-file <file> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `file` | Path to the encrypted file |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--output` | `-o` | Output path for decrypted file |
| `--cid` | | CID for metadata lookup (if different from file) |
| `--force` | `-f` | Overwrite existing file if it exists |
| `--config` | `-c` | Path to configuration file |

### Description

Decrypts a file that was encrypted with Lit Protocol. Encryption metadata is looked up from the database (by CID) or from a sidecar `.lit` file.

### Examples

```bash
# Decrypt a file
haven download decrypt-file encrypted.mp4 --output decrypted.mp4

# Decrypt with specific CID for metadata lookup
haven download decrypt-file encrypted.mp4 --output decrypted.mp4 --cid bafybeig...
```

---

## haven jobs

Manage scheduled jobs for plugin polling.

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `list` | List all scheduled jobs |
| `create` | Create a new scheduled job |
| `delete` | Delete a scheduled job |
| `run` | Run a job immediately (outside of schedule) |
| `pause` | Pause a scheduled job |
| `resume` | Resume a paused job |
| `history` | Show job execution history |

---

## haven jobs list

List all scheduled jobs.

```bash
haven jobs list [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--status` | `-s` | Filter by status (active, paused, all) |

### Examples

```bash
# List all jobs
haven jobs list

# List only active jobs
haven jobs list --status active

# List only paused jobs
haven jobs list --status paused
```

---

## haven jobs create

Create a new scheduled job.

```bash
haven jobs create [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--plugin` | `-p` | Plugin name to use for discovery (required) |
| `--schedule` | `-s` | Cron schedule expression (required) |
| `--on-success` | | Action on success: archive_all, archive_new, log_only |
| `--name` | `-n` | Optional job name |

### Description

Creates a recurring job that runs on a cron schedule. The job will discover sources using the specified plugin and perform the configured action.

### Cron Schedule Format

| Expression | Description |
|------------|-------------|
| `0 * * * *` | Every hour |
| `*/30 * * * *` | Every 30 minutes |
| `0 */6 * * *` | Every 6 hours |
| `0 0 * * *` | Daily at midnight |
| `@daily` | Daily at midnight |
| `@hourly` | Every hour |

### Examples

```bash
# Create hourly job for YouTube plugin
haven jobs create --plugin YouTubePlugin --schedule "0 * * * *"

# Create job with custom name and action
haven jobs create --plugin YouTubePlugin --schedule "*/30 * * * *" \
  --name "Hourly YouTube Check" --on-success archive_new

# Create daily job
haven jobs create --plugin BitTorrentPlugin --schedule "0 0 * * *"
```

---

## haven jobs delete

Delete a scheduled job.

```bash
haven jobs delete <job-id> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `job-id` | ID of the job to delete |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--force` | `-f` | Skip confirmation prompt |

### Examples

```bash
# Delete a job (with confirmation)
haven jobs delete job-001

# Force delete without confirmation
haven jobs delete job-001 --force
```

---

## haven jobs run

Run a job immediately (outside of schedule).

```bash
haven jobs run <job-id>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `job-id` | ID of the job to run immediately |

### Examples

```bash
# Run a job now
haven jobs run job-001
```

---

## haven jobs pause

Pause a scheduled job.

```bash
haven jobs pause <job-id>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `job-id` | ID of the job to pause |

### Examples

```bash
haven jobs pause job-001
```

---

## haven jobs resume

Resume a paused job.

```bash
haven jobs resume <job-id>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `job-id` | ID of the job to resume |

### Examples

```bash
haven jobs resume job-001
```

---

## haven jobs history

Show job execution history.

```bash
haven jobs history [job-id] [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `job-id` | Job ID to show history for (or all if not specified) |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-l` | Number of history entries to show (default: 10) |

### Examples

```bash
# Show all job history
haven jobs history

# Show history for specific job
haven jobs history job-001

# Show more entries
haven jobs history --limit 50
```

---

## haven plugins

Manage archiver plugins.

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `list` | List all available plugins |
| `info` | Show detailed information about a plugin |
| `enable` | Enable a plugin |
| `disable` | Disable a plugin |
| `configure` | Configure a plugin |
| `test` | Test a plugin's functionality |

---

## haven plugins list

List all available plugins.

```bash
haven plugins list [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--all` | `-a` | Show disabled plugins as well |

### Examples

```bash
# List active plugins
haven plugins list

# List all plugins including disabled
haven plugins list --all
```

---

## haven plugins info

Show detailed information about a plugin.

```bash
haven plugins info <name>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `name` | Name of the plugin to get info about |

### Examples

```bash
haven plugins info YouTubePlugin
```

---

## haven plugins enable

Enable a plugin.

```bash
haven plugins enable <name>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `name` | Name of the plugin to enable |

### Examples

```bash
haven plugins enable YouTubePlugin
```

---

## haven plugins disable

Disable a plugin.

```bash
haven plugins disable <name>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `name` | Name of the plugin to disable |

### Examples

```bash
haven plugins disable YouTubePlugin
```

---

## haven plugins configure

Configure a plugin.

```bash
haven plugins configure <name> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `name` | Name of the plugin to configure |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--set` | `-s` | Set a configuration value (format: key=value) |
| `--show` | | Show current configuration |

### Examples

```bash
# Show current configuration
haven plugins configure YouTubePlugin --show

# Set configuration values
haven plugins configure YouTubePlugin --set api_key=YOUR_API_KEY
haven plugins configure YouTubePlugin --set channel_ids=UCxxx,UCyyy
haven plugins configure YouTubePlugin --set max_videos=20
```

---

## haven plugins test

Test a plugin's functionality.

```bash
haven plugins test <name> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `name` | Name of the plugin to test |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--discover` | `-d` | Test discovery functionality |
| `--archive` | `-a` | Test archiving with a specific URL |

### Examples

```bash
# Run basic health check
haven plugins test YouTubePlugin

# Test discovery
haven plugins test YouTubePlugin --discover

# Test archiving a specific URL
haven plugins test YouTubePlugin --archive https://youtube.com/watch?v=...
```

---

## haven run

Start Haven daemon with scheduler and pipeline processing.

```bash
haven run [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to configuration file |
| `--daemon` | `-d` | Run in background as daemon |
| `--max-concurrent` | `-m` | Maximum concurrent pipeline executions (default: 4) |
| `--verbose` | `-v` | Enable verbose logging |

### Description

Starts the main Haven service which:
- Loads and executes scheduled jobs (plugin polling)
- Processes videos through the pipeline (ingest → analyze → encrypt → upload → sync)
- Manages parallel execution of multiple pipelines

### Examples

```bash
# Run in foreground
haven run

# Run in background
haven run --daemon

# Run with verbose logging
haven run --verbose

# Run with more concurrent pipelines
haven run --max-concurrent 8

# Run with custom config
haven run --config /path/to/config.toml
```

---

## haven run status

Check daemon status.

```bash
haven run status [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to configuration file |

### Examples

```bash
haven run status
```

---

## haven run stop

Stop the daemon.

```bash
haven run stop [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to configuration file |
| `--force` | `-f` | Force kill the daemon (SIGKILL) |

### Description

Sends a shutdown signal to the running daemon process. By default, sends SIGTERM for graceful shutdown. Use `--force` to send SIGKILL for immediate termination.

### Examples

```bash
# Graceful shutdown
haven run stop

# Force kill
haven run stop --force
```

---

## haven run restart

Restart the daemon.

```bash
haven run restart [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to configuration file |
| `--daemon` | `-d` | Run in background as daemon |
| `--max-concurrent` | `-m` | Maximum concurrent pipeline executions |
| `--verbose` | `-v` | Enable verbose logging |

### Examples

```bash
haven run restart
haven run restart --daemon
```

---

## haven config

Manage Haven configuration.

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `show` | Show current configuration |
| `set` | Set a configuration value |
| `init` | Initialize Haven configuration |
| `path` | Show configuration file path |
| `validate` | Validate current configuration |
| `edit` | Open configuration file in editor |
| `env` | Show supported environment variables |

---

## haven config show

Show current configuration.

```bash
haven config show [section] [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `section` | Configuration section to show (e.g., pipeline, scheduler, plugins) |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--format` | `-f` | Output format: table, yaml, json (default: table) |
| `--unmask` | | Show unmasked secrets (use with caution) |

### Examples

```bash
# Show all configuration
haven config show

# Show specific section
haven config show pipeline
haven config show scheduler

# Output as YAML
haven config show --format yaml

# Output as JSON
haven config show --format json

# Show unmasked secrets
haven config show --unmask
```

---

## haven config set

Set a configuration value.

```bash
haven config set <key> <value>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `key` | Configuration key (format: section.key, e.g., pipeline.vlm_model) |
| `value` | Value to set |

### Examples

```bash
haven config set pipeline.vlm_model zai-org/glm-4.6v-flash
haven config set pipeline.max_concurrent_videos 8
haven config set scheduler.enabled false
haven config set logging.level DEBUG
```

---

## haven config init

Initialize Haven configuration.

```bash
haven config init [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--force` | `-f` | Overwrite existing configuration |
| `--interactive` | `-i` | Run interactive configuration wizard (default: true) |
| `--no-interactive` | `-I` | Skip interactive wizard |

### Examples

```bash
# Interactive setup
haven config init

# Non-interactive setup
haven config init --no-interactive

# Overwrite existing config
haven config init --force
```

---

## haven config path

Show configuration file path.

```bash
haven config path
```

---

## haven config validate

Validate current configuration.

```bash
haven config validate
```

---

## haven config edit

Open configuration file in editor.

```bash
haven config edit
```

Uses the `$EDITOR` environment variable to determine which editor to open.

---

## haven config env

Show supported environment variables.

```bash
haven config env
```

Displays all environment variables that can be used to override configuration values.

---

## Exit Codes

| Code | Description |
|------|-------------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 3 | Configuration error |
| 4 | Network error |
| 5 | Service unavailable |
| 130 | Interrupted by user (Ctrl+C) |
