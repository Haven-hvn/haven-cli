"""Tests for video metadata extraction."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from haven_cli.media.exceptions import FFmpegError, VideoMetadataError
from haven_cli.media.metadata import (
    VideoTechnicalMetadata,
    _add_to_cache,
    _get_ffprobe_cache_key,
    _get_from_cache,
    clear_metadata_cache,
    detect_mime_type,
    extract_video_duration,
    extract_video_metadata,
    get_metadata_cache_info,
)


class TestVideoTechnicalMetadata:
    """Test VideoTechnicalMetadata dataclass."""
    
    def test_creation_with_all_fields(self):
        """Test creating metadata with all fields."""
        meta = VideoTechnicalMetadata(
            duration=120.5,
            width=1920,
            height=1080,
            fps=30.0,
            codec="h264",
            bitrate=5000000,
            audio_codec="aac",
            audio_channels=2,
            container="mp4",
            has_audio=True,
        )
        assert meta.duration == 120.5
        assert meta.width == 1920
        assert meta.height == 1080
        assert meta.fps == 30.0
        assert meta.codec == "h264"
        assert meta.bitrate == 5000000
        assert meta.audio_codec == "aac"
        assert meta.audio_channels == 2
        assert meta.container == "mp4"
        assert meta.has_audio is True
    
    def test_creation_without_audio(self):
        """Test creating metadata for video without audio."""
        meta = VideoTechnicalMetadata(
            duration=60.0,
            width=1280,
            height=720,
            fps=24.0,
            codec="vp9",
            bitrate=2000000,
            audio_codec="",
            audio_channels=0,
            container="webm",
            has_audio=False,
        )
        assert meta.has_audio is False
        assert meta.audio_codec == ""
        assert meta.audio_channels == 0
    
    def test_immutability(self):
        """Test that VideoTechnicalMetadata is frozen/immutable."""
        meta = VideoTechnicalMetadata(
            duration=10.0,
            width=1920,
            height=1080,
            fps=30.0,
            codec="h264",
            bitrate=1000000,
            audio_codec="",
            audio_channels=0,
            container="mp4",
        )
        with pytest.raises(AttributeError):
            meta.duration = 20.0


class TestMimeTypeDetection:
    """Test MIME type detection."""
    
    def test_detect_mp4(self):
        """Test detecting MP4 MIME type."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            path = Path(f.name)
        try:
            mime = detect_mime_type(path, use_magic=False)
            assert mime == "video/mp4"
        finally:
            path.unlink()
    
    def test_detect_mkv(self):
        """Test detecting MKV MIME type."""
        with tempfile.NamedTemporaryFile(suffix=".mkv", delete=False) as f:
            path = Path(f.name)
        try:
            mime = detect_mime_type(path, use_magic=False)
            assert mime == "video/x-matroska"
        finally:
            path.unlink()
    
    def test_detect_webm(self):
        """Test detecting WebM MIME type."""
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            path = Path(f.name)
        try:
            mime = detect_mime_type(path, use_magic=False)
            assert mime == "video/webm"
        finally:
            path.unlink()
    
    def test_detect_avi(self):
        """Test detecting AVI MIME type."""
        with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as f:
            path = Path(f.name)
        try:
            mime = detect_mime_type(path, use_magic=False)
            assert mime == "video/x-msvideo"
        finally:
            path.unlink()
    
    def test_detect_mov(self):
        """Test detecting MOV MIME type."""
        with tempfile.NamedTemporaryFile(suffix=".mov", delete=False) as f:
            path = Path(f.name)
        try:
            mime = detect_mime_type(path, use_magic=False)
            assert mime == "video/quicktime"
        finally:
            path.unlink()
    
    def test_detect_unknown_extension(self):
        """Test fallback for unknown extension."""
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            path = Path(f.name)
        try:
            mime = detect_mime_type(path, use_magic=False)
            # Should return either the actual detected type or fallback to video/mp4
            assert mime in ("video/mp4", "chemical/x-xyz") or mime.startswith("video/")
        finally:
            path.unlink()
    
    def test_detect_nonexistent_file(self):
        """Test handling non-existent file."""
        path = Path("/nonexistent/video.mp4")
        mime = detect_mime_type(path)
        assert mime == "application/octet-stream"
    
    def test_detect_case_insensitive(self):
        """Test that detection is case insensitive."""
        with tempfile.NamedTemporaryFile(suffix=".MP4", delete=False) as f:
            path = Path(f.name)
        try:
            mime = detect_mime_type(path, use_magic=False)
            assert mime == "video/mp4"
        finally:
            path.unlink()


class TestCacheFunctions:
    """Test caching functionality."""
    
    def setup_method(self):
        """Clear cache before each test."""
        clear_metadata_cache()
    
    def teardown_method(self):
        """Clear cache after each test."""
        clear_metadata_cache()
    
    def test_cache_key_generation(self):
        """Test cache key generation."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = Path(f.name)
        try:
            key1 = _get_ffprobe_cache_key(path)
            key2 = _get_ffprobe_cache_key(path)
            assert key1 == key2
            assert path.name in key1
        finally:
            path.unlink()
    
    def test_cache_add_and_get(self):
        """Test adding and retrieving from cache."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)
        try:
            test_data = {"format": {"duration": "120.0"}}
            _add_to_cache(path, test_data)
            retrieved = _get_from_cache(path)
            assert retrieved == test_data
        finally:
            path.unlink()
    
    def test_cache_returns_none_for_uncached(self):
        """Test cache returns None for uncached files."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)
        try:
            result = _get_from_cache(path)
            assert result is None
        finally:
            path.unlink()
    
    def test_clear_cache(self):
        """Test clearing the cache."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)
        try:
            _add_to_cache(path, {"test": "data"})
            clear_metadata_cache()
            result = _get_from_cache(path)
            assert result is None
        finally:
            path.unlink()
    
    def test_get_cache_info(self):
        """Test getting cache information."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)
        try:
            _add_to_cache(path, {"test": "data"})
            info = get_metadata_cache_info()
            assert info["cache_size"] == 1
            assert len(info["cache_keys"]) == 1
        finally:
            path.unlink()


class TestExtractVideoDuration:
    """Test video duration extraction."""
    
    @pytest.fixture
    async def sample_video(self):
        """Create a sample test video using FFmpeg."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            path = Path(f.name)
        
        # Create a 5-second test video
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=5:size=320x240:rate=30",
            "-pix_fmt", "yuv420p",
            str(path),
        ]
        proc = await asyncio.create_subprocess_exec(*cmd)
        await proc.communicate()
        
        yield path
        
        if path.exists():
            path.unlink()
    
    @pytest.mark.asyncio
    async def test_extract_duration_from_valid_video(self):
        """Test extracting duration from a valid video."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            path = Path(f.name)
        
        try:
            # Create a 5-second test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=5:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            duration = await extract_video_duration(path)
            assert 4.5 <= duration <= 5.5  # Allow small tolerance
        finally:
            if path.exists():
                path.unlink()
    
    @pytest.mark.asyncio
    async def test_extract_duration_nonexistent_file(self):
        """Test extracting duration from non-existent file."""
        path = Path("/nonexistent/video.mp4")
        with pytest.raises(FileNotFoundError):
            await extract_video_duration(path)
    
    @pytest.mark.asyncio
    async def test_extract_duration_uses_cache(self):
        """Test that duration extraction uses cache."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            path = Path(f.name)
        
        try:
            # Create test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=3:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            clear_metadata_cache()
            
            # First call should populate cache
            duration1 = await extract_video_duration(path)
            
            # Second call should use cache
            duration2 = await extract_video_duration(path)
            
            assert duration1 == duration2
        finally:
            if path.exists():
                path.unlink()


class TestExtractVideoMetadata:
    """Test comprehensive metadata extraction."""
    
    @pytest.mark.asyncio
    async def test_extract_full_metadata(self):
        """Test extracting all metadata fields."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            path = Path(f.name)
        
        try:
            # Create test video with audio
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=5:size=640x480:rate=30",
                "-f", "lavfi",
                "-i", "sine=frequency=1000:duration=5",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264",
                "-c:a", "aac",
                str(path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            meta = await extract_video_metadata(path)
            
            assert isinstance(meta, VideoTechnicalMetadata)
            assert meta.duration > 0
            assert meta.width == 640
            assert meta.height == 480
            assert meta.fps > 0
            assert meta.codec == "h264"
            assert meta.bitrate > 0
            assert meta.has_audio is True
            assert meta.audio_codec == "aac"
            assert meta.audio_channels == 1
            # FFmpeg reports 'mov' as format for mp4 files
            assert meta.container in ("mp4", "mov")
        finally:
            if path.exists():
                path.unlink()
    
    @pytest.mark.asyncio
    async def test_extract_video_without_audio(self):
        """Test extracting metadata from video without audio."""
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            path = Path(f.name)
        
        try:
            # Create video-only test file
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=3:size=320x240:rate=30",
                "-c:v", "libvpx-vp9",
                str(path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                pytest.skip("VP9 codec not available")
            
            meta = await extract_video_metadata(path)
            
            assert meta.has_audio is False
            assert meta.audio_codec == ""
            assert meta.audio_channels == 0
        finally:
            if path.exists():
                path.unlink()
    
    @pytest.mark.asyncio
    async def test_extract_metadata_nonexistent_file(self):
        """Test extracting metadata from non-existent file."""
        path = Path("/nonexistent/video.mp4")
        with pytest.raises(FileNotFoundError):
            await extract_video_metadata(path)
    
    @pytest.mark.asyncio
    async def test_extract_metadata_not_a_file(self):
        """Test extracting metadata from directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            with pytest.raises(VideoMetadataError):
                await extract_video_metadata(path)
    
    @pytest.mark.asyncio
    async def test_extract_mkv_metadata(self):
        """Test extracting metadata from MKV container."""
        with tempfile.NamedTemporaryFile(suffix=".mkv", delete=False) as f:
            path = Path(f.name)
        
        try:
            # Create MKV test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=2:size=320x240:rate=24",
                "-c:v", "libx264",
                str(path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            meta = await extract_video_metadata(path)
            
            assert meta.container in ("matroska", "mkv")
            assert meta.width == 320
            assert meta.height == 240
        finally:
            if path.exists():
                path.unlink()
    
    @pytest.mark.asyncio
    async def test_extract_avi_metadata(self):
        """Test extracting metadata from AVI container."""
        with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as f:
            path = Path(f.name)
        
        try:
            # Create AVI test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=2:size=320x240:rate=24",
                "-c:v", "mpeg4",
                str(path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            meta = await extract_video_metadata(path)
            
            assert meta.container == "avi"
        finally:
            if path.exists():
                path.unlink()
    
    @pytest.mark.asyncio
    async def test_graceful_handling_of_corrupted_file(self):
        """Test handling of corrupted video file."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            path = Path(f.name)
            f.write(b"This is not a valid video file")
        
        try:
            with pytest.raises(FFmpegError):
                await extract_video_metadata(path)
        finally:
            path.unlink()
    
    @pytest.mark.asyncio
    async def test_container_from_extension_fallback(self):
        """Test that container is derived from extension when ffprobe doesn't provide it."""
        with tempfile.NamedTemporaryFile(suffix=".mov", delete=False) as f:
            path = Path(f.name)
        
        try:
            # Create QuickTime format video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=1:size=320x240:rate=24",
                "-f", "mov",
                str(path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            meta = await extract_video_metadata(path)
            
            # Container should be detected
            assert meta.container != ""
        finally:
            if path.exists():
                path.unlink()
