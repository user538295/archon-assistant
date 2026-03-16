"""Build structured text prompts for file attachments."""

from __future__ import annotations

from archon.ai.attachment_types import AttachmentInfo, format_file_size

# MIME type categories
_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_PDF_MIMES = {"application/pdf"}
_VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo", "video/mpeg"}
_ARCHIVE_MIMES = {
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
    "application/x-bzip2",
}


def build_attachment_prompt(
    attachments: list[AttachmentInfo],
    caption: str | None = None,
) -> str:
    """Build a structured prompt from attachment metadata.

    Args:
        attachments: One or more attachment info objects.
        caption: User's caption text (Telegram caption or message text).

    Returns:
        Formatted prompt string ready to pass to Claude.
    """
    blocks = [_format_single(info) for info in attachments]
    prompt = "\n\n".join(blocks)

    if caption:
        prompt += f"\n\nUser message: {caption}"
    else:
        prompt += "\n\nThe user sent this file without a message. Ask what they'd like you to do with it."

    return prompt


def _format_single(info: AttachmentInfo) -> str:
    """Format a single attachment block."""
    lines: list[str] = [f"[Attachment: {info.path}]"]

    type_label = _type_label(info)
    size_str = format_file_size(info.size_bytes)

    if info.dimensions:
        w, h = info.dimensions
        if info.resized_from:
            orig_w, orig_h = info.resized_from
            lines.append(f"Type: {type_label}, {orig_w}\u00d7{orig_h} (original)")
            if info.resized_path:
                rw, rh = info.dimensions
                if info.resized_size_bytes is not None:
                    resized_size = format_file_size(info.resized_size_bytes)
                    lines.append(f"Resized copy: {info.resized_path} ({rw}\u00d7{rh}, {resized_size})")
                else:
                    lines.append(f"Resized copy: {info.resized_path} ({rw}\u00d7{rh})")
        else:
            lines.append(f"Type: {type_label}, {size_str}, {w}\u00d7{h}")
    else:
        lines.append(f"Type: {type_label}, {size_str}")

    # Type-specific notes
    if info.mime_type in _PDF_MIMES:
        lines.append(
            "Note: PDF is a binary format \u2014 use a CLI tool (e.g., pdftotext, mutool) "
            "to extract text content. The Read tool will not work on PDFs."
        )
    elif info.mime_type in _IMAGE_MIMES:
        if info.resized_from:
            lines.append("Note: Image saved to disk. Visual analysis is not available.")
        else:
            lines.append(
                "Note: Image saved to disk. Visual analysis is not available \u2014 "
                "you can inspect metadata via CLI tools (file, identify, exiftool)."
            )
    elif info.mime_type in _VIDEO_MIMES:
        lines.append(
            "Note: The user sent a video file. Ask the user what they'd like you to do with it."
        )
    elif info.mime_type in _ARCHIVE_MIMES:
        lines.append(
            "Note: The user sent an archive file. Ask the user what they'd like you to do with it."
        )

    return "\n".join(lines)


def _type_label(info: AttachmentInfo) -> str:
    """Human-readable type label from MIME type."""
    mime = info.mime_type
    labels: dict[str, str] = {
        "image/jpeg": "JPEG image",
        "image/png": "PNG image",
        "image/gif": "GIF image",
        "image/webp": "WebP image",
        "application/pdf": "PDF document",
        "text/x-python": "Python file",
        "text/csv": "CSV file",
        "text/plain": "Text file",
        "application/json": "JSON file",
        "application/zip": "ZIP archive",
    }
    if mime in labels:
        return labels[mime]
    if mime.startswith("video/"):
        return "Video file"
    if mime.startswith("text/"):
        return "Text file"
    if mime.startswith("image/"):
        return "Image file"
    # Fallback: use the subtype from MIME
    subtype = mime.split("/")[-1] if "/" in mime else mime
    return f"{subtype.upper()} file"
