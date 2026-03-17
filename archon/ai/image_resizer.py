"""Image resize utility for attachments."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("archon")

_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
_MAX_EDGE_PX = 8000
_TARGET_LONG_EDGE = 1568
_MAX_PIXEL_COUNT = 100_000_000  # 100M pixels (~400 MB RGBA) — decompression bomb guard

try:
    from PIL import ExifTags, Image

    _PILLOW_AVAILABLE = True
    # Process-global: aligns Pillow's built-in decompression bomb guard with our threshold.
    Image.MAX_IMAGE_PIXELS = _MAX_PIXEL_COUNT
except ImportError:
    _PILLOW_AVAILABLE = False

_MAX_COLLISION_ATTEMPTS = 1000


@dataclass
class ResizeResult:
    """Result of an image resize check."""

    resized: bool
    source_path: Path
    original_dimensions: tuple[int, int] | None = None
    new_dimensions: tuple[int, int] | None = None
    resized_path: Path | None = None


class ImageResizer:
    """Resize images exceeding size/dimension thresholds."""

    def resize_if_needed(self, image_path: Path) -> ResizeResult:
        """Check image and resize if it exceeds thresholds.

        Order of operations:
        1. Try to open with Pillow — if fails, return resized=False
        2. Apply EXIF orientation before reading dimensions
        3. Check if animated — if so, skip resize, return dimensions only
        4. Check thresholds: >5 MB OR >8000 px on any edge → resize
        5. Resize to long edge ≤1568 px, preserving aspect ratio
        6. Save resized copy as {stem}_resized{suffix}

        Returns:
            ResizeResult with resize status and paths.
        """
        if not _PILLOW_AVAILABLE:
            logger.warning("Pillow not available — skipping image resize")
            return ResizeResult(resized=False, source_path=image_path)

        try:
            img = Image.open(image_path)
        except Exception:
            logger.warning("Cannot open image %s — skipping resize", image_path)
            return ResizeResult(resized=False, source_path=image_path)

        try:
            # Apply EXIF orientation
            try:
                img = _apply_exif_orientation(img)
            except Exception:
                pass  # EXIF parsing can fail on malformed data

            width, height = img.size

            # Decompression bomb guard
            if width * height > _MAX_PIXEL_COUNT:
                logger.warning(
                    "Image %s exceeds pixel limit (%dx%d = %d px > %d) — skipping",
                    image_path, width, height, width * height, _MAX_PIXEL_COUNT,
                )
                return ResizeResult(resized=False, source_path=image_path)

            original_dims = (width, height)

            # Check if animated
            if getattr(img, "is_animated", False):
                return ResizeResult(
                    resized=False,
                    source_path=image_path,
                    original_dimensions=original_dims,
                )

            # Check thresholds
            file_size = image_path.stat().st_size
            needs_resize = (
                file_size > _MAX_FILE_SIZE
                or width > _MAX_EDGE_PX
                or height > _MAX_EDGE_PX
            )

            if not needs_resize:
                return ResizeResult(
                    resized=False,
                    source_path=image_path,
                    original_dimensions=original_dims,
                )

            # Resize: long edge → _TARGET_LONG_EDGE, preserve aspect ratio
            long_edge = max(width, height)
            scale = _TARGET_LONG_EDGE / long_edge
            new_w = int(width * scale)
            new_h = int(height * scale)

            resized = img.resize((new_w, new_h), Image.LANCZOS)
            try:
                # Save resized copy, avoiding filename collisions
                resized_path = (
                    image_path.parent
                    / f"{image_path.stem}_resized{image_path.suffix}"
                )
                counter = 1
                while resized_path.exists():
                    if counter > _MAX_COLLISION_ATTEMPTS:
                        raise ValueError(
                            f"Filename collision limit ({_MAX_COLLISION_ATTEMPTS}) "
                            f"exceeded for {image_path.name}"
                        )
                    resized_path = (
                        image_path.parent
                        / f"{image_path.stem}_resized_{counter}{image_path.suffix}"
                    )
                    counter += 1

                # Determine save format
                fmt = img.format or _guess_format(image_path.suffix)
                save_kwargs: dict[str, object] = {}
                if fmt == "JPEG":
                    if resized.mode == "RGBA":
                        resized = resized.convert("RGB")
                    save_kwargs["quality"] = 85
                elif fmt == "WEBP":
                    save_kwargs["quality"] = 85

                resized.save(resized_path, format=fmt, **save_kwargs)
            finally:
                resized.close()

            return ResizeResult(
                resized=True,
                source_path=image_path,
                original_dimensions=original_dims,
                new_dimensions=(new_w, new_h),
                resized_path=resized_path,
            )
        finally:
            img.close()


def _apply_exif_orientation(img: "Image.Image") -> "Image.Image":  # type: ignore[name-defined]
    """Apply EXIF orientation tag and return corrected image."""
    from PIL import ImageOps

    return ImageOps.exif_transpose(img)  # type: ignore[return-value]


def _guess_format(suffix: str) -> str:
    """Guess PIL format from file suffix."""
    mapping = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".gif": "GIF",
        ".webp": "WEBP",
        ".bmp": "BMP",
    }
    return mapping.get(suffix.lower(), "PNG")
