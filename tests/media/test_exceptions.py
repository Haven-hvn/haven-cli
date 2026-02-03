"""Tests for media exceptions."""

import pytest

from haven_cli.media.exceptions import (
    FFmpegError,
    MediaError,
    MimeTypeError,
    ThumbnailError,
    VideoMetadataError,
)


class TestMediaError:
    """Test base MediaError class."""
    
    def test_basic_error(self):
        """Test creating basic media error."""
        error = MediaError("Something went wrong")
        assert error.message == "Something went wrong"
        assert error.path is None
        assert str(error) == "Something went wrong"
    
    def test_error_with_path(self):
        """Test creating error with path."""
        error = MediaError("File not found", path="/path/to/file.mp4")
        assert error.path == "/path/to/file.mp4"
        assert "/path/to/file.mp4" in str(error)
    
    def test_error_inheritance(self):
        """Test that all errors inherit from MediaError."""
        assert issubclass(VideoMetadataError, MediaError)
        assert issubclass(ThumbnailError, MediaError)
        assert issubclass(FFmpegError, MediaError)
        assert issubclass(MimeTypeError, MediaError)


class TestVideoMetadataError:
    """Test VideoMetadataError class."""
    
    def test_basic_error(self):
        """Test creating video metadata error."""
        error = VideoMetadataError("Failed to extract duration")
        assert error.message == "Failed to extract duration"
    
    def test_error_with_path(self):
        """Test creating error with video path."""
        error = VideoMetadataError(
            "No video stream found",
            path="/path/to/corrupted.mp4",
        )
        assert "/path/to/corrupted.mp4" in str(error)


class TestThumbnailError:
    """Test ThumbnailError class."""
    
    def test_basic_error(self):
        """Test creating thumbnail error."""
        error = ThumbnailError("Failed to generate thumbnail")
        assert error.message == "Failed to generate thumbnail"
    
    def test_empty_thumbnail_error(self):
        """Test error for empty thumbnail."""
        error = ThumbnailError(
            "Generated thumbnail is empty",
            path="/output/thumb.jpg",
        )
        assert "empty" in error.message.lower()


class TestFFmpegError:
    """Test FFmpegError class."""
    
    def test_basic_error(self):
        """Test creating FFmpeg error."""
        error = FFmpegError("ffprobe failed")
        assert error.message == "ffprobe failed"
        assert error.returncode is None
        assert error.stderr is None
    
    def test_error_with_returncode(self):
        """Test creating error with return code."""
        error = FFmpegError(
            "FFmpeg failed",
            returncode=1,
        )
        assert error.returncode == 1
        assert "returncode: 1" in str(error)
    
    def test_error_with_stderr(self):
        """Test creating error with stderr output."""
        error = FFmpegError(
            "FFmpeg failed",
            stderr="Unknown encoder 'h265'",
        )
        assert "Unknown encoder" in str(error)
    
    def test_error_with_all_fields(self):
        """Test creating error with all fields."""
        error = FFmpegError(
            "Conversion failed",
            path="/input/video.mp4",
            returncode=255,
            stderr="Error while decoding stream",
        )
        error_str = str(error)
        assert "Conversion failed" in error_str
        assert "/input/video.mp4" in error_str
        assert "returncode: 255" in error_str
        assert "Error while decoding" in error_str
    
    def test_stderr_truncation(self):
        """Test that long stderr is truncated in string representation."""
        long_stderr = "Error: " + "x" * 1000
        error = FFmpegError(
            "FFmpeg failed",
            stderr=long_stderr,
        )
        error_str = str(error)
        assert len(error_str) < 500  # Should be truncated


class TestMimeTypeError:
    """Test MimeTypeError class."""
    
    def test_basic_error(self):
        """Test creating MIME type error."""
        error = MimeTypeError("Could not detect MIME type")
        assert error.message == "Could not detect MIME type"
    
    def test_error_with_path(self):
        """Test creating error with file path."""
        error = MimeTypeError(
            "Unknown file format",
            path="/path/to/unknown.xyz",
        )
        assert "/path/to/unknown.xyz" in str(error)
