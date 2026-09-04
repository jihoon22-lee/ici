"""Focused contracts for build-variant selection and engine boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.cmake import BACKEND_CMAKE, BuildSession, ConfigureOptions, configure
from ici.core.context import BuildVariant
from ici.engines.build import BuildEngine
from ici.engines.sanitize import SanitizeEngine
from ici.engines.test import TestEngine


def test_configure_options_requires_an_explicit_variant() -> None:
    with pytest.raises(TypeError):
        ConfigureOptions()

    # The former boolean API must not remain as a silently accepted alias.
    with pytest.raises(TypeError):
        ConfigureOptions(coverage=False)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("variant", "coverage", "suffix", "cxx_flags", "link_flags"),
    (
        (BuildVariant.RELEASE, False, "-build", [], []),
        (BuildVariant.COVERAGE, True, "", ["--coverage"], ["--coverage"]),
        (
            BuildVariant.SANITIZE,
            False,
            "-asan",
            ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"],
            ["-fsanitize=address,undefined"],
        ),
        (
            BuildVariant.THREAD_SANITIZE,
            False,
            "-tsan",
            ["-fsanitize=thread", "-fno-omit-frame-pointer", "-g"],
            ["-fsanitize=thread"],
        ),
    ),
)
def test_variant_policy_is_exact_and_isolated(
    variant: BuildVariant,
    coverage: bool,
    suffix: str,
    cxx_flags: list[str],
    link_flags: list[str],
) -> None:
    options = ConfigureOptions(variant)

    assert options.coverage is coverage
    assert options.shadow_suffix == suffix
    assert options.cxx_flags() == cxx_flags
    assert options.c_flags() == []
    assert options.link_flags() == link_flags
    assert all(
        flag not in options.cxx_flags() + options.link_flags()
        for flag in (
            "--coverage",
            "-fsanitize=address,undefined",
            "-fsanitize=thread",
        )
        if flag not in cxx_flags + link_flags
    )


def test_link_reachability_options_use_an_isolated_audited_shadow() -> None:
    options = ConfigureOptions(
        BuildVariant.RELEASE,
        extra_c_flags=("-ffunction-sections",),
        extra_cxx_flags=("-ffunction-sections",),
        extra_link_flags=("-Wl,--gc-sections",),
        generator="Unix Makefiles",
        shadow_suffix_override="-link-reachability",
    )

    assert options.shadow_suffix == "-link-reachability"
    assert options.c_flags() == ["-ffunction-sections"]
    assert options.cxx_flags() == ["-ffunction-sections"]
    assert options.link_flags() == ["-Wl,--gc-sections"]


@pytest.mark.parametrize("suffix", ["link", "-UPPER", "-", "-unsafe/path", "-x" * 40])
def test_shadow_suffix_override_rejects_unbounded_or_unsafe_values(suffix: str) -> None:
    options = ConfigureOptions(BuildVariant.RELEASE, shadow_suffix_override=suffix)

    with pytest.raises(ValueError, match="bounded lowercase suffix"):
        _ = options.shadow_suffix


@pytest.mark.parametrize("variant", tuple(BuildVariant))
def test_configure_propagates_variant_to_build_session(
    tmp_path: Path, variant: BuildVariant
) -> None:
    session = configure(tmp_path, ConfigureOptions(variant))

    assert session.variant is variant


def _cmake_session(root: Path, variant: BuildVariant) -> BuildSession:
    return BuildSession(
        root=root,
        shadow=root / "build" / f"ici-cmake-{variant.value}",
        variant=variant,
        backend=BACKEND_CMAKE,
        descriptor="CMakeLists.txt",
        reason="test session",
        configured=False,
        errors=["test boundary stopped before build"],
    )


def _write_cpp_project(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "lib.cpp").write_text("int twice(int value) { return value * 2; }\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_lib.cpp").write_text("int main() { return 0; }\n")
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\nproject(x)\n")
    (root / "ici.toml").write_text(
        'name = "variant-fixture"\ntype = "cpp"\nversion = "1.0.0"\n', encoding="utf-8"
    )


def test_build_engine_requests_release_variant_at_adapter_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cpp_project(tmp_path)
    seen: list[ConfigureOptions] = []

    def fake_configure(root: Path, options: ConfigureOptions, _config=None) -> BuildSession:
        seen.append(options)
        return _cmake_session(root, options.variant)

    monkeypatch.setattr("ici.engines.build.adapter_configure", fake_configure)

    BuildEngine(tmp_path).run()

    assert len(seen) == 1
    assert seen[0].variant is BuildVariant.RELEASE
    assert seen[0].coverage is False


def test_test_engine_requests_coverage_variant_at_adapter_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cpp_project(tmp_path)
    seen: list[ConfigureOptions] = []

    def fake_configure(root: Path, options: ConfigureOptions, _config=None) -> BuildSession:
        seen.append(options)
        return _cmake_session(root, options.variant)

    monkeypatch.setattr("ici.engines.test.adapter_configure", fake_configure)
    engine = TestEngine(tmp_path)

    assert engine._run_cpp_tests_via_adapter([]) == (0, 0, False)
    assert len(seen) == 1
    assert seen[0].variant is BuildVariant.COVERAGE
    assert seen[0].coverage is True


def test_sanitize_engine_requests_sanitize_variant_at_adapter_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cpp_project(tmp_path)
    seen: list[ConfigureOptions] = []

    def fake_configure(root: Path, options: ConfigureOptions, _config=None) -> BuildSession:
        seen.append(options)
        return _cmake_session(root, options.variant)

    monkeypatch.setattr("ici.engines.sanitize.adapter_configure", fake_configure)
    engine = SanitizeEngine(tmp_path)

    assert engine._run_cpp_sanitizer_via_adapter([]) is False
    assert len(seen) == 1
    assert seen[0].variant is BuildVariant.SANITIZE
    assert seen[0].coverage is False
