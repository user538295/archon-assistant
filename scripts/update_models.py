# scripts/update_models.py — called by release.sh to sync AVAILABLE_MODELS
import sys, json, re, pathlib, os

_root = pathlib.Path(__file__).resolve().parent.parent

data = json.load(sys.stdin)
models_raw = {
    m['id']: m.get('context_window', 200_000)
    for m in data.get('data', [])
    if m['id'].startswith('claude-') and not m['id'].endswith('-latest')
}
ids = sorted(models_raw)

# ── Build new constants.py block ─────────────────────────────────────────────
if ids:
    dict_body = ''.join(f'    "{i}": {models_raw[i]:_},\n' for i in ids)
    new_block = f'AVAILABLE_MODELS: dict[str, int] = {{\n{dict_body}}}'
else:
    new_block = 'AVAILABLE_MODELS: dict[str, int] = {}'

# ── Update constants.py ──────────────────────────────────────────────────────
_default = _root / 'archon' / 'ai' / 'constants.py'
f = pathlib.Path(os.environ.get('UPDATE_MODELS_CONSTANTS_PATH', str(_default)))
txt = f.read_text()
new_txt = re.sub(
    r'AVAILABLE_MODELS(?:\s*:\s*dict\[str,\s*int\])?\s*=\s*(?:\[[^\]]*\]|\{[^}]*\})',
    new_block,
    txt,
    flags=re.DOTALL,
)
if new_txt != txt:
    f.write_text(new_txt)
    print(f"Updated AVAILABLE_MODELS in constants.py")
elif 'AVAILABLE_MODELS' in txt:
    print("AVAILABLE_MODELS already up to date")
else:
    print("Warning: AVAILABLE_MODELS pattern not found in constants.py — no update performed", file=sys.stderr)
    sys.exit(1)

# ── Update config.toml.example ───────────────────────────────────────────────
_TOML_COMMENT = (
    "# Each entry enables a model and declares its context window size (tokens).\n"
    "# update_models.py keeps this list current on every release.\n"
    "# Add custom or proxy models here with their actual context window.\n"
)

if ids:
    table_body = ''.join(f'"{i}" = {models_raw[i]:_}\n' for i in ids)
else:
    table_body = ''

_example_default = _root / 'examples' / 'config.toml.example'
example_path = pathlib.Path(os.environ.get('UPDATE_MODELS_EXAMPLE_PATH', str(_example_default)))
if example_path.exists():
    toml_txt = example_path.read_text()

    # Detect old inline-list format: `available = [...]` under [models] and convert
    if re.search(r'available\s*=\s*\[', toml_txt):
        # Remove the old available = [...] key entirely from under [models]
        toml_txt = re.sub(
            r'available\s*=\s*\[[^\]]*\]\s*\n?',
            '',
            toml_txt,
            flags=re.DOTALL,
        )
        # Remove any existing [models.available] section (avoid duplicates)
        toml_txt = re.sub(
            r'\[models\.available\].*?(?=\n\[|\Z)',
            '',
            toml_txt,
            flags=re.DOTALL,
        )
        # Insert [models.available] table right after the [models] section header line
        toml_txt = re.sub(
            r'(\[models\].*?)(?=\n\[|\Z)',
            lambda m: m.group(1).rstrip('\n') + f'\n\n[models.available]\n{_TOML_COMMENT}{table_body}\n',
            toml_txt,
            flags=re.DOTALL,
        )
        example_path.write_text(toml_txt)
        print("Converted config.toml.example from list format to [models.available] table")
    else:
        new_toml_txt = re.sub(
            r'(\[models\.available\]).*?(?=\n\[|\Z)',
            f'[models.available]\n{_TOML_COMMENT}{table_body}\n',
            toml_txt,
            flags=re.DOTALL,
        )
        if new_toml_txt != toml_txt:
            example_path.write_text(new_toml_txt)
            print("Updated config.toml.example [models.available] table")

# ── Warn on empty result ──────────────────────────────────────────────────────
if not ids:
    print("Warning: no models found after filtering — wrote empty AVAILABLE_MODELS")
