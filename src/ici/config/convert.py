"""Translate a stable-path ``ici.toml`` document into the next schema.

This is the *convert* half of the migration the other half — ``migration.py``
— only reports. The split is deliberate: explaining what layered legacy files
do to each other is a read; producing a candidate next config is a decision
the user previews before anything is written (#225 item 2).

Every key the stable schema knows lands in exactly one of three places:

- **converted** — a next-path key carries the same meaning;
- **confirm** — a judgment call the preview names rather than guesses;
- **unsupported** — no next-path equivalent exists, and the note says what
  stands in for it or why nothing does.

Nothing is silently dropped: a key that reaches neither the document nor a
note would be a value the user set that nobody told them vanished.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

__all__ = ["Note", "convert_document"]


@dataclass(frozen=True)
class Note:
    """One judgement the preview must show, keyed by the source key."""

    key: str
    disposition: str  #: "converted" | "confirm" | "unsupported"
    detail: str

    def __str__(self) -> str:
        return f"[{self.disposition}] {self.key}: {self.detail}"


#: Stable engine id → the next checks that cover it. One engine maps to
#: several checks because the next registry splits by language; a key that
#: enabled `lint` enabled ruff *and* clang-tidy, and folding them back into
#: one switch would hide that.
_ENGINE_TO_CHECKS: dict[str, tuple[str, ...]] = {
    "line": ("python.line", "cpp.line"),
    "lint": ("python.lint", "cpp.tidy", "cpp.diagnostics"),
    "compile_db": ("cpp.compile",),
    "test": ("python.test", "python.coverage", "cpp.test", "cpp.coverage"),
    "type": ("python.type",),
    "python_compat": ("python.compat", "python.compat-runtime"),
    "complexity": ("python.complexity", "cpp.complexity"),
    "cognitive": ("python.cognitive", "cpp.cognitive"),
    "sanitize": ("cpp.sanitize",),
    "thread_sanitize": ("cpp.tsan",),
    # The C++ dead-code linker path was never migrated — the WP20 disposition
    # table defers it — so only the Python side converts.
    "dead": ("python.dead",),
    "dup": ("python.dup", "cpp.dup"),
    "exception": ("python.exception", "cpp.exception"),
    "cycle": ("python.cycle", "cpp.cycle"),
    "security": ("python.security",),
    "resource": ("python.resource",),
    "build": ("cpp.artifact",),
    "binary_compat": ("cpp.binary-compat",),
    "integration": ("integration",),
}

#: Engine keys that are not enabled/mode/required. They tune a gate the next
#: schema expresses differently — quality policy lives on the check's
#: `required` flag and the profile, not on per-engine thresholds — so none of
#: them can be carried verbatim.
_TUNING_KEYS: dict[str, str] = {
    "warn_limit": "no per-check warn threshold; the gate is fail-on-violation",
    "fail_limit": "no per-check fail threshold; the gate is fail-on-violation",
    "gate_dirs": "scope is component `sources`/`include`/`exclude`, not a gate",
    "include_dirs": "component `include` globs",
    "exclude_dirs": "component `exclude` globs",
    "min_tem_score": "TEM is recorded as a metric; no threshold key exists",
    "min_line_cov": "no coverage threshold key; coverage is evidence, not a gate knob",
    "min_file_cov": "no coverage threshold key",
    "min_file_statements": "no coverage threshold key",
    "min_branch_cov": "no coverage threshold key",
    "min_func_cov": "no coverage threshold key",
    "min_changed_line_cov": "no coverage threshold key",
    "changed_lines": "no changed-lines gate in the next policy",
    "max_coverage_regression": "no regression threshold; use `next diff` against a baseline",
    "warn_cc": "no per-check threshold; the gate is fail-on-violation",
    "fail_cc": "no per-check threshold",
    "warn_nesting": "no per-check threshold",
    "warn_pct": "no per-check threshold",
    "fail_pct": "no per-check threshold",
    "min_window": "not configurable; the duplicate detector's window is fixed",
    "warn": "no per-check threshold",
    "fail": "no per-check threshold",
    "max_reported": "not configurable",
    "slow_test_threshold": "recorded as a measurement; no threshold key",
    "slow_threshold": "recorded as a measurement; no threshold key",
    "max_slow_tests": "no threshold key",
}


_CPP_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ui", ".qrc"})


def convert_document(
    stable: dict[str, Any],
    *,
    workspace_name: str,
    component_root: str,
    workspace_root: Path | None = None,
) -> tuple[str, tuple[Note, ...]]:
    """Convert one stable config document to next-schema TOML text + notes.

    The caller owns file discovery — which file, which layer, whether the
    result is written at all. This function only answers: what would the same
    intent look like in the next schema, and what did not carry over.
    """

    notes: list[Note] = []
    document: dict[str, Any] = {"schema_version": 1}

    workspace: dict[str, Any] = {"name": workspace_name}
    ici = _table(stable, "ici")
    if profile := ici.get("profile"):
        workspace["profile"] = profile
        notes.append(Note("ici.profile", "converted", "workspace.profile"))
    for key in ("version", "policy_name"):
        if key in ici:
            notes.append(
                Note(f"ici.{key}", "unsupported", "the next schema derives this; it is not set")
            )
    document["workspace"] = workspace

    component = _component(stable, component_root, workspace_root, notes)
    document["components"] = [component]

    checks = _checks(stable, notes)
    if checks:
        document["checks"] = checks

    _leftovers(stable, notes)
    header = (
        "# Written by `ici next migrate` — a preview, applied only when you ask.\n"
        "# Review the [confirm] and [unsupported] notes before using this file.\n"
    )
    return header + tomli_w.dumps(document), tuple(notes)


def _component(
    stable: dict[str, Any],
    root: str,
    workspace_root: Path | None,
    notes: list[Note],
) -> dict[str, Any]:
    project = _table(stable, "project")
    component: dict[str, Any] = {"id": project.get("name") or "main"}
    component["root"] = root

    if project.get("source_dirs"):
        component["sources"] = [f"{item}/**" for item in project["source_dirs"]]
        notes.append(
            Note(
                "project.source_dirs",
                "converted",
                "component `sources` globs; review — the stable value named directories, "
                "the next one wants workspace-relative glob patterns",
            )
        )
    build = _table(stable, "build")
    if (python_table := _table(build, "python")) and (entrypoint := python_table.get("entrypoint")):
        notes.append(
            Note(
                "build.python.entrypoint",
                "confirm",
                f"there is no entrypoint key; the suite runs under the component's "
                f"declared interpreter — set `python.executable` (was {entrypoint!r}?)",
            )
        )
    for key in ("cpp_pkg_config", "cpp_external_build_dirs", "compile_database"):
        if key in project:
            notes.append(
                Note(
                    f"project.{key}",
                    "confirm",
                    "declare a `[[builds]]` entry so the compile database is found "
                    "explicitly — the next path never guesses a build directory",
                )
            )
    for key in ("type", "version"):
        if key in project:
            notes.append(Note(f"project.{key}", "unsupported", "the next schema does not carry it"))
    component["languages"] = _languages(stable, workspace_root, notes)
    return component


def _languages(stable: dict[str, Any], workspace_root: Path | None, notes: list[Note]) -> list[str]:
    """What the tree says it contains, not what the config guesses.

    The stable schema never declared languages — the engines just ran. The
    next schema makes them explicit, so the honest source is the files.
    Without a root to scan the claim would be invented; then the component
    declares Python only and a note says why.
    """

    if workspace_root is None:
        notes.append(
            Note(
                "components[].languages",
                "confirm",
                "no tree to scan — declared as python; add cpp if the project has it",
            )
        )
        return ["python"]

    found: set[str] = set()
    for path in workspace_root.rglob("*"):
        if not path.is_file() or any(
            part.startswith(".") or part in {"node_modules", "build"}
            for part in path.relative_to(workspace_root).parts
        ):
            continue
        if path.suffix == ".py":
            found.add("python")
        elif path.suffix in _CPP_SUFFIXES:
            found.add("cpp")
    languages = sorted(found) or ["python"]
    if found:
        notes.append(
            Note(
                "components[].languages",
                "converted",
                f"detected from the tree: {', '.join(languages)}",
            )
        )
    return languages


def _checks(stable: dict[str, Any], notes: list[Note]) -> dict[str, Any]:
    engines = _table(stable, "engines")
    checks: dict[str, dict[str, Any]] = {}
    for engine, settings in engines.items():
        if not isinstance(settings, dict):
            notes.append(Note(f"engines.{engine}", "unsupported", "not a table"))
            continue
        targets = _ENGINE_TO_CHECKS.get(engine)
        if targets is None:
            notes.append(
                Note(
                    f"engines.{engine}",
                    "unsupported",
                    "no next check covers this engine — it is dropped, not silently kept",
                )
            )
            continue
        for key, value in settings.items():
            if key == "mode":
                notes.append(
                    Note(
                        f"engines.{engine}.mode",
                        "confirm",
                        "the next path uses `workspace.profile` (fast/standard/deep) "
                        "instead of per-engine modes",
                    )
                )
                continue
            if key in ("enabled", "required"):
                for target in targets:
                    checks.setdefault(target, {})[key] = value
                notes.append(
                    Note(
                        f"engines.{engine}.{key}",
                        "converted",
                        f"applied to {', '.join(targets)}",
                    )
                )
                continue
            notes.append(
                Note(
                    f"engines.{engine}.{key}",
                    "confirm" if key in _TUNING_KEYS else "unsupported",
                    _TUNING_KEYS.get(key, "no next-path equivalent"),
                )
            )
    return checks


def _leftovers(stable: dict[str, Any], notes: list[Note]) -> None:
    if tools := _table(stable, "doctor").get("required_tools"):
        notes.append(
            Note(
                "doctor.required_tools",
                "unsupported",
                f"next `doctor` reports what the selected checks need — the list "
                f"({tools!r}) is declared nowhere",
            )
        )
    for key in stable:
        if key not in {"ici", "project", "engines", "build", "doctor", "name", "type", "version"}:
            notes.append(Note(key, "confirm", "not a known stable section — left for review"))


def _table(document: dict[str, Any], key: str) -> dict[str, Any]:
    found = document.get(key)
    return found if isinstance(found, dict) else {}
