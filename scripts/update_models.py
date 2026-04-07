# scripts/update_models.py — called by release.sh to sync AVAILABLE_MODELS
import sys, json, re, pathlib, os

_root = pathlib.Path(__file__).resolve().parent.parent

data = json.load(sys.stdin)
ids = sorted(set(
    m['id'] for m in data.get('data', [])
    if m['id'].startswith('claude-') and not m['id'].endswith('-latest')
))
model_list = '[' + ', '.join(f'"{i}"' for i in ids) + ']'

# ── Update constants.py ──────────────────────────────────────────────────────
_default = _root / 'archon' / 'ai' / 'constants.py'
f = pathlib.Path(os.environ.get('UPDATE_MODELS_CONSTANTS_PATH', str(_default)))
txt = f.read_text()
new_txt = re.sub(r'AVAILABLE_MODELS\s*=\s*\[[^\]]*\]', f'AVAILABLE_MODELS = {model_list}', txt)
if new_txt != txt:
    f.write_text(new_txt)
    print(f"Updated AVAILABLE_MODELS: {model_list}")
elif 'AVAILABLE_MODELS' in txt:
    print("AVAILABLE_MODELS already up to date")
else:
    print("Warning: AVAILABLE_MODELS pattern not found in constants.py — no update performed", file=sys.stderr)
    sys.exit(1)

# ── Update config.toml.example to stay in sync ──────────────────────────────
example_list = '[\n' + ''.join(f'    "{i}",\n' for i in ids) + ']'
_example_default = _root / 'examples' / 'config.toml.example'
example_path = pathlib.Path(os.environ.get('UPDATE_MODELS_EXAMPLE_PATH', str(_example_default)))
if example_path.exists():
    ex_txt = example_path.read_text()
    new_ex_txt = re.sub(
        r'(available\s*=\s*)\[[^\]]*\]',
        lambda m: m.group(1) + example_list,
        ex_txt,
        flags=re.DOTALL,
    )
    if new_ex_txt != ex_txt:
        example_path.write_text(new_ex_txt)
        print(f"Updated config.toml.example available list")
