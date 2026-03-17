"""Persistent storage for Telegram file attachments."""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path

logger = logging.getLogger("archon")

# Characters forbidden in saved filenames
_SANITIZE_RE = re.compile(r"[\x00/\\]")
_DOTDOT_RE = re.compile(r"\.\.+")
_CONTROL_RE = re.compile(r"[\x01-\x1f\x7f]")
_MAX_FILENAME_LEN = 255
_MAX_COLLISION_ATTEMPTS = 10000


class AttachmentStore:
    """Saves attachments to date-based subdirectories with sanitized filenames."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir).resolve()

    @property
    def base_dir(self) -> Path:
        return self._base

    def save(
        self, filename: str, data: bytes, target_date: date | None = None
    ) -> Path:
        """Save attachment data to disk.

        Args:
            filename: Original filename from Telegram (will be sanitized).
            data: Raw file bytes.
            target_date: Date for subdirectory (defaults to today).

        Returns:
            Path to saved file, relative to ``base_dir``.

        Raises:
            ValueError: If the resolved path escapes ``base_dir``.
        """
        if target_date is None:
            target_date = date.today()

        safe_name = self._sanitize(filename)
        date_dir = self._base / target_date.isoformat()
        date_dir.mkdir(parents=True, exist_ok=True)

        dest = self._resolve_collision(date_dir, safe_name)

        # Validate resolved path is within base_dir
        resolved = dest.resolve()
        if not resolved.is_relative_to(self._base):
            raise ValueError(
                f"Resolved path {resolved} escapes attachments directory {self._base}"
            )

        dest.write_bytes(data)
        logger.info("Saved attachment: %s (%d bytes)", dest, len(data))
        return dest.relative_to(self._base)

    def _sanitize(self, filename: str) -> str:
        """Sanitize a filename for safe storage."""
        name = _SANITIZE_RE.sub("", filename)
        name = _DOTDOT_RE.sub("", name)
        name = _CONTROL_RE.sub("", name)
        name = name.strip(". ")

        if not name:
            ext = Path(filename).suffix if "." in filename else ""
            name = f"attachment_{int(time.time())}{ext}"

        if len(name) > _MAX_FILENAME_LEN:
            stem = Path(name).stem
            suffix = Path(name).suffix
            if len(suffix) >= _MAX_FILENAME_LEN:
                # Extension alone exceeds limit — use fallback
                name = f"attachment_{int(time.time())}"[:_MAX_FILENAME_LEN]
            else:
                max_stem = _MAX_FILENAME_LEN - len(suffix)
                name = stem[:max_stem] + suffix

        return name

    def cleanup(self, max_age_hours: float) -> int:
        """Delete files older than max_age_hours based on mtime.

        Only operates on directories matching YYYY-MM-DD pattern within
        base_dir.  Removes empty date directories after cleanup.

        Args:
            max_age_hours: Maximum file age in hours.  Skips if <= 0.

        Returns:
            Number of deleted files.
        """
        if max_age_hours <= 0:
            return 0
        if not self._base.exists():
            return 0

        cutoff = time.time() - (max_age_hours * 3600)
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        deleted = 0

        for entry in self._base.iterdir():
            if not entry.is_dir() or not date_pattern.match(entry.name):
                continue
            for fpath in entry.iterdir():
                if fpath.is_file() and fpath.stat().st_mtime < cutoff:
                    fpath.unlink()
                    logger.info("Cleaned up attachment: %s", fpath)
                    deleted += 1
            # Remove empty date directory
            if entry.is_dir() and not any(entry.iterdir()):
                entry.rmdir()
                logger.debug("Removed empty date directory: %s", entry)

        return deleted

    def _resolve_collision(self, directory: Path, filename: str) -> Path:
        """Find a non-colliding path, adding numeric suffix if needed."""
        dest = directory / filename
        if not dest.exists():
            return dest

        stem = Path(filename).stem
        suffix = Path(filename).suffix
        for counter in range(2, _MAX_COLLISION_ATTEMPTS + 2):
            candidate = directory / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
        raise ValueError(
            f"Filename collision cap ({_MAX_COLLISION_ATTEMPTS}) exhausted for {filename!r}"
        )
