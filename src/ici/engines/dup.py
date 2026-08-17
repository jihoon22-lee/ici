"""8. Code Clone & Duplication Detection Engine with Cluster Grouping."""

import hashlib
import time
from collections import defaultdict

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    get_all_cpp_sources,
    get_all_python_sources,
)
from ici.engines.base import BaseEngine


class DuplicateEngine(BaseEngine):
    """Detects copy-pasted code blocks across files using token/line hash sliding windows."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("dup")
        warn_pct = cfg.get("warn_pct", 5.0)
        fail_pct = cfg.get("fail_pct", 15.0)
        window_size = cfg.get("min_window", 6)
        mode = cfg.get("mode", "pass_warn")

        py_sources = get_all_python_sources(self.project_root)
        cpp_sources = get_all_cpp_sources(self.project_root)
        all_sources = py_sources + cpp_sources

        # Map hash -> list of (file_rel, start_line, end_line, sample_text)
        block_map: dict[str, list[tuple[str, int, int, str]]] = defaultdict(list)
        total_code_lines = 0
        duplicate_lines_count = 0

        for src_file in all_sources:
            try:
                rel_p = str(src_file.relative_to(self.project_root))
                normalized_lines = []
                orig_line_nums = []

                with open(src_file, encoding="utf-8", errors="ignore") as f:
                    for line_idx, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped and not stripped.startswith(
                            ("#", "//", "/*", "*", "import ", "from ", "#include")
                        ):
                            normalized_lines.append(self._normalize_line(stripped))
                            orig_line_nums.append(line_idx)
                            total_code_lines += 1

                # Sliding window
                for i in range(len(normalized_lines) - window_size + 1):
                    window = "".join(normalized_lines[i : i + window_size])
                    w_hash = hashlib.sha256(window.encode("utf-8")).hexdigest()
                    start_l = orig_line_nums[i]
                    end_l = orig_line_nums[i + window_size - 1]
                    sample = "\n".join(normalized_lines[i : i + window_size])
                    block_map[w_hash].append((rel_p, start_l, end_l, sample))
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        # Group into Clone Groups
        clone_groups: list[dict] = []
        targets: list[InspectionTarget] = []
        group_idx = 1

        for _w_hash, occurrences in block_map.items():
            if len(occurrences) > 1:
                # Merge duplicate files in this group
                duplicate_lines_count += (len(occurrences) - 1) * window_size
                sample_snippet = occurrences[0][3]
                occ_list = []

                for f_p, s_l, e_l, _ in occurrences:
                    occ_list.append(
                        {
                            "file_path": f_p,
                            "start_line": s_l,
                            "end_line": e_l,
                            "loc": f"{f_p}:{s_l}-{e_l}",
                        }
                    )
                    targets.append(
                        InspectionTarget(
                            file_path=f_p,
                            start_line=s_l,
                            end_line=e_l,
                            target_name=f"CloneGroup#{group_idx}",
                            status=EngineStatus.WARN,
                            message=f"Duplicate code block ({window_size} lines) across {len(occurrences)} locations",
                            snippet=sample_snippet[:200],
                            metrics={"clone_group": group_idx, "occurrences": len(occurrences)},
                        )
                    )

                clone_groups.append(
                    {
                        "id": group_idx,
                        "lines_count": window_size,
                        "occurrences_count": len(occurrences),
                        "occurrences": occ_list,
                        "snippet": sample_snippet[:250],
                    }
                )
                group_idx += 1

        dup_pct = (
            (duplicate_lines_count / total_code_lines * 100.0) if total_code_lines > 0 else 0.0
        )
        duration = time.time() - t0

        has_fail = dup_pct > fail_pct
        has_warn = dup_pct > warn_pct or len(clone_groups) > 0
        overall_status = self.evaluate_status(has_fail, has_warn, mode)

        summary = f"Code Duplication Rate: {dup_pct:.1f}% ({len(clone_groups)} duplicate clone groups found)"

        return self.create_result(
            name="dup",
            status=overall_status,
            summary=summary,
            score=dup_pct,
            duration=duration,
            targets=targets,
            extra={
                "duplication_pct": dup_pct,
                "clone_groups_count": len(clone_groups),
                "clone_groups": clone_groups,
                "metrics_summary": f"Duplication: {dup_pct:.1f}% ({len(clone_groups)} groups)",
            },
        )

    def _normalize_line(self, line: str) -> str:
        """Removes spaces and quotes to match structurally identical lines."""
        return "".join(c for c in line if not c.isspace()).strip(";'\"")
