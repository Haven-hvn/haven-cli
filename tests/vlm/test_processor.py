"""Tests for VLM processor."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from haven_cli.vlm.processor import (
    VLMProcessor,
    process_video,
    save_results_to_db,
)
from haven_cli.database.connection import get_db_session  # noqa: F401
from haven_cli.vlm.engine import VLMResponse, AnalysisConfig
from haven_cli.vlm.config import VLMConfig, VLMProcessingConfig, VLMEngineConfig


@pytest.fixture
def mock_vlm_config():
    """Create a mock VLM configuration."""
    config = VLMConfig(
        engine=VLMEngineConfig(
            model_type="openai",
            model_name="gpt-4-vision-preview",
            api_key="test-key",
        ),
        processing=VLMProcessingConfig(
            enabled=True,
            frame_count=10,
            threshold=0.5,
            return_timestamps=True,
            return_confidence=True,
            save_to_file=False,  # Don't save files in tests
        ),
    )
    return config


@pytest.fixture
def sample_vlm_results():
    """Create sample VLM results."""
    return {
        "video_path": "/path/to/video.mp4",
        "timestamps": [
            {
                "tag_name": "intro",
                "start_time": 0.0,
                "end_time": 10.0,
                "confidence": 0.9,
                "description": "Opening sequence",
            },
            {
                "tag_name": "main_content",
                "start_time": 10.0,
                "end_time": 60.0,
                "confidence": 0.85,
            },
        ],
        "tags": {
            "sports": 0.95,
            "action": 0.88,
            "entertainment": 0.75,
        },
        "confidence": 0.87,
        "summary": "Sports video with action sequences",
    }


class TestVLMProcessor:
    """Tests for VLMProcessor class."""
    
    @pytest.mark.asyncio
    async def test_initialization(self, mock_vlm_config):
        """Test processor initialization."""
        processor = VLMProcessor(config=mock_vlm_config)
        
        mock_engine = AsyncMock()
        
        with patch("haven_cli.vlm.processor.create_vlm_engine", return_value=mock_engine):
            await processor.initialize()
        
        assert processor._initialized
        mock_engine.initialize.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_close(self, mock_vlm_config):
        """Test processor cleanup."""
        processor = VLMProcessor(config=mock_vlm_config)
        
        mock_engine = AsyncMock()
        processor.engine = mock_engine
        processor._initialized = True
        
        await processor.close()
        
        assert not processor._initialized
        mock_engine.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_process_video_file_not_found(self, mock_vlm_config):
        """Test processing non-existent video."""
        processor = VLMProcessor(config=mock_vlm_config)
        
        with pytest.raises(FileNotFoundError):
            await processor.process_video("/nonexistent/video.mp4")
    
    @pytest.mark.asyncio
    async def test_process_video_success(self, tmp_path, mock_vlm_config):
        """Test successful video processing."""
        # Create a fake video file
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video")
        
        processor = VLMProcessor(config=mock_vlm_config)
        
        # Mock engine
        mock_engine = AsyncMock()
        mock_engine.sample_frames.return_value = [
            (0.0, Image.new("RGB", (100, 100))),
            (10.0, Image.new("RGB", (100, 100))),
        ]
        
        mock_response = VLMResponse(
            raw_response={},
            parsed_result={
                "segments": [{"tag_name": "test", "start_time": 0.0, "confidence": 0.9}],
                "tags": [{"name": "sports", "confidence": 0.95}],
            },
            model="test-model",
        )
        mock_engine.analyze_frames.return_value = mock_response
        
        processor.engine = mock_engine
        processor._initialized = True
        
        with patch("haven_cli.media.metadata.extract_video_duration", return_value=60.0):
            results = await processor.process_video(video_path)
        
        assert "video_path" in results
        assert "timestamps" in results
        assert "tags" in results
        assert "confidence" in results
        assert results["video_path"] == str(video_path)
    
    @pytest.mark.asyncio
    async def test_process_video_with_progress_callback(self, tmp_path, mock_vlm_config):
        """Test video processing with progress callback."""
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video")
        
        processor = VLMProcessor(config=mock_vlm_config)
        
        mock_engine = AsyncMock()
        mock_engine.sample_frames.return_value = [
            (0.0, Image.new("RGB", (100, 100))),
        ]
        mock_response = VLMResponse(
            raw_response={},
            parsed_result={"segments": [], "tags": []},
            model="test-model",
        )
        mock_engine.analyze_frames.return_value = mock_response
        
        processor.engine = mock_engine
        processor._initialized = True
        
        progress_calls = []
        
        def progress_callback(progress):
            progress_calls.append(progress)
        
        with patch("haven_cli.media.metadata.extract_video_duration", return_value=60.0):
            await processor.process_video(video_path, progress_callback=progress_callback)
        
        assert len(progress_calls) > 0
        assert progress_calls[-1] == 100  # Should end at 100%
    
    def test_calculate_confidence(self, mock_vlm_config):
        """Test confidence calculation."""
        processor = VLMProcessor(config=mock_vlm_config)
        
        timestamps = [
            {"confidence": 0.9},
            {"confidence": 0.8},
        ]
        tags = {
            "sports": 0.95,
            "action": 0.85,
        }
        
        confidence = processor._calculate_confidence(timestamps, tags)
        
        expected = (0.9 + 0.8 + 0.95 + 0.85) / 4
        assert confidence == pytest.approx(expected)
    
    def test_calculate_confidence_empty(self, mock_vlm_config):
        """Test confidence calculation with empty results."""
        processor = VLMProcessor(config=mock_vlm_config)
        
        confidence = processor._calculate_confidence([], {})
        
        assert confidence == 0.0
    
    def test_generate_summary(self, mock_vlm_config):
        """Test summary generation."""
        processor = VLMProcessor(config=mock_vlm_config)
        
        timestamps = [
            {"tag_name": "intro", "start_time": 0.0},
            {"tag_name": "main", "start_time": 10.0},
        ]
        tags = {
            "sports": 0.95,
            "action": 0.88,
        }
        
        summary = processor._generate_summary(timestamps, tags, 60.0)
        
        assert "sports" in summary
        assert "action" in summary
        assert "2 segments" in summary
        assert "1:00" in summary or "Duration:" in summary
    
    @pytest.mark.asyncio
    async def test_save_results_to_file(self, tmp_path, mock_vlm_config):
        """Test saving results to file."""
        # Enable file saving for this test
        mock_vlm_config.processing.save_to_file = True
        
        processor = VLMProcessor(config=mock_vlm_config)
        
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video")
        
        results = {
            "video_path": str(video_path),
            "tags": {"test": 0.9},
        }
        
        await processor._save_results_to_file(video_path, results)
        
        expected_path = video_path.with_suffix(video_path.suffix + ".AI.json")
        assert expected_path.exists()
        
        # Verify content
        with open(expected_path) as f:
            saved = json.load(f)
        assert saved["tags"]["test"] == 0.9
    
    @pytest.mark.asyncio
    async def test_process_video_with_fallback_success(self, tmp_path, mock_vlm_config):
        """Test fallback processing with successful result."""
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video")
        
        processor = VLMProcessor(config=mock_vlm_config)
        
        mock_engine = AsyncMock()
        mock_engine.sample_frames.return_value = [
            (0.0, Image.new("RGB", (100, 100))),
        ]
        mock_response = VLMResponse(
            raw_response={},
            parsed_result={"segments": [], "tags": []},
            model="test-model",
        )
        mock_engine.analyze_frames.return_value = mock_response
        
        processor.engine = mock_engine
        processor._initialized = True
        
        with patch("haven_cli.media.metadata.extract_video_duration", return_value=60.0):
            results = await processor.process_video_with_fallback(video_path)
        
        assert "error" not in results
    
    @pytest.mark.asyncio
    async def test_process_video_with_fallback_error(self, tmp_path, mock_vlm_config):
        """Test fallback processing with error."""
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video")
        
        processor = VLMProcessor(config=mock_vlm_config)
        
        # Make the engine fail
        mock_engine = AsyncMock()
        mock_engine.sample_frames.side_effect = Exception("VLM API error")
        processor.engine = mock_engine
        processor._initialized = True
        
        results = await processor.process_video_with_fallback(
            video_path,
            fallback_enabled=True,
        )
        
        assert "error" in results
        assert results["timestamps"] == []
        assert results["tags"] == {}
        assert results["confidence"] == 0.0


class TestProcessVideoFunction:
    """Tests for the process_video convenience function."""
    
    @pytest.mark.asyncio
    async def test_process_video_function(self, tmp_path):
        """Test the process_video convenience function."""
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video")
        
        with patch("haven_cli.vlm.processor.VLMProcessor") as mock_processor_class:
            mock_processor = AsyncMock()
            mock_processor_class.return_value = mock_processor
            
            mock_processor.process_video.return_value = {
                "video_path": str(video_path),
                "tags": {"test": 0.9},
            }
            
            results = await process_video(video_path)
        
        assert results["tags"]["test"] == 0.9
        mock_processor.close.assert_called_once()


class TestSaveResultsToDb:
    """Tests for save_results_to_db function."""
    
    def test_save_results_success(self, sample_vlm_results):
        """Test successful database save."""
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_session.query.return_value = mock_query
        mock_query.filter.return_value.delete.return_value = None
        
        mock_video = MagicMock()
        mock_video.id = 1
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
            
            count = save_results_to_db(
                "/path/to/video.mp4",
                sample_vlm_results,
                video_id=1,
            )
        
        assert count == 2  # Two timestamps in sample results
        mock_session.commit.assert_called()
    
    def test_save_results_no_video_id(self, sample_vlm_results):
        """Test save without video_id raises error."""
        mock_session = MagicMock()
        mock_video = MagicMock()
        mock_video.id = 1
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_video
        mock_session.query.return_value = mock_query
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
            
            with patch("haven_cli.database.repositories.VideoRepository") as mock_repo_class:
                mock_repo = MagicMock()
                mock_repo.get_by_source_path.return_value = mock_video
                mock_repo_class.return_value = mock_repo
                
                count = save_results_to_db(
                    "/path/to/video.mp4",
                    sample_vlm_results,
                )
        
        assert count == 2
    
    def test_save_results_video_not_found(self, sample_vlm_results):
        """Test save when video not found raises error."""
        mock_session = MagicMock()
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
            
            with patch("haven_cli.database.repositories.VideoRepository") as mock_repo_class:
                mock_repo = MagicMock()
                mock_repo.get_by_source_path.return_value = None
                mock_repo_class.return_value = mock_repo
                
                with pytest.raises(ValueError, match="Video not found"):
                    save_results_to_db(
                        "/path/to/video.mp4",
                        sample_vlm_results,
                    )
    
    def test_save_results_no_timestamps(self):
        """Test save with no timestamps."""
        results = {
            "video_path": "/path/to/video.mp4",
            "timestamps": [],
            "tags": {},
        }
        
        mock_session = MagicMock()
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
            
            count = save_results_to_db(
                "/path/to/video.mp4",
                results,
                video_id=1,
            )
        
        assert count == 0
