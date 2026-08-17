"""AST test to ensure pure Python compliance and no banned imports."""

import ast
from pathlib import Path

BANNED_MODULES = {"tomllib", "requests", "httpx", "certifi"}


def test_no_banned_imports():
    src_dir = Path(__file__).resolve().parent.parent / "src"

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    assert root_mod not in BANNED_MODULES, (
                        f"Banned import '{root_mod}' in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                assert root_mod not in BANNED_MODULES, f"Banned import '{root_mod}' in {py_file}"
