#!/usr/bin/env bash
# dist/ici.pyz 를 만든다 — 단일 독립 실행 파일.
#
#   1) 3.10 을 대상으로 의존성을 풀어 build/site-packages 에 설치
#   2) 네이티브 확장이 섞이지 않았는지 검사 (AGENTS.md 규약의 기계적 강제)
#   3) 빌드 환경 흔적 제거 (재현 가능 빌드)
#   4) shiv 로 zipapp 생성
#   5) shebang 자리에 scripts/launcher.sh 를 얹어 sh 폴리글롯으로 마감
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY_TARGET="${ICI_BUILD_PYTHON:-3.10}"   # 산출물이 돌아야 하는 하한 = 3.10
BUILD="$ROOT/build"
SITE="$BUILD/site-packages"
RAW="$BUILD/ici-raw.pyz"
OUT="$ROOT/dist/ici.pyz"

command -v uv >/dev/null || { echo "uv 가 필요합니다: https://docs.astral.sh/uv/" >&2; exit 1; }

case "$ROOT" in
    /mnt/*) echo "경고: $ROOT 는 Windows 드라이브입니다. 파일 권한이 0777 로 기록됩니다." >&2
            echo "      반입용 빌드는 ~/ 아래 등 ext4 경로에서 하세요." >&2 ;;
esac

echo "[1/4] 의존성 설치 (python $PY_TARGET 대상)"
rm -rf "$BUILD"
mkdir -p "$SITE" "$ROOT/dist"
uv pip install --quiet --python "$PY_TARGET" --target "$SITE" "$ROOT"

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

print(f"      {len(removed)} 개 항목 제거")
PY

echo "[3/4] shiv zipapp 생성"
uv run --quiet --with shiv -- shiv \
    --site-packages "$SITE" \
    --console-script ici \
    --compressed \
    --reproducible \
    -o "$RAW"

echo "[4/4] sh 런처 프리앰블 부착"
python3 - "$RAW" "$ROOT/scripts/launcher.sh" "$OUT" <<'PY'
import sys

raw_path, preamble_path, out_path = sys.argv[1:4]
body = open(raw_path, "rb").read()
if body.startswith(b"#!"):
    body = body[body.index(b"\n") + 1:]
if not body.startswith(b"PK\x03\x04"):
    sys.exit("zip 시그니처가 아닙니다 — shiv 산출물이 예상과 다릅니다")
preamble = open(preamble_path, "rb").read()
if not preamble.endswith(b"\n"):
    sys.exit("launcher.sh 가 개행으로 끝나야 합니다")
with open(out_path, "wb") as fh:
    fh.write(preamble + body)
PY
chmod +x "$OUT"
cp -f "$OUT" "$ROOT/dist/ici"

printf '완료: %s (%s)\n' "$OUT" "$(du -h "$OUT" | cut -f1)"
echo "스모크 테스트: ./scripts/smoke.sh"
