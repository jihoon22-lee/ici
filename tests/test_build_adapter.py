"""Tests for build adapters and the build_definition engine."""

from pathlib import Path

import pytest

from ici.build_adapters.base import ArtifactManifest, BuildAdapterError
from ici.build_adapters.cmake import CMakeAdapter
from ici.build_adapters.qmake import QMakeAdapter
from ici.build_adapters.registry import detect_build_system
from ici.core.models import EngineStatus
from ici.core.runner import ProcessResult
from ici.engines.build_definition import BuildDefinitionEngine


def test_detect_build_system_none(tmp_path: Path):
    assert detect_build_system(tmp_path) == ("none", None)


def test_detect_build_system_cmake(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8"
    )
    assert detect_build_system(tmp_path)[0] == "cmake"


def test_detect_build_system_qmake_single_pro(tmp_path: Path):
    (tmp_path / "app.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    name, pro = detect_build_system(tmp_path)
    assert (name, pro.name) == ("qmake", "app.pro")


def test_detect_both_build_systems_raises(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "app.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    with pytest.raises(BuildAdapterError, match="multiple build systems"):
        detect_build_system(tmp_path)


def test_manifest_rejects_path_outside_project(tmp_path: Path):
    manifest = ArtifactManifest(adapter="cmake", build_dir="../outside", steps=[])
    with pytest.raises(BuildAdapterError, match="outside project"):
        manifest.validate(tmp_path)


def _ok_result(argv=None, duration=0.01):
    return ProcessResult(returncode=0, stdout="", stderr="", duration=duration)


def test_cmake_adapter_argv_sequence(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "ici.build_adapters.cmake.run_process",
        lambda argv, cwd=None: calls.append((argv, cwd)) or _ok_result(),
    )
    request = type(
        "R",
        (),
        {
            "project_root": tmp_path,
            "build_dir": tmp_path / "build" / "ici" / "cmake",
            "jobs": 4,
            "run_ctest": False,
        },
    )()
    adapter = CMakeAdapter({"cmake": "/usr/bin/cmake"})
    outcome = adapter.run(request)

    assert outcome.ok is True
    assert calls[0][0][:3] == ["/usr/bin/cmake", "-S", str(tmp_path)]
    assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in calls[0][0]
    assert calls[1][0][:2] == ["/usr/bin/cmake", "--build"]
    assert "--parallel" in calls[1][0]


def test_qmake_adapter_uses_shadow_makefile(tmp_path: Path, monkeypatch):
    pro = tmp_path / "app.pro"
    pro.write_text("TEMPLATE = app\nTARGET = demo\n", encoding="utf-8")
    calls = []

    def fake_run(argv, cwd=None):
        # qmake runs in build dir; pretend it produced a Makefile there
        if argv[0] == "/usr/bin/qmake":
            cwd_obj = Path(cwd)
            (cwd_obj / "Makefile").write_text("all:\n", encoding="utf-8")
        calls.append((argv, cwd))
        return _ok_result()

    monkeypatch.setattr("ici.build_adapters.qmake.run_process", fake_run)
    build_dir = tmp_path / "build" / "ici" / "qmake"
    request = type("R", (), {"project_root": tmp_path, "build_dir": build_dir, "jobs": 2})()
    adapter = QMakeAdapter({"qmake": "/usr/bin/qmake", "make": "/usr/bin/make"}, pro)
    outcome = adapter.run(request)

    assert outcome.ok is True
    assert calls[0][0] == ["/usr/bin/qmake", "-o", "Makefile", str(pro)]
    assert calls[0][1] == build_dir
    assert calls[1][0][0] == "/usr/bin/make"


def test_engine_no_build_definition_passes(tmp_path: Path):
    result = BuildDefinitionEngine(tmp_path, {"engines": {"build_definition": {}}}).run()
    assert result.status == EngineStatus.PASS


def test_engine_cmake_success(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
    monkeypatch.setattr("ici.build_adapters.cmake.run_process", lambda argv, cwd=None: _ok_result())
    cfg = {
        "engines": {
            "build_definition": {
                "mode": "pass_warn",
                "required": False,
                "adapter": "auto",
                "jobs": 2,
            }
        }
    }
    result = BuildDefinitionEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.PASS
    assert result.tool_evidence


def test_engine_cmake_failure_warns_in_pass_warn_mode(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.build_adapters.cmake.run_process",
        lambda argv, cwd=None: ProcessResult(returncode=1, stdout="", stderr="boom", duration=0.01),
    )
    cfg = {"engines": {"build_definition": {"mode": "pass_warn", "required": False}}}
    result = BuildDefinitionEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.WARN


def test_engine_select_qmake_without_pro_warns(tmp_path: Path):
    cfg = {"engines": {"build_definition": {"adapter": "qmake"}}}
    result = BuildDefinitionEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.WARN
    assert "no .pro file" in result.summary


def test_engine_missing_cmake_tool_warns(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    cfg = {"engines": {"build_definition": {"adapter": "auto"}}}
    result = BuildDefinitionEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.WARN
