"""The stable/next namespace boundary, pinned until #265 removes stable.

During the transition the two paths share ``src/ici`` — and eleven leaf names
mean a stable module in one place and a next module in another
(``ici.core.cache`` vs ``ici.execution.cache``). Importing the wrong one
compiles fine and fails later; a reviewer's muscle memory imports the wrong
side without a second thought.

This test makes the hazard machine-checked rather than memorized:

- the cross-side collision inventory below is exact — renaming one side is a
  deliberate act, and the inventory must be edited in the same commit;
- adding a *new* name that exists on both sides of the line is refused;
- ``ici.config_schema`` vs the ``ici.config`` package is pinned as the
  known package/module shadow.

When #265 deletes the stable modules the stable set shrinks to nothing and
the test still stands: a new next module may not collide with a stable one
that is gone (the inventory records what "stable side" meant).
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ici"

#: Packages that belong to the stable path (scheduled for #265 removal) —
#: plus the loose modules at the package root that are stable code.
STABLE_NAMESPACES = ("core", "engines", "reporters")

#: Packages that belong to the next path.
NEXT_NAMESPACES = (
    "adapters",
    "application",
    "cli",
    "config",
    "domain",
    "execution",
    "languages",
    "reporting",
    "toolchain",
    "workspace",
)

#: The known cross-side collisions, each documented with the two meanings.
#: Format: leaf name → (stable path, next path).
KNOWN_CROSS_SIDE_COLLISIONS: dict[str, tuple[str, str]] = {
    "base": ("ici.engines.base — stable EngineBase", "ici.adapters.providers.base — next Provider"),
    "baseline": (
        "ici.core.baseline — stable baseline files",
        "ici.application.baseline — next baseline",
    ),
    "cache": (
        "ici.core.cache — stable analysis cache",
        "ici.execution.cache — next observation cache",
    ),
    "cycles": (
        "ici.reporters.html.sections.cycles — stable report",
        "ici.languages.cycles — next check",
    ),
    "integration": (
        "ici.engines.integration — stable integration engine",
        "ici.languages.integration / ici.adapters.providers.integration — next check/provider",
    ),
    "publish": (
        "ici.engines.publish — stable publish engine",
        "ici.application.publish — next publisher",
    ),
    "registry": (
        "ici.engines.registry — stable engine factory registry",
        "ici.languages.registry — next check registry",
    ),
    "report": (
        "ici.reporters.html.report — stable HTML report",
        "ici.application.report — next assembly",
    ),
    "sanitize": (
        "ici.engines.sanitize — stable engine",
        "ici.adapters.providers.sanitize — next provider",
    ),
    "sarif": (
        "ici.reporters.sarif — stable SARIF writer",
        "ici.reporting.sarif — next SARIF writer",
    ),
    "toolchain": (
        "ici.core.toolchain — stable tool discovery",
        "ici.domain.toolchain — next toolchain types",
    ),
    "verify": (
        "ici.engines.verify — stable orchestrator",
        "ici.application.verify — next gate application",
    ),
}

#: Names whose leaf collides *within* one side by design — the reporters'
#: section modules mirror their engines on purpose.
SAME_SIDE_MIRRORS = frozenset(
    {
        ("engines", "reporters.html.sections"),
    }
)


def _modules() -> dict[str, set[str]]:
    """Leaf module name → the set of dotted paths that provide it."""

    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        dotted = str(path.relative_to(SRC.parent).with_suffix("")).replace("/", ".")
        found.setdefault(path.stem, set()).add(dotted)
    return found


def _side(dotted: str) -> str | None:
    parts = dotted.split(".")[1:]  # drop "ici"
    namespace = parts[0] if parts else ""
    if namespace in STABLE_NAMESPACES:
        return "stable"
    if namespace in NEXT_NAMESPACES:
        return "next"
    return None  # package-root modules (config_schema.py, __main__.py, ...)


def test_the_known_collisions_still_exist_and_mean_what_they_say() -> None:
    modules = _modules()
    for leaf, (stable_meaning, next_meaning) in KNOWN_CROSS_SIDE_COLLISIONS.items():
        paths = modules.get(leaf, set())
        sides = {_side(p) for p in paths}
        assert "stable" in sides and "next" in sides, (
            f"{leaf}: expected a stable and a next module — {stable_meaning} vs {next_meaning}. "
            f"Found: {sorted(paths)}. If you renamed one side, update the inventory."
        )


def test_no_new_cross_side_collision_is_added() -> None:
    modules = _modules()
    offenders: list[str] = []
    for leaf, paths in sorted(modules.items()):
        sides = {_side(p) for p in paths} - {None}
        if sides == {"stable", "next"} and leaf not in KNOWN_CROSS_SIDE_COLLISIONS:
            offenders.append(f"{leaf}: {sorted(paths)}")
    assert not offenders, (
        "new module names that collide across the stable/next boundary — "
        "rename one side or record the collision in KNOWN_CROSS_SIDE_COLLISIONS:\n"
        + "\n".join(offenders)
    )


def test_the_config_package_module_shadow_is_still_the_only_one() -> None:
    """``ici.config_schema`` (stable) vs the ``ici.config`` package (next)."""

    assert (SRC / "config_schema.py").is_file()
    assert (SRC / "config" / "__init__.py").is_file()
    other_shadows = []
    for module in SRC.glob("*.py"):
        if module.name in {"__init__.py", "__main__.py"}:
            continue
        package = SRC / module.stem
        if package.is_dir() and module.stem != "config_schema":
            other_shadows.append(module.name)
    # config_schema.py vs config/ is the pinned pair; flag any new x.py + x/ pair.
    shadows = [f"{m} vs {m[:-3]}/" for m in other_shadows]
    assert not shadows, f"new module/package shadows: {shadows}"


#: The cross-boundary imports that exist today, pinned as an allowlist.
#: next code may consume the stable analysis core — the ``engines._*``
#: helper modules and a handful of shared primitives — and stable code may
#: consume next substrate (the common executor, TEM scoring, config
#: composition). Anything beyond this list is a new coupling point and must
#: be argued for by editing the list in the same commit.
NEXT_TO_STABLE_ALLOWED: frozenset[str] = frozenset(
    {
        "ici.core.backend",
        "ici.core.compile_db",
        "ici.core.context",
        "ici.core.models",
        "ici.core.pipeline",
        "ici.core.runner",
        "ici.engines",  # config/schema.py reads the registry
        "ici.analysis.binary_abi",
        "ici.analysis.cpp_complexity",
        "ici.analysis.coverage_support",
        "ici.analysis.cycles",
        "ici.analysis.gcov_json",
        "ici.analysis.line_count",
        "ici.analysis.pytest_output",
        "ici.reporters.issue_view",
        # The shared analysis core — underscore modules are the documented
        # helpers both paths call until #265 relocates them.
        "ici.analysis._cpp_cognitive",
        "ici.analysis._cpp_diagnostics",
        "ici.analysis._cpp_dup_tokenization",
        "ici.analysis._dup_matching",
        "ici.analysis._dup_regions",
        "ici.analysis._elf",
        "ici.analysis._exception_rules",
        "ici.analysis._python_compatibility",
        "ici.analysis._python_dead_code",
        "ici.analysis._python_dup_tokenization",
        "ici.analysis._python_metrics",
        "ici.analysis._python_resources",
        "ici.analysis._python_security",
        "ici.analysis._sanitizer_diagnostics",
        "ici.analysis._source_inputs",
    }
)

STABLE_TO_NEXT_ALLOWED: frozenset[str] = frozenset(
    {
        "ici.application.tem",
        "ici.config",
        "ici.execution.process",
    }
)


def _imports_of(module: Path) -> set[str]:
    import ast

    tree = ast.parse(module.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_new_cross_boundary_imports_are_explicitly_registered() -> None:
    offenders: list[str] = []
    for module in SRC.rglob("*.py"):
        relative = str(module.relative_to(SRC))
        namespace = relative.split("/")[0]
        for imported in _imports_of(module):
            parts = imported.split(".")
            if len(parts) < 2 or parts[0] != "ici":
                continue
            target_side = (
                "stable"
                if parts[1] in STABLE_NAMESPACES
                else ("next" if parts[1] in NEXT_NAMESPACES else None)
            )
            if target_side is None:
                continue
            if (
                namespace in NEXT_NAMESPACES
                and target_side == "stable"
                and imported not in NEXT_TO_STABLE_ALLOWED
            ):
                offenders.append(f"{relative} imports {imported}")
            if (
                namespace in STABLE_NAMESPACES
                and target_side == "next"
                and imported not in STABLE_TO_NEXT_ALLOWED
            ):
                offenders.append(f"{relative} imports {imported}")
    assert not offenders, (
        "new cross-boundary imports — add the module to the allowlist in "
        "test_namespace_boundaries.py only if the coupling is intentional:\n" + "\n".join(offenders)
    )
