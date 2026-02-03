"""Video thumbnail generation using FFmpeg.

This module provides functions to generate thumbnail images from video files
at specified timestamps with customizable dimensions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from haven_cli.media.exceptions import FFmpegError, ThumbnailError


async def generate_thumbnail(
    video_path: Path,
    output_path: Path,
    timestamp: float = 0.0,
    size: tuple[int, int] = (320, 180),
    quality: int = 2,
) -> Path:
    """Generate a thumbnail image from a video file.
    
    Uses FFmpeg to extract a frame at the specified timestamp and resize it
    to the desired dimensions. The output format is determined by the
    output_path extension (e.g., .jpg, .png).
    
    Args:
        video_path: Path to the source video file
        output_path: Path where the thumbnail will be saved
        timestamp: Time in seconds to extract the frame from (default: 0.0)
        size: Tuple of (width, height) for the output thumbnail (default: 320x180)
        quality: JPEG quality scale 1-31 (lower is better, default: 2)
        
    Returns:
        Path to the generated thumbnail file
        
    Raises:
        FileNotFoundError: If the video file doesn't exist
        ThumbnailError: If thumbnail generation fails
        FFmpegError: If FFmpeg command fails
        
    Example:
        >>> thumbnail = await generate_thumbnail(
        ...     Path("video.mp4"),
        ...     Path("thumb.jpg"),
        ...     timestamp=5.0,
        ...     size=(640, 360),
        ... )
        >>> print(f"Thumbnail saved to: {thumbnail}")
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    if not video_path.is_file():
        raise ThumbnailError(f"Path is not a file: {video_path}", path=str(video_path))
    
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Determine output format from extension
    ext = output_path.suffix.lower()
    
    width, height = size
    
    # Build FFmpeg command
    cmd: list[str | Any] = [
        "ffmpeg",
        "-y",  # Overwrite output file if exists
        "-ss", str(timestamp),  # Seek to timestamp
        "-i", str(video_path),  # Input file
        "-vframes", "1",  # Extract single frame
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-q:v", str(quality),  # Quality setting
    ]
    
    # Add format-specific options
    if ext in (".jpg", ".jpeg"):
        cmd.extend(["-f", "image2", "-c:v", "mjpeg"])
    elif ext == ".png":
        cmd.extend(["-f", "image2", "-c:v", "png"])
    elif ext == ".webp":
        cmd.extend(["-f", "image2", "-c:v", "libwebp"])
    
    cmd.append(str(output_path))
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            raise FFmpegError(
                f"FFmpeg failed to generate thumbnail",
                path=str(video_path),
                returncode=proc.returncode,
                stderr=stderr_text,
            )
        
        # Verify the thumbnail was created
        if not output_path.exists():
            raise ThumbnailError(
                "Thumbnail file was not created",
                path=str(output_path),
            )
        
        if output_path.stat().st_size == 0:
            output_path.unlink()  # Clean up empty file
            raise ThumbnailError(
                "Generated thumbnail is empty",
                path=str(output_path),
            )
        
        return output_path
        
    except FileNotFoundError as e:
        raise FFmpegError(
            "ffmpeg not found. Please install FFmpeg.",
            path=str(video_path),
        ) from e
    except Exception as e:
        if isinstance(e, (ThumbnailError, FFmpegError)):
            raise
        raise ThumbnailError(
            f"Unexpected error generating thumbnail: {e}",
            path=str(video_path),
        ) from e


async def generate_thumbnails_at_intervals(
    video_path: Path,
    output_dir: Path,
    interval: float = 10.0,
    size: tuple[int, int] = (320, 180),
    format: str = "jpg",
    max_thumbnails: int | None = None,
) -> list[Path]:
    """Generate multiple thumbnails at regular intervals.
    
    Args:
        video_path: Path to the source video file
        output_dir: Directory where thumbnails will be saved
        interval: Interval in seconds between thumbnails (default: 10.0)
        size: Tuple of (width, height) for the output thumbnails (default: 320x180)
        format: Output format extension without dot (default: "jpg")
        max_thumbnails: Maximum number of thumbnails to generate (default: None = unlimited)
        
    Returns:
        List of paths to generated thumbnail files
        
    Raises:
        FileNotFoundError: If the video file doesn't exist
        ThumbnailError: If thumbnail generation fails
        
    Example:
        >>> from haven_cli.media.metadata import extract_video_duration
        >>> duration = await extract_video_duration(Path("video.mp4"))
        >>> thumbnails = await generate_thumbnails_at_intervals(
        ...     Path("video.mp4"),
        ...     Path("thumbnails/"),
        ...     interval=duration / 5,  # 5 evenly spaced thumbnails
        ...     size=(640, 360),
        ... )
    """
    from haven_cli.media.metadata import extract_video_duration
    
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Get video duration
    try:
        duration = await extract_video_duration(video_path)
    except Exception as e:
        raise ThumbnailError(
            f"Failed to get video duration: {e}",
            path=str(video_path),
        ) from e
    
    if duration <= 0:
        raise ThumbnailError(
            "Cannot generate thumbnails: video has no duration",
            path=str(video_path),
        )
    
    # Calculate timestamps
    timestamps: list[float] = []
    current_time = interval  # Start after first interval (skip black frame at 0)
    
    while current_time < duration:
        timestamps.append(current_time)
        current_time += interval
        
        if max_thumbnails and len(timestamps) >= max_thumbnails:
            break
    
    # Ensure at least one thumbnail (at middle of video if duration is short)
    if not timestamps:
        timestamps = [duration / 2]
    
    # Generate thumbnails
    thumbnails: list[Path] = []
    base_name = video_path.stem
    
    for i, timestamp in enumerate(timestamps):
        output_path = output_dir / f"{base_name}_thumb_{i:03d}.{format}"
        try:
            thumbnail_path = await generate_thumbnail(
                video_path,
                output_path,
                timestamp=timestamp,
                size=size,
            )
            thumbnails.append(thumbnail_path)
        except Exception as e:
            # Log warning but continue with other thumbnails
            print(f"Warning: Failed to generate thumbnail at {timestamp}s: {e}")
    
    return thumbnails


async def generate_preview_grid(
    video_path: Path,
    output_path: Path,
    grid_size: tuple[int, int] = (3, 3),
    size: tuple[int, int] = (960, 540),
) -> Path:
    """Generate a preview grid image with multiple frames from the video.
    
    Creates a grid of frames sampled evenly throughout the video, useful
    for content previews.
    
    Args:
        video_path: Path to the source video file
        output_path: Path where the grid image will be saved
        grid_size: Tuple of (columns, rows) for the grid (default: 3x3)
        size: Size of the output image (default: 960x540)
        
    Returns:
        Path to the generated grid image
        
    Raises:
        FileNotFoundError: If the video file doesn't exist
        ThumbnailError: If grid generation fails
    """
    from haven_cli.media.metadata import extract_video_duration
    
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    cols, rows = grid_size
    num_frames = cols * rows
    
    # Get video duration
    try:
        duration = await extract_video_duration(video_path)
    except Exception as e:
        raise ThumbnailError(
            f"Failed to get video duration: {e}",
            path=str(video_path),
        ) from e
    
    if duration <= 0:
        raise ThumbnailError(
            "Cannot generate preview: video has no duration",
            path=str(video_path),
        )
    
    # Calculate timestamps evenly distributed
    timestamps = [
        duration * (i + 1) / (num_frames + 1)
        for i in range(num_frames)
    ]
    
    # Create temp directory for individual frames
    import tempfile
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        frame_paths: list[Path] = []
        
        # Generate individual frames
        for i, timestamp in enumerate(timestamps):
            frame_path = temp_path / f"frame_{i:03d}.jpg"
            try:
                await generate_thumbnail(
                    video_path,
                    frame_path,
                    timestamp=timestamp,
                    size=(size[0] // cols, size[1] // rows),
                )
                frame_paths.append(frame_path)
            except Exception as e:
                print(f"Warning: Failed to generate frame at {timestamp}s: {e}")
        
        if not frame_paths:
            raise ThumbnailError(
                "Failed to generate any frames for grid",
                path=str(video_path),
            )
        
        # Use FFmpeg to create grid (montage)
        filter_parts: list[str] = []
        
        # Input files
        for _ in frame_paths:
            filter_parts.append("[in]")
        
        # Create xstack filter
        inputs = "".join([f"[{i}:v]" for i in range(len(frame_paths))])
        layout = "|".join([
            f"{i % cols}_{i // cols}" if i < len(frame_paths) else "0_0"
            for i in range(num_frames)
        ])
        
        cmd = [
            "ffmpeg",
            "-y",
        ]
        
        for frame_path in frame_paths:
            cmd.extend(["-i", str(frame_path)])
        
        cmd.extend([
            "-filter_complex",
            f"{inputs}xstack=inputs={len(frame_paths)}:layout={layout}[out]",
            "-map", "[out]",
            "-c:v", "mjpeg",
            "-q:v", "2",
            str(output_path),
        ])
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace")
                raise FFmpegError(
                    f"FFmpeg failed to create grid",
                    path=str(video_path),
                    returncode=proc.returncode,
                    stderr=stderr_text,
                )
            
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise ThumbnailError(
                    "Grid image was not created",
                    path=str(output_path),
                )
            
            return output_path
            
        except FileNotFoundError as e:
            raise FFmpegError(
                "ffmpeg not found. Please install FFmpeg.",
                path=str(video_path),
            ) from e
