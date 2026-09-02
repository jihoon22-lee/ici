"""Project type detection, metadata parsing, and source file discovery for ici."""

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import tomli

from ici.core.env import get_nas_cpp_lib_dir
from ici.core.path_utils import _resolve_project_root, resolve_project_path
from ici.core.runner import run_process

DEFAULT_SOURCE_DIRS = ["src", "lib", "app", "packages", "python"]
_PROJECT_VALUE_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


def _safe_project_file(base: Path, name: str) -> Path | None:
    """Return an existing in-project file without following an escaped link."""
    try:
        candidate = resolve_project_path(base, name)
        return candidate if candidate.is_file() else None
    except (OSError, ValueError):
        return None


def _safe_project_dir(base: Path, path: Path) -> Path | None:
    """Return an existing in-project directory without following an escaped link."""
    try:
        candidate = resolve_project_path(base, os.fspath(path))
        return candidate if candidate.is_dir() else None
    except (OSError, ValueError):
        return None


def _iter_project_dirs(root: Path, base: Path) -> Iterator[Path]:
    """Yield canonical directories below ``base`` without traversing symlinks."""
    safe_root = _safe_project_dir(base, root)
    if safe_root is None:
        return

    yield safe_root

    for current, dir_names, _file_names in os.walk(safe_root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_names: list[str] = []
        safe_dirs: list[Path] = []
        for dir_name in dir_names:
            path = current_path / dir_name
            if path.is_symlink():
                continue
            safe_dir = _safe_project_dir(base, path)
            if safe_dir is None or _should_ignore_path(safe_dir):
                continue
            safe_names.append(dir_name)
            safe_dirs.append(safe_dir)
        dir_names[:] = safe_names
        yield from safe_dirs


def _iter_project_files(root: Path, base: Path, suffixes: tuple[str, ...]) -> Iterator[Path]:
    """Yield contained regular files while ignoring symlink traversal."""
    safe_root = _safe_project_dir(base, root)
    if safe_root is None:
        return

    for current, dir_names, file_names in os.walk(safe_root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_names: list[str] = []
        for dir_name in dir_names:
            path = current_path / dir_name
            if path.is_symlink():
                continue
            safe_dir = _safe_project_dir(base, path)
            if safe_dir is None or _should_ignore_path(safe_dir):
                continue
            safe_names.append(dir_name)
        dir_names[:] = safe_names

        for file_name in file_names:
            path = current_path / file_name
            if path.is_symlink() or path.suffix not in suffixes:
                continue
            try:
                canonical = resolve_project_path(base, os.fspath(path))
                if canonical.is_file() and not _should_ignore_path(canonical):
                    yield canonical
            except (OSError, ValueError):
                continue


def get_source_dirs(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """Resolves existing project source directories (overridable via config project.source_dirs)."""
    base = _resolve_project_root(base_path or Path.cwd())
    names: list[str] | None = None
    configured = False
    if config:
        proj_cfg = config.get("project")
        if isinstance(proj_cfg, dict):
            raw = proj_cfg.get("source_dirs")
            if isinstance(raw, list):
                configured = True
                names = []
                for item in raw:
                    if not isinstance(item, str) or not item:
                        raise ValueError("project.source_dirs must be a list of non-empty strings")
                    names.append(item)
    if names is None:
        names = DEFAULT_SOURCE_DIRS

    dirs: list[Path] = []
    for name in names:
        try:
            candidate = resolve_project_path(base, name)
        except ValueError:
            if configured:
                raise
            continue
        try:
            if candidate.is_dir():
                dirs.append(candidate)
        except OSError as err:
            if configured:
                raise ValueError(f"could not inspect source directory {name!r}: {err}") from err
    return dirs


def detect_project_type(target_dir: Path | None = None) -> str:
    """Detects whether target project is 'cpp', 'python', or 'hybrid'."""
    base = _resolve_project_root(target_dir or Path.cwd())

    # 1. Check configuration files (ici.toml, dev.toml)
    for conf_name in ("ici.toml", "dev.toml"):
        conf_path = _safe_project_file(base, conf_name)
        if conf_path is not None:
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

    # 2. Check source file signatures across all configured source directories
    source_dirs = get_source_dirs(base)
    has_cpp = (
        _safe_project_file(base, "CMakeLists.txt") is not None
        or _safe_project_file(base, "Makefile") is not None
        or any(any(_iter_project_files(d, base, (".cpp",))) for d in source_dirs)
    )
    has_py = (
        _safe_project_file(base, "pyproject.toml") is not None
        or _safe_project_file(base, "setup.py") is not None
        or any(any(_iter_project_files(d, base, (".py",))) for d in source_dirs)
    )

    if has_cpp and has_py:
        return "hybrid"
    if has_cpp:
        return "cpp"
    return "python"


def _validate_project_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or _PROJECT_VALUE_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"invalid project name: {value!r}")
    return value


def _validate_project_version(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid project version: {value!r}")
    normalized = value[1:] if value.startswith("v") else value
    if _PROJECT_VALUE_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"invalid project version: {value!r}")
    return f"v{normalized}"


def _metadata_path(base: Path, name: str) -> Path | None:
    """Resolve one project metadata file, rejecting an escaped symlink."""
    candidate = resolve_project_path(base, name)
    try:
        if not candidate.exists():
            return None
        if not candidate.is_file():
            raise ValueError(f"project metadata path is not a file: {name}")
    except OSError as err:
        raise ValueError(f"could not inspect project metadata {name}: {err}") from err
    return candidate


def _read_toml(path: Path) -> dict[str, Any]:
    """Read one canonical TOML metadata file."""
    try:
        with path.open("rb") as stream:
            try:
                document = tomli.load(stream)
            except (ValueError, RecursionError, UnicodeError) as err:
                raise ValueError(f"could not parse project metadata {path.name}: {err}") from err
    except OSError as err:
        raise ValueError(f"could not read project metadata {path.name}: {err}") from err
    if not isinstance(document, dict):
        raise ValueError(f"project metadata must be a table: {path.name}")
    return document


def _metadata_table(document: dict[str, Any], path_name: str) -> dict[str, Any]:
    if path_name == "pyproject.toml":
        project = document.get("project", {})
        if not isinstance(project, dict):
            raise ValueError("project metadata [project] must be a table")
        return project
    return document


def _git_project_version(base: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            cwd=base,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "v1.0.0"
    if result.returncode == 0 and result.stdout.strip():
        return _validate_project_version(result.stdout.strip())
    return "v1.0.0"


def read_project_metadata(base: Path, *, allow_git: bool = True) -> tuple[str, str]:
    """Read project metadata, optionally disabling the git version fallback."""
    project_root = _resolve_project_root(base)
    name: str | None = None
    version: str | None = None

    for file_name in ("ici.toml", "dev.toml", "pyproject.toml"):
        path = _metadata_path(project_root, file_name)
        if path is None:
            continue
        table = _metadata_table(_read_toml(path), file_name)
        if "name" in table:
            candidate_name = _validate_project_name(table["name"])
            if name is None:
                name = candidate_name
        if "version" in table:
            candidate_version = _validate_project_version(table["version"])
            if version is None:
                version = candidate_version

    if name is None:
        name = _validate_project_name(project_root.name)
    if version is None and allow_git:
        version = _git_project_version(project_root)
    elif version is None:
        version = "v1.0.0"
    return name, version


def get_project_name(target_dir: Path | None = None) -> str:
    """Gets and validates the project name from TOML metadata or its directory."""
    return read_project_metadata(target_dir or Path.cwd())[0]


def get_project_version(target_dir: Path | None = None) -> str:
    """Extract and validate the project version or fall back to git/v1.0.0."""
    return read_project_metadata(target_dir or Path.cwd())[1]


def get_all_cpp_sources(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """Finds all C++ source files (.cpp, .cc, .cxx, .c) across project source directories."""
    base = _resolve_project_root(base_path or Path.cwd())
    cpp_files: list[Path] = []
    for src_dir in get_source_dirs(base, config):
        cpp_files.extend(_iter_project_files(src_dir, base, (".cpp", ".cc", ".cxx", ".c")))

    return sorted(cpp_files)


def get_all_cpp_headers(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """Find owned C/C++ headers and opt-in generated ``.moc`` inputs."""

    base = _resolve_project_root(base_path or Path.cwd())
    roots = list(get_source_dirs(base, config))
    include_root = _safe_project_dir(base, base / "include")
    if include_root is not None and include_root not in roots:
        roots.append(include_root)
    headers = {
        path
        for source_dir in roots
        for path in _iter_project_files(
            source_dir,
            base,
            (".h", ".hh", ".hpp", ".hxx", ".moc"),
        )
    }
    return sorted(headers)


def _project_string_list(config: dict[str, Any] | None, key: str) -> list[str]:
    """Read a `[project]` list-of-strings setting, tolerating an absent table."""
    if not config:
        return []
    project = config.get("project")
    if not isinstance(project, dict):
        return []
    raw = project.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def get_cpp_pkg_config_flags(config: dict[str, Any] | None = None) -> list[str]:
    """Resolve `project.cpp_pkg_config` packages into compiler flags.

    A GUI or any library-backed source needs its toolkit's include paths before
    it will even parse, and hardcoding those paths in a config file breaks the
    moment the project moves to another machine. Asking pkg-config keeps the
    setting portable: the project names the package, the host supplies the path.

    Returns an empty list when nothing is configured or pkg-config is missing;
    the caller's compile then fails with the real "no such file" diagnostic,
    which says more than anything this function could invent.
    """
    packages = _project_string_list(config, "cpp_pkg_config")
    if not packages:
        return []
    pkg_config = shutil.which("pkg-config")
    if pkg_config is None:
        return []
    flags: list[str] = []
    for package in packages:
        try:
            result = run_process([pkg_config, "--cflags", package])
        except (OSError, ValueError):
            continue
        if result.returncode == 0:
            flags.extend(result.stdout.split())
    return flags


def get_cpp_external_build_dirs(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """Directories ici analyses but does not compile itself.

    Some C++ in a project genuinely cannot be built by a bare `g++` invocation:
    Qt widgets need moc-generated sources, and anything driven by CMake may need
    generated headers. Those files are still project source and every text- and
    AST-based engine should read them; only the engines that produce a binary
    have to step around them.
    """
    base = _resolve_project_root(base_path or Path.cwd())
    dirs: list[Path] = []
    for name in _project_string_list(config, "cpp_external_build_dirs"):
        try:
            candidate = resolve_project_path(base, name)
        except ValueError:
            continue
        if candidate.is_dir():
            dirs.append(candidate)
    return dirs


def get_compilable_cpp_sources(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """C++ sources ici can compile and link on its own.

    This is `get_all_cpp_sources` minus anything under
    `project.cpp_external_build_dirs`. Engines that only read source should keep
    using the full list — excluding a file from a link is not a reason to stop
    checking its complexity, duplication or exception safety.
    """
    base = _resolve_project_root(base_path or Path.cwd())
    external = get_cpp_external_build_dirs(base, config)
    if not external:
        return get_all_cpp_sources(base, config)
    return [
        source
        for source in get_all_cpp_sources(base, config)
        if not any(_is_within(source, directory) for directory in external)
    ]


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def get_all_cpp_includes(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[str]:
    """Finds C++ compile flags: project include directories plus configured packages."""
    base = _resolve_project_root(base_path or Path.cwd())
    inc_dirs = set()

    inc_dir = _safe_project_dir(base, base / "include")
    if inc_dir is not None:
        inc_dirs.add(f"-I{inc_dir}")
        for p in _iter_project_dirs(inc_dir, base):
            if not _should_ignore_path(p):
                inc_dirs.add(f"-I{p}")

    for src_dir in get_source_dirs(base, config):
        sub_inc = _safe_project_dir(base, src_dir / "include")
        if sub_inc is not None:
            inc_dirs.add(f"-I{sub_inc}")

    nas_cpp = get_nas_cpp_lib_dir()
    if nas_cpp.exists() and (nas_cpp / "include").exists():
        inc_dirs.add(f"-I{nas_cpp / 'include'}")

    # Package flags keep their order: pkg-config emits -I and -D together and
    # sorting them apart would be meaningless.
    return sorted(inc_dirs) + get_cpp_pkg_config_flags(config)


def get_all_python_sources(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """Finds all Python source files across project source directories."""
    base = _resolve_project_root(base_path or Path.cwd())
    py_files: list[Path] = []
    for src_dir in get_source_dirs(base, config):
        py_files.extend(_iter_project_files(src_dir, base, (".py",)))
    return sorted(py_files)


def _should_ignore_path(p: Path) -> bool:
    parts = p.parts
    return any(
        part in (".venv", "venv", "build", "__pycache__", ".git", ".pytest_cache", ".ruff_cache")
        or part.startswith("v1.")
        or part.startswith("v0.")
        for part in parts
    )
