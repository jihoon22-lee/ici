"""External process failure fixture; never packaged or imported by ici runtime."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]
if mode == "echo":
    os.write(1, sys.argv[2].encode())
    os.write(2, sys.argv[3].encode())
    sys.exit(int(sys.argv[4]))
if mode == "binary":
    os.write(1, b"\x00\xffpartial\xfe")
    os.write(2, b"\x80stderr")
elif mode == "flood":
    for _ in range(128):
        os.write(1, b"a" * 8192)
        os.write(2, b"b" * 8192)
elif mode in ("sleep", "ignore-term"):
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(str(os.getpid()))
    os.write(1, b"READY\n")
    time.sleep(30)
elif mode == "descendant":
    child = subprocess.Popen([sys.executable, __file__, "ignore-term"])
    Path(sys.argv[2]).write_text(f"{os.getpid()} {child.pid}")
    os.write(1, b"PARENT-EXIT\n")
    # Deliberately return while a descendant holds stdout and stderr open.
elif mode == "signal":
    os.kill(os.getpid(), signal.SIGTERM)
elif mode == "environment":
    print(json.dumps(dict(os.environ), sort_keys=True))
elif mode == "resource":
    marker = Path(sys.argv[2])
    descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        time.sleep(0.12)
        with Path(sys.argv[3]).open("a") as output:
            output.write(sys.argv[4] + "\n")
    finally:
        marker.unlink()
elif mode == "parallel":
    directory = Path(sys.argv[2])
    marker = directory / str(os.getpid())
    marker.touch()
    try:
        time.sleep(0.08)
        print(len(list(directory.iterdir())))
        time.sleep(0.08)
    finally:
        marker.unlink()
elif mode == "hold-lock":
    with Path(sys.argv[2]).open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        Path(sys.argv[3]).write_text(str(os.getpid()))
        time.sleep(30)
elif mode == "once":
    with Path(sys.argv[2]).open("a") as output:
        output.write("executed\n")
else:
    raise ValueError(f"unknown fixture mode: {mode}")
