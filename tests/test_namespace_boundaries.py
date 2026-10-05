"""The stable shell is gone — this test pins what "gone" means.

The transition-era version of this file pinned the cross-side collision
inventory while ``ici.engines`` and ``ici.reporters`` coexisted with the next
namespaces. With the shell deleted the hazard inverts: a module reappearing
under a retired stable name is no longer a collision risk, it is an
unreviewed resurrection — ``ici.core.cache`` once meant the stable analysis
cache while ``ici.execution.cache`` is the next observation cache, and a new
file at the old path would silently re-enter the name a reader already filed
under a different meaning.

So the test now asserts three things:

- the stable namespace directories and loose modules do not exist;
- the retired leaf names below stay retired — adding one back is a deliberate
  act and this inventory is edited in the same commit;
- no ``x.py`` + ``x/`` package shadow pair exists at the package root
  (``ici.config_schema`` vs ``ici.config`` was the pinned offender).
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ici"

#: Dotted paths the stable shell occupied, retired with it. A new module at
#: one of these paths fails loudly rather than reoccupying a name whose
#: meaning the codebase already spent.
RETIRED_STABLE_PATHS: frozenset[str] = frozenset(
    {
        "engines",
        "reporters",
        "config_schema.py",
        "doctor.py",
        "compilation_export_cli.py",
        "_analysis_config.py",
        "_config_paths.py",
        "_config_validation.py",
        "cli/cutover.py",
        "core/baseline.py",
        "core/cache.py",
        "core/cache_codec.py",
        "core/cache_identity.py",
        "core/cmake.py",
        "core/cmake_context.py",
        "core/compilation_export.py",
        "core/make.py",
        "core/pipeline.py",
        "core/python_rule_registry.py",
        "core/python_rules.py",
        "core/qmake_context.py",
        "core/redaction.py",
        "core/result_keys.py",
        "core/support.py",
    }
)


def test_the_stable_shell_is_gone() -> None:
    resurrected = sorted(p for p in RETIRED_STABLE_PATHS if (SRC / p).exists())
    assert not resurrected, (
        "retired stable paths reappeared — adding a module back under a retired "
        "name is a deliberate act: remove it from RETIRED_STABLE_PATHS in the "
        "same commit that reintroduces it:\n" + "\n".join(resurrected)
    )


def test_no_module_package_shadow_at_the_root() -> None:
    """``ici.config_schema`` vs ``ici.config`` was the pinned pair; keep it the
    only shape this failure mode ever took."""

    shadows = []
    for module in SRC.glob("*.py"):
        if module.name in {"__init__.py", "__main__.py"}:
            continue
        if (SRC / module.stem).is_dir():
            shadows.append(f"{module.name} vs {module.stem}/")
    assert not shadows, f"new module/package shadows: {shadows}"


def test_core_is_shared_substrate_not_a_stable_namespace() -> None:
    """What survived in ``ici.core`` is shared substrate the next path consumes
    — models, findings, runner, project — not a side that can grow a stable
    sibling again. New ``core`` modules must not import a namespace that no
    longer exists; this asserts the boundary stayed honest."""

    import ast

    offenders = []
    for module in (SRC / "core").rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for imported in names:
                if imported.startswith(("ici.engines", "ici.reporters")):
                    offenders.append(f"{module.name} imports {imported}")
    assert not offenders, "core modules importing retired namespaces:\n" + "\n".join(offenders)
