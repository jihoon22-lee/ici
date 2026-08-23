"""1. Line Count Engine — 500-line WARN / 1000-line ERROR Rules with Policy & Tree Data."""

import os
import time
from pathlib import Path
from typing import ClassVar

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

_JUNK_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "htmlcov",
}


class LineCountEngine(BaseEngine):
    """Counts lines and verifies single-file size thresholds (500 WARN, 1000 FAIL)."""

    _DEFAULT_SOURCE: ClassVar[list[str]] = ["src", "include", "lib", "app"]

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("line")
        plan = self._build_scan_plan(cfg)
        data = self._collect_files(plan)

        files_data = data["files_data"]
        files_data.sort(key=lambda x: x["code"], reverse=True)
        top_files = [f for f in files_data if f["scope"] == "source"][:5]

        duration = time.time() - t0
        total_all = data["code"] + data["comment"] + data["blank"]
        overall_status = self.evaluate_status(data["has_error"], data["has_warn"], plan["mode"])

        summary = (
            f"Total {total_all:,} lines ({data['code']:,} code, {data['comment']:,} comment,"
            f" {data['blank']:,} blank) across {len(data['targets'])} files"
        )

        return self.create_result(
            name="line",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=data["targets"],
            extra={
                "code": data["code"],
                "comment": data["comment"],
                "blank": data["blank"],
                "total": total_all,
                "files_data": files_data,
                "top_files": top_files,
                "top_files_all": files_data[:5],
                "all": {
                    "code": data["all_code"],
                    "comment": data["all_comment"],
                    "blank": data["all_blank"],
                    "total": data["all_code"] + data["all_comment"] + data["all_blank"],
                    "files": len(files_data),
                },
                "metrics_summary": f"{data['code']:,} code / {len(data['targets'])} files",
            },
            required=bool(cfg.get("required", True)),
        )

    def _build_scan_plan(self, cfg: dict) -> dict:
        """Resolves source scope (defaults + include_dirs), gate set and limits."""
        include_dirs = [str(x) for x in cfg.get("include_dirs", []) or []]
        exclude_set = {str(x).strip("/") for x in cfg.get("exclude_dirs", []) or []}

        scope_list: list[str] = []
        for d in [*self._DEFAULT_SOURCE, *include_dirs]:
            d_clean = str(d).strip("/")
            if d_clean and d_clean not in exclude_set and d_clean not in scope_list:
                scope_list.append(d_clean)

        return {
            "scope_list": scope_list,
            "gate_set": {
                str(d).strip("/") for d in cfg.get("gate_dirs", list(self._DEFAULT_SOURCE))
            }
            - exclude_set,
            "exclude_roots": {self.project_root / x for x in exclude_set},
            "warn_limit": cfg.get("warn_limit", 500),
            "fail_limit": cfg.get("fail_limit", 1000),
            "mode": cfg.get("mode", "pass_warn_fail"),
        }

    def _classify(
        self, code: int, is_gated: bool, warn: int, fail: int
    ) -> tuple[EngineStatus, str]:
        """Applies single-file size thresholds to gated source files only."""
        if is_gated and fail is not None and code > fail:
            return EngineStatus.FAIL, (
                f"Pure code lines ({code}) exceed {fail} lines limit (Refactoring required)"
            )
        if is_gated and warn is not None and code > warn:
            return EngineStatus.WARN, (
                f"Pure code lines ({code}) exceed {warn} lines threshold (Split recommended)"
            )
        return EngineStatus.PASS, f"{code} code lines"

    def _record_file(self, data: dict, filepath: Path, in_scope: bool) -> None:
        """Counts one file into the accumulator; gating only applies in scope."""
        code, comment, blank = self._count_file(filepath)
        rel_p = str(filepath.relative_to(self.project_root))
        parts = rel_p.split("/")
        top_dir = parts[0] if len(parts) > 1 else ""
        is_gated = in_scope and top_dir in data["gate_set"]

        status, msg = self._classify(code, is_gated, data["warn_limit"], data["fail_limit"])
        if status is EngineStatus.FAIL:
            data["has_error"] = True
        elif status is EngineStatus.WARN:
            data["has_warn"] = True

        entry_scope = "source" if in_scope else "extra"
        entry = {
            "path": rel_p,
            "lang": EXT_MAP[filepath.suffix],
            "code": code,
            "comment": comment,
            "blank": blank,
            "total": code + comment + blank,
            "status": status.value,
            "scope": entry_scope,
        }
        data["files_data"].append(entry)
        if in_scope:
            data["targets"].append(
                InspectionTarget(
                    file_path=rel_p,
                    start_line=1,
                    end_line=code + comment + blank,
                    target_name=EXT_MAP[filepath.suffix],
                    status=status,
                    message=msg,
                    metrics={
                        "code": code,
                        "comment": comment,
                        "blank": blank,
                        "total": code + comment + blank,
                    },
                )
            )
            data["code"] += code
            data["comment"] += comment
            data["blank"] += blank
        data["all_code"] += code
        data["all_comment"] += comment
        data["all_blank"] += blank

    def _acceptable(self, filepath: Path) -> bool:
        return (
            not filepath.is_symlink()
            and filepath.is_file()
            and filepath.suffix in EXT_MAP
            and not _should_ignore_path(filepath)
        )

    def _fresh_accumulator(self) -> dict:
        return {
            "targets": [],
            "files_data": [],
            "seen": set(),
            "code": 0,
            "comment": 0,
            "blank": 0,
            "all_code": 0,
            "all_comment": 0,
            "all_blank": 0,
            "has_error": False,
            "has_warn": False,
            "gate_set": set(),
            "warn_limit": None,
            "fail_limit": None,
        }

    def _collect_files(self, plan: dict) -> dict:
        """Pass 1 scans source scope; pass 2 sweeps the whole project for extras."""
        data = self._fresh_accumulator()
        data.update(
            gate_set=plan["gate_set"], warn_limit=plan["warn_limit"], fail_limit=plan["fail_limit"]
        )

        for d in plan["scope_list"]:
            dir_path = self.project_root / d
            if not dir_path.exists():
                continue
            for filepath in sorted(dir_path.rglob("*")):
                if filepath in data["seen"] or not self._acceptable(filepath):
                    continue
                data["seen"].add(filepath)
                self._record_file(data, filepath, True)

        for current, dir_names, file_names in os.walk(self.project_root):
            cur_path = Path(current)
            dir_names[:] = [
                x
                for x in dir_names
                if x not in _JUNK_DIRS and (cur_path / x) not in plan["exclude_roots"]
            ]
            if any(cur_path == root or root in cur_path.parents for root in plan["exclude_roots"]):
                continue
            for name in sorted(file_names):
                filepath = cur_path / name
                if filepath in data["seen"] or not self._acceptable(filepath):
                    continue
                data["seen"].add(filepath)
                self._record_file(data, filepath, False)

        return data

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
            # Unreadable/undecodable files contribute nothing; keep counting others.
            _ = err

        return code, comment, blank
