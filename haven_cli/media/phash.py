"""Perceptual hash (pHash) calculation for video deduplication.

This module provides functions to calculate perceptual hashes for videos,
which allow identifying duplicate or near-duplicate content regardless of
encoding differences, resolution changes, or minor edits.

The implementation uses a DCT-based algorithm:
1. Extract frames uniformly distributed across the video
2. Create a sprite image (grid) from the frames
3. Calculate pHash using DCT (Discrete Cosine Transform)
4. Compare hashes using Hamming distance

Based on the electron app implementation in backend/app/utils/phash/phash_calculator.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, TYPE_CHECKING

from PIL import Image

from haven_cli.media.frames import create_sprite_image, extract_frames_uniform
from haven_cli.media.exceptions import VideoMetadataError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Constants for frame extraction and sprite creation
SPRITE_WIDTH = 160  # pixels per frame in sprite
SPRITE_COLUMNS = 5  # frames per row
SPRITE_ROWS = 5  # rows in sprite
DEFAULT_FRAME_COUNT = SPRITE_COLUMNS * SPRITE_ROWS  # 25 frames total

# Default hash size (8 = 64-bit hash)
DEFAULT_HASH_SIZE = 8

# Default threshold for duplicate detection (Hamming distance)
DEFAULT_DUPLICATE_THRESHOLD = 10


class VideoHashError(VideoMetadataError):
    """Raised when video hash calculation fails."""

    pass


def calculate_frame_phash(image: Image.Image, hash_size: int = DEFAULT_HASH_SIZE) -> str:
    """
    Calculate perceptual hash for a single image/frame.

    Uses DCT-based algorithm via the imagehash library:
    1. Resize to 32x32 grayscale
    2. Apply DCT (Discrete Cosine Transform)
    3. Take top-left 8x8 (low-frequency components)
    4. Calculate median and generate hash bits

    Args:
        image: PIL Image object
        hash_size: Size of hash in bits per dimension (default 8 = 64-bit hash)

    Returns:
        Hexadecimal string representing the perceptual hash

    Raises:
        VideoHashError: If hash calculation fails

    Example:
        >>> from PIL import Image
        >>> img = Image.open("frame.png")
        >>> hash_str = calculate_frame_phash(img)
        >>> print(hash_str)
        'a3f5c2d8e9b1a7f4'
    """
    try:
        import imagehash
    except ImportError as e:
        raise VideoHashError(
            "imagehash library not installed. "
            "Install with: pip install imagehash"
        ) from e

    try:
        # Calculate pHash using imagehash library
        phash = imagehash.phash(image, hash_size=hash_size)
        return str(phash)
    except Exception as e:
        raise VideoHashError(f"Failed to calculate frame pHash: {e}") from e


async def calculate_video_phash(
    video_path: Path,
    frame_count: int = DEFAULT_FRAME_COUNT,
    hash_size: int = DEFAULT_HASH_SIZE,
) -> str:
    """
    Calculate perceptual hash for video content.

    Algorithm:
    1. Extract N frames evenly distributed across video duration
    2. Create a sprite image (grid) from the frames
    3. Calculate pHash for the sprite using DCT-based algorithm

    Args:
        video_path: Path to video file
        frame_count: Number of frames to sample (default 25)
        hash_size: Size of hash in bits per dimension (default 8 = 64-bit hash)

    Returns:
        Hexadecimal string representing the video pHash

    Raises:
        FileNotFoundError: If video file doesn't exist
        VideoHashError: If hash calculation fails

    Example:
        >>> phash = await calculate_video_phash(Path("video.mp4"))
        >>> print(phash)
        'a3f5c2d8e9b1a7f4'
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    try:
        # Extract frames uniformly distributed across the video
        frames = await extract_frames_uniform(
            video_path,
            frame_count=frame_count,
            output_format="RGB",
            width=SPRITE_WIDTH,
            height=SPRITE_WIDTH,
        )

        if not frames:
            raise VideoHashError(
                "No frames could be extracted from video",
                path=str(video_path),
            )

        # Create sprite image from frames
        sprite = create_sprite_image(
            frames,
            columns=SPRITE_COLUMNS,
            frame_size=SPRITE_WIDTH,
        )

        # Calculate pHash from sprite
        phash = calculate_frame_phash(sprite, hash_size=hash_size)

        return phash

    except VideoHashError:
        raise
    except Exception as e:
        raise VideoHashError(
            f"Failed to calculate video pHash: {e}",
            path=str(video_path),
        ) from e


def hamming_distance(hash1: str, hash2: str) -> int:
    """
    Calculate Hamming distance between two pHash strings.

    The Hamming distance is the number of positions at which the
    corresponding bits are different. Lower values indicate more
    similar images.

    Args:
        hash1: First pHash as hex string
        hash2: Second pHash as hex string

    Returns:
        Hamming distance (0 = identical, higher = more different)

    Raises:
        ValueError: If hashes are not valid hex strings or different lengths

    Example:
        >>> hamming_distance("a3f5", "a3f4")
        1
        >>> hamming_distance("0000", "ffff")
        16
    """
    if len(hash1) != len(hash2):
        raise ValueError(
            f"Hashes must be same length: {len(hash1)} vs {len(hash2)}"
        )

    try:
        # Convert hex strings to integers
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
    except ValueError as e:
        raise ValueError("Hashes must be valid hex strings") from e

    # XOR to find differing bits
    xor = int1 ^ int2

    # Count set bits (Hamming distance)
    distance = 0
    while xor:
        distance += xor & 1
        xor >>= 1

    return distance


def is_similar(
    hash1: str,
    hash2: str,
    threshold: int = DEFAULT_DUPLICATE_THRESHOLD,
) -> bool:
    """
    Check if two pHashes are similar (within threshold).

    Args:
        hash1: First pHash as hex string
        hash2: Second pHash as hex string
        threshold: Maximum Hamming distance for similarity (default 10)

    Returns:
        True if hashes are similar, False otherwise

    Example:
        >>> is_similar("a3f5", "a3f4", threshold=2)
        True
        >>> is_similar("a3f5", "b4e6", threshold=2)
        False
    """
    try:
        distance = hamming_distance(hash1, hash2)
        return distance <= threshold
    except ValueError:
        return False


def calculate_hash_similarity(hash1: str, hash2: str) -> float:
    """
    Calculate similarity score between two pHashes.

    Returns a normalized similarity score from 0.0 (completely different)
    to 1.0 (identical).

    Args:
        hash1: First pHash as hex string
        hash2: Second pHash as hex string

    Returns:
        Similarity score between 0.0 and 1.0

    Example:
        >>> calculate_hash_similarity("a3f5", "a3f5")
        1.0
        >>> calculate_hash_similarity("0000", "ffff")
        0.0  # (for 16-bit hashes)
    """
    try:
        distance = hamming_distance(hash1, hash2)
        # Each hex character is 4 bits
        max_distance = len(hash1) * 4
        return max(0.0, 1.0 - (distance / max_distance))
    except ValueError:
        return 0.0


async def find_similar_videos(
    phash: str,
    session: Session,
    threshold: int = DEFAULT_DUPLICATE_THRESHOLD,
) -> List[dict]:
    """
    Find videos with similar pHash in database.

    Note: This function performs exact database queries and filters by
    Hamming distance in Python. For large databases, consider using
    database-specific Hamming distance functions.

    Args:
        phash: pHash to compare
        threshold: Maximum Hamming distance for match
        session: SQLAlchemy session

    Returns:
        List of similar video dictionaries with similarity scores

    Example:
        >>> similar = await find_similar_videos("a3f5c2d8", session)
        >>> print(similar[0]["similarity"])
        0.95
    """
    from haven_cli.database.models import Video

    # Query all videos with pHash (could be optimized with indexed queries)
    videos = session.query(Video).filter(Video.phash.isnot(None)).all()

    similar_videos: List[dict] = []

    for video in videos:
        if video.phash is None:
            continue

        try:
            distance = hamming_distance(phash, video.phash)
            if distance <= threshold:
                similar_videos.append({
                    "id": video.id,
                    "source_path": video.source_path,
                    "title": video.title,
                    "phash": video.phash,
                    "hamming_distance": distance,
                    "similarity": calculate_hash_similarity(phash, video.phash),
                })
        except ValueError:
            # Skip invalid hashes
            continue

    # Sort by similarity (highest first)
    similar_videos.sort(key=lambda x: x["similarity"], reverse=True)

    return similar_videos


async def is_duplicate(
    phash: str,
    session: Session,
    threshold: int = DEFAULT_DUPLICATE_THRESHOLD,
) -> bool:
    """
    Check if video with similar pHash exists in database.

    Args:
        phash: pHash to check
        threshold: Maximum Hamming distance for duplicate detection
        session: SQLAlchemy session

    Returns:
        True if a similar video exists, False otherwise

    Example:
        >>> if await is_duplicate(phash, session):
        ...     print("Video already exists!")
    """
    similar = await find_similar_videos(phash, session, threshold)
    return len(similar) > 0


async def get_video_by_phash(
    phash: str,
    session: Session,
    threshold: int = DEFAULT_DUPLICATE_THRESHOLD,
) -> dict | None:
    """
    Find the most similar video by pHash.

    Args:
        phash: pHash to search for
        threshold: Maximum Hamming distance for match
        session: SQLAlchemy session

    Returns:
        Video dictionary if found, None otherwise

    Example:
        >>> video = await get_video_by_phash("a3f5c2d8", session)
        >>> if video:
        ...     print(f"Found: {video['title']}")
    """
    similar = await find_similar_videos(phash, session, threshold)
    return similar[0] if similar else None
