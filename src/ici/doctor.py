"""System, toolchain, and runtime environment diagnostics for ici."""

import sys
from pathlib import Path
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
from ici.core.support import evaluate_support_matrix
from ici.core.toolchain import DEFAULT_PROBES, collect_tool_capability
from ici.reporters.json_rep import serialize_support_matrix

console = Console()


def collect_diagnostics(
    project_root: Path | None = None, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Collects all environment and toolchain information."""
    root = (project_root or Path.cwd()).resolve()
    sys_info = get_system_info()
    py_candidates = find_python_candidates()

    # Unified tool probing via core/toolchain (required_tools policy aware)
    if config is None:
        try:
            from ici.config import load_config

            config = load_config(root)
        except Exception:
            config = {}
    if config is None:
        config = {}
    required_tools = set(config.get("doctor", {}).get("required_tools", []) or [])
    probe_map: dict[str, list[str]] = dict(DEFAULT_PROBES)
    probe_map.update(
        {
            "clang": ["clang", "--version"],
            "clang-format": ["clang-format", "--version"],
            "ruff": ["ruff", "--version"],
            "mypy": ["mypy", "--version"],
            "pytest": ["pytest", "--version"],
            "uv": [find_uv() or "uv", "--version"],
        }
    )
    tools: dict[str, dict[str, Any]] = {}
    for tool_name, probe in probe_map.items():
        cap, _result = collect_tool_capability(tool_name, probe, cwd=root)
        tools[tool_name] = {
            "available": cap.available,
            "version": cap.version,
            "path": cap.path,
            "error": cap.error,
            "required": tool_name in required_tools,
        }

    # NAS checks
    nas_root = get_nas_shared_dir()
    nas_cpp = get_nas_cpp_lib_dir()
    infra_root = find_infra_root()
    support_matrix = serialize_support_matrix(evaluate_support_matrix(root, config))

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
        "required_tools": sorted(required_tools),
        "support_matrix": support_matrix,
        "paths": {
            "infra_root": str(infra_root),
            "nas_shared": str(nas_root),
            "nas_shared_exists": nas_root.exists(),
            "nas_cpp_lib": str(nas_cpp),
            "nas_cpp_lib_exists": nas_cpp.exists(),
        },
    }


def _support_language(entry: dict[str, Any]) -> str:
    language = str(entry.get("language") or "-")
    frameworks = entry.get("frameworks", []) or []
    if frameworks:
        language += " (" + ", ".join(str(item) for item in frameworks) + ")"
    return language


def _support_state(entry: dict[str, Any]) -> str:
    if not entry.get("applicable", False):
        return "not-applicable"
    if not entry.get("enabled", False):
        return "disabled"
    return "applicable"


def _support_tools(entry: dict[str, Any]) -> str:
    required = entry.get("required_tools", []) or []
    optional = entry.get("optional_tools", []) or []
    parts = []
    if required:
        parts.append("req: " + ", ".join(str(item) for item in required))
    if optional:
        parts.append("opt: " + ", ".join(str(item) for item in optional))
    return "; ".join(parts) or "-"


def _support_detail(entry: dict[str, Any]) -> str:
    parts = [str(entry.get("reason") or "-")]
    limitations = entry.get("limitations", []) or []
    if limitations:
        parts.append(str(limitations[0]))
    return " | ".join(parts)


def _render_support_table(matrix: dict[str, Any]) -> None:
    """Render declarations without adding branches to the doctor shell."""
    table = Table(
        title="Engine Capability Matrix",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=False,
    )
    table.add_column("Engine", style="bold", width=12, no_wrap=True)
    table.add_column("Language", width=13, no_wrap=True)
    table.add_column("State", width=15, no_wrap=True)
    table.add_column("Declared / Active", width=24)
    table.add_column("Evidence / Confidence", width=22)
    table.add_column("Tools", width=25)
    table.add_column("Fallback", width=14)
    table.add_column("Detail", style="dim")

    for entry in matrix.get("entries", []):
        declared = entry.get("mode") or "-"
        active = entry.get("active_mode") or "-"
        evidence = entry.get("evidence") or "-"
        confidence = entry.get("confidence") or "-"
        table.add_row(
            str(entry.get("engine_name") or "-"),
            _support_language(entry),
            _support_state(entry),
            f"{declared} / {active}",
            f"{evidence} / {confidence}",
            _support_tools(entry),
            str(entry.get("fallback_mode") or "-"),
            _support_detail(entry),
        )
    console.print(table)


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
        is_required = bool(info.get("required"))
        if info["available"]:
            st = "[green]Available[/green]"
            v = f"{info['version']} ({info['path']})"
        elif is_required:
            st = "[yellow]Missing (required) WARN[/yellow]"
            v = info.get("error") or "-"
        else:
            st = "[red]Missing[/red]"
            v = "-"
        t_tool.add_row(tool_name, st, v)
    console.print(t_tool)

    # Engine capability matrix. This is intentionally a read-only declaration
    # view: doctor never runs an engine, so applicable enabled rows are
    # normally reported as NOT_RUN until a verification command observes them.
    matrix = data.get("support_matrix")
    if matrix:
        _render_support_table(matrix)

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

    matrix = data.get("support_matrix")
    if matrix:
        languages = ", ".join(matrix.get("project_languages", []) or []) or "none"
        frameworks = ", ".join(matrix.get("project_frameworks", []) or []) or "none"
        print(f"scope   languages={languages}  frameworks={frameworks}")

    # tool summaries
    tool_strs = []
    for t in ("gcc", "g++", "clang", "make", "cmake", "ruff", "mypy", "pytest", "git"):
        info = tools.get(t, {"available": False})
        label = f"{t}={info['version']}" if info.get("available") else f"{t}=-"
        if not info.get("available") and info.get("required"):
            label += "(!required WARN)"
        tool_strs.append(label)
    print("tools   " + "  ".join(tool_strs[:5]))
    if len(tool_strs) > 5:
        print("        " + "  ".join(tool_strs[5:]))
