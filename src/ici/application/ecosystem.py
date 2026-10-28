"""Bind native ecosystem configuration and local tools to the common planner."""

import os
from pathlib import Path

from ici.adapters.providers.ecosystem import EcosystemProvider
from ici.application.testing import component_targets
from ici.application.tooling import locate_tool

PROVIDERS = {
    "rust.clippy": "clippy",
    "javascript.lint": "eslint",
    "typescript.lint": "eslint",
    "typescript.type": "tsc",
}
IGNORED = {"node_modules", "target", ".git", ".ici", ".venv", "dist", "build"}
CONFIG_NAMES = {
    "Cargo.toml",
    "Cargo.lock",
    "config.toml",
    "rust-toolchain.toml",
    "rust-toolchain",
    "clippy.toml",
    ".clippy.toml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "jsconfig.json",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    ".eslintrc.json",
    ".eslintrc.js",
}


def configs_under(root: Path) -> tuple[str, ...]:
    found = []
    for directory, children, files in os.walk(root):
        children[:] = sorted(set(children) - IGNORED)
        for name in sorted(files):
            if name in CONFIG_NAMES or (name.startswith("tsconfig.") and name.endswith(".json")):
                found.append((Path(directory) / name).relative_to(root).as_posix())
    return tuple(found)


def planner(fallback, root, component_root, units, files):
    def work(check, executable, task_id):
        provider = PROVIDERS.get(check.id)
        if provider is None:
            return fallback(check, executable, task_id)
        return EcosystemProvider(provider, root).plan(
            executable,
            component_root,
            component_targets(component_root, root, files[check.language]),
            task_id,
            units[check.language].id,
            configs_under(component_root),
        )

    return work


def locator(root: Path, fallback=locate_tool):
    def locate(name):
        local = root / "node_modules" / ".bin" / name
        if name in {"eslint", "tsc"} and local.is_file() and os.access(local, os.X_OK):
            return str(local)
        return fallback(name)

    return locate
