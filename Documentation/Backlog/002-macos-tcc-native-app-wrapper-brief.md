# macOS Native App Wrapper for TCC Permissions

**Purpose**: Options analysis and implementation guide for giving `archon_server` its own TCC identity on macOS
**Audience**: Backend engineers
**Status**: Pending — research complete, not yet implemented
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

---

## Status

Pending

## Priority

P3 — Low (only relevant when a TCC-gated permission is actually required)

## Estimated Effort

Small (1–2 days, one-time setup)

## Background

macOS **TCC** (Transparency, Consent & Control) attributes permissions — Screen Recording, Accessibility, Full Disk Access, etc. — to the **responsible process**, identified by its code signature and bundle identity, not `argv[0]`. The current launchd configuration runs `uv run python main.py` directly, so any TCC prompt or System Settings → Privacy entry will show **uv** or **python**, not `archon_server`.

This does not affect Archon today (no TCC-gated permissions are required). It becomes relevant if a future feature needs, for example, screen recording for visual context or accessibility APIs for desktop automation.

## The Core Problem

macOS TCC looks at the **responsible process's code signature + bundle identity**. Changing `argv[0]` via a thin C stub changes the `ps` name but does NOT change the TCC-responsible process — the code signature of `uv` or `python` still owns the permission grant.

---

## Approach 1: Thin C Stub (no `.app` bundle)

Write a tiny C program named `archon_server` that `exec()`s into `uv`:

```c
// archon_server.c
#include <unistd.h>
#include <stdio.h>

int main(int argc, char *argv[]) {
    char *args[] = {
        "archon_server",   // argv[0] → process name in ps
        "run",
        "python",
        "main.py",
        NULL
    };

    execv("/path/to/.venv/bin/uv", args);

    perror("execv failed");
    return 1;
}
```

```bash
gcc -o archon_server archon_server.c
```

**Limitation**: changes the `ps` name, but **TCC still sees the code signature of `uv`/`python`**. Use Approach 2 if TCC ownership matters.

---

## Approach 2: `.app` Bundle with Embedded Launcher ✅ Recommended

The proper macOS way for a local daemon. A lightweight `.app` bundle with its own `Info.plist` and entitlements wraps `uv run`. The bundle binary `exec()`s into `uv`, so the same PID carries the bundle's code signature through to the Python process. TCC sees the bundle identity.

### Bundle Structure

```
archon_server.app/
├── Contents/
│   ├── Info.plist              ← defines CFBundleIdentifier (used by TCC)
│   ├── MacOS/
│   │   └── archon_server       ← compiled launcher binary (C or Swift)
│   └── Resources/
│       └── entitlements.plist
```

### `Info.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.yourname.archon-server</string>
    <key>CFBundleName</key>
    <string>archon_server</string>
    <key>CFBundleExecutable</key>
    <string>archon_server</string>
    <key>NSScreenCaptureUsageDescription</key>
    <string>Archon needs screen access to assist with your tasks.</string>
</dict>
</plist>
```

### `entitlements.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
</dict>
</plist>
```

### Launcher Binary (Swift)

```swift
// archon_server.swift
import Foundation

let uvPath = "/path/to/uv"   // or resolve from PATH dynamically
let projectPath = "/path/to/archon"

let args: [String] = [
    "archon_server",   // argv[0]
    "run", "python", "main.py"
]

// exec() replaces this process (same PID), preserving bundle identity for TCC
let cArgs = args.map { strdup($0) } + [nil]
execv(uvPath, cArgs)
```

```bash
swiftc -o archon_server.app/Contents/MacOS/archon_server archon_server.swift
```

### Code Sign (required for TCC)

```bash
# Ad-hoc signing for local use:
codesign --force --sign "-" \
  --entitlements entitlements.plist \
  archon_server.app

# For distribution, use a Developer ID:
codesign --force --sign "Developer ID Application: Your Name (TEAMID)" \
  --entitlements entitlements.plist \
  --options runtime \
  --timestamp \
  archon_server.app
```

**Result**: System Settings → Privacy → Screen Recording shows **archon_server**. ✅

### launchd Integration

Update the `ProgramArguments` in the launchd plist to point to the bundle binary:

```xml
<key>ProgramArguments</key>
<array>
    <string>/path/to/archon_server.app/Contents/MacOS/archon_server</string>
</array>
```

---

## Approach 3: PyInstaller (Automated, heavier)

```bash
uv run pyinstaller --name archon_server --onefile main.py

codesign --force --sign "-" \
  --entitlements entitlements.plist \
  dist/archon_server
```

**Downside**: ~50–100 MB bundle; must re-bundle on every code change. Suitable for distribution, not for local development.

---

## Approach 4: `py2app` (macOS-native packaging)

```bash
uv run py2app --make-setup main.py
uv run python setup.py py2app
```

Generates a full `.app` bundle. Best for end-user distribution.

---

## Comparison

| | `.app` + C/Swift stub | PyInstaller | py2app |
|---|---|---|---|
| Bundle size | ~50 KB | ~80 MB | ~80 MB |
| Code changes | Edit Python freely, re-run `uv run` | Re-bundle every time | Re-bundle every time |
| TCC ownership | ✅ Full | ✅ Full | ✅ Full |
| Process name in `ps` | `archon_server` | `archon_server` | `archon_server` |
| launchd integration | Easy | Easy | Easy |
| One-time setup complexity | Medium | Low | Low |

**Recommendation**: Use **Approach 2** (thin `.app` bundle with Swift/C `exec()` launcher). The launcher binary is compiled once, stays tiny, and Python code is edited freely — `uv run` handles the virtualenv transparently.

---

## Key Technical Notes

### `exec()` vs `spawn()`

- **`exec()`** — replaces the current process (same PID). macOS tracks the **original launcher's code signature** as the responsible process. ✅ Correct for TCC ownership.
- **`fork()` / `spawn()`** — creates a child process. Inheritance of TCC responsibility depends on `posix_spawn` attributes — more complex to set up correctly.

Always use `exec()` in the launcher binary for proper TCC identity transfer.

---

## Related Documents

- [`Documentation/Architecture/510_release_and_environment_strategy.md`](../Architecture/510_release_and_environment_strategy.md) — current macOS daemon configuration
- [macOS TCC — HackTricks](https://book.hacktricks.xyz/macos-hardening/macos-security-and-privilege-escalation/macos-security-protections/macos-tcc)
- [Entitlements — Apple Developer Documentation](https://developer.apple.com/documentation/bundleresources/entitlements)
- [OS X Code Signing with PyInstaller](https://gist.github.com/txoof/0636835d3cc65245c6288b2374799c43)
