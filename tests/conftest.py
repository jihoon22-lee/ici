"""Pytest fixtures for ici test suite."""

import sys
from pathlib import Path

import pytest

# Ensure src/ is in sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def tmp_python_project(tmp_path: Path) -> Path:
    """Creates a temporary sample Python project with tests."""
    src = tmp_path / "src" / "sample_pkg"
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        """def add(a: int, b: int) -> int:
    # Add function
    if a > 0 and b > 0:
        return a + b
    return a + b

def unused_private():
    return "unused"
""",
        encoding="utf-8",
    )

    tests = tmp_path / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_core.py").write_text(
        """from sample_pkg.core import add

def test_add():
    assert add(1, 2) == 3
""",
        encoding="utf-8",
    )

    (tmp_path / "ici.toml").write_text(
        'name = "sample_pkg"\ntype = "python"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def tmp_cpp_project(tmp_path: Path) -> Path:
    """Creates a temporary sample C++ project."""
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.cpp").write_text(
        """#include <iostream>

int main() {
    std::cout << "Hello C++" << std::endl;
    return 0;
}
""",
        encoding="utf-8",
    )

    inc = tmp_path / "include"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "calc.h").write_text("int add(int a, int b);\n", encoding="utf-8")

    (tmp_path / "ici.toml").write_text(
        'name = "sample_cpp"\ntype = "cpp"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    return tmp_path
