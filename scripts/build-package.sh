#!/usr/bin/env bash
# Build standard Python release artifacts with the locked packaging backend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export SOURCE_DATE_EPOCH=1700000000
uv run --frozen --group package --python 3.10 python -m hatchling build -t wheel -t sdist
(cd dist && sha256sum ici-*.whl ici-*.tar.gz > ici-package.sha256)
