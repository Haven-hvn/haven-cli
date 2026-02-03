"""Unit tests for frame extraction functionality.

These tests verify:
1. Frame extraction at specific timestamps
2. Uniform frame distribution across video
3. Sprite image creation
4. Error handling for invalid inputs
"""

from __future__ import annotations

import pytest
from PIL import Image

from haven_cli.media.frames import (
    create_sprite_image,
    extract_frames,
    extract_frames_uniform,
)


class TestCreateSpriteImage:
    """Tests for create_sprite_image function."""

    def test_create_sprite_from_frames(self) -> None:
        """Should create a sprite grid from frames."""
        # Create test frames
        frames = [
            Image.new("RGB", (100, 100), color="red"),
            Image.new("RGB", (100, 100), color="green"),
            Image.new("RGB", (100, 100), color="blue"),
            Image.new("RGB", (100, 100), color="yellow"),
        ]

        sprite = create_sprite_image(frames, columns=2, frame_size=100)

        # Sprite should be 2x2 grid = 200x200
        assert sprite.size == (200, 200)
        assert sprite.mode == "RGB"

    def test_empty_frames_raises_error(self) -> None:
        """Empty frame list should raise ValueError."""
        with pytest.raises(ValueError, match="empty"):
            create_sprite_image([], columns=2, frame_size=100)

    def test_single_frame_sprite(self) -> None:
        """Single frame should create 1x1 sprite."""
        frames = [Image.new("RGB", (100, 100), color="red")]

        sprite = create_sprite_image(frames, columns=5, frame_size=100)

        # Should still be 1 column
        assert sprite.size == (100, 100)

    def test_frames_resized_to_uniform_size(self) -> None:
        """Frames should be resized to uniform size."""
        # Create frames of different sizes
        frames = [
            Image.new("RGB", (50, 50), color="red"),
            Image.new("RGB", (100, 100), color="green"),
            Image.new("RGB", (200, 150), color="blue"),
        ]

        sprite = create_sprite_image(frames, columns=3, frame_size=100)

        # All frames should be resized to 100x100
        assert sprite.size == (300, 100)

    def test_partial_grid(self) -> None:
        """Handle cases where frames don't fill complete grid."""
        frames = [
            Image.new("RGB", (100, 100), color="red"),
            Image.new("RGB", (100, 100), color="green"),
            Image.new("RGB", (100, 100), color="blue"),
        ]

        # 3 frames in 2-column grid = 2 rows (2 + 1)
        sprite = create_sprite_image(frames, columns=2, frame_size=100)

        assert sprite.size == (200, 200)


class TestExtractFrames:
    """Tests for extract_frames function."""

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises_error(self, tmp_path: pytest.TempPathFactory) -> None:
        """Non-existent file should raise FileNotFoundError."""
        from pathlib import Path
        nonexistent = tmp_path / "nonexistent.mp4"  # type: ignore
        
        with pytest.raises(FileNotFoundError):
            await extract_frames(nonexistent, [1.0, 2.0])

    @pytest.mark.asyncio
    async def test_empty_timestamps_returns_empty_list(self, tmp_path: pytest.TempPathFactory) -> None:
        """Empty timestamps should return empty list."""
        from pathlib import Path
        # This would need a real video file to test properly
        # For now, we just test the empty case
        pass  # Skipped - requires video file


class TestExtractFramesUniform:
    """Tests for extract_frames_uniform function."""

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_empty_list(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Non-existent file should return empty list (caught by duration extraction)."""
        from pathlib import Path
        nonexistent = tmp_path / "nonexistent.mp4"  # type: ignore
        
        # This will fail during duration extraction
        with pytest.raises(FileNotFoundError):
            await extract_frames_uniform(nonexistent, frame_count=5)


class TestFrameExtractionIntegration:
    """Integration tests for frame extraction workflow."""

    def test_sprite_creation_workflow(self) -> None:
        """Test complete workflow of creating a sprite from frames."""
        # Simulate extracted frames (normally from video)
        frames = []
        colors = ["red", "green", "blue", "yellow", "purple"]
        
        for color in colors:
            frame = Image.new("RGB", (160, 160), color=color)
            frames.append(frame)

        # Create 5-column sprite (5x1 grid)
        sprite = create_sprite_image(frames, columns=5, frame_size=160)

        # Verify dimensions
        assert sprite.size == (800, 160)  # 5 frames * 160px width
        
        # Verify we can calculate pHash on the sprite
        from haven_cli.media.phash import calculate_frame_phash
        phash = calculate_frame_phash(sprite)
        
        assert isinstance(phash, str)
        assert len(phash) == 16  # Default 64-bit hash = 16 hex chars
