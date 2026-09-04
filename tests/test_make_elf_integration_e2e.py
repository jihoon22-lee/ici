"""Real-tool acceptance for Make -> manifest -> ELF -> process contracts."""

from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from ici.config import DEFAULT_CONFIG
from ici.core.models import EngineStatus, EvidenceState
from ici.core.pipeline import AnalysisProfile
from ici.engines import verify as verify_module
from ici.engines.verify import VerifyOrchestrator


def _required_tools() -> tuple[str, str, str]:
    make = shutil.which("make")
    compiler = shutil.which("c++") or shutil.which("g++")
    readelf = shutil.which("readelf")
    if not make or not compiler or not readelf:
        pytest.skip("Make, a C++ compiler, and readelf are required for this acceptance test")
    return make, compiler, readelf


def _write_project(root: Path) -> None:
    source = root / "src"
    source.mkdir()
    (source / "main.cpp").write_text(
        """#include <iostream>
#include <string>

int main(int argc, char **argv) {
    if (argc == 2 && std::string(argv[1]) == "--self-test") {
        std::cout << "native-contract-ok\\n";
        return 0;
    }
    return 2;
}
""",
        encoding="utf-8",
    )
    (root / "Makefile").write_text(
        """OUT ?= build/ici-make

.PHONY: build clean
build:
	mkdir -p $(OUT)
	$(CXX) -std=c++17 src/main.cpp -o $(OUT)/app

clean:
	rm -f $(OUT)/app
""",
        encoding="utf-8",
    )


def _config(compiler: str) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    config["name"] = "make-elf-contract"
    config["version"] = "0.1.0"
    config["type"] = "cpp"
    config["ici"]["profile"] = "deep"
    config["project"]["source_dirs"] = ["src"]
    for engine in config["engines"].values():
        engine["enabled"] = False
    for name in ("build", "binary_compat", "integration"):
        config["engines"][name]["enabled"] = True
        config["engines"][name]["required"] = True
    config["build"]["make"].update(
        {
            "enabled": True,
            "build_argv": ["make", f"CXX={compiler}", "OUT=build/ici-make", "build"],
            "clean_argv": ["make", "OUT=build/ici-make", "clean"],
        }
    )
    config["engines"]["integration"]["cases"] = [
        {
            "name": "native-self-test",
            "argv": ["{artifact:app}", "--self-test"],
            "stdout_contains": ["native-contract-ok"],
        }
    ]
    return config


def test_real_make_artifact_is_inspected_then_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make, compiler, _readelf = _required_tools()
    _write_project(tmp_path)
    monkeypatch.setattr(verify_module, "print_suite_dashboard", lambda *_args, **_kwargs: None)

    suite = VerifyOrchestrator(tmp_path, _config(compiler)).run_all(
        profile=AnalysisProfile.DEEP,
        use_cache=False,
    )

    assert [result.engine_name for result in suite.results] == [
        "build",
        "binary_compat",
        "integration",
    ]
    assert all(result.status is EngineStatus.PASS for result in suite.results), [
        (result.engine_name, result.status.value, result.summary) for result in suite.results
    ]
    assert all(result.evidence is EvidenceState.MEASURED for result in suite.results)
    build, binary, integration = suite.results
    assert len(build.artifact_manifests) == 1
    assert build.artifact_manifests[0].artifacts[0].path == "app"
    assert binary.extra["elf"]["artifacts_checked"] == 1
    assert integration.extra["integration"]["cases"][0]["status"] == "PASS"
    assert suite.analysis_context is not None
    assert suite.analysis_context.manifests == build.artifact_manifests
