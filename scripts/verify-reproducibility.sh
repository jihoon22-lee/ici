#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

status_before=$(git status --porcelain=v1 --untracked-files=all)

(
    umask 077
    SOURCE_DATE_EPOCH=1 PYTHONHASHSEED=random TZ=Pacific/Honolulu \
        ./scripts/build-pyz.sh
)
first_sha256=$(sha256sum dist/ici.pyz | awk '{print $1}')

(
    umask 002
    SOURCE_DATE_EPOCH=4102444800 PYTHONHASHSEED=123 TZ=Asia/Seoul \
        ./scripts/build-pyz.sh
)
second_sha256=$(sha256sum dist/ici.pyz | awk '{print $1}')

if [[ "$first_sha256" != "$second_sha256" ]]; then
    echo "Reproducibility failure: first=$first_sha256 second=$second_sha256" >&2
    exit 1
fi

python3 - "dist/ici.pyz" <<'PY'
import json
import stat
import sys
import zipfile
from datetime import datetime, timezone

archive = sys.argv[1]
expected_timestamp = datetime.fromtimestamp(1700000000, timezone.utc).timetuple()[:6]
with zipfile.ZipFile(archive) as bundle:
    members = bundle.infolist()
    if not members or any(member.date_time != expected_timestamp for member in members):
        raise SystemExit("ZipApp members do not use the canonical archive timestamp")
    for member in members:
        mode = member.external_attr >> 16
        if member.filename.startswith(("site-packages/", "_bootstrap/")) and mode != (
            stat.S_IFREG | 0o644
        ):
            raise SystemExit(f"ZipApp member has a non-canonical mode: {member.filename} {mode:o}")
    if "site-packages/.lock" in bundle.namelist():
        raise SystemExit("uv target lock leaked into the ZipApp")
    environment = json.loads(bundle.read("environment.json"))
    if environment.get("built_at") != "2023-11-14 22:13:20":
        raise SystemExit("shiv environment did not use the canonical archive timestamp")
PY

status_after=$(git status --porcelain=v1 --untracked-files=all)
if [[ "$status_before" != "$status_after" ]]; then
    echo "Project mutation failure: build changed tracked or untracked source state" >&2
    diff <(printf '%s\n' "$status_before") <(printf '%s\n' "$status_after") >&2 || true
    exit 1
fi

echo "Reproducible ici.pyz SHA256: $first_sha256"
echo "Project source status unchanged"
