"""Configuration management (ici.toml / dev.toml) using tomli."""

from pathlib import Path
from typing import Any

import tomli
import tomli_w


def load_config(base_dir: Path | None = None) -> dict[str, Any]:
    """Loads configuration from ici.toml or dev.toml."""
    base = (base_dir or Path.cwd()).resolve()

    for conf_name in ("ici.toml", "dev.toml"):
        p = base / conf_name
        if p.exists():
            try:
                with open(p, "rb") as f:
                    return tomli.load(f)
            except (OSError, tomli.TOMLDecodeError) as err:
                _ = err

    return {}


def save_config(config: dict[str, Any], path: Path) -> None:
    """Saves dictionary to TOML file atomically."""
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "wb") as f:
        tomli_w.dump(config, f)
    temp_path.replace(path)
