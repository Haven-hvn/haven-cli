"""Tests for VLM engines."""

import base64
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from haven_cli.vlm.engine import (
    AnalysisConfig,
    GeminiVLMEngine,
    LocalVLMEngine,
    OpenAIVLMEngine,
    VLMEngine,
    VLMResponse,
    create_vlm_engine,
)


@pytest.fixture
def sample_image():
    """Create a sample PIL Image for testing."""
    return Image.new("RGB", (100, 100), color="red")


@pytest.fixture
def sample_frames():
    """Create sample frames for testing."""
    return [
        Image.new("RGB", (100, 100), color="red"),
        Image.new("RGB", (100, 100), color="green"),
        Image.new("RGB", (100, 100), color="blue"),
    ]


class TestVLMResponse:
    """Tests for VLMResponse dataclass."""
    
    def test_vlm_response_creation(self):
        """Test creating a VLMResponse."""
        response = VLMResponse(
            raw_response={"choices": [{"message": {"content": "test"}}]},
            parsed_result={"result": "test"},
            model="gpt-4-vision",
            tokens_used=100,
            processing_time_ms=500.0,
        )
        
        assert response.model == "gpt-4-vision"
        assert response.tokens_used == 100
        assert response.processing_time_ms == 500.0


class TestAnalysisConfig:
    """Tests for AnalysisConfig dataclass."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = AnalysisConfig()
        
        assert config.frame_count == 20
        assert config.frame_interval == 2.0
        assert config.threshold == 0.5
        assert config.return_timestamps is True
        assert config.return_confidence is True
        assert config.max_tokens == 4096
        assert config.timeout == 120.0
    
    def test_custom_config(self):
        """Test custom configuration values."""
        config = AnalysisConfig(
            frame_count=10,
            threshold=0.7,
            timeout=60.0,
        )
        
        assert config.frame_count == 10
        assert config.threshold == 0.7
        assert config.timeout == 60.0


class TestOpenAIVLMEngine:
    """Tests for OpenAI VLM Engine."""
    
    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test engine initialization."""
        engine = OpenAIVLMEngine(
            api_key="test-key",
            model="gpt-4-vision-preview",
        )
        
        assert not engine._initialized
        
        with patch("httpx.AsyncClient") as mock_client:
            await engine.initialize()
            
            assert engine._initialized
            assert engine._client is not None
            mock_client.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_initialization_without_api_key(self):
        """Test that initialization fails without API key."""
        engine = OpenAIVLMEngine(api_key="")
        
        with pytest.raises(ValueError, match="API key is required"):
            await engine.initialize()
    
    @pytest.mark.asyncio
    async def test_analyze_frames(self, sample_frames):
        """Test analyzing frames."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": '{"tags": [{"name": "test", "confidence": 0.9}]}'}}
            ],
            "usage": {"total_tokens": 150},
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        
        with patch.object(engine, "_client", mock_client):
            with patch.object(engine, "_initialized", True):
                response = await engine.analyze_frames(
                    sample_frames,
                    "Analyze these frames",
                )
        
        assert isinstance(response, VLMResponse)
        assert response.model == "gpt-4-vision-preview"
        assert response.tokens_used == 150
        assert "tags" in response.parsed_result
    
    def test_encode_frame_to_base64(self, sample_image):
        """Test encoding frame to base64."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        encoded = engine._encode_frame_to_base64(sample_image)
        
        assert encoded.startswith("data:image/jpeg;base64,")
        # Verify it's valid base64
        base64_part = encoded.split(",")[1]
        decoded = base64.b64decode(base64_part)
        assert len(decoded) > 0
    
    def test_parse_response_content_json(self):
        """Test parsing JSON response content."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        content = '{"tags": [{"name": "test", "confidence": 0.9}]}'
        result = engine._parse_response_content(content)
        
        assert result == {"tags": [{"name": "test", "confidence": 0.9}]}
    
    def test_parse_response_content_markdown(self):
        """Test parsing markdown-wrapped JSON."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        content = '''```json
{"tags": [{"name": "test", "confidence": 0.9}]}
```'''
        result = engine._parse_response_content(content)
        
        assert result == {"tags": [{"name": "test", "confidence": 0.9}]}
    
    def test_parse_response_content_raw_text(self):
        """Test parsing non-JSON content returns raw_text."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        content = "This is plain text"
        result = engine._parse_response_content(content)
        
        assert result == {"raw_text": "This is plain text"}
    
    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing the engine."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        mock_client = AsyncMock()
        engine._client = mock_client
        engine._initialized = True
        
        await engine.close()
        
        assert not engine._initialized
        assert engine._client is None
        mock_client.aclose.assert_called_once()


class TestGeminiVLMEngine:
    """Tests for Gemini VLM Engine."""
    
    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test engine initialization."""
        engine = GeminiVLMEngine(
            api_key="test-key",
            model="gemini-pro-vision",
        )
        
        with patch("httpx.AsyncClient"):
            await engine.initialize()
            
            assert engine._initialized
    
    @pytest.mark.asyncio
    async def test_analyze_frames(self, sample_frames):
        """Test analyzing frames with Gemini."""
        engine = GeminiVLMEngine(api_key="test-key")
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": '{"tags": [{"name": "test"}]}'}]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 200},
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        
        with patch.object(engine, "_client", mock_client):
            with patch.object(engine, "_initialized", True):
                response = await engine.analyze_frames(
                    sample_frames,
                    "Analyze these frames",
                )
        
        assert isinstance(response, VLMResponse)
        assert response.model == "gemini-pro-vision"
        assert response.tokens_used == 200


class TestLocalVLMEngine:
    """Tests for Local VLM Engine."""
    
    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test engine initialization."""
        engine = LocalVLMEngine(
            base_url="http://localhost:8000/v1",
            model="local-model",
        )
        
        with patch("httpx.AsyncClient"):
            await engine.initialize()
            
            assert engine._initialized
    
    @pytest.mark.asyncio
    async def test_analyze_frames(self, sample_frames):
        """Test analyzing frames with local model."""
        engine = LocalVLMEngine(base_url="http://localhost:8000/v1")
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": '{"tags": [{"name": "local_test"}]}'}}
            ],
            "usage": {"total_tokens": 100},
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        
        with patch.object(engine, "_client", mock_client):
            with patch.object(engine, "_initialized", True):
                response = await engine.analyze_frames(
                    sample_frames,
                    "Analyze these frames",
                )
        
        assert isinstance(response, VLMResponse)
        assert response.tokens_used == 100


class TestCreateVLMEngine:
    """Tests for engine factory function."""
    
    def test_create_openai_engine(self):
        """Test creating OpenAI engine."""
        engine = create_vlm_engine(
            model="gpt-4-vision-preview",
            api_key="test-key",
        )
        
        assert isinstance(engine, OpenAIVLMEngine)
        assert engine.model == "gpt-4-vision-preview"
    
    def test_create_gemini_engine(self):
        """Test creating Gemini engine."""
        engine = create_vlm_engine(
            model="gemini-pro-vision",
            api_key="test-key",
        )
        
        assert isinstance(engine, GeminiVLMEngine)
    
    def test_create_local_engine(self):
        """Test creating local engine."""
        engine = create_vlm_engine(
            model="llava-model",
            base_url="http://localhost:8000/v1",
        )
        
        assert isinstance(engine, LocalVLMEngine)
    
    def test_create_default_engine(self):
        """Test default engine creation."""
        engine = create_vlm_engine(
            model="unknown-model",
            base_url="http://custom:8000/v1",
        )
        
        assert isinstance(engine, LocalVLMEngine)


class TestFrameSampling:
    """Tests for frame sampling functionality."""
    
    def test_calculate_uniform_timestamps(self):
        """Test uniform timestamp calculation."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        # Test with 60 second video, 10 frames
        timestamps = engine._calculate_uniform_timestamps(60.0, 10)
        
        assert len(timestamps) == 10
        # First timestamp should be after 5% skip (3 seconds)
        assert timestamps[0] > 3.0
        # Last timestamp should be before 95% (57 seconds)
        assert timestamps[-1] < 57.0
    
    def test_calculate_uniform_timestamps_single_frame(self):
        """Test with single frame."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        timestamps = engine._calculate_uniform_timestamps(60.0, 1)
        
        assert len(timestamps) == 1
        assert timestamps[0] == 30.0  # Middle of video
    
    @pytest.mark.asyncio
    async def test_sample_frames(self, tmp_path):
        """Test frame sampling from video."""
        engine = OpenAIVLMEngine(api_key="test-key")
        
        # Create a mock video file (just for path existence check)
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video content")
        
        # Mock the metadata extraction at the module level where it's imported
        with patch(
            "haven_cli.media.metadata.extract_video_duration",
            return_value=60.0,
        ):
            with patch(
                "haven_cli.media.frames.extract_frames",
                return_value=[Image.new("RGB", (100, 100))] * 5,
            ):
                frames = await engine.sample_frames(video_path, count=5)
        
        assert len(frames) == 5
        assert all(isinstance(ts, float) for ts, _ in frames)
        assert all(isinstance(frame, Image.Image) for _, frame in frames)
