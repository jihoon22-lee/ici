"""1. Line Count Engine — 500-line WARN / 1000-line ERROR Rules."""

import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import _should_ignore_path
from ici.engines.base import BaseEngine

EXT_MAP = {
    ".cpp": "C++",
    ".c": "C++",
    ".h": "C++",
    ".hpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".py": "Python",
    ".sh": "Shell",
    ".csh": "Shell",
    ".toml": "TOML",
    ".md": "Markdown",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
}


class LineCountEngine(BaseEngine):
    """Counts lines and verifies single-file size thresholds (500 WARN, 1000 FAIL)."""

    def run(self) -> EngineResult:
        t0 = time.time()
        target_dirs = ["src", "include", "tests", "lib", "app", "docs", "scripts"]

        total_code, total_comments, total_blanks = 0, 0, 0
        targets: list[InspectionTarget] = []
        has_error = False
        has_warn = False

        for d in target_dirs:
            dir_path = self.project_root / d
            if not dir_path.exists():
                continue

            for filepath in dir_path.rglob("*"):
                if not filepath.is_file() or filepath.suffix not in EXT_MAP:
                    continue
                if _should_ignore_path(filepath):
                    continue

                code, comment, blank = self._count_file(filepath)
                total_lines = code + comment + blank
                total_code += code
                total_comments += comment
                total_blanks += blank

                rel_p = str(filepath.relative_to(self.project_root))

                # Check 500 / 1000 pure code lines threshold
                if code > 1000:
                    status = EngineStatus.FAIL
                    has_error = True
                    msg = (
                        f"Pure code lines ({code}) exceed 1,000 lines limit (Refactoring required)"
                    )
                elif code > 500:
                    status = EngineStatus.WARN
                    has_warn = True
                    msg = f"Pure code lines ({code}) exceed 500 lines threshold (Split recommended)"
                else:
                    status = EngineStatus.PASS
                    msg = f"{code} code lines, {comment} comments, {blank} blanks"

                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=1,
                        end_line=total_lines,
                        target_name=EXT_MAP[filepath.suffix],
                        status=status,
                        message=msg,
                        metrics={
                            "code": code,
                            "comment": comment,
                            "blank": blank,
                            "total": total_lines,
                        },
                    )
                )

        duration = time.time() - t0
        total_all = total_code + total_comments + total_blanks
        overall_status = (
            EngineStatus.FAIL
            if has_error
            else (EngineStatus.WARN if has_warn else EngineStatus.PASS)
        )

        summary = (
            f"Total {total_all:,} lines ({total_code:,} code, {total_comments:,} comment, {total_blanks:,} blank) "
            f"across {len(targets)} files"
        )

        return self.create_result(
            name="line",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "code": total_code,
                "comment": total_comments,
                "blank": total_blanks,
                "total": total_all,
                "metrics_summary": f"{total_code:,} code / {len(targets)} files",
            },
        )

    def _count_file(self, filepath: Path) -> tuple[int, int, int]:
        code, comment, blank = 0, 0, 0
        in_block_comment = False

        try:
            with open(filepath, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        blank += 1
                        continue

                    if filepath.suffix in (".cpp", ".c", ".h", ".hpp", ".cc", ".cxx"):
                        if in_block_comment:
                            comment += 1
                            if "*/" in stripped:
                                in_block_comment = False
                            continue
                        if stripped.startswith("/*"):
                            comment += 1
                            if "*/" not in stripped:
                                in_block_comment = True
                            continue
                        if stripped.startswith("//"):
                            comment += 1
                            continue
                    elif filepath.suffix in (".py", ".sh", ".csh", ".toml", ".yml", ".yaml"):
                        if stripped.startswith("#"):
                            comment += 1
                            continue

                    code += 1
        except (OSError, UnicodeDecodeError) as err:
            _ = err

        return code, comment, blank
