"""Persistent storage for Telegram file attachments."""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from archon.ai.attachment_types import detect_mime_type, format_file_size

logger = logging.getLogger("archon")

# Characters forbidden in saved filenames
_SANITIZE_RE = re.compile(r"[\x00/\\]")
_DOTDOT_RE = re.compile(r"\.\.+")
_CONTROL_RE = re.compile(r"[\x01-\x1f\x7f]")
_MAX_FILENAME_LEN = 255
_MAX_COLLISION_ATTEMPTS = 10000
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
        try:
            dest.chmod(0o600)
        except OSError:
            logger.warning("Could not set permissions on %s — filesystem may not support chmod", dest)
        logger.info("Saved attachment: %s (%d bytes)", dest, len(data))
        return dest.relative_to(self._base)

    def _sanitize(self, filename: str) -> str:
        """Sanitize a filename for safe storage."""
        name = _SANITIZE_RE.sub("", filename)
        name = _DOTDOT_RE.sub("", name)
        name = _CONTROL_RE.sub("", name)
        name = name.strip(". ")

        if not name:
            # Extract extension from the original filename safely
            raw_suffix = Path(filename).suffix if "." in filename else ""
            # Validate extension: only allow alphanumeric chars and a leading dot
            ext = raw_suffix if re.fullmatch(r"\.[a-zA-Z0-9]+", raw_suffix) else ""
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
        deleted = 0

        for entry in self._base.iterdir():
            if entry.is_symlink():
                continue
            if not entry.is_dir() or not _DATE_DIR_RE.match(entry.name):
                continue
            for fpath in entry.iterdir():
                if fpath.is_symlink():
                    continue
                if fpath.is_file() and fpath.stat().st_mtime < cutoff:
                    fpath.unlink()
                    logger.info("Cleaned up attachment: %s", fpath)
                    deleted += 1
            # Remove empty date directory
            if entry.is_dir() and not any(entry.iterdir()):
                entry.rmdir()
                logger.debug("Removed empty date directory: %s", entry)

        return deleted

    def list_entries(
        self,
        *,
        date: str | None = None,
        mime_prefix: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List stored attachments with metadata.

        Args:
            date: Optional YYYY-MM-DD filter — only files from this date.
            mime_prefix: Optional MIME prefix filter (e.g. ``"image/"``).
            limit: Maximum entries to return (default 50).

        Returns:
            List of dicts sorted by mtime descending (filename as tie-breaker),
            each with keys: filename, path, abs_path, size_bytes, size_human,
            mime_type, date, mtime.
        """
        if not self._base.exists():
            return []

        limit = max(limit, 0)

        entries: list[tuple[float, str, dict[str, Any]]] = []
        for dir_entry in self._base.iterdir():
            if dir_entry.is_symlink():
                continue
            if not dir_entry.is_dir() or not _DATE_DIR_RE.match(dir_entry.name):
                continue
            if date is not None and dir_entry.name != date:
                continue

            for fpath in dir_entry.iterdir():
                if fpath.is_symlink():
                    continue
                if not fpath.is_file():
                    continue

                mime = detect_mime_type(fpath.name)
                if mime_prefix is not None and not mime.startswith(mime_prefix):
                    continue

                try:
                    stat = fpath.stat()
                except OSError:
                    logger.debug("Skipping inaccessible file: %s", fpath)
                    continue
                rel = fpath.relative_to(self._base)
                mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                entry = {
                    "filename": fpath.name,
                    "path": str(rel),
                    "abs_path": str(fpath),
                    "size_bytes": stat.st_size,
                    "size_human": format_file_size(stat.st_size),
                    "mime_type": mime,
                    "date": dir_entry.name,
                    "mtime": mtime_dt.isoformat(),
                }
                entries.append((-stat.st_mtime, fpath.name, entry))

        entries.sort(key=lambda t: (t[0], t[1]))
        return [t[2] for t in entries[:limit]]

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
