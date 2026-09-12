#!/usr/bin/env bash
# WP01 (#199) spike: measure the bundle's environment contract.
#
# Covers tests 2-4 of #199: relocation, clean HOME, read-only install, offline,
# paths containing spaces, and whether core isolation and project-environment
# preservation actually hold when a project sets PYTHONPATH / PYTHONHOME /
# VIRTUAL_ENV / LD_LIBRARY_PATH / Qt variables.
#
# Every case prints PASS / FAIL / BLOCKED with the evidence behind it. BLOCKED
# means the environment could not host the test, which is not the same as the
# behaviour being wrong -- #199 requires the distinction.
#
# Usage: smoke-environment.sh <bundle-dir> <workdir>
set -uo pipefail

BUNDLE="${1:?usage: smoke-environment.sh <bundle-dir> <workdir>}"
WORK="${2:?usage: smoke-environment.sh <bundle-dir> <workdir>}"
PASS=0 FAIL=0 BLOCK=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { PASS=$((PASS+1)); printf '  PASS    %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL    %s\n' "$*"; }
skip() { BLOCK=$((BLOCK+1)); printf '  BLOCKED %s\n' "$*"; }
info() { printf '          %s\n' "$*"; }

mkdir -p "$WORK"

# --- a fake project environment ----------------------------------------
# A venv whose python is a symlink, plus the variables a prepared industrial
# environment would already carry. Nothing here reads or writes devenv.csh.
PROJECT="$WORK/project"
rm -rf "$PROJECT"; mkdir -p "$PROJECT/src" "$PROJECT/fakelib"
( cd "$PROJECT" && python3.10 -m venv .venv >/dev/null 2>&1 ) \
  || echo "WARN: could not create project venv" >&2
PROJECT_PY="$PROJECT/.venv/bin/python"

project_env() {
    export VIRTUAL_ENV="$PROJECT/.venv"
    export PYTHONPATH="$PROJECT/src"
    export LD_LIBRARY_PATH="$PROJECT/fakelib"
    export QT_SELECT=6
    export QTDIR="$PROJECT/fake-qt"
    export QT_PLUGIN_PATH="$PROJECT/fake-qt/plugins"
    export PATH="$PROJECT/.venv/bin:$PATH"
}

# A module only reachable through the project's PYTHONPATH. If core can import
# it, core is not isolated.
echo "MARKER = 'project-only'" > "$PROJECT/src/ici_project_marker.py"

run_probe() { "$BUNDLE/bin/ici-env-probe" "$@" 2>&1; }
jq_get() { "$BUNDLE/runtime/python/bin/python3.13" -c "
import json,sys
try: d=json.loads(sys.stdin.read())
except Exception: print('<unparseable>'); raise SystemExit
for k in sys.argv[1].split('.'):
    if isinstance(d,dict) and k in d: d=d[k]
    else: print('<missing>'); raise SystemExit
print(d)
" "$1"; }

# =======================================================================
say "1. baseline: bundle runs in place"
OUT="$("$BUNDLE/bin/ici" --version 2>&1)"
if [ "${OUT#ici }" != "$OUT" ]; then ok "ici --version -> $OUT"; else bad "ici --version -> $OUT"; fi

say "2. core isolation while a project environment is set"
REPORT="$( project_env; run_probe "$PROJECT_PY" )"
CORE_OK="$(printf '%s' "$REPORT" | jq_get core.ici_importable)"
LEAKED="$(printf '%s' "$REPORT" | jq_get core.sys_path_has_project)"
ISOLATED="$(printf '%s' "$REPORT" | jq_get core.isolated)"
[ "$CORE_OK" = "True" ] && ok "core imported ici with project env set" \
                        || bad "core could not import ici: $(printf '%s' "$REPORT" | jq_get core.ici_error)"
[ "$ISOLATED" = "True" ] && ok "core runs isolated (-I)" || bad "core not isolated: $ISOLATED"
[ "$LEAKED" = "False" ] && ok "project PYTHONPATH did not leak onto core sys.path" \
                        || bad "project PYTHONPATH leaked onto core sys.path"
# The decisive check: can core import a project-only module?
if ( project_env; "$BUNDLE/runtime/python/bin/python3.13" -I -c \
        'import ici_project_marker' >/dev/null 2>&1 ); then
    bad "core could import a project-only module (isolation broken)"
else
    ok "core cannot import a project-only module"
fi

say "3. project child keeps the entry environment"
CHILD_EXE="$(printf '%s' "$REPORT" | jq_get child.executable)"
CHILD_VENV="$(printf '%s' "$REPORT" | jq_get child.env.VIRTUAL_ENV)"
CHILD_PP="$(printf '%s' "$REPORT" | jq_get child.env.PYTHONPATH)"
CHILD_LD="$(printf '%s' "$REPORT" | jq_get child.env.LD_LIBRARY_PATH)"
CHILD_QT="$(printf '%s' "$REPORT" | jq_get child.env.QTDIR)"
CHILD_VER="$(printf '%s' "$REPORT" | jq_get child.version)"
if [ "$CHILD_EXE" = "<missing>" ] || [ "$CHILD_EXE" = "<unparseable>" ]; then
    skip "project child did not run: $(printf '%s' "$REPORT" | jq_get child.error)"
else
    info "child executable: $CHILD_EXE (python $CHILD_VER)"
    [ "$CHILD_VENV" = "$PROJECT/.venv" ] && ok "child VIRTUAL_ENV preserved" || bad "child VIRTUAL_ENV=$CHILD_VENV"
    [ "$CHILD_PP" = "$PROJECT/src" ]     && ok "child PYTHONPATH preserved"  || bad "child PYTHONPATH=$CHILD_PP"
    [ "$CHILD_LD" = "$PROJECT/fakelib" ] && ok "child LD_LIBRARY_PATH preserved" || bad "child LD_LIBRARY_PATH=$CHILD_LD"
    [ "$CHILD_QT" = "$PROJECT/fake-qt" ] && ok "child QTDIR preserved" || bad "child QTDIR=$CHILD_QT"
    case "$CHILD_VER" in 3.10*) ok "child ran the project interpreter, not core's $( "$BUNDLE/runtime/python/bin/python3.13" -c 'import sys;print(sys.version.split()[0])')";;
                         *)     bad "child version $CHILD_VER is not the project's 3.10";; esac
    # Does the bundle's own runtime appear ahead of the project's on the child PATH?
    CHILD_PATH="$(printf '%s' "$REPORT" | jq_get child.env.PATH)"
    case "$CHILD_PATH" in "$BUNDLE"*) bad "bundle path was prepended to the child PATH";;
                          *)          ok "bundle path not prepended to child PATH";; esac
fi

say "4. symlinked venv python: launch path vs realpath"
if [ -L "$PROJECT_PY" ] || [ -e "$PROJECT_PY" ]; then
    REAL="$(readlink -f "$PROJECT_PY")"
    L_PREFIX="$("$PROJECT_PY" -c 'import sys;print(sys.prefix)' 2>/dev/null)"
    R_PREFIX="$("$REAL" -c 'import sys;print(sys.prefix)' 2>/dev/null)"
    info "launch path: $PROJECT_PY -> prefix $L_PREFIX"
    info "realpath   : $REAL -> prefix $R_PREFIX"
    if [ "$L_PREFIX" != "$R_PREFIX" ]; then
        ok "substituting realpath CHANGES sys.prefix; launch path must be preserved"
    else
        bad "launch path and realpath gave the same prefix ($L_PREFIX); this case did not reproduce here"
    fi
else
    skip "no project venv python to compare"
fi

say "5. relocation: copy the bundle to another absolute path"
MOVED="$WORK/moved/ici-bundle"
rm -rf "$WORK/moved"; mkdir -p "$WORK/moved"; cp -a "$BUNDLE" "$MOVED"
OUT="$("$MOVED/bin/ici" --version 2>&1)"
[ "${OUT#ici }" != "$OUT" ] && ok "runs after relocation -> $OUT" || bad "relocation broke it: $OUT"

say "6. a path containing spaces"
SPACED="$WORK/dir with spaces/ici bundle"
rm -rf "$WORK/dir with spaces"; mkdir -p "$WORK/dir with spaces"; cp -a "$BUNDLE" "$SPACED"
OUT="$("$SPACED/bin/ici" --version 2>&1)"
[ "${OUT#ici }" != "$OUT" ] && ok "runs from a path with spaces -> $OUT" || bad "spaces broke it: $OUT"

say "7. clean HOME"
CLEANHOME="$WORK/clean-home"; rm -rf "$CLEANHOME"; mkdir -p "$CLEANHOME"
OUT="$(env HOME="$CLEANHOME" XDG_CONFIG_HOME="$CLEANHOME/.config" \
           XDG_CACHE_HOME="$CLEANHOME/.cache" "$BUNDLE/bin/ici" --version 2>&1)"
[ "${OUT#ici }" != "$OUT" ] && ok "runs with a clean HOME -> $OUT" || bad "clean HOME broke it: $OUT"
# --version short-circuits before configuration is loaded, so run a command
# that actually resolves config: load_config() writes an XDG global default
# when it finds no configuration at all, which a read-only HOME would reject.
DOUT="$(cd "$WORK" && env HOME="$CLEANHOME" XDG_CONFIG_HOME="$CLEANHOME/.config" \
           XDG_CACHE_HOME="$CLEANHOME/.cache" "$BUNDLE/bin/ici" doctor --brief 2>&1)"
DRC=$?
info "doctor --brief exit=$DRC, first line: $(printf '%s' "$DOUT" | head -1)"
CREATED="$(find "$CLEANHOME" -type f 2>/dev/null | wc -l)"
info "files created under a clean HOME by --version + doctor: $CREATED"
find "$CLEANHOME" -type f 2>/dev/null | head -5 | sed "s|^$CLEANHOME|          <HOME>|"
if [ "$CREATED" -gt 0 ]; then
    info "ici writes into HOME on a config-loading command. That is a real"
    info "constraint for read-only HOME and for the offline contract; it is"
    info "recorded, not judged, by this spike."
fi

say "8. read-only install directory"
ROBUNDLE="$WORK/readonly/ici-bundle"
rm -rf "$WORK/readonly"; mkdir -p "$WORK/readonly"; cp -a "$BUNDLE" "$ROBUNDLE"
chmod -R a-w "$ROBUNDLE" 2>/dev/null
chmod -R u+w "$ROBUNDLE" 2>/dev/null
# Mode bits are not enough: root bypasses DAC write checks, so a chmod-only
# test would pass for the wrong reason. Use a real read-only bind mount in a
# private mount namespace, which is what a locked-down install actually is.
if unshare --mount --map-root-user true 2>/dev/null; then
    OUT="$(unshare --mount --map-root-user bash -c '
        mount --bind "$1" "$1" 2>/dev/null || exit 97
        mount -o remount,bind,ro "$1" 2>/dev/null || exit 98
        touch "$1/.write-probe" 2>/dev/null && exit 96   # must NOT succeed
        "$1/bin/ici" --version 2>&1
    ' _ "$ROBUNDLE" 2>&1)"
    RC=$?
    case "$RC" in
      96) bad "the mount was still writable; the read-only case did not hold" ;;
      97|98) skip "could not create a read-only bind mount in this container (rc=$RC)"
             info "field-check item: verify on a real read-only install directory" ;;
      *) [ "${OUT#ici }" != "$OUT" ] \
           && ok "runs from a genuinely read-only install -> $OUT" \
           || bad "read-only install broke it: $OUT" ;;
    esac
else
    skip "mount namespaces unavailable; cannot build a real read-only install"
    info "field-check item: verify on a real read-only install directory"
fi

say "9. no network during a run"
# The contract is that ici does not REQUIRE the network, so the check is that
# it still works once every proxy handle is removed from the environment.
OUT="$(env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy \
           -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy \
           "$BUNDLE/bin/ici" --version 2>&1)"
[ "${OUT#ici }" != "$OUT" ] && ok "runs with every proxy variable removed -> $OUT" \
                            || bad "removing proxy variables broke it: $OUT"
info "note: this removes ici's ability to reach the network, it does not prove"
info "      the process made no syscall. A netns-isolated run is a field check."

say "10. CA trust after core isolation"
CA_KEPT="$(project_env; run_probe | jq_get core.env.SSL_CERT_FILE)"
if [ -n "${SSL_CERT_FILE:-}" ]; then
    [ "$CA_KEPT" = "${SSL_CERT_FILE}" ] && ok "SSL_CERT_FILE survived core isolation ($CA_KEPT)" \
                                        || bad "SSL_CERT_FILE lost during isolation: $CA_KEPT"
else
    skip "SSL_CERT_FILE is not set in this environment; nothing to preserve"
fi
NOCA="$(env -u SSL_CERT_FILE -u SSL_CERT_DIR "$BUNDLE/runtime/python/bin/python3.13" \
        -c 'import ssl;p=ssl.get_default_verify_paths();print(p.cafile)')"
info "with SSL_CERT_FILE unset, the bundled runtime's cafile is: $NOCA"

printf '\n\033[1m== summary\033[0m\n  PASS %d  FAIL %d  BLOCKED %d\n' "$PASS" "$FAIL" "$BLOCK"
[ "$FAIL" -eq 0 ]
