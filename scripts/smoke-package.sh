#!/usr/bin/env bash
# Install the wheel into a fresh environment, outside the source checkout.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
wheel="${1:-}"
if [ -z "$wheel" ]; then
  version="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$ROOT/src/ici/__init__.py")"
  wheel="$ROOT/dist/ici-$version-py3-none-any.whl"
fi
wheel="$(realpath "$wheel")"
task_dir="$(mktemp -d "${TMPDIR:-/tmp}/ici-package-smoke.XXXXXX")"
trap 'rm -rf "$task_dir"' EXIT
uv venv --python 3.10 "$task_dir/venv"
uv pip install --python "$task_dir/venv/bin/python" "$wheel"
mkdir "$task_dir/project"
printf 'answer = 42\n' > "$task_dir/project/example.py"
cd "$task_dir/project"
unset PYTHONPATH ICI_BUNDLE_ROOT
"$task_dir/venv/bin/ici" --version
"$task_dir/venv/bin/ici" analyze --plan --json > plan.json
set +e
"$task_dir/venv/bin/ici" analyze --json --events events.jsonl > result.json
code=$?
set -e
case "$code" in 0|1|3) ;; *) echo "Unexpected analyze exit: $code" >&2; exit 1 ;; esac
"$task_dir/venv/bin/python" - <<'PY'
import json
from pathlib import Path
from ici import __version__
result = json.loads(Path("result.json").read_text())
assert result["schema_id"] == "ici.next.run" and result["schema_version"] == 2
assert result["producer"]["ici_version"] == __version__
assert any(c["check_id"] == "python.line" and c["state"] == "SUCCEEDED" for c in result["checks"])
events = [json.loads(line) for line in Path("events.jsonl").read_text().splitlines()]
assert events and all(event["run_id"] == result["run_id"] for event in events)
assert not Path("ici.toml").exists()
print("isolated wheel install, analyze, result v2 and event v1: verified")
PY
"$task_dir/venv/bin/ici" report --result result.json --out report.html
test -s report.html
