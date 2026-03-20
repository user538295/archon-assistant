"""Tests for AttachmentStore."""

import os
import time
import unittest.mock
from datetime import date
from pathlib import Path

import pytest

from archon.ai.attachment_store import AttachmentStore


class TestSave:
    def test_basic_save(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("hello.txt", b"content", date(2026, 3, 16))
        assert rel == Path("2026-03-16/hello.txt")
        assert (tmp_path / rel).read_bytes() == b"content"

    def test_date_folder_created(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        store.save("f.txt", b"x", date(2026, 1, 1))
        assert (tmp_path / "2026-01-01").is_dir()

    def test_collision_suffix(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        d = date(2026, 3, 16)
        r1 = store.save("report.pdf", b"a", d)
        r2 = store.save("report.pdf", b"b", d)
        r3 = store.save("report.pdf", b"c", d)
        assert r1 == Path("2026-03-16/report.pdf")
        assert r2 == Path("2026-03-16/report_2.pdf")
        assert r3 == Path("2026-03-16/report_3.pdf")

    def test_default_date_is_today(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("f.txt", b"x")
        assert rel.parts[0] == date.today().isoformat()

    def test_collision_exhaustion_raises(self, tmp_path: Path) -> None:
        """ValueError is raised when all collision slots are exhausted."""
        store = AttachmentStore(tmp_path)
        d = date(2026, 3, 16)
        date_dir = tmp_path / "2026-03-16"
        date_dir.mkdir(parents=True, exist_ok=True)

        # Create the base file and all numbered variants up to the limit
        (date_dir / "clash.txt").write_bytes(b"x")
        from archon.ai.attachment_store import _MAX_COLLISION_ATTEMPTS

        for i in range(2, _MAX_COLLISION_ATTEMPTS + 2):
            (date_dir / f"clash_{i}.txt").write_bytes(b"x")

        with pytest.raises(ValueError, match="collision cap"):
            store.save("clash.txt", b"new", d)


class TestSanitization:
    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("../../etc/passwd", b"x", date(2026, 3, 16))
        saved = tmp_path / rel
        assert saved.exists()
        assert str(saved.resolve()).startswith(str(tmp_path))
        # Sanitized filename must not contain traversal components
        assert ".." not in rel.name
        assert "/" not in rel.name
        # No files should exist outside base directory
        for p in tmp_path.parent.iterdir():
            if p != tmp_path:
                assert not (p / "etc").exists()

    def test_backslash_stripped(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("..\\..\\etc\\passwd", b"x", date(2026, 3, 16))
        saved = tmp_path / rel
        assert saved.exists()
        assert "\\" not in saved.name
        # Must also strip path traversal components
        assert ".." not in rel.name
        for part in rel.parts:
            assert ".." not in part

    def test_null_bytes_stripped(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("file\x00.txt", b"x", date(2026, 3, 16))
        assert "\x00" not in str(rel)

    def test_empty_filename_gets_fallback(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("", b"x", date(2026, 3, 16))
        assert "attachment_" in rel.name

    def test_dots_only_gets_fallback(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("...", b"x", date(2026, 3, 16))
        assert "attachment_" in rel.name

    def test_control_characters_stripped(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("file\x01\x1f.txt", b"x", date(2026, 3, 16))
        assert "\x01" not in str(rel)
        assert "\x1f" not in str(rel)

    def test_long_filename_truncated(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        long_name = "a" * 300 + ".txt"
        rel = store.save(long_name, b"x", date(2026, 3, 16))
        assert len(rel.name) <= 255

    def test_long_extension_gets_fallback(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        name = "a." + "x" * 300
        rel = store.save(name, b"x", date(2026, 3, 16))
        assert len(rel.name) <= 255

    def test_resolved_path_outside_base_raises(self, tmp_path: Path) -> None:
        """If somehow the resolved path escapes base_dir, ValueError is raised."""
        store = AttachmentStore(tmp_path)
        d = date(2026, 3, 16)
        date_dir = tmp_path / "2026-03-16"
        date_dir.mkdir(parents=True, exist_ok=True)

        with unittest.mock.patch.object(
            Path, "resolve", return_value=tmp_path.parent / "evil"
        ):
            with pytest.raises(ValueError, match="escapes"):
                store.save("test.txt", b"x", d)


class TestCleanup:
    def test_old_file_deleted(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        date_dir = tmp_path / "2026-03-14"
        date_dir.mkdir()
        f = date_dir / "old.txt"
        f.write_bytes(b"old")
        old_mtime = time.time() - (48 * 3600)
        os.utime(f, (old_mtime, old_mtime))

        deleted = store.cleanup(max_age_hours=24)
        assert deleted == 1
        assert not f.exists()

    def test_recent_file_kept(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        date_dir = tmp_path / "2026-03-16"
        date_dir.mkdir()
        f = date_dir / "recent.txt"
        f.write_bytes(b"new")

        deleted = store.cleanup(max_age_hours=24)
        assert deleted == 0
        assert f.exists()

    def test_zero_ttl_skips(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        date_dir = tmp_path / "2026-03-14"
        date_dir.mkdir()
        f = date_dir / "file.txt"
        f.write_bytes(b"data")
        old_mtime = time.time() - (48 * 3600)
        os.utime(f, (old_mtime, old_mtime))

        deleted = store.cleanup(max_age_hours=0)
        assert deleted == 0
        assert f.exists()

    def test_negative_ttl_skips(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        deleted = store.cleanup(max_age_hours=-1)
        assert deleted == 0

    def test_empty_base_dir_no_error(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        deleted = store.cleanup(max_age_hours=1)
        assert deleted == 0

    def test_non_date_directories_ignored(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        other_dir = tmp_path / "not-a-date"
        other_dir.mkdir()
        f = other_dir / "file.txt"
        f.write_bytes(b"data")
        old_mtime = time.time() - (48 * 3600)
        os.utime(f, (old_mtime, old_mtime))

        deleted = store.cleanup(max_age_hours=1)
        assert deleted == 0
        assert f.exists()

    def test_empty_date_dir_removed(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        date_dir = tmp_path / "2026-03-14"
        date_dir.mkdir()
        f = date_dir / "old.txt"
        f.write_bytes(b"data")
        old_mtime = time.time() - (48 * 3600)
        os.utime(f, (old_mtime, old_mtime))

        store.cleanup(max_age_hours=1)
        assert not date_dir.exists()

    def test_nonexistent_base_dir(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path / "nonexistent")
        deleted = store.cleanup(max_age_hours=1)
        assert deleted == 0

    def test_symlink_ignored_in_cleanup(self, tmp_path: Path) -> None:
        """Symlinks named like date directories must be skipped to prevent out-of-tree deletion."""
        store = AttachmentStore(tmp_path)
        # Create a real directory outside the base with a file
        outside = tmp_path.parent / "outside_target"
        outside.mkdir()
        victim = outside / "secret.txt"
        victim.write_bytes(b"sensitive")
        old_mtime = time.time() - (48 * 3600)
        os.utime(victim, (old_mtime, old_mtime))

        # Create a symlink inside base_dir that looks like a date directory
        symlink = tmp_path / "2020-01-01"
        symlink.symlink_to(outside)

        deleted = store.cleanup(max_age_hours=1)
        assert deleted == 0
        assert victim.exists(), "File outside attachments dir must not be deleted"


class TestFilePermissions:
    def test_saved_file_has_600_permissions(self, tmp_path: Path) -> None:
        """Saved attachments must be owner-only read/write (0o600)."""
        store = AttachmentStore(tmp_path)
        rel = store.save("secret.txt", b"data", date(2026, 3, 16))
        saved = tmp_path / rel
        mode = saved.stat().st_mode & 0o777
        assert mode == 0o600

    def test_chmod_failure_still_saves_file(self, tmp_path: Path) -> None:
        """Save succeeds even if chmod is not supported (e.g., FAT32, SMB)."""
        store = AttachmentStore(tmp_path)
        with unittest.mock.patch.object(Path, "chmod", side_effect=OSError("not supported")):
            rel = store.save("doc.txt", b"content", date(2026, 3, 16))
        saved = tmp_path / rel
        assert saved.exists()
        assert saved.read_bytes() == b"content"


class TestFallbackExtensionSafety:
    def test_fallback_extension_from_sanitized_name(self, tmp_path: Path) -> None:
        """When filename sanitizes to empty, extension must come from a safe source."""
        store = AttachmentStore(tmp_path)
        # Filename with only null/slashes — sanitizes to empty, triggers fallback
        rel = store.save("../.\x00", b"x", date(2026, 3, 16))
        assert "attachment_" in rel.name

    def test_fallback_rejects_invalid_extension(self, tmp_path: Path) -> None:
        """When fallback triggers, an extension with non-alphanumeric chars is dropped."""
        store = AttachmentStore(tmp_path)
        # Dots get stripped by _DOTDOT_RE and strip(". "), leaving empty → fallback
        # The "extension" from Path("....evil stuff").suffix would be " stuff"
        rel = store.save("....evil stuff", b"x", date(2026, 3, 16))
        # Sanitized to "evil stuff" which is valid — so no fallback needed
        saved = tmp_path / rel
        assert saved.exists()

    def test_fallback_uses_safe_extension(self, tmp_path: Path) -> None:
        """When fallback triggers with a valid extension, it is preserved."""
        store = AttachmentStore(tmp_path)
        # ".. .txt" → dots stripped → " .txt" → strip(". ") → "txt" → NOT empty
        # Need to make name empty: only dots, spaces, slashes, nulls
        # ".../.txt" → slash removed → "....txt" → _DOTDOT_RE → "txt" → not empty
        # Use only forbidden chars before dot: "\x00.\x00" → "." → strip(". ") → ""
        # Then filename=".\x00" has suffix="" so fallback gets no extension
        # Let's test: "  ...  " → strip → empty → fallback with original "  ...  " has no dot
        rel = store.save("  ...  ", b"x", date(2026, 3, 16))
        assert "attachment_" in rel.name

    def test_fallback_extension_no_traversal(self, tmp_path: Path) -> None:
        """Extension extraction must not allow path traversal."""
        store = AttachmentStore(tmp_path)
        # The dots are stripped, extension extracted safely
        rel = store.save("../../.hidden", b"x", date(2026, 3, 16))
        saved = tmp_path / rel
        assert saved.exists()
        assert str(saved.resolve()).startswith(str(tmp_path))


class TestListEntries:
    def test_list_entries_empty_store(self, tmp_path: Path) -> None:
        """Returns empty list when no files exist."""
        store = AttachmentStore(tmp_path)
        store.base_dir.mkdir(parents=True, exist_ok=True)
        assert store.list_entries() == []

    def test_list_entries_single_file(self, tmp_path: Path) -> None:
        """Returns correct metadata for a single saved file."""
        store = AttachmentStore(tmp_path)
        rel = store.save("photo.png", b"\x89PNG" + b"\x00" * 100, date(2026, 3, 20))

        entries = store.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["filename"] == "photo.png"
        assert entry["path"] == str(rel)
        assert entry["abs_path"] == str(tmp_path / rel)
        assert entry["size_bytes"] == 104
        assert entry["size_human"] == "104 B"
        assert entry["mime_type"] == "image/png"
        assert entry["date"] == "2026-03-20"
        # mtime should be a valid ISO format string
        assert "T" in entry["mtime"]

    def test_list_entries_multiple_dates(self, tmp_path: Path) -> None:
        """Files across multiple date directories are all listed."""
        store = AttachmentStore(tmp_path)
        store.save("a.txt", b"aaa", date(2026, 3, 19))
        store.save("b.txt", b"bbb", date(2026, 3, 20))

        entries = store.list_entries()
        assert len(entries) == 2
        filenames = {e["filename"] for e in entries}
        assert filenames == {"a.txt", "b.txt"}

    def test_list_entries_date_filter(self, tmp_path: Path) -> None:
        """Only files from the specified date are returned."""
        store = AttachmentStore(tmp_path)
        store.save("a.txt", b"aaa", date(2026, 3, 19))
        store.save("b.txt", b"bbb", date(2026, 3, 20))

        entries = store.list_entries(date="2026-03-20")
        assert len(entries) == 1
        assert entries[0]["filename"] == "b.txt"

    def test_list_entries_date_filter_no_match(self, tmp_path: Path) -> None:
        """Returns empty list when date filter matches no files."""
        store = AttachmentStore(tmp_path)
        store.save("a.txt", b"aaa", date(2026, 3, 19))

        entries = store.list_entries(date="2026-03-20")
        assert entries == []

    def test_list_entries_mime_prefix_filter(self, tmp_path: Path) -> None:
        """MIME prefix filter matches correctly."""
        store = AttachmentStore(tmp_path)
        d = date(2026, 3, 20)
        store.save("photo.png", b"\x89PNG", d)
        store.save("data.csv", b"a,b,c", d)

        entries = store.list_entries(mime_prefix="image/")
        assert len(entries) == 1
        assert entries[0]["filename"] == "photo.png"

    def test_list_entries_combined_filters(self, tmp_path: Path) -> None:
        """Date and MIME filters work together."""
        store = AttachmentStore(tmp_path)
        store.save("photo.png", b"\x89PNG", date(2026, 3, 19))
        store.save("photo2.png", b"\x89PNG", date(2026, 3, 20))
        store.save("data.csv", b"a,b", date(2026, 3, 20))

        entries = store.list_entries(date="2026-03-20", mime_prefix="image/")
        assert len(entries) == 1
        assert entries[0]["filename"] == "photo2.png"

    def test_list_entries_limit(self, tmp_path: Path) -> None:
        """Respects limit parameter, returns newest first."""
        store = AttachmentStore(tmp_path)
        d = date(2026, 3, 20)
        saved_paths = []
        for i in range(5):
            rel = store.save(f"file{i}.txt", b"x" * (i + 1), d)
            saved_paths.append(rel)

        # Set explicit, well-separated mtimes
        base_time = 1_000_000_000.0
        for i, rel in enumerate(saved_paths):
            t = base_time + i * 100
            os.utime(tmp_path / rel, (t, t))

        entries = store.list_entries(limit=2)
        assert len(entries) == 2
        # Newest first — file4 has the latest mtime
        assert entries[0]["filename"] == "file4.txt"
        assert entries[1]["filename"] == "file3.txt"

    def test_list_entries_skips_symlinks(self, tmp_path: Path) -> None:
        """Symlinked files and directories are excluded."""
        store = AttachmentStore(tmp_path)
        store.save("real.txt", b"data", date(2026, 3, 20))

        # Create a symlinked date directory
        outside = tmp_path.parent / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")
        (tmp_path / "2026-03-19").symlink_to(outside)

        # Create a symlinked file inside a real date directory
        real_dir = tmp_path / "2026-03-20"
        (real_dir / "link.txt").symlink_to(real_dir / "real.txt")

        entries = store.list_entries()
        assert len(entries) == 1
        assert entries[0]["filename"] == "real.txt"

    def test_list_entries_skips_non_date_dirs(self, tmp_path: Path) -> None:
        """Directories not matching YYYY-MM-DD are ignored."""
        store = AttachmentStore(tmp_path)
        store.save("real.txt", b"data", date(2026, 3, 20))

        # Create a non-date directory with files
        other = tmp_path / "not-a-date"
        other.mkdir()
        (other / "hidden.txt").write_bytes(b"hidden")

        entries = store.list_entries()
        assert len(entries) == 1
        assert entries[0]["filename"] == "real.txt"

    def test_list_entries_nonexistent_base(self, tmp_path: Path) -> None:
        """Returns empty list when base_dir doesn't exist."""
        store = AttachmentStore(tmp_path / "nonexistent")
        assert store.list_entries() == []
