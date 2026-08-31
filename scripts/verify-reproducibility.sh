#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

status_before=$(git status --porcelain=v1 --untracked-files=all)

./scripts/build-pyz.sh
first_sha256=$(sha256sum dist/ici.pyz | awk '{print $1}')

./scripts/build-pyz.sh
second_sha256=$(sha256sum dist/ici.pyz | awk '{print $1}')

if [[ "$first_sha256" != "$second_sha256" ]]; then
    echo "Reproducibility failure: first=$first_sha256 second=$second_sha256" >&2
    exit 1
fi

status_after=$(git status --porcelain=v1 --untracked-files=all)
if [[ "$status_before" != "$status_after" ]]; then
    echo "Project mutation failure: build changed tracked or untracked source state" >&2
    diff <(printf '%s\n' "$status_before") <(printf '%s\n' "$status_after") >&2 || true
    exit 1
fi

echo "Reproducible ici.pyz SHA256: $first_sha256"
echo "Project source status unchanged"
