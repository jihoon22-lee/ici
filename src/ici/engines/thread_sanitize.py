"""Deep-profile ThreadSanitizer verification with an isolated build tree."""

from __future__ import annotations

import os
import re

from ici.core.context import BuildVariant
from ici.engines.sanitize import SanitizeEngine

_TSAN_ERROR_RE = re.compile(r"(?mi)^\s*(?:==\d+==)?\s*(?:WARNING|SUMMARY):\s*ThreadSanitizer:\s*\S")


class ThreadSanitizeEngine(SanitizeEngine):
    """Run C++ tests under TSan only when the deep profile selects this engine."""

    ENGINE_NAME = "thread_sanitize"
    CONFIG_SECTION = "thread_sanitize"
    ISSUE_COUNT_KEY = "thread_sanitize_issues"
    BUILD_VARIANT = BuildVariant.THREAD_SANITIZE
    CHECK_PYTHON_RESOURCES = False
    TEMP_PREFIX = "ici-thread-sanitize-"
    BINARY_SUFFIX = "_tsan"
    CPP_LABEL = "TSan"
    CPP_COMPILE_FLAGS = (
        "-fsanitize=thread",
        "-fno-omit-frame-pointer",
        "-g",
    )
    NO_SCOPE_NAME = "ThreadSanitizer"
    NO_SCOPE_MESSAGE = "No applicable C++ sources were selected; ThreadSanitizer was not run"
    PARTIAL_SUMMARY = (
        "ThreadSanitizer partially executed: one or more applicable scopes were skipped"
    )
    CLEAN_SUMMARY = "Thread Safety & TSan Clean (0 Defects)"
    SKIP_SUMMARY = "ThreadSanitizer skipped: no applicable C++ checks were executed"

    @staticmethod
    def _contains_sanitizer_diagnostic(output: str) -> bool:
        return _TSAN_ERROR_RE.search(output) is not None

    @classmethod
    def _sanitizer_environment(cls) -> dict[str, str]:
        env = os.environ.copy()
        env["TSAN_OPTIONS"] = cls._append_option(
            env.get("TSAN_OPTIONS", ""),
            "halt_on_error=1",
        )
        return env
