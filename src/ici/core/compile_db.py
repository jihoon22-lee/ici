"""compile_commands.json validation — offline consistency checks."""

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from ici.build_adapters.base import BuildAdapterError


@dataclass
class CompileCommand:
    """One parsed compile database entry."""

    directory: Path
    file: Path
    arguments: list[str]

    @property
    def compiler(self) -> str:
        return Path(self.arguments[0]).name if self.arguments else ""

    @property
    def flags(self) -> list[str]:
        return self.arguments[1:]


def load_compile_database(
    db_path: Path, project_root: Path, build_root: Path | None = None
) -> list[CompileCommand]:
    """Parse a compile database and enforce path containment.

    Entries whose ``directory``/``file`` resolve outside the project (or the
    allowed build root) raise :class:`BuildAdapterError`.
    """
    try:
        raw_entries = json.loads(db_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise BuildAdapterError(f"could not read compile database {db_path}: {err}") from err
    if not isinstance(raw_entries, list):
        raise BuildAdapterError("compile database must be a JSON array")

    allowed_roots = [project_root.resolve()]
    if build_root is not None:
        allowed_roots.append(build_root.resolve())

    commands: list[CompileCommand] = []
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise BuildAdapterError(f"compile db entry #{index} is not an object")
        command = _parse_entry(entry, index, allowed_roots)
        commands.append(command)
    return commands


def _parse_entry(entry: dict, index: int, allowed_roots: list[Path]) -> CompileCommand:
    directory_raw = entry.get("directory")
    file_raw = entry.get("file")
    if not isinstance(directory_raw, str) or not isinstance(file_raw, str):
        raise BuildAdapterError(f"compile db entry #{index} missing directory/file")

    directory = Path(directory_raw).resolve()
    _require_contained(directory, allowed_roots, f"entry #{index} directory")

    source = Path(file_raw)
    if not source.is_absolute():
        source = directory / source
    source = source.resolve()
    _require_contained(source, allowed_roots, f"entry #{index} file")

    arguments = entry.get("arguments")
    if isinstance(arguments, list) and all(isinstance(a, str) for a in arguments):
        argv = list(arguments)
    elif isinstance(entry.get("command"), str):
        argv = shlex.split(entry["command"], posix=True)
    else:
        raise BuildAdapterError(f"compile db entry #{index} needs arguments or command")
    if not argv:
        raise BuildAdapterError(f"compile db entry #{index} has empty arguments")

    return CompileCommand(directory=directory, file=source, arguments=argv)


def _require_contained(path: Path, allowed_roots: list[Path], label: str) -> None:
    for root in allowed_roots:
        try:
            path.relative_to(root)
            return
        except ValueError:
            continue
    raise BuildAdapterError(f"{label} escapes project/build boundary: {path}")


def extract_standard(flags: list[str]) -> str | None:
    """Return the -std= value from compiler flags, if any."""
    for index, flag in enumerate(flags):
        if flag.startswith("-std="):
            return flag[5:]
        if flag == "-std" and index + 1 < len(flags):
            return flags[index + 1]
    return None


def extract_include_dirs(flags: list[str], base: Path) -> list[Path]:
    """Resolve -I/-isystem include directories against ``base``."""
    includes: list[Path] = []
    index = 0
    while index < len(flags):
        flag = flags[index]
        if flag.startswith("-I") and len(flag) > 2:
            includes.append((base / flag[2:]).resolve())
        elif flag in ("-I", "-isystem") and index + 1 < len(flags):
            index += 1
            includes.append((base / flags[index]).resolve())
        index += 1
    return includes
