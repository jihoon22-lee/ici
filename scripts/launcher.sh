#!/bin/sh
# ici Polyglot sh launcher preamble — attached to the head of dist/ici.pyz.
# Searches for Python >= 3.10 across candidate paths or $ICI_PYTHON.

for c in "$ICI_PYTHON" python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    [ -n "$c" ] || continue
    p=$(command -v "$c" 2>/dev/null) || continue
    "$p" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null \
        && exec "$p" "$0" "$@"
done

echo "오류: Python 3.10 이상을 찾을 수 없습니다." >&2
echo "      시스템에 설치된 3.10+ 인터프리터 경로를 ICI_PYTHON 에 지정하세요:" >&2
echo "      예: export ICI_PYTHON=/opt/python3.10/bin/python3.10" >&2
exit 1
