"""The domain must stay pure, and the test has to prove it rather than ask.

#200 step 7 requires that filesystem discovery and subprocess calls are kept
out of the domain and that the restriction is tested. Two things are checked
here, because a docstring promising purity is exactly what stops being true
first.

1. Import graph. No module under ``ici.domain`` may import ``os``,
   ``pathlib``, ``subprocess`` and friends, and none of them may reach into
   ``ici.core`` — except ``ici.domain.legacy``, which exists to bridge the two
   and is allowed ``ici.core.models`` only.

2. Side effects at import time. Importing the package must not touch the
   filesystem or start a process. This is checked by actually breaking those
   operations and then importing, which catches a call the AST scan would miss
   (an indirect one through a helper, say).

The AST scan deliberately reads source rather than inspecting loaded modules:
an ``if TYPE_CHECKING`` import is invisible at runtime but would still be a
design violation if it pointed the wrong way, and a scan sees it.
"""

from __future__ import annotations

import ast
import builtins
import subprocess
import sys
from pathlib import Path

import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "src" / "ici" / "domain"

# Modules that imply I/O. ``re``, ``enum``, ``dataclasses``, ``typing`` and
# ``collections.abc`` are fine: they compute, they do not reach out.
FORBIDDEN_MODULES = frozenset(
    {
        "glob",
        "io",
        "multiprocessing",
        "os",
        "os.path",
        "pathlib",
        "shutil",
        "socket",
        "subprocess",
        "tempfile",
        "threading",
        "urllib",
        "urllib.request",
    }
)

# ``pathlib.PurePosixPath`` is the one exception, and only in the validator: it
# is pure string algebra with no filesystem access, and re-implementing POSIX
# path parsing by hand to satisfy a lint rule would be worse than allowing it.
PURE_PATH_ALLOWED = {"_validation.py": {"pathlib"}}

# Only the declared bridge may see the old models, and only the data module.
CORE_IMPORT_ALLOWED = {"legacy.py": {"ici.core.models"}}


def _domain_sources() -> list[Path]:
    return sorted(path for path in DOMAIN_DIR.glob("*.py"))


def _imported_modules(tree: ast.AST) -> set[str]:
    """Collect every module name a source file imports, including type-only ones."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_domain_package_has_sources():
    """Guard against the scan silently passing because it found nothing."""

    assert len(_domain_sources()) >= 8


@pytest.mark.parametrize("source", _domain_sources(), ids=lambda p: p.name)
def test_no_io_imports(source: Path):
    allowed = PURE_PATH_ALLOWED.get(source.name, set())
    imported = _imported_modules(ast.parse(source.read_text(encoding="utf-8")))
    offending = sorted((imported & FORBIDDEN_MODULES) - allowed)
    assert not offending, f"{source.name} imports I/O modules: {offending}"


@pytest.mark.parametrize("source", _domain_sources(), ids=lambda p: p.name)
def test_only_the_bridge_imports_core(source: Path):
    allowed = CORE_IMPORT_ALLOWED.get(source.name, set())
    imported = _imported_modules(ast.parse(source.read_text(encoding="utf-8")))
    reaching = sorted(
        name for name in imported if name.startswith("ici.core") and name not in allowed
    )
    assert not reaching, f"{source.name} must not import from ici.core: {reaching}"


def test_bridge_imports_only_pure_core_data():
    """``ici.core.models`` is importable here because it is pure data.

    If it ever grows a runtime import of the impure modules it currently keeps
    behind ``TYPE_CHECKING``, the bridge stops being safe and this fails.
    """

    models = Path(__file__).resolve().parents[1] / "src" / "ici" / "core" / "models.py"
    imported = _imported_modules(ast.parse(models.read_text(encoding="utf-8")))
    assert not (imported & FORBIDDEN_MODULES), (
        f"ici.core.models is no longer pure: {sorted(imported & FORBIDDEN_MODULES)}"
    )


def test_importing_the_domain_touches_nothing(monkeypatch):
    """Import the package with file and process access broken.

    This is the check the AST scan cannot make: an indirect call through some
    helper would not appear as a forbidden import but would still run here.
    """

    for name in [key for key in sys.modules if key.startswith("ici.domain")]:
        monkeypatch.delitem(sys.modules, name, raising=False)

    def refuse_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing ici.domain opened a file")

    def refuse_process(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing ici.domain started a process")

    monkeypatch.setattr(builtins, "open", refuse_open)
    monkeypatch.setattr(subprocess, "run", refuse_process)
    monkeypatch.setattr(subprocess, "Popen", refuse_process)

    import ici.domain

    assert ici.domain.SCHEMA_ID == "ici.next.run"
