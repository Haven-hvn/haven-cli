"""Tests for thumbnail generation."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from haven_cli.media.exceptions import FFmpegError, ThumbnailError
from haven_cli.media.thumbnail import (
    generate_preview_grid,
    generate_thumbnail,
    generate_thumbnails_at_intervals,
)


class TestGenerateThumbnail:
    """Test single thumbnail generation."""
    
    @pytest.mark.asyncio
    async def test_generate_basic_thumbnail(self):
        """Test generating a basic thumbnail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            thumb_path = Path(tmpdir) / "thumb.jpg"
            
            # Create test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=5:size=640x480:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            result = await generate_thumbnail(video_path, thumb_path)
            
            assert result.exists()
            assert result.stat().st_size > 0
    
    @pytest.mark.asyncio
    async def test_generate_thumbnail_at_timestamp(self):
        """Test generating thumbnail at specific timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            thumb_path = Path(tmpdir) / "thumb.jpg"
            
            # Create 10-second test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=10:size=640x480:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            result = await generate_thumbnail(
                video_path,
                thumb_path,
                timestamp=5.0,
            )
            
            assert result.exists()
    
    @pytest.mark.asyncio
    async def test_generate_thumbnail_custom_size(self):
        """Test generating thumbnail with custom dimensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            thumb_path = Path(tmpdir) / "thumb.jpg"
            
            # Create test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=3:size=640x480:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            result = await generate_thumbnail(
                video_path,
                thumb_path,
                size=(640, 360),
            )
            
            assert result.exists()
    
    @pytest.mark.asyncio
    async def test_generate_png_thumbnail(self):
        """Test generating PNG thumbnail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            thumb_path = Path(tmpdir) / "thumb.png"
            
            # Create test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=3:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            result = await generate_thumbnail(video_path, thumb_path)
            
            assert result.exists()
            assert result.suffix == ".png"
    
    @pytest.mark.asyncio
    async def test_generate_thumbnail_nonexistent_video(self):
        """Test generating thumbnail from non-existent video."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "nonexistent.mp4"
            thumb_path = Path(tmpdir) / "thumb.jpg"
            
            with pytest.raises(FileNotFoundError):
                await generate_thumbnail(video_path, thumb_path)
    
    @pytest.mark.asyncio
    async def test_generate_thumbnail_directory(self):
        """Test generating thumbnail from directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir)
            thumb_path = Path(tmpdir) / "thumb.jpg"
            
            with pytest.raises(ThumbnailError):
                await generate_thumbnail(video_path, thumb_path)
    
    @pytest.mark.asyncio
    async def test_generate_thumbnail_creates_output_dir(self):
        """Test that output directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            output_dir = Path(tmpdir) / "nested" / "thumbs"
            thumb_path = output_dir / "thumb.jpg"
            
            # Create test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=2:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            result = await generate_thumbnail(video_path, thumb_path)
            
            assert result.exists()
            assert output_dir.exists()
    
    @pytest.mark.asyncio
    async def test_generate_thumbnail_corrupted_video(self):
        """Test handling corrupted video file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "corrupted.mp4"
            thumb_path = Path(tmpdir) / "thumb.jpg"
            
            # Write invalid data
            video_path.write_bytes(b"This is not a video file")
            
            with pytest.raises(FFmpegError):
                await generate_thumbnail(video_path, thumb_path)


class TestGenerateThumbnailsAtIntervals:
    """Test generating multiple thumbnails at intervals."""
    
    @pytest.mark.asyncio
    async def test_generate_multiple_thumbnails(self):
        """Test generating thumbnails at regular intervals."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            output_dir = Path(tmpdir) / "thumbs"
            
            # Create 20-second test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=20:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            results = await generate_thumbnails_at_intervals(
                video_path,
                output_dir,
                interval=5.0,  # Every 5 seconds
            )
            
            # Should have thumbnails at ~5s, ~10s, ~15s (0s is skipped)
            assert len(results) >= 2
            for thumb in results:
                assert thumb.exists()
    
    @pytest.mark.asyncio
    async def test_generate_thumbnails_max_limit(self):
        """Test limiting number of thumbnails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            output_dir = Path(tmpdir) / "thumbs"
            
            # Create 30-second test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=30:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            results = await generate_thumbnails_at_intervals(
                video_path,
                output_dir,
                interval=1.0,  # Every second would be 30 thumbnails
                max_thumbnails=3,
            )
            
            assert len(results) <= 3
    
    @pytest.mark.asyncio
    async def test_generate_thumbnails_short_video(self):
        """Test generating thumbnails from short video."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            output_dir = Path(tmpdir) / "thumbs"
            
            # Create 2-second test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=2:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            results = await generate_thumbnails_at_intervals(
                video_path,
                output_dir,
                interval=10.0,  # Longer than video duration
            )
            
            # Should still get at least one thumbnail (middle of video)
            assert len(results) >= 1
    
    @pytest.mark.asyncio
    async def test_generate_thumbnails_no_duration(self):
        """Test handling video with no duration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            output_dir = Path(tmpdir) / "thumbs"
            
            # Create a very short video that might have issues
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=0.1:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            # Should handle gracefully - either succeed or raise appropriate error
            try:
                results = await generate_thumbnails_at_intervals(
                    video_path,
                    output_dir,
                    interval=1.0,
                )
                # If it succeeds, we should get results or an empty list
                assert isinstance(results, list)
            except ThumbnailError:
                pass  # Also acceptable


class TestGeneratePreviewGrid:
    """Test preview grid generation."""
    
    @pytest.mark.asyncio
    async def test_generate_basic_grid(self):
        """Test generating a basic preview grid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            grid_path = Path(tmpdir) / "grid.jpg"
            
            # Create 30-second test video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=30:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            result = await generate_preview_grid(
                video_path,
                grid_path,
                grid_size=(2, 2),
                size=(640, 480),
            )
            
            assert result.exists()
            assert result.stat().st_size > 0
    
    @pytest.mark.asyncio
    async def test_generate_grid_nonexistent_video(self):
        """Test generating grid from non-existent video."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "nonexistent.mp4"
            grid_path = Path(tmpdir) / "grid.jpg"
            
            with pytest.raises(FileNotFoundError):
                await generate_preview_grid(video_path, grid_path)
    
    @pytest.mark.asyncio
    async def test_generate_grid_no_duration(self):
        """Test generating grid from video with very short duration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test.mp4"
            grid_path = Path(tmpdir) / "grid.jpg"
            
            # Create a very short video (0.1 seconds)
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "testsrc=duration=0.1:size=320x240:rate=30",
                "-pix_fmt", "yuv420p",
                str(video_path),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            
            # Very short videos might either fail or succeed with warnings
            # Both outcomes are acceptable as long as they don't crash
            try:
                result = await generate_preview_grid(video_path, grid_path)
                # If it succeeds, result should exist
                if result.exists():
                    assert result.stat().st_size > 0
            except (ThumbnailError, FFmpegError):
                pass  # Expected for very short videos
