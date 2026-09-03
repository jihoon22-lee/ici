"""8. Code Clone & Duplication Detection Engine with Type-2 Token Matching."""

import hashlib
import time
from collections import Counter, defaultdict
from dataclasses import replace

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.engines._cpp_dup_tokenization import tokenize_cpp_lines
from ici.engines._dup_matching import (
    DuplicateComparisonLimit,
    DuplicateFileData,
    DuplicateMatchLimits,
    MatchPair,
    filter_subsumed_matches,
    find_raw_matches,
)
from ici.engines._dup_regions import cpp_duplicate_regions, python_duplicate_regions
from ici.engines._python_dup_semantics import (
    SEMANTIC_SHAPE_ALGORITHM,
    SEMANTIC_SHAPE_VERSION,
    SemanticAnalysisOutcome,
    SemanticExclusion,
    SemanticRegion,
    analyze_python_sources,
)
from ici.engines._python_dup_tokenization import tokenize_python_lines
from ici.engines._source_inputs import (
    AnalysisSource,
    AnalysisSourceError,
    AnalysisSourceInventory,
    read_analysis_sources,
)
from ici.engines.base import BaseEngine

LocTuple = tuple[int, int, int]  # (file_idx, start_line, end_line)

MAX_DUPLICATE_NORMALIZED_CHARS = 128 * 1024 * 1024
MAX_DUPLICATE_INDEXED_RECORDS = 500_000
MAX_DUPLICATE_WINDOW_OCCURRENCES = 2_048
MAX_DUPLICATE_SAME_FILE_SEED_PAIRS = 100_000
MAX_DUPLICATE_CROSS_FILE_PAIRS = 20_000
MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS = 250_000
MAX_DUPLICATE_EXTENSION_COMPARISONS = 5_000_000
MAX_DUPLICATE_RAW_MATCHES = 10_000
_FINGERPRINT_ALGORITHM = "sha256/type2-region-v2"
_SEMANTIC_POLICY = "python-bounded-ast-shape-v1"
_REGION_POLICY = "language-function-scope-v1"
_SIGNAL_POLICY = "minimum-semantic-lines-v1"
_TOKENIZER_VERSIONS = {
    "cpp": "cpp-lexical-v1",
    "python": "python-lexical-v1",
}


class _SourceTokenizationError(ValueError):
    """One source failed lexical normalization or its resource budget."""

    def __init__(self, file_path: str, message: str) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.message = message


FileData = DuplicateFileData


class DuplicateEngine(BaseEngine):
    """Detects maximal copy-pasted code blocks across files and groups them into unified clusters."""

    CACHE_IMPLEMENTATION_MODULES = (
        "ici.core._compile_db_paths",
        "ici.core.project",
        "ici.engines._cpp_dup_tokenization",
        "ici.engines._dup_matching",
        "ici.engines._dup_regions",
        "ici.engines._dup_signal",
        "ici.engines._python_dup_tokenization",
        "ici.engines._python_dup_semantics",
        "ici.engines._source_inputs",
        "ici.engines.base",
        "ici.engines.complexity",
        "ici.engines.cpp_text",
        "ici.engines.dup",
    )

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("dup")
        warn_pct = cfg.get("warn_pct", 5.0)
        fail_pct = cfg.get("fail_pct", 15.0)
        window_size = cfg.get("min_window", 6)
        python_semantic_policy = cfg.get("python_semantic", "auto")
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
        try:
            files_data, total_code_lines = self._load_and_index_files(ordered_sources)
        except _SourceTokenizationError as err:
            duration = time.time() - t0
            return self.create_result(
                name="dup",
                status=EngineStatus.ERROR,
                summary=f"Duplicate analysis could not normalize {err.file_path}",
                duration=duration,
                targets=[
                    InspectionTarget(
                        file_path=err.file_path,
                        start_line=1,
                        target_name="SourceTokenizationError",
                        status=EngineStatus.ERROR,
                        message=err.message,
                    )
                ],
                extra=self._source_evidence(inventory),
                required=bool(cfg.get("required", True)),
                evidence=EvidenceState.NOT_RUN,
            )

        python_source_texts = {
            source.file_path: source.text
            for source in ordered_sources
            if source.language == "python"
        }
        semantic_outcome: SemanticAnalysisOutcome | None = None
        if python_semantic_policy != "off" and python_source_texts:
            semantic_outcome = analyze_python_sources(tuple(python_source_texts.items()))
            if python_semantic_policy == "required" and semantic_outcome.exclusions:
                duration = time.time() - t0
                return self.create_result(
                    name="dup",
                    status=EngineStatus.ERROR,
                    summary="Required Python AST-shape duplicate analysis was incomplete",
                    duration=duration,
                    targets=self._semantic_error_targets(semantic_outcome.exclusions),
                    extra={
                        **self._source_evidence(inventory),
                        **self._semantic_evidence(
                            semantic_outcome,
                            policy=python_semantic_policy,
                            eligible_regions=0,
                            trivial_regions_excluded=0,
                            parent_regions_excluded=0,
                            groups_suppressed=0,
                            groups_reported=0,
                        ),
                    },
                    required=bool(cfg.get("required", True)),
                    evidence=EvidenceState.NOT_RUN,
                )
        try:
            raw_matches = self._find_raw_matches(files_data, window_size)
            filtered_matches = self._filter_subsumed_matches(raw_matches)
            clone_groups, targets, duplicated_positions = self._cluster_matches(
                filtered_matches, files_data
            )
        except DuplicateComparisonLimit as err:
            duration = time.time() - t0
            return self.create_result(
                name="dup",
                status=EngineStatus.ERROR,
                summary="Duplicate analysis exceeded a bounded comparison limit",
                duration=duration,
                targets=[
                    InspectionTarget(
                        file_path=err.file_path,
                        start_line=1,
                        target_name="DuplicateComparisonLimit",
                        status=EngineStatus.ERROR,
                        message=err.message,
                    )
                ],
                extra=self._source_evidence(inventory),
                required=bool(cfg.get("required", True)),
                evidence=EvidenceState.NOT_RUN,
            )

        semantic_groups_count = 0
        semantic_eligible_regions = 0
        semantic_trivial_excluded = 0
        semantic_parent_excluded = 0
        semantic_groups_suppressed = 0
        if semantic_outcome is not None:
            (
                semantic_groups,
                semantic_targets,
                semantic_positions,
                semantic_eligible_regions,
                semantic_trivial_excluded,
                semantic_parent_excluded,
                semantic_groups_suppressed,
            ) = self._cluster_python_semantic_regions(
                semantic_outcome.regions,
                files_data,
                clone_groups,
                window_size,
            )
            semantic_groups_count = len(semantic_groups)
            clone_groups.extend(semantic_groups)
            targets.extend(semantic_targets)
            duplicated_positions.update(semantic_positions)

        duplicate_lines_count = len(duplicated_positions)

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

        summary = (
            f"Code Duplication Rate: {dup_pct:.1f}% "
            f"({len(clone_groups)} distinct clone groups found, "
            f"{semantic_groups_count} from Python AST shapes)"
        )

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
                "fingerprint_algorithm": _FINGERPRINT_ALGORITHM,
                "fingerprint_algorithms": [
                    _FINGERPRINT_ALGORITHM,
                    *(
                        [SEMANTIC_SHAPE_ALGORITHM]
                        if python_semantic_policy != "off" and python_source_texts
                        else []
                    ),
                ],
                "tokenizer_versions": dict(_TOKENIZER_VERSIONS),
                "region_policy": _REGION_POLICY,
                "signal_policy": _SIGNAL_POLICY,
                **self._semantic_evidence(
                    semantic_outcome,
                    policy=python_semantic_policy,
                    eligible_regions=semantic_eligible_regions,
                    trivial_regions_excluded=semantic_trivial_excluded,
                    parent_regions_excluded=semantic_parent_excluded,
                    groups_suppressed=semantic_groups_suppressed,
                    groups_reported=semantic_groups_count,
                ),
                **self._source_evidence(inventory),
            },
            required=bool(cfg.get("required", True)),
            evidence=EvidenceState.ESTIMATED,
        )

    @staticmethod
    def _source_evidence(inventory: AnalysisSourceInventory) -> dict:
        return {
            "analysis_provenance": "language-lexical-region-heuristic",
            "source_files_analyzed": len(inventory.sources),
            "source_bytes_analyzed": inventory.total_bytes,
            "source_files_excluded": len(inventory.excluded),
            "source_exclusion_counts": inventory.exclusion_counts,
        }

    @staticmethod
    def _semantic_evidence(
        outcome: SemanticAnalysisOutcome | None,
        *,
        policy: str,
        eligible_regions: int,
        trivial_regions_excluded: int,
        parent_regions_excluded: int,
        groups_suppressed: int,
        groups_reported: int,
    ) -> dict:
        if outcome is None:
            mode = "off" if policy == "off" else "not-applicable"
            exclusion_counts: dict[str, int] = {}
            region_count = 0
            node_count = 0
            serialized_chars = 0
        else:
            exclusion_counts = dict(
                sorted(Counter(item.reason for item in outcome.exclusions).items())
            )
            region_count = len(outcome.regions)
            node_count = outcome.node_count
            serialized_chars = outcome.serialized_chars
            if outcome.exclusions and outcome.regions:
                mode = "partial"
            elif outcome.exclusions:
                mode = "unavailable"
            else:
                mode = "bounded"
        return {
            "python_semantic_policy": policy,
            "python_semantic_mode": mode,
            "python_semantic_shape_policy": _SEMANTIC_POLICY,
            "python_semantic_fingerprint_algorithm": SEMANTIC_SHAPE_ALGORITHM,
            "python_semantic_shape_version": SEMANTIC_SHAPE_VERSION,
            "python_semantic_regions_observed": region_count,
            "python_semantic_regions_eligible": eligible_regions,
            "python_semantic_trivial_regions_excluded": trivial_regions_excluded,
            "python_semantic_parent_regions_excluded": parent_regions_excluded,
            "python_semantic_exclusions": sum(exclusion_counts.values()),
            "python_semantic_exclusion_counts": exclusion_counts,
            "python_semantic_groups_suppressed": groups_suppressed,
            "python_semantic_groups_reported": groups_reported,
            "python_semantic_nodes_observed": node_count,
            "python_semantic_serialized_chars": serialized_chars,
        }

    @staticmethod
    def _semantic_error_targets(
        exclusions: tuple[SemanticExclusion, ...],
    ) -> list[InspectionTarget]:
        targets: list[InspectionTarget] = []
        for exclusion in exclusions:
            file_path = exclusion.file_path
            if file_path == "<sources>":
                file_path = "."
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=exclusion.start_line or 1,
                    end_line=exclusion.end_line,
                    target_name="PythonSemanticShapeError",
                    status=EngineStatus.ERROR,
                    message=(
                        f"{exclusion.reason}: {exclusion.message}"
                        + (
                            f" ({exclusion.region_name})"
                            if exclusion.region_name is not None
                            else ""
                        )
                    ),
                )
            )
        return targets

    @staticmethod
    def _nested_callable_keys(regions: tuple[SemanticRegion, ...]) -> set[tuple]:
        """Return callable regions whose contiguous span contains a named child.

        Parent shapes deliberately prune nested named scopes. Reporting their
        whole physical span would therefore attribute child lines to a shape
        which did not inspect them. Only leaf callables are eligible.
        """

        nested_parents: set[tuple] = set()
        by_file: dict[str, list[SemanticRegion]] = defaultdict(list)
        for region in regions:
            by_file[region.file_path].append(region)
        for file_regions in by_file.values():
            stack: list[SemanticRegion] = []
            for region in sorted(
                file_regions,
                key=lambda item: (item.start_line, -item.end_line, item.name, item.kind),
            ):
                while stack and region.start_line > stack[-1].end_line:
                    stack.pop()
                for parent in reversed(stack):
                    if (
                        parent.kind in {"function", "method"}
                        and parent.start_line <= region.start_line
                        and region.end_line <= parent.end_line
                    ):
                        nested_parents.add(
                            (parent.file_path, parent.name, parent.start_line, parent.end_line)
                        )
                        break
                stack.append(region)
        return nested_parents

    @classmethod
    def _cluster_python_semantic_regions(
        cls,
        regions: tuple[SemanticRegion, ...],
        files_data: list[FileData],
        lexical_groups: list[dict],
        min_code_lines: int,
    ) -> tuple[
        list[dict],
        list[InspectionTarget],
        set[tuple[int, int]],
        int,
        int,
        int,
        int,
    ]:
        file_indexes = {item.file_path: index for index, item in enumerate(files_data)}
        code_lines = [{line_no for line_no, _normalized in item.indexed} for item in files_data]
        nested_parents = cls._nested_callable_keys(regions)
        parent_excluded = 0
        trivial_excluded = 0
        buckets: dict[tuple[str, str], list[tuple[SemanticRegion, int, int]]] = defaultdict(list)
        for region in regions:
            if region.kind not in {"function", "method"}:
                continue
            key = (region.file_path, region.name, region.start_line, region.end_line)
            if key in nested_parents:
                parent_excluded += 1
                continue
            file_index = file_indexes.get(region.file_path)
            if file_index is None:
                continue
            counted_lines = sum(
                region.start_line <= line_no <= region.end_line
                for line_no in code_lines[file_index]
            )
            if counted_lines < min_code_lines:
                trivial_excluded += 1
                continue
            buckets[(region.fingerprint, region.canonical_shape)].append(
                (region, file_index, counted_lines)
            )

        existing_occurrences = {
            frozenset(
                (
                    occurrence.get("file_path"),
                    occurrence.get("start_line"),
                    occurrence.get("end_line"),
                )
                for occurrence in group.get("occurrences", [])
                if isinstance(occurrence, dict)
            )
            for group in lexical_groups
            if isinstance(group, dict)
        }
        candidates: list[list[tuple[SemanticRegion, int, int]]] = []
        suppressed = 0
        for occurrences in buckets.values():
            if len(occurrences) < 2:
                continue
            occurrences.sort(
                key=lambda item: (
                    item[0].file_path,
                    item[0].start_line,
                    item[0].end_line,
                    item[0].name,
                )
            )
            occurrence_key = frozenset(
                (item.file_path, item.start_line, item.end_line)
                for item, _file_index, _count in occurrences
            )
            if occurrence_key in existing_occurrences:
                suppressed += 1
                continue
            candidates.append(occurrences)
        candidates.sort(
            key=lambda items: (
                -max(item[2] for item in items),
                -len(items),
                tuple((item[0].file_path, item[0].start_line, item[0].end_line) for item in items),
            )
        )

        groups: list[dict] = []
        targets: list[InspectionTarget] = []
        duplicated_positions: set[tuple[int, int]] = set()
        next_id = len(lexical_groups) + 1
        for offset, occurrences in enumerate(candidates):
            group_id = next_id + offset
            representative, representative_index, _count = occurrences[0]
            lines_count = min(item[2] for item in occurrences)
            raw_lines = files_data[representative_index].raw_lines
            snippet = "".join(
                raw_lines[representative.start_line - 1 : representative.end_line]
            ).rstrip()
            serialized_occurrences: list[dict] = []
            for occurrence_index, (region, file_index, _line_count) in enumerate(occurrences):
                serialized_occurrences.append(
                    {
                        "file_path": region.file_path,
                        "start_line": region.start_line,
                        "end_line": region.end_line,
                        "loc": f"{region.file_path}:{region.start_line}-{region.end_line}",
                        "region_name": region.name,
                        "region_kind": region.kind,
                    }
                )
                if occurrence_index > 0:
                    duplicated_positions.update(
                        (file_index, line_no)
                        for line_no in code_lines[file_index]
                        if region.start_line <= line_no <= region.end_line
                    )
                targets.append(
                    InspectionTarget(
                        file_path=region.file_path,
                        start_line=region.start_line,
                        end_line=region.end_line,
                        target_name=f"CloneGroup#{group_id}",
                        status=EngineStatus.WARN,
                        message=(
                            "Equivalent bounded Python AST shape shared across "
                            f"{len(occurrences)} callable regions"
                        ),
                        snippet=snippet[:300],
                        metrics={
                            "clone_group": group_id,
                            "duplicate_lines": lines_count,
                            "fingerprint": representative.fingerprint,
                            "fingerprint_algorithm": SEMANTIC_SHAPE_ALGORITHM,
                            "shape_policy": _SEMANTIC_POLICY,
                        },
                    )
                )
            groups.append(
                {
                    "id": group_id,
                    "fingerprint": representative.fingerprint,
                    "fingerprint_algorithm": SEMANTIC_SHAPE_ALGORITHM,
                    "detection": "python-ast-semantic-shape",
                    "shape_policy": _SEMANTIC_POLICY,
                    "language": "python",
                    "lines_count": lines_count,
                    "occurrences_count": len(serialized_occurrences),
                    "occurrences": serialized_occurrences,
                    "snippet": snippet,
                }
            )

        eligible_regions = sum(len(items) for items in buckets.values())
        return (
            groups,
            targets,
            duplicated_positions,
            eligible_regions,
            trivial_excluded,
            parent_excluded,
            suppressed,
        )

    def _load_and_index_files(
        self,
        all_sources: tuple[AnalysisSource, ...],
        *,
        max_normalized_chars: int | None = None,
        max_indexed_records: int | None = None,
    ) -> tuple[list[FileData], int]:
        normalized_limit = (
            MAX_DUPLICATE_NORMALIZED_CHARS if max_normalized_chars is None else max_normalized_chars
        )
        if type(normalized_limit) is not int or normalized_limit <= 0:
            raise ValueError("max_normalized_chars must be a positive integer")
        record_limit = (
            MAX_DUPLICATE_INDEXED_RECORDS if max_indexed_records is None else max_indexed_records
        )
        if type(record_limit) is not int or record_limit <= 0:
            raise ValueError("max_indexed_records must be a positive integer")

        files_data: list[FileData] = []
        total_code_lines = 0
        normalized_chars = 0
        indexed_records = 0

        for source in all_sources:
            raw_lines = source.lines
            try:
                if source.language == "cpp":
                    indexed = list(tokenize_cpp_lines(source.text))
                    regions = cpp_duplicate_regions(
                        source.text,
                        (line for line, _tokens in indexed),
                    )
                else:
                    indexed = list(tokenize_python_lines(source.text))
                    regions = python_duplicate_regions(
                        source.text,
                        (line for line, _tokens in indexed),
                    )
            except ValueError as err:
                raise _SourceTokenizationError(
                    source.file_path,
                    f"Source lexical normalization failed: {err}",
                ) from err

            normalized_chars += sum(len(tokens) for _line, tokens in indexed)
            if normalized_chars > normalized_limit:
                raise _SourceTokenizationError(
                    source.file_path,
                    "Normalized duplicate-analysis input exceeds "
                    f"max_normalized_chars={normalized_limit}",
                )
            indexed_records += len(indexed)
            if indexed_records > record_limit:
                raise _SourceTokenizationError(
                    source.file_path,
                    "Normalized duplicate-analysis input exceeds "
                    f"MAX_DUPLICATE_INDEXED_RECORDS={record_limit}",
                )
            total_code_lines += len(indexed)

            files_data.append(
                FileData(source.file_path, source.language, raw_lines, indexed, regions)
            )

        return files_data, total_code_lines

    def _find_raw_matches(self, files_data: list[FileData], window_size: int) -> list[MatchPair]:
        """Find exact Type-2 clones through bounded shared-window seed extension."""

        limits = DuplicateMatchLimits(
            window_occurrences=MAX_DUPLICATE_WINDOW_OCCURRENCES,
            same_file_seed_pairs=MAX_DUPLICATE_SAME_FILE_SEED_PAIRS,
            cross_file_pairs=MAX_DUPLICATE_CROSS_FILE_PAIRS,
            cross_file_seed_pairs=MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS,
            extension_comparisons=MAX_DUPLICATE_EXTENSION_COMPARISONS,
            raw_matches=MAX_DUPLICATE_RAW_MATCHES,
        )
        return find_raw_matches(files_data, window_size, limits)

    def _filter_subsumed_matches(self, raw_matches: list[MatchPair]) -> list[MatchPair]:
        return filter_subsumed_matches(raw_matches)

    def _cluster_matches(
        self, matches: list[MatchPair], files_data: list[FileData]
    ) -> tuple[list[dict], list[InspectionTarget], set[tuple[int, int]]]:
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
                f"{_FINGERPRINT_ALGORITHM}\0{representative.language}\0{normalized_region}".encode()
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
                            "fingerprint_algorithm": _FINGERPRINT_ALGORITHM,
                        },
                    )
                )

            clone_groups.append(
                {
                    "id": group_idx,
                    "fingerprint": fingerprint,
                    "fingerprint_algorithm": _FINGERPRINT_ALGORITHM,
                    "detection": "type2-lexical-region",
                    "language": representative.language,
                    "lines_count": lines_k,
                    "occurrences_count": len(occ_list),
                    "occurrences": occ_list,
                    "snippet": raw_snippet,
                }
            )

        return clone_groups, targets, duplicated_positions
