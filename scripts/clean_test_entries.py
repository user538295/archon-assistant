#!/usr/bin/env python3
"""Remove live-test entries from Archon history log files.

Test entries are identified by the header pattern:
    ## HH:MM:SS UTC · User 100 · /tmp

Each entry is a block starting with a `## ` header and extending until the
next `## ` header or end of file.  Trailing `---` separators belonging to
a removed entry are also stripped.

Usage:
    # Dry run — summary only
    python scripts/clean_test_entries.py ~/.archon/history/sessions/2026-02-23.md

    # Dry run — show every matched block with line ranges
    python scripts/clean_test_entries.py -v ~/.archon/history/sessions/2026-0*.md

    # Dump matched blocks with full content
    python scripts/clean_test_entries.py --dump ~/.archon/history/sessions/2026-02-23.md

    # Apply changes
    python scripts/clean_test_entries.py --apply ~/.archon/history/sessions/2026-02-23.md
"""

import argparse
import re
from pathlib import Path

# Matches the test-entry header: ## <time> UTC · User 100 · /tmp
TEST_HEADER_RE = re.compile(r"^## .+ UTC · User 100 · /tmp\s*$")


def parse_entries(
    text: str,
) -> tuple[str, list[tuple[int, int, str, str]]]:
    """Split a history file into (file_header, [(start, end, header, body), ...]).

    Line numbers are 1-based. The file header is everything before the first
    `## ` line (typically the title).
    """
    lines = text.split("\n")
    file_header_lines: list[str] = []
    entries: list[tuple[int, int, str, str]] = []

    current_header: str | None = None
    current_body: list[str] = []
    current_start: int = 1

    for i, line in enumerate(lines, start=1):
        if line.startswith("## "):
            if current_header is not None:
                entries.append((
                    current_start,
                    i - 1,
                    current_header,
                    "\n".join(current_body),
                ))
            else:
                file_header_lines = current_body[:]
            current_header = line
            current_body = []
            current_start = i
        else:
            current_body.append(line)

    # Flush last entry
    if current_header is not None:
        entries.append((
            current_start,
            len(lines),
            current_header,
            "\n".join(current_body),
        ))
    else:
        file_header_lines = current_body[:]

    return "\n".join(file_header_lines), entries


def is_test_entry(header: str) -> bool:
    return bool(TEST_HEADER_RE.match(header))


def rebuild(file_header: str, entries: list[tuple[int, int, str, str]]) -> str:
    """Reassemble the file from header + kept entries, cleaning up separators."""
    parts = [file_header]
    for _, _, header, body in entries:
        parts.append(header)
        parts.append(body)
    text = "\n".join(parts)
    # Collapse 3+ consecutive blank lines into 2
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # Remove orphaned separators (--- surrounded by blank lines with no content)
    text = re.sub(r"\n---\n+---\n", "\n---\n", text)
    # Strip trailing whitespace
    text = text.rstrip() + "\n"
    return text


def process_file(
    path: Path, *, apply: bool, verbose: bool, dump: bool
) -> dict[str, int]:
    """Process a single history file. Returns stats dict."""
    original = path.read_text(encoding="utf-8")
    file_header, entries = parse_entries(original)

    kept: list[tuple[int, int, str, str]] = []
    matches: list[tuple[int, int, str, str]] = []

    for start, end, header, body in entries:
        if is_test_entry(header):
            matches.append((start, end, header, body))
        else:
            kept.append((start, end, header, body))

    if dump and matches:
        for start, end, header, body in matches:
            print(f"# {path.name}:{start}-{end}:\n")
            print(header)
            print(body)
            print("---\n")
    elif verbose and matches:
        for start, end, _header, _body in matches:
            print(f"  {path.name}:{start}-{end}")

    if apply and matches:
        cleaned = rebuild(file_header, kept)
        path.write_text(cleaned, encoding="utf-8")

    return {
        "total": len(entries),
        "removed": len(matches),
        "kept": len(kept),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove live-test entries (User 100 · /tmp) from Archon history logs."
    )
    parser.add_argument(
        "files", nargs="+", type=Path, help="History .md files to process"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually modify files (default: dry run)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Show each matched block with line range",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        default=False,
        help="Print full content of each matched block",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Mode: {mode}\n")

    grand_total = 0
    grand_removed = 0

    for path in sorted(args.files):
        if not path.is_file():
            print(f"  SKIP {path} (not a file)")
            continue

        stats = process_file(path, apply=args.apply, verbose=args.verbose, dump=args.dump)
        grand_total += stats["total"]
        grand_removed += stats["removed"]

        if stats["removed"] > 0:
            label = "removed" if args.apply else "would remove"
            print(
                f"  {path.name}: {stats['total']} entries, "
                f"{label} {stats['removed']}, keeping {stats['kept']}"
            )
        else:
            print(f"  {path.name}: {stats['total']} entries, clean")

    print(
        f"\nTotal: {grand_total} entries, {grand_removed} test entries "
        f"{'removed' if args.apply else 'to remove'}"
    )


if __name__ == "__main__":
    main()
