"""Tests for safe build artifact production and packaging."""

from pathlib import Path

import pytest

from ici.core.cmake import BACKEND_CMAKE, BuildSession
from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.engines.build import BuildEngine


def _write_project(
    root: Path,
    project_type: str,
    *,
    name: str = "sample",
    version: str = "1.0.0",
) -> None:
    (root / "ici.toml").write_text(
        f'name = "{name}"\ntype = "{project_type}"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def _target(root: Path, version: str = "v1.0.0") -> Path:
    return root / version / "x86_64"


def _successful_cpp_process(command: list[str], **_kwargs) -> ProcessResult:
    output = Path(command[command.index("-o") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"binary")
    return ProcessResult(0, "", "", 0.01)


def test_build_truncated_compile_output_is_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr(
        "ici.engines.build.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.build.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", "", 0.01, truncated=True),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert not (_target(tmp_path) / "env.sh").exists()


def test_empty_python_source_does_not_pass_with_only_environment_scripts(tmp_path):
    (tmp_path / "src").mkdir()
    _write_project(tmp_path, "python")

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    target = _target(tmp_path)
    assert not (target / "env.sh").exists()
    assert not (target / "env.csh").exists()


def test_python_packages_all_configured_sources_without_mutating_source_tree(tmp_path):
    src = tmp_path / "src" / "pkg"
    lib = tmp_path / "lib" / "other"
    src.mkdir(parents=True)
    lib.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (lib / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    config = {"project": {"source_dirs": ["src", "lib"]}}

    result = BuildEngine(tmp_path, config).run()

    assert result.status == EngineStatus.PASS
    target = _target(tmp_path)
    assert (target / "lib" / "pkg" / "core.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (target / "lib" / "other" / "helper.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert not list((tmp_path / "src").rglob("*.pyc"))
    assert not list((tmp_path / "lib").rglob("*.pyc"))
    assert (target / "env.sh").is_file()
    assert (target / "env.csh").is_file()


def test_python_destination_collision_is_structured_error_without_environment(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "src" / "same.py").write_text("SRC = 1\n", encoding="utf-8")
    (tmp_path / "lib" / "same.py").write_text("LIB = 1\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    config = {"project": {"source_dirs": ["src", "lib"]}}

    result = BuildEngine(tmp_path, config).run()

    assert result.status == EngineStatus.ERROR
    assert "collision" in result.summary.lower()
    assert not (_target(tmp_path) / "env.sh").exists()


def test_python_existing_destination_symlink_cannot_escape_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    destination = _target(tmp_path) / "lib"
    destination.mkdir(parents=True)
    outside = tmp_path.parent / "outside-artifact.py"
    outside.write_text("ORIGINAL\n", encoding="utf-8")
    (destination / "pkg.py").symlink_to(outside)

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert outside.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert not (_target(tmp_path) / "env.sh").exists()


def test_python_copy_failure_is_structured_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_project(tmp_path, "python")

    def fail_copy(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("ici.engines.build.shutil.copy2", fail_copy)

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert "copy" in result.summary.lower()
    assert not (_target(tmp_path) / "env.sh").exists()


def test_python_rebuild_reuses_regular_outputs_and_refreshes_content(tmp_path):
    module = tmp_path / "src" / "pkg"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    source = module / "cli.py"
    source.write_text("VALUE = 1\ndef main():\n    return 0\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    config = {"build": {"python": {"entrypoint": "pkg.cli:main"}}}

    first = BuildEngine(tmp_path, config).run()
    target = _target(tmp_path)
    launcher = target / "bin" / "sample"
    env_sh = target / "env.sh"
    assert first.status == EngineStatus.PASS
    launcher.write_text("stale launcher\n", encoding="utf-8")
    env_sh.write_text("stale environment\n", encoding="utf-8")
    source.write_text("VALUE = 2\ndef main():\n    return 0\n", encoding="utf-8")

    second = BuildEngine(tmp_path, config).run()

    assert second.status == EngineStatus.PASS
    assert (target / "lib" / "pkg" / "cli.py").read_text(encoding="utf-8").startswith("VALUE = 2")
    assert "stale launcher" not in launcher.read_text(encoding="utf-8")
    assert "stale environment" not in env_sh.read_text(encoding="utf-8")


def test_cpp_stale_regular_binary_is_removed_before_rc0_without_create(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    target_bin = _target(tmp_path) / "bin" / "sample"
    target_bin.parent.mkdir(parents=True)
    target_bin.write_bytes(b"stale")
    calls = []
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: "/usr/bin/g++")

    def no_create(command, **_kwargs):
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.build.run_process", no_create)

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert len(calls) == 1
    assert not target_bin.exists()
    assert not (_target(tmp_path) / "env.sh").exists()


@pytest.mark.parametrize("component", ["version", "bin", "lib", "env"])
def test_existing_output_path_symlink_is_rejected_without_escape(tmp_path, component):
    src = tmp_path / "src"
    src.mkdir()
    (src / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    target = _target(tmp_path)
    outside = tmp_path.parent / f"outside-{component}"
    outside.mkdir()
    if component == "version":
        (tmp_path / "v1.0.0").symlink_to(outside, target_is_directory=True)
    elif component == "bin":
        target.mkdir(parents=True)
        (target / "bin").symlink_to(outside, target_is_directory=True)
    elif component == "lib":
        (target / "bin").mkdir(parents=True)
        (target / "lib").symlink_to(outside, target_is_directory=True)
    else:
        (target / "bin").mkdir(parents=True)
        (target / "lib").mkdir(parents=True)
        (target / "env.sh").symlink_to(outside / "env.sh")

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert not list(outside.iterdir())


def test_cpp_source_inspection_error_is_error_without_gxx(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    unreadable = src / "unreadable.cpp"
    unreadable.write_text("int helper() { return 0; }\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    original_read_text = Path.read_text

    def fail_source_read(path, *args, **kwargs):
        if path == unreadable:
            raise UnicodeError("invalid source encoding")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_source_read)
    monkeypatch.setattr(
        "ici.engines.build.shutil.which",
        lambda _name: pytest.fail("g++ must not run when source inspection fails"),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_main_count_masks_comments_strings_and_raw_strings(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text(
        "// int main() { return 1; }\n"
        'const char *text = "int main() { return 2; }";\n'
        'const char *raw = u8R"tag(int main() { return 3; })tag";\n'
        "int main() { return 0; }\n",
        encoding="utf-8",
    )
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: "/usr/bin/g++")
    monkeypatch.setattr("ici.engines.build.run_process", _successful_cpp_process)

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS


def test_cpp_fake_main_only_in_comments_and_strings_needs_adapter(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "fake.cpp").write_text(
        "/* int main() { return 1; } */\n"
        'const char *text = "int main() { return 2; }";\n'
        'const char *raw = R"(int main() { return 3; })";\n',
        encoding="utf-8",
    )
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr(
        "ici.engines.build.shutil.which",
        lambda _name: pytest.fail("g++ must not run without a real main"),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_explicit_python_entrypoint_creates_callable_launcher(tmp_path):
    module = tmp_path / "src" / "pkg"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    config = {"build": {"python": {"entrypoint": "pkg.cli:main"}}}

    result = BuildEngine(tmp_path, config).run()

    assert result.status == EngineStatus.PASS
    launcher = _target(tmp_path) / "bin" / "sample"
    assert launcher.is_file()
    content = launcher.read_text(encoding="utf-8")
    assert "pkg.cli" in content and "main()" in content
    assert "python -m" not in content
    assert len(list((_target(tmp_path) / "bin").iterdir())) == 1


def test_python_library_without_entrypoint_does_not_choose_arbitrary_launcher(tmp_path):
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    _write_project(tmp_path, "python")

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert not list((_target(tmp_path) / "bin").iterdir())


def test_pyproject_scripts_create_all_safe_callable_launchers(tmp_path):
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (pkg / "tools.py").write_text("def run():\n    return 0\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "1.0.0"\n'
        '[project.scripts]\nfoo = "pkg.cli:main"\nbar-tool = "pkg.tools:run"\n',
        encoding="utf-8",
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    launchers = sorted(p.name for p in (_target(tmp_path) / "bin").iterdir())
    assert launchers == ["bar-tool", "foo"]


@pytest.mark.parametrize(
    "entrypoint",
    ["pkg.missing:main", "pkg.cli:not-valid-callable", "pkg.cli:main.extra"],
)
def test_invalid_or_missing_explicit_entrypoint_is_structured_error(tmp_path, entrypoint):
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    config = {"build": {"python": {"entrypoint": entrypoint}}}

    result = BuildEngine(tmp_path, config).run()

    assert result.status == EngineStatus.ERROR
    assert not (_target(tmp_path) / "env.sh").exists()


def test_unsafe_pyproject_script_name_is_structured_error(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    _write_project(tmp_path, "python")
    (tmp_path / "pyproject.toml").write_text(
        '[project.scripts]\n"../escape" = "cli:main"\n', encoding="utf-8"
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert not (_target(tmp_path) / "env.sh").exists()
    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize("descriptor", ["Makefile", "build.mk"])
def test_cpp_descriptor_requires_adapter_without_invoking_gxx(tmp_path, monkeypatch, descriptor):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / descriptor).write_text("# descriptor\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    calls = []
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda name: calls.append(name))
    monkeypatch.setattr(
        "ici.engines.build.run_process",
        lambda *_args, **_kwargs: pytest.fail("descriptor build must use an adapter"),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert calls == []
    assert result.evidence == EvidenceState.NOT_RUN


@pytest.mark.parametrize(
    "source_text",
    ["int helper() { return 1; }\n", "int main() { return 0; }\nint main() { return 1; }\n"],
)
def test_cpp_requires_exactly_one_conventional_main_without_gxx(tmp_path, monkeypatch, source_text):
    src = tmp_path / "src"
    src.mkdir()
    (src / "unit.cpp").write_text(source_text, encoding="utf-8")
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr(
        "ici.engines.build.shutil.which",
        lambda _name: pytest.fail("g++ must not run without exactly one main"),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_missing_gxx_is_structured_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: None)

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.tool_evidence and result.tool_evidence[0].error


def test_cpp_rc0_without_regular_binary_is_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: "/usr/bin/g++")
    monkeypatch.setattr(
        "ici.engines.build.run_process",
        lambda *_args, **_kwargs: ProcessResult(0, "", "", 0.01),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_exactly_one_main_with_helper_creates_regular_binary(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return helper(); }\n", encoding="utf-8")
    (src / "helper.cpp").write_text("int helper() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: "/usr/bin/g++")
    monkeypatch.setattr("ici.engines.build.run_process", _successful_cpp_process)

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    binary = _target(tmp_path) / "bin" / "sample"
    assert binary.is_file() and not binary.is_symlink()
    assert any(target.target_name == "CppBinary" for target in result.targets)
    assert result.tool_evidence[0].argv
    assert (_target(tmp_path) / "env.sh").is_file()


def test_cpp_spawn_signal_timeout_and_truncation_are_errors(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: "/usr/bin/g++")

    cases = [
        OSError("spawn failed"),
        ProcessResult(-9, "", "", 0.01),
        ProcessResult(124, "", "", 0.01, timed_out=True),
        ProcessResult(0, "", "", 0.01, truncated=True),
    ]
    for case in cases:
        monkeypatch.setattr(
            "ici.engines.build.run_process",
            lambda *_args, case=case, **_kwargs: (
                (_ for _ in ()).throw(case) if isinstance(case, Exception) else case
            ),
        )
        result = BuildEngine(tmp_path).run()
        assert result.status == EngineStatus.ERROR
        assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_compile_exit_one_is_fail(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: "/usr/bin/g++")
    monkeypatch.setattr(
        "ici.engines.build.run_process",
        lambda *_args, **_kwargs: ProcessResult(1, "", "compile error", 0.01),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert "compile error" in result.tool_evidence[0].error
    assert not (_target(tmp_path) / "env.sh").exists()


def test_hybrid_requires_python_and_cpp_success(tmp_path, monkeypatch):
    py = tmp_path / "src" / "pkg.py"
    py.parent.mkdir(parents=True)
    py.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    _write_project(tmp_path, "hybrid")
    monkeypatch.setattr("ici.engines.build.shutil.which", lambda _name: "/usr/bin/g++")
    monkeypatch.setattr(
        "ici.engines.build.run_process",
        lambda *_args, **_kwargs: ProcessResult(1, "", "compile error", 0.01),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert not (_target(tmp_path) / "env.sh").exists()


def test_unsafe_project_metadata_is_engine_error_without_escape(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_project(tmp_path, "python", name="../escape")

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert not (tmp_path.parent / "escape").exists()


def test_dot_source_root_excludes_current_target_on_rebuild(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_project(tmp_path, "python", version="2.0.0")
    config = {"project": {"source_dirs": ["."]}}

    first = BuildEngine(tmp_path, config).run()
    target = _target(tmp_path, "v2.0.0")
    first_artifacts = [
        (item.target_name, item.file_path, item.status)
        for item in first.targets
        if item.target_name in {"PythonLibrary", "PythonLauncher", "CppBinary"}
    ]

    second = BuildEngine(tmp_path, config).run()
    second_artifacts = [
        (item.target_name, item.file_path, item.status)
        for item in second.targets
        if item.target_name in {"PythonLibrary", "PythonLauncher", "CppBinary"}
    ]

    assert first.status == EngineStatus.PASS
    assert second.status == EngineStatus.PASS
    assert second_artifacts == first_artifacts
    assert sorted(path.relative_to(target / "lib") for path in (target / "lib").rglob("*.py")) == [
        Path("src/pkg.py")
    ]


def test_cmake_project_is_built_through_the_adapter(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")

    shadow = tmp_path / "build" / "ici-cmake"

    def _fake_configure(root):
        shadow.mkdir(parents=True, exist_ok=True)
        return BuildSession(
            root=root,
            shadow=shadow,
            backend=BACKEND_CMAKE,
            descriptor="CMakeLists.txt",
            reason="CMakeLists.txt at the project root selected the CMake backend",
            configured=True,
        )

    def _fake_build(session):
        binary = session.shadow / "app"
        binary.write_bytes(b"\x7fELF")
        binary.chmod(0o755)
        return True

    monkeypatch.setattr("ici.engines.build.adapter_configure", _fake_configure)
    monkeypatch.setattr("ici.engines.build.adapter_build", _fake_build)

    result = BuildEngine(tmp_path).run()

    # Before this change the engine refused outright with
    # "C++ build descriptor requires an adapter".
    assert result.status is not EngineStatus.ERROR
    assert "requires an adapter" not in result.summary


def test_makefile_only_project_still_refuses_with_a_precise_reason(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")

    result = BuildEngine(tmp_path).run()

    assert result.status is EngineStatus.ERROR
    # The old message predates the adapters existing at all. Now that CMake and
    # qmake are handled, it has to say which adapter is missing.
    messages = " ".join(t.message for t in result.targets)
    assert "Makefile" in messages
    assert "CMake and qmake" in messages


@pytest.mark.parametrize("descriptor", ["CMakeLists.txt", "project.pro"])
def test_adapter_path_never_falls_back_to_gxx(tmp_path, monkeypatch, descriptor):
    """The adapter replaces the generic g++ link; it must not run alongside it."""

    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / descriptor).write_text("# descriptor\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")

    monkeypatch.setattr(
        "ici.engines.build.run_process",
        lambda *_args, **_kwargs: pytest.fail("the adapter path must not invoke g++"),
    )
    monkeypatch.setattr(
        "ici.engines.build.adapter_configure",
        lambda root: BuildSession(
            root=root,
            shadow=root / "build" / "ici-cmake",
            backend=BACKEND_CMAKE,
            descriptor=descriptor,
            reason="selected for this test",
        ),
    )

    result = BuildEngine(tmp_path).run()

    # configure() reported nothing configured, so this is an unmeasured build,
    # not an inapplicable one.
    assert result.status == EngineStatus.ERROR
