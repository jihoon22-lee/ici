"""Focused contract tests for the shared tool capability probes."""

import json
from pathlib import Path

import pytest

from ici.core import toolchain
from ici.core.runner import ProcessResult


def _result(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    truncated: bool = False,
) -> ProcessResult:
    """Build deterministic subprocess evidence without invoking a process."""

    return ProcessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=0.01,
        timed_out=timed_out,
        truncated=truncated,
    )


@pytest.mark.parametrize(
    ("name", "stdout", "stderr", "expected_line", "expected_version"),
    [
        pytest.param(
            "gcc",
            "gcc: warning: target note 2024.1\n",
            "gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n",
            "gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0",
            (11, 4, 0),
            id="gcc-ubuntu-multiline-stderr",
        ),
        pytest.param(
            "clang",
            "Ubuntu clang version 14.0.6-12\nSelected GCC installation: /usr/bin/gcc-11\n",
            "",
            "Ubuntu clang version 14.0.6-12",
            (14, 0, 6),
            id="ubuntu-clang",
        ),
        pytest.param(
            "clang",
            "",
            "Apple clang version 15.0.0 (clang-1500.0.40.1)\n",
            "Apple clang version 15.0.0 (clang-1500.0.40.1)",
            (15, 0, 0),
            id="apple-clang-stderr",
        ),
        pytest.param(
            "python3",
            "Python 3.12.3 (main, Jun 1 2024, 12:00:00)\n",
            "[GCC 13.2.0]\n",
            "Python 3.12.3 (main, Jun 1 2024, 12:00:00)",
            (3, 12, 3),
            id="python-vv",
        ),
        pytest.param(
            "clazy",
            "Ubuntu LLVM version 21.1.8\nclazy version 1.17\n",
            "",
            "clazy version 1.17",
            (1, 17),
            id="clazy-standalone-multiline",
        ),
        pytest.param(
            "cmake",
            "cmake version 3.30.2\n\nCMake suite maintained and supported by Kitware\n",
            "",
            "cmake version 3.30.2",
            (3, 30, 2),
            id="cmake",
        ),
        pytest.param(
            "qmake",
            "QMake version 3.1\nUsing Qt version 6.7.2 in /opt/Qt\n",
            "",
            "QMake version 3.1",
            (3, 1),
            id="qmake",
        ),
        pytest.param(
            "make",
            "GNU Make 4.4.1\nBuilt for x86_64-pc-linux-gnu\n",
            "",
            "GNU Make 4.4.1",
            (4, 4, 1),
            id="gnu-make",
        ),
        pytest.param(
            "ninja",
            "1.11.1\n",
            "",
            "1.11.1",
            (1, 11, 1),
            id="plain-ninja",
        ),
        pytest.param(
            "readelf",
            "GNU readelf (GNU Binutils for Debian) 2.40\n",
            "",
            "GNU readelf (GNU Binutils for Debian) 2.40",
            (2, 40),
            id="binutils",
        ),
    ],
)
def test_parse_tool_version_handles_vendor_multiline_and_both_streams(
    name, stdout, stderr, expected_line, expected_version
):
    line, version = toolchain.parse_tool_version(name, stdout, stderr)

    assert line == expected_line
    assert version == expected_version


@pytest.mark.parametrize(
    "target",
    [
        "x86_64-linux-gnu",
        "x86_64-pc-linux-gnu",
        "armv8.6-a-none-eabi",
    ],
)
def test_parse_tool_version_does_not_treat_a_target_triple_as_a_version(target):
    assert toolchain.parse_tool_version("gcc", target) == ("", ())
    assert toolchain.parse_tool_version("ninja", target) == ("", ())


def test_parse_qmake_query_extracts_qt_version_spec_prefix_and_features():
    details = toolchain.parse_qmake_query(
        "\n".join(
            [
                "QT_VERSION: 6.7.2",
                "QMAKE_XSPEC: linux-clang",
                "QMAKE_SPEC: fallback-spec",
                "QT_INSTALL_PREFIX: /opt/Qt/6.7",
                "QT_CONFIG:  c++11   c++14\topengl   ssl  ",
                "QMAKE_MKSPEC: ignored",
            ]
        )
    )

    assert details == {
        "qt_version": "6.7.2",
        "qt_major": "6",
        "generator": "linux-clang",
        "qt_prefix": "/opt/Qt/6.7",
        "features": "c++11 c++14 opengl ssl",
    }


def test_parse_qmake_query_redacts_credentials_in_metadata_values():
    details = toolchain.parse_qmake_query(
        "\n".join(
            [
                "QT_VERSION: 5.15.18",
                "QMAKE_XSPEC: linux-g++",
                "QT_INSTALL_PREFIX: /opt/qt/password=super-secret",
                "QT_CONFIG: ssl api_key=abc123 token=ghp_12345678901234567890",
            ]
        )
    )

    rendered = repr(details)
    assert "super-secret" not in rendered
    assert "abc123" not in rendered
    assert "ghp_12345678901234567890" not in rendered
    assert "***REDACTED***" in rendered
    assert details["qt_prefix"].endswith("password=***REDACTED***")
    assert details["features"] == "ssl api_key=***REDACTED*** token=***REDACTED***"


def test_parse_cmake_capabilities_is_sorted_deduplicated_and_filters_bad_entries():
    long_name = "A" * 250
    payload = {
        "generators": [
            {"name": "Unix Makefiles"},
            {"name": "Ninja"},
            {"name": "Ninja"},
            {"name": long_name},
            {"name": "token=cmake-secret"},
            {"not_name": "ignored"},
            "ignored scalar",
            None,
        ],
        "serverMode": True,
    }

    details = toolchain.parse_cmake_capabilities(json.dumps(payload))
    expected_names = sorted(
        {
            "Unix Makefiles",
            "Ninja",
            long_name[:200],
            "token=***REDACTED***",
        }
    )

    assert details["generators"] == ", ".join(expected_names)
    assert details["server_mode"] == "true"
    assert "cmake-secret" not in details["generators"]


def test_parse_cmake_capabilities_bounds_generator_count_and_text():
    payload = {
        "generators": [{"name": f"{index:03d}-{'x' * 150}"} for index in range(120)],
        "serverMode": False,
    }

    details = toolchain.parse_cmake_capabilities(json.dumps(payload))
    rendered_names = details["generators"].split(", ")

    assert len(rendered_names) <= 100
    assert len(details["generators"]) <= 4_000
    assert all(len(name) <= 200 for name in rendered_names)
    assert details["server_mode"] == "false"


@pytest.mark.parametrize("text", ["", "{", "[]", '"not an object"', None])
def test_parse_cmake_capabilities_returns_empty_details_for_malformed_input(text):
    assert toolchain.parse_cmake_capabilities(text) == {}


def test_collect_tool_capability_missing_executable_does_not_spawn(monkeypatch):
    calls = []

    def fake_which(command):
        calls.append(command)
        return None

    def fail_run(*args, **kwargs):
        pytest.fail(f"run_process must not be called: {args!r} {kwargs!r}")

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fail_run)

    capability, result = toolchain.collect_tool_capability("missing", ["missing", "--version"])

    assert calls == ["missing"]
    assert result is None
    assert capability.name == "missing"
    assert capability.path == ""
    assert capability.available is False
    assert capability.complete is False
    assert capability.evidence == ()


@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        pytest.param(_result(returncode=7, stderr="broken"), "probe exited 7", id="nonzero"),
        pytest.param(
            _result(returncode=124, timed_out=True),
            "probe timed out",
            id="timeout",
        ),
        pytest.param(
            _result(stdout="ninja 1.11.1", truncated=True),
            "probe output truncated",
            id="truncated",
        ),
    ],
)
def test_collect_tool_capability_execution_failures_are_unavailable(
    monkeypatch, result, expected_error
):
    path = "/opt/tools/probe"
    calls = []

    def fake_which(command):
        return path if command == "probe" else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        calls.append((tuple(argv), cwd, timeout, max_output_chars))
        return result

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, returned = toolchain.collect_tool_capability("probe", ["probe", "--version"])

    assert returned is result
    assert calls == [((path, "--version"), None, toolchain.PROBE_TIMEOUT_SECONDS, 65_536)]
    assert capability.path == path
    assert capability.available is False
    assert capability.complete is False
    assert capability.error == expected_error
    assert capability.returncode == result.returncode
    assert capability.timed_out is result.timed_out
    assert capability.truncated is result.truncated
    assert len(capability.evidence) == 1
    assert capability.evidence[0].purpose == "version"


def test_collect_tool_capability_forwards_64k_limit_and_preserves_unparseable_availability(
    monkeypatch, tmp_path: Path
):
    path = "/opt/tools/mystery"
    calls = []
    result = _result(stdout="release channel stable\n")

    def fake_which(command):
        return path if command == "mystery" else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        calls.append((tuple(argv), cwd, timeout, max_output_chars))
        return result

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, returned = toolchain.collect_tool_capability(
        "mystery", ["mystery", "--version"], cwd=tmp_path, timeout=1.25
    )

    assert returned is result
    assert calls == [((path, "--version"), tmp_path, 1.25, 65_536)]
    assert capability.available is True
    assert capability.complete is False
    assert capability.version == ""
    assert capability.version_tuple == ()
    assert capability.error == "probe did not report a parseable version"


def test_collect_tool_capability_redacts_secret_argv_evidence_and_freezes_details(monkeypatch):
    path = "/opt/tools/ninja"
    secret = "super-secret-token"
    result = _result(stdout="1.11.1\n")

    def fake_which(command):
        return path if command == "ninja" else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        return result

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, _returned = toolchain.collect_tool_capability(
        "ninja",
        [
            "ninja",
            "--token",
            secret,
            "--api-key=inline-secret",
            "--password",
            "password-value",
        ],
    )

    expected_argv = (
        path,
        "--token",
        "***REDACTED***",
        "--api-key=***REDACTED***",
        "--password",
        "***REDACTED***",
    )
    assert capability.probe_argv == expected_argv
    assert capability.evidence[0].argv == expected_argv
    assert secret not in repr(capability)
    with pytest.raises(TypeError):
        capability.details["new"] = "value"


@pytest.mark.parametrize(
    ("which_values", "expected_alias", "expected_path"),
    [
        pytest.param(
            {
                "qmake6": "/opt/qt6/bin/qmake6",
                "qmake": "/opt/qt5/bin/qmake",
                "/opt/qt6/bin/qmake6": "/opt/qt6/bin/qmake6",
            },
            "qmake6",
            "/opt/qt6/bin/qmake6",
            id="qmake6-priority",
        ),
        pytest.param(
            {
                "qmake6": None,
                "qmake": "/opt/qt5/bin/qmake",
                "/opt/qt5/bin/qmake": "/opt/qt5/bin/qmake",
            },
            "qmake",
            "/opt/qt5/bin/qmake",
            id="qmake-fallback",
        ),
    ],
)
def test_collect_registered_capability_resolves_qmake_priority_and_fallback(
    monkeypatch, which_values, expected_alias, expected_path
):
    which_calls = []
    run_calls = []
    process_results = [
        _result(stdout="QMake version 3.1\n"),
        _result(
            stdout="\n".join(
                [
                    "QT_VERSION: 6.7.2",
                    "QMAKE_XSPEC: linux-clang",
                    "QT_INSTALL_PREFIX: /opt/Qt/6.7",
                    "QT_CONFIG: c++11   ssl",
                ]
            )
        ),
    ]
    probe = toolchain.ToolProbe(
        "qmake",
        ("qmake6", "qmake"),
        ("-v",),
        "qmake-query",
        ("-query",),
    )

    def fake_which(command):
        which_calls.append(command)
        return which_values.get(command)

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        run_calls.append((tuple(argv), cwd, timeout, max_output_chars))
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    expected_which_calls = ["qmake6"]
    if expected_alias == "qmake":
        expected_which_calls.append("qmake")
    expected_which_calls.append(expected_path)
    assert which_calls == expected_which_calls
    assert [call[0] for call in run_calls] == [
        (expected_path, "-v"),
        (expected_path, "-query"),
    ]
    assert all(call[2:] == (toolchain.PROBE_TIMEOUT_SECONDS, 65_536) for call in run_calls)
    assert len(results) == 2
    assert capability.available is True
    assert capability.complete is True
    assert capability.path == expected_path
    assert capability.details == {
        "resolved_alias": expected_alias,
        "qt_version": "6.7.2",
        "qt_major": "6",
        "generator": "linux-clang",
        "qt_prefix": "/opt/Qt/6.7",
        "features": "c++11 ssl",
    }
    assert [e.purpose for e in capability.evidence] == ["version", "qmake-query"]


def test_collect_registered_capability_records_compiler_target(monkeypatch):
    path = "/usr/bin/gcc"
    probe = next(item for item in toolchain.DEFAULT_TOOL_PROBES if item.name == "gcc")
    process_results = [
        _result(stdout="gcc (Ubuntu 11.4.0-1ubuntu1) 11.4.0\n"),
        _result(stdout="x86_64-linux-gnu\n"),
    ]
    run_calls = []

    def fake_which(command):
        return path if command in {"gcc", path} else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        run_calls.append(tuple(argv))
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert run_calls == [
        (path, "--version"),
        (path, "-dumpmachine"),
    ]
    assert len(results) == 2
    assert capability.available is True
    assert capability.complete is True
    assert capability.version_tuple == (11, 4, 0)
    assert capability.details["vendor"] == "Ubuntu"
    assert capability.details["compiler_family"] == "gcc"
    assert capability.details["target_triple"] == "x86_64-linux-gnu"
    assert capability.details["resolved_alias"] == "gcc"


def test_gxx_registry_probe_identifies_clang_behind_the_only_available_alias(monkeypatch):
    path = "/opt/toolchain/c++"
    probe = next(item for item in toolchain.DEFAULT_TOOL_PROBES if item.name == "g++")
    process_results = [
        _result(stdout="Apple clang version 18.1.0\n"),
        _result(stdout="arm64-apple-darwin24.0.0\n"),
    ]
    run_calls = []

    def fake_which(command):
        return path if command in {"g++", path} else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        run_calls.append(tuple(argv))
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert run_calls == [(path, "--version"), (path, "-dumpmachine")]
    assert len(results) == 2
    assert capability.available is True
    assert capability.complete is True
    assert capability.version == "Apple clang version 18.1.0"
    assert capability.version_tuple == (18, 1, 0)
    assert capability.details["compiler_family"] == "clang"
    assert capability.details["resolved_alias"] == "g++"


def test_compiler_registry_probe_rejects_unidentified_family(monkeypatch):
    path = "/opt/toolchain/g++"
    probe = next(item for item in toolchain.DEFAULT_TOOL_PROBES if item.name == "g++")
    process_results = [
        _result(stdout="Acme C++ driver 1.2.3\n"),
        _result(stdout="x86_64-linux-gnu\n"),
    ]

    def fake_which(command):
        return path if command in {"g++", path} else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert len(results) == 2
    assert capability.available is True
    assert capability.complete is False
    assert capability.version_tuple == (1, 2, 3)
    assert "compiler_family" not in capability.details
    assert capability.error == "probe did not identify a supported compiler family"


def test_collect_registered_capability_metadata_failure_keeps_tool_available_but_incomplete(
    monkeypatch,
):
    path = "/opt/qt/bin/qmake"
    probe = toolchain.ToolProbe(
        "qmake",
        ("qmake",),
        ("-v",),
        "qmake-query",
        ("-query",),
    )
    process_results = [
        _result(stdout="QMake version 3.1\n"),
        _result(returncode=42, stderr="query failed"),
    ]

    def fake_which(command):
        return path if command in {"qmake", path} else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert len(results) == 2
    assert capability.available is True
    assert capability.complete is False
    assert capability.error == "metadata probe exited 42"
    assert capability.details["resolved_alias"] == "qmake"
    assert [e.returncode for e in capability.evidence] == [0, 42]
    assert capability.evidence[1].purpose == "qmake-query"


def test_collect_registered_capability_rejects_malformed_target_metadata(monkeypatch):
    path = "/usr/bin/cc"
    probe = toolchain.ToolProbe("cc", ("cc",), ("--version",), "target", ("-dumpmachine",))
    process_results = [
        _result(stdout="cc 12.2.0\n"),
        _result(stdout="not-a-triple\n"),
    ]

    def fake_which(command):
        return path if command in {"cc", path} else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, _results = toolchain.collect_registered_capability(probe)

    assert capability.available is True
    assert capability.complete is False
    assert capability.error == "metadata probe returned an invalid target triple"
    assert "target_triple" not in capability.details


def test_collect_registered_capability_records_cmake_generators_and_server_mode(monkeypatch):
    path = "/usr/bin/cmake"
    probe = next(item for item in toolchain.DEFAULT_TOOL_PROBES if item.name == "cmake")
    process_results = [
        _result(stdout="cmake version 3.30.2\n"),
        _result(
            stdout=json.dumps(
                {
                    "generators": [
                        {"name": "Unix Makefiles"},
                        {"name": "Ninja"},
                        {"name": "Ninja"},
                    ],
                    "serverMode": True,
                }
            )
        ),
    ]
    run_calls = []

    def fake_which(command):
        return path if command in {"cmake", path} else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        run_calls.append(tuple(argv))
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert run_calls == [(path, "--version"), (path, "-E", "capabilities")]
    assert len(results) == 2
    assert capability.available is True
    assert capability.complete is True
    assert capability.version_tuple == (3, 30, 2)
    assert capability.details["generators"] == "Ninja, Unix Makefiles"
    assert capability.details["server_mode"] == "true"
    assert capability.details["resolved_alias"] == "cmake"
    assert [e.purpose for e in capability.evidence] == ["version", "cmake-capabilities"]


def test_default_tool_probe_registry_is_deterministic_and_covers_requested_tools():
    expected_names = (
        "gcc",
        "g++",
        "clang",
        "clang++",
        "clang-format",
        "clang-tidy",
        "clazy",
        "clangd",
        "clang-check",
        "cmake",
        "ctest",
        "qmake",
        "make",
        "ninja",
        "gcov",
        "readelf",
        "addr2line",
        "objdump",
        "nm",
        "ld",
        "ar",
        "strip",
        "pkg-config",
        "qt5",
        "qt6",
        "git",
        "ruff",
        "mypy",
        "pytest",
        "coverage",
        "uv",
        "python3",
    )
    names = tuple(probe.name for probe in toolchain.DEFAULT_TOOL_PROBES)
    requested = {
        "python3",
        "gcc",
        "g++",
        "clang",
        "clang++",
        "clang-format",
        "clang-tidy",
        "clazy",
        "clangd",
        "clang-check",
        "cmake",
        "qmake",
        "make",
        "ninja",
        "gcov",
        "readelf",
        "addr2line",
        "objdump",
        "nm",
        "ld",
        "ar",
        "strip",
        "qt5",
        "qt6",
    }

    assert names == expected_names
    assert names == tuple(probe.name for probe in toolchain.DEFAULT_TOOL_PROBES)
    assert len(names) == len(set(names))
    assert requested.issubset(names)


def test_clazy_probe_prefers_standalone_candidate_and_preserves_alias(monkeypatch):
    path = "/opt/llvm/bin/clazy-standalone"
    probe = next(item for item in toolchain.DEFAULT_TOOL_PROBES if item.name == "clazy")
    which_calls = []
    run_calls = []

    def fake_which(command):
        which_calls.append(command)
        return path if command in {"clazy-standalone", path} else None

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        del cwd, timeout, max_output_chars
        run_calls.append(tuple(argv))
        return _result(stdout="Ubuntu LLVM version 21.1.8\nclazy version 1.17\n")

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert which_calls == ["clazy-standalone", path]
    assert run_calls == [(path, "--version")]
    assert len(results) == 1
    assert capability.available is True
    assert capability.complete is True
    assert capability.version_tuple == (1, 17)
    assert capability.details["resolved_alias"] == "clazy-standalone"


@pytest.mark.parametrize(
    "standalone_result",
    [
        pytest.param(_result(returncode=127, stderr="broken"), id="execution-failure"),
        pytest.param(_result(stdout="Ubuntu LLVM version 21.1.8\n"), id="version-parse-failure"),
    ],
)
def test_clazy_probe_falls_back_to_wrapper_after_standalone_version_failure(
    monkeypatch, standalone_result
):
    standalone = "/opt/llvm/bin/clazy-standalone"
    wrapper = "/usr/bin/clazy"
    probe = next(item for item in toolchain.DEFAULT_TOOL_PROBES if item.name == "clazy")
    which_calls = []
    run_calls = []
    wrapper_result = _result(stdout="clazy version: 1.17\n")
    process_results = [standalone_result, wrapper_result]
    available = {
        "clazy-standalone": standalone,
        standalone: standalone,
        "clazy": wrapper,
        wrapper: wrapper,
    }

    def fake_which(command):
        which_calls.append(command)
        return available.get(command)

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        del cwd, timeout, max_output_chars
        run_calls.append(tuple(argv))
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert which_calls == ["clazy-standalone", standalone, "clazy", wrapper]
    assert run_calls == [(standalone, "--version"), (wrapper, "--version")]
    assert results == (standalone_result, wrapper_result)
    assert capability.available is True
    assert capability.complete is True
    assert capability.path == wrapper
    assert capability.version_tuple == (1, 17)
    assert capability.details["resolved_alias"] == "clazy"
    assert capability.probe_argv == (wrapper, "--version")


def test_collect_registered_capability_preserves_first_failed_candidate(monkeypatch):
    first = "/opt/tools/first"
    second = "/opt/tools/second"
    probe = toolchain.ToolProbe("fallback", ("first", "second"), ("--version",))
    process_results = [
        _result(returncode=17, stderr="first candidate failed"),
        _result(stdout="unparseable release output\n"),
    ]
    available = {
        "first": first,
        first: first,
        "second": second,
        second: second,
    }

    def fake_which(command):
        return available.get(command)

    def fake_run(argv, *, cwd, timeout, max_output_chars):
        del cwd, timeout, max_output_chars
        return process_results.pop(0)

    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fake_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert results == (
        _result(returncode=17, stderr="first candidate failed"),
        _result(stdout="unparseable release output\n"),
    )
    assert capability.path == first
    assert capability.available is False
    assert capability.complete is False
    assert capability.error == "probe exited 17"
    assert capability.details["resolved_alias"] == "first"
    assert capability.probe_argv == (first, "--version")


def test_clazy_version_parser_does_not_fall_back_to_llvm_version():
    assert toolchain.parse_tool_version("clazy", "Ubuntu LLVM version 21.1.8\n") == ("", ())


def test_clazy_wrapper_version_parser_accepts_packaged_colon_format():
    assert toolchain.parse_tool_version(
        "clazy",
        "clazy version: 1.17\nclang version: 21.1.8\n",
    ) == ("clazy version: 1.17", (1, 17))


def test_collect_registered_capability_missing_candidates_returns_no_process_result(monkeypatch):
    calls = []

    def fake_which(command):
        calls.append(command)
        return None

    def fail_run(*args, **kwargs):
        pytest.fail(f"run_process must not be called: {args!r} {kwargs!r}")

    probe = toolchain.ToolProbe("optional", ("first", "second"), ("--version",))
    monkeypatch.setattr(toolchain.shutil, "which", fake_which)
    monkeypatch.setattr(toolchain, "run_process", fail_run)

    capability, results = toolchain.collect_registered_capability(probe)

    assert calls == ["first", "second"]
    assert results == ()
    assert capability.available is False
    assert capability.complete is False
    assert capability.path == ""


def test_python_module_probe_uses_the_selected_interpreter(monkeypatch):
    interpreter = "/isolated/python"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(toolchain.sys, "executable", interpreter)
    monkeypatch.setattr(
        toolchain.shutil,
        "which",
        lambda command: interpreter if command == interpreter else None,
    )

    def fake_run(argv, cwd=None, timeout=0, max_output_chars=0):
        del cwd, timeout, max_output_chars
        calls.append(tuple(argv))
        return _result(stdout="pytest 9.1.1\n")

    monkeypatch.setattr(toolchain, "run_process", fake_run)
    probe = toolchain.ToolProbe(
        "pytest",
        ("pytest",),
        ("--version",),
        python_module="pytest",
    )

    capability, results = toolchain.collect_registered_capability(probe)

    assert calls == [(interpreter, "-m", "pytest", "--version")]
    assert len(results) == 1
    assert capability.available is True
    assert capability.complete is True
    assert capability.path == interpreter
    assert capability.version_tuple == (9, 1, 1)
    assert capability.details["provider"] == "python-module"
    assert capability.details["module"] == "pytest"
    assert capability.probe_argv == calls[0]


def test_python_module_probe_rejects_unsafe_module_name():
    with pytest.raises(ValueError, match="invalid Python module probe"):
        toolchain.ToolProbe("bad", (), ("--version",), python_module="pytest;echo")
