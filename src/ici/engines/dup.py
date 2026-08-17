"""8. Code Clone & Duplication (Copy-Paste) Detection Engine."""

import hashlib
import time
from collections import defaultdict

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    get_all_cpp_sources,
    get_all_python_sources,
)
from ici.engines.base import BaseEngine

WINDOW_SIZE = 6  # Minimum identical consecutive lines to constitute a clone block


class DuplicateEngine(BaseEngine):
    """Detects copy-pasted code blocks across files using token/line hash sliding windows."""

    def run(self) -> EngineResult:
        t0 = time.time()
        py_sources = get_all_python_sources(self.project_root)
        cpp_sources = get_all_cpp_sources(self.project_root)
        all_sources = py_sources + cpp_sources

        # Map hash -> list of (file_rel, start_line, end_line, sample_text)
        block_map: dict[str, list[tuple[str, int, int, str]]] = defaultdict(list)
        total_code_lines = 0
        duplicate_lines_count = 0
        targets: list[InspectionTarget] = []

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
                for i in range(len(normalized_lines) - WINDOW_SIZE + 1):
                    window = "".join(normalized_lines[i : i + WINDOW_SIZE])
                    w_hash = hashlib.sha256(window.encode("utf-8")).hexdigest()
                    start_l = orig_line_nums[i]
                    end_l = orig_line_nums[i + WINDOW_SIZE - 1]
                    sample = "\n".join(normalized_lines[i : i + WINDOW_SIZE])
                    block_map[w_hash].append((rel_p, start_l, end_l, sample))
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        # Identify duplicates
        seen_pairs = set()
        for _w_hash, occurrences in block_map.items():
            if len(occurrences) > 1:
                # Multiple identical blocks
                duplicate_lines_count += (len(occurrences) - 1) * WINDOW_SIZE
                for idx_a in range(len(occurrences)):
                    for idx_b in range(idx_a + 1, len(occurrences)):
                        f_a, s_a, e_a, sample = occurrences[idx_a]
                        f_b, s_b, e_b, _ = occurrences[idx_b]
                        pair_key = (f_a, s_a, f_b, s_b)

                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            targets.append(
                                InspectionTarget(
                                    file_path=f_a,
                                    start_line=s_a,
                                    end_line=e_a,
                                    target_name="DuplicateBlock",
                                    status=EngineStatus.WARN,
                                    message=f"Duplicate code block ({WINDOW_SIZE} lines) identical to {f_b}:{s_b}-{e_b}",
                                    snippet=sample[:200],
                                    metrics={"clone_target": f"{f_b}:{s_b}-{e_b}"},
                                )
                            )

        dup_pct = (
            (duplicate_lines_count / total_code_lines * 100.0) if total_code_lines > 0 else 0.0
        )
        duration = time.time() - t0

        if dup_pct > 15.0:
            overall_status = EngineStatus.FAIL
        elif dup_pct > 5.0 or len(targets) > 0:
            overall_status = EngineStatus.WARN if len(targets) > 0 else EngineStatus.PASS
        else:
            overall_status = EngineStatus.PASS

        summary = f"Code Duplication Rate: {dup_pct:.1f}% ({len(targets)} duplicate blocks found)"

        return self.create_result(
            name="dup",
            status=overall_status,
            summary=summary,
            score=dup_pct,
            duration=duration,
            targets=targets,
            extra={"duplication_pct": dup_pct, "metrics_summary": f"Duplication: {dup_pct:.1f}%"},
        )

    def _normalize_line(self, line: str) -> str:
        """Removes spaces and quotes to match structurally identical lines."""
        return "".join(c for c in line if not c.isspace()).strip(";'\"")
