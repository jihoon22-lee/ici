#!/usr/bin/env bash
# dist/ici.pyz 를 만든다 — 단일 독립 실행 파일.
#
#   1) uv.lock으로 고정한 runtime/package 도구를 Python 3.10 대상으로 설치
#   2) 네이티브 확장이 섞이지 않았는지 검사 (AGENTS.md 규약의 기계적 강제)
#   3) 빌드 환경 흔적 제거 (재현 가능 빌드)
#   4) shiv 로 zipapp 생성
#   5) shebang 자리에 scripts/launcher.sh 를 얹어 sh 폴리글롯으로 마감
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# The artifact must not inherit caller-controlled archive timestamps, locale,
# hash randomization, or permission masks. 1700000000 is the repository's
# documented canonical packaging epoch and is deliberately independent of a
# commit timestamp.
readonly CANONICAL_SOURCE_DATE_EPOCH=1700000000
export SOURCE_DATE_EPOCH="$CANONICAL_SOURCE_DATE_EPOCH"
export PYTHONHASHSEED=0
export PYTHONUTF8=1
export TZ=UTC
export LANG=C
export LC_ALL=C
umask 022

PY_TARGET="${ICI_BUILD_PYTHON:-3.10}"   # 산출물이 돌아야 하는 하한 = 3.10
BUILD="$ROOT/build"
SITE="$BUILD/site-packages"
TOOLS="$BUILD/package-tools"
WHEELS="$BUILD/wheels"
RUNTIME_REQUIREMENTS="$BUILD/runtime-requirements.txt"
PACKAGE_REQUIREMENTS="$BUILD/package-requirements.txt"
RAW="$BUILD/ici-raw.pyz"
OUT="$ROOT/dist/ici.pyz"

command -v uv >/dev/null || { echo "uv 가 필요합니다: https://docs.astral.sh/uv/" >&2; exit 1; }
readonly EXPECTED_UV_VERSION="0.12.5"
actual_uv_version="$(uv --version)"
actual_uv_number="$(printf '%s\n' "$actual_uv_version" | awk '{print $2}')"
if [ "$actual_uv_number" != "$EXPECTED_UV_VERSION" ]; then
    echo "uv $EXPECTED_UV_VERSION가 필요합니다: $actual_uv_version" >&2
    exit 1
fi

if [ -L "$ROOT/dist" ]; then
    echo "dist 경로는 symlink일 수 없습니다: $ROOT/dist" >&2
    exit 1
fi
if [ -e "$ROOT/dist" ] && [ ! -d "$ROOT/dist" ]; then
    echo "dist 경로는 directory여야 합니다: $ROOT/dist" >&2
    exit 1
fi

case "$ROOT" in
    /mnt/*) echo "경고: $ROOT 는 Windows 드라이브입니다. 파일 권한이 0777 로 기록됩니다." >&2
            echo "      반입용 빌드는 ~/ 아래 등 ext4 경로에서 하세요." >&2 ;;
esac

echo "[1/4] 잠긴 의존성 설치 (python $PY_TARGET 대상)"
rm -rf "$BUILD"
mkdir -p "$SITE" "$TOOLS" "$WHEELS" "$ROOT/dist"
uv export --quiet --frozen --no-dev --no-emit-project --no-header \
    --output-file "$RUNTIME_REQUIREMENTS"
uv export --quiet --frozen --only-group package --no-emit-project --no-header \
    --output-file "$PACKAGE_REQUIREMENTS"
uv pip install --quiet --python "$PY_TARGET" --target "$TOOLS" --link-mode copy \
    --require-hashes --only-binary :all: --requirements "$PACKAGE_REQUIREMENTS"
build_python="$(uv python find "$PY_TARGET")"
PYTHONPATH="$TOOLS" "$build_python" -m hatchling build \
    --target wheel --directory "$WHEELS"
wheel_count="$(find "$WHEELS" -maxdepth 1 -type f -name 'ici-*.whl' | wc -l)"
if [ "$wheel_count" -ne 1 ]; then
    echo "ici wheel이 정확히 하나여야 합니다: $wheel_count" >&2
    exit 1
fi
wheel="$(find "$WHEELS" -maxdepth 1 -type f -name 'ici-*.whl' -print)"
uv pip install --quiet --python "$PY_TARGET" --target "$SITE" --link-mode copy \
    --require-hashes --only-binary :all: --requirements "$RUNTIME_REQUIREMENTS"
uv pip install --quiet --python "$PY_TARGET" --target "$SITE" --link-mode copy \
    --no-deps --only-binary :all: "$wheel"

echo "[2/4] 순수 파이썬 검사"
impure="$(find "$SITE" \( -name '*.so' -o -name '*.pyd' -o -name '*.dylib' \) -print)"
if [ -n "$impure" ]; then
    echo "네이티브 확장이 포함됐습니다 — glibc/아키텍처에 묶여 폐쇄망에서 깨집니다:" >&2
    echo "$impure" >&2
    exit 1
fi
bad_tags="$(grep -h '^Tag:' "$SITE"/*.dist-info/WHEEL 2>/dev/null | grep -v -- '-none-any$' || true)"
if [ -n "$bad_tags" ]; then
    echo "플랫폼 종속 휠이 포함됐습니다:" >&2
    echo "$bad_tags" >&2
    exit 1
fi
if [ -d "$SITE/certifi" ]; then
    echo "certifi 가 딸려 들어왔습니다 — 사내 TLS 인터셉션 환경에서 깨집니다 (AGENTS.md)." >&2
    exit 1
fi
echo "      $(find "$SITE" -maxdepth 1 -name '*.dist-info' | wc -l) 개 배포판 전부 py3-none-any, certifi 없음"

for schema in ici-result-v3.schema.json ici-compilation-export-v1.schema.json; do
    if [ ! -f "$SITE/ici/schemas/$schema" ]; then
        echo "공개 JSON schema가 패키지에서 누락됐습니다: ici/schemas/$schema" >&2
        exit 1
    fi
done
echo "      공개 JSON schema 2개 패키징 확인"

echo "      빌드 환경 흔적 제거 (재현 가능 빌드)"
python3 - "$SITE" <<'PY'
import sys
from pathlib import Path

site = Path(sys.argv[1])
removed = set()

bindir = site / "bin"
if bindir.is_dir():
    for path in sorted(bindir.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            removed.add(path)
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    bindir.rmdir()

for name in ("direct_url.json", "uv_cache.json", "uv_build.json"):
    for path in site.glob(f"*.dist-info/{name}"):
        removed.add(path)
        path.unlink()

for record in site.glob("*.dist-info/RECORD"):
    lines = record.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not (site / ln.split(",", 1)[0]) in removed]
    record.write_text("".join(kept), encoding="utf-8")

lock = site / ".lock"
if lock.is_file():
    removed.add(lock)
    lock.unlink()

for path in sorted(site.rglob("*")):
    if path.is_symlink():
        raise SystemExit(f"symbolic link is not allowed in the ZipApp: {path}")
    if path.is_dir():
        path.chmod(0o755)
    elif path.is_file():
        path.chmod(0o644)
    else:
        raise SystemExit(f"unsupported packaged filesystem entry: {path}")
site.chmod(0o755)

print(f"      {len(removed)} 개 항목 제거")
PY

python3 - "$TOOLS" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in sorted(root.rglob("*")):
    if path.is_symlink():
        raise SystemExit(f"symbolic link is not allowed in packaging tools: {path}")
    if path.is_dir():
        path.chmod(0o755)
    elif path.is_file():
        path.chmod(0o644)
    else:
        raise SystemExit(f"unsupported packaging-tool filesystem entry: {path}")
root.chmod(0o755)
PY

echo "[3/4] shiv zipapp 생성"
PYTHONPATH="$TOOLS" "$build_python" -m shiv \
    --site-packages "$SITE" \
    --console-script ici \
    --compressed \
    --reproducible \
    -o "$RAW"

echo "[4/4] sh 런처 프리앰블 부착"
python3 scripts/assemble_pyz.py \
    --raw "$RAW" \
    --preamble "$ROOT/scripts/launcher.sh" \
    --output "$OUT" \
    --output "$ROOT/dist/ici"

printf '완료: %s (%s)\n' "$OUT" "$(du -h "$OUT" | cut -f1)"
echo "스모크 테스트: ./scripts/smoke.sh"
