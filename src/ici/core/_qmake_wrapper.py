"""Standalone compiler-wrapper source materialized by qmake capture."""

from __future__ import annotations

WRAPPER_SOURCE = r"""import fcntl
import json
import os
import stat
import sys

MAX_CAPTURE_BYTES = 32 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024


def fail(message):
    sys.stderr.write("ici qmake capture: " + message + "\n")
    return 125


def append_record(path, arguments):
    payload = json.dumps(
        {"arguments": arguments, "directory": os.getcwd()},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_RECORD_BYTES:
        raise OSError("compile invocation exceeds the capture record limit")
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("capture journal is not a regular file")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise OSError("capture journal ownership or permissions changed")
        if metadata.st_size + len(payload) > MAX_CAPTURE_BYTES:
            raise OSError("capture journal exceeds its size limit")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("capture journal write did not progress")
            view = view[written:]
    finally:
        os.close(descriptor)


def main():
    arguments = sys.argv[1:]
    if not arguments:
        return fail("original compiler command is missing")
    if "-c" in arguments:
        journal = os.environ.get("ICI_QMAKE_CAPTURE_PATH", "")
        if not journal:
            return fail("capture journal was not configured")
        try:
            append_record(journal, arguments)
        except (OSError, UnicodeError, ValueError) as error:
            return fail(str(error)[:300])
    try:
        os.execvp(arguments[0], arguments)
    except OSError as error:
        return fail(str(error)[:300])
    return 125


if __name__ == "__main__":
    raise SystemExit(main())
"""

__all__ = ["WRAPPER_SOURCE"]
