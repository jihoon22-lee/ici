"""Contract tests for the canonical CMake compilation-database preflight.

The preflight deliberately owns only the CMake-generated database path.  An
explicit or already-discovered database remains authoritative, while a
canonical shadow is configured only when no usable database exists.  All
processes in this module are fakes: the tests exercise argv, bounded evidence,
and reload ordering without requiring CMake or a particular generator.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from ici.core import cmake_context
from ici.core.capabilities import CapabilityInventory
from ici.core.cmake import (
    BuildSession,
    ConfigureOptions,
    cmake_build_argv,
    cmake_configure_argv,
)
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineResult, EngineStatus, SupportMatrix
from ici.core.runner import ProcessResult
from ici.core.support import ENGINE_NAMES
from ici.engines import verify as verify_module
from ici.engines.verify import VerifyOrchestrator

_CMK = "/usr/bin/cmake"
_COMPILE_DB = "build/ici-cmake-build/compile_commands.json"


def _project(
    root: Path,
    *,
    project_type: str = "cpp",
    sources: tuple[str, ...] = ("src/main.cpp",),
    backend: str | None = "cmake",
) -> ProjectModel:
    return ProjectModel(
        root=root,
        name="cmake-context",
        version="1.0.0",
        project_type=project_type,
        cpp_sources=sources,
        compilable_cpp_sources=sources,
        backend=backend,
        backend_descriptor="CMakeLists.txt" if backend == "cmake" else "",
    )


def _write_cpp_sources(root: Path, sources: tuple[str, ...]) -> None:
    for relative in sources:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int value() { return 0; }\n", encoding="utf-8")


def _write_database(root: Path, relative: str, rows: list[dict[str, object]]) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _row(
    root: Path,
    source: str,
    *,
    directory: Path | None = None,
    output: str | None = None,
) -> dict[str, object]:
    source_path = root / source
    working_directory = directory or root
    arguments = ["/usr/bin/c++", "-std=c++17", "-c", str(source_path)]
    if output is not None:
        arguments.extend(["-o", output])
    return {
        "directory": str(working_directory),
        "file": str(source_path),
        "arguments": arguments,
    }


def _cmake_result(
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    truncated: bool = False,
) -> ProcessResult:
    return ProcessResult(
        returncode,
        stdout,
        stderr,
        0.01,
        timed_out=timed_out,
        truncated=truncated,
    )


def _install_fake_cmake(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    generator: str = "Unix Makefiles",
    on_configure: Callable[[Path, list[str]], None] | None = None,
    on_build: Callable[[Path], None] | None = None,
    configure_result: ProcessResult | None = None,
) -> list[tuple[list[str], Path | None]]:
    """Install a bounded fake CMake adapter and return every argv/cwd pair."""

    calls: list[tuple[list[str], Path | None]] = []

    def fake_configure(project_root: Path, options: ConfigureOptions) -> BuildSession:
        shadow = project_root / "build" / "ici-cmake-build"
        shadow.mkdir(parents=True, exist_ok=True)
        argv = cmake_configure_argv(_CMK, project_root, shadow, options)
        calls.append((argv, project_root))
        successful = configure_result is None or configure_result.returncode == 0
        if successful:
            (shadow / "CMakeCache.txt").write_text(
                "CMAKE_GENERATOR:INTERNAL=" + generator + "\n"
                "CMAKE_UNITY_BUILD:BOOL=OFF\n"
                "CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON\n",
                encoding="utf-8",
            )
            if on_configure is not None:
                on_configure(shadow, argv)
        return BuildSession(
            root=project_root,
            shadow=shadow,
            variant=options.variant,
            backend="cmake",
            descriptor="CMakeLists.txt",
            configured=successful,
        )

    def fake_build(session: BuildSession) -> bool:
        argv = cmake_build_argv(_CMK, session.shadow)
        calls.append((argv, session.root))
        if on_build is not None:
            on_build(session.shadow)
        return True

    monkeypatch.setattr(cmake_context, "configure", fake_configure)
    monkeypatch.setattr(cmake_context, "build", fake_build)
    return calls


def _diagnostic_text(context: CompilationContext) -> str:
    return " ".join(
        f"{diagnostic.code} {diagnostic.message}" for diagnostic in context.diagnostics
    ).lower()


def test_explicit_and_discovered_databases_precede_cmake_and_never_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    _write_cpp_sources(root, ("src/main.cpp",))

    explicit = "metadata/compile_commands.json"
    _write_database(root, explicit, [_row(root, "src/main.cpp")])
    _write_database(root, "compile_commands.json", [])

    def fail_if_configured(*_args: object, **_kwargs: object) -> ProcessResult:
        raise AssertionError("an existing compile database must avoid CMake")

    monkeypatch.setattr(cmake_context, "configure", fail_if_configured)

    configured = cmake_context.prepare_cmake_compilation_context(
        root,
        {"project": {"compile_database": explicit}},
        _project(root),
    )

    assert configured.database_path == explicit
    assert configured.origin == "configured"
    assert len(configured.units) == 1

    (root / explicit).unlink()
    discovered = cmake_context.prepare_cmake_compilation_context(root, {}, _project(root))

    assert discovered.database_path == "compile_commands.json"
    assert discovered.origin == "discovered"
    assert discovered.units == ()


@pytest.mark.parametrize(
    ("project_type", "sources", "write_cmake"),
    [
        ("python", (), True),
        ("cpp", ("src/main.cpp",), False),
    ],
)
def test_no_cpp_or_no_root_cmake_returns_empty_context_without_configuring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project_type: str,
    sources: tuple[str, ...],
    write_cmake: bool,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    if write_cmake:
        (root / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
    _write_cpp_sources(root, sources)
    config = {}
    project = _project(
        root,
        project_type=project_type,
        sources=sources,
        backend="cmake" if write_cmake else None,
    )
    monkeypatch.setattr(
        cmake_context,
        "configure",
        lambda *_args, **_kwargs: pytest.fail("CMake must not run for this project"),
    )
    actual = cmake_context.prepare_cmake_compilation_context(root, config, project)

    assert actual == CompilationContext()


@pytest.mark.parametrize("failure_kind", ["missing", "configure"])
def test_default_cmake_absence_or_failure_is_bounded_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    _write_cpp_sources(root, ("src/main.cpp",))
    project = _project(root)

    if failure_kind == "missing":

        def missing_configure(*_args: object, **_kwargs: object) -> BuildSession:
            raise FileNotFoundError("cmake")

        monkeypatch.setattr(cmake_context, "configure", missing_configure)
    else:
        failure = _cmake_result(1, stderr="cmake failed: " + ("E" * 20_000))
        _install_fake_cmake(monkeypatch, root, configure_result=failure)

    result = cmake_context.prepare_cmake_compilation_context(root, {}, project)

    assert isinstance(result, CompilationContext)
    assert result.units == ()
    assert result.diagnostics
    assert len(result.diagnostics) <= 4
    assert all(len(item.message) <= 512 for item in result.diagnostics)
    text = _diagnostic_text(result)
    assert "cmake" in text
    assert any(word in text for word in ("unavailable", "failed", "error"))
    assert "E" * 512 not in text


def test_canonical_preflight_is_release_uninstrumented_unity_off_and_uses_owned_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    _write_cpp_sources(root, ("src/main.cpp",))

    def configure(shadow: Path, _argv: list[str]) -> None:
        _write_database(root, _COMPILE_DB, [_row(root, "src/main.cpp", directory=shadow)])

    calls = _install_fake_cmake(monkeypatch, root, on_configure=configure)
    result = cmake_context.prepare_cmake_compilation_context(root, {}, _project(root))
    configure_argv = next(argv for argv, _cwd in calls if "-S" in argv)

    assert result.database_path == _COMPILE_DB
    assert result.origin == "cmake"
    assert result.generator == "Unix Makefiles"
    assert result.unity_build is False
    assert configure_argv[configure_argv.index("-B") + 1] == str(root / "build" / "ici-cmake-build")
    assert "-DCMAKE_BUILD_TYPE=Release" in configure_argv
    assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in configure_argv
    assert "-DCMAKE_UNITY_BUILD=OFF" in configure_argv
    assert not any(
        argument.startswith(("-DCMAKE_CXX_FLAGS", "-DCMAKE_EXE_LINKER_FLAGS"))
        for argument in configure_argv
    )


@pytest.mark.parametrize("generator", ["Unix Makefiles", "Ninja"])
def test_makefiles_and_ninja_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    _write_cpp_sources(root, ("src/main.cpp",))

    calls = _install_fake_cmake(
        monkeypatch,
        root,
        generator=generator,
        on_configure=lambda shadow, _argv: _write_database(
            root,
            _COMPILE_DB,
            [_row(root, "src/main.cpp", directory=shadow)],
        ),
    )
    result = cmake_context.prepare_cmake_compilation_context(root, {}, _project(root))

    assert calls
    assert result.generator == generator
    assert "unsupported" not in _diagnostic_text(result)


def test_unsupported_cmake_generator_is_diagnosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    _write_cpp_sources(root, ("src/main.cpp",))
    _install_fake_cmake(
        monkeypatch,
        root,
        generator="Visual Studio 17 2022",
        on_configure=lambda shadow, _argv: _write_database(
            root,
            _COMPILE_DB,
            [_row(root, "src/main.cpp", directory=shadow)],
        ),
    )

    result = cmake_context.prepare_cmake_compilation_context(root, {}, _project(root))

    text = _diagnostic_text(result)
    assert "unsupported" in text
    assert "generator" in text


def test_target_name_comes_from_cmakefiles_object_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    _write_cpp_sources(root, ("src/main.cpp",))

    def configure(shadow: Path, _argv: list[str]) -> None:
        output = "CMakeFiles/demo_core.dir/src/main.cpp.o"
        _write_database(
            root,
            _COMPILE_DB,
            [_row(root, "src/main.cpp", directory=shadow, output=output)],
        )

    _install_fake_cmake(monkeypatch, root, on_configure=configure)
    result = cmake_context.prepare_cmake_compilation_context(root, {}, _project(root))

    assert len(result.units) == 1
    assert result.units[0].output.endswith("CMakeFiles/demo_core.dir/src/main.cpp.o")
    assert result.units[0].target == "demo_core"


def test_unity_compile_database_rows_are_diagnosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    shadow = root / "build" / "ici-cmake-build"
    unity_source = "build/ici-cmake-build/CMakeFiles/demo.dir/Unity/unity_0_cxx.cxx"
    (root / unity_source).parent.mkdir(parents=True, exist_ok=True)
    (root / unity_source).write_text("int generated_unity;\n", encoding="utf-8")

    _install_fake_cmake(
        monkeypatch,
        root,
        on_configure=lambda _shadow, _argv: _write_database(
            root,
            _COMPILE_DB,
            [
                _row(
                    root,
                    unity_source,
                    directory=shadow,
                    output="CMakeFiles/demo.dir/Unity/unity_0_cxx.cxx.o",
                )
            ],
        ),
    )
    result = cmake_context.prepare_cmake_compilation_context(root, {}, _project(root))

    assert any("unity" in item.lower() for item in _diagnostic_text(result).split())


def test_stale_generated_source_builds_once_then_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    _write_cpp_sources(root, ("src/main.cpp",))
    generated = "build/ici-cmake-build/generated/moc_demo.cpp"
    loads: list[bool] = []

    def configure(shadow: Path, _argv: list[str]) -> None:
        _write_database(
            root,
            _COMPILE_DB,
            [_row(root, generated, directory=shadow, output="CMakeFiles/demo.dir/moc_demo.o")],
        )

    def build(shadow: Path) -> None:
        generated_path = root / generated
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_text("int moc_demo() { return 0; }\n", encoding="utf-8")
        assert shadow == root / "build" / "ici-cmake-build"

    calls = _install_fake_cmake(monkeypatch, root, on_configure=configure, on_build=build)
    real_loader = cmake_context.load_compilation_context

    def load(root_arg: Path, config: dict[str, object]) -> CompilationContext:
        context = real_loader(root_arg, config)
        loads.append(
            any(item.code == "stale-source" for unit in context.units for item in unit.diagnostics)
        )
        return context

    monkeypatch.setattr(cmake_context, "load_compilation_context", load)
    result = cmake_context.prepare_cmake_compilation_context(root, {}, _project(root))

    build_calls = [argv for argv, _cwd in calls if "--build" in argv]
    assert len(build_calls) == 1
    assert loads == [False, True, False]
    assert result.units and not result.units[0].diagnostics


def test_stale_ordinary_production_source_does_not_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("project(demo LANGUAGES CXX)\n", encoding="utf-8")
    missing_source = "src/missing.cpp"
    project = _project(root, sources=(missing_source,))

    def configure(shadow: Path, _argv: list[str]) -> None:
        _write_database(root, _COMPILE_DB, [_row(root, missing_source, directory=shadow)])

    calls = _install_fake_cmake(monkeypatch, root, on_configure=configure)
    result = cmake_context.prepare_cmake_compilation_context(root, {}, project)

    assert not [argv for argv, _cwd in calls if "--build" in argv]
    assert any(item.code == "stale-source" for item in result.units[0].diagnostics)


def test_verify_preflight_finishes_before_context_and_cache_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    project = _project(root, project_type="python", sources=())
    inventory = CapabilityInventory()
    context = AnalysisContext(
        project=project,
        capabilities=inventory,
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({}),
            toolchain_digest=canonical_digest([]),
        ),
    )
    events: list[str] = []

    def discover(_root: Path, _config: dict[str, object]) -> ProjectModel:
        events.append("discover")
        return project

    def preflight(
        _root: Path, _config: dict[str, object], _project: ProjectModel
    ) -> CompilationContext:
        events.append("preflight")
        return context.compilation

    def create_context(*_args: object, **_kwargs: object) -> AnalysisContext:
        events.append("context")
        return context

    class FakeCache:
        def __init__(self) -> None:
            events.append("cache-init")

        def load(self, *_args: object, **_kwargs: object) -> None:
            return None

        def store(self, *_args: object, **_kwargs: object) -> bool:
            return True

    class FakeLint:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self) -> EngineResult:
            return EngineResult("lint", EngineStatus.PASS, "ok")

    monkeypatch.setattr(verify_module, "discover_project_model", discover)
    monkeypatch.setattr(verify_module, "prepare_cmake_compilation_context", preflight)
    monkeypatch.setattr(verify_module, "create_analysis_context", create_context)
    monkeypatch.setattr(verify_module, "collect_capability_inventory", lambda **_kwargs: inventory)
    monkeypatch.setattr(verify_module, "evaluate_support_matrix", lambda *_a, **_k: SupportMatrix())
    monkeypatch.setattr(
        verify_module, "project_source_digest", lambda _project: "sha256:" + "a" * 64
    )
    monkeypatch.setattr(verify_module, "AnalysisCache", FakeCache)
    monkeypatch.setattr(verify_module, "LintEngine", FakeLint)
    monkeypatch.setattr(
        verify_module,
        "build_analysis_cache_key",
        lambda *_args, **_kwargs: (
            events.append("cache-key") or SimpleNamespace(digest="sha256:" + "b" * 64)
        ),
    )
    monkeypatch.setattr(verify_module, "print_suite_dashboard", lambda *_a, **_k: None)

    config = {"engines": {name: {"enabled": name == "lint"} for name in ENGINE_NAMES}}
    VerifyOrchestrator(root, config).run_all()

    assert events.index("preflight") < events.index("context") < events.index("cache-key")
