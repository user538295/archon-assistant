# scripts/update_models.py — called by release.sh to sync AVAILABLE_MODELS
import sys, json, re, pathlib, os

data = json.load(sys.stdin)
ids = sorted(set(
    m['id'] for m in data.get('data', [])
    if m['id'].startswith('claude-') and not m['id'].endswith('-latest')
))
model_list = '[' + ', '.join(f'"{i}"' for i in ids) + ']'
_default = pathlib.Path(__file__).resolve().parent.parent / 'archon' / 'ai' / 'constants.py'
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
