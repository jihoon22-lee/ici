"""A private HOME and XDG tree for a capture process (development-only helper).

Frozen stable commands read and, on a first run, write the per-user global policy
(``$XDG_CONFIG_HOME/ici`` or ``~/.config/ici``) and keep the analysis cache under
``$XDG_CACHE_HOME/ici`` (``ICI_CACHE_DIR`` overrides it); project tools such as pytest,
coverage and mypy keep their own caches under the home directory too. A capture that
inherits the real environment therefore reads the user's own policy and grows the user's
caches. ``private_home()`` points HOME and every XDG base directory at a new temporary
directory outside any captured project root, drops ``ICI_CACHE_DIR`` and ``ICI_CONFIG`` for
the duration, and restores the previous values afterwards. Subprocesses spawned with
``dict(os.environ, ...)`` and frozen code called in-process both see the private tree. The
Rust tool installations (``CARGO_HOME``/``RUSTUP_HOME``) keep their current location so the
toolchain still resolves; a capture project without dependencies fetches nothing into them.

Scripts may run with ``python -I`` (no script directory on ``sys.path``), so each one loads
this file by path through ``importlib.util.spec_from_file_location``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

DIRECTORIES = {
    "HOME": "home",
    "XDG_CONFIG_HOME": "config",
    "XDG_CACHE_HOME": "cache",
    "XDG_DATA_HOME": "data",
    "XDG_STATE_HOME": "state",
}
REMOVED = ("ICI_CACHE_DIR", "ICI_CONFIG")
# Tool installations located through HOME (rustup proxies and Cargo); they stay where they are
# so `cargo`/`rustc` still resolve their toolchain under the private HOME.
INSTALLATIONS = {"CARGO_HOME": ".cargo", "RUSTUP_HOME": ".rustup"}


@contextlib.contextmanager
def private_home() -> Iterator[Path]:
    names = (*DIRECTORIES, *REMOVED, *INSTALLATIONS)
    previous = {name: os.environ.get(name) for name in names}
    installations = {
        name: os.environ.get(name) or str(Path.home() / directory)
        for name, directory in INSTALLATIONS.items()
    }
    with tempfile.TemporaryDirectory(prefix="ici-capture-home-") as temporary:
        base = Path(temporary)
        for name, directory in DIRECTORIES.items():
            (base / directory).mkdir()
            os.environ[name] = str(base / directory)
        for name in REMOVED:
            os.environ.pop(name, None)
        for name, value in installations.items():
            if Path(value).is_dir():
                os.environ[name] = value
        try:
            yield base
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
