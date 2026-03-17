"""Tests for AttachmentPromptBuilder."""

from pathlib import Path

from archon.ai.attachment_prompt import build_attachment_prompt
from archon.ai.attachment_types import AttachmentInfo


class TestSingleFile:
    def test_text_file_with_caption(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/utils.py"),
            mime_type="text/x-python",
            size_bytes=12 * 1024,
        )
        result = build_attachment_prompt([info], caption="Review this code")
        assert "[Attachment: attachments/2026-03-16/utils.py]" in result
        assert "Python file" in result
        assert "12 KB" in result
        assert "User message: Review this code" in result

    def test_pdf_has_cli_note(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/report.pdf"),
            mime_type="application/pdf",
            size_bytes=int(2.3 * 1024 * 1024),
        )
        result = build_attachment_prompt([info], caption="Summarize this report")
        assert "PDF document" in result
        assert "pdftotext" in result
        assert "Read tool will not work" in result

    def test_image_with_metadata(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/photo.jpg"),
            mime_type="image/jpeg",
            size_bytes=1200000,
            dimensions=(3024, 4032),
        )
        result = build_attachment_prompt([info], caption="What's in this image?")
        assert "JPEG image" in result
        assert "3024×4032" in result
        assert "Visual analysis is not available" in result

    def test_resized_image(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/photo.jpg"),
            mime_type="image/jpeg",
            size_bytes=5000000,
            dimensions=(1568, 1045),
            resized_from=(4000, 3000),
            resized_path=Path("attachments/2026-03-16/photo_resized.jpg"),
        )
        result = build_attachment_prompt([info])
        assert "4000×3000 (original)" in result
        assert "Resized copy:" in result
        assert "photo_resized.jpg" in result
        assert "1568×1045" in result
        assert "Visual analysis is not available." in result
        assert "CLI tools" not in result  # resized images get shorter note

    def test_resized_image_with_size(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/photo.jpg"),
            mime_type="image/jpeg",
            size_bytes=5000000,
            dimensions=(1568, 1045),
            resized_from=(4000, 3000),
            resized_path=Path("attachments/2026-03-16/photo_resized.jpg"),
            resized_size_bytes=1200000,
        )
        result = build_attachment_prompt([info])
        assert "1.1 MB" in result  # resized file size shown

    def test_no_caption_asks_user(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/data.csv"),
            mime_type="text/csv",
            size_bytes=45 * 1024,
        )
        result = build_attachment_prompt([info], caption=None)
        assert "sent this file without a message" in result
        assert "Ask what they'd like you to do" in result

    def test_video_file(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/clip.mp4"),
            mime_type="video/mp4",
            size_bytes=15 * 1024 * 1024,
        )
        result = build_attachment_prompt([info], caption="Check this video")
        assert "Video file" in result
        assert "Ask the user what they'd like you to do" in result

    def test_archive_file(self) -> None:
        info = AttachmentInfo(
            path=Path("attachments/2026-03-16/backup.zip"),
            mime_type="application/zip",
            size_bytes=8 * 1024 * 1024,
        )
        result = build_attachment_prompt([info])
        assert "ZIP archive" in result
        assert "archive file" in result


class TestMediaGroup:
    def test_multiple_files_combined(self) -> None:
        infos = [
            AttachmentInfo(
                path=Path("attachments/2026-03-16/screenshot1.png"),
                mime_type="image/png",
                size_bytes=850 * 1024,
                dimensions=(1920, 1080),
            ),
            AttachmentInfo(
                path=Path("attachments/2026-03-16/screenshot2.png"),
                mime_type="image/png",
                size_bytes=int(1.1 * 1024 * 1024),
                dimensions=(1568, 1045),
                resized_from=(4000, 3000),
                resized_path=Path("attachments/2026-03-16/screenshot2_resized.png"),
            ),
        ]
        result = build_attachment_prompt(infos, caption="Compare these two screenshots")
        assert "[Attachment: attachments/2026-03-16/screenshot1.png]" in result
        assert "[Attachment: attachments/2026-03-16/screenshot2.png]" in result
        assert "User message: Compare these two screenshots" in result
        # Two separate attachment blocks
        assert result.count("[Attachment:") == 2


class TestTypeLabels:
    def test_json_file(self) -> None:
        info = AttachmentInfo(
            path=Path("f.json"), mime_type="application/json", size_bytes=100
        )
        result = build_attachment_prompt([info], caption="parse")
        assert "JSON file" in result

    def test_unknown_mime(self) -> None:
        info = AttachmentInfo(
            path=Path("f.xyz"), mime_type="application/octet-stream", size_bytes=100
        )
        result = build_attachment_prompt([info], caption="check")
        assert "OCTET-STREAM file" in result
