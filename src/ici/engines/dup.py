"""8. Code Clone & Duplication Detection Engine with Type-2 Token Matching."""

import difflib
import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass, replace

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.engines._source_inputs import (
    AnalysisSource,
    AnalysisSourceError,
    AnalysisSourceInventory,
    read_analysis_sources,
)
from ici.engines.base import BaseEngine

LocTuple = tuple[int, int, int]  # (file_idx, start_line, end_line)
MatchPair = tuple[int, int, int, int, int, int, int]  # (f1, s1, e1, f2, s2, e2, k)


@dataclass(frozen=True)
class FileData:
    file_path: str
    language: str
    raw_lines: list[str]
    indexed: list[tuple[int, str]]


_STRUCT_KEYWORDS = {
    "if",
    "elif",
    "else",
    "for",
    "while",
    "return",
    "def",
    "class",
    "try",
    "except",
    "finally",
    "with",
    "as",
    "lambda",
    "yield",
    "import",
    "from",
    "pass",
    "break",
    "continue",
    "raise",
    "assert",
    "global",
    "nonlocal",
    "del",
    "and",
    "or",
    "not",
    "in",
    "is",
    "switch",
    "case",
    "default",
    "do",
    "goto",
    "struct",
    "enum",
    "union",
    "template",
    "typename",
    "namespace",
    "using",
    "public",
    "private",
    "protected",
    "virtual",
    "override",
    "const",
    "static",
    "inline",
    "new",
    "delete",
    "this",
    "true",
    "false",
    "nullptr",
    "None",
    "True",
    "False",
    "catch",
    "throw",
}

_TOKEN_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b")


class DuplicateEngine(BaseEngine):
    """Detects maximal copy-pasted code blocks across files and groups them into unified clusters."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("dup")
        warn_pct = cfg.get("warn_pct", 5.0)
        fail_pct = cfg.get("fail_pct", 15.0)
        window_size = cfg.get("min_window", 6)
        mode = cfg.get("mode", "pass_warn")

        py_sources = self.project_python_sources()
        cpp_sources = self.project_cpp_sources()
        cpp_headers = self.project_cpp_headers() or []
        all_sources = py_sources + cpp_sources + cpp_headers

        try:
            inventory = read_analysis_sources(
                self.project_root,
                all_sources,
                include_generated=cfg.get("include_generated") is True,
                include_vendor=cfg.get("include_vendor") is True,
            )
        except AnalysisSourceError as err:
            duration = time.time() - t0
            return self.create_result(
                name="dup",
                status=EngineStatus.ERROR,
                summary=err.message,
                duration=duration,
                targets=[
                    InspectionTarget(
                        file_path=err.file_path,
                        start_line=1,
                        target_name="SourceInputError",
                        status=EngineStatus.ERROR,
                        message=f"{err.code}: {err.message}",
                    )
                ],
                extra=self._source_evidence(AnalysisSourceInventory((), (), 0)),
                required=bool(cfg.get("required", True)),
                evidence=EvidenceState.NOT_RUN,
            )

        if not inventory.sources:
            duration = time.time() - t0
            return self.create_result(
                name="dup",
                status=EngineStatus.SKIP,
                summary="Duplicate analysis skipped: no owned source files",
                duration=duration,
                targets=[
                    InspectionTarget(
                        file_path=".",
                        start_line=1,
                        target_name="DuplicateCode",
                        status=EngineStatus.SKIP,
                        message=(
                            "All selected sources were excluded by the generated/vendor policy"
                            if inventory.excluded
                            else "No applicable source files were selected"
                        ),
                    )
                ],
                extra=self._source_evidence(inventory),
                required=bool(cfg.get("required", True)),
                evidence=EvidenceState.NOT_APPLICABLE,
            )

        ordered_sources = tuple(sorted(inventory.sources, key=lambda source: source.file_path))
        files_data, total_code_lines = self._load_and_index_files(ordered_sources)
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
        # warn_pct governs. It used to be `or len(clone_groups) > 0`, which made
        # the setting unreachable: any clone at all warned, whatever the rate was
        # configured to allow. A project at 1.8% against a 5% policy was told it
        # had a problem it had explicitly decided not to have. The groups stay in
        # the report either way — see below — so nothing is hidden by this.
        has_warn = dup_pct > warn_pct
        overall_status = self.evaluate_status(has_fail, has_warn, mode)

        # Clone groups are built before the rate is known, so their status is
        # aligned here. Under the policy they stay in the report as findings to
        # read — location, size and snippet intact — without being counted as
        # something to act on; an engine reporting PASS while its own targets say
        # WARN would be the report contradicting itself.
        if not has_warn and not has_fail:
            targets = [
                replace(target, status=EngineStatus.PASS)
                if target.status == EngineStatus.WARN
                else target
                for target in targets
            ]

        represented = {target.file_path for target in targets}
        for file_data in files_data:
            if file_data.file_path in represented:
                continue
            targets.append(
                InspectionTarget(
                    file_path=file_data.file_path,
                    start_line=1,
                    target_name="DuplicateScan",
                    status=EngineStatus.PASS,
                    message="Source was analyzed and has no reported duplicate region",
                )
            )

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
                "fingerprint_algorithm": "sha256/type2-region-v1",
                **self._source_evidence(inventory),
            },
            required=bool(cfg.get("required", True)),
            evidence=EvidenceState.ESTIMATED,
        )

    @staticmethod
    def _source_evidence(inventory: AnalysisSourceInventory) -> dict:
        return {
            "analysis_provenance": "token-region-heuristic",
            "source_files_analyzed": len(inventory.sources),
            "source_bytes_analyzed": inventory.total_bytes,
            "source_files_excluded": len(inventory.excluded),
            "source_exclusion_counts": inventory.exclusion_counts,
        }

    def _load_and_index_files(
        self, all_sources: tuple[AnalysisSource, ...]
    ) -> tuple[list[FileData], int]:
        files_data: list[FileData] = []
        total_code_lines = 0

        for source in all_sources:
            raw_lines = source.lines
            indexed = []
            for idx, r_line in enumerate(raw_lines, 1):
                s = r_line.strip()
                if s and not s.startswith(("#", "//", "/*", "*", "import ", "from ", "#include")):
                    norm = self._tokenize_line(s)
                    if norm:
                        indexed.append((idx, norm))
                        total_code_lines += 1

            files_data.append(FileData(source.file_path, source.language, raw_lines, indexed))

        return files_data, total_code_lines

    def _find_raw_matches(self, files_data: list[FileData], window_size: int) -> list[MatchPair]:
        """Finds Type-1/Type-2 clones via token windows, greedy extension (same-file)
        and gap-tolerant block matching (cross-file)."""
        window_map: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
        for f_idx, file_data in enumerate(files_data):
            indexed = file_data.indexed
            if len(indexed) < window_size:
                continue
            for t_pos in range(len(indexed) - window_size + 1):
                w_str = "\x00".join(indexed[t_pos + k][1] for k in range(window_size))
                w_hash = hashlib.sha256(w_str.encode("utf-8")).hexdigest()
                # Python and C++ may normalize to the same token shapes but are
                # not actionable clones of each other.
                window_map[(file_data.language, w_hash)].append((f_idx, t_pos))

        same_occ: dict[int, list[int]] = defaultdict(list)
        cross_files: set[tuple[int, int]] = set()
        for occs in window_map.values():
            if len(occs) < 2:
                continue
            files_in = {f for f, _ in occs}
            cross_files.update((a, b) for a in files_in for b in files_in if a < b)
            for f, p in occs:
                same_occ[f].append(p)

        raw_matches: list[MatchPair] = []

        # 1. Same-file internal duplication: greedy window extension
        for f_idx, positions in same_occ.items():
            positions = sorted(set(positions))
            if len(positions) < 2:
                continue
            idx = files_data[f_idx].indexed
            matched: set[tuple[int, int]] = set()
            for i in range(len(positions)):
                p1 = positions[i]
                for j in range(i + 1, len(positions)):
                    p2 = positions[j]
                    if p2 - p1 < window_size:
                        continue
                    if (p1, p2) in matched:
                        continue
                    k = 0
                    while p1 + k < p2 and p2 + k < len(idx) and idx[p1 + k][1] == idx[p2 + k][1]:
                        matched.add((p1 + k, p2 + k))
                        k += 1
                    if k >= window_size:
                        raw_matches.append(
                            (
                                f_idx,
                                idx[p1][0],
                                idx[p1 + k - 1][0],
                                f_idx,
                                idx[p2][0],
                                idx[p2 + k - 1][0],
                                k,
                            )
                        )

        # 2. Cross-file duplication: gap-tolerant SequenceMatcher blocks
        for f1, f2 in sorted(cross_files):
            idx1 = files_data[f1].indexed
            idx2 = files_data[f2].indexed
            seq1 = [norm for _, norm in idx1]
            seq2 = [norm for _, norm in idx2]
            sm = difflib.SequenceMatcher(None, seq1, seq2, autojunk=False)
            for block in sm.get_matching_blocks():
                if block.size < window_size:
                    continue
                raw_matches.append(
                    (
                        f1,
                        idx1[block.a][0],
                        idx1[block.a + block.size - 1][0],
                        f2,
                        idx2[block.b][0],
                        idx2[block.b + block.size - 1][0],
                        block.size,
                    )
                )

        return raw_matches

    def _filter_subsumed_matches(self, raw_matches: list[MatchPair]) -> list[MatchPair]:
        """Keeps maximal clones: drops matches overlapping a larger kept match on both sides."""
        raw_matches.sort(key=lambda x: x[6], reverse=True)
        filtered: list[MatchPair] = []

        for match in raw_matches:
            f1, s1, e1, f2, s2, e2, _ = match
            redundant = False
            for pf1, ps1, pe1, pf2, ps2, pe2, _ in filtered:
                if f1 != pf1 or f2 != pf2:
                    continue
                overlaps_1 = not (e1 < ps1 or s1 > pe1)
                overlaps_2 = not (e2 < ps2 or s2 > pe2)
                if overlaps_1 and overlaps_2:
                    redundant = True
                    break
            if not redundant:
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
        for component in clusters:
            component.sort(key=lambda loc: (files_data[loc[0]].file_path, loc[1], loc[2]))
        clusters.sort(
            key=lambda component: (
                -max(match_len_map.get(loc, 0) for loc in component),
                -len(component),
                tuple((files_data[loc[0]].file_path, loc[1], loc[2]) for loc in component),
            )
        )

        clone_groups: list[dict] = []
        targets: list[InspectionTarget] = []
        duplicated_positions: set[tuple[int, int]] = set()
        # Only lines the denominator also counted may go into the numerator.
        # total_code_lines excludes blanks, comments and import lines, while a
        # clone span covers every physical line between its endpoints — counting
        # those against each other is how the "rate" could read above 100%.
        code_lines_by_file = [
            {line_no for line_no, _ in file_data.indexed} for file_data in files_data
        ]

        for group_idx, component in enumerate(clusters, 1):
            # Sort occurrences inside component by (file_path, start_line)
            rep_f, rep_s, rep_e = min(
                component,
                key=lambda loc: (
                    -match_len_map.get(loc, 0),
                    files_data[loc[0]].file_path,
                    loc[1],
                    loc[2],
                ),
            )
            representative = files_data[rep_f]
            rep_raw = representative.raw_lines
            lines_k = max(match_len_map.get(loc, 0) for loc in component)
            normalized_region = "\n".join(
                normalized
                for line_no, normalized in representative.indexed
                if rep_s <= line_no <= rep_e
            )
            fingerprint = hashlib.sha256(
                f"{representative.language}\0{normalized_region}".encode()
            ).hexdigest()

            # Preserve exact raw indentation (do not strip leading whitespace of line 1!)
            raw_snippet = "".join(rep_raw[rep_s - 1 : rep_e]).rstrip()
            for occ_idx, (f_idx, s_l, e_l) in enumerate(component):
                if occ_idx > 0:
                    counted = code_lines_by_file[f_idx]
                    for line_no in range(s_l, e_l + 1):
                        if line_no in counted:
                            duplicated_positions.add((f_idx, line_no))

            occ_list = []
            for f_idx, s_l, e_l in component:
                f_path = files_data[f_idx].file_path
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
                        metrics={
                            "clone_group": group_idx,
                            "duplicate_lines": lines_k,
                            "fingerprint": fingerprint,
                        },
                    )
                )

            clone_groups.append(
                {
                    "id": group_idx,
                    "fingerprint": fingerprint,
                    "language": representative.language,
                    "lines_count": lines_k,
                    "occurrences_count": len(occ_list),
                    "occurrences": occ_list,
                    "snippet": raw_snippet,
                }
            )

        return clone_groups, targets, len(duplicated_positions)

    def _tokenize_line(self, line: str) -> str:
        """Normalizes identifiers to ID and literals to LIT (Type-2 clone support)."""

        def _repl(match: re.Match) -> str:
            tok = match.group(0)
            if tok.startswith(("'", '"')):
                return "LIT"
            if tok[0].isdigit():
                return "LIT"
            return tok if tok in _STRUCT_KEYWORDS else "ID"

        tokenized = _TOKEN_RE.sub(_repl, line)
        return "".join(c for c in tokenized if not c.isspace())
