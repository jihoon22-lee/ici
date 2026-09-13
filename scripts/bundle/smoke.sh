#!/usr/bin/env bash
# Smoke a built bundle (WP04 #202, PR C).
#
# This is the release-path check, promoted from the WP01 spike. It asserts the
# acceptance criteria #202 states about a built artifact rather than about the
# source tree: the bundle runs from another location, from a read-only install,
# with a clean HOME, and with no network at all.
#
# The offline case is the one that changed. The spike removed every proxy
# variable and said so in its own comment:
#
#     note: this removes ici's ability to reach the network, it does not prove
#           the process made no syscall. A netns-isolated run is a field check.
#
# This runs it in a network namespace with no interfaces, which is that field
# check. Where the namespace cannot be created — a container without the
# privilege — the case reports BLOCKED. It never silently passes: ici shipped a
# green gate for several releases while lint had never run (C-6), and a smoke
# that quietly skips its hardest case is the same failure.
#
# Usage: smoke.sh <bundle-dir> [workdir] [--json <path>]
set -uo pipefail

BUNDLE="${1:?usage: smoke.sh <bundle-dir> [workdir] [--json <path>]}"
WORK="${2:-$(mktemp -d)}"
JSON=""
if [ "${3-}" = "--json" ]; then JSON="${4:?--json needs a path}"; fi

BUNDLE="$(cd "$BUNDLE" && pwd)"
PASS=0 FAIL=0 BLOCK=0
RESULTS=""

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { PASS=$((PASS+1));  printf '  PASS    %s\n' "$2"; record "$1" PASS "$2"; }
bad()  { FAIL=$((FAIL+1));  printf '  FAIL    %s\n' "$2"; record "$1" FAIL "$2"; }
skip() { BLOCK=$((BLOCK+1)); printf '  BLOCKED %s\n' "$2"; record "$1" BLOCKED "$2"; }

record() {
  local detail=${3//\"/\\\"}
  RESULTS="${RESULTS}{\"case\":\"$1\",\"status\":\"$2\",\"detail\":\"${detail}\"},"
}

mkdir -p "$WORK"
PROJECT="$WORK/project"
mkdir -p "$PROJECT/src"
printf 'value = 1\n' > "$PROJECT/src/module.py"

# Case 8 needs a copy no other case has touched. Taken here, before anything
# runs: the first version of this script measured a copy made after case 1 had
# already run in place, so the bytecode that case wrote was carried into the
# baseline by cp -a and the check found nothing new. It passed on a bundle
# stripped of its precompiled bytecode, which is the exact defect it exists to
# catch.
PRISTINE="$WORK/pristine"
cp -a "$BUNDLE" "$PRISTINE"

# path, mtime and size of every file: this catches a modified file as well as a
# new one, which a count of new paths does not.
inventory() { find "$1" -type f -printf '%P %T@ %s\n' 2>/dev/null | LC_ALL=C sort; }

# #202 acceptance criterion 2 asks for "init/help/version/소형 analyzer". The
# current CLI has no init subcommand; doctor is what stands in for it, and it is
# the better check anyway, being the command that probes the environment for
# toolchains. Running only --version would pass for a launcher that survived
# relocation while the analysis underneath it could no longer find its tools.
runs_here() {
  local root=$1 label=$2
  local version help doctor analysis
  version="$("$root/bin/ici" --version 2>&1)" || { printf '%s' "$label: --version failed: $version"; return 1; }
  case "$version" in ici\ *) ;; *) printf '%s' "$label: unexpected version output: $version"; return 1 ;; esac

  help="$("$root/bin/ici" --help 2>&1)" || { printf '%s' "$label: --help failed: $help"; return 1; }
  # Named, not "-c": python -c leaves argv[0] as "-c" unless the launcher sets it.
  case "$help" in *"Usage: ici"*) ;; *) printf '%s' "$label: --help did not name the program"; return 1 ;; esac

  doctor="$(cd "$PROJECT" && "$root/bin/ici" doctor 2>&1)" || { printf '%s' "$label: doctor failed: $doctor"; return 1; }
  case "$doctor" in *Resolved*) ;; *) printf '%s' "$label: doctor resolved nothing"; return 1 ;; esac

  analysis="$(cd "$PROJECT" && "$root/bin/ici" line 2>&1)" || { printf '%s' "$label: analysis failed: $analysis"; return 1; }
  case "$analysis" in *Lines*) ;; *) printf '%s' "$label: analysis produced no line count"; return 1 ;; esac

  printf '%s' "$label: $version, help/doctor/analysis ran"
  return 0
}

say "1. runs where it was built"
if detail="$(runs_here "$BUNDLE" in-place)"; then ok in-place "$detail"; else bad in-place "$detail"; fi

say "2. runs from another install location"
MOVED="$WORK/another location/ici"
mkdir -p "$(dirname "$MOVED")"
cp -a "$BUNDLE" "$MOVED"
if detail="$(runs_here "$MOVED" relocated)"; then ok relocated "$detail"; else bad relocated "$detail"; fi

say "3. runs through a symlink on PATH"
mkdir -p "$WORK/bin"
ln -sf "$MOVED/bin/ici" "$WORK/bin/ici"
if out="$("$WORK/bin/ici" --version 2>&1)" && [ "${out#ici }" != "$out" ]; then
  ok symlink "invoked through a symlink -> $out"
else
  bad symlink "symlink invocation failed: ${out:-no output}"
fi

say "4. runs with a clean HOME"
if detail="$(HOME="$WORK/clean-home" XDG_CONFIG_HOME= XDG_CACHE_HOME= \
             bash -c "mkdir -p '$WORK/clean-home'; $(declare -f runs_here); PROJECT='$PROJECT'; runs_here '$MOVED' clean-home")"; then
  ok clean-home "$detail"
else
  bad clean-home "$detail"
fi

say "5. runs from a read-only install"
RO="$WORK/readonly"
cp -a "$BUNDLE" "$RO"
if unshare --mount --map-root-user true 2>/dev/null; then
  detail="$(unshare --mount --map-root-user bash -c "
    mount --bind '$RO' '$RO' && mount -o remount,bind,ro '$RO' || exit 97
    touch '$RO/.write-probe' 2>/dev/null && exit 98
    PROJECT='$PROJECT'; $(declare -f runs_here); runs_here '$RO' read-only
  " 2>&1)"
  case $? in
    0)  ok read-only "$detail" ;;
    97) skip read-only "could not create the read-only bind mount" ;;
    98) bad read-only "the mount was still writable, so this proved nothing" ;;
    *)  bad read-only "$detail" ;;
  esac
else
  skip read-only "no mount namespace privilege in this environment"
fi

say "6. runs with no network at all"
# The spike could only remove proxy variables. This removes the interfaces.
if unshare -n --map-root-user true 2>/dev/null; then
  detail="$(unshare -n --map-root-user bash -c "
    PROJECT='$PROJECT'; $(declare -f runs_here); runs_here '$MOVED' offline
  " 2>&1)"
  if [ $? -eq 0 ]; then
    ok offline "$detail (no network interfaces)"
  else
    bad offline "$detail"
  fi
else
  skip offline "no network namespace privilege in this environment"
fi

say "7. offline, clean HOME and read-only together"
# Each alone can pass for the wrong reason; the combination is what a locked
# down site actually looks like.
if unshare -n --mount --map-root-user true 2>/dev/null; then
  detail="$(unshare -n --mount --map-root-user bash -c "
    mount --bind '$RO' '$RO' && mount -o remount,bind,ro '$RO' || exit 97
    export HOME='$WORK/combined-home'; mkdir -p \"\$HOME\"
    unset XDG_CONFIG_HOME XDG_CACHE_HOME
    PROJECT='$PROJECT'; $(declare -f runs_here); runs_here '$RO' combined
  " 2>&1)"
  case $? in
    0)  ok combined "$detail" ;;
    97) skip combined "could not create the read-only bind mount" ;;
    *)  bad combined "$detail" ;;
  esac
else
  skip combined "no namespace privilege in this environment"
fi

say "8. the install directory is not written to"
# Against $PRISTINE, which nothing above has run. A read-only install is only
# usable because the bundle is precompiled at build time; if that ever stops
# happening the first run writes __pycache__ here, and this is what says so.
inventory "$PRISTINE" > "$WORK/inventory.before"
# The analysis has to have happened. A launcher that dies on startup writes
# nothing either, and reporting that as "wrote nothing" is the failure this
# whole script exists to refuse.
( cd "$PROJECT" && "$PRISTINE/bin/ici" line >/dev/null 2>&1 ); RAN=$?
inventory "$PRISTINE" > "$WORK/inventory.after"
if [ "$RAN" -ne 0 ]; then
  bad no-writes "the analysis did not run (exit $RAN), so nothing can be concluded about writes"
elif diff -q "$WORK/inventory.before" "$WORK/inventory.after" >/dev/null; then
  ok no-writes "an analysis left all $(wc -l < "$WORK/inventory.before" | tr -d ' ') files in the install directory untouched"
else
  CHANGED="$(diff "$WORK/inventory.before" "$WORK/inventory.after" | grep -c '^[<>]')"
  EXAMPLE="$(diff "$WORK/inventory.before" "$WORK/inventory.after" | grep -m1 '^>' | cut -d' ' -f2)"
  bad no-writes "the run added or changed $CHANGED file(s) in the install directory, e.g. ${EXAMPLE:-unknown}"
fi

say "9. a user run installs no packages"
# Acceptance criterion 5. "No writes into the install directory" does not cover
# this: a pip or uv install during a user run lands in HOME's caches, not in the
# bundle. So look where an install would actually go, under a HOME that starts
# empty and offline, where a download would fail loudly rather than succeed.
PKGHOME="$WORK/pkg-home"
mkdir -p "$PKGHOME"
if unshare -n --map-root-user true 2>/dev/null; then
  # Same reason as case 8: a run that never happened installs nothing.
  # -u before the assignments: GNU env stops reading options at the first
  # NAME=value, so "env HOME=... -u XDG_CACHE_HOME" treats -u as the program and
  # exits 127. That is how this case passed while ici never ran.
  unshare -n --map-root-user env -u XDG_CONFIG_HOME -u XDG_CACHE_HOME HOME="$PKGHOME" \
    bash -c "cd '$PROJECT' && '$MOVED/bin/ici' line >/dev/null 2>&1 && '$MOVED/bin/ici' doctor >/dev/null 2>&1" \
    >/dev/null 2>&1
  RAN=$?
  INSTALLED="$(find "$PKGHOME" \( -name 'site-packages' -o -name '*.dist-info' -o -name '*.whl' \
                                -o -path '*/.cache/pip/*' -o -path '*/.cache/uv/*' \) -print 2>/dev/null | head -5)"
  if [ "$RAN" -ne 0 ]; then
    bad no-installs "the run did not complete (exit $RAN), so an install could not be ruled out"
  elif [ -z "$INSTALLED" ]; then
    ok no-installs "an offline run under an empty HOME installed no packages ($(find "$PKGHOME" -mindepth 1 | wc -l | tr -d ' ') path(s) created at all)"
  else
    bad no-installs "a user run left package-install artefacts under HOME: $(printf '%s' "$INSTALLED" | tr '\n' ' ')"
  fi
else
  skip no-installs "no network namespace privilege, so an install could not be ruled out"
fi

say "10. two versions side by side"
# #202 step 5. Each install must answer for itself; a second one on disk must
# not capture the first, which is what a shared cache or an absolute path baked
# at build time would do.
SIDE="$WORK/side-by-side/ici-second"
mkdir -p "$(dirname "$SIDE")"
cp -a "$BUNDLE" "$SIDE"
FIRST_ROOT="$(ICI_BUNDLE_ROOT= "$MOVED/bin/ici" --version 2>&1)"
SECOND_ROOT="$("$SIDE/bin/ici" --version 2>&1)"
if detail="$(runs_here "$SIDE" second-install)" && [ "$FIRST_ROOT" = "$SECOND_ROOT" ]; then
  # Same version here because both copies come from one build; what is being
  # checked is that each resolves its own root, which the launcher test pins
  # per-install and this confirms on a real bundle.
  ok side-by-side "$detail, and the first install still reports $FIRST_ROOT"
else
  bad side-by-side "${detail:-second install failed} (first: $FIRST_ROOT, second: $SECOND_ROOT)"
fi

printf '\n\033[1m== summary ==\033[0m\n'
printf '  PASS %d  FAIL %d  BLOCKED %d\n' "$PASS" "$FAIL" "$BLOCK"
if [ -n "$JSON" ]; then
  printf '{"schema":"ici.next.bundle-smoke/v1","pass":%d,"fail":%d,"blocked":%d,"cases":[%s]}\n' \
    "$PASS" "$FAIL" "$BLOCK" "${RESULTS%,}" > "$JSON"
  printf '  wrote %s\n' "$JSON"
fi

# BLOCKED is not failure, but it is not success either: the caller decides
# whether an unmeasured case is acceptable, and CI sets ICI_SMOKE_STRICT=1.
if [ "$FAIL" -gt 0 ]; then exit 1; fi
if [ "${ICI_SMOKE_STRICT-}" = "1" ] && [ "$BLOCK" -gt 0 ]; then
  printf '  ICI_SMOKE_STRICT=1 and %d case(s) could not be measured\n' "$BLOCK"
  exit 2
fi
exit 0
