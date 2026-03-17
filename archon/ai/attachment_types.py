"""Attachment metadata types and helpers."""

from __future__ import annotations

import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("archon")

# Control characters and newlines that must not appear in MIME types
_MIME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class AttachmentInfo:
    """Metadata for a saved attachment."""

    path: Path
    mime_type: str
    size_bytes: int
    dimensions: tuple[int, int] | None = None  # (width, height), images only
    resized_from: tuple[int, int] | None = None  # original dimensions before resize
    resized_path: Path | None = None  # path to resized copy if created
    resized_size_bytes: int | None = None  # size of resized copy if created


def detect_mime_type(filename: str, telegram_mime: str | None = None) -> str:
    """Detect MIME type for a file.

    Uses Telegram's reported MIME if available, falls back to
    ``mimetypes.guess_type()`` based on extension.  Returns
    ``"application/octet-stream"`` when nothing matches.
    """
    if telegram_mime:
        sanitized = _MIME_CONTROL_RE.sub("", telegram_mime).strip()
        if sanitized:
            return sanitized
        # MIME was all control characters — fall through to extension detection
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def format_file_size(size_bytes: int) -> str:
    """Format byte count as human-readable string.

    Returns strings like ``"45 KB"``, ``"2.3 MB"``, ``"1.1 GB"``.
    Values under 1 KB are shown as bytes (e.g., ``"512 B"``).
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        kb = size_bytes / 1024
        return f"{kb:.0f} KB" if kb >= 10 else f"{kb:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        mb = size_bytes / (1024 * 1024)
        return f"{mb:.1f} MB"
    else:
        gb = size_bytes / (1024 * 1024 * 1024)
        return f"{gb:.1f} GB"


def check_file_size(file_size: int | None, max_bytes: int = 20 * 1024 * 1024) -> str | None:
    """Check if a file exceeds the size limit.

    Returns a user-friendly error message if ``file_size`` exceeds
    ``max_bytes``.  Returns ``None`` if the size is acceptable or
    unknown (``file_size is None`` — allow download attempt).
    """
    if file_size is None:
        logger.debug("file_size is None — Telegram 20MB server-side limit applies")
        return None
    if file_size <= max_bytes:
        return None
    size_str = format_file_size(file_size)
    limit_str = format_file_size(max_bytes)
    return f"File is too large ({size_str}). Telegram limits bot downloads to {limit_str}."
