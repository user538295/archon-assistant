# Native App Wrapper for `archon_server`

> How to make `archon_server` appear as a named process in macOS — owning its own TCC permissions (e.g. screen recording) — while still running `uv run python main.py` under the hood.

## The Core Problem

macOS **TCC** (Transparency, Consent & Control) attributes permissions to the **responsible process** — identified by its **code signature + bundle identity**, not by `argv[0]`. So if `uv run python main.py` is the entry point, the TCC dialog will show **uv** or **python** in System Settings → Privacy, not `archon_server`.

---

## Approach 1: Thin C Stub (Simplest, no `.app` bundle)

Write a tiny C program named `archon_server` that `exec()`s into `uv`:

```c
// archon_server.c
#include <unistd.h>
#include <stdio.h>

int main(int argc, char *argv[]) {
    char *args[] = {
        "archon_server",   // argv[0] → process name in `ps`
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

**Limitation**: `argv[0]` changes the `ps` name, but **TCC still sees the code signature of `uv`/`python`**. The responsible process for TCC is determined by the *binary's code signature*, not `argv[0]`. Use Approach 2 if TCC ownership matters.

---

## Approach 2: `.app` Bundle with Embedded Launcher ✅ Recommended

The proper macOS way for a local daemon. Create a lightweight `.app` bundle with your own `Info.plist` and entitlements that wraps `uv run`.

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

// exec() replaces this process (same PID), preserving our bundle identity for TCC
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

---

## Approach 3: PyInstaller (Automated, heavier)

```bash
uv run pyinstaller --name archon_server --onefile main.py

codesign --force --sign "-" \
  --entitlements entitlements.plist \
  dist/archon_server
```

**Downside**: ~50–100 MB bundle, must re-bundle on every code change. Suitable for distribution, not ideal for local development iteration.

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

**Recommendation for Archon**: Use **Approach 2** (thin `.app` bundle with Swift/C `exec()` launcher). The launcher binary is compiled once, stays tiny, and Python code is edited freely — `uv run` handles the virtualenv transparently.

---

## Key Technical Notes

### `exec()` vs `spawn()`

- **`exec()`** — replaces the current process (same PID). macOS tracks the **original launcher's code signature** as the responsible process. ✅ Correct for TCC ownership.
- **`fork()` / `spawn()`** — creates a child process. The child may or may not inherit the parent's TCC responsibility depending on `posix_spawn` attributes. More complex to set up correctly.

Always use `exec()` in the launcher binary for proper TCC identity transfer.

### launchd Integration

The `.app` bundle can be referenced directly in a `launchd` plist:

```xml
<key>ProgramArguments</key>
<array>
    <string>/path/to/archon_server.app/Contents/MacOS/archon_server</string>
</array>
```

---

## References

- [macOS TCC — HackTricks](https://book.hacktricks.xyz/macos-hardening/macos-security-and-privilege-escalation/macos-security-protections/macos-tcc)
- [Entitlements — Apple Developer Documentation](https://developer.apple.com/documentation/bundleresources/entitlements)
- [Signing and notarizing a Python macOS app](https://haim.dev/posts/2020-08-08-python-macos-app/)
- [OS X Code Signing with PyInstaller (2025)](https://gist.github.com/txoof/0636835d3cc65245c6288b2374799c43)
- [py2app documentation](https://py2app.readthedocs.io/)
- [Ghostty: use launch helper to shed responsible process bit](https://github.com/ghostty-org/ghostty/issues/9263)
- [Why bother with argv[0]?](https://www.wietzebeukema.nl/blog/why-bother-with-argv0)
