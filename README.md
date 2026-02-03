# Haven CLI

Decentralized video archival with AI-powered analysis and blockchain verification.

## Features

- 📹 **Video Archival**: Archive videos from YouTube, local files, and more
- 🔐 **Encryption**: Lit Protocol access-controlled encryption
- 🗄️ **Decentralized Storage**: Filecoin/IPFS via Synapse
- 🤖 **AI Analysis**: VLM-powered timestamp and tag generation
- ⛓️ **Blockchain Sync**: Arkiv on-chain metadata records
- 🔌 **Plugin System**: Extensible archiver plugins
- ⏰ **Scheduling**: Cron-based automated archival

## Quick Start

### Installation

```bash
pip install haven-cli
```

### Configuration

```bash
haven config init
```

### Upload a Video

```bash
haven upload video.mp4
```

### Start Daemon

```bash
haven run
```

## Documentation

- [User Guide](docs/user-guide.md) - Comprehensive guide to using Haven CLI
- [Configuration](docs/configuration.md) - Configuration options and environment variables
- [Plugins](docs/plugins.md) - Plugin system documentation
- [API Reference](docs/api.md) - Python API documentation
- [CLI Reference](docs/cli-reference.md) - Command-line reference
- [Troubleshooting](docs/troubleshooting.md) - Common issues and solutions

## Requirements

- Python 3.11+
- FFmpeg (for video processing)
- Deno 1.40+ (for JS runtime)
- yt-dlp (for YouTube plugin)

## Installation

### From PyPI

```bash
pip install haven-cli
```

### From Source

```bash
git clone https://github.com/haven/haven-cli
cd haven-cli
pip install -e .
```

### Development Installation

```bash
git clone https://github.com/haven/haven-cli
cd haven-cli
pip install -e ".[dev]"
```

## Quick Command Reference

| Command | Description |
|---------|-------------|
| `haven config init` | Initialize configuration |
| `haven config show` | Show current configuration |
| `haven upload <file>` | Upload a video file |
| `haven download <cid>` | Download a file by CID |
| `haven jobs list` | List scheduled jobs |
| `haven jobs create --plugin <name> --schedule <cron>` | Create a scheduled job |
| `haven plugins list` | List available plugins |
| `haven plugins enable <name>` | Enable a plugin |
| `haven plugins test <name>` | Test a plugin |
| `haven run` | Start the daemon |
| `haven run status` | Check daemon status |
| `haven run stop` | Stop the daemon |

## Pipeline Overview

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

## Configuration

Configuration is stored in `~/.config/haven/config.toml`:

```toml
[pipeline]
vlm_enabled = true
encryption_enabled = true
upload_enabled = true
sync_enabled = true

[scheduler]
enabled = true

[js_runtime]
runtime = "deno"
```

See [Configuration Reference](docs/configuration.md) for all options.

## Environment Variables

All configuration can be overridden via environment variables:

```bash
export HAVEN_VLM_ENABLED=true
export HAVEN_SYNAPSE_API_KEY=your-key
export HAVEN_LOG_LEVEL=DEBUG
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy haven_cli

# Linting
ruff check haven_cli

# Format code
ruff format haven_cli
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        HAVEN CLI                             │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Upload    │  │  Download   │  │   Job Scheduler     │  │
│  │   Command   │  │   Command   │  │   (Cron-based)      │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                    │             │
│         └────────────────┴────────────────────┘             │
│                              │                              │
│                              ▼                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                   Pipeline Engine                    │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌────────┐  │   │
│  │  │ Ingest  │→│ Analyze │→│ Encrypt │→│ Upload │  │   │
│  │  └─────────┘  └─────────┘  └─────────┘  └────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│                              │                              │
│         ┌────────────────────┼────────────────────┐         │
│         ▼                    ▼                    ▼         │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐  │
│  │   Plugin    │      │   Lit/      │      │  Synapse/   │  │
│  │   System    │      │   Arkiv     │      │  Filecoin   │  │
│  └─────────────┘      └─────────────┘      └─────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## License

MIT

## Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## Support

- 📖 [Documentation](docs/)
- 🐛 [Issue Tracker](https://github.com/haven/haven-cli/issues)
- 💬 [Discussions](https://github.com/haven/haven-cli/discussions)
