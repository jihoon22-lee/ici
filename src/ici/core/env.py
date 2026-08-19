"""Environment, path resolution, and system runtime utilities for ici."""

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Invariant: Must strictly match the candidate list in scripts/launcher.sh
PYTHON_CANDIDATES = [
    "python3.14",
    "python3.13",
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
]


def find_infra_root(start_dir: Path | None = None) -> Path:
    """Finds the root of the infrastructure repository or workspace."""
    if "ICI_INFRA_ROOT" in os.environ:
        return Path(os.environ["ICI_INFRA_ROOT"]).resolve()
    if "DEVOPS_INFRA_ROOT" in os.environ:
        return Path(os.environ["DEVOPS_INFRA_ROOT"]).resolve()

    curr = (start_dir or Path.cwd()).resolve()
    for parent in [curr, *list(curr.parents)]:
        if (parent / "nas_shared").exists():
            return parent
        if (parent / ".git").exists() and (parent / "ici").exists():
            return parent

    return curr


def get_nas_shared_dir() -> Path:
    """Returns the nas_shared directory path."""
    if "NAS_SHARED_DIR" in os.environ:
        return Path(os.environ["NAS_SHARED_DIR"]).resolve()
    infra_root = find_infra_root()
    return infra_root / "nas_shared"


def find_uv() -> str | None:
    """Locates the uv executable: $ICI_UV, shared NAS/infra paths, then PATH."""
    ici_uv = os.environ.get("ICI_UV")
    if ici_uv and os.path.isfile(ici_uv) and os.access(ici_uv, os.X_OK):
        return ici_uv

    candidates = [
        Path.home() / ".local" / "bin" / "uv",
        get_nas_shared_dir() / "bin" / "uv",
        find_infra_root() / "bin" / "uv",
    ]
    for cand in candidates:
        if cand.is_file() and os.access(str(cand), os.X_OK):
            return str(cand)

    return shutil.which("uv")


def find_project_executable(project_root: Path, name: str) -> str | None:
    """Find a directly executable tool in the project's virtual environment.

    Package runners such as ``uv run`` and ``uvx`` are intentionally excluded:
    resolving a missing tool through them can access the network or mutate the
    environment.  The explicit candidates cover the Unix and Windows venv
    layouts while requiring executable permissions on Unix.
    """

    for directory in ("bin", "Scripts"):
        for suffix in ("", ".exe"):
            candidate = project_root / ".venv" / directory / f"{name}{suffix}"
            if not candidate.is_file():
                continue
            if os.name == "nt" or os.access(str(candidate), os.X_OK):
                return str(candidate)
    return None


def get_nas_cpp_lib_dir() -> Path:
    """Returns the default NAS shared C++ library directory."""
    return get_nas_shared_dir() / "libs/cpp/ips-core-lib/v1.2.3/x86_64"


def get_glibc_version() -> str:
    """Detects the system glibc version."""
    try:
        res = subprocess.run(["ldd", "--version"], capture_output=True, text=True, timeout=2)
        if res.stdout:
            first_line = res.stdout.splitlines()[0]
            # e.g., "ldd (GNU libc) 2.28" or "ldd (Ubuntu GLIBC 2.35-0ubuntu3.8) 2.35"
            parts = first_line.split()
            for part in reversed(parts):
                if part.replace(".", "").isdigit():
                    return part
    except (OSError, subprocess.SubprocessError) as err:
        _ = err
    try:
        import ctypes

        gnu_get_libc_version = ctypes.CDLL(None).gnu_get_libc_version
        gnu_get_libc_version.restype = ctypes.c_char_p
        return gnu_get_libc_version().decode("utf-8")
    except (OSError, AttributeError) as err:
        _ = err
        return "unknown"


def is_wsl() -> bool:
    """Checks if running inside Windows Subsystem for Linux."""
    if "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ:
        return True
    try:
        with open("/proc/version", encoding="utf-8") as f:
            content = f.read().lower()
            return "microsoft" in content or "wsl" in content
    except OSError as err:
        _ = err
        return False


def get_os_release_info() -> dict[str, str]:
    """Reads /etc/os-release into a dictionary."""
    info = {}
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            with open(os_release, encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.startswith("#"):
                        k, v = line.strip().split("=", 1)
                        info[k] = v.strip('"').strip("'")
        except OSError as err:
            _ = err
    return info


def get_system_info() -> dict[str, Any]:
    """Gathers comprehensive system information for diagnostics."""
    os_info = get_os_release_info()
    os_id = os_info.get("ID", platform.system().lower())
    os_version = os_info.get("VERSION_ID", platform.release())

    return {
        "os_id": os_id,
        "os_version": os_version,
        "pretty_name": os_info.get("PRETTY_NAME", f"{os_id} {os_version}"),
        "glibc": get_glibc_version(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "is_wsl": is_wsl(),
        "shell": os.environ.get("SHELL", "/bin/sh"),
        "lang": os.environ.get("LANG", "C.UTF-8"),
        "term": os.environ.get("TERM", "xterm-256color"),
    }


def find_python_candidates() -> list[tuple[str, str, str]]:
    """Finds all candidate Python interpreters and their detected versions.

    Returns list of tuples: (candidate_name, path, version_str)
    """
    results = []
    seen_paths = set()

    # 1. Custom ICI_PYTHON
    if os.environ.get("ICI_PYTHON"):
        p = shutil.which(os.environ["ICI_PYTHON"]) or os.environ["ICI_PYTHON"]
        if os.path.exists(p) and os.access(p, os.X_OK):
            ver = _get_py_ver(p)
            if ver:
                results.append(("$ICI_PYTHON", p, ver))
                seen_paths.add(os.path.realpath(p))

    # 2. Standard Candidates
    for cand in PYTHON_CANDIDATES:
        cand_p = shutil.which(cand)
        if cand_p and os.access(cand_p, os.X_OK):
            real = os.path.realpath(cand_p)
            if real not in seen_paths:
                ver = _get_py_ver(cand_p)
                if ver:
                    results.append((cand, cand_p, ver))
                    seen_paths.add(real)

    return results


def _get_py_ver(py_path: str) -> str | None:
    try:
        res = subprocess.run(
            [
                py_path,
                "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError) as err:
        _ = err
    return None
