#!/usr/bin/env bash
# The bundle entry point, copied verbatim to bin/ici (WP04 #202, step 3).
#
# It exists as a file rather than a heredoc inside the builder so that it can be
# read, reviewed and tested. tests/test_bundle_launcher.py runs it against a
# synthetic bundle and pins the contract below.
#
# The contract has two halves that pull against each other, and getting both is
# the point (SPEC-02 section 2):
#
#   preserve  the environment the user invoked ici in, so a project child is
#             handed what the user actually had.
#   isolate   core's imports, so an inherited PYTHONPATH cannot shadow them.
#
# Preservation happens FIRST. Measured with a PYTHONPATH holding a typer.py that
# raises on import: core starts cleanly (PYTHONPATH unset inside it) while
# ICI_ENTRY_PYTHONPATH still carries the original value.
#
# Only the three Python-specific variables are cleared. QTDIR, LD_LIBRARY_PATH,
# compiler settings and the rest reach core untouched, which is what lets a C++
# analysis inherit the toolchain the user configured.
#
# SSL_CERT_FILE and SSL_CERT_DIR are deliberately NOT cleared: WP01 found this
# runtime links OpenSSL statically, and clearing them loses the system CA.
set -euo pipefail

# readlink -f so that invoking through a symlink on PATH still finds the bundle.
BUNDLE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

export ICI_ENTRY_PATH="${PATH-}"
export ICI_ENTRY_PYTHONPATH="${PYTHONPATH-}"
export ICI_ENTRY_PYTHONHOME="${PYTHONHOME-}"
export ICI_ENTRY_VIRTUAL_ENV="${VIRTUAL_ENV-}"
export ICI_ENTRY_LD_LIBRARY_PATH="${LD_LIBRARY_PATH-}"
export ICI_BUNDLE_ROOT="$BUNDLE"

unset PYTHONPATH PYTHONHOME VIRTUAL_ENV

# bin/python3, not bin/python3.13. The version-qualified name pins the launcher
# to one runtime release, and assemble_bundle.py already reads bin/python3 — the
# two had drifted apart, so a runtime upgrade would have broken only this file.
#
# -I ignores PYTHONPATH and the user site directory. The bundle's own paths go
# on sys.path inside -c rather than through an exported PYTHONPATH, so they are
# never inherited by a project child process.
exec "$BUNDLE/runtime/python/bin/python3" -I -c '
import os
import sys

root = os.environ["ICI_BUNDLE_ROOT"]
sys.path.insert(0, os.path.join(root, "app"))
sys.path.insert(1, os.path.join(root, "app", "vendor"))

# python -c leaves argv[0] as "-c", and Click reads the program name from it,
# so the bundle told every user to run "-c verify". Setting it here rather than
# passing prog_name keeps the launcher independent of core CLI framework.
sys.argv[0] = "ici"

from ici.__main__ import app

app()
' "$@"
