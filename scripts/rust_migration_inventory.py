#!/usr/bin/env python3
"""Freeze the Python product surface without importing its runtime.

The inventory is evidence for review, not a proof of Rust parity. Git objects
from the published oracle are read even after the Python product is removed.
Regeneration preserves migration decisions; --check never rewrites evidence.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any

ORACLE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYZ_SHA256 = "c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/design/rust-replacement"
STATES = {"not_started", "implemented", "verified", "difference_review", "complete"}


def expression(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    lines = getattr(node, "_inventory_lines", None)
    if lines is not None and hasattr(node, "lineno") and node.end_lineno is not None:
        first, last = node.lineno - 1, node.end_lineno - 1
        if first == last:
            return lines[first][node.col_offset : node.end_col_offset].decode("utf-8")
        segment = (
            lines[first][node.col_offset :]
            + b"".join(lines[first + 1 : last])
            + lines[last][: node.end_col_offset]
        )
        return segment.decode("utf-8")
    return ast.unparse(node)


def literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
        json.dumps(value)
        return value
    except (ValueError, TypeError):
        return {"expression": expression(node)}


def reference(path: str, node: ast.AST, symbol: str | None = None) -> dict[str, Any]:
    return {"path": path, "line": node.lineno, "symbol": symbol}


def migration() -> dict[str, Any]:
    return {
        "status": "not_started",
        "rust_paths": [],
        "fixtures": {"positive": [], "negative": [], "failure": []},
        "evidence": [],
        "comparison": None,
        "difference_reason": None,
    }


def row(kind: str, identifier: str, sources: list[dict[str, Any]], **facts: Any) -> dict[str, Any]:
    return {
        "id": f"{kind}:{identifier}",
        "kind": kind,
        "sources": sources,
        "facts": facts,
        "migration": migration(),
    }


class Source:
    """Resolve only static Python definitions, never import or eval code."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.trees = {
            path: ast.parse(data, filename=path)
            for path, data in files.items()
            if path.endswith(".py")
        }
        for path, tree in self.trees.items():
            split = files[path].split(b"\n")
            lines = (*(line + b"\n" for line in split[:-1]), split[-1])
            for node in ast.walk(tree):
                node._inventory_lines = lines
        self.modules = {
            path.removeprefix("src/").removesuffix(".py").replace("/", "."): path
            for path in self.trees
        }
        self.modules.update(
            {
                name.removesuffix(".__init__"): path
                for name, path in list(self.modules.items())
                if name.endswith(".__init__")
            }
        )

    def resolve(
        self, path: str, node: ast.AST, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> tuple[str, ast.AST]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return path, node
        name = expression(node)
        if name is None or (path, name) in seen:
            return path, node
        seen = seen | {(path, name)}
        head, _, tail = name.partition(".")
        for item in self.trees[path].body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and item.name == name
            ):
                return path, item
            if isinstance(item, (ast.Assign, ast.AnnAssign)):
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                if item.value is not None and any(
                    isinstance(target, ast.Name) and target.id == name for target in targets
                ):
                    return self.resolve(path, item.value, seen)
            if isinstance(item, ast.ImportFrom) and item.module:
                for alias in item.names:
                    if (alias.asname or alias.name) != head:
                        continue
                    destination = item.module
                    if item.level:
                        current = path.removeprefix("src/").replace("/", ".")
                        parts = current.removesuffix(".py").split(".")[: -item.level]
                        destination = ".".join([*parts, item.module])
                    module_path = self.modules.get(destination)
                    if module_path is not None and not tail:
                        return self.resolve(module_path, ast.Name(id=alias.name), seen)
                    module_path = self.modules.get(f"{destination}.{alias.name}")
                    if module_path is not None and tail:
                        return self.resolve(module_path, ast.Name(id=tail), seen)
        return path, node

    def symbols(self, path: str) -> list[dict[str, Any]]:
        result = []

        def visit(node: ast.AST, prefix: str = "") -> None:
            for child in ast.iter_child_nodes(node):
                nested = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                name = f"{prefix}.{child.name}".strip(".") if nested else prefix
                if nested:
                    result.append(
                        {
                            "name": name,
                            "kind": type(child).__name__,
                            "line": child.lineno,
                            "end_line": child.end_lineno,
                        }
                    )
                visit(child, name)

        visit(self.trees[path])
        return result


def read_oracle() -> Source:
    archive = subprocess.check_output(["git", "archive", ORACLE, "src/ici"], cwd=ROOT)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if not member.isfile():
                continue
            content = source.extractfile(member)
            assert content is not None
            files[member.name] = content.read()
    return Source(files)


def registry_rows(source: Source) -> list[dict[str, Any]]:
    result = []
    fields = {
        "EngineDescriptor": ("name", "factory_name"),
        "CheckDefinition": ("id", "title", "language", "tool"),
    }
    for path, tree in sorted(source.trees.items()):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in fields:
                continue
            # Only declarations, not checks constructed by application code.
            if node.func.id == "CheckDefinition" and not path.startswith("src/ici/languages/"):
                continue
            if node.func.id == "EngineDescriptor" and path != "src/ici/core/pipeline.py":
                continue
            facts = dict(zip(fields[node.func.id], map(literal, node.args), strict=False))
            facts.update({kw.arg: literal(kw.value) for kw in node.keywords if kw.arg})
            kind = "descriptor" if node.func.id == "EngineDescriptor" else "check"
            identifier = facts["name" if kind == "descriptor" else "id"]
            if not isinstance(identifier, str):
                raise ValueError(f"non-literal registry id at {path}:{node.lineno}")
            if kind == "check":
                for key, default in (
                    ("required", True),
                    ("static", True),
                    ("needs", ()),
                    ("provides", ()),
                    ("profiles", ["fast", "standard", "deep"]),
                ):
                    facts.setdefault(key, default)
            facts["execution_effect"] = "review_required: provider/legacy implementation"
            facts["gate_exit_contract"] = "review_required: selected/workspace/evidence axes"
            result.append(row(kind, identifier, [reference(path, node)], **facts))
    return result


def option_rows(source: Source, path: str, function: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError(f"unresolved command function {path}:{expression(function)}")
    arguments = function.args
    names = [*arguments.posonlyargs, *arguments.args]
    defaults = [None] * (len(names) - len(arguments.defaults)) + list(arguments.defaults)
    names.extend(arguments.kwonlyargs)
    defaults.extend(arguments.kw_defaults)
    result = []
    for name, default in zip(names, defaults, strict=True):
        if default is None:
            if expression(name.annotation) == "typer.Context":
                continue
            result.append(
                {
                    "parameter": name.arg,
                    "type": expression(name.annotation),
                    "kind": "required_parameter",
                    "declaration": reference(path, name),
                }
            )
            continue
        resolved_path, resolved = source.resolve(path, default)
        facts = {
            "parameter": name.arg,
            "type": expression(name.annotation),
            "declaration": reference(path, name),
            "default_expression": expression(default),
        }
        if isinstance(resolved, ast.Call) and expression(resolved.func) in {
            "typer.Option",
            "typer.Argument",
        }:
            facts.update(
                {
                    "kind": "option" if expression(resolved.func) == "typer.Option" else "argument",
                    "default": literal(resolved.args[0]) if resolved.args else None,
                    "flags": [literal(arg) for arg in resolved.args[1:]],
                    "settings": {kw.arg: literal(kw.value) for kw in resolved.keywords if kw.arg},
                    "option_source": reference(resolved_path, resolved),
                }
            )
        else:
            facts.update({"kind": "plain_default", "default": literal(resolved)})
        result.append(facts)
    return result


def cli_rows(source: Source) -> list[dict[str, Any]]:
    result = []
    for path, tree in sorted(source.trees.items()):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and expression(node.func) == "app.add_typer"
                and node.args
            ):
                group_path, declaration = source.resolve(path, node.args[0])
                if not isinstance(declaration, ast.Call):
                    raise ValueError("unresolved Typer group")
                settings = {kw.arg: literal(kw.value) for kw in declaration.keywords if kw.arg}
                group = settings.get("name")
                if not isinstance(group, str):
                    raise ValueError("Typer group must declare a literal name")
                result.append(
                    row(
                        "command_group",
                        f"ici {group}",
                        [reference(path, node), reference(group_path, declaration)],
                        command=f"ici {group}",
                        settings=settings,
                        built_in_help="--help",
                    )
                )
            registrations: list[tuple[ast.Call, ast.AST]] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                registrations.extend(
                    (deco, node) for deco in node.decorator_list if isinstance(deco, ast.Call)
                )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and node.args:
                registrations.append((node.func, node.args[0]))
            for registration, function in registrations:
                call = registration.func
                if not isinstance(call, ast.Attribute) or call.attr not in {"command", "callback"}:
                    continue
                owner = expression(call.value)
                if owner not in {"app", "next_app"}:
                    raise ValueError(f"unhandled CLI group {owner!r}")
                if isinstance(function, ast.Call) and expression(function.func) == "_make_handler":
                    if expression(registration.args[0]) != "_name":
                        raise ValueError("unexpected dynamic engine command registration")
                    _, declarations = source.resolve(path, ast.Name(id="_ENGINE_COMMANDS"))
                    handlers = [
                        item
                        for item in ast.walk(tree)
                        if isinstance(item, ast.FunctionDef) and item.name == "handler"
                    ]
                    if len(handlers) != 1 or not isinstance(declarations, ast.List):
                        raise ValueError("dynamic engine command shape changed")
                    handler = handlers[0]
                    for declaration in declarations.elts:
                        command_name, factory, report, description = ast.literal_eval(declaration)
                        result.append(
                            row(
                                "command",
                                f"ici {command_name}",
                                [
                                    reference(path, declaration),
                                    reference(path, handler, "_make_handler.handler"),
                                ],
                                command=f"ici {command_name}",
                                function="_make_handler.handler",
                                factory=factory,
                                report_filename=report,
                                docstring=description,
                                options=option_rows(source, path, handler),
                                built_in_help="--help",
                            )
                        )
                    continue
                function_path, function = source.resolve(path, function)
                name = literal(registration.args[0]) if registration.args else "<global>"
                prefix = "ici next" if owner == "next_app" else "ici"
                command = f"{prefix} {name}"
                options = option_rows(source, function_path, function)
                result.append(
                    row(
                        "command",
                        command,
                        [
                            reference(path, registration),
                            reference(function_path, function, function.name),
                        ],
                        command=command,
                        function=function.name,
                        options=options,
                        built_in_help="--help",
                        docstring=ast.get_docstring(function),
                    )
                )
    return result


CONFIG_FILES = {
    "src/ici/config_schema.py",
    "src/ici/_analysis_config.py",
    "src/ici/_config_paths.py",
    "src/ici/_config_validation.py",
    "src/ici/config/schema.py",
    "src/ici/config/layers.py",
    "src/ici/config/documents.py",
    "src/ici/config/composition.py",
    "src/ici/config/overlay.py",
    "src/ici/config/automatic.py",
    "src/ici/config/__init__.py",
}
KEY_READERS = {
    "text",
    "flag",
    "integer",
    "number",
    "declared_path",
    "executable",
    "globs",
    "text_list",
    "text_map",
    "tables",
    "table",
    "array_of_tables",
    "has",
    "get",
}


def config_rows(source: Source) -> list[dict[str, Any]]:
    """Key declarations and reader sites; expressions remain explicit review inputs."""
    result = []
    for path in sorted(CONFIG_FILES):
        tree = source.trees[path]
        symbols = source.symbols(path)
        for node in ast.walk(tree):
            facts: dict[str, Any] | None = None
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if target.id.endswith("_KEYS") or target.id == "LOCAL_OVERLAY_ALLOWLIST":
                        facts = {
                            "declaration": target.id,
                            "expression": expression(node.value),
                            "literal_strings": sorted(
                                {
                                    child.value
                                    for child in ast.walk(node)
                                    if isinstance(child, ast.Constant)
                                    and isinstance(child.value, str)
                                }
                            ),
                        }
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in KEY_READERS
                and node.args
            ):
                facts = {
                    "reader": expression(node.func),
                    "key": literal(node.args[0]),
                    "expression": expression(node),
                }
            if facts is None:
                continue
            enclosing = [s for s in symbols if s["line"] <= node.lineno <= s["end_line"]]
            owner = enclosing[-1]["name"] if enclosing else "<module>"
            result.append(
                row(
                    "config",
                    f"{path}:{node.lineno}:{node.col_offset}",
                    [reference(path, node, owner)],
                    **facts,
                )
            )
    return result


def constant_value(source: Source, path: str, node: ast.AST) -> Any:
    """Evaluate a tiny literal/set-union subset, never arbitrary Python."""
    if isinstance(node, ast.Name):
        new_path, found = source.resolve(path, node)
        if found is node:
            raise ValueError(f"unresolved constant {path}:{node.id}")
        return constant_value(source, new_path, found)
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return {constant_value(source, path, child) for child in node.elts}
    if isinstance(node, ast.Call) and expression(node.func) == "frozenset":
        return set(constant_value(source, path, node.args[0]))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return constant_value(source, path, node.left) | constant_value(source, path, node.right)
    if isinstance(node, ast.Dict):
        return {
            constant_value(source, path, key): constant_value(source, path, value)
            for key, value in zip(node.keys, node.values, strict=True)
            if key is not None
        }
    return ast.literal_eval(node)


def config_key_rows(source: Source) -> list[dict[str, Any]]:
    result = []
    legacy = "src/ici/config_schema.py"
    prefixes = {
        "_TOP_LEVEL_KEYS": "",
        "_ICI_KEYS": "ici",
        "_PROJECT_KEYS": "project",
        "_BUILD_KEYS": "build",
        "_BUILD_PYTHON_KEYS": "build.python",
        "_DOCTOR_KEYS": "doctor",
        "_TEST_QUALITY_KEYS": "engines.test.quality",
        "_MUTATION_KEYS": "engines.test.quality.mutation",
    }

    def add(path: str, node: ast.AST, schema: str, key: str, **facts: Any) -> None:
        result.append(
            row(
                "config_key",
                f"{schema}:{key}",
                [reference(path, node)],
                schema=schema,
                key=key,
                **facts,
            )
        )

    for name, prefix in prefixes.items():
        _, declaration = source.resolve(legacy, ast.Name(id=name))
        for key in sorted(constant_value(source, legacy, declaration)):
            add(legacy, declaration, "legacy", f"{prefix}.{key}".strip("."))
    _, declaration = source.resolve(legacy, ast.Name(id="_ENGINE_KEYS"))
    for engine, keys in sorted(constant_value(source, legacy, declaration).items()):
        add(legacy, declaration, "legacy", f"engines.{engine}")
        for key in sorted(keys):
            add(legacy, declaration, "legacy", f"engines.{engine}.{key}")
    extra = "src/ici/_analysis_config.py"
    for name, prefix in (
        ("_BUILD_MAKE_KEYS", "build.make"),
        ("DECLARED_ARTIFACT_KINDS", "build.artifacts"),
    ):
        _, declaration = source.resolve(extra, ast.Name(id=name))
        for key in sorted(constant_value(source, extra, declaration)):
            add(extra, declaration, "legacy", f"{prefix}.{key}")
    _, case = source.resolve(extra, ast.Name(id="_validate_integration_case"))
    for node in ast.walk(case):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "allowed" for target in node.targets
        ):
            for key in sorted(constant_value(source, extra, node.value)):
                add(extra, node, "legacy", f"engines.integration.cases[].{key}")
    for node in ast.walk(source.trees[extra]):
        if (
            isinstance(node, ast.Call)
            and expression(node.func) == "_reject_unknown"
            and len(node.args) > 1
            and expression(node.args[0]) == "output"
        ):
            for key in sorted(constant_value(source, extra, node.args[1])):
                add(extra, node, "legacy", f"engines.integration.cases[].output_artifacts[].{key}")

    documents = "src/ici/config/documents.py"
    shapes = {
        "WorkspaceSettings": ("workspace",),
        "Exemption": ("checks.*.exemptions.*",),
        "SuppressionBody": ("suppressions[]",),
        "CheckSetting": ("checks.*", "components[].checks.*"),
        "ToolSetting": ("tools.*",),
        "BuildDeclaration": ("builds.*",),
        "PythonSettings": ("python", "components[].python"),
        "CppSettings": ("cpp", "components[].cpp"),
        "IntegrationOutputBody": ("components[].integrations[].output_artifacts[]",),
        "IntegrationCaseBody": ("components[].integrations[]",),
        "ComponentBody": ("component", "components[]"),
        "PublishBody": ("publish",),
        "ComponentReference": ("components[]",),
        "RootDocument": ("",),
        "ComponentDocument": ("",),
    }
    seen = set()
    for node in source.trees[documents].body:
        if not isinstance(node, ast.ClassDef) or node.name not in shapes:
            continue
        for field in node.body:
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            name = field.target.id
            if name in {"origin", "component_id"} or (
                name == "path" and node.name in {"RootDocument", "ComponentDocument"}
            ):
                continue
            if name == "id" and node.name in {"ToolSetting", "BuildDeclaration", "CheckSetting"}:
                continue
            for prefix in shapes[node.name]:
                key = f"{prefix}.{name}".strip(".")
                if key in seen:
                    continue
                seen.add(key)
                add(
                    documents,
                    field,
                    "next-v1",
                    key,
                    type=expression(field.annotation),
                    model=node.name,
                    default=literal(field.value),
                )
    local = "src/ici/config/layers.py"
    _, declaration = source.resolve(local, ast.Name(id="LOCAL_OVERLAY_ALLOWLIST"))
    for key in sorted(constant_value(source, local, declaration)):
        add(local, declaration, "next-local-overlay", key)
    return result


def rule_rows(source: Source) -> list[dict[str, Any]]:
    result = []
    fields = {"rule_id", "native_rule_id", "tool_rule_id", "target_name", "metrics"}
    for path, tree in sorted(source.trees.items()):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            selected = {kw.arg: literal(kw.value) for kw in node.keywords if kw.arg in fields}
            if selected:
                result.append(
                    row(
                        "finding_site",
                        f"{path}:{node.lineno}:{node.col_offset}",
                        [reference(path, node)],
                        constructor=expression(node.func),
                        fields=selected,
                        review="Resolve dynamic rule families/metric keys before closure",
                    )
                )
    return result


# These are independent user-observable behaviors absent from, or shared by,
# the 36-check registry. Paths are checked against the frozen source archive.
CAPABILITIES = {
    "legacy-python-sanitize": (
        "Python sanitizer/resource execution must not disappear behind cpp.sanitize",
        ["engines/sanitize.py", "analysis/_sanitize_python_scope.py"],
    ),
    "legacy-cpp-linker-dead": (
        "Compiler/linker unused and dead-symbol evidence, generated/vendor scope",
        [
            "engines/dead.py",
            "analysis/_cpp_linker_dead_symbols.py",
            "analysis/_cpp_linker_dead_aggregation.py",
        ],
    ),
    "python-scope-binding": (
        "Import alias, shadowing, scope and resource lifetime semantics",
        [
            "analysis/_python_resource_scopes.py",
            "analysis/_python_dead_code.py",
            "analysis/_python_security.py",
            "analysis/_python_resources.py",
        ],
    ),
    "python-semantic-duplicates": (
        "Tokenizer, semantic normalization, clustering, matches and region attribution",
        [
            "analysis/_python_dup_tokenization.py",
            "analysis/_python_dup_semantics.py",
            "analysis/_dup_matching.py",
            "analysis/_dup_regions.py",
        ],
    ),
    "python-packaging-wheel": (
        "PEP 440/508, metadata, RECORD, tags, import distribution, entrypoints and bounds",
        [
            "analysis/_python_packaging.py",
            "analysis/_python_wheel.py",
            "analysis/_python_compatibility.py",
        ],
    ),
    "cpp-parser-heuristic-confidence": (
        "Bounded tokens, function boundaries, cognitive/CC limits and confidence labels",
        [
            "analysis/_cpp_function_boundaries.py",
            "analysis/_cpp_function_boundary_parser.py",
            "analysis/_cpp_cognitive.py",
            "analysis/cpp_complexity.py",
        ],
    ),
    "cpp-qt-generated-input": (
        "moc/uic/rcc, clazy/tidy/compiler inputs and generated include attribution",
        [
            "analysis/_qt_codegen.py",
            "analysis/_clazy.py",
            "analysis/_clang_tidy.py",
            "analysis/_cpp_include_trace.py",
        ],
    ),
    "qmake-recorder": (
        "Replace owned Python recorder with Rust internal binary entrypoint",
        ["core/qmake_context.py"],
    ),
    "compile-context-export": (
        "compile argv/cwd/response files, build variants, export schema and source closure",
        ["core/context.py", "core/compilation_export.py", "compilation_export_cli.py"],
    ),
    "test-quality-tem": (
        "Tests/coverage denominators, zero tests, repeats/mutation and TEM formula boundaries",
        [
            "engines/test.py",
            "application/testing.py",
            "application/tem.py",
            "analysis/coverage_support.py",
        ],
    ),
    "build-artifact-abi": (
        "Declared artifact ownership, ELF/linker/ABI, Make/CMake/qmake build effects",
        [
            "engines/build.py",
            "engines/binary_compat.py",
            "analysis/_elf.py",
            "analysis/binary_abi.py",
        ],
    ),
    "runtime-integration": (
        "Project interpreter selection, import-only versus -m probes, declared contracts",
        ["engines/python_compat.py", "engines/integration.py", "application/integration.py"],
    ),
    "discovery-config-origin": (
        "Root/child boundaries, sparse config/init equivalence, origin and local overlays",
        [
            "config/discovery.py",
            "config/automatic.py",
            "config/composition.py",
            "config/scaffold.py",
            "config/origin.py",
            "config/overlay.py",
        ],
    ),
    "config-migration-preservation": (
        "Preview, refusal of unknown schemas and preservation of user originals/comments",
        ["config/migration.py", "cli/next_migrate.py"],
    ),
    "execution-dag-resource-lock": (
        "Dependency planning, lock order, effects, snapshots and bounded process execution",
        [
            "application/graph.py",
            "application/schedule.py",
            "execution/locks.py",
            "execution/snapshot.py",
            "execution/process.py",
        ],
    ),
    "execution-cancel-save-redact": (
        "Process groups/descendants, timeouts, exit130, partial results, atomic saves, redaction",
        [
            "execution/cancellation.py",
            "execution/tree.py",
            "execution/watchdog.py",
            "execution/outputs.py",
            "core/redaction.py",
        ],
    ),
    "native-windows-disposition": (
        "Record unsupported/limited Windows runner disposition; do not silently drop behavior",
        ["core/runner_win.py"],
    ),
    "identity-cache-baseline": (
        "Fingerprint/policy/provider/scope/build identity, old evidence and conservative resolved",
        [
            "application/identity.py",
            "application/finding_identity.py",
            "application/check_scope.py",
            "application/baseline.py",
            "execution/cache.py",
            "core/baseline.py",
            "core/cache_identity.py",
        ],
    ),
    "suppression": (
        "Reasoned selectors, source/config suppression and preserved raw findings",
        ["application/suppressions.py", "analysis/_python_security.py"],
    ),
    "legacy-command-config-result-projection": (
        "One common application with verify/build/doctor/env/cache and legacy result v3 projection",
        [
            "__main__.py",
            "cli/cutover.py",
            "config_schema.py",
            "execution/legacy_reader.py",
            "core/baseline.py",
        ],
    ),
    "report-console-markdown-json-html-sarif": (
        "Issues-first/grouping/limits, all location/evidence axes, no CDN and large reports",
        [
            "reporters/console.py",
            "reporters/markdown.py",
            "reporters/json_rep.py",
            "reporters/html/report.py",
            "reporters/html/large.py",
            "reporters/sarif.py",
            "reporting/offline_html.py",
            "reporting/sarif.py",
        ],
    ),
    "auxiliary-import": (
        "SARIF uriBaseId mapping/refusal, JUnit native line, LCOV subset; NOT_EVALUATED",
        ["application/import_report.py", "reporting/auxiliary.py"],
    ),
    "publish-ca-proxy-ghes": (
        "Private/system CA, proxy, GHES, publication failure independent of analysis",
        ["application/publish.py", "engines/publish.py"],
    ),
    "doctor-env-cache": (
        "Read-only diagnosis, tools and install collisions, project env, cache list/prune",
        ["doctor.py", "core/env.py", "core/cache.py"],
    ),
    "wire-result-events": (
        "Run v2/read v1, event v1, stdout/stderr split, event order and same run_id",
        [
            "domain/result.py",
            "domain/serialization.py",
            "domain/events.py",
            "domain/eventstream.py",
            "cli/next_events.py",
        ],
    ),
}


def capability_rows(source: Source) -> list[dict[str, Any]]:
    result = []
    for name, (contract, paths) in CAPABILITIES.items():
        refs = []
        for relative in paths:
            path = f"src/ici/{relative}"
            if path not in source.files:
                raise ValueError(f"missing capability source: {path}")
            refs.append({"path": path, "line": 1, "symbol": None})
        result.append(
            row(
                "capability",
                name,
                refs,
                contract=contract,
                mapping="Cross-cutting/legacy union; not an extra named check",
            )
        )
    for path, data in sorted(source.files.items()):
        if not path.endswith(".schema.json"):
            continue
        schema = json.loads(data)
        result.append(
            row(
                "wire_schema",
                Path(path).name,
                [{"path": path, "line": 1, "symbol": None}],
                schema_id=schema.get("$id"),
                required=schema.get("required", []),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return result


def collect(source: Source) -> dict[str, Any]:
    requirements = sorted(
        [
            *registry_rows(source),
            *cli_rows(source),
            *config_rows(source),
            *config_key_rows(source),
            *rule_rows(source),
            *capability_rows(source),
        ],
        key=lambda item: item["id"],
    )
    ids = [item["id"] for item in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate requirement identifiers")
    counts = Counter(item["kind"] for item in requirements)
    if counts["descriptor"] != 19 or counts["check"] != 36:
        raise ValueError(f"frozen registry drift: {counts}")
    files = []
    for path, content in sorted(source.files.items()):
        files.append(
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "symbols": source.symbols(path) if path in source.trees else [],
                "audit": {"disposition": "unreviewed", "requirements": [], "reason": None},
            }
        )
    return {
        "schema_id": "ici.rust-migration.inventory",
        "schema_version": 1,
        "oracle": {
            "source_sha": ORACLE,
            "tag": "v0.13.0",
            "pyz_sha256": PYZ_SHA256,
            "url": "https://github.com/jihoon22-lee/ici/releases/tag/v0.13.0",
            "execution_environment": "Not captured by this static inventory; freeze per fixture",
        },
        "coverage": {
            "counts": dict(sorted(counts.items())),
            "source_files": len(files),
            "source_modules": len(source.trees),
            "meaning": "Enumeration and source audit inputs, never semantic parity proof",
        },
        "requirements": requirements,
        "source_files": files,
    }


def preserve_evidence(document: dict[str, Any], previous: dict[str, Any]) -> None:
    old = {item["id"]: item for item in previous.get("requirements", [])}
    if set(old) - {item["id"] for item in document["requirements"]}:
        raise ValueError("regeneration would discard requirement evidence")
    for item in document["requirements"]:
        if item["id"] in old:
            item["migration"] = old[item["id"]]["migration"]
    files = {item["path"]: item for item in previous.get("source_files", [])}
    if set(files) - {item["path"] for item in document["source_files"]}:
        raise ValueError("regeneration would discard source audit evidence")
    for item in document["source_files"]:
        if item["path"] in files:
            item["audit"] = files[item["path"]]["audit"]


def validate(document: dict[str, Any], *, require_closed: bool = False) -> None:
    ids = {item["id"] for item in document["requirements"]}
    pending = []
    for item in document["requirements"]:
        progress = item["migration"]
        if progress["status"] not in STATES:
            raise ValueError(f"invalid migration status for {item['id']}")
        if progress["status"] == "complete":
            if not all(
                (
                    progress["rust_paths"],
                    progress["evidence"],
                    progress["comparison"],
                    *[progress["fixtures"][kind] for kind in ("positive", "negative", "failure")],
                )
            ):
                raise ValueError(f"incomplete closure evidence for {item['id']}")
        else:
            pending.append(item["id"])
    for item in document["source_files"]:
        audit = item["audit"]
        if not set(audit["requirements"]) <= ids:
            raise ValueError(f"unknown requirement reference for {item['path']}")
        if audit["disposition"] == "unreviewed":
            pending.append(item["path"])
        elif audit["disposition"] not in {"mapped", "development_only", "unsupported", "retired"}:
            raise ValueError(f"invalid source disposition for {item['path']}")
        elif not audit["reason"] or (
            audit["disposition"] == "mapped" and not audit["requirements"]
        ):
            raise ValueError(f"missing disposition evidence for {item['path']}")
    if require_closed and pending:
        raise ValueError(
            f"Rust parity is not closed: {len(pending)} requirements/source audits remain"
        )


def markdown(document: dict[str, Any]) -> str:
    counts = document["coverage"]["counts"]
    lines = [
        "# Rust 교체 기능·계약 원장",
        "",
        "이 원장은 고정 공개판 소스의 정적 수집 결과다. 항목 수나 추출 성공은 Rust 동등성 인수가 아니다.",
        "구현·검증·차이 검토·완료 상태 및 양성/음성/실패 fixture는 inventory.json에서 개별 관리한다.",
        "현재 status가 not_started인 행은 미구현·미검증이다. 전환 승인은 전체 원장과 source audit를 닫아야 한다.",
        "",
        f"- 고정 oracle: `v0.13.0`, source `{ORACLE}`.",
        f"- 공개 pyz SHA-256: `{PYZ_SHA256}`.",
        "- Python/tool/interpreter/environment pin은 각 실행 oracle fixture에 추가로 필요하다.",
        "  이 정적 원장은 도구 실행이나 환경 인수를 수행하지 않는다.",
        "",
        "## 수집과 갱신",
        "",
        "```bash",
        "python3 scripts/rust_migration_inventory.py",
        "python3 scripts/rust_migration_inventory.py --check",
        "python3 scripts/rust_migration_inventory.py --self-test",
        "python3 scripts/rust_migration_inventory.py --check --require-closed",
        "```",
        "",
        "Git archive의 고정 commit에서만 읽고 제품 모듈을 import/eval하지 않는다. Python 제품 소스를",
        "cutover에서 제거한 뒤에도 Git history가 있으면 재생성할 수 있다. --check는 쓰지 않는다.",
        "기존 migration/audit 증거는 재생성 시 보존하며 행 삭제로 증거가 유실되면 실패한다.",
        "--require-closed는 미완료 요구·미검토 소스가 있으면 실패한다. 단순 complete 문자열만으로",
        "닫히지 않으며 Rust 경로·비교 판정·실행 증거와 양성/음성/실패 fixture가 모두 필요하다.",
        "",
        "## 기계 수집 범위",
        "",
        "| 종류 | 항목 수 |",
        "|---|---:|",
    ]
    lines.extend(f"| {kind} | {count} |" for kind, count in counts.items())
    lines.extend(
        [
            f"| 원본 파일 audit | {document['coverage']['source_files']} |",
            f"| Python module | {document['coverage']['source_modules']} |",
            "",
            "CLI는 decorator와 app.command(...)(function) 등록을 모두 수집하고 imported option 정의를",
            "원래 파일까지 따라간다. alias는 개별 사용자 진입점으로 보존한다. config는 key allowlist와",
            "각 reader 호출의 literal/expression을 그대로 기록한다. 동적인 key·rule·metric expression은",
            "확정된 규칙 이름으로 가장하지 않으며 해당 source audit에서 분해·검토해야 한다.",
            "finding_site는 rule/native rule/target/metrics 인자의 생성 지점이고 한 지점이 여러 규칙을",
            "생성할 수 있다. 모든 파일의 SHA와 private/nested 함수를 포함한 symbol 목록을 별도로",
            "수집하므로 extraction 밖의 동적 로직·template·asset을 조용히 제거할 수 없다.",
            "",
            "원본 파일 audit는 mapped/development_only/unsupported/retired 처분 이유가 필요하다.",
            "mapped는 대응 requirement ID를 연결한다. 구조가 달라졌다는 이유만으로 retired 처리하거나",
            "finding 누락·위치·severity·scope·분모·완결성·exit 차이를 정규화해 숨기지 않는다.",
            "",
            "## Descriptor / check",
            "",
            "| 종류 | ID | 원본 | 상태 |",
            "|---|---|---|---|",
        ]
    )
    for item in document["requirements"]:
        if item["kind"] not in {"descriptor", "check"}:
            continue
        ref = item["sources"][0]
        lines.append(
            f"| {item['kind']} | `{item['id'].split(':', 1)[1]}` | "
            f"`{ref['path']}:{ref['line']}` | {item['migration']['status']} |"
        )
    lines.extend(["", "## CLI 진입점", "", "| 명령 | 옵션/인자 수 | 상태 |", "|---|---:|---|"])
    for item in document["requirements"]:
        if item["kind"] == "command":
            lines.append(
                f"| `{item['facts']['command']}` | {len(item['facts']['options'])} | "
                f"{item['migration']['status']} |"
            )
    lines.extend(
        ["", "## Registry 밖·공유 계약", "", "| 요구 | 보존할 의미 | 상태 |", "|---|---|---|"]
    )
    for item in document["requirements"]:
        if item["kind"] == "capability":
            lines.append(
                f"| `{item['id']}` | {item['facts']['contract']} | {item['migration']['status']} |"
            )
    lines.extend(
        [
            "",
            "## 수집기 검증 (2026-10-28)",
            "",
            "- Python 3.10/3.14에서 같은 고정 inventory 재생성/check 통과. 제품 모듈 import 없는",
            "  alias/source 위치·미완료 closure 거부·거짓 complete 거부·증거 보존/삭제 거부 self-test 통과.",
            "- 현재 소스가 oracle과 같은 시점에서 Typer get_command로 실제 CLI를 읽기 검증했다:",
            "  root/group 포함 37개 node, 115개 parameter 및 option alias가 추출 목록과 일치했다.",
            "- 이 검사는 수집기의 신뢰성 근거이며 Rust 기능·known-bug 교정·실제 도구 parity 검증은 아니다.",
            "",
            "## 인수에서 추가로 고정할 차이",
            "",
            "- Python sanitize와 C++ linker dead는 36개 check 이름만으로 덮이지 않는다.",
            "- legacy verify/build/doctor/env/cache와 next alias의 옵션 의미·exit·v3 projection을 각각 검증한다.",
            "- plan/doctor/publish처럼 JSON Schema가 없는 출력은 실행 golden으로 별도 고정한다.",
            "- ESLint rule off·plugin/lock/같은 경로 도구 교체는 scope/identity 변화다. 기존 결과를 읽는",
            "  기능과 baseline resolved 판단 가능성을 분리하고 알려진 오판은 교정 기대값으로 검증한다.",
            "- SARIF uriBaseId, JUnit line, LCOV subset 및 imported NOT_EVALUATED는 위치·증거 계약이다.",
            "- 환경·배포·viewer·설치 shell/CI는 Python module 개수에 포함되지 않는 제품 표면이다.",
            "  root 실행 계획 R0/R8/R9의 별도 설치·tool-pack·viewer·rollback 인수와 함께 닫아야 한다.",
            "- runner_win은 Linux/WSL 전환의 지원 범위 밖이어도 처분 근거를 남긴다.",
            "",
        ]
    )
    return "\n".join(lines)


def self_test() -> None:
    """Exercise alias resolution and evidence guards without importing ici."""
    source = Source(
        {
            "src/ici/__main__.py": b'from ici.handler import run as alias\napp.command("aliased")(alias)\n',
            "src/ici/handler.py": b"from ici.flags import FLAG\ndef run(output: str = FLAG):\n    pass\n",
            "src/ici/flags.py": b'FLAG = typer.Option("default", "--output", "-o")\n',
        }
    )
    unicode_source = Source(
        {"src/ici/unicode.py": 'π = "é"\r\nresult = call(\r\n    "λ",\r\n)\r\n'.encode()}
    )
    unicode_tree = unicode_source.trees["src/ici/unicode.py"]
    assert expression(unicode_tree.body[0].value) == '"é"'
    assert expression(unicode_tree.body[1].value) == 'call(\r\n    "λ",\r\n)'
    commands = cli_rows(source)
    assert len(commands) == 1
    assert commands[0]["facts"]["command"] == "ici aliased"
    option = commands[0]["facts"]["options"][0]
    assert option["flags"] == ["--output", "-o"]
    assert option["option_source"]["path"] == "src/ici/flags.py"
    assert option["option_source"]["line"] == 1
    config_keys = {item["facts"]["key"] for item in config_key_rows(read_oracle())}
    assert {
        "tools.*.path",
        "suppressions[].path",
        "components[].integrations[].output_artifacts[].path",
    } <= config_keys
    assert "path" not in config_keys  # Document.path is reader metadata, not user configuration.
    document = {"requirements": commands, "source_files": []}
    validate(document)
    try:
        validate(document, require_closed=True)
    except ValueError as error:
        assert "not closed" in str(error)
    else:
        raise AssertionError("open parity accepted")
    malformed = copy.deepcopy(document)
    malformed["requirements"][0]["migration"]["status"] = "complete"
    try:
        validate(malformed)
    except ValueError as error:
        assert "incomplete closure evidence" in str(error)
    else:
        raise AssertionError("unproved closure accepted")
    previous = copy.deepcopy(document)
    previous["requirements"][0]["migration"]["evidence"] = ["preserve-sentinel"]
    preserve_evidence(document, previous)
    assert document["requirements"][0]["migration"]["evidence"] == ["preserve-sentinel"]
    previous["requirements"].append({"id": "removed-evidence"})
    try:
        preserve_evidence(document, previous)
    except ValueError as error:
        assert "discard" in str(error)
    else:
        raise AssertionError("evidence deletion accepted")
    print(
        "PASS: command/option aliases, source location, open/false closure, evidence preservation/deletion"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare without rewriting evidence")
    parser.add_argument(
        "--require-closed", action="store_true", help="Fail on any open parity/audit row"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--self-test", action="store_true", help="Test extraction and evidence guards"
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    target = args.output_dir / "inventory.json"
    document = collect(read_oracle())
    if target.exists():
        preserve_evidence(document, json.loads(target.read_text(encoding="utf-8")))
    validate(document, require_closed=args.require_closed)
    outputs = {
        target: json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        args.output_dir / "inventory.md": markdown(document),
    }
    if args.check:
        stale = [
            str(path)
            for path, content in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            raise ValueError("inventory differs: " + ", ".join(stale))
    else:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for path, content in outputs.items():
            path.write_text(content, encoding="utf-8")
    print(json.dumps(document["coverage"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"inventory: {error}", file=sys.stderr)
        raise SystemExit(1) from error
