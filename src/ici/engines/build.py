"""10. Artifact Packaging & Release Environment Generator Engine."""

import compileall
import os
import shutil
import time
from pathlib import Path

from ici.core.env import get_nas_cpp_lib_dir
from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    detect_project_type,
    get_all_cpp_includes,
    get_all_cpp_sources,
    get_project_name,
    get_project_version,
    get_source_dirs,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine


class BuildEngine(BaseEngine):
    """Compiles and packages release artifacts and portable environment loaders."""

    def run(self) -> EngineResult:
        t0 = time.time()
        base = self.project_root
        proj_name = get_project_name(base)
        proj_version = get_project_version(base)
        proj_type = detect_project_type(base)

        arch = "x86_64"
        target_path = base / proj_version / arch
        bin_dir = target_path / "bin"
        lib_dir = target_path / "lib"

        bin_dir.mkdir(parents=True, exist_ok=True)
        lib_dir.mkdir(parents=True, exist_ok=True)

        targets: list[InspectionTarget] = []
        has_error = False

        # 1. Python Artifact Packaging
        if proj_type in ("python", "hybrid") and (base / "src").exists():
            self._package_python(base, proj_name, bin_dir, lib_dir, targets)

        # 2. C++ Compilation & Linking
        if proj_type in ("cpp", "hybrid"):
            c_err = self._compile_cpp(base, proj_name, bin_dir, targets)
            if c_err:
                has_error = True

        # 3. Generate portable env.sh and env.csh
        self._generate_env_scripts(target_path, targets)

        duration = time.time() - t0
        overall_status = EngineStatus.FAIL if has_error else EngineStatus.PASS
        summary = f"Build Completed: {proj_name} {proj_version} ({arch}) into {target_path.relative_to(base)}"

        return self.create_result(
            name="build",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "project_name": proj_name,
                "project_version": proj_version,
                "target_path": str(target_path),
                "metrics_summary": f"Built into {proj_version}/{arch}",
            },
        )

    def _package_python(
        self,
        base: Path,
        proj_name: str,
        bin_dir: Path,
        lib_dir: Path,
        targets: list[InspectionTarget],
    ) -> None:
        src_dirs = get_source_dirs(base, self.config)
        src_dir = src_dirs[0] if src_dirs else base / "src"
        compileall.compile_dir(str(src_dir), legacy=True, quiet=1)

        for root, _dirs, files in os.walk(str(src_dir)):
            for file in files:
                if file.endswith((".pyc", ".py")):
                    src_f = Path(root) / file
                    rel_p = src_f.relative_to(src_dir)
                    dst_f = lib_dir / rel_p
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_f, dst_f)

        # Identify entrypoint module
        entrypoint = None
        for root, _dirs, files in os.walk(str(src_dir)):
            if "main.py" in files:
                rel = Path(root).relative_to(src_dir)
                entrypoint = f"{str(rel).replace(os.sep, '.')}.main" if str(rel) != "." else "main"
                break

        if not entrypoint and any(src_dir.rglob("*.py")):
            first_py = next(src_dir.rglob("*.py"))
            rel = first_py.relative_to(src_dir).with_suffix("")
            entrypoint = str(rel).replace(os.sep, ".")

        if entrypoint:
            launcher_bin = bin_dir / proj_name
            launcher_content = f"""#!/usr/bin/env bash
# Dynamic Portable Python Launcher for {proj_name}
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$SCRIPT_DIR/../lib:$ROOT_DIR/src:${{PYTHONPATH}}"

if [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    exec "$ROOT_DIR/.venv/bin/python" -m {entrypoint} "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --directory "$ROOT_DIR" python -m {entrypoint} "$@"
else
    exec python3 -m {entrypoint} "$@"
fi
"""
            launcher_bin.write_text(launcher_content, encoding="utf-8")
            os.chmod(launcher_bin, 0o755)

            targets.append(
                InspectionTarget(
                    file_path=str(launcher_bin.relative_to(base)),
                    start_line=1,
                    target_name="PythonLauncher",
                    status=EngineStatus.PASS,
                    message=f"Executable launcher for module '{entrypoint}'",
                )
            )

    def _compile_cpp(
        self, base: Path, proj_name: str, bin_dir: Path, targets: list[InspectionTarget]
    ) -> bool:
        gxx = shutil.which("g++")
        if not gxx:
            return False

        all_cpp_sources = [str(f) for f in get_all_cpp_sources(base, self.config)]
        inc_flags = get_all_cpp_includes(base, self.config)
        nas_cpp = get_nas_cpp_lib_dir()
        lib_flags = []
        if nas_cpp.exists() and (nas_cpp / "lib").exists():
            lib_flags = [f"-L{nas_cpp / 'lib'}", "-lips_core", f"-Wl,-rpath,{nas_cpp / 'lib'}"]

        if all_cpp_sources:
            target_bin = bin_dir / proj_name
            cmd = [
                gxx,
                "-std=c++17",
                *inc_flags,
                *all_cpp_sources,
                *lib_flags,
                "-o",
                str(target_bin),
            ]
            code, _out, err, dur = run_process(cmd, cwd=base)
            if code == 0:
                targets.append(
                    InspectionTarget(
                        file_path=str(target_bin.relative_to(base)),
                        start_line=1,
                        target_name="CppBinary",
                        status=EngineStatus.PASS,
                        message=f"Compiled C++17 binary ({dur:.2f}s)",
                    )
                )
                return False
            else:
                targets.append(
                    InspectionTarget(
                        file_path=str(target_bin.relative_to(base)),
                        start_line=1,
                        target_name="CppBinary",
                        status=EngineStatus.FAIL,
                        message=f"C++ Compilation failed: {err[:200]}",
                    )
                )
                return True
        return False

    def _generate_env_scripts(self, target_path: Path, targets: list[InspectionTarget]) -> None:
        env_sh = target_path / "env.sh"
        env_sh_content = """#!/usr/bin/env bash
# Portable Environment Loader for BASH / ZSH / SH
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="${SCRIPT_DIR}/bin:${PATH}"
export PYTHONPATH="${SCRIPT_DIR}/lib:${PYTHONPATH}"
echo "[ici Env] Loaded release environment from ${SCRIPT_DIR}"
"""
        env_sh.write_text(env_sh_content, encoding="utf-8")
        os.chmod(env_sh, 0o755)

        env_csh = target_path / "env.csh"
        env_csh_content = """#!/bin/csh
# Portable Environment Loader for CSH / TCSH
set SCRIPT_PATH = ($_)
if ("$SCRIPT_PATH" == "") then
    set SCRIPT_DIR = `dirname $0`
else
    set SCRIPT_DIR = `dirname $SCRIPT_PATH[2]`
endif
set FULL_DIR = `cd "$SCRIPT_DIR" && pwd`
setenv PATH "${FULL_DIR}/bin:${PATH}"
setenv PYTHONPATH "${FULL_DIR}/lib:${PYTHONPATH}"
echo "[ici Env] Loaded release environment from ${FULL_DIR}"
"""
        env_csh.write_text(env_csh_content, encoding="utf-8")
        os.chmod(env_csh, 0o755)
