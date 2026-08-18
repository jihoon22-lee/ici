"""Configuration management & Global Verification Policy for ici."""

import os
from pathlib import Path
from typing import Any

import tomli
import tomli_w

# Default Enterprise Quality Policy — Embedded inside ici
DEFAULT_CONFIG: dict[str, Any] = {
    "ici": {
        "version": "0.2.0",
        "policy_name": "Standard Enterprise CI/CD Quality Gate",
    },
    "engines": {
        "line": {
            "enabled": True,
            "mode": "pass_warn_fail",  # pass_warn_fail | pass_fail | pass_warn
            "warn_limit": 500,
            "fail_limit": 1000,
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
    """Loads configuration by deep-merging user/system config on top of standard default policy."""
    import copy

    config = copy.deepcopy(DEFAULT_CONFIG)

    # Search candidates: ICI_CONFIG env var, ~/.config/ici/ici.toml, base_dir/ici.toml
    candidate_paths: list[Path] = []
    if os.environ.get("ICI_CONFIG"):
        candidate_paths.append(Path(os.environ["ICI_CONFIG"]).resolve())

    home_conf = Path.home() / ".config/ici/ici.toml"
    candidate_paths.append(home_conf)

    base = (base_dir or Path.cwd()).resolve()
    for conf_name in ("ici.toml", "dev.toml"):
        candidate_paths.append(base / conf_name)

    for p in candidate_paths:
        if p.exists():
            try:
                with open(p, "rb") as f:
                    user_cfg = tomli.load(f)
                    _deep_merge(config, user_cfg)
                    break
            except (OSError, tomli.TOMLDecodeError) as err:
                _ = err

    return config


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
