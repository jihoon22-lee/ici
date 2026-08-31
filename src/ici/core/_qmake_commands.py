"""Pure argv construction for the qmake build adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class QmakeConfigureOptions(Protocol):
    @property
    def analysis_database(self) -> bool: ...

    @property
    def qmake_capture_wrapper(self) -> str: ...

    @property
    def qmake_capture_cxx(self) -> str: ...

    @property
    def qmake_capture_cc(self) -> str: ...

    def cxx_flags(self) -> list[str]: ...

    def link_flags(self) -> list[str]: ...


def qmake_configure_argv(
    qmake_bin: str,
    pro_file: Path,
    options: QmakeConfigureOptions,
) -> list[str]:
    """Build qmake argv without asking a shell to reinterpret compiler commands."""

    argv = [qmake_bin, str(pro_file)]
    if options.analysis_database:
        # -recursive materializes nested SUBDIRS Makefiles with the same
        # compiler metadata/override before the canonical build starts.
        argv.insert(1, "-recursive")
    if options.qmake_capture_wrapper:
        if not options.qmake_capture_cxx or not options.qmake_capture_cc:
            raise ValueError("qmake capture requires resolved C and C++ compilers")
        argv.extend(
            [
                "-after",
                f"QMAKE_CXX={options.qmake_capture_wrapper} {options.qmake_capture_cxx}",
                f"QMAKE_CC={options.qmake_capture_wrapper} {options.qmake_capture_cc}",
            ]
        )
    argv.extend(f"QMAKE_CXXFLAGS+={flag}" for flag in options.cxx_flags())
    argv.extend(f"QMAKE_LFLAGS+={flag}" for flag in options.link_flags())
    return argv


__all__ = ["qmake_configure_argv"]
