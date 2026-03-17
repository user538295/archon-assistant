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


class TestSanitization:
    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("../../etc/passwd", b"x", date(2026, 3, 16))
        saved = tmp_path / rel
        assert saved.exists()
        assert str(saved.resolve()).startswith(str(tmp_path))

    def test_backslash_stripped(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        rel = store.save("..\\..\\etc\\passwd", b"x", date(2026, 3, 16))
        saved = tmp_path / rel
        assert saved.exists()
        assert "\\" not in saved.name

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
