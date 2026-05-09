# Configuration Reference

## File Location

Default: `~/.config/haven/config.toml`

Override with: `HAVEN_CONFIG_DIR` environment variable

## Configuration Loading Order

Configuration is loaded in the following priority (highest to lowest):

1. Environment variables (`HAVEN_*`)
2. Configuration file (`~/.config/haven/config.toml`)
3. Default values

## Full Configuration Example

```toml
# Haven CLI Configuration
# Generated automatically - edit with care

config_dir = "~/.config/haven"
data_dir = "~/.local/share/haven"
database_url = "sqlite:///~/.local/share/haven/haven.db"

[blockchain]
# Network mode: "testnet" or "mainnet"
# This automatically configures all blockchain integrations
network_mode = "testnet"

# Optional: Override specific endpoints
# filecoin_rpc_override = "https://api.calibration.node.glif.io/rpc/v1"
# arkiv_rpc_override = "https://mendoza.hoodi.arkiv.network/rpc"

[pipeline]
vlm_enabled = true
vlm_model = "zai-org/glm-4.6v-flash"
vlm_api_key = ""  # Or use HAVEN_VLM_API_KEY
vlm_timeout = 120.0

encryption_enabled = true

upload_enabled = true

sync_enabled = true
arkiv_contract = "0x..."

max_concurrent_videos = 4
retry_attempts = 3
retry_delay = 5.0

[scheduler]
enabled = true
check_interval = 60
max_concurrent_jobs = 2
default_cron = "0 */6 * * *"
job_timeout = 3600
state_file = "~/.local/share/haven/scheduler_state.json"

[plugins]
plugin_dirs = []
enabled_plugins = []
disabled_plugins = []

[plugins.settings.YouTubePlugin]
channel_ids = []
playlist_ids = []
max_videos = 10
quality = "best"
output_dir = "~/haven/downloads"

[js_runtime]
runtime = "deno"  # or "node", "bun", null for auto-detect
services_path = ""
startup_timeout = 30.0
request_timeout = 60.0
debug = false

[logging]
level = "INFO"
format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
file = ""
max_size = 10485760
backup_count = 5
```

## Configuration Sections

### Pipeline Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| vlm_enabled | boolean | true | Enable AI analysis with VLM |
| vlm_model | string | "zai-org/glm-4.6v-flash" | VLM model to use |
| vlm_api_key | string | null | API key for VLM service |
| vlm_timeout | float | 120.0 | Timeout for VLM requests (seconds) |
| encryption_enabled | boolean | true | Enable Haven-AOL encryption |
| upload_enabled | boolean | true | Enable Filecoin upload |
| sync_enabled | boolean | true | Enable Arkiv blockchain sync |
| arkiv_contract | string | null | Arkiv contract address |

### Blockchain Configuration

The `[blockchain]` section provides unified network configuration for all blockchain integrations.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| network_mode | string | "testnet" | Network mode: "testnet" or "mainnet" |
| filecoin_rpc_override | string | null | Override Filecoin RPC endpoint URL |
| arkiv_rpc_override | string | null | Override Arkiv RPC endpoint URL |

**Note:** When `network_mode` is set to "testnet" or "mainnet", the appropriate endpoints for Filecoin and Arkiv are automatically configured. Use the `*_override` settings only if you need to use custom endpoints.
| max_concurrent_videos | integer | 4 | Maximum concurrent video processing |
| retry_attempts | integer | 3 | Number of retry attempts for failed operations |
| retry_delay | float | 5.0 | Delay between retries (seconds) |

### Scheduler Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| enabled | boolean | true | Enable job scheduler |
| check_interval | integer | 60 | Interval to check for due jobs (seconds) |
| max_concurrent_jobs | integer | 2 | Maximum concurrent job executions |
| default_cron | string | "0 */6 * * *" | Default cron schedule for new jobs |
| job_timeout | integer | 3600 | Job execution timeout (seconds) |
| state_file | string | null | Path to scheduler state file |

### Plugin Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| plugin_dirs | list | [] | Additional plugin directories |
| enabled_plugins | list | [] | List of enabled plugin names |
| disabled_plugins | list | [] | List of disabled plugin names |
| plugin_settings | dict | {} | Plugin-specific settings |

### JS Runtime Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| runtime | string | null | JS runtime: "deno", "node", "bun", or null for auto-detect |
| services_path | string | null | Path to JS services directory |
| startup_timeout | float | 30.0 | JS runtime startup timeout (seconds) |
| request_timeout | float | 60.0 | JS runtime request timeout (seconds) |
| debug | boolean | false | Enable debug logging for JS runtime |

### Logging Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| level | string | "INFO" | Logging level (DEBUG, INFO, WARNING, ERROR) |
| format | string | "%(asctime)s..." | Log message format |
| file | string | null | Log file path (null for stdout only) |
| max_size | integer | 10485760 | Maximum log file size (bytes) |
| backup_count | integer | 5 | Number of log file backups to keep |

## Environment Variables

All configuration options can be overridden via environment variables:

### Pipeline Variables

| Variable | Description | Example |
|----------|-------------|---------|
| HAVEN_VLM_ENABLED | Enable/disable VLM analysis | true/false |
| HAVEN_VLM_MODEL | VLM model to use | zai-org/glm-4.6v-flash |
| HAVEN_VLM_API_KEY | API key for VLM service | sk-... |
| HAVEN_ENCRYPTION_ENABLED | Enable/disable Haven-AOL encryption | true/false |
| HAVEN_UPLOAD_ENABLED | Enable/disable Filecoin upload | true/false |
| HAVEN_SYNC_ENABLED | Enable/disable Arkiv sync | true/false |

### Blockchain Variables

| Variable | Description | Example |
|----------|-------------|---------|
| HAVEN_NETWORK_MODE | Network mode (testnet/mainnet) | testnet |
| HAVEN_FILECOIN_RPC_OVERRIDE | Override Filecoin RPC endpoint | https://... |
| HAVEN_ARKIV_RPC_OVERRIDE | Override Arkiv RPC endpoint | https://... |

### Scheduler Variables

| Variable | Description | Example |
|----------|-------------|---------|
| HAVEN_SCHEDULER_ENABLED | Enable/disable job scheduler | true/false |

### JS Runtime Variables

| Variable | Description | Example |
|----------|-------------|---------|
| HAVEN_JS_RUNTIME | JS runtime to use | deno/node/bun |
| HAVEN_JS_DEBUG | Enable JS debug mode | true/false |

### Path Variables

| Variable | Description | Example |
|----------|-------------|---------|
| HAVEN_CONFIG_DIR | Configuration directory | ~/.config/haven |
| HAVEN_DATA_DIR | Data storage directory | ~/.local/share/haven |
| HAVEN_DATABASE_URL | Database connection URL | sqlite:///... |

### Logging Variables

| Variable | Description | Example |
|----------|-------------|---------|
| HAVEN_LOG_LEVEL | Logging level | DEBUG/INFO/WARNING/ERROR |

## Configuration Commands

### Initialize Configuration

```bash
# Interactive setup
haven config init

# Non-interactive
haven config init --no-interactive

# Overwrite existing
haven config init --force
```

### View Configuration

```bash
# Table format (default)
haven config show

# YAML format
haven config show --format yaml

# JSON format
haven config show --format json

# Show specific section
haven config show pipeline

# Show unmasked secrets
haven config show --unmask
```

### Set Configuration Values

```bash
haven config set <section.key> <value>
```

Examples:

```bash
haven config set pipeline.vlm_model zai-org/glm-4.6v-flash
haven config set pipeline.max_concurrent_videos 8
haven config set scheduler.enabled false
haven config set logging.level DEBUG
```

### Validate Configuration

```bash
haven config validate
```

### Show Configuration Path

```bash
haven config path
```

### Edit Configuration File

```bash
haven config edit  # Opens in $EDITOR
```

### Show Environment Variables

```bash
haven config env
```

## Security Considerations

### API Keys and Secrets

- API keys can be set via environment variables to avoid storing them in config files
- Config file is created with 0600 permissions (owner read/write only)
- Secrets are masked in `haven config show` output
- Use `haven config show --unmask` with caution

### File Permissions

The configuration file is created with restrictive permissions:

```bash
-rw------- 1 user user  1234 Jan  1 12:00 config.toml
```

Only the owner can read or write the configuration file.

## Validation

Configuration is validated on load. Common validation errors:

- Invalid cron expressions
- Invalid URL formats
- Non-writable directories
- Missing required fields

Run `haven config validate` to check your configuration.
