#!/usr/bin/env bash
# dist/ici.pyz 스모크 테스트
# 배포 전 산출물의 무결성을 검증한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/dist/ici.pyz"
SMOKE_REPORT="/tmp/ici_smoke_report.html"
SMOKE_JSON="/tmp/ici_smoke_report.json"

cleanup() {
    rm -f "$SMOKE_REPORT" "$SMOKE_JSON"
}
trap cleanup EXIT

[ -f "$BIN" ] || { echo "산출물이 없습니다: $BIN (먼저 ./scripts/build-pyz.sh 실행)" >&2; exit 1; }

echo "[1/4] 직접 실행 (--version, --help)"
"$BIN" --version
"$BIN" --help >/dev/null

echo "[2/4] 환경 진단 (doctor --brief)"
"$BIN" doctor --brief

echo "[3/4] 셸 환경 생성 (env --sh, env --csh)"
"$BIN" env --sh | grep -q 'export PATH='
"$BIN" env --csh | grep -q 'setenv PATH'

echo "[4/4] Python 3.10 직접 실행 테스트"
if command -v python3.10 >/dev/null 2>&1; then
    python3.10 "$BIN" --version
    echo "      python3.10 으로 직접 실행 성공"
else
    echo "      python3.10 없음 (스킵)"
fi

echo "[5/5] 산출물 무결성 및 Zero-CDN 검증"
if [ -f "$ROOT/dist/ici" ]; then
    if cmp -s "$BIN" "$ROOT/dist/ici"; then
        echo "      dist/ici == dist/ici.pyz 일치"
    else
        echo "      dist/ici 와 dist/ici.pyz 불일치 (경고)" >&2
    fi
else
    echo "      dist/ici 없음 (스킵, pyz만 검증)"
fi
# 품질 finding 때문에 verify 자체가 non-zero여도 리포터 산출물은 반드시 생성·검사한다.
# 태그 속성에 쓰인 일반 문서 링크는 허용하지만 실행/표시 asset의 외부 의존성은 거부한다.
rm -f "$SMOKE_REPORT" "$SMOKE_JSON"
set +e
"$BIN" verify --html "$SMOKE_REPORT" >/dev/null 2>&1
verify_status=$?
set -e
if [ ! -s "$SMOKE_REPORT" ]; then
    echo "      HTML 리포트가 생성되지 않음 (verify exit $verify_status)" >&2
    exit 1
fi
python3 - "$SMOKE_REPORT" <<'PY'
import re
import sys
from pathlib import Path

report = Path(sys.argv[1]).read_text(encoding="utf-8")
patterns = (
    r"<(?:script|img|iframe)\b[^>]*\bsrc\s*=\s*['\"](?:https?:)?//",
    r"<link\b[^>]*\bhref\s*=\s*['\"](?:https?:)?//",
    r"url\(\s*['\"]?(?:https?:)?//",
)
if any(re.search(pattern, report, re.IGNORECASE) for pattern in patterns):
    raise SystemExit("HTML report has an external executable/display asset")
PY
echo "      HTML Zero-CDN 검증 통과 (verify exit $verify_status)"

echo "✔ 모든 스모크 테스트 통과: $BIN"
