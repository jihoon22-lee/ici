"""Proving a built binary actually carries the instrumentation a variant claims.

A ``[builds.<id>] variant = "sanitize"`` declaration is a claim — running the
suite under sanitizer env vars proves nothing if the binaries were never
compiled with ``-fsanitize``. The runtime embeds a marker that survives in
the file: the shared library's ``DT_NEEDED`` name and the init symbols it
references. This module scans for exactly that evidence — a byte-level
substring search, no toolchain — and answers three ways:

- ``True`` — a marker is present: the binary is instrumented.
- ``False`` — the file was read fully and no marker exists.
- ``None`` — the file could not be checked (unreadable, too large): the
  instrumentation claim is *unproven*, which is not the same as false and
  never counts as satisfied.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["MAX_BINARY_BYTES", "ctest_binaries", "is_elf", "sanitizer_marked"]

#: A binary larger than this is not scanned — the instrumentation claim is
#: unproven rather than searched forever.
MAX_BINARY_BYTES = 256 * 1024 * 1024

#: Runtime markers per declared variant — the shared object name and the
#: runtime's init/handler symbols, both present in an instrumented binary's
#: dynamic section.
_MARKERS = {
    "sanitize": (b"libasan", b"libubsan", b"__asan_init", b"__ubsan_handle"),
    "thread-sanitize": (b"libtsan", b"__tsan_init"),
}

#: ``add_test([=[name]=] "command" ...)`` — the command is the executable.
_ADD_TEST_RE = re.compile(r"add_test\s*\(\s*(?:\[=*\[)?[^\s\)\]\"]+\]?=*\s*\"?([^\s\")]+)")

_ELF_MAGIC = b"\x7fELF"
_MAX_TESTFILE_BYTES = 1024 * 1024


def is_elf(path: Path) -> bool | None:
    """Whether the file is an ELF object — ``None`` when it cannot be read."""

    try:
        with path.open("rb") as stream:
            return stream.read(4) == _ELF_MAGIC
    except OSError:
        return None


def sanitizer_marked(binary: Path, variant: str) -> bool | None:
    """Whether ``binary`` carries the markers ``variant`` requires."""

    markers = _MARKERS.get(variant, ())
    if not markers:
        return None
    try:
        stat = binary.stat()
    except OSError:
        return None
    if not stat.st_size or stat.st_size > MAX_BINARY_BYTES:
        return None
    try:
        payload = binary.read_bytes()
    except OSError:
        return None
    if not payload.startswith(_ELF_MAGIC):
        # A script or data file cannot carry sanitizer instrumentation —
        # that is a definitive absence, not an unproven claim.
        return False
    return any(marker in payload for marker in markers)


def ctest_binaries(root: Path, build_dir: str) -> tuple[Path, ...]:
    """The executables a build tree's ``add_test`` entries name.

    Every generated ``CTestTestfile.cmake`` is read — ctest recurses the
    same files — and each command is resolved against the file's own
    directory, which is where the generated build put the binary.
    """

    base = root / build_dir
    if not base.is_dir():
        return ()
    found: list[Path] = []
    try:
        test_files = sorted(base.rglob("CTestTestfile.cmake"))
    except OSError:
        return ()
    for test_file in test_files:
        try:
            text = test_file.read_bytes()[: _MAX_TESTFILE_BYTES + 1].decode("utf-8", "replace")
        except OSError:
            continue
        for match in _ADD_TEST_RE.finditer(text):
            command = match.group(1)
            candidate = Path(command)
            if not candidate.is_absolute():
                candidate = test_file.parent / candidate
            if candidate.is_file():
                found.append(candidate)
    return tuple(dict.fromkeys(found))
