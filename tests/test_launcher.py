"""Tests launcher.sh and env.py candidate synchronization."""

import re
from pathlib import Path

from ici.core.env import PYTHON_CANDIDATES


def test_launcher_candidates_sync():
    """Ensures launcher.sh candidates list matches env.py PYTHON_CANDIDATES exactly in order."""
    launcher_path = Path(__file__).resolve().parent.parent / "scripts/launcher.sh"
    content = launcher_path.read_text(encoding="utf-8")

    # Match: for c in "$ICI_PYTHON" ... ; do
    m = re.search(r'for c in "\$ICI_PYTHON"\s+([^;]+); do', content)
    assert m is not None, "launcher.sh loop pattern not found"

    tokens = m.group(1).split()
    assert tokens == PYTHON_CANDIDATES, f"launcher.sh tokens {tokens} != env.py {PYTHON_CANDIDATES}"
