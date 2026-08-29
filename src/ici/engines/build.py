"""10. Artifact Packaging & Release Environment Generator Engine."""

import os
import re
import shutil
import time
from pathlib import Path

import tomli

from ici.core.cmake import build as adapter_build
from ici.core.cmake import configure as adapter_configure
from ici.core.cmake import select_backend
from ici.core.env import get_nas_cpp_lib_dir
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.project import (
    detect_project_type,
    get_all_cpp_includes,
    get_all_python_sources,
    get_compilable_cpp_sources,
    get_project_name,
    get_project_version,
    get_source_dirs,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine
from ici.engines.cpp_text import mask_cpp_literals as _mask_cpp_literals

_ENTRYPOINT_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):"
    r"(?P<callable>[A-Za-z_][A-Za-z0-9_]*)$"
)
_SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MAIN_DEFINITION_RE = re.compile(r"\bint\s+main\s*\([^{};]*\)\s*(?:noexcept\s*)?\{")


class BuildEngine(BaseEngine):
    """Compile and package only artifacts created inside the project root."""

    def run(self) -> EngineResult:
        t0 = time.time()
        base = self.project_root
        targets: list[InspectionTarget] = []
        self._tool_errors: list[str] = []
        self._tool_evidence: list[ToolEvidence] = []
        self._has_fail = False
        self._artifact_count = 0
        project_name = ""
        project_version = ""
        target_path: Path | None = None

        try:
            project_name = get_project_name(base)
            project_version = get_project_version(base)
            project_type = detect_project_type(base)
            target_path = base / project_version / "x86_64"
            python_sources = (
                self._exclude_target_sources(get_all_python_sources(base, self.config), target_path)
                if project_type in ("python", "hybrid")
                else []
            )
            cpp_sources = (
                self._exclude_target_sources(
                    get_compilable_cpp_sources(base, self.config), target_path
                )
                if project_type in ("cpp", "hybrid")
                else []
            )
            bin_dir, lib_dir = self._prepare_output_tree(base, target_path)

            if project_type in ("python", "hybrid"):
                self._package_python(base, project_name, python_sources, bin_dir, lib_dir, targets)

            cpp_scope = (
                project_type == "cpp" or bool(cpp_sources) or self._has_build_descriptor(base)
            )
            if cpp_scope:
                self._compile_cpp(base, project_name, cpp_sources, bin_dir, targets)
        except Exception as exc:
            self._record_error(targets, f"Build metadata or setup error: {exc}")

        if not self._tool_errors and not self._has_fail and self._artifact_count == 0:
            self._has_fail = True
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="BuildArtifacts",
                    status=EngineStatus.FAIL,
                    message="No Python library, launcher, or C++ binary artifact was created",
                )
            )

        if not self._tool_errors and not self._has_fail and self._artifact_count:
            try:
                if target_path is None:
                    raise ValueError("build target path was not prepared")
                self._generate_env_scripts(base, target_path, targets)
            except Exception as exc:
                self._record_error(targets, f"Could not generate environment scripts: {exc}")

        duration = time.time() - t0
        if self._tool_errors:
            status = EngineStatus.ERROR
            summary = "; ".join(self._tool_errors[:3])
        elif self._has_fail:
            status = EngineStatus.FAIL
            summary = "Build did not produce a complete artifact set"
        else:
            status = EngineStatus.PASS
            relative_target = (
                str(target_path.relative_to(base)) if target_path is not None else "unknown"
            )
            summary = f"Build Completed: {project_name} {project_version} into {relative_target}"

        return self.create_result(
            name="build",
            status=status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "project_name": project_name,
                "project_version": project_version,
                "target_path": str(target_path) if target_path is not None else "",
                "metrics_summary": (
                    f"Built into {target_path.relative_to(base)}"
                    if target_path is not None
                    else "Build target was not prepared"
                ),
            },
            required=True,
            evidence=(EvidenceState.NOT_RUN if self._tool_errors else EvidenceState.MEASURED),
            tool_evidence=self._tool_evidence,
        )

    @staticmethod
    def _exclude_target_sources(sources: list[Path], target: Path) -> list[Path]:
        """Keep source discovery from treating this run's output as input."""

        filtered: list[Path] = []
        for source in sources:
            try:
                source.relative_to(target)
            except ValueError:
                filtered.append(source)
        return filtered

    def _prepare_output_tree(self, base: Path, target: Path) -> tuple[Path, Path]:
        self._ensure_directory_chain(base, target)
        bin_dir = target / "bin"
        lib_dir = target / "lib"
        self._ensure_directory_chain(base, bin_dir)
        self._ensure_directory_chain(base, lib_dir)
        return bin_dir, lib_dir

    @staticmethod
    def _ensure_directory_chain(base: Path, path: Path) -> None:
        try:
            parts = path.relative_to(base).parts
        except ValueError as err:
            raise ValueError(f"output path is outside project root: {path}") from err
        current = base
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"output path contains symlink: {current}")
            if current.exists():
                if not current.is_dir():
                    raise ValueError(f"output path is not a directory: {current}")
            else:
                current.mkdir()

    @staticmethod
    def _ensure_output_file_available(base: Path, path: Path) -> None:
        try:
            path.relative_to(base)
        except ValueError as err:
            raise ValueError(f"output path is outside project root: {path}") from err
        current = path
        while current != base:
            if current.is_symlink():
                raise ValueError(f"output path contains symlink: {current}")
            current = current.parent
        if path.is_symlink():
            raise ValueError(f"output path contains symlink: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"output path is not a regular file: {path}")

    def _package_python(
        self,
        base: Path,
        project_name: str,
        sources: list[Path],
        bin_dir: Path,
        lib_dir: Path,
        targets: list[InspectionTarget],
    ) -> None:
        try:
            source_dirs = get_source_dirs(base, self.config)
        except Exception as exc:
            self._record_error(targets, f"Could not discover Python source directories: {exc}")
            return

        seen_sources: set[Path] = set()
        destinations: set[Path] = set()
        for source in sources:
            if source in seen_sources:
                continue
            seen_sources.add(source)
            if source.is_symlink() or not source.is_file():
                self._record_error(
                    targets, f"Python source is not a regular non-symlink file: {source}"
                )
                continue
            relative = self._source_relative_path(source, source_dirs)
            if relative is None:
                self._record_error(targets, f"Python source is outside configured roots: {source}")
                continue
            destination = lib_dir / relative
            if destination in destinations:
                self._record_error(targets, f"Python artifact collision at {destination}")
                continue
            destinations.add(destination)
            try:
                self._ensure_directory_chain(base, destination.parent)
                self._ensure_output_file_available(base, destination)
                shutil.copy2(source, destination)
            except Exception as exc:
                self._record_error(targets, f"Could not copy Python source {source}: {exc}")
                continue
            self._artifact_count += 1
            targets.append(
                InspectionTarget(
                    file_path=str(destination.relative_to(base)),
                    start_line=1,
                    target_name="PythonLibrary",
                    status=EngineStatus.PASS,
                    message=f"Packaged Python source {relative}",
                )
            )

        entrypoints = self._python_entrypoints(base, project_name, targets)
        for script_name, target in entrypoints:
            match = _ENTRYPOINT_RE.fullmatch(target)
            if match is None:
                self._record_error(targets, f"Invalid Python entrypoint: {target}")
                continue
            if not self._valid_script_name(script_name):
                self._record_error(targets, f"Unsafe Python launcher name: {script_name!r}")
                continue
            module = match.group("module")
            callable_name = match.group("callable")
            if not self._python_module_exists(base, module):
                self._record_error(targets, f"Python entrypoint module does not exist: {module}")
                continue
            launcher = bin_dir / script_name
            try:
                self._ensure_output_file_available(base, launcher)
                launcher.write_text(self._launcher_content(module, callable_name), encoding="utf-8")
                os.chmod(launcher, 0o755)
            except Exception as exc:
                self._record_error(
                    targets, f"Could not create Python launcher {script_name}: {exc}"
                )
                continue
            self._artifact_count += 1
            targets.append(
                InspectionTarget(
                    file_path=str(launcher.relative_to(base)),
                    start_line=1,
                    target_name="PythonLauncher",
                    status=EngineStatus.PASS,
                    message=f"Callable launcher for {target}",
                )
            )

    @staticmethod
    def _source_relative_path(source: Path, source_dirs: list[Path]) -> Path | None:
        for source_dir in source_dirs:
            try:
                relative = source.relative_to(source_dir)
            except ValueError:
                continue
            if relative.parts:
                return relative
        return None

    def _python_entrypoints(
        self, base: Path, project_name: str, targets: list[InspectionTarget]
    ) -> list[tuple[str, str]]:
        build_config = self.config.get("build", {})
        if not isinstance(build_config, dict):
            self._record_error(targets, "build configuration must be a table")
            return []
        python_config = build_config.get("python", {})
        if not isinstance(python_config, dict):
            self._record_error(targets, "build.python configuration must be a table")
            return []
        if "entrypoint" in python_config:
            entrypoint = python_config["entrypoint"]
            if not isinstance(entrypoint, str) or not entrypoint.strip():
                self._record_error(targets, "build.python.entrypoint must be a non-empty string")
                return []
            return [(project_name, entrypoint)]

        try:
            scripts = self._read_project_scripts(base)
        except Exception as exc:
            self._record_error(targets, f"Could not read Python project scripts: {exc}")
            return []
        return sorted(scripts.items())

    @staticmethod
    def _read_project_scripts(base: Path) -> dict[str, str]:
        path = base / "pyproject.toml"
        if not path.exists():
            return {}
        if path.is_symlink():
            raise ValueError("pyproject.toml is a symlink")
        try:
            with path.open("rb") as stream:
                document = tomli.load(stream)
        except (OSError, ValueError, RecursionError, UnicodeError) as exc:
            raise ValueError(f"could not parse pyproject.toml: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("pyproject.toml must contain a table")
        project = document.get("project", {})
        if not isinstance(project, dict):
            raise ValueError("pyproject.toml [project] must be a table")
        scripts = project.get("scripts", {})
        if not isinstance(scripts, dict):
            raise ValueError("pyproject.toml [project.scripts] must be a table")
        result: dict[str, str] = {}
        for name, target in scripts.items():
            if not isinstance(name, str) or not isinstance(target, str):
                raise ValueError("project.scripts names and targets must be strings")
            result[name] = target
        return result

    def _python_module_exists(self, base: Path, module: str) -> bool:
        try:
            source_dirs = get_source_dirs(base, self.config)
        except Exception:
            return False
        parts = module.split(".")
        for source_dir in source_dirs:
            module_path = source_dir.joinpath(*parts)
            candidates = (module_path.with_suffix(".py"), module_path / "__init__.py")
            for candidate in candidates:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(base)
                except (OSError, RuntimeError, ValueError):
                    continue
                return True
        return False

    @staticmethod
    def _valid_script_name(name: str) -> bool:
        return name not in {".", ".."} and _SCRIPT_NAME_RE.fullmatch(name) is not None

    @staticmethod
    def _launcher_content(module: str, callable_name: str) -> str:
        return f'''#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export PYTHONPATH="$SCRIPT_DIR/../lib:$ROOT_DIR/src:${{PYTHONPATH:-}}"

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/.venv/Scripts/python" ]; then
    PYTHON="$ROOT_DIR/.venv/Scripts/python"
else
    PYTHON="python3"
fi
exec "$PYTHON" -c 'import importlib, sys; sys.exit(importlib.import_module("{module}").{callable_name}())' "$@"
'''

    def _compile_cpp(
        self,
        base: Path,
        project_name: str,
        sources: list[Path],
        bin_dir: Path,
        targets: list[InspectionTarget],
    ) -> None:
        choice = select_backend(base)
        if choice.kind is not None:
            self._build_with_adapter(base, targets)
            return
        if self._has_build_descriptor(base):
            self._record_error(
                targets,
                f"{choice.descriptor or 'A build descriptor'} at the project root has no "
                "adapter; only CMake and qmake are supported, and generic g++ was not invoked",
            )
            return

        main_count, inspection_error = self._count_cpp_main_definitions(base, sources, targets)
        if inspection_error:
            return
        if main_count != 1:
            self._record_error(
                targets,
                f"C++ adapter required: expected exactly one int main definition, found {main_count}",
            )
            return

        gxx = shutil.which("g++")
        if not gxx:
            self._record_tool_error(
                targets,
                "g++ is not installed; C++ build could not run",
                path="g++",
                argv=["g++"],
            )
            return

        target_bin = bin_dir / project_name
        try:
            self._ensure_output_file_available(base, target_bin)
            if target_bin.exists():
                target_bin.unlink()
            include_flags = get_all_cpp_includes(base, self.config)
            nas_cpp = get_nas_cpp_lib_dir()
            lib_flags: list[str] = []
            if nas_cpp.exists() and (nas_cpp / "lib").exists():
                lib_flags = [
                    f"-L{nas_cpp / 'lib'}",
                    "-lips_core",
                    f"-Wl,-rpath,{nas_cpp / 'lib'}",
                ]
            command = [
                gxx,
                "-std=c++17",
                *include_flags,
                *(str(source) for source in sources),
                *lib_flags,
                "-o",
                str(target_bin),
            ]
        except Exception as exc:
            self._record_error(targets, f"Could not prepare C++ output: {exc}")
            return

        try:
            result = run_process(command, cwd=base)
        except Exception as exc:
            self._record_tool_error(
                targets,
                f"g++ could not execute: {type(exc).__name__}: {exc}",
                path=gxx,
                argv=command,
            )
            return

        evidence = ToolEvidence(
            name="g++ build",
            path=gxx,
            argv=command,
            returncode=result.returncode,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )
        self._tool_evidence.append(evidence)
        target_file = str(target_bin.relative_to(base))
        if result.timed_out or result.truncated:
            message = "C++ compilation output was incomplete"
            evidence.error = message
            self._record_error(targets, message, target_file)
            return
        if not isinstance(result.returncode, int) or result.returncode < 0:
            message = "C++ compiler terminated before producing a result"
            evidence.error = message
            self._record_error(targets, message, target_file)
            return
        if result.returncode == 1:
            message = f"C++ compilation failed: {result.stderr[:200]}"
            evidence.error = message
            self._has_fail = True
            targets.append(
                InspectionTarget(
                    file_path=target_file,
                    start_line=1,
                    target_name="CppBinary",
                    status=EngineStatus.FAIL,
                    message=message,
                )
            )
            return
        if result.returncode != 0:
            message = f"g++ failed with exit code {result.returncode}"
            evidence.error = message
            self._record_error(targets, message, target_file)
            return
        if target_bin.is_symlink() or not target_bin.is_file():
            message = "g++ reported success but no regular binary was produced"
            evidence.error = message
            self._record_error(targets, message, target_file)
            return
        self._artifact_count += 1
        targets.append(
            InspectionTarget(
                file_path=target_file,
                start_line=1,
                target_name="CppBinary",
                status=EngineStatus.PASS,
                message=f"Compiled C++17 binary ({result.duration:.2f}s)",
            )
        )

    @staticmethod
    def _has_build_descriptor(base: Path) -> bool:
        direct_names = {"CMakeLists.txt", "Makefile", "makefile", "GNUmakefile"}
        if any((base / name).is_file() and not (base / name).is_symlink() for name in direct_names):
            return True
        return any(
            path.is_file() and not path.is_symlink()
            for pattern in ("*.pro", "Makefile.*", "*.mk", "*.make")
            for path in base.glob(pattern)
        )

    def _count_cpp_main_definitions(
        self, base: Path, sources: list[Path], targets: list[InspectionTarget]
    ) -> tuple[int, bool]:
        count = 0
        inspection_error = False
        for source in sources:
            try:
                text = _mask_cpp_literals(source.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                try:
                    file_path = str(source.relative_to(base))
                except ValueError:
                    file_path = "."
                self._record_error(
                    targets,
                    f"Could not inspect C++ source {source}: {exc}",
                    file_path,
                )
                inspection_error = True
                continue
            count += len(_MAIN_DEFINITION_RE.findall(text))
        return count, inspection_error

    def _generate_env_scripts(
        self, base: Path, target_path: Path, targets: list[InspectionTarget]
    ) -> None:
        env_files = {
            "env.sh": """#!/usr/bin/env bash
# Portable Environment Loader for BASH / ZSH / SH
SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"
export PATH=\"${SCRIPT_DIR}/bin:${PATH}\"
export PYTHONPATH=\"${SCRIPT_DIR}/lib:${PYTHONPATH:-}\"
echo \"[ici Env] Loaded release environment from ${SCRIPT_DIR}\"
""",
            "env.csh": """#!/bin/csh
# Portable Environment Loader for CSH / TCSH
set SCRIPT_PATH = ($_)
if (\"$SCRIPT_PATH\" == \"\") then
    set SCRIPT_DIR = `dirname $0`
else
    set SCRIPT_DIR = `dirname $SCRIPT_PATH[2]`
endif
set FULL_DIR = `cd \"$SCRIPT_DIR\" && pwd`
setenv PATH \"${FULL_DIR}/bin:${PATH}\"
setenv PYTHONPATH \"${FULL_DIR}/lib:${PYTHONPATH}\"
echo \"[ici Env] Loaded release environment from ${FULL_DIR}\"
""",
        }
        paths = [(target_path / name, content) for name, content in env_files.items()]
        for path, _content in paths:
            self._ensure_output_file_available(base, path)
        for path, content in paths:
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o755)
            targets.append(
                InspectionTarget(
                    file_path=str(path.relative_to(base)),
                    start_line=1,
                    target_name="EnvironmentScript",
                    status=EngineStatus.PASS,
                    message=f"Generated {path.name}",
                )
            )

    def _build_with_adapter(self, base: Path, targets: list[InspectionTarget]) -> None:
        """Delegate configure and build to the project's own build system."""

        session = adapter_configure(base)
        if not session.configured:
            self._tool_evidence.extend(session.tool_evidence)
            # A configure that fails without saying why must still be
            # diagnosable. Without this the run falls through to the generic
            # "no artifact was created" failure, which names the wrong cause.
            messages = session.errors or [
                f"{session.backend} configure did not complete and reported no reason"
            ]
            for message in messages:
                self._record_error(targets, message, file_path=session.descriptor or ".")
            return

        built = adapter_build(session)
        # Copy evidence once, after every adapter call. Slicing by the engine's
        # own list length would index into the wrong list.
        self._tool_evidence.extend(session.tool_evidence)
        if not built:
            self._has_fail = True
            for message in session.errors:
                targets.append(
                    InspectionTarget(
                        file_path=session.descriptor or ".",
                        start_line=1,
                        target_name="BuildAdapter",
                        status=EngineStatus.FAIL,
                        message=message,
                    )
                )
            return

        produced = self._count_adapter_artifacts(session.shadow)
        self._artifact_count += produced
        targets.append(
            InspectionTarget(
                file_path=session.descriptor,
                start_line=1,
                target_name=f"BuildAdapter[{session.backend}]",
                status=EngineStatus.PASS,
                message=f"{session.reason}; produced {produced} artifact(s)",
            )
        )

    @staticmethod
    def _count_adapter_artifacts(shadow: Path) -> int:
        """Count linked outputs in the shadow tree: executables and libraries."""

        count = 0
        for path in shadow.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix in (".a", ".so"):
                count += 1
                continue
            try:
                is_elf = os.access(path, os.X_OK) and path.read_bytes()[:4] == b"\x7fELF"
            except OSError:
                continue
            if is_elf:
                count += 1
        return count

    def _record_error(
        self,
        targets: list[InspectionTarget],
        message: str,
        file_path: str = ".",
    ) -> None:
        self._tool_errors.append(message)
        targets.append(
            InspectionTarget(
                file_path=file_path,
                start_line=1,
                target_name="BuildError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )

    def _record_tool_error(
        self,
        targets: list[InspectionTarget],
        message: str,
        *,
        path: str,
        argv: list[str],
    ) -> None:
        self._tool_evidence.append(
            ToolEvidence(name="g++ build", path=path, argv=argv, error=message)
        )
        self._record_error(targets, message)
