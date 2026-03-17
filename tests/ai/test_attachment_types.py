"""Tests for attachment_types module."""

from pathlib import Path

import pytest

from archon.ai.attachment_types import (
    AttachmentInfo,
    check_file_size,
    detect_mime_type,
    format_file_size,
)


class TestDetectMimeType:
    def test_known_extension_py(self) -> None:
        assert detect_mime_type("utils.py") == "text/x-python"

    def test_known_extension_pdf(self) -> None:
        assert detect_mime_type("report.pdf") == "application/pdf"

    def test_known_extension_jpg(self) -> None:
        mime = detect_mime_type("photo.jpg")
        assert mime in ("image/jpeg",)

    def test_known_extension_csv(self) -> None:
        assert detect_mime_type("data.csv") == "text/csv"

    def test_unknown_extension(self) -> None:
        assert detect_mime_type("file.xyz123") == "application/octet-stream"

    def test_no_extension(self) -> None:
        assert detect_mime_type("Makefile") == "application/octet-stream"

    def test_telegram_mime_overrides(self) -> None:
        # Telegram says it's a PDF even though extension is .txt
        assert detect_mime_type("file.txt", telegram_mime="application/pdf") == "application/pdf"

    def test_telegram_mime_none_falls_back(self) -> None:
        assert detect_mime_type("script.js", telegram_mime=None) != "application/octet-stream"

    def test_telegram_mime_empty_string_falls_back(self) -> None:
        # Empty string is falsy — should fall back
        result = detect_mime_type("report.pdf", telegram_mime="")
        assert result == "application/pdf"


class TestFormatFileSize:
    def test_bytes(self) -> None:
        assert format_file_size(0) == "0 B"
        assert format_file_size(512) == "512 B"
        assert format_file_size(1023) == "1023 B"

    def test_kilobytes(self) -> None:
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(5 * 1024) == "5.0 KB"
        assert format_file_size(45 * 1024) == "45 KB"
        assert format_file_size(999 * 1024) == "999 KB"

    def test_megabytes(self) -> None:
        assert format_file_size(1024 * 1024) == "1.0 MB"
        assert format_file_size(int(2.3 * 1024 * 1024)) == "2.3 MB"
        assert format_file_size(12 * 1024 * 1024) == "12.0 MB"

    def test_gigabytes(self) -> None:
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"
        assert format_file_size(int(1.1 * 1024 * 1024 * 1024)) == "1.1 GB"

    def test_boundary_kb_to_mb(self) -> None:
        # Just under 1 MB
        assert "KB" in format_file_size(1024 * 1024 - 1)
        # Exactly 1 MB
        assert "MB" in format_file_size(1024 * 1024)


class TestAttachmentInfo:
    def test_basic_construction(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/file.txt"),
            mime_type="text/plain",
            size_bytes=1024,
        )
        assert info.path == Path("attachments/2026-03-16/file.txt")
        assert info.mime_type == "text/plain"
        assert info.size_bytes == 1024
        assert info.dimensions is None
        assert info.resized_from is None
        assert info.resized_path is None

    def test_image_with_dimensions(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/photo.jpg"),
            mime_type="image/jpeg",
            size_bytes=1200000,
            dimensions=(3024, 4032),
        )
        assert info.dimensions == (3024, 4032)

    def test_resized_image(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/photo.jpg"),
            mime_type="image/jpeg",
            size_bytes=5000000,
            dimensions=(1568, 1045),
            resized_from=(4000, 3000),
            resized_path=Path("attachments/2026-03-16/photo_resized.jpg"),
        )
        assert info.resized_from == (4000, 3000)
        assert info.resized_path == Path("attachments/2026-03-16/photo_resized.jpg")

    def test_all_fields(self) -> None:
        info = AttachmentInfo(
            path=Path("a.png"),
            mime_type="image/png",
            size_bytes=100,
            dimensions=(800, 600),
            resized_from=(1600, 1200),
            resized_path=Path("a_resized.png"),
        )
        assert info.dimensions == (800, 600)
        assert info.resized_from == (1600, 1200)
        assert info.resized_path == Path("a_resized.png")


class TestCheckFileSize:
    def test_under_limit(self) -> None:
        assert check_file_size(1024) is None

    def test_at_limit(self) -> None:
        assert check_file_size(20 * 1024 * 1024) is None

    def test_over_limit(self) -> None:
        result = check_file_size(25 * 1024 * 1024)
        assert result is not None
        assert "too large" in result
        assert "25" in result  # size in MB

    def test_none_size_allows_download(self) -> None:
        assert check_file_size(None) is None

    def test_custom_max_bytes(self) -> None:
        assert check_file_size(500, max_bytes=1000) is None
        result = check_file_size(1500, max_bytes=1000)
        assert result is not None
        assert "too large" in result

    def test_zero_size(self) -> None:
        assert check_file_size(0) is None

    def test_exactly_one_over(self) -> None:
        limit = 20 * 1024 * 1024
        assert check_file_size(limit) is None
        result = check_file_size(limit + 1)
        assert result is not None
