"""The ``EngineResult.extra`` key contract, enforced on the syntax tree.

``extra`` is the engine→reporter evidence channel and both ends are string
keys: an engine writing ``clone_groups`` and a reporter reading
``clon_groups`` fails silently — an empty section where evidence should be.
This test walks ``src/ici`` and rejects any literal key used on ``.extra``
that ``ici.core.result_keys.EXTRA_KEYS`` does not register.

Three access shapes are covered:

- reads: ``result.extra.get("k")``, ``result.extra["k"]``, and the same on a
  bare ``extra`` name (deserialized results in ``core/baseline.py``)
- literal writes: ``extra={"k": ...}``
- helper writes: ``extra={**self._evidence(...)}`` / ``extra=self._evidence()``
  — the helper's ``return {...}`` literal keys, one level deep, same module
"""

from __future__ import annotations

import ast
from pathlib import Path

from ici.core.result_keys import EXTRA_KEYS

SRC = Path(__file__).resolve().parents[1] / "src" / "ici"

#: Files whose ``extra`` name is not the result channel (exempted explicitly,
#: not skipped silently — adding to this list needs a comment saying why).
EXEMPT_FILES = frozenset(
    {
        "core/result_keys.py",  # the registry itself
    }
)


def _is_extra(node: ast.AST) -> bool:
    """Whether a value expression names a result's ``extra`` mapping."""

    if isinstance(node, ast.Attribute):
        return node.attr == "extra"
    return isinstance(node, ast.Name) and node.id == "extra"


def _returned_keys(fn: ast.FunctionDef) -> set[str]:
    """Literal keys of every dict a function returns — one level, no calls."""

    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys.update(k.value for k in node.value.keys if isinstance(k, ast.Constant))
    return keys


def _module_helper_keys(tree: ast.Module) -> dict[str, set[str]]:
    return {
        node.name: _returned_keys(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _call_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return getattr(fn, "id", None)


def _keys_used(path: Path) -> list[tuple[str, int]]:
    """Every literal key used on ``.extra``, as ``(key, line)`` pairs."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    helpers = _module_helper_keys(tree)
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        # extra.get("k") / extra["k"] / extra.setdefault("k", ...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "setdefault", "pop")
            and _is_extra(node.func.value)
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            found.append((node.args[0].value, node.lineno))
        if (
            isinstance(node, ast.Subscript)
            and _is_extra(node.value)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            found.append((node.slice.value, node.lineno))
        # extra={"k": ...} / extra={**helper()} / extra=helper()
        if isinstance(node, ast.keyword) and node.arg == "extra":
            value = node.value
            if isinstance(value, ast.Dict):
                for i, key in enumerate(value.keys):
                    if isinstance(key, ast.Constant):
                        found.append((key.value, node.lineno))
                    elif key is None and isinstance(value.values[i], ast.Call):
                        name = _call_name(value.values[i])
                        found.extend((k, node.lineno) for k in helpers.get(name or "", ()))
            elif isinstance(value, ast.Call):
                found.extend((k, node.lineno) for k in helpers.get(_call_name(value) or "", ()))
    return found


def test_every_literal_extra_key_is_registered() -> None:
    offenders: list[str] = []
    for module in sorted(SRC.rglob("*.py")):
        relative = module.relative_to(SRC)
        if str(relative) in EXEMPT_FILES:
            continue
        for key, line in _keys_used(module):
            if key not in EXTRA_KEYS:
                offenders.append(f"{relative}:{line}: {key!r}")
    assert not offenders, (
        "unregistered EngineResult.extra keys — add them to "
        "ici.core.result_keys.EXTRA_KEYS or fix the spelling:\n" + "\n".join(offenders)
    )


def test_registered_keys_are_produced_or_consumed() -> None:
    """A dead registry entry hides drift — every key must appear in code."""

    used: set[str] = set()
    for module in sorted(SRC.rglob("*.py")):
        relative = module.relative_to(SRC)
        if str(relative) in EXEMPT_FILES:
            continue
        used.update(key for key, _ in _keys_used(module))
    stale = EXTRA_KEYS - used
    assert not stale, f"registry entries no code uses — remove or justify: {sorted(stale)}"
