"""8. Code Clone & Duplication Detection Engine with Clean Modular Helpers."""

import hashlib
import time
from collections import defaultdict
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    get_all_cpp_sources,
    get_all_python_sources,
)
from ici.engines.base import BaseEngine

FileData = tuple[str, list[str], list[tuple[int, str]]]
CloneTuple = tuple[int, int, int, int, int, int, int]


class DuplicateEngine(BaseEngine):
    """Detects maximal copy-pasted code blocks across files with raw formatting preservation."""

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

        files_data, total_code_lines = self._load_and_index_files(all_sources)
        raw_clones = self._find_raw_clones(files_data, window_size)
        filtered_clones = self._filter_subsumed_clones(raw_clones)
        clone_groups, targets, duplicate_lines_count = self._assemble_groups(
            filtered_clones, files_data
        )

        dup_pct = (
            (duplicate_lines_count / total_code_lines * 100.0) if total_code_lines > 0 else 0.0
        )
        duration = time.time() - t0

        has_fail = dup_pct > fail_pct
        has_warn = dup_pct > warn_pct or len(clone_groups) > 0
        overall_status = self.evaluate_status(has_fail, has_warn, mode)

        summary = f"Code Duplication Rate: {dup_pct:.1f}% ({len(clone_groups)} distinct clone groups found)"

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

    def _load_and_index_files(self, all_sources: list[Path]) -> tuple[list[FileData], int]:
        files_data: list[FileData] = []
        total_code_lines = 0

        for src_file in all_sources:
            try:
                rel_p = str(src_file.relative_to(self.project_root))
                with open(src_file, encoding="utf-8", errors="ignore") as f:
                    raw_lines = f.readlines()

                indexed = []
                for idx, r_line in enumerate(raw_lines, 1):
                    s = r_line.strip()
                    if s and not s.startswith(
                        ("#", "//", "/*", "*", "import ", "from ", "#include")
                    ):
                        norm = self._normalize_line(s)
                        if norm:
                            indexed.append((idx, norm))
                            total_code_lines += 1

                files_data.append((rel_p, raw_lines, indexed))
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        return files_data, total_code_lines

    def _find_raw_clones(self, files_data: list[FileData], window_size: int) -> list[CloneTuple]:
        window_map: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for f_idx, (_, _, indexed) in enumerate(files_data):
            for t_pos in range(len(indexed) - window_size + 1):
                w_str = "".join(indexed[t_pos + k][1] for k in range(window_size))
                w_hash = hashlib.sha256(w_str.encode("utf-8")).hexdigest()
                window_map[w_hash].append((f_idx, t_pos))

        matched_pairs: set[tuple[int, int, int, int]] = set()
        raw_clones: list[CloneTuple] = []

        for occs in window_map.values():
            if len(occs) < 2:
                continue
            for i in range(len(occs)):
                f1, p1 = occs[i]
                for j in range(i + 1, len(occs)):
                    f2, p2 = occs[j]
                    if (f1, p1, f2, p2) in matched_pairs:
                        continue

                    _, _, idx1 = files_data[f1]
                    _, _, idx2 = files_data[f2]
                    k = 0
                    while (
                        p1 + k < len(idx1)
                        and p2 + k < len(idx2)
                        and idx1[p1 + k][1] == idx2[p2 + k][1]
                    ):
                        matched_pairs.add((f1, p1 + k, f2, p2 + k))
                        k += 1

                    if k >= window_size:
                        s1 = idx1[p1][0]
                        e1 = idx1[p1 + k - 1][0]
                        s2 = idx2[p2][0]
                        e2 = idx2[p2 + k - 1][0]
                        raw_clones.append((f1, s1, e1, f2, s2, e2, k))

        return raw_clones

    def _filter_subsumed_clones(self, raw_clones: list[CloneTuple]) -> list[CloneTuple]:
        raw_clones.sort(key=lambda x: x[6], reverse=True)
        filtered_clones: list[CloneTuple] = []

        for clone in raw_clones:
            f1, s1, e1, f2, s2, e2, _ = clone
            is_subsumed = any(
                f1 == pf1 and f2 == pf2 and ps1 <= s1 and pe1 >= e1 and ps2 <= s2 and pe2 >= e2
                for pf1, ps1, pe1, pf2, ps2, pe2, _ in filtered_clones
            )
            if not is_subsumed:
                filtered_clones.append(clone)

        return filtered_clones

    def _assemble_groups(
        self, filtered_clones: list[CloneTuple], files_data: list[FileData]
    ) -> tuple[list[dict], list[InspectionTarget], int]:
        clone_groups: list[dict] = []
        targets: list[InspectionTarget] = []
        duplicate_lines_count = 0

        for group_idx, (f1, s1, e1, f2, s2, e2, k) in enumerate(filtered_clones, 1):
            file1_path, file1_raw, _ = files_data[f1]
            file2_path, _, _ = files_data[f2]

            raw_snippet = "".join(file1_raw[s1 - 1 : e1])
            duplicate_lines_count += k

            occ_list = [
                {
                    "file_path": file1_path,
                    "start_line": s1,
                    "end_line": e1,
                    "loc": f"{file1_path}:{s1}-{e1}",
                },
                {
                    "file_path": file2_path,
                    "start_line": s2,
                    "end_line": e2,
                    "loc": f"{file2_path}:{s2}-{e2}",
                },
            ]

            clone_groups.append(
                {
                    "id": group_idx,
                    "lines_count": k,
                    "occurrences_count": 2,
                    "occurrences": occ_list,
                    "snippet": raw_snippet.strip(),
                }
            )

            targets.append(
                InspectionTarget(
                    file_path=file1_path,
                    start_line=s1,
                    end_line=e1,
                    target_name=f"CloneGroup#{group_idx}",
                    status=EngineStatus.WARN,
                    message=f"Duplicate code block ({k} lines) shared between {file1_path} and {file2_path}",
                    snippet=raw_snippet[:300],
                    metrics={"clone_group": group_idx, "duplicate_lines": k},
                )
            )
            targets.append(
                InspectionTarget(
                    file_path=file2_path,
                    start_line=s2,
                    end_line=e2,
                    target_name=f"CloneGroup#{group_idx}",
                    status=EngineStatus.WARN,
                    message=f"Duplicate code block ({k} lines) shared between {file1_path} and {file2_path}",
                    snippet=raw_snippet[:300],
                    metrics={"clone_group": group_idx, "duplicate_lines": k},
                )
            )

        return clone_groups, targets, duplicate_lines_count

    def _normalize_line(self, line: str) -> str:
        """Removes spaces and quotes to match structurally identical lines."""
        return "".join(c for c in line if not c.isspace()).strip(";'\"")
