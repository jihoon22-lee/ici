"""Configuration management & Global Verification Policy for ici."""

import os
import sys
from pathlib import Path
from typing import Any

import tomli
import tomli_w

from ici import __version__
from ici.config_schema import ConfigError, validate_config, validate_config_paths

# Default Enterprise Quality Policy — Embedded inside ici
DEFAULT_CONFIG: dict[str, Any] = {
    "ici": {
        "version": __version__,
        "policy_name": "Standard Enterprise CI/CD Quality Gate",
        "profile": "standard",
    },
    "project": {
        "source_dirs": ["src", "lib", "app", "packages", "python"],
    },
    "build": {
        # Handwritten Make projects opt in explicitly.  Empty command vectors
        # keep the default safe and make the required build/test contract
        # visible when a project enables this backend.
        "make": {
            "enabled": False,
            "workdir": ".",
            "shadow_dir": "build/ici-make",
            "out_of_tree": "allow",
            "configure_argv": [],
            "build_argv": [],
            "test_argv": [],
            "clean_argv": [],
            "jobs": 1,
            "coverage_build_argv": [],
            "coverage_test_argv": [],
            "sanitize_build_argv": [],
            "sanitize_test_argv": [],
            "thread_sanitize_build_argv": [],
            "thread_sanitize_test_argv": [],
        }
    },
    "doctor": {
        # Tools named here are still probed even if missing, but a missing
        # required tool renders as a WARN row in `ici doctor` instead of a
        # silently-blank one. `doctor` is diagnostic-only (not part of the
        # `verify` gate), so this lives outside `engines`.
        "required_tools": [],
    },
    "engines": {
        "build": {
            "enabled": False,
            "mode": "pass_warn_fail",
            "required": False,
        },
        "line": {
            "enabled": True,
            "mode": "pass_warn_fail",  # pass_warn_fail | pass_fail | pass_warn
            "warn_limit": 500,
            "fail_limit": 1000,
            "gate_dirs": ["src", "include", "lib", "app"],
            "include_dirs": [],
            "exclude_dirs": [],
        },
        "lint": {
            "enabled": True,
            "mode": "pass_warn_fail",
            "ruff_required": False,
            "clang_tidy": "auto",
            "clazy": "auto",
            "clazy_profile": "level0",
        },
        "compile_db": {
            "enabled": True,
            "mode": "pass_warn_fail",
            "database_required": False,
            "required_flags": [],
            "forbidden_flags": [],
        },
        "test": {
            "enabled": True,
            "mode": "pass_fail",
            "min_tem_score": 4.0,
            "min_branch_cov": 80.0,
            "min_func_cov": 90.0,
            "coverage_required": False,
            # Deep-profile quality observations reuse the base pytest output.
            # Repeats and mutation probing are explicitly opt-in and therefore
            # add no default subprocess cost.
            "quality": {
                "enabled": True,
                "mode": "report",
                "repeat_runs": 1,
                "timeout": 300.0,
                "slow_test_threshold": 1.0,
                "max_slow_tests": 50,
                "mutation": {
                    "enabled": False,
                    "tool": "auto",
                },
            },
        },
        "type": {
            "enabled": True,
            "mode": "pass_warn",
            "fail_on_error": True,
            "warn_on_missing_annotation": False,  # Missing annotations do not warn by default
            "mypy_required": False,
            # project: preserve Mypy's own discovered configuration unchanged.
            # ici: explicit opt-in overlay for additional untyped-body hygiene.
            "mypy_profile": "project",
        },
        "python_compat": {
            "enabled": True,
            "mode": "pass_warn_fail",
            "required": False,
            # Empty means the interpreter currently running ici, treated as required.
            "interpreters": [],
            "required_interpreters": [],
            # Importing executes module top-level code, so smoke targets are opt-in.
            "imports": [],
            # Empty means infer the earliest supported minor from requires-python.
            "target_version": "",
            # Package metadata is inspected without importing project code.
            # Wheels are opt-in evidence and are never built or extracted.
            "wheel_globs": [],
            "wheel_required": False,
            "wheel_policy": "allow-native",
            "check_entrypoints": True,
            "check_package_files": True,
            "max_wheels": 32,
            "max_wheel_members": 8192,
            "max_wheel_uncompressed_bytes": 64 * 1024 * 1024,
        },
        "complexity": {
            "enabled": True,
            "mode": "pass_warn_fail",
            "warn_cc": 15,
            "fail_cc": 25,
            "warn_nesting": 4,
            "cpp_boundaries": "auto",
        },
        "sanitize": {
            "enabled": True,
            "mode": "pass_fail",
        },
        "thread_sanitize": {
            "enabled": True,
            "mode": "pass_fail",
        },
        "dead": {
            "enabled": True,
            "mode": "pass_warn",
            "cpp_unused": "auto",
            "cpp_linker": "off",
            "include_generated": False,
            "include_vendor": False,
        },
        "dup": {
            "enabled": True,
            "mode": "pass_warn",
            "warn_pct": 5.0,
            "fail_pct": 15.0,
            "min_window": 6,
            "python_semantic": "auto",
            "include_generated": False,
            "include_vendor": False,
        },
        "exception": {
            "enabled": True,
            "mode": "pass_fail",
        },
        "cycle": {
            "enabled": True,
            "mode": "pass_warn_fail",
            "max_reported": 20,
        },
        "cognitive": {
            "enabled": True,
            "mode": "pass_warn",
            "warn": 30,
            "fail": 60,
            "warn_nesting": 4,
        },
        "security": {
            "enabled": True,
            "mode": "pass_warn",
            "scan_tests": False,
            "secret_name_allowlist": [],
        },
        "resource": {
            "enabled": True,
            "mode": "pass_warn",
        },
        "binary_compat": {
            "enabled": False,
            "mode": "pass_warn_fail",
            "required": False,
            "artifacts": [],
            "expected_class": "",
            "expected_machine": "",
            "max_glibc": "",
            "max_glibcxx": "",
            "max_cxxabi": "",
            "forbid_absolute_rpath": True,
            "forbidden_needed": [],
            "allowed_needed": [],
            "forbid_build_paths": True,
            "allow_non_elf": False,
            "max_artifacts": 64,
        },
        "integration": {
            "enabled": False,
            "mode": "pass_warn_fail",
            "required": False,
            "max_cases": 32,
            "max_output_bytes": 64 * 1024,
            "python_targets": {},
            "cases": [],
        },
    },
}


def load_config(
    base_dir: Path | None = None, *, create_global_default: bool = True
) -> dict[str, Any]:
    """Load the effective policy in deterministic precedence order.

    Defaults are merged first, followed by the XDG global policy, the
    project's ``ici.toml`` and ``dev.toml``, and finally ``ICI_CONFIG`` when
    set.  Every present file is loaded; malformed files and a missing
    explicitly requested file are configuration errors.
    """
    import copy

    config = copy.deepcopy(DEFAULT_CONFIG)
    base = _resolve_filesystem_path(base_dir or Path.cwd(), "project root")
    explicit_value = os.environ.get("ICI_CONFIG")
    explicit_path = (
        _resolve_filesystem_path(Path(explicit_value).expanduser(), "explicit configuration")
        if explicit_value
        else None
    )

    loaded = False
    for path in _config_paths(base, explicit_path):
        try:
            exists = path.exists()
        except OSError as err:
            raise ConfigError(f"could not inspect configuration {path}: {err}") from err
        if not exists:
            if explicit_path is not None and path == explicit_path:
                raise ConfigError(f"explicit configuration file does not exist: {path}")
            continue
        try:
            is_file = path.is_file()
        except OSError as err:
            raise ConfigError(f"could not inspect configuration {path}: {err}") from err
        if not is_file:
            raise ConfigError(f"configuration path is not a file: {path}")
        try:
            with path.open("rb") as stream:
                try:
                    user_cfg = tomli.load(stream)
                except (ValueError, RecursionError) as err:
                    raise ConfigError(f"could not parse configuration {path}: {err}") from err
        except OSError as err:
            raise ConfigError(f"could not read configuration {path}: {err}") from err
        if not isinstance(user_cfg, dict):
            raise ConfigError(f"configuration must be a table: {path}")
        _deep_merge(config, user_cfg)
        loaded = True

    if not loaded and explicit_path is None and create_global_default:
        _ensure_global_default_config(config)

    validate_config(config)
    validate_config_paths(config, base)
    return config


def _resolve_filesystem_path(path: Path, description: str) -> Path:
    """Canonicalize a filesystem path and normalize resolution failures."""

    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as err:
        raise ConfigError(f"could not resolve {description} path {path}: {err}") from err


def _config_paths(base: Path, explicit_path: Path | None = None) -> list[Path]:
    """Return policy files in their fixed precedence order."""

    paths = [
        _resolve_filesystem_path(get_global_config_path(), "global configuration"),
        _resolve_filesystem_path(base / "ici.toml", "project configuration"),
        _resolve_filesystem_path(base / "dev.toml", "development configuration"),
    ]
    if explicit_path is None:
        explicit = os.environ.get("ICI_CONFIG")
        if explicit:
            explicit_path = _resolve_filesystem_path(
                Path(explicit).expanduser(), "explicit configuration"
            )
    if explicit_path is not None:
        paths.append(explicit_path)
    return paths


def get_global_config_path() -> Path:
    """Returns the per-user global config path (XDG_CONFIG_HOME aware)."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ici" / "ici.toml"


def _ensure_global_default_config(config: dict[str, Any]) -> None:
    """Creates the per-user global ici.toml from the default policy on first run."""
    target = get_global_config_path()
    try:
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        save_config(config, target)
        print(f"[ici] 기본 전역 설정을 생성했습니다: {target}", file=sys.stderr)
    except RuntimeError as err:
        raise ConfigError(f"could not resolve global configuration path {target}: {err}") from err
    except OSError as err:
        _ = err


def get_engine_config(config: dict[str, Any], engine_name: str) -> dict[str, Any]:
    """Extracts specific engine configuration dictionary."""
    engines = config.get("engines", {})
    return engines.get(engine_name, {})


def save_config(config: dict[str, Any], path: Path) -> None:
    """Saves dictionary to TOML file atomically."""
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "wb") as f:
        tomli_w.dump(config, f)
    temp_path.replace(path)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
