# Python API Reference

Complete reference for Haven CLI's Python API.

## Table of Contents

1. [Configuration API](#configuration-api)
2. [Pipeline API](#pipeline-api)
3. [Plugin API](#plugin-api)
4. [Database API](#database-api)
5. [Scheduler API](#scheduler-api)
6. [JS Runtime API](#js-runtime-api)

---

## Configuration API

### Loading Configuration

```python
from haven_cli.config import load_config, get_config

# Load from default location
config = get_config()

# Load from specific file
config = load_config("/path/to/config.toml")

# Load from Path object
from pathlib import Path
config = load_config(Path("/path/to/config.toml"))
```

### Configuration Object

```python
from haven_cli.config import HavenConfig

# Access configuration sections
print(config.pipeline.vlm_enabled)
print(config.pipeline.vlm_model)
print(config.scheduler.enabled)

# Modify configuration
config.pipeline.vlm_enabled = True
config.pipeline.max_concurrent_videos = 8
```

### Saving Configuration

```python
from haven_cli.config import save_config

# Save to default location
save_config(config)

# Save to specific file
save_config(config, "/path/to/config.toml")
```

### Environment Variables

```python
from haven_cli.config import get_env_config

# Get configuration from environment variables
env_config = get_env_config()

# Environment variables:
# HAVEN_VLM_ENABLED=true
# HAVEN_VLM_MODEL=zai-org/glm-4.6v-flash
# HAVEN_SYNAPSE_API_KEY=...
# etc.
```

### Configuration Validation

```python
from haven_cli.config import validate_config, ValidationError

# Validate configuration
errors = validate_config(config)

for error in errors:
    print(f"{error.severity}: {error.field} - {error.message}")
```

---

## Pipeline API

### Pipeline Manager

```python
from haven_cli.pipeline.manager import PipelineManager
from haven_cli.config import get_config

# Create pipeline manager
config = get_config()
pipeline_manager = PipelineManager(config=config)

# Process a video
from haven_cli.pipeline.context import PipelineContext
from pathlib import Path

context = PipelineContext(
    source_path=Path("/path/to/video.mp4"),
    options={
        "encrypt": True,
        "vlm_enabled": True,
        "arkiv_sync_enabled": True,
    }
)

# Run pipeline
result = await pipeline_manager.process(context)

if result.success:
    print(f"Success! CID: {result.cid}")
else:
    print(f"Failed: {result.error}")
```

### Pipeline Context

```python
from haven_cli.pipeline.context import PipelineContext
from pathlib import Path

context = PipelineContext(
    source_path=Path("/path/to/video.mp4"),
    dataset_id=123,  # Optional
    options={
        "encrypt": True,
        "vlm_enabled": True,
        "upload_enabled": True,
        "arkiv_sync_enabled": True,
    }
)
```

### Pipeline Results

```python
from haven_cli.pipeline.results import PipelineResult

result = await pipeline_manager.process(context)

# Check result
if result.success:
    print(f"CID: {result.cid}")
    print(f"Video ID: {result.video_id}")
    print(f"Encryption metadata: {result.encryption_metadata}")
else:
    print(f"Error: {result.error}")
```

### Pipeline Steps

Individual pipeline steps can be used directly:

#### Ingest Step

```python
from haven_cli.pipeline.steps.ingest_step import IngestStep
from haven_cli.pipeline.context import PipelineContext

step = IngestStep(config)
context = PipelineContext(source_path=Path("/path/to/video.mp4"))

result = await step.execute(context)

if result.success:
    print(f"Video ID: {context.video_id}")
    print(f"pHash: {context.metadata.get('phash')}")
```

#### Analyze Step

```python
from haven_cli.pipeline.steps.analyze_step import AnalyzeStep

step = AnalyzeStep(config)
result = await step.execute(context)

if result.success:
    print(f"Timestamps: {context.metadata.get('timestamps')}")
    print(f"Tags: {context.metadata.get('tags')}")
```

#### Encrypt Step

```python
from haven_cli.pipeline.steps.encrypt_step import EncryptStep

step = EncryptStep(config)
result = await step.execute(context)

if result.success:
    print(f"Encrypted path: {context.encrypted_path}")
    print(f"Encryption metadata: {context.encryption_metadata}")
```

#### Upload Step

```python
from haven_cli.pipeline.steps.upload_step import UploadStep

step = UploadStep(config)
result = await step.execute(context)

if result.success:
    print(f"CID: {context.cid}")
```

#### Sync Step

```python
from haven_cli.pipeline.steps.sync_step import SyncStep

step = SyncStep(config)
result = await step.execute(context)

if result.success:
    print(f"Arkiv entity key: {context.arkiv_entity_key}")
```

---

## Plugin API

### Plugin Manager

```python
from haven_cli.plugins.manager import get_plugin_manager

manager = get_plugin_manager()

# Initialize all plugins
await manager.initialize_all()

# Get a plugin
plugin = manager.get_plugin("YouTubePlugin")

# Get all plugins
plugins = manager.get_all_plugins()

# Register a plugin
from my_plugin import MyCustomPlugin
manager.register(MyCustomPlugin)

# Shutdown all plugins
await manager.shutdown_all()
```

### Plugin Registry

```python
from haven_cli.plugins.registry import get_registry

registry = get_registry()

# Discover all plugins
registry.discover_all()

# Get available plugins
available = registry.available_plugins

# Get plugin info
info = registry.get_info("YouTubePlugin")

# Load a plugin
plugin_class = registry.load("YouTubePlugin")
plugin = plugin_class(config={...})

# Register a plugin
registry.register("my_plugin", MyCustomPlugin)
```

### Creating a Plugin

```python
from haven_cli.plugins.base import (
    ArchiverPlugin, 
    PluginCapability, 
    MediaSource, 
    ArchiveResult
)
from typing import Any, Optional

class MyCustomPlugin(ArchiverPlugin):
    @property
    def name(self) -> str:
        return "MyCustomPlugin"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "My custom archiver plugin"
    
    @property
    def capabilities(self) -> set[PluginCapability]:
        return {PluginCapability.DISCOVER, PluginCapability.ARCHIVE}
    
    async def initialize(self) -> None:
        """Initialize the plugin."""
        self._initialized = True
    
    async def shutdown(self) -> None:
        """Cleanup plugin resources."""
        self._initialized = False
    
    async def health_check(self) -> bool:
        """Check if plugin is healthy."""
        return True
    
    async def discover_sources(self) -> list[MediaSource]:
        """Discover new media sources."""
        sources = []
        # Discovery logic here
        return sources
    
    async def archive(self, source: MediaSource) -> ArchiveResult:
        """Archive a media source."""
        # Archive logic here
        return ArchiveResult(success=True, output_path="/path/to/file")
```

### MediaSource

```python
from haven_cli.plugins.base import MediaSource

source = MediaSource(
    source_id="unique-id",
    media_type="video/youtube",
    uri="https://youtube.com/watch?v=...",
    metadata={
        "title": "Video Title",
        "duration": 120.5,
        "author": "Channel Name",
    }
)

# Access properties
print(source.source_id)
print(source.media_type)
print(source.uri)
print(source.metadata.get("title"))
```

### ArchiveResult

```python
from haven_cli.plugins.base import ArchiveResult

result = ArchiveResult(
    success=True,
    output_path="/path/to/downloaded/video.mp4",
    file_size=12345678,
    duration=120.5,
)

# Or for failures
result = ArchiveResult(
    success=False,
    error="Network timeout",
)
```

---

## Database API

### Database Connection

```python
from haven_cli.database.connection import get_db_session, init_db

# Initialize database (creates tables)
init_db()

# Get a database session
with get_db_session() as session:
    # Use session for queries
    ...
```

### Video Repository

```python
from haven_cli.database.repositories import VideoRepository
from haven_cli.database.connection import get_db_session

with get_db_session() as session:
    repo = VideoRepository(session)
    
    # Create a video
    video = repo.create(
        title="My Video",
        file_path="/path/to/video.mp4",
        file_size=12345678,
        duration=120.5,
        phash="abc123...",
    )
    
    # Get video by ID
    video = repo.get_by_id(video_id)
    
    # Get video by CID
    video = repo.get_by_cid(cid)
    
    # Get video by pHash
    video = repo.get_by_phash(phash)
    
    # Update video
    repo.update(video_id, cid="bafybeig...")
    
    # List all videos
    videos = repo.list_all(limit=100, offset=0)
    
    # Delete video
    repo.delete(video_id)
```

### Video Model

```python
from haven_cli.database.models import Video

video = Video(
    id=1,
    title="Video Title",
    file_path="/path/to/video.mp4",
    file_size=12345678,
    duration=120.5,
    phash="abc123...",
    cid="bafybeig...",
    encrypted=True,
    encryption_metadata={...},
    arkiv_entity_key="key123...",
    metadata={...},
)
```

---

## Scheduler API

### Job Scheduler

```python
from haven_cli.scheduler.job_scheduler import get_scheduler, RecurringJob, OnSuccessAction

scheduler = get_scheduler()

# Create a job
job = RecurringJob(
    name="YouTube Check",
    plugin_name="YouTubePlugin",
    schedule="0 * * * *",  # Every hour
    on_success=OnSuccessAction.ARCHIVE_NEW,
)

# Add job to scheduler
scheduler.add_job(job)

# Get all jobs
jobs = scheduler.jobs

# Get job by ID
job = scheduler.get_job(job_id)

# Pause a job
scheduler.pause_job(job_id)

# Resume a job
scheduler.resume_job(job_id)

# Remove a job
scheduler.remove_job(job_id)

# Run a job immediately
result = await scheduler.run_job_now(job_id)

# Start the scheduler
scheduler.start()

# Shutdown the scheduler
scheduler.shutdown()
```

### Job History

```python
# Get job history
history = scheduler.get_history(limit=50)

# Get history for specific job
history = scheduler.get_history(job_id=job_id, limit=10)

# History record
for record in history:
    print(f"Job: {record.job_id}")
    print(f"Started: {record.started_at}")
    print(f"Completed: {record.completed_at}")
    print(f"Success: {record.success}")
    print(f"Sources found: {record.sources_found}")
    print(f"Sources archived: {record.sources_archived}")
```

### Cron Expressions

```python
from croniter import croniter

# Validate cron expression
try:
    croniter("0 * * * *")
    print("Valid cron expression")
except ValueError as e:
    print(f"Invalid: {e}")

# Get next run time
itr = croniter("0 * * * *")
next_run = itr.get_next(datetime)
```

---

## JS Runtime API

### JS Bridge Manager

```python
from haven_cli.js_runtime.manager import JSBridgeManager, js_call
from haven_cli.js_runtime.protocol import JSRuntimeMethods

# Get singleton instance
manager = JSBridgeManager.get_instance()

# Use as async context manager
async with manager:
    # Call a JS method
    result = await js_call(
        JSRuntimeMethods.SYNAPSE_CONNECT,
        {"endpoint": "https://api.synapse.example.com", "apiKey": "..."},
    )
```

### Available Methods

```python
from haven_cli.js_runtime.protocol import JSRuntimeMethods

# Lit Protocol methods
JSRuntimeMethods.LIT_CONNECT
JSRuntimeMethods.LIT_ENCRYPT
JSRuntimeMethods.LIT_DECRYPT

# Synapse methods
JSRuntimeMethods.SYNAPSE_CONNECT
JSRuntimeMethods.SYNAPSE_UPLOAD
JSRuntimeMethods.SYNAPSE_DOWNLOAD
JSRuntimeMethods.SYNAPSE_GET_STATUS

# Utility methods
JSRuntimeMethods.PING
JSRuntimeMethods.HEALTH_CHECK
```

### Example: Lit Protocol

```python
from haven_cli.js_runtime.manager import JSBridgeManager, js_call
from haven_cli.js_runtime.protocol import JSRuntimeMethods

async def encrypt_file(file_path: str) -> dict:
    manager = JSBridgeManager.get_instance()
    
    async with manager:
        # Connect to Lit
        await js_call(
            JSRuntimeMethods.LIT_CONNECT,
            {"network": "datil-dev"},
        )
        
        # Encrypt file
        result = await js_call(
            JSRuntimeMethods.LIT_ENCRYPT,
            {
                "filePath": file_path,
                "accessControlConditions": [...],
            },
        )
        
        return result
```

### Example: Synapse

```python
from haven_cli.js_runtime.manager import JSBridgeManager, js_call
from haven_cli.js_runtime.protocol import JSRuntimeMethods

async def upload_to_synapse(file_path: str) -> str:
    manager = JSBridgeManager.get_instance()
    
    async with manager:
        # Connect to Synapse
        await js_call(
            JSRuntimeMethods.SYNAPSE_CONNECT,
            {
                "endpoint": "https://api.synapse.example.com",
                "apiKey": "your-api-key",
            },
        )
        
        # Upload file
        result = await js_call(
            JSRuntimeMethods.SYNAPSE_UPLOAD,
            {"filePath": file_path},
        )
        
        return result["cid"]
```

---

## Complete Example

Here's a complete example using multiple APIs:

```python
import asyncio
from pathlib import Path
from haven_cli.config import get_config
from haven_cli.pipeline.manager import PipelineManager
from haven_cli.pipeline.context import PipelineContext
from haven_cli.plugins.manager import get_plugin_manager
from haven_cli.database.connection import get_db_session, init_db
from haven_cli.database.repositories import VideoRepository

async def main():
    # Initialize database
    init_db()
    
    # Load configuration
    config = get_config()
    
    # Initialize plugins
    plugin_manager = get_plugin_manager()
    await plugin_manager.initialize_all()
    
    # Get YouTube plugin
    youtube = plugin_manager.get_plugin("YouTubePlugin")
    
    if youtube and await youtube.health_check():
        print("YouTube plugin is healthy")
        
        # Discover sources
        sources = await youtube.discover_sources()
        print(f"Discovered {len(sources)} sources")
        
        # Archive first source
        if sources:
            result = await youtube.archive(sources[0])
            
            if result.success:
                print(f"Downloaded: {result.output_path}")
                
                # Process through pipeline
                pipeline_manager = PipelineManager(config)
                
                context = PipelineContext(
                    source_path=Path(result.output_path),
                    options={
                        "encrypt": True,
                        "vlm_enabled": True,
                        "upload_enabled": True,
                        "arkiv_sync_enabled": True,
                    }
                )
                
                pipeline_result = await pipeline_manager.process(context)
                
                if pipeline_result.success:
                    print(f"Pipeline complete! CID: {pipeline_result.cid}")
                    
                    # Query database
                    with get_db_session() as session:
                        repo = VideoRepository(session)
                        video = repo.get_by_cid(pipeline_result.cid)
                        
                        if video:
                            print(f"Video in database: {video.title}")
                else:
                    print(f"Pipeline failed: {pipeline_result.error}")
    
    # Cleanup
    await plugin_manager.shutdown_all()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Error Handling

All APIs use exceptions for error handling:

```python
from haven_cli.config import ConfigError
from haven_cli.pipeline import PipelineError
from haven_cli.plugins import PluginError

try:
    config = load_config("/invalid/path")
except ConfigError as e:
    print(f"Config error: {e}")

try:
    result = await pipeline_manager.process(context)
    if not result.success:
        print(f"Pipeline error: {result.error}")
except PipelineError as e:
    print(f"Pipeline exception: {e}")

try:
    await plugin.initialize()
except PluginError as e:
    print(f"Plugin error: {e}")
```
