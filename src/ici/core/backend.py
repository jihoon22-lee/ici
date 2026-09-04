"""Build-backend discovery without build-session dependencies."""

from dataclasses import dataclass
from pathlib import Path

BACKEND_CMAKE = "cmake"
BACKEND_QMAKE = "qmake"
BACKEND_MAKE = "make"

# Only the project root is inspected. Descriptors in subdirectories do not
# select a backend, which keeps partially migrated projects on their existing
# direct compiler path.
_MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")


@dataclass(frozen=True)
class BackendChoice:
    """Which backend runs, and why. The reason becomes tool evidence."""

    kind: str | None
    reason: str
    descriptor: str = ""


def _is_real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def select_backend(root: Path, config: dict | None = None) -> BackendChoice:
    """Pick a build backend from the descriptor at the project root."""

    cmake_file = root / "CMakeLists.txt"
    has_cmake = _is_real_file(cmake_file)
    pro_files = sorted(path for path in root.glob("*.pro") if _is_real_file(path))
    makefiles = [name for name in _MAKEFILE_NAMES if _is_real_file(root / name)]

    if has_cmake:
        reason = "CMakeLists.txt at the project root selected the CMake backend"
        if pro_files:
            reason += f"; {pro_files[0].name} was present and passed over"
        return BackendChoice(BACKEND_CMAKE, reason, "CMakeLists.txt")

    if pro_files:
        return BackendChoice(
            BACKEND_QMAKE,
            f"{pro_files[0].name} at the project root selected the qmake backend",
            pro_files[0].name,
        )

    if makefiles:
        build = config.get("build", {}) if isinstance(config, dict) else {}
        make_config = build.get("make", {}) if isinstance(build, dict) else {}
        if isinstance(make_config, dict) and make_config.get("enabled") is True:
            return BackendChoice(
                BACKEND_MAKE,
                f"{makefiles[0]} at the project root selected the configured Make backend",
                makefiles[0],
            )
        return BackendChoice(
            None,
            f"{makefiles[0]} at the project root requires build.make.enabled=true "
            "and explicit shell-free command argv",
            makefiles[0],
        )

    return BackendChoice(None, "No build descriptor at the project root", "")
