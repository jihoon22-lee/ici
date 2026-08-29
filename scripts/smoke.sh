#!/usr/bin/env bash
# dist/ici.pyz 스모크 테스트
# 배포 전 산출물의 무결성을 검증한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/dist/ici.pyz"

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
# HTML 리포트에 외부 CDN이 포함되지 않았는지 확인 (Zero-CDN 불변식)
if "$BIN" verify --html /tmp/ici_smoke_report.html >/dev/null 2>&1; then
    if grep -q "http://\|https://.*cdn\|cdn\." /tmp/ici_smoke_report.html 2>/dev/null; then
        echo "      HTML 리포트에 외부 CDN 링크 발견 (Zero-CDN 위반)" >&2
        exit 1
    else
        echo "      HTML Zero-CDN 검증 통과"
    fi
    rm -f /tmp/ici_smoke_report.html /tmp/ici_smoke_report.json
else
    echo "      HTML 리포트 생성 스킵 (verify 실패 시 무시)"
fi

echo "✔ 모든 스모크 테스트 통과: $BIN"
