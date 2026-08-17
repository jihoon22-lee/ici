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

echo "✔ 모든 스모크 테스트 통과: $BIN"
