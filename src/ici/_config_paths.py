"""Project-root containment checks for path-bearing ici settings."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from typing import Any

from ici._config_validation import ConfigError, _error, resolve_project_path


def _validate_path_list(value: Any, setting: str, base: Path) -> None:
    """Validate and canonicalize a list of project-relative path settings."""

    if not isinstance(value, list):
        raise _error(setting, "must be a list of non-empty strings")
    for index, item in enumerate(value):
        item_path = f"{setting}[{index}]"
        if not isinstance(item, str) or not item:
            raise _error(item_path, "must be a non-empty string")
        try:
            resolve_project_path(base, item)
        except ConfigError as err:
            raise ConfigError(f"{item_path}: {err}") from err


def _validate_compile_database(value: Any, base: Path) -> None:
    setting = "project.compile_database"
    if not isinstance(value, str) or not value:
        raise _error(setting, "must be a non-empty string")
    if os.name != "nt" and ("\\" in value or bool(PureWindowsPath(value).drive)):
        raise _error(setting, "must use native project path syntax")
    try:
        resolve_project_path(base, value)
    except ConfigError as err:
        raise ConfigError(f"{setting}: {err}") from err


def _validate_project_paths(project: dict[str, Any], base: Path) -> None:
    for key in ("source_dirs", "cpp_external_build_dirs"):
        if key in project:
            _validate_path_list(project[key], f"project.{key}", base)
    compile_database = project.get("compile_database")
    if compile_database is not None:
        _validate_compile_database(compile_database, base)


def _validate_make_paths(make: dict[str, Any], base: Path) -> None:
    canonical_base = base.resolve(strict=False)
    resolved: dict[str, Path] = {}
    for key in ("workdir", "shadow_dir"):
        if key not in make:
            continue
        setting = f"build.make.{key}"
        try:
            path = resolve_project_path(base, make[key])
        except ConfigError as err:
            raise ConfigError(f"{setting}: {err}") from err
        if key == "shadow_dir" and path == canonical_base:
            raise ConfigError(f"{setting} must not be the project root")
        resolved[key] = path
    if (
        make.get("out_of_tree", "allow") == "required"
        and resolved.get("workdir", canonical_base) == canonical_base
    ):
        raise ConfigError("build.make.out_of_tree=required needs a non-root workdir")


def _validate_build_paths(config: dict[str, Any], base: Path) -> None:
    build = config.get("build")
    if isinstance(build, dict) and isinstance(build.get("make"), dict):
        _validate_make_paths(build["make"], base)


def _validate_engine_paths(engines: dict[str, Any], base: Path) -> None:
    line = engines.get("line")
    if isinstance(line, dict):
        for key in ("gate_dirs", "include_dirs", "exclude_dirs"):
            if key in line:
                _validate_path_list(line[key], f"engines.line.{key}", base)
    lint = engines.get("lint")
    if isinstance(lint, dict) and "clang_tidy_config" in lint:
        value = lint["clang_tidy_config"]
        setting = "engines.lint.clang_tidy_config"
        if not isinstance(value, str) or not value:
            raise _error(setting, "must be a non-empty string")
        try:
            resolve_project_path(base, value)
        except ConfigError as err:
            raise ConfigError(f"{setting}: {err}") from err


def validate_config_paths(config: dict[str, Any], base: Path) -> None:
    """Reject configured paths that resolve outside the project root.

    Partial engine configurations are accepted so standalone engine
    construction remains safe when callers provide only a relevant table.
    """

    if not isinstance(config, dict):
        raise ConfigError("configuration must be a table")
    project = config.get("project")
    if isinstance(project, dict):
        _validate_project_paths(project, base)
    _validate_build_paths(config, base)
    engines = config.get("engines")
    if isinstance(engines, dict):
        _validate_engine_paths(engines, base)
