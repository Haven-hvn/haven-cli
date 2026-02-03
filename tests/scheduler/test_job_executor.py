"""Tests for the job executor."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from uuid import UUID, uuid4

import pytest

from haven_cli.scheduler.job_executor import (
    ArchiveResult,
    BatchJobExecutor,
    JobExecutor,
    MediaSource,
)
from haven_cli.scheduler.job_scheduler import (
    JobExecutionResult,
    OnSuccessAction,
    RecurringJob,
)
from haven_cli.scheduler.source_tracker import SourceTracker
from haven_cli.plugins.base import (
    ArchiverPlugin,
    ArchiveResult as PluginArchiveResult,
    MediaSource as PluginMediaSource,
    PluginInfo,
    PluginCapability,
)


class MockPlugin(ArchiverPlugin):
    """Mock plugin for testing."""
    
    def __init__(self, name: str = "MockPlugin", config: Optional[Dict] = None) -> None:
        super().__init__(config)
        self._name = name
        self._health_check_result = True
        self._discover_result: List[PluginMediaSource] = []
        self._archive_result: PluginArchiveResult = PluginArchiveResult(success=True)
        self.discover_called = False
        self.archive_called = False
        self.initialize_called = False
        self._should_fail_health = False
        self._should_fail_discover = False
        self._should_fail_archive = False
    
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name=self._name,
            display_name=f"Mock {self._name}",
            capabilities=[
                PluginCapability.DISCOVER,
                PluginCapability.ARCHIVE,
            ],
        )
    
    async def initialize(self) -> None:
        self.initialize_called = True
        self._initialized = True
    
    async def health_check(self) -> bool:
        if self._should_fail_health:
            return False
        return self._health_check_result
    
    async def discover_sources(self) -> List[PluginMediaSource]:
        self.discover_called = True
        if self._should_fail_discover:
            raise RuntimeError("Discover failed")
        return self._discover_result
    
    async def archive(self, source: PluginMediaSource) -> PluginArchiveResult:
        self.archive_called = True
        if self._should_fail_archive:
            raise RuntimeError("Archive failed")
        return self._archive_result
    
    def set_discover_result(self, sources: List[PluginMediaSource]) -> None:
        self._discover_result = sources
    
    def set_archive_result(self, result: PluginArchiveResult) -> None:
        self._archive_result = result


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def sample_job() -> RecurringJob:
    """Create a sample recurring job."""
    return RecurringJob(
        job_id=uuid4(),
        name="Test Job",
        plugin_name="TestPlugin",
        schedule="0 * * * *",
        on_success=OnSuccessAction.ARCHIVE_NEW,
        enabled=True,
        metadata={"test": "data"},
    )


@pytest.fixture
def mock_plugin() -> MockPlugin:
    """Create a mock plugin."""
    return MockPlugin("TestPlugin")


@pytest.fixture
def executor(temp_data_dir) -> JobExecutor:
    """Create a job executor with temp data dir."""
    config = {"data_dir": str(temp_data_dir)}
    return JobExecutor(config=config)


class TestMediaSource:
    """Tests for MediaSource dataclass."""
    
    def test_media_source_creation(self) -> None:
        """Test creating a media source."""
        source = MediaSource(
            source_id="vid_123",
            media_type="youtube",
            uri="https://youtube.com/watch?v=123",
        )
        
        assert source.source_id == "vid_123"
        assert source.media_type == "youtube"
        assert source.uri == "https://youtube.com/watch?v=123"
        assert source.priority == "medium"  # Default
        assert source.metadata == {}
    
    def test_media_source_with_metadata(self) -> None:
        """Test media source with metadata."""
        source = MediaSource(
            source_id="vid_456",
            media_type="youtube",
            uri="https://youtube.com/watch?v=456",
            priority="high",
            metadata={"title": "Test Video", "duration": 120},
        )
        
        assert source.priority == "high"
        assert source.metadata["title"] == "Test Video"
        assert source.metadata["duration"] == 120


class TestArchiveResult:
    """Tests for ArchiveResult dataclass."""
    
    def test_archive_result_success(self) -> None:
        """Test successful archive result."""
        result = ArchiveResult(
            success=True,
            output_path="/path/to/video.mp4",
            file_size=1024000,
            duration=300,
        )
        
        assert result.success is True
        assert result.output_path == "/path/to/video.mp4"
        assert result.file_size == 1024000
        assert result.duration == 300
    
    def test_archive_result_failure(self) -> None:
        """Test failed archive result."""
        result = ArchiveResult(
            success=False,
            error="Download failed",
        )
        
        assert result.success is False
        assert result.error == "Download failed"


class TestSourceTracker:
    """Tests for SourceTracker."""
    
    def test_tracker_load_empty(self, temp_data_dir: Path) -> None:
        """Test loading sources when no cache exists."""
        tracker = SourceTracker(temp_data_dir)
        known = tracker.load("TestPlugin")
        
        assert known == set()
    
    def test_tracker_add_and_load(self, temp_data_dir: Path) -> None:
        """Test adding and loading sources."""
        tracker = SourceTracker(temp_data_dir)
        
        tracker.add("TestPlugin", "source_1")
        tracker.add("TestPlugin", "source_2")
        
        known = tracker.load("TestPlugin")
        
        assert "source_1" in known
        assert "source_2" in known
        assert "source_3" not in known
    
    def test_tracker_persistence(self, temp_data_dir: Path) -> None:
        """Test that sources are persisted to disk."""
        tracker1 = SourceTracker(temp_data_dir)
        tracker1.add("TestPlugin", "source_1")
        
        # Create new tracker instance pointing to same directory
        tracker2 = SourceTracker(temp_data_dir)
        known = tracker2.load("TestPlugin")
        
        assert "source_1" in known
    
    def test_tracker_is_known(self, temp_data_dir: Path) -> None:
        """Test is_known method."""
        tracker = SourceTracker(temp_data_dir)
        
        tracker.add("TestPlugin", "source_1")
        
        assert tracker.is_known("TestPlugin", "source_1") is True
        assert tracker.is_known("TestPlugin", "source_2") is False
        assert tracker.is_known("OtherPlugin", "source_1") is False
    
    def test_tracker_add_many(self, temp_data_dir: Path) -> None:
        """Test add_many method."""
        tracker = SourceTracker(temp_data_dir)
        
        tracker.add_many("TestPlugin", {"source_1", "source_2", "source_3"})
        
        known = tracker.load("TestPlugin")
        assert len(known) == 3
    
    def test_tracker_filter_new_sources(self, temp_data_dir: Path) -> None:
        """Test filter_new_sources method."""
        tracker = SourceTracker(temp_data_dir)
        
        tracker.add("TestPlugin", "source_1")
        
        new_sources = tracker.filter_new_sources(
            "TestPlugin",
            ["source_1", "source_2", "source_3"]
        )
        
        assert "source_1" not in new_sources
        assert "source_2" in new_sources
        assert "source_3" in new_sources
    
    def test_tracker_clear(self, temp_data_dir: Path) -> None:
        """Test clear method."""
        tracker = SourceTracker(temp_data_dir)
        
        tracker.add("TestPlugin", "source_1")
        tracker.clear("TestPlugin")
        
        known = tracker.load("TestPlugin")
        assert len(known) == 0
    
    def test_tracker_get_stats(self, temp_data_dir: Path) -> None:
        """Test get_stats method."""
        tracker = SourceTracker(temp_data_dir)
        
        tracker.add("TestPlugin", "source_1")
        tracker.add("TestPlugin", "source_2")
        
        stats = tracker.get_stats("TestPlugin")
        assert stats["known_count"] == 2


class TestJobExecutorInitialization:
    """Tests for JobExecutor initialization."""
    
    def test_executor_creation(self) -> None:
        """Test creating a job executor."""
        executor = JobExecutor()
        
        assert executor._pipeline_manager is None
        assert executor._config == {}
    
    def test_executor_with_config(self, temp_data_dir: Path) -> None:
        """Test executor with custom config."""
        config = {
            "data_dir": str(temp_data_dir),
            "max_concurrent_archives": 5,
        }
        executor = JobExecutor(config=config)
        
        assert executor._config["max_concurrent_archives"] == 5
        assert executor._max_concurrent_archives == 5


class TestJobExecutorGetPlugin:
    """Tests for _get_plugin method."""
    
    @pytest.mark.asyncio
    async def test_get_plugin_from_manager(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test getting plugin from manager."""
        with patch("haven_cli.plugins.manager.PluginManager") as MockManager:
            manager = MagicMock()
            manager.get_plugin.return_value = mock_plugin
            MockManager.return_value = manager
            
            plugin = await executor._get_plugin("TestPlugin")
            
            assert plugin is mock_plugin
            assert mock_plugin.initialize_called is True
    
    @pytest.mark.asyncio
    async def test_get_plugin_not_found(self, executor: JobExecutor) -> None:
        """Test getting non-existent plugin."""
        with patch("haven_cli.plugins.manager.PluginManager") as MockManager:
            manager = MagicMock()
            manager.get_plugin.return_value = None
            MockManager.return_value = manager
            
            with patch("haven_cli.plugins.registry.get_registry") as mock_get_registry:
                registry = MagicMock()
                registry.load.return_value = None
                mock_get_registry.return_value = registry
                
                plugin = await executor._get_plugin("NonExistentPlugin")
                
                assert plugin is None
    
    @pytest.mark.asyncio
    async def test_get_plugin_from_registry(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test loading plugin from registry."""
        # Create a simple mock manager that tracks registrations
        registered_plugins: dict = {}
        
        class SimpleMockManager:
            def get_plugin(self, name: str):
                return registered_plugins.get(name)
            
            def register(self, plugin_class):
                plugin_instance = plugin_class()
                registered_plugins[plugin_instance.name] = plugin_instance
        
        with patch("haven_cli.plugins.manager.PluginManager") as MockManager:
            MockManager.return_value = SimpleMockManager()
            
            with patch("haven_cli.plugins.registry.get_registry") as mock_get_registry:
                registry = MagicMock()
                registry.load.return_value = MockPlugin
                mock_get_registry.return_value = registry
                
                # Use "MockPlugin" as the name since that's what MockPlugin.info.name returns
                plugin = await executor._get_plugin("MockPlugin")
                
                assert plugin is not None
                assert isinstance(plugin, MockPlugin)
    
    @pytest.mark.asyncio
    async def test_get_plugin_initialize_failure(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test handling of plugin initialization failure."""
        mock_plugin._initialized = False
        
        async def failing_init():
            raise RuntimeError("Init failed")
        
        mock_plugin.initialize = failing_init
        
        with patch("haven_cli.plugins.manager.PluginManager") as MockManager:
            manager = MagicMock()
            manager.get_plugin.return_value = mock_plugin
            MockManager.return_value = manager
            
            plugin = await executor._get_plugin("TestPlugin")
            
            assert plugin is None


class TestJobExecutorDiscoverSources:
    """Tests for _discover_sources method."""
    
    @pytest.mark.asyncio
    async def test_discover_sources_success(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test successful source discovery."""
        mock_plugin.set_discover_result([
            PluginMediaSource(
                source_id="vid_1",
                media_type="youtube",
                uri="https://youtube.com/watch?v=1",
            ),
        ])
        
        sources = await executor._discover_sources(mock_plugin, "TestPlugin")
        
        assert len(sources) == 1
        assert sources[0].source_id == "vid_1"
        assert sources[0].media_type == "youtube"
    
    @pytest.mark.asyncio
    async def test_discover_sources_health_check_failure(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test discovery when health check fails."""
        mock_plugin._should_fail_health = True
        
        with pytest.raises(RuntimeError) as exc_info:
            await executor._discover_sources(mock_plugin, "TestPlugin")
        
        assert "health check failed" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_discover_sources_empty_result(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test discovery with no sources found."""
        mock_plugin.set_discover_result([])
        
        sources = await executor._discover_sources(mock_plugin, "TestPlugin")
        
        assert sources == []


class TestJobExecutorFilterSources:
    """Tests for _filter_sources method."""
    
    def test_filter_log_only(self, executor: JobExecutor) -> None:
        """Test LOG_ONLY action returns empty list."""
        sources = [
            MediaSource(source_id="1", media_type="test", uri="uri1"),
            MediaSource(source_id="2", media_type="test", uri="uri2"),
        ]
        
        filtered = executor._filter_sources(
            sources, "TestPlugin", OnSuccessAction.LOG_ONLY
        )
        
        assert filtered == []
    
    def test_filter_archive_all(self, executor: JobExecutor) -> None:
        """Test ARCHIVE_ALL action returns all sources."""
        sources = [
            MediaSource(source_id="1", media_type="test", uri="uri1"),
            MediaSource(source_id="2", media_type="test", uri="uri2"),
        ]
        
        filtered = executor._filter_sources(
            sources, "TestPlugin", OnSuccessAction.ARCHIVE_ALL
        )
        
        assert len(filtered) == 2
    
    def test_filter_archive_new(self, executor: JobExecutor) -> None:
        """Test ARCHIVE_NEW action filters known sources."""
        # Mark one source as known
        executor._source_tracker.add("TestPlugin", "1")
        
        sources = [
            MediaSource(source_id="1", media_type="test", uri="uri1"),
            MediaSource(source_id="2", media_type="test", uri="uri2"),
            MediaSource(source_id="3", media_type="test", uri="uri3"),
        ]
        
        filtered = executor._filter_sources(
            sources, "TestPlugin", OnSuccessAction.ARCHIVE_NEW
        )
        
        # Source "1" should be filtered out
        assert len(filtered) == 2
        assert all(s.source_id != "1" for s in filtered)


class TestJobExecutorArchiveSource:
    """Tests for _archive_source method."""
    
    @pytest.mark.asyncio
    async def test_archive_source_success(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test successful archive."""
        mock_plugin.set_archive_result(
            PluginArchiveResult(
                success=True,
                output_path="/path/to/video.mp4",
                file_size=1024000,
                duration=300,
            )
        )
        
        source = MediaSource(
            source_id="vid_1",
            media_type="youtube",
            uri="https://youtube.com/watch?v=1",
        )
        
        result = await executor._archive_source(mock_plugin, source)
        
        assert result.success is True
        assert result.output_path == "/path/to/video.mp4"
        assert result.file_size == 1024000
        assert result.duration == 300
    
    @pytest.mark.asyncio
    async def test_archive_source_failure(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test failed archive."""
        mock_plugin.set_archive_result(
            PluginArchiveResult(
                success=False,
                error="Download failed",
            )
        )
        
        source = MediaSource(
            source_id="vid_1",
            media_type="youtube",
            uri="https://youtube.com/watch?v=1",
        )
        
        result = await executor._archive_source(mock_plugin, source)
        
        assert result.success is False
        assert result.error == "Download failed"
    
    @pytest.mark.asyncio
    async def test_archive_source_exception(
        self, executor: JobExecutor, mock_plugin: MockPlugin
    ) -> None:
        """Test archive with exception."""
        mock_plugin._should_fail_archive = True
        
        source = MediaSource(
            source_id="vid_1",
            media_type="youtube",
            uri="https://youtube.com/watch?v=1",
        )
        
        result = await executor._archive_source(mock_plugin, source)
        
        assert result.success is False
        assert "Archive failed" in result.error


class TestJobExecutorEnqueueToPipeline:
    """Tests for _enqueue_to_pipeline method."""
    
    @pytest.mark.asyncio
    async def test_enqueue_no_pipeline_manager(self, executor: JobExecutor) -> None:
        """Test enqueue when no pipeline manager configured."""
        job = RecurringJob(
            job_id=uuid4(),
            name="Test Job",
            plugin_name="TestPlugin",
        )
        source = MediaSource(
            source_id="vid_1",
            media_type="youtube",
            uri="https://youtube.com/watch?v=1",
            metadata={"title": "Test"},
        )
        
        # Should not raise any errors
        await executor._enqueue_to_pipeline("/path/to/video.mp4", job, source)
    
    @pytest.mark.asyncio
    async def test_enqueue_with_pipeline_manager(
        self, executor: JobExecutor, temp_data_dir: Path
    ) -> None:
        """Test enqueue with pipeline manager."""
        mock_pipeline = AsyncMock()
        executor._pipeline_manager = mock_pipeline
        
        job = RecurringJob(
            job_id=uuid4(),
            name="Test Job",
            plugin_name="TestPlugin",
            metadata={"job_key": "job_value"},
        )
        source = MediaSource(
            source_id="vid_1",
            media_type="youtube",
            uri="https://youtube.com/watch?v=1",
            metadata={"source_key": "source_value"},
        )
        
        await executor._enqueue_to_pipeline("/path/to/video.mp4", job, source)
        
        # Wait for the async task to complete
        await asyncio.sleep(0.1)
        
        # Verify pipeline was called
        assert mock_pipeline.process.called


class TestJobExecutorExecute:
    """Tests for execute method."""
    
    @pytest.mark.asyncio
    async def test_execute_plugin_not_found(
        self, executor: JobExecutor, sample_job: RecurringJob
    ) -> None:
        """Test execution when plugin is not found."""
        with patch.object(executor, "_get_plugin", return_value=None):
            result = await executor.execute(sample_job)
            
            assert result.success is False
            assert "Plugin not found" in result.error
    
    @pytest.mark.asyncio
    async def test_execute_no_sources_found(
        self, executor: JobExecutor, sample_job: RecurringJob, mock_plugin: MockPlugin
    ) -> None:
        """Test execution when no sources are found."""
        mock_plugin.set_discover_result([])
        
        with patch.object(executor, "_get_plugin", return_value=mock_plugin):
            result = await executor.execute(sample_job)
            
            assert result.success is True
            assert result.sources_found == 0
            assert result.sources_archived == 0
    
    @pytest.mark.asyncio
    async def test_execute_successful_archiving(
        self, executor: JobExecutor, sample_job: RecurringJob, mock_plugin: MockPlugin
    ) -> None:
        """Test successful archiving of sources."""
        mock_plugin.set_discover_result([
            PluginMediaSource(
                source_id="vid_1",
                media_type="youtube",
                uri="https://youtube.com/watch?v=1",
            ),
        ])
        mock_plugin.set_archive_result(
            PluginArchiveResult(
                success=True,
                output_path="/path/to/video.mp4",
                file_size=1024000,
            )
        )
        
        with patch.object(executor, "_get_plugin", return_value=mock_plugin):
            with patch.object(executor, "_enqueue_to_pipeline") as mock_enqueue:
                with patch.object(executor, "_save_execution") as mock_save:
                    result = await executor.execute(sample_job)
                    
                    assert result.success is True
                    assert result.sources_found == 1
                    assert result.sources_archived == 1
                    assert mock_enqueue.called
    
    @pytest.mark.asyncio
    async def test_execute_log_only_action(
        self, executor: JobExecutor, sample_job: RecurringJob, mock_plugin: MockPlugin
    ) -> None:
        """Test LOG_ONLY action doesn't archive."""
        sample_job.on_success = OnSuccessAction.LOG_ONLY
        
        mock_plugin.set_discover_result([
            PluginMediaSource(
                source_id="vid_1",
                media_type="youtube",
                uri="https://youtube.com/watch?v=1",
            ),
        ])
        
        with patch.object(executor, "_get_plugin", return_value=mock_plugin):
            with patch.object(executor, "_archive_source") as mock_archive:
                result = await executor.execute(sample_job)
                
                assert result.success is True
                assert result.sources_found == 1
                assert result.sources_archived == 0
                assert not mock_archive.called
    
    @pytest.mark.asyncio
    async def test_execute_with_archive_failures(
        self, executor: JobExecutor, sample_job: RecurringJob, mock_plugin: MockPlugin
    ) -> None:
        """Test handling of partial archive failures."""
        mock_plugin.set_discover_result([
            PluginMediaSource(source_id="vid_1", media_type="youtube", uri="uri1"),
            PluginMediaSource(source_id="vid_2", media_type="youtube", uri="uri2"),
        ])
        
        # Make archive fail for all sources
        mock_plugin._should_fail_archive = True
        
        with patch.object(executor, "_get_plugin", return_value=mock_plugin):
            result = await executor.execute(sample_job)
            
            assert result.success is True  # Job itself succeeded
            assert result.sources_found == 2
            assert result.sources_archived == 0  # But none archived
    
    @pytest.mark.asyncio
    async def test_execute_exception_handling(
        self, executor: JobExecutor, sample_job: RecurringJob
    ) -> None:
        """Test handling of exceptions during execution."""
        with patch.object(executor, "_get_plugin", side_effect=RuntimeError("Boom")):
            result = await executor.execute(sample_job)
            
            assert result.success is False
            assert "Boom" in result.error


class TestBatchJobExecutor:
    """Tests for BatchJobExecutor."""
    
    @pytest.mark.asyncio
    async def test_execute_batch(self) -> None:
        """Test executing multiple jobs in batch."""
        batch_executor = BatchJobExecutor(max_concurrent=2)
        
        jobs = [
            RecurringJob(job_id=uuid4(), name="Job 1", plugin_name="Plugin1"),
            RecurringJob(job_id=uuid4(), name="Job 2", plugin_name="Plugin2"),
        ]
        
        with patch.object(JobExecutor, "execute") as mock_execute:
            mock_execute.return_value = JobExecutionResult(
                job_id=uuid4(),
                started_at=datetime.utcnow(),
                success=True,
                sources_found=1,
                sources_archived=1,
            )
            
            results = await batch_executor.execute_batch(jobs)
            
            assert len(results) == 2
            assert all(r.success for r in results)
    
    @pytest.mark.asyncio
    async def test_execute_batch_with_exception(self) -> None:
        """Test batch execution handles exceptions."""
        batch_executor = BatchJobExecutor(max_concurrent=2)
        
        jobs = [
            RecurringJob(job_id=uuid4(), name="Job 1", plugin_name="Plugin1"),
        ]
        
        with patch.object(JobExecutor, "execute", side_effect=RuntimeError("Boom")):
            results = await batch_executor.execute_batch(jobs)
            
            assert len(results) == 1
            assert results[0].success is False
            assert "Boom" in results[0].error


class TestJobExecutorSaveExecution:
    """Tests for _save_execution method."""
    
    @pytest.mark.asyncio
    async def test_save_execution_success(self, executor: JobExecutor) -> None:
        """Test saving execution result."""
        result = JobExecutionResult(
            job_id=uuid4(),
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            success=True,
            sources_found=5,
            sources_archived=3,
        )
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_context = MagicMock()
            mock_context.__enter__ = MagicMock(return_value=mock_session)
            mock_context.__exit__ = MagicMock(return_value=False)
            mock_get_session.return_value = mock_context
            
            await executor._save_execution(result, "TestPlugin")
            
            assert mock_session.add.called
            assert mock_session.commit.called
    
    @pytest.mark.asyncio
    async def test_save_execution_failure(self, executor: JobExecutor) -> None:
        """Test handling of save failure."""
        result = JobExecutionResult(
            job_id=uuid4(),
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            success=True,
            sources_found=5,
            sources_archived=3,
        )
        
        with patch("haven_cli.database.connection.get_db_session", side_effect=Exception("DB Error")):
            # Should not raise exception
            await executor._save_execution(result, "TestPlugin")
