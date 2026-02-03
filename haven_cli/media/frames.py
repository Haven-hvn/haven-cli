"""Frame extraction utilities for video processing.

This module provides functions to extract frames from videos at specific
timestamps using FFmpeg. Extracted frames are used for perceptual hashing
and thumbnail generation.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import List

from PIL import Image

from haven_cli.media.exceptions import FFmpegError


async def extract_frames(
    video_path: Path,
    timestamps: List[float],
    output_format: str = "RGB",
    width: int = 320,
    height: int = 320,
) -> List[Image.Image]:
    """
    Extract frames at specified timestamps using FFmpeg.

    Args:
        video_path: Path to video file
        timestamps: List of timestamps in seconds
        output_format: PIL image mode (default "RGB")
        width: Target width for extracted frames (default 320)
        height: Target height for extracted frames (default 320)

    Returns:
        List of PIL Image objects in the requested format

    Raises:
        FileNotFoundError: If video file doesn't exist
        FFmpegError: If frame extraction fails

    Example:
        >>> frames = await extract_frames(
        ...     Path("video.mp4"),
        ...     [1.0, 5.0, 10.0]
        ... )
        >>> len(frames)
        3
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if not timestamps:
        return []

    frames: List[Image.Image] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Extract frames concurrently for better performance
        tasks = [
            _extract_single_frame(video_path, ts, tmp_path, i, width, height)
            for i, ts in enumerate(timestamps)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                # Log warning but continue with other frames
                continue
            if result is not None:
                try:
                    # Open and convert to requested format
                    img = Image.open(result)
                    if output_format and img.mode != output_format:
                        img = img.convert(output_format)
                    frames.append(img)
                except Exception:
                    # Skip frames that can't be opened
                    continue

    return frames


async def _extract_single_frame(
    video_path: Path,
    timestamp: float,
    output_dir: Path,
    index: int,
    width: int = 320,
    height: int = 320,
) -> Path | None:
    """
    Extract a single frame at the specified timestamp.

    Args:
        video_path: Path to video file
        timestamp: Timestamp in seconds
        output_dir: Directory to save the frame
        index: Frame index for naming
        width: Target width for the frame
        height: Target height for the frame

    Returns:
        Path to the extracted frame, or None if extraction failed
    """
    output_path = output_dir / f"frame_{index:04d}.png"

    # FFmpeg command to extract frame at specific timestamp
    # -ss: seek to timestamp (placed before -i for faster seeking)
    # -i: input file
    # -vframes: extract only 1 frame
    # -vf: video filter for scaling
    # -f: output format
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output
        "-ss",
        str(timestamp),
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        "-f",
        "image2",
        "-pix_fmt",
        "rgb24",
        str(output_path),
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
                f"Failed to extract frame at {timestamp}s",
                path=str(video_path),
                returncode=proc.returncode,
                stderr=stderr.decode("utf-8", errors="replace"),
            )

        if output_path.exists():
            return output_path
        return None

    except FileNotFoundError as e:
        raise FFmpegError(
            "ffmpeg not found. Please install FFmpeg.",
            path=str(video_path),
        ) from e


async def extract_frames_uniform(
    video_path: Path,
    frame_count: int = 25,
    skip_start_pct: float = 0.05,
    skip_end_pct: float = 0.05,
    **kwargs,
) -> List[Image.Image]:
    """
    Extract frames uniformly distributed across video duration.

    This is useful for creating a visual summary of the video or for
    calculating perceptual hashes.

    Args:
        video_path: Path to video file
        frame_count: Number of frames to extract (default 25)
        skip_start_pct: Percentage of start to skip (default 0.05 = 5%)
        skip_end_pct: Percentage of end to skip (default 0.05 = 5%)
        **kwargs: Additional arguments passed to extract_frames

    Returns:
        List of PIL Image objects

    Example:
        >>> frames = await extract_frames_uniform(Path("video.mp4"), frame_count=8)
        >>> len(frames)
        8
    """
    from haven_cli.media.metadata import extract_video_duration

    duration = await extract_video_duration(video_path)

    if duration <= 0:
        return []

    # Calculate the usable duration (excluding skipped portions)
    skip_start = duration * skip_start_pct
    skip_end = duration * skip_end_pct
    usable_duration = duration - skip_start - skip_end

    if usable_duration <= 0:
        return []

    # Calculate timestamps evenly distributed across usable duration
    if frame_count == 1:
        timestamps = [skip_start + usable_duration / 2]
    else:
        step = usable_duration / frame_count
        timestamps = [skip_start + i * step + step / 2 for i in range(frame_count)]

    return await extract_frames(video_path, timestamps, **kwargs)


def create_sprite_image(
    frames: List[Image.Image],
    columns: int = 5,
    frame_size: int = 160,
) -> Image.Image:
    """
    Create a sprite image (grid) from a list of frames.

    This combines multiple frames into a single image grid, useful for
    perceptual hashing as it captures visual content from across the video.

    Args:
        frames: List of PIL Image objects
        columns: Number of columns in the grid (default 5)
        frame_size: Size of each frame in pixels (default 160)

    Returns:
        PIL Image containing the sprite grid

    Raises:
        ValueError: If frames list is empty

    Example:
        >>> frames = [frame1, frame2, frame3, frame4]
        >>> sprite = create_sprite_image(frames, columns=2, frame_size=160)
        >>> sprite.size
        (320, 320)
    """
    if not frames:
        raise ValueError("Cannot create sprite from empty frame list")

    # Calculate grid dimensions
    frame_count = len(frames)
    columns = min(columns, frame_count)
    rows = (frame_count + columns - 1) // columns  # Ceiling division

    # Create sprite canvas
    sprite = Image.new("RGB", (columns * frame_size, rows * frame_size))

    # Place frames in grid
    for idx, frame in enumerate(frames):
        row = idx // columns
        col = idx % columns

        # Resize frame to uniform size
        resized = frame.resize((frame_size, frame_size), Image.Resampling.LANCZOS)

        # Paste into sprite
        x = col * frame_size
        y = row * frame_size
        sprite.paste(resized, (x, y))

    return sprite
