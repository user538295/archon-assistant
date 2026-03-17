"""Tests for ImageResizer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from archon.ai.image_resizer import ImageResizer, ResizeResult, _MAX_PIXEL_COUNT


def _create_test_image(
    path: Path,
    size: tuple[int, int] = (800, 600),
    fmt: str = "JPEG",
    fill_color: str = "red",
) -> Path:
    """Create a test image file."""
    img = Image.new("RGB", size, fill_color)
    img.save(path, format=fmt)
    img.close()
    return path


def _create_large_image(path: Path, size: tuple[int, int] = (4000, 3000)) -> Path:
    """Create a large image that exceeds dimension threshold."""
    return _create_test_image(path, size=size)


def _create_animated_gif(path: Path) -> Path:
    """Create a simple animated GIF."""
    frames = [Image.new("RGB", (100, 100), c) for c in ["red", "green", "blue"]]
    frames[0].save(
        path, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0
    )
    for f in frames:
        f.close()
    return path


def _create_webp_with_alpha(path: Path, size: tuple[int, int] = (9000, 6000)) -> Path:
    """Create a WebP image with alpha channel that needs resizing."""
    img = Image.new("RGBA", size, (255, 0, 0, 128))
    img.save(path, format="WEBP")
    img.close()
    return path


class TestResizeIfNeeded:
    def test_small_image_unchanged(self, tmp_path: Path) -> None:
        img_path = _create_test_image(tmp_path / "small.jpg", size=(800, 600))
        resizer = ImageResizer()
        result = resizer.resize_if_needed(img_path)
        assert not result.resized
        assert result.original_dimensions == (800, 600)
        assert result.resized_path is None

    def test_large_dimension_resized(self, tmp_path: Path) -> None:
        img_path = _create_large_image(tmp_path / "big.jpg", size=(9000, 6000))
        resizer = ImageResizer()
        result = resizer.resize_if_needed(img_path)
        assert result.resized
        assert result.original_dimensions == (9000, 6000)
        assert result.new_dimensions is not None
        assert max(result.new_dimensions) <= 1568
        assert result.resized_path is not None
        assert result.resized_path.exists()
        assert "_resized" in result.resized_path.name

    def test_aspect_ratio_preserved(self, tmp_path: Path) -> None:
        img_path = _create_test_image(tmp_path / "wide.jpg", size=(9000, 3000))
        resizer = ImageResizer()
        result = resizer.resize_if_needed(img_path)
        assert result.resized
        assert result.new_dimensions is not None
        w, h = result.new_dimensions
        original_ratio = 9000 / 3000
        new_ratio = w / h
        assert abs(original_ratio - new_ratio) < 0.01

    def test_resized_suffix(self, tmp_path: Path) -> None:
        img_path = _create_test_image(tmp_path / "photo.jpg", size=(9000, 6000))
        resizer = ImageResizer()
        result = resizer.resize_if_needed(img_path)
        assert result.resized_path is not None
        assert result.resized_path.name == "photo_resized.jpg"

    def test_animated_gif_skipped(self, tmp_path: Path) -> None:
        gif_path = _create_animated_gif(tmp_path / "anim.gif")
        resizer = ImageResizer()
        result = resizer.resize_if_needed(gif_path)
        assert not result.resized
        assert result.original_dimensions is not None
        assert result.resized_path is None

    def test_corrupted_image_graceful(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "corrupt.jpg"
        bad_path.write_bytes(b"not an image")
        resizer = ImageResizer()
        result = resizer.resize_if_needed(bad_path)
        assert not result.resized
        assert result.original_dimensions is None

    def test_webp_alpha_preserved(self, tmp_path: Path) -> None:
        img_path = _create_webp_with_alpha(tmp_path / "alpha.webp")
        resizer = ImageResizer()
        result = resizer.resize_if_needed(img_path)
        assert result.resized
        assert result.resized_path is not None
        with Image.open(result.resized_path) as resized:
            assert resized.mode == "RGBA"

    def test_pillow_unavailable_skips(self, tmp_path: Path) -> None:
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"fake")
        resizer = ImageResizer()
        with patch("archon.ai.image_resizer._PILLOW_AVAILABLE", False):
            result = resizer.resize_if_needed(img_path)
        assert not result.resized
        assert result.original_dimensions is None

    def test_decompression_bomb_rejected(self, tmp_path: Path) -> None:
        """Images exceeding the pixel limit are rejected gracefully."""
        resizer = ImageResizer()
        img_path = tmp_path / "bomb.png"
        # Create a small image, then mock its size to simulate a bomb
        _create_test_image(img_path, size=(100, 100), fmt="PNG")

        mock_img = MagicMock()
        mock_img.size = (100_000, 100_000)  # 10 billion pixels
        mock_img.is_animated = False

        with (
            patch.object(Image, "open", return_value=mock_img),
            patch("archon.ai.image_resizer._apply_exif_orientation", return_value=mock_img),
        ):
            result = resizer.resize_if_needed(img_path)
            assert not result.resized
            assert result.original_dimensions is None
            mock_img.close.assert_called_once()

    def test_resized_filename_collision(self, tmp_path: Path) -> None:
        """When _resized path already exists, a numeric suffix should be added."""
        # Use dimensions that exceed 8000px edge but stay under pixel limit
        img_path = _create_test_image(tmp_path / "photo.jpg", size=(8500, 4000))
        resizer = ImageResizer()

        # First resize creates photo_resized.jpg
        result1 = resizer.resize_if_needed(img_path)
        assert result1.resized
        assert result1.resized_path is not None
        assert result1.resized_path.name == "photo_resized.jpg"

        # Second resize should create photo_resized_1.jpg (collision avoidance)
        result2 = resizer.resize_if_needed(img_path)
        assert result2.resized
        assert result2.resized_path is not None
        assert result2.resized_path.name == "photo_resized_1.jpg"

        # Third resize should create photo_resized_2.jpg
        result3 = resizer.resize_if_needed(img_path)
        assert result3.resized
        assert result3.resized_path is not None
        assert result3.resized_path.name == "photo_resized_2.jpg"

    def test_collision_loop_capped(self, tmp_path: Path) -> None:
        """Collision loop must not exceed cap; raises ValueError when exhausted."""
        img_path = _create_test_image(tmp_path / "photo.jpg", size=(9000, 6000))
        resizer = ImageResizer()

        # Pre-create the base _resized path plus 1000 numbered variants
        (tmp_path / "photo_resized.jpg").touch()
        for i in range(1, 1001):
            (tmp_path / f"photo_resized_{i}.jpg").touch()

        with pytest.raises(ValueError, match="collision"):
            resizer.resize_if_needed(img_path)

    def test_max_image_pixels_aligned_with_pillow(self) -> None:
        """Image.MAX_IMAGE_PIXELS must match _MAX_PIXEL_COUNT."""
        assert Image.MAX_IMAGE_PIXELS == _MAX_PIXEL_COUNT

    def test_large_file_size_triggers_resize(self, tmp_path: Path) -> None:
        """Image with acceptable dimensions but >5 MB file size should be resized."""
        # Solid-color 4000x3000 BMP is ~36 MB uncompressed, well above 5 MB
        img_path = _create_test_image(
            tmp_path / "hefty.bmp", size=(4000, 3000), fmt="BMP", fill_color="red"
        )

        file_size = img_path.stat().st_size
        assert file_size > 5 * 1024 * 1024, (
            f"BMP file should exceed 5 MB, got {file_size / 1024 / 1024:.1f} MB"
        )

        resizer = ImageResizer()
        result = resizer.resize_if_needed(img_path)
        assert result.resized
        assert result.original_dimensions is not None
