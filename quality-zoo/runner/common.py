"""Shared validation primitives for untrusted quality-zoo inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCENARIO_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ContractError(ValueError):
    """An input violated a checked quality-zoo contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def load_json_object(
    path: Path, *, label: str, max_bytes: int = 16 * 1024 * 1024
) -> dict[str, Any]:
    """Load one UTF-8 JSON object without accepting duplicate keys."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError("duplicate-json-key", f"{label} repeats {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ContractError("invalid-json-number", f"{label} contains {value}")

    try:
        if not path.is_file() or path.is_symlink():
            raise ContractError("unsafe-file", f"{label} is not a regular file")
        if path.stat().st_size > max_bytes:
            raise ContractError(
                "json-too-large", f"{label} exceeds the {max_bytes}-byte limit"
            )
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractError("read-failed", f"cannot read {label}: {error}") from error
    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except ContractError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ContractError("invalid-json", f"cannot parse {label}: {error}") from error
    if not isinstance(payload, dict):
        raise ContractError("invalid-json-root", f"{label} must be an object")
    return payload


def sha256_file(path: Path, *, max_bytes: int | None = None) -> tuple[str, int]:
    """Hash a regular file through a bounded streaming read."""

    try:
        if not path.is_file() or path.is_symlink():
            raise ContractError(
                "unsafe-file", f"not a regular non-symlink file: {path}"
            )
        size = path.stat().st_size
    except OSError as error:
        raise ContractError("stat-failed", f"cannot inspect {path}: {error}") from error
    if max_bytes is not None and size > max_bytes:
        raise ContractError(
            "file-too-large", f"{path} is {size} bytes; limit is {max_bytes}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise ContractError("read-failed", f"cannot hash {path}: {error}") from error
    return digest.hexdigest(), size


def require_string(value: Any, label: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ContractError("invalid-field", f"{label} must be a string")
    return value


def require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("invalid-field", f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ContractError("invalid-field", f"{label} must be >= {minimum}")
    return value


def require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ContractError("invalid-field", f"{label} must be a boolean")
    return value


def contained_path(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    """Resolve a POSIX-like relative path beneath root."""

    if not relative or "\\" in relative or WINDOWS_DRIVE_RE.match(relative):
        raise ContractError("unsafe-path", f"invalid relative path: {relative!r}")
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise ContractError("unsafe-path", f"path escapes its root: {relative!r}")
    root = root.resolve(strict=True)
    try:
        resolved = (root / raw).resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ContractError(
            "unsafe-path", f"path is not contained: {relative!r}"
        ) from error
    return resolved


MAX_TOOL_OUTPUT_BYTES = 1024 * 1024
ALLOWED_PROFILES = {"fast", "standard", "deep"}


def validate_command(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError("unsafe-command", "command must be an argv string array")
    if len(value) not in {3, 4} or value[:2] != ["verify", "--profile"]:
        raise ContractError(
            "unsafe-command",
            "command must be verify --profile <fast|standard|deep> [--no-cache]",
        )
    if value[2] not in ALLOWED_PROFILES:
        raise ContractError("unsafe-command", f"unsupported profile {value[2]!r}")
    if len(value) == 4 and value[3] != "--no-cache":
        raise ContractError("unsafe-command", f"unsupported argument {value[3]!r}")
    return list(value)


def reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ContractError("unsafe-scenario", f"scenario root is a symlink: {root}")
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ContractError(
                    "unsafe-scenario",
                    f"scenario contains symlink: {path.relative_to(root)}",
                )
    except OSError as error:
        raise ContractError("scenario-scan-failed", str(error)) from error


def load_registry(manifest_path: Path) -> tuple[Path, dict[str, Path]]:
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    payload = load_json_object(manifest_path, label="quality-zoo manifest")
    if payload.get("schema") != 1:
        raise ContractError("manifest-schema", "quality-zoo manifest schema must be 1")
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ContractError(
            "manifest-scenarios", "manifest needs at least one scenario"
        )
    registry: dict[str, Path] = {}
    for index, item in enumerate(raw_scenarios):
        if not isinstance(item, dict) or set(item) != {"id", "path"}:
            raise ContractError("manifest-entry", f"scenario entry {index} is invalid")
        scenario_id = require_string(item["id"], f"scenarios[{index}].id")
        if not SCENARIO_ID_RE.fullmatch(scenario_id) or scenario_id in registry:
            raise ContractError(
                "manifest-entry", f"invalid/duplicate scenario ID {scenario_id!r}"
            )
        scenario_path = contained_path(
            root, require_string(item["path"], "scenario path")
        )
        if not scenario_path.is_dir():
            raise ContractError(
                "manifest-entry", f"scenario path is not a directory: {scenario_path}"
            )
        registry[scenario_id] = scenario_path
    return root, registry


def read_bounded(path: Path) -> tuple[str, bool]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        data = stream.read(MAX_TOOL_OUTPUT_BYTES)
    return data.decode("utf-8", errors="replace"), size > MAX_TOOL_OUTPUT_BYTES


def isolated_environment(temp_root: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "HOME": str(temp_root / "home"),
        "XDG_CONFIG_HOME": str(temp_root / "config"),
        "ICI_CACHE_DIR": str(temp_root / "cache"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
    }
    for name in ("ICI_PYTHON", "QT_QPA_PLATFORM"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    for path in (temp_root / "home", temp_root / "config", temp_root / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    return env


def run_version(ici_bin: Path, timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            [str(ici_bin), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError("ici-version-failed", str(error)) from error
    version = completed.stdout.strip()
    if completed.returncode != 0 or not version.startswith("ici "):
        raise ContractError(
            "ici-version-failed", f"exit {completed.returncode}, output {version!r}"
        )
    return version.removeprefix("ici ")


def copy_artifact(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ContractError("output-exists", f"refusing to replace {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
