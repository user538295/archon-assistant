"""S4.2 — launchd plist template structural tests."""
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLIST = REPO_ROOT / "scripts" / "com.archon.assistant.plist"

# ──────────────────────────────────────────────────────────────────
# Plist template
# ──────────────────────────────────────────────────────────────────


def test_plist_file_exists() -> None:
    assert PLIST.exists(), f"{PLIST} not found"


def test_plist_is_valid_xml() -> None:
    ET.parse(PLIST)  # raises if malformed


def _plist_keys() -> set[str]:
    """Return the top-level keys of the plist <dict>."""
    root = ET.parse(PLIST).getroot()
    top_dict = root.find("dict")
    assert top_dict is not None
    return {el.text for el in top_dict.findall("key") if el.text}


def test_plist_has_label() -> None:
    assert "Label" in _plist_keys()


def test_plist_has_program_arguments() -> None:
    assert "ProgramArguments" in _plist_keys()


def test_plist_has_working_directory() -> None:
    assert "WorkingDirectory" in _plist_keys()


def test_plist_has_keep_alive_true() -> None:
    root = ET.parse(PLIST).getroot()
    top_dict = root.find("dict")
    assert top_dict is not None
    keys = list(top_dict)
    for i, el in enumerate(keys):
        if el.tag == "key" and el.text == "KeepAlive":
            assert keys[i + 1].tag == "true", "KeepAlive must be <true/>"
            return
    raise AssertionError("KeepAlive key not found in plist")


def test_plist_has_log_file_placeholder() -> None:
    content = PLIST.read_text()
    assert "__LOG_FILE__" in content, "Plist must use __LOG_FILE__ placeholder"


def test_plist_has_archon_dir_placeholder() -> None:
    content = PLIST.read_text()
    assert "__ARCHON_DIR__" in content, "Plist must use __ARCHON_DIR__ placeholder"


def test_plist_has_uv_path_placeholder() -> None:
    content = PLIST.read_text()
    assert "__UV_PATH__" in content, "Plist must use __UV_PATH__ placeholder"
