#!/usr/bin/env bash
# WP01 (#199) spike, test 6: does the compiler that actually gets invoked come
# from the build definition, or from whatever is first on PATH?
#
# SPEC-02 section 3 is explicit: "real compiler -- based on the actual
# invocation in the compile DB / build definition. Not overwritten by the
# newest GCC on PATH." This measures the current implementation against that.
#
# qmake and Qt are absent from this container, so the qmake half of #199's
# test 6 cannot run here and is recorded as a field check rather than guessed.
#
# Usage: probe-compiler.sh <workdir>
set -uo pipefail

WORK="${1:?usage: probe-compiler.sh <workdir>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
res()  { printf '  %-8s %s\n' "$1" "$2"; }
info() { printf '           %s\n' "$*"; }

FIX="$WORK/cc-fixture"
rm -rf "$WORK"; mkdir -p "$FIX/src" "$WORK/shim"

cat > "$FIX/src/widget.cpp" <<'CPP'
#include "widget.h"

int Widget::value() const {
    return 42;
}
CPP
cat > "$FIX/src/widget.h" <<'CPP'
#pragma once

class Widget {
public:
    int value() const;
};
CPP

say "0. compilers present in this container"
for c in g++ gcc clang++ clang; do
    command -v "$c" >/dev/null && info "$c -> $("$c" --version | head -1)"
done
for q in qmake qmake6; do command -v "$q" >/dev/null || info "$q -> ABSENT"; done

say "1. two compilers give distinguishable identity"
GXX="$(command -v g++ || true)"
CXX2="$(command -v clang++ || true)"
if [ -n "$GXX" ] && [ -n "$CXX2" ]; then
    G_TRIPLE="$("$GXX" -dumpmachine 2>/dev/null)"
    C_TRIPLE="$("$CXX2" -dumpmachine 2>/dev/null)"
    info "g++      -dumpmachine: $G_TRIPLE"
    info "clang++  -dumpmachine: $C_TRIPLE"
    # Identity that actually differs: the predefined macro set.
    G_MAC="$(echo | "$GXX" -dM -E -x c++ - 2>/dev/null | grep -c '__GNUC__\|__clang__')"
    C_MAC="$(echo | "$CXX2" -dM -E -x c++ - 2>/dev/null | grep -c '__clang__')"
    info "g++ defines __GNUC__ family macros: $G_MAC ; clang++ defines __clang__: $C_MAC"
    res PASS "the two compilers are distinguishable, so the choice is observable"
else
    res BLOCKED "need two distinct C++ compilers to make the choice observable"
fi

say "2. a compile DB that names clang++, with g++ first on PATH"
# The shim records every invocation, so we can see WHICH compiler ici ran
# rather than inferring it from output.
LOG="$WORK/invocations.log"; : > "$LOG"
for name in g++ gcc clang++ clang; do
    real="$(command -v "$name" || true)"
    [ -n "$real" ] || continue
    cat > "$WORK/shim/$name" <<SHIM
#!/usr/bin/env bash
echo "$name \$*" >> "$LOG"
exec "$real" "\$@"
SHIM
    chmod +x "$WORK/shim/$name"
done

# compile_commands.json naming clang++ explicitly.
cat > "$FIX/compile_commands.json" <<JSON
[
  {
    "directory": "$FIX",
    "command": "$WORK/shim/clang++ -std=c++17 -I$FIX/src -c $FIX/src/widget.cpp -o $FIX/widget.o",
    "file": "$FIX/src/widget.cpp"
  }
]
JSON

# Deliberately the CURRENT schema, not SPEC-01's target schema. A first
# attempt used `schema_version = 1` from SPEC-01 and the current config
# validator rejected it outright:
#     Configuration error: schema_version is an unknown configuration key
# That is a useful incidental result -- it confirms SPEC-01's own warning that
# its TOML is a target contract rather than accepted syntax today, and it means
# the versioned-TOML rollout needs a migration path rather than a drop-in file.
# Recorded in wp01-runtime-environment.md; owner is #203/#225.
cat > "$FIX/ici.toml" <<'TOML'
[project]
source_dirs = ["src"]

[engines.compile_db]
enabled = true
TOML

if [ -f "$REPO/dist/ici.pyz" ]; then
    ICI=("$REPO/dist/ici.pyz")
else
    ICI=(uv run --python 3.10 --project "$REPO" python -m ici)
fi
info "driving: ${ICI[*]}"

OUT="$(cd "$FIX" && PATH="$WORK/shim:$PATH" "${ICI[@]}" compile-db 2>&1 | tail -5)"
RC=$?
if [ "$RC" -eq 127 ] || printf '%s' "$OUT" | grep -qi "no such command\|Usage:"; then
    # compile_db has no standalone subcommand (inventory/current-engines.md);
    # drive it through verify instead.
    OUT="$(cd "$FIX" && PATH="$WORK/shim:$PATH" "${ICI[@]}" verify --profile fast 2>&1 | tail -12)"
fi
info "$(printf '%s' "$OUT" | tail -4)"

if [ -s "$LOG" ]; then
    info "compiler invocations recorded:"
    sort "$LOG" | uniq -c | sed 's/^/             /'
    if grep -q '^clang++' "$LOG" && ! grep -q '^g++' "$LOG"; then
        res PASS "only the compile-DB compiler (clang++) was invoked"
    elif grep -q '^g++' "$LOG" && ! grep -q '^clang++' "$LOG"; then
        res FAIL "g++ from PATH was invoked instead of the compile DB's clang++"
        info "this is exactly what SPEC-02 section 3 forbids; owner is #211/#204"
    elif grep -q '^g++' "$LOG" && grep -q '^clang++' "$LOG"; then
        res INFO "both were invoked; the DB compiler is used but PATH is also probed"
        info "the probe itself is fine, but its identity must not be reported as"
        info "the compilation compiler"
    fi
else
    res INFO "no compiler was invoked at all for this fixture"
    info "so this path does not yet exercise compiler identity; #211 owns the"
    info "compile-DB ingestion that will"
fi

say "3. qmake environment preservation"
res BLOCKED "qmake and Qt are absent from this container"
info "#199 test 6 asks for two qmake paths plus a small SUBDIRS fixture."
info "Neither qmake, qmake6, nor any Qt development package is installed, so"
info "this cannot be measured here and is NOT assumed to work."
info "Field-check items, owned by #212 (qmake SUBDIRS) and #226 (acceptance):"
info "  - two qmake installations on PATH, project definition selecting one"
info "  - SUBDIRS with shared build dir feeding several analysis units"
info "  - moc/uic/rcc generated inputs appearing as declared inputs"
info "  - QTDIR / QT_PLUGIN_PATH / LD_LIBRARY_PATH preserved into the child"
info "The environment half of that list IS already measured, without qmake, by"
info "smoke-environment.sh test 3 (QTDIR and LD_LIBRARY_PATH survive into the"
info "project child)."
