#!/usr/bin/env bash
# WP01 (#199) spike, test 5: how should ici reach pytest and coverage?
#
# Two candidate paths:
#   project  -- run the project's own pytest/coverage through the project's
#               interpreter. This is what SPEC-02 section 3 names as default.
#   overlay  -- ici supplies pytest/coverage from the bundle and injects them
#               into the project interpreter via PYTHONPATH.
#
# The overlay is attractive (it removes a per-project install, R02) and risky
# (it changes what the project's interpreter imports). This script measures the
# risk instead of arguing about it: inherited site-packages, a version conflict,
# plugin autoload, and subprocess coverage.
#
# Usage: probe-test-tools.sh <bundle-dir> <workdir>
set -uo pipefail

BUNDLE="${1:?usage: probe-test-tools.sh <bundle-dir> <workdir>}"
WORK="${2:?usage: probe-test-tools.sh <bundle-dir> <workdir>}"
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
res()  { printf '  %-8s %s\n' "$1" "$2"; }
info() { printf '           %s\n' "$*"; }

PROJ="$WORK/tt-project"
rm -rf "$PROJ"; mkdir -p "$PROJ/src/demo" "$PROJ/tests"

cat > "$PROJ/src/demo/__init__.py" <<'PY'
def add(a, b):
    return a + b

def spawn_child():
    """Covered only inside a subprocess, which is the interesting case."""
    import subprocess, sys
    return subprocess.run(
        [sys.executable, "-c", "import demo; print(demo.add(1, 2))"],
        capture_output=True, text=True,
    ).stdout.strip()
PY

cat > "$PROJ/tests/test_demo.py" <<'PY'
import demo

def test_add():
    assert demo.add(2, 3) == 5

def test_child():
    assert demo.spawn_child() == "3"
PY

cat > "$PROJ/pyproject.toml" <<'PY'
[project]
name = "demo"
version = "0"
requires-python = ">=3.10"
PY

# A project-local pytest plugin. If it stops loading under the overlay, the
# overlay has changed the project's test semantics -- the thing SPEC-03
# section 7 says must not happen silently.
cat > "$PROJ/tests/conftest.py" <<'PY'
import pytest

@pytest.fixture(autouse=True)
def _marker(request):
    request.node.user_properties.append(("ici-spike", "conftest-loaded"))
PY

PROJ_PY="$PROJ/.venv/bin/python"
python3.10 -m venv "$PROJ/.venv" >/dev/null 2>&1 || { echo "cannot create venv" >&2; exit 1; }
"$PROJ_PY" -m pip install --quiet --no-cache-dir -e "$PROJ" >/dev/null 2>&1

say "0. project interpreter"
info "$("$PROJ_PY" -VV | head -1)"

# =======================================================================
say "1. project path: pytest installed in the project venv"
"$PROJ_PY" -m pip install --quiet --no-cache-dir pytest coverage >/dev/null 2>&1
if "$PROJ_PY" -m pytest --version >/dev/null 2>&1; then
    PV="$("$PROJ_PY" -m pytest --version 2>&1 | head -1)"
    res PASS "project pytest available: $PV"
    OUT="$(cd "$PROJ" && "$PROJ_PY" -m pytest -q tests 2>&1 | tail -2)"
    info "$OUT"
    case "$OUT" in *"2 passed"*) res PASS "project pytest ran the suite";;
                   *)            res FAIL "project pytest did not pass: $OUT";; esac
else
    res BLOCKED "could not install pytest into the project venv (offline?)"
fi

say "2. project path: coverage over a subprocess"
# SPEC-03 section 7 wants executed/total raw counts to be trustworthy. A plain
# `coverage run` does not follow subprocesses, so code that only runs in a
# child is reported as uncovered. Measured on a module imported ONLY by the
# child, so the difference is unambiguous.
#
# Note the glob: `rm -f .coverage*` would delete .coveragerc as well, and a
# COVERAGE_PROCESS_START pointing at a deleted file makes every subsequent
# subprocess of that interpreter print a traceback to stderr -- which is how a
# tool-output parser gets fed garbage. Hence `.coverage .coverage.*`.
if "$PROJ_PY" -m coverage --version >/dev/null 2>&1; then
    cat > "$PROJ/src/demo/child_only.py" <<'PY2'
def only_in_child():
    return "child"
PY2
    cat > "$PROJ/src/demo/__init__.py" <<'PY2'
def add(a, b):
    return a + b

def spawn_child():
    import subprocess, sys
    return subprocess.run(
        [sys.executable, "-c",
         "from demo.child_only import only_in_child; print(only_in_child())"],
        capture_output=True, text=True,
    ).stdout.strip()
PY2
    cat > "$PROJ/tests/test_demo.py" <<'PY2'
import demo

def test_add():
    assert demo.add(2, 3) == 5

def test_child():
    assert demo.spawn_child() == "child"
PY2
    printf '[run]\nparallel = True\nsource = demo\n' > "$PROJ/.coveragerc"

    # Via a file, not a pipe: a heredoc would take over stdin and discard the
    # piped report.
    cat > "$WORK/read-child-cov.py" <<'COVPY'
import json, sys

with open(sys.argv[1]) as fh:
    data = json.load(fh)
for name, info in data.get("files", {}).items():
    if name.endswith("child_only.py"):
        s = info["summary"]
        print(f'{s["covered_lines"]}/{s["num_statements"]}')
        break
else:
    print("absent")
COVPY
    child_cov() {
        "$PROJ_PY" -m coverage json -o "$WORK/cov.json" >/dev/null 2>&1 \
          && "$PROJ_PY" "$WORK/read-child-cov.py" "$WORK/cov.json" \
          || echo "no-report"
    }

    ( cd "$PROJ" && rm -f .coverage .coverage.*
      "$PROJ_PY" -m coverage run -m pytest -q tests >/dev/null 2>&1
      "$PROJ_PY" -m coverage combine >/dev/null 2>&1 )
    NOHOOK="$(cd "$PROJ" && child_cov)"

    ( cd "$PROJ" && rm -f .coverage .coverage.*
      COVERAGE_PROCESS_START="$PROJ/.coveragerc" \
        "$PROJ_PY" -m coverage run -m pytest -q tests >/dev/null 2>&1
      "$PROJ_PY" -m coverage combine >/dev/null 2>&1 )
    HOOK="$(cd "$PROJ" && child_cov)"

    info "child-only statements without COVERAGE_PROCESS_START: $NOHOOK"
    info "child-only statements with    COVERAGE_PROCESS_START: $HOOK"
    if [ "$NOHOOK" != "$HOOK" ]; then
        res PASS "subprocess coverage is under-counted unless the env var is set"
        info "a silent under-count lowers the coverage term of the TEM score,"
        info "so ici must set this explicitly and record that it did."
    else
        res INFO "no measurable difference here ($NOHOOK); needs a field re-check"
    fi

    # Does enabling it require mutating the project? Modern coverage ships its
    # own .pth, so the answer decides whether R02 is threatened.
    SITE="$("$PROJ_PY" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
    if ls "$SITE"/*coverage*.pth >/dev/null 2>&1; then
        res PASS "coverage installs its own .pth ($(cd "$SITE" && ls -1 *coverage*.pth | head -1))"
        info "so ici needs only the environment variable -- no write into the"
        info "project's site-packages, which keeps R02 intact."
    else
        res INFO "no coverage .pth found; enabling subprocess coverage would need"
        info "a write into the project's site-packages. That conflicts with R02"
        info "and must be an explicit, recorded choice."
    fi
else
    res BLOCKED "coverage not available in the project venv"
fi

say "3. overlay path: bundle pytest injected into the project interpreter"
OVER="$WORK/tt-overlay"; rm -rf "$OVER"; mkdir -p "$OVER"
if "$BUNDLE/runtime/python/bin/python3.13" -m pip install --quiet --no-cache-dir \
      --target "$OVER" pytest coverage >/dev/null 2>&1; then
    res INFO "overlay built at $OVER"
    # A venv WITHOUT pytest, to isolate the overlay's effect.
    BARE="$WORK/tt-bare"; rm -rf "$BARE"
    python3.10 -m venv "$BARE" >/dev/null 2>&1
    BARE_PY="$BARE/bin/python"
    "$BARE_PY" -m pip install --quiet --no-cache-dir -e "$PROJ" >/dev/null 2>&1
    "$BARE_PY" -m pytest --version >/dev/null 2>&1 \
      && res FAIL "bare venv unexpectedly already has pytest" \
      || res PASS "bare venv has no pytest (overlay effect is isolated)"

    OUT="$(cd "$PROJ" && PYTHONPATH="$OVER" "$BARE_PY" -m pytest -q tests 2>&1 | tail -3)"
    case "$OUT" in
      *"2 passed"*) res PASS "overlay pytest ran the project suite"
                    info "$(printf '%s' "$OUT" | tail -1)" ;;
      *)            res FAIL "overlay pytest failed"
                    info "$(printf '%s' "$OUT" | head -3)" ;;
    esac
    # Did the project's own conftest still load under the overlay?
    OUT2="$(cd "$PROJ" && PYTHONPATH="$OVER" "$BARE_PY" -m pytest -q tests \
              -o addopts= --collect-only 2>&1 | tail -2)"
    info "collect-only under overlay: $(printf '%s' "$OUT2" | head -1)"
else
    res BLOCKED "could not build the overlay (offline?)"
fi

say "4. overlay path: version conflict with a project-installed pytest"
# The project venv from case 1 already has its own pytest. Putting a different
# version on PYTHONPATH asks: which one wins, and does the answer surprise?
if [ -d "$OVER" ]; then
    PROJ_V="$("$PROJ_PY" -m pytest --version 2>&1 | head -1)"
    OVER_V="$(cd "$WORK" && PYTHONPATH="$OVER" "$PROJ_PY" -m pytest --version 2>&1 | head -1)"
    WHICH="$(cd "$WORK" && PYTHONPATH="$OVER" "$PROJ_PY" -c 'import pytest;print(pytest.__file__)' 2>&1)"
    info "project venv pytest : $PROJ_V"
    info "with overlay on path: $OVER_V"
    info "resolved module     : $WHICH"
    case "$WHICH" in
      "$OVER"*) res INFO "PYTHONPATH overlay WINS over the project's installed pytest"
                info "so an overlay silently replaces the project's test tool;"
                info "that is a change of project semantics, not a convenience." ;;
      *)        res INFO "the project's own pytest won; the overlay was shadowed"
                info "so an overlay cannot be relied on when the project has one." ;;
    esac
else
    res BLOCKED "overlay unavailable"
fi

say "5. inherited site-packages leaking into the project interpreter"
# A venv created WITHOUT --system-site-packages must not see the system's
# dist-packages. Checking for "/usr/lib/python3" would be wrong: the venv's
# stdlib legitimately lives there. Compare the real site-packages dirs.
LEAKED="$("$PROJ_PY" - <<'PY2'
import site, sys
venv = sys.prefix
outside = [p for p in site.getsitepackages() if not p.startswith(venv)]
extra = [p for p in sys.path if "dist-packages" in p or (
    "site-packages" in p and not p.startswith(venv))]
print("|".join(sorted(set(outside + extra))) or "none")
PY2
)"
if [ "$LEAKED" = "none" ]; then
    res PASS "project venv does not inherit system site-packages"
else
    res INFO "project venv can reach site dirs outside the venv:"
    info "$LEAKED"
fi
SYS_SITE="$("$PROJ_PY" -c 'import sys;print(sys.prefix != sys.base_prefix)')"
info "venv active (prefix != base_prefix): $SYS_SITE"

say "6. core must not be the fallback interpreter"
# The current implementation falls back to sys.executable (ici core) when no
# project interpreter is configured -- inventory/execution-flow.md row 1.
# Here we measure what that fallback would actually run.
CORE_V="$("$BUNDLE/runtime/python/bin/python3.13" -c 'import sys;print(sys.version.split()[0])')"
PROJ_V2="$("$PROJ_PY" -c 'import sys;print(sys.version.split()[0])')"
info "core runtime: $CORE_V   project runtime: $PROJ_V2"
if [ "$CORE_V" != "$PROJ_V2" ]; then
    res PASS "a core fallback would run tests on $CORE_V instead of $PROJ_V2"
    info "the two differ, so the fallback is observable and must be an error,"
    info "not a silent substitution (SPEC-02 section 3)."
else
    res INFO "core and project runtimes match here; the fallback is not observable"
fi
if "$BUNDLE/runtime/python/bin/python3.13" -m pytest --version >/dev/null 2>&1; then
    res INFO "core runtime HAS pytest, so a fallback would appear to work"
else
    res PASS "core runtime has no pytest, so a fallback fails loudly rather than lying"
fi
