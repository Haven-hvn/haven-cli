"""Media processing module for Haven CLI.

This module provides video metadata extraction, thumbnail generation,
perceptual hashing, and other media processing utilities using FFmpeg.
"""

from haven_cli.media.exceptions import (
    FFmpegError,
    MediaError,
    MimeTypeError,
    ThumbnailError,
    VideoMetadataError,
)
from haven_cli.media.frames import (
    create_sprite_image,
    extract_frames,
    extract_frames_uniform,
)
from haven_cli.media.metadata import (
    VideoTechnicalMetadata,
    clear_metadata_cache,
    detect_mime_type,
    extract_video_duration,
    extract_video_metadata,
    get_metadata_cache_info,
)
from haven_cli.media.phash import (
    VideoHashError,
    calculate_frame_phash,
    calculate_hash_similarity,
    calculate_video_phash,
    hamming_distance,
    is_similar,
)
from haven_cli.media.thumbnail import (
    generate_preview_grid,
    generate_thumbnail,
    generate_thumbnails_at_intervals,
)

__all__ = [
    # Exceptions
    "MediaError",
    "VideoMetadataError",
    "ThumbnailError",
    "FFmpegError",
    "MimeTypeError",
    "VideoHashError",
    # Metadata
    "VideoTechnicalMetadata",
    "extract_video_metadata",
    "extract_video_duration",
    "detect_mime_type",
    "clear_metadata_cache",
    "get_metadata_cache_info",
    # Thumbnail
    "generate_thumbnail",
    "generate_thumbnails_at_intervals",
    "generate_preview_grid",
    # Frame extraction
    "extract_frames",
    "extract_frames_uniform",
    "create_sprite_image",
    # Perceptual hashing
    "calculate_video_phash",
    "calculate_frame_phash",
    "hamming_distance",
    "is_similar",
    "calculate_hash_similarity",
]
