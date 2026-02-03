"""Video metadata extraction using FFmpeg/ffprobe.

This module provides functions to extract comprehensive video metadata
including duration, resolution, codec information, and more.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haven_cli.media.exceptions import FFmpegError, VideoMetadataError


@dataclass(frozen=True)
class VideoTechnicalMetadata:
    """Comprehensive technical metadata for a video file.
    
    Attributes:
        duration: Duration in seconds
        width: Width in pixels
        height: Height in pixels
        fps: Frames per second
        codec: Video codec name (e.g., "h264", "vp9")
        bitrate: Bitrate in bits per second
        audio_codec: Audio codec name (e.g., "aac", "opus"), empty if no audio
        audio_channels: Number of audio channels, 0 if no audio
        container: Container format (e.g., "mp4", "mkv", "webm")
        has_audio: Whether the video has an audio track
    """
    
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    bitrate: int
    audio_codec: str
    audio_channels: int
    container: str
    has_audio: bool = False


# Cache for ffprobe results to avoid re-extraction
_ffprobe_cache: dict[str, dict[str, Any]] = {}


def _get_ffprobe_cache_key(video_path: Path) -> str:
    """Generate a cache key for a video file.
    
    Uses file path and modification time to invalidate stale cache entries.
    """
    stat = video_path.stat()
    return f"{video_path}:{stat.st_mtime}:{stat.st_size}"


def _get_from_cache(video_path: Path) -> dict[str, Any] | None:
    """Get cached ffprobe result if available and not stale."""
    cache_key = _get_ffprobe_cache_key(video_path)
    return _ffprobe_cache.get(cache_key)


def _add_to_cache(video_path: Path, data: dict[str, Any]) -> None:
    """Add ffprobe result to cache."""
    cache_key = _get_ffprobe_cache_key(video_path)
    _ffprobe_cache[cache_key] = data


async def _run_ffprobe(video_path: Path) -> dict[str, Any]:
    """Run ffprobe and return parsed JSON output.
    
    Args:
        video_path: Path to video file
        
    Returns:
        Parsed ffprobe JSON output
        
    Raises:
        FFmpegError: If ffprobe fails or returns invalid output
    """
    # Check cache first
    cached = _get_from_cache(video_path)
    if cached is not None:
        return cached
    
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise FFmpegError(
                f"ffprobe failed for {video_path}",
                path=str(video_path),
                returncode=proc.returncode,
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        
        data = json.loads(stdout.decode("utf-8"))
        _add_to_cache(video_path, data)
        return data
        
    except FileNotFoundError as e:
        raise FFmpegError(
            "ffprobe not found. Please install FFmpeg.",
            path=str(video_path),
        ) from e
    except json.JSONDecodeError as e:
        raise FFmpegError(
            f"Failed to parse ffprobe output: {e}",
            path=str(video_path),
        ) from e
    except Exception as e:
        if isinstance(e, FFmpegError):
            raise
        raise FFmpegError(
            f"Unexpected error running ffprobe: {e}",
            path=str(video_path),
        ) from e


def _parse_video_stream(stream: dict[str, Any]) -> dict[str, Any]:
    """Parse video stream data from ffprobe output.
    
    Args:
        stream: Video stream dictionary from ffprobe
        
    Returns:
        Dictionary with parsed video metadata
    """
    result: dict[str, Any] = {
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "codec": "",
    }
    
    # Resolution
    result["width"] = stream.get("width", 0)
    result["height"] = stream.get("height", 0)
    
    # Codec name
    codec_name = stream.get("codec_name", "")
    result["codec"] = codec_name.lower() if codec_name else ""
    
    # FPS calculation
    fps_str = stream.get("r_frame_rate", "0/1")
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 0.0
        else:
            fps = float(fps_str)
        result["fps"] = fps
    except (ValueError, ZeroDivisionError):
        result["fps"] = 0.0
    
    return result


def _parse_audio_stream(stream: dict[str, Any]) -> dict[str, Any]:
    """Parse audio stream data from ffprobe output.
    
    Args:
        stream: Audio stream dictionary from ffprobe
        
    Returns:
        Dictionary with parsed audio metadata
    """
    codec_name = stream.get("codec_name", "")
    channels = stream.get("channels", 0)
    
    return {
        "audio_codec": codec_name.lower() if codec_name else "",
        "audio_channels": channels,
        "has_audio": True,
    }


def _parse_format_data(format_data: dict[str, Any]) -> dict[str, Any]:
    """Parse format-level data from ffprobe output.
    
    Args:
        format_data: Format dictionary from ffprobe
        
    Returns:
        Dictionary with parsed format metadata
    """
    result: dict[str, Any] = {
        "duration": 0.0,
        "bitrate": 0,
        "container": "",
    }
    
    # Duration
    duration_str = format_data.get("duration", "0")
    try:
        result["duration"] = float(duration_str)
    except (ValueError, TypeError):
        result["duration"] = 0.0
    
    # Bitrate
    bitrate_str = format_data.get("bit_rate", "0")
    try:
        result["bitrate"] = int(bitrate_str)
    except (ValueError, TypeError):
        result["bitrate"] = 0
    
    # Container format
    format_name = format_data.get("format_name", "")
    if format_name:
        # Take the first format name if multiple (e.g., "matroska,webm" -> "matroska")
        result["container"] = format_name.split(",")[0].lower()
    
    return result


async def extract_video_metadata(video_path: Path) -> VideoTechnicalMetadata:
    """Extract comprehensive video metadata using ffprobe.
    
    This function extracts technical metadata including duration, resolution,
    codec information, and audio properties from a video file.
    
    Args:
        video_path: Path to the video file
        
    Returns:
        VideoTechnicalMetadata with all extracted information
        
    Raises:
        VideoMetadataError: If metadata extraction fails
        FileNotFoundError: If the video file doesn't exist
        
    Example:
        >>> metadata = await extract_video_metadata(Path("video.mp4"))
        >>> print(f"Duration: {metadata.duration}s, Resolution: {metadata.width}x{metadata.height}")
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    if not video_path.is_file():
        raise VideoMetadataError(f"Path is not a file: {video_path}", path=str(video_path))
    
    try:
        data = await _run_ffprobe(video_path)
    except FFmpegError:
        raise
    except Exception as e:
        raise VideoMetadataError(
            f"Failed to extract metadata: {e}",
            path=str(video_path),
        ) from e
    
    # Initialize with defaults
    metadata: dict[str, Any] = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "codec": "",
        "bitrate": 0,
        "audio_codec": "",
        "audio_channels": 0,
        "container": "",
        "has_audio": False,
    }
    
    # Parse format data
    format_data = data.get("format", {})
    metadata.update(_parse_format_data(format_data))
    
    # Parse streams
    streams = data.get("streams", [])
    video_stream_found = False
    audio_stream_found = False
    
    for stream in streams:
        codec_type = stream.get("codec_type", "").lower()
        
        if codec_type == "video" and not video_stream_found:
            metadata.update(_parse_video_stream(stream))
            video_stream_found = True
            
            # Try to get duration from video stream if not in format
            if metadata["duration"] == 0.0:
                duration_ts = stream.get("duration_ts", 0)
                time_base = stream.get("time_base", "1/1")
                if duration_ts and time_base:
                    try:
                        num, den = time_base.split("/")
                        metadata["duration"] = float(duration_ts) * float(num) / float(den)
                    except (ValueError, ZeroDivisionError):
                        pass
        
        elif codec_type == "audio" and not audio_stream_found:
            metadata.update(_parse_audio_stream(stream))
            audio_stream_found = True
    
    # Validate that we found a video stream
    if not video_stream_found:
        raise VideoMetadataError(
            "No video stream found in file",
            path=str(video_path),
        )
    
    # Derive container from file extension if not detected
    if not metadata["container"]:
        ext = video_path.suffix.lower()
        container_map = {
            ".mp4": "mp4",
            ".m4v": "mp4",
            ".mkv": "matroska",
            ".webm": "webm",
            ".avi": "avi",
            ".mov": "mov",
            ".qt": "mov",
            ".wmv": "wmv",
            ".flv": "flv",
            ".ogv": "ogg",
        }
        metadata["container"] = container_map.get(ext, "")
    
    return VideoTechnicalMetadata(**metadata)


async def extract_video_duration(video_path: Path) -> float:
    """Extract video duration in seconds using ffprobe.
    
    This is a convenience function that extracts just the duration.
    For full metadata, use extract_video_metadata().
    
    Args:
        video_path: Path to the video file
        
    Returns:
        Duration in seconds (float)
        
    Raises:
        VideoMetadataError: If duration extraction fails
        FileNotFoundError: If the video file doesn't exist
        
    Example:
        >>> duration = await extract_video_duration(Path("video.mp4"))
        >>> print(f"Video is {duration:.2f} seconds long")
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    try:
        data = await _run_ffprobe(video_path)
    except FFmpegError:
        raise
    except Exception as e:
        raise VideoMetadataError(
            f"Failed to extract duration: {e}",
            path=str(video_path),
        ) from e
    
    # Try format duration first
    format_data = data.get("format", {})
    duration_str = format_data.get("duration", "")
    
    try:
        if duration_str:
            return float(duration_str)
    except (ValueError, TypeError):
        pass
    
    # Fallback to video stream duration
    for stream in data.get("streams", []):
        if stream.get("codec_type", "").lower() == "video":
            duration_ts = stream.get("duration_ts", 0)
            time_base = stream.get("time_base", "1/1")
            if duration_ts and time_base:
                try:
                    num, den = time_base.split("/")
                    return float(duration_ts) * float(num) / float(den)
                except (ValueError, ZeroDivisionError):
                    pass
            
            # Calculate from fps and frame count
            fps_str = stream.get("r_frame_rate", "0/1")
            nb_frames = stream.get("nb_frames", "0")
            try:
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    fps = float(num) / float(den) if float(den) != 0 else 0.0
                else:
                    fps = float(fps_str)
                frames = float(nb_frames)
                if fps > 0 and frames > 0:
                    return frames / fps
            except (ValueError, ZeroDivisionError):
                pass
    
    # If no duration found but file is valid, return 0.0
    return 0.0


def detect_mime_type(video_path: Path, use_magic: bool = True) -> str:
    """Detect MIME type of a video file.
    
    This function attempts to detect the MIME type using multiple methods:
    1. python-magic (if available and use_magic=True)
    2. mimetypes module (file extension based)
    3. Fallback to generic video/mp4
    
    Args:
        video_path: Path to the video file
        use_magic: Whether to try python-magic first (requires libmagic)
        
    Returns:
        MIME type string
        
    Example:
        >>> mime = detect_mime_type(Path("video.mp4"))
        >>> print(mime)  # "video/mp4"
    """
    if not video_path.exists():
        return "application/octet-stream"
    
    # Try python-magic if requested
    if use_magic:
        try:
            import magic
            mime = magic.from_file(str(video_path), mime=True)
            if mime and isinstance(mime, str):
                return mime
        except ImportError:
            pass  # magic not available, fall through
        except Exception:
            pass  # magic failed, fall through
    
    # Try mimetypes module (extension-based)
    mime_type, _ = mimetypes.guess_type(str(video_path))
    if mime_type:
        return mime_type
    
    # Fallback to extension-based detection
    extension_map = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".qt": "video/quicktime",
        ".wmv": "video/x-ms-wmv",
        ".flv": "video/x-flv",
        ".f4v": "video/x-f4v",
        ".ogv": "video/ogg",
        ".ogg": "video/ogg",
        ".3gp": "video/3gpp",
        ".3g2": "video/3gpp2",
        ".ts": "video/mp2t",
        ".mts": "video/mp2t",
        ".m2ts": "video/mp2t",
    }
    
    ext = video_path.suffix.lower()
    return extension_map.get(ext, "video/mp4")


def clear_metadata_cache() -> None:
    """Clear the ffprobe result cache.
    
    This is useful for testing or when files may have changed.
    """
    _ffprobe_cache.clear()


def get_metadata_cache_info() -> dict[str, Any]:
    """Get information about the current cache state.
    
    Returns:
        Dictionary with cache statistics
    """
    return {
        "cache_size": len(_ffprobe_cache),
        "cache_keys": list(_ffprobe_cache.keys()),
    }
