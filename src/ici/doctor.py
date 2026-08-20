"""System, toolchain, and runtime environment diagnostics for ici."""

import shutil
import subprocess
import sys
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table

from ici import __version__
from ici.core.env import (
    find_infra_root,
    find_python_candidates,
    find_uv,
    get_nas_cpp_lib_dir,
    get_nas_shared_dir,
    get_system_info,
)

console = Console()


def collect_diagnostics() -> dict[str, Any]:
    """Collects all environment and toolchain information."""
    sys_info = get_system_info()
    py_candidates = find_python_candidates()

    # Tool checks
    tools = {
        "git": _check_tool("git", ["--version"]),
        "gcc": _check_tool("gcc", ["--version"]),
        "g++": _check_tool("g++", ["--version"]),
        "clang": _check_tool("clang", ["--version"]),
        "clang-format": _check_tool("clang-format", ["--version"]),
        "make": _check_tool("make", ["--version"]),
        "cmake": _check_tool("cmake", ["--version"]),
        "ruff": _check_tool("ruff", ["--version"]),
        "mypy": _check_tool("mypy", ["--version"]),
        "pytest": _check_tool("pytest", ["--version"]),
        "uv": _check_tool(find_uv() or "uv", ["--version"]),
    }

    # NAS checks
    nas_root = get_nas_shared_dir()
    nas_cpp = get_nas_cpp_lib_dir()
    infra_root = find_infra_root()

    return {
        "system": sys_info,
        "running_python": {
            "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "executable": sys.executable,
        },
        "python_candidates": [
            {"candidate": c[0], "path": c[1], "version": c[2]} for c in py_candidates
        ],
        "tools": tools,
        "paths": {
            "infra_root": str(infra_root),
            "nas_shared": str(nas_root),
            "nas_shared_exists": nas_root.exists(),
            "nas_cpp_lib": str(nas_cpp),
            "nas_cpp_lib_exists": nas_cpp.exists(),
        },
    }


def render_doctor_table(data: dict[str, Any]) -> None:
    """Renders formatted Rich tables for doctor output."""
    sys_info = data["system"]
    console.print("\n[bold cyan]🚀 ici Environment Diagnostics[/bold cyan]\n")

    # System table
    t_sys = Table(title="OS & System Environment", box=box.ROUNDED, header_style="bold magenta")
    t_sys.add_column("Property", style="bold white", width=18)
    t_sys.add_column("Value", style="cyan")

    t_sys.add_row("Operating System", sys_info.get("pretty_name", "-"))
    t_sys.add_row("Glibc Version", sys_info.get("glibc", "-"))
    t_sys.add_row("Kernel", sys_info.get("kernel", "-"))
    t_sys.add_row("Architecture", sys_info.get("arch", "-"))
    t_sys.add_row("WSL Environment", "Yes" if sys_info.get("is_wsl") else "No")
    t_sys.add_row("Shell", sys_info.get("shell", "-"))
    console.print(t_sys)

    # Python Table
    t_py = Table(title="Python Toolchains & Candidates", box=box.ROUNDED, header_style="bold green")
    t_py.add_column("Candidate", style="bold", width=16)
    t_py.add_column("Version", width=12)
    t_py.add_column("Executable Path", style="dim")

    run_py = data["running_python"]
    t_py.add_row(
        "[green]* running[/green]", f"[green]{run_py['version']}[/green]", run_py["executable"]
    )

    for c in data["python_candidates"]:
        t_py.add_row(c["candidate"], c["version"], c["path"])
    console.print(t_py)

    # Tools Table
    t_tool = Table(
        title="Compiler, Linter & Build Tools", box=box.ROUNDED, header_style="bold yellow"
    )
    t_tool.add_column("Tool", style="bold", width=16)
    t_tool.add_column("Status", width=12)
    t_tool.add_column("Version / Path", style="dim")

    for tool_name, info in data["tools"].items():
        if info["available"]:
            st = "[green]Available[/green]"
            v = f"{info['version']} ({info['path']})"
        else:
            st = "[red]Missing[/red]"
            v = "-"
        t_tool.add_row(tool_name, st, v)
    console.print(t_tool)

    # Paths Table
    t_path = Table(title="Workspace & Shared Paths", box=box.ROUNDED, header_style="bold blue")
    t_path.add_column("Component", style="bold", width=18)
    t_path.add_column("Status", width=12)
    t_path.add_column("Resolved Path", style="dim")

    p = data["paths"]
    t_path.add_row("Infra Root", "[green]Resolved[/green]", p["infra_root"])
    t_path.add_row(
        "NAS Shared",
        "[green]Mounted[/green]" if p["nas_shared_exists"] else "[yellow]Not Found[/yellow]",
        p["nas_shared"],
    )
    t_path.add_row(
        "NAS C++ Libs",
        "[green]Available[/green]" if p["nas_cpp_lib_exists"] else "[yellow]Not Found[/yellow]",
        p["nas_cpp_lib"],
    )
    console.print(t_path)
    console.print()


def render_doctor_brief(data: dict[str, Any]) -> None:
    """Renders concise brief output."""
    sys_info = data["system"]
    run_py = data["running_python"]
    tools = data["tools"]

    print(f"ici {__version__} brief")
    print(
        f"os      {sys_info.get('os_id')}-{sys_info.get('os_version')}  glibc={sys_info.get('glibc')}  arch={sys_info.get('arch')}  wsl={'yes' if sys_info.get('is_wsl') else 'no'}"
    )
    print(
        f"shell   {sys_info.get('shell')}  TERM={sys_info.get('term')}  LANG={sys_info.get('lang')}"
    )
    print(f"python  running={run_py['version']}  path={run_py['executable']}")

    # tool summaries
    tool_strs = []
    for t in ("gcc", "g++", "clang", "make", "cmake", "ruff", "mypy", "pytest", "git"):
        if tools[t]["available"]:
            tool_strs.append(f"{t}={tools[t]['version']}")
        else:
            tool_strs.append(f"{t}=-")
    print("tools   " + "  ".join(tool_strs[:5]))
    if len(tool_strs) > 5:
        print("        " + "  ".join(tool_strs[5:]))


def _check_tool(name: str, ver_args: list[str]) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"available": False, "version": "", "path": ""}
    try:
        res = subprocess.run([path, *ver_args], capture_output=True, text=True, timeout=2)
        out = (res.stdout or res.stderr).strip()
        first_line = out.splitlines()[0] if out else ""
        # Extract version like 8.5.0, 17.0.0, etc.
        parts = first_line.split()
        ver = parts[-1] if parts else first_line
        for p in parts:
            if any(c.isdigit() for c in p) and "." in p:
                ver = p.strip("v").strip("()")
                break
        return {"available": True, "version": ver, "path": path}
    except (OSError, subprocess.SubprocessError) as err:
        _ = err
        return {"available": True, "version": "unknown", "path": path}
