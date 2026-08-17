"""Project type detection, metadata parsing, and source file discovery for ici."""

import re
import subprocess
from pathlib import Path

from ici.core.env import get_nas_cpp_lib_dir


def detect_project_type(target_dir: Path | None = None) -> str:
    """Detects whether target project is 'cpp', 'python', or 'hybrid'."""
    base = (target_dir or Path.cwd()).resolve()

    # 1. Check configuration files (ici.toml, dev.toml)
    for conf_name in ("ici.toml", "dev.toml"):
        conf_path = base / conf_name
        if conf_path.exists():
            try:
                content = conf_path.read_text(encoding="utf-8")
                if re.search(r'type\s*=\s*["\']hybrid["\']', content):
                    return "hybrid"
                elif re.search(r'type\s*=\s*["\']cpp["\']', content):
                    return "cpp"
                elif re.search(r'type\s*=\s*["\']python["\']', content):
                    return "python"
            except OSError as err:
                _ = err

    # 2. Check source file signatures
    has_cpp = (
        (base / "CMakeLists.txt").exists()
        or (base / "Makefile").exists()
        or any((base / "src").rglob("*.cpp"))
        if (base / "src").exists()
        else False
    )
    has_py = (
        (base / "pyproject.toml").exists()
        or (base / "setup.py").exists()
        or any((base / "src").rglob("*.py"))
        if (base / "src").exists()
        else False
    )

    if has_cpp and has_py:
        return "hybrid"
    if has_cpp:
        return "cpp"
    return "python"


def get_project_name(target_dir: Path | None = None) -> str:
    """Gets the project name from configs or directory name."""
    base = (target_dir or Path.cwd()).resolve()
    for conf_name in ("ici.toml", "dev.toml"):
        conf_path = base / conf_name
        if conf_path.exists():
            try:
                for line in conf_path.read_text(encoding="utf-8").splitlines():
                    if "name" in line and "=" in line:
                        val = line.split("=")[1].strip().strip('"').strip("'")
                        if val:
                            return val
            except OSError as err:
                _ = err

    pyproj = base / "pyproject.toml"
    if pyproj.exists():
        try:
            for line in pyproj.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("name") and "=" in line:
                    val = line.split("=")[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError as err:
            _ = err

    return base.name


def get_project_version(target_dir: Path | None = None) -> str:
    """Extracts project version or falls back to git describe / v1.0.0."""
    base = (target_dir or Path.cwd()).resolve()

    # 1. Config files
    for conf_name in ("ici.toml", "dev.toml"):
        conf_path = base / conf_name
        if conf_path.exists():
            try:
                for line in conf_path.read_text(encoding="utf-8").splitlines():
                    if "version" in line and "=" in line:
                        v = line.split("=")[1].strip().strip('"').strip("'")
                        if v:
                            return v if v.startswith("v") else f"v{v}"
            except OSError as err:
                _ = err

    # 2. Git tag
    try:
        res = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            cwd=base,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip():
            tag = res.stdout.strip()
            return tag if tag.startswith("v") else f"v{tag}"
    except (OSError, subprocess.SubprocessError) as err:
        _ = err

    return "v1.0.0"


def get_all_cpp_sources(base_path: Path | None = None) -> list[Path]:
    """Finds all C++ source files (.cpp, .cc, .cxx, .c) in src/ and root."""
    base = (base_path or Path.cwd()).resolve()
    cpp_files = []
    src_dir = base / "src"
    if src_dir.exists():
        for p in src_dir.rglob("*.cpp"):
            if not _should_ignore_path(p):
                cpp_files.append(p)
        for p in src_dir.rglob("*.cc"):
            if not _should_ignore_path(p):
                cpp_files.append(p)

    return sorted(cpp_files)


def get_all_cpp_includes(base_path: Path | None = None) -> list[str]:
    """Finds all C++ include directories (-I flags)."""
    base = (base_path or Path.cwd()).resolve()
    inc_dirs = set()

    inc_dir = base / "include"
    if inc_dir.exists():
        inc_dirs.add(f"-I{inc_dir}")
        for p in inc_dir.rglob("*"):
            if p.is_dir() and not _should_ignore_path(p):
                inc_dirs.add(f"-I{p}")

    nas_cpp = get_nas_cpp_lib_dir()
    if nas_cpp.exists() and (nas_cpp / "include").exists():
        inc_dirs.add(f"-I{nas_cpp / 'include'}")

    return sorted(list(inc_dirs))


def get_all_python_sources(base_path: Path | None = None) -> list[Path]:
    """Finds all Python source files in src/."""
    base = (base_path or Path.cwd()).resolve()
    py_files = []
    src_dir = base / "src"
    if src_dir.exists():
        for p in src_dir.rglob("*.py"):
            if not _should_ignore_path(p):
                py_files.append(p)
    return sorted(py_files)


def _should_ignore_path(p: Path) -> bool:
    parts = p.parts
    return any(
        part in (".venv", "venv", "build", "__pycache__", ".git", ".pytest_cache", ".ruff_cache")
        or part.startswith("v1.")
        or part.startswith("v0.")
        for part in parts
    )
