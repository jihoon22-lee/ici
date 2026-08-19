"""Configuration management & Global Verification Policy for ici."""

import os
from pathlib import Path
from typing import Any

import tomli
import tomli_w

from ici.config_schema import ConfigError, validate_config

# Default Enterprise Quality Policy — Embedded inside ici
DEFAULT_CONFIG: dict[str, Any] = {
    "ici": {
        "version": "0.3.3",
        "policy_name": "Standard Enterprise CI/CD Quality Gate",
    },
    "project": {
        "source_dirs": ["src", "lib", "app", "packages", "python"],
    },
    "engines": {
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
        },
        "test": {
            "enabled": True,
            "mode": "pass_fail",
            "min_tem_score": 4.0,
            "min_branch_cov": 80.0,
            "min_func_cov": 90.0,
        },
        "type": {
            "enabled": True,
            "mode": "pass_warn",
            "fail_on_error": True,
            "warn_on_missing_annotation": False,  # Missing annotations do not warn by default
        },
        "complexity": {
            "enabled": True,
            "mode": "pass_warn_fail",
            "warn_cc": 15,
            "fail_cc": 25,
            "warn_nesting": 4,
        },
        "sanitize": {
            "enabled": True,
            "mode": "pass_fail",
        },
        "dead": {
            "enabled": True,
            "mode": "pass_warn",
        },
        "dup": {
            "enabled": True,
            "mode": "pass_warn",
            "warn_pct": 5.0,
            "fail_pct": 15.0,
            "min_window": 6,
        },
        "exception": {
            "enabled": True,
            "mode": "pass_fail",
        },
    },
}


def load_config(base_dir: Path | None = None) -> dict[str, Any]:
    """Load the effective policy in deterministic precedence order.

    Defaults are merged first, followed by the XDG global policy, the
    project's ``ici.toml`` and ``dev.toml``, and finally ``ICI_CONFIG`` when
    set.  Every present file is loaded; malformed files and a missing
    explicitly requested file are configuration errors.
    """
    import copy

    config = copy.deepcopy(DEFAULT_CONFIG)
    base = (base_dir or Path.cwd()).resolve()
    explicit_value = os.environ.get("ICI_CONFIG")
    explicit_path = Path(explicit_value).expanduser().resolve() if explicit_value else None

    loaded = False
    for path in _config_paths(base):
        if not path.exists():
            if explicit_path is not None and path == explicit_path:
                raise ConfigError(f"explicit configuration file does not exist: {path}")
            continue
        if not path.is_file():
            raise ConfigError(f"configuration path is not a file: {path}")
        try:
            with path.open("rb") as stream:
                user_cfg = tomli.load(stream)
        except (OSError, tomli.TOMLDecodeError) as err:
            raise ConfigError(f"could not read configuration {path}: {err}") from err
        if not isinstance(user_cfg, dict):
            raise ConfigError(f"configuration must be a table: {path}")
        _deep_merge(config, user_cfg)
        loaded = True

    if not loaded and explicit_path is None:
        _ensure_global_default_config(config)

    validate_config(config)
    return config


def _config_paths(base: Path) -> list[Path]:
    """Return policy files in their fixed precedence order."""

    paths = [get_global_config_path(), base / "ici.toml", base / "dev.toml"]
    explicit = os.environ.get("ICI_CONFIG")
    if explicit:
        paths.append(Path(explicit).expanduser().resolve())
    return paths


def get_global_config_path() -> Path:
    """Returns the per-user global config path (XDG_CONFIG_HOME aware)."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ici" / "ici.toml"


def _ensure_global_default_config(config: dict[str, Any]) -> None:
    """Creates the per-user global ici.toml from the default policy on first run."""
    target = get_global_config_path()
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        save_config(config, target)
        print(f"[ici] 기본 전역 설정을 생성했습니다: {target}")
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
