"""Static import closure of the frozen ici package from ``ici.__main__``.

Development-only audit helper: parses (never imports) the frozen source and lists which of the
given source files are unreachable from the CLI entry point, including function-local imports.
Usage: import_closure.py <oracle-src> <src/ici/...py> [...]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def module_path(root: Path, name: str) -> Path | None:
    path = root.joinpath(*name.split("."))
    if (path / "__init__.py").exists():
        return path / "__init__.py"
    if path.with_suffix(".py").exists():
        return path.with_suffix(".py")
    return None


def imports(root: Path, path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    base = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = base[: len(base) - node.level + 1]
                module = ".".join(parent + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    expanded = set()
    for module in found:
        parts = module.split(".")
        expanded.update(".".join(parts[:index]) for index in range(1, len(parts) + 1))
    return {module for module in expanded if module.startswith("ici") and module_path(root, module)}


def reachable(root: Path) -> set[str]:
    seen: set[str] = set()
    pending = ["ici.__main__"]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = module_path(root, module)
        assert path is not None
        pending.extend(imports(root, path, module) - seen)
    return seen


def main() -> None:
    root = Path(sys.argv[1])
    seen = reachable(root)
    for path in sys.argv[2:]:
        module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        module = module.removesuffix(".__init__")
        print(("reachable " if module in seen else "UNREACHABLE ") + path)


if __name__ == "__main__":
    main()
