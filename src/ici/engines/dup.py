"""8. Code Clone & Duplication Detection Engine with Connected Cluster Merging."""

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
LocTuple = tuple[int, int, int]  # (file_idx, start_line, end_line)
MatchPair = tuple[int, int, int, int, int, int, int]  # (f1, s1, e1, f2, s2, e2, k)


class DuplicateEngine(BaseEngine):
    """Detects maximal copy-pasted code blocks across files and groups them into unified clusters."""

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
        raw_matches = self._find_raw_matches(files_data, window_size)
        filtered_matches = self._filter_subsumed_matches(raw_matches)
        clone_groups, targets, duplicate_lines_count = self._cluster_matches(
            filtered_matches, files_data
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

    def _find_raw_matches(self, files_data: list[FileData], window_size: int) -> list[MatchPair]:
        window_map: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for f_idx, (_, _, indexed) in enumerate(files_data):
            for t_pos in range(len(indexed) - window_size + 1):
                w_str = "".join(indexed[t_pos + k][1] for k in range(window_size))
                w_hash = hashlib.sha256(w_str.encode("utf-8")).hexdigest()
                window_map[w_hash].append((f_idx, t_pos))

        matched_pairs: set[tuple[int, int, int, int]] = set()
        raw_matches: list[MatchPair] = []

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
                        raw_matches.append((f1, s1, e1, f2, s2, e2, k))

        return raw_matches

    def _filter_subsumed_matches(self, raw_matches: list[MatchPair]) -> list[MatchPair]:
        raw_matches.sort(key=lambda x: x[6], reverse=True)
        filtered: list[MatchPair] = []

        for match in raw_matches:
            f1, s1, e1, f2, s2, e2, _ = match
            is_subsumed = any(
                f1 == pf1 and f2 == pf2 and ps1 <= s1 and pe1 >= e1 and ps2 <= s2 and pe2 >= e2
                for pf1, ps1, pe1, pf2, ps2, pe2, _ in filtered
            )
            if not is_subsumed:
                filtered.append(match)

        return filtered

    def _cluster_matches(
        self, matches: list[MatchPair], files_data: list[FileData]
    ) -> tuple[list[dict], list[InspectionTarget], int]:
        """Clusters pairwise matches into unified multi-occurrence connected components."""
        # 1. Build adjacency graph between location nodes (f_idx, start_line, end_line)
        adj: dict[LocTuple, set[LocTuple]] = defaultdict(set)
        match_len_map: dict[LocTuple, int] = {}

        for f1, s1, e1, f2, s2, e2, k in matches:
            loc1 = (f1, s1, e1)
            loc2 = (f2, s2, e2)
            adj[loc1].add(loc2)
            adj[loc2].add(loc1)
            match_len_map[loc1] = max(match_len_map.get(loc1, 0), k)
            match_len_map[loc2] = max(match_len_map.get(loc2, 0), k)

        # 2. Find Connected Components
        visited: set[LocTuple] = set()
        clusters: list[list[LocTuple]] = []

        for node in sorted(adj.keys()):
            if node not in visited:
                component: list[LocTuple] = []
                queue = [node]
                visited.add(node)
                while queue:
                    curr = queue.pop()
                    component.append(curr)
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                clusters.append(component)

        # 3. Sort clusters by match size descending
        clusters.sort(
            key=lambda c: (max(match_len_map.get(loc, 0) for loc in c), len(c)),
            reverse=True,
        )

        clone_groups: list[dict] = []
        targets: list[InspectionTarget] = []
        duplicate_lines_count = 0

        for group_idx, component in enumerate(clusters, 1):
            # Sort occurrences inside component by (file_path, start_line)
            component.sort(key=lambda loc: (files_data[loc[0]][0], loc[1]))

            rep_f, rep_s, rep_e = component[0]
            _, rep_raw, _ = files_data[rep_f]
            lines_k = max(match_len_map.get(loc, 0) for loc in component)

            # Preserve exact raw indentation (do not strip leading whitespace of line 1!)
            raw_snippet = "".join(rep_raw[rep_s - 1 : rep_e]).rstrip()
            duplicate_lines_count += lines_k * (len(component) - 1)

            occ_list = []
            for f_idx, s_l, e_l in component:
                f_path = files_data[f_idx][0]
                occ_list.append(
                    {
                        "file_path": f_path,
                        "start_line": s_l,
                        "end_line": e_l,
                        "loc": f"{f_path}:{s_l}-{e_l}",
                    }
                )
                targets.append(
                    InspectionTarget(
                        file_path=f_path,
                        start_line=s_l,
                        end_line=e_l,
                        target_name=f"CloneGroup#{group_idx}",
                        status=EngineStatus.WARN,
                        message=f"Duplicate code block ({lines_k} lines) shared across {len(component)} locations",
                        snippet=raw_snippet[:300],
                        metrics={"clone_group": group_idx, "duplicate_lines": lines_k},
                    )
                )

            clone_groups.append(
                {
                    "id": group_idx,
                    "lines_count": lines_k,
                    "occurrences_count": len(occ_list),
                    "occurrences": occ_list,
                    "snippet": raw_snippet,
                }
            )

        return clone_groups, targets, duplicate_lines_count

    def _normalize_line(self, line: str) -> str:
        """Removes spaces and quotes to match structurally identical lines."""
        return "".join(c for c in line if not c.isspace()).strip(";'\"")
