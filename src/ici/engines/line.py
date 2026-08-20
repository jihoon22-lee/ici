"""1. Line Count Engine — 500-line WARN / 1000-line ERROR Rules with Policy & Tree Data."""

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
        cfg = self.get_config("line")
        warn_limit = cfg.get("warn_limit", 500)
        fail_limit = cfg.get("fail_limit", 1000)
        mode = cfg.get("mode", "pass_warn_fail")
        gate_dirs = cfg.get("gate_dirs", ["src", "include", "lib", "app"])
        include_dirs = [str(x) for x in cfg.get("include_dirs", []) or []]
        exclude_dirs = set(str(x) for x in cfg.get("exclude_dirs", []) or [])

        default_dirs = ["src", "include", "tests", "lib", "app", "docs", "scripts"]
        target_dirs = include_dirs if include_dirs else default_dirs
        target_dirs = [d for d in target_dirs if d not in exclude_dirs]
        gate_set = {d for d in gate_dirs if d not in exclude_dirs}

        total_code, total_comments, total_blanks = 0, 0, 0
        targets: list[InspectionTarget] = []
        files_data: list[dict] = []
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
                is_gated = rel_p.split("/", 1)[0] in gate_set

                # Check thresholds (only for production-code gate directories)
                if is_gated and fail_limit is not None and code > fail_limit:
                    status = EngineStatus.FAIL
                    has_error = True
                    msg = f"Pure code lines ({code}) exceed {fail_limit} lines limit (Refactoring required)"
                elif is_gated and warn_limit is not None and code > warn_limit:
                    status = EngineStatus.WARN
                    has_warn = True
                    msg = f"Pure code lines ({code}) exceed {warn_limit} lines threshold (Split recommended)"
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

                files_data.append(
                    {
                        "path": rel_p,
                        "lang": EXT_MAP[filepath.suffix],
                        "code": code,
                        "comment": comment,
                        "blank": blank,
                        "total": total_lines,
                        "status": status.value,
                    }
                )

        # Top 5 largest files by code lines
        files_data.sort(key=lambda x: x["code"], reverse=True)
        top_files = files_data[:5]

        duration = time.time() - t0
        total_all = total_code + total_comments + total_blanks
        overall_status = self.evaluate_status(has_error, has_warn, mode)

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
                "files_data": files_data,
                "top_files": top_files,
                "metrics_summary": f"{total_code:,} code / {len(targets)} files",
            },
            required=bool(cfg.get("required", True)),
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
