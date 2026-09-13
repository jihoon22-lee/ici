#!/usr/bin/env bash
# WP01 (#199) spike: assemble a prototype standalone bundle.
#
# This is an EXPERIMENT, not a release path. It does not touch dist/ici.pyz,
# scripts/build-pyz.sh, any project .venv, or the shared Python. Its only job
# is to make the risk assumptions in #199 measurable:
#
#   - does a PBS CPython run ici core after being copied out of uv's store?
#   - does the bundle survive being moved, made read-only, and run with a
#     clean HOME and no network?
#   - does core isolation leak into, or get broken by, the project environment?
#
# Usage: build-bundle.sh <output-dir> [pbs-runtime-dir]
set -euo pipefail

OUT="${1:?usage: build-bundle.sh <output-dir> [pbs-runtime-dir]}"
PBS="${2:-$(uv python find 3.13.7 2>/dev/null | xargs -r dirname | xargs -r dirname)}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

[ -x "$PBS/bin/python3.13" ] || { echo "no PBS runtime at $PBS" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"/{bin,runtime,app,tools/python-static,licenses}

# --- runtime ------------------------------------------------------------
# Copied, not symlinked: the point is to prove the runtime is relocatable.
# tkinter is dropped because it is the only extension needing libtcl/libtk,
# and ici never imports it — carrying it would add an external .so dependency
# to the bundle's requirements for no benefit.
cp -a "$PBS/." "$OUT/runtime/python/"
rm -f "$OUT/runtime/python/lib/python3.13/lib-dynload/_tkinter"*.so
rm -rf "$OUT/runtime/python/lib/python3.13/tkinter" \
       "$OUT/runtime/python/lib/python3.13/test" \
       "$OUT/runtime/python/lib/python3.13/idlelib" \
       "$OUT/runtime/python/share/man"

# --- app (ici core) -----------------------------------------------------
cp -a "$REPO/src/ici" "$OUT/app/ici"
find "$OUT/app" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# core dependencies, installed into the bundle rather than a project .venv.
# The list is read from pyproject.toml so it cannot drift from what core
# actually declares. --no-cache keeps the bundle's inputs explicit; this runs
# at BUILD time, and the acceptance criterion is that no install happens at
# RUN time.
mapfile -t CORE_DEPS < <(
    "$OUT/runtime/python/bin/python3.13" - "$REPO/pyproject.toml" <<'PYDEPS'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    for dep in tomllib.load(fh)["project"]["dependencies"]:
        print(dep)
PYDEPS
)
[ "${#CORE_DEPS[@]}" -gt 0 ] || { echo "no dependencies parsed from pyproject.toml" >&2; exit 1; }
echo "core deps: ${CORE_DEPS[*]}"
"$OUT/runtime/python/bin/python3.13" -m pip install --quiet --no-cache-dir \
    --target "$OUT/app/vendor" "${CORE_DEPS[@]}" >/dev/null 2>&1 \
  || echo "WARN: vendor install failed (offline?); core may not import" >&2

# --- analyzer tools -----------------------------------------------------
# Bundled so that `verify` never needs ruff inside the project's .venv (R02).
if command -v ruff >/dev/null; then
    cp "$(command -v ruff)" "$OUT/tools/python-static/ruff"
fi

cp "$PBS/lib/python3.13/LICENSE.txt" "$OUT/licenses/cpython-LICENSE.txt" 2>/dev/null || true

# --- launcher -----------------------------------------------------------
# Copied from scripts/bundle/launcher.sh rather than written here. The launcher
# became a reviewable, testable file in WP04 (#202), and keeping a second copy in
# this spike would let the two drift — which is exactly how the spike ended up
# running bin/python3.13 while the assembler read bin/python3.
cp "$REPO/scripts/bundle/launcher.sh" "$OUT/bin/ici"
chmod +x "$OUT/bin/ici"

# --- env probe ----------------------------------------------------------
# Shares the launcher preamble above, so it measures the real contract rather
# than a reimplementation of it. Reports what core sees and what a project
# child would see, which is the pair SPEC-02 section 2 asks for.
cat > "$OUT/bin/ici-env-probe" <<'PROBE'
#!/usr/bin/env bash
set -euo pipefail
BUNDLE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

export ICI_ENTRY_PATH="${PATH-}"
export ICI_ENTRY_PYTHONPATH="${PYTHONPATH-}"
export ICI_ENTRY_PYTHONHOME="${PYTHONHOME-}"
export ICI_ENTRY_VIRTUAL_ENV="${VIRTUAL_ENV-}"
export ICI_ENTRY_LD_LIBRARY_PATH="${LD_LIBRARY_PATH-}"
export ICI_BUNDLE_ROOT="$BUNDLE"

unset PYTHONPATH PYTHONHOME VIRTUAL_ENV
exec "$BUNDLE/runtime/python/bin/python3.13" -I -c '
import json, os, subprocess, sys

root = os.environ["ICI_BUNDLE_ROOT"]
sys.path.insert(0, os.path.join(root, "app"))
sys.path.insert(1, os.path.join(root, "app", "vendor"))

WATCHED = ("PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
           "LD_LIBRARY_PATH", "QT_SELECT", "QTDIR", "QT_PLUGIN_PATH",
           "SSL_CERT_FILE", "SSL_CERT_DIR")

def entry_env():
    """The environment as it was at launcher entry, for a project child."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ICI_ENTRY_")}
    for name in ("PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "LD_LIBRARY_PATH"):
        saved = os.environ.get("ICI_ENTRY_" + name)
        if saved:
            env[name] = saved
        else:
            env.pop(name, None)
    env.pop("ICI_BUNDLE_ROOT", None)
    return env

report = {
    "core": {
        "executable": sys.executable,
        "version": sys.version.split()[0],
        "prefix": sys.prefix,
        "isolated": sys.flags.isolated == 1,
        "ici_importable": None,
        "ici_file": None,
        "env": {k: os.environ.get(k) for k in WATCHED},
        "sys_path_has_project": None,
    },
}

try:
    import ici
    report["core"]["ici_importable"] = True
    report["core"]["ici_file"] = ici.__file__
except Exception as err:
    report["core"]["ici_importable"] = False
    report["core"]["ici_error"] = f"{type(err).__name__}: {err}"

# Did an inherited project PYTHONPATH leak onto core sys.path?
leaked = os.environ.get("ICI_ENTRY_PYTHONPATH", "")
report["core"]["sys_path_has_project"] = any(
    part and part in sys.path for part in leaked.split(os.pathsep)
)

# What a project child sees when handed the preserved entry environment.
# Double quotes only: this whole block lives inside a single-quoted shell
# argument, so a literal apostrophe here would end the argument early.
child_code = (
    "import json,os,sys;"
    "print(json.dumps({"
    "\"executable\":sys.executable,"
    "\"version\":sys.version.split()[0],"
    "\"prefix\":sys.prefix,"
    "\"env\":{k:os.environ.get(k) for k in json.loads(sys.argv[1])},"
    "\"sys_path_head\":sys.path[:4]}))"
)
project_python = sys.argv[1] if len(sys.argv) > 1 else None
if project_python:
    try:
        done = subprocess.run(
            [project_python, "-c", child_code, json.dumps(list(WATCHED))],
            capture_output=True, text=True, timeout=60, env=entry_env())
        report["child"] = json.loads(done.stdout) if done.returncode == 0 else {
            "error": done.stderr.strip()[:400], "returncode": done.returncode}
        report["child"]["requested_executable"] = project_python
    except Exception as err:
        report["child"] = {"error": f"{type(err).__name__}: {err}"}

print(json.dumps(report, indent=2, sort_keys=True))
' "$@"
PROBE
chmod +x "$OUT/bin/ici-env-probe"

# --- normalisation ------------------------------------------------------
# scripts/assemble_bundle.py exists since PR A but nothing called it, so every
# bundle this script produced shipped app/ici with no bytecode at all. The first
# run then wrote 149 .pyc files into the install directory — which the PR C smoke
# caught, and which is why a read-only install worked only by CPython tolerating
# a failed write. Precompiling here is what makes "the install directory is not
# written to" true rather than merely untested.
#
# Run before the manifest so its tree digests describe the artifact as shipped.
"$OUT/runtime/python/bin/python3" - "$REPO" "$OUT" <<'PYNORM'
import sys
from pathlib import Path

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(repo / "scripts"))
from assemble_bundle import normalize

report = normalize(out, out / "runtime" / "python" / "bin" / "python3")
print("normalised:", ", ".join(report.recompiled_roots),
      "-", len(report.removed_scripts), "vendor script(s) removed")
PYNORM

# --- manifest -----------------------------------------------------------
"$OUT/runtime/python/bin/python3.13" - "$OUT" "$PBS" "$REPO" <<'PYMANIFEST'
import hashlib, json, os, platform, subprocess, sys

out, pbs, repo = sys.argv[1:4]

def tree_digest(path):
    """Order-independent digest over relative path + content."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            if os.path.islink(full):
                h.update(os.path.relpath(full, path).encode() + b"\0L")
                h.update(os.readlink(full).encode())
                continue
            h.update(os.path.relpath(full, path).encode() + b"\0F")
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
    return "sha256:" + h.hexdigest()

def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()

def run(*argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as err:
        return f"<unavailable: {err}>"

py = os.path.join(out, "runtime", "python", "bin", "python3.13")
glibc = sorted(
    {
        line.split("GLIBC_")[1].split()[0].rstrip(")")
        for line in run("objdump", "-T", py).splitlines()
        if "GLIBC_" in line
    },
    key=lambda v: tuple(int(p) for p in v.split(".") if p.isdigit()),
)

tools = {}
tool_dir = os.path.join(out, "tools", "python-static")
for name in sorted(os.listdir(tool_dir)) if os.path.isdir(tool_dir) else []:
    full = os.path.join(tool_dir, name)
    tools[name] = {"digest": file_digest(full), "version": run(full, "--version")}

manifest = {
    "schema_id": "ici.next.bundle.spike",
    "schema_version": 0,
    "note": "WP01 (#199) spike artifact. Not a release manifest.",
    "source_commit": run("git", "-C", repo, "rev-parse", "HEAD"),
    "source_dirty": bool(run("git", "-C", repo, "status", "--porcelain")),
    "runtime": {
        "implementation": platform.python_implementation(),
        "version": run(py, "-c", "import sys;print(sys.version.split()[0])"),
        "build": run(py, "-VV"),
        "source": "python-build-standalone via uv",
        "upstream_dir": pbs,
        "tree_digest": tree_digest(os.path.join(out, "runtime", "python")),
        "openssl": run(py, "-c", "import ssl;print(ssl.OPENSSL_VERSION)"),
        "openssl_linkage": "static (_ssl is built into the interpreter binary)",
    },
    "requirements": {
        "os": "linux",
        "architecture": "x86_64",
        "glibc_symbols_required": glibc,
        "glibc_max_required": glibc[-1] if glibc else None,
        "shared_libraries": sorted(
            {
                line.split("=>")[0].strip()
                for line in run("ldd", py).splitlines()
                if "=>" in line
            }
        ),
        "cpu_isa": "not asserted by this spike; see wp01-runtime-environment.md",
    },
    "app": {"tree_digest": tree_digest(os.path.join(out, "app"))},
    "tools": tools,
}
with open(os.path.join(out, "manifest.json"), "w") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
    fh.write("\n")
print(f"bundle: {out}")
print(f"runtime digest: {manifest['runtime']['tree_digest']}")
print(f"glibc max: {manifest['requirements']['glibc_max_required']}")
PYMANIFEST
