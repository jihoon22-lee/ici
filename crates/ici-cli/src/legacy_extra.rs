//! Frozen engine `extra` keys every v0.13.0 engine wrote unconditionally (engines/*.py
//! `create_result(extra=...)`) that the native checks do not carry themselves. Each value is
//! either a constant of the frozen engine, the engine's configured policy, a count over the
//! engine's own (already shaped) targets, or a measured value a native check recorded; nothing
//! here estimates a measurement the native run did not make (the frozen coverage estimates of a
//! project without tests stay absent, see `tests/legacy_engine_extras.rs`).
use serde_json::{Map, Value, json};

/// Frozen `_TOKENIZER_VERSIONS`, `_REGION_POLICY`, `_SIGNAL_POLICY` (engines/dup.py) and the
/// semantic-shape constants of `analysis/_python_semantic.py`.
const TOKENIZER_VERSIONS: [(&str, &str); 2] =
    [("cpp", "cpp-lexical-v1"), ("python", "python-lexical-v1")];
const REGION_POLICY: &str = "language-function-scope-v1";
const SIGNAL_POLICY: &str = "minimum-semantic-lines-v1";
const TYPE2_ALGORITHM: &str = "sha256/type2-region-v2";
const SEMANTIC_ALGORITHM: &str = "sha256/semantic-shape-v1";
const SEMANTIC_SHAPE_POLICY: &str = "python-bounded-ast-shape-v1";
const SEMANTIC_SHAPE_VERSION: &str = "semantic-shape-v1";

/// Inputs of the completion: the engine's `[engines.<family>]` table and the profile name.
pub(crate) struct Context<'a> {
    pub settings: &'a Value,
    pub profile: &'a str,
}

fn checks(engine: &Value) -> Vec<Value> {
    engine["extra"]["native_checks"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}

/// A native check's own extra block (direct, or under its task id when merged).
fn check_blocks(check: &Value) -> Vec<&Value> {
    let extra = &check["extra"];
    let Some(fields) = extra.as_object() else {
        return vec![];
    };
    let nested: Vec<&Value> = fields
        .iter()
        .filter(|(key, value)| key.starts_with("project.") && value.is_object())
        .map(|(_, value)| value)
        .collect();
    if nested.is_empty() {
        vec![extra]
    } else {
        nested
    }
}

fn targets(engine: &Value) -> Vec<Value> {
    engine["targets"].as_array().cloned().unwrap_or_default()
}

fn status_in(target: &Value, statuses: &[&str]) -> bool {
    target["status"]
        .as_str()
        .is_some_and(|status| statuses.contains(&status))
}

fn setting<'a>(context: &'a Context<'_>, key: &str) -> Option<&'a str> {
    context.settings.get(key).and_then(Value::as_str)
}

fn insert_absent(extra: &mut Value, key: &str, value: Value) {
    if extra.get(key).is_none() {
        extra[key] = value;
    }
}

/// Complete the frozen `extra` keys of one legacy engine (legacy schema only).
pub(crate) fn complete(family: &str, engine: &mut Value, context: &Context<'_>) {
    if !engine["extra"].is_object() {
        engine["extra"] = json!({});
    }
    match family {
        "type" => complete_type(engine, context),
        "sanitize" | "thread_sanitize" => complete_sanitize(family, engine),
        "dup" => complete_dup(engine, context),
        "dead" => complete_dead(engine, context),
        "test" => complete_test(engine, context),
        "resource" | "security" => complete_python_half(family, engine),
        "exception" => {
            // Frozen `ExceptionEngine.run` over every language's targets.
            let (fail, warn) = counts(engine);
            engine["extra"]["violations_count"] = json!(fail + warn);
            engine["extra"]["metrics_summary"] = json!(format!("{fail} exc errors"));
        }
        // The no-C++ SKIP of frozen `CompileDbEngine` writes no counters.
        "compile_db" if engine["extra"].get("production_units").is_some() => {
            let (fail, warn) = counts(engine);
            engine["extra"]["issues_count"] = json!(fail + warn);
        }
        "cycle" => complete_cycle(engine),
        "complexity" | "cognitive" => complete_boundaries(engine),
        _ => {}
    }
}

/// FAIL and WARN targets of the engine.
fn counts(engine: &Value) -> (usize, usize) {
    let targets = targets(engine);
    let count = |status: &str| {
        targets
            .iter()
            .filter(|target| target["status"] == status)
            .count()
    };
    (count("FAIL"), count("WARN"))
}

fn check<'a>(checks: &'a [Value], id: &str) -> Option<&'a Value> {
    checks
        .iter()
        .find(|check| check["check_id"] == id)
        .and_then(|check| check_blocks(check).into_iter().next())
}

/// Frozen `CycleEngine.run` beside the native include graph: the Python half's keys, the C++
/// include outcome under its frozen names (`_analyze_cpp_includes`: the heuristic suffix
/// resolution checks no configuration and has no scope counts) and the cycle totals.
fn complete_cycle(engine: &mut Value) {
    let checks = checks(engine);
    let python = check(&checks, "python.cycle").cloned();
    let cpp = check(&checks, "cpp.cycle").cloned();
    let extra = &mut engine["extra"];
    if let (Some(python), true) = (&python, checks.len() > 1)
        && let Some(fields) = python.as_object()
    {
        for (key, value) in fields {
            insert_absent(extra, key, value.clone());
        }
    }
    if let Some(cpp) = &cpp {
        for (frozen, native) in [
            ("cpp_include_resolution", "resolution"),
            ("cpp_configurations_checked", "configurations_checked"),
            ("cpp_include_scope_counts", "scope_counts"),
            (
                "cpp_include_diagnostics_truncated",
                "include_diagnostics_truncated",
            ),
            ("resolved_cpp_includes", "resolved_edges"),
        ] {
            if let Some(value) = cpp.get(frozen).or_else(|| cpp.get(native)) {
                extra[frozen] = value.clone();
            }
        }
        for key in [
            "ambiguous_cpp_includes",
            "unresolved_cpp_includes",
            "cpp_include_diagnostics_truncated",
        ] {
            if let Some(value) = cpp.get(key) {
                extra[key] = value.clone();
            }
        }
        if extra["cpp_include_resolution"] == "unique_project_path_suffix" {
            insert_absent(extra, "cpp_configurations_checked", json!(0));
            insert_absent(extra, "cpp_include_scope_counts", json!({}));
        }
        let cycles = cpp["cpp_cycles"]
            .as_u64()
            .or_else(|| Some(cpp["cycles_reported"].as_u64()? + cpp["cycles_truncated"].as_u64()?));
        if let Some(cycles) = cycles {
            extra["cpp_cycles"] = json!(cycles);
        }
    }
    let py = python
        .as_ref()
        .and_then(|python| python["py_cycles"].as_u64())
        .unwrap_or(0);
    extra["py_cycles"] = json!(py);
    // Without a C++ check frozen counted no C++ cycle; with one, only its recorded count.
    let cpp_cycles = match cpp {
        Some(_) => extra["cpp_cycles"].as_u64(),
        None => Some(extra["cpp_cycles"].as_u64().unwrap_or(0)),
    };
    if let Some(cpp_cycles) = cpp_cycles {
        extra["total_cycles"] = json!(py + cpp_cycles);
    }
}

/// Frozen complexity/cognitive C++ boundary evidence (`_cpp_boundary_evidence`) under its frozen
/// names; the heuristic boundary pass checks no exact configuration or source and reports no
/// boundary error or warning.
fn complete_boundaries(engine: &mut Value) {
    let checks = checks(engine);
    let id = if engine["engine_name"] == "cognitive" {
        "cpp.cognitive"
    } else {
        "cpp.complexity"
    };
    let Some(cpp) = check(&checks, id).cloned() else {
        return;
    };
    if id == "cpp.cognitive" && engine["extra"].get("max_cognitive").is_none() {
        // Frozen `CognitiveEngine.run`: one target per analyzed function (Python then C++).
        let scores: Vec<(bool, u64)> = targets(engine)
            .iter()
            .filter_map(|target| {
                let python = target["file_path"]
                    .as_str()
                    .is_some_and(|path| path.ends_with(".py") || path.ends_with(".pyi"));
                Some((python, target["metrics"]["cognitive"].as_u64()?))
            })
            .collect();
        let python = scores.iter().filter(|(python, _)| *python).count();
        let extra = &mut engine["extra"];
        extra["max_cognitive"] = json!(scores.iter().map(|(_, score)| *score).max().unwrap_or(0));
        extra["total_functions"] = json!(scores.len());
        extra["python_functions"] = json!(python);
        extra["cpp_functions"] = json!(scores.len() - python);
    }
    let extra = &mut engine["extra"];
    for (frozen, native) in [
        ("cpp_boundary_mode", "boundary_mode"),
        ("cpp_estimated_boundaries", "estimated_boundaries"),
        ("cpp_exact_boundaries", "exact_boundaries"),
    ] {
        if let Some(value) = cpp.get(frozen).or_else(|| cpp.get(native)) {
            extra[frozen] = value.clone();
        }
    }
    if extra["cpp_boundary_mode"] == "heuristic" {
        for (key, value) in [
            ("cpp_boundary_configurations_checked", json!(0)),
            ("cpp_boundary_sources_checked", json!(0)),
            ("cpp_boundary_errors", json!([])),
            ("cpp_boundary_warnings", json!([])),
        ] {
            insert_absent(extra, key, value);
        }
    }
}

/// Frozen `TypeEngine.run`: `type_issues`/`metrics_summary` count every type target, the
/// configured `mypy_profile` (default `project`) and the always-on project config discovery.
fn complete_type(engine: &mut Value, context: &Context<'_>) {
    let count = targets(engine).len();
    let extra = &mut engine["extra"];
    extra["type_issues"] = json!(count);
    extra["metrics_summary"] = json!(format!("{count} type targets"));
    extra["mypy_profile"] = json!(setting(context, "mypy_profile").unwrap_or("project"));
    extra["mypy_project_config_discovery"] = json!(true);
}

/// Frozen `SanitizeEngine.run`: `ISSUE_COUNT_KEY` (`_issue_count`, the WARN/FAIL targets) and
/// `sanitizer_diagnostics`, the diagnostics the sanitizer runs parsed (none ran: empty).
fn complete_sanitize(family: &str, engine: &mut Value) {
    let key = if family == "sanitize" {
        "sanitize_issues"
    } else {
        "thread_sanitize_issues"
    };
    let issues = targets(engine)
        .iter()
        .filter(|target| status_in(target, &["WARN", "FAIL"]))
        .count();
    let diagnostics: Vec<Value> = checks(engine)
        .iter()
        .flat_map(check_blocks)
        .filter_map(|block| block["sanitizer_diagnostics"].as_array())
        .flatten()
        .cloned()
        .collect();
    let extra = &mut engine["extra"];
    insert_absent(extra, key, json!(issues));
    insert_absent(extra, "sanitizer_diagnostics", json!(diagnostics));
}

/// Frozen `DupEngine.run`: the summary line, the algorithm/tokenizer/region/signal policy
/// constants, and the semantic evidence of a project without Python sources
/// (`_semantic_evidence(None, ...)`: mode `off` or `not-applicable`, all counters zero).
fn complete_dup(engine: &mut Value, context: &Context<'_>) {
    let policy = setting(context, "python_semantic").unwrap_or("auto");
    merge_dup_checks(engine);
    let extra = &mut engine["extra"];
    if extra.get("python_semantic_mode").is_none() {
        let mode = if policy == "off" {
            "off"
        } else {
            "not-applicable"
        };
        for (key, value) in [
            ("python_semantic_policy", json!(policy)),
            ("python_semantic_mode", json!(mode)),
            ("python_semantic_shape_policy", json!(SEMANTIC_SHAPE_POLICY)),
            (
                "python_semantic_fingerprint_algorithm",
                json!(SEMANTIC_ALGORITHM),
            ),
            (
                "python_semantic_shape_version",
                json!(SEMANTIC_SHAPE_VERSION),
            ),
            ("python_semantic_regions_observed", json!(0)),
            ("python_semantic_regions_eligible", json!(0)),
            ("python_semantic_trivial_regions_excluded", json!(0)),
            ("python_semantic_parent_regions_excluded", json!(0)),
            ("python_semantic_exclusions", json!(0)),
            ("python_semantic_exclusion_counts", json!({})),
            ("python_semantic_groups_suppressed", json!(0)),
            ("python_semantic_groups_reported", json!(0)),
            ("python_semantic_nodes_observed", json!(0)),
            ("python_semantic_serialized_chars", json!(0)),
        ] {
            extra[key] = value;
        }
    }
    // Frozen adds the semantic algorithm when the policy is not off and Python texts were read,
    // which is exactly when the semantic outcome exists (a mode other than off/not-applicable).
    let semantic = !matches!(
        extra["python_semantic_mode"].as_str(),
        Some("off" | "not-applicable") | None
    );
    let primary = extra["fingerprint_algorithm"]
        .as_str()
        .unwrap_or(TYPE2_ALGORITHM)
        .to_owned();
    let mut algorithms = vec![json!(primary)];
    if semantic {
        algorithms.push(json!(SEMANTIC_ALGORITHM));
    }
    extra["fingerprint_algorithms"] = json!(algorithms);
    extra["tokenizer_versions"] = Value::Object(
        TOKENIZER_VERSIONS
            .iter()
            .map(|(language, version)| ((*language).to_owned(), json!(version)))
            .collect::<Map<_, _>>(),
    );
    extra["region_policy"] = json!(REGION_POLICY);
    extra["signal_policy"] = json!(SIGNAL_POLICY);
    if let (Some(rate), Some(groups)) = (
        extra["duplication_pct"].as_f64(),
        extra["clone_groups_count"].as_u64(),
    ) {
        extra["metrics_summary"] = json!(format!("Duplication: {rate:.1}% ({groups} groups)"));
    }
}

/// Frozen `DupEngine.run` is one scan over every language: with a Python and a C++ check the
/// engine's counters are their sums (`duplication_pct` = duplicated over total code lines), the
/// semantic evidence is the Python check's, and the shared provenance/algorithm stay. Byte and
/// exclusion counters are summed only when every check measured them; clone groups are kept
/// only when at most one language reported any (frozen's cross-language group order is not
/// reconstructed).
fn merge_dup_checks(engine: &mut Value) {
    let checks = checks(engine);
    let blocks: Vec<&Value> = ["python.dup", "cpp.dup"]
        .iter()
        .filter_map(|id| check(&checks, id))
        .collect();
    if blocks.len() < 2 {
        return;
    }
    let extra = &mut engine["extra"];
    let sum = |key: &str| -> Option<u64> {
        blocks
            .iter()
            .map(|block| block[key].as_u64())
            .sum::<Option<u64>>()
    };
    for key in [
        "clone_groups_count",
        "source_files_analyzed",
        "source_files_excluded",
        "source_bytes_analyzed",
    ] {
        if let Some(total) = sum(key) {
            insert_absent(extra, key, json!(total));
        }
    }
    if let (Some(duplicated), Some(total)) = (sum("duplicated_lines"), sum("total_code_lines")) {
        let rate = if total > 0 {
            duplicated as f64 / total as f64 * 100.0
        } else {
            0.0
        };
        insert_absent(extra, "duplication_pct", json!(rate));
    }
    if blocks
        .iter()
        .all(|block| block["source_exclusion_counts"].is_object())
    {
        let mut reasons = Map::new();
        for block in &blocks {
            for (reason, count) in block["source_exclusion_counts"].as_object().unwrap() {
                let previous = reasons.get(reason).and_then(Value::as_u64).unwrap_or(0);
                reasons.insert(
                    reason.clone(),
                    json!(previous + count.as_u64().unwrap_or(0)),
                );
            }
        }
        insert_absent(extra, "source_exclusion_counts", Value::Object(reasons));
    }
    let groups: Vec<&Vec<Value>> = blocks
        .iter()
        .filter_map(|block| block["clone_groups"].as_array())
        .filter(|groups| !groups.is_empty())
        .collect();
    if groups.len() <= 1 && blocks.iter().all(|block| block["clone_groups"].is_array()) {
        insert_absent(
            extra,
            "clone_groups",
            json!(
                groups
                    .first()
                    .map(|groups| (*groups).clone())
                    .unwrap_or_default()
            ),
        );
    }
    for key in ["analysis_provenance", "fingerprint_algorithm"] {
        if blocks.iter().all(|block| block[key] == blocks[0][key]) && !blocks[0][key].is_null() {
            insert_absent(extra, key, blocks[0][key].clone());
        }
    }
    if let Some(fields) = blocks[0].as_object() {
        for (key, value) in fields {
            if key.starts_with("python_semantic_") {
                insert_absent(extra, key, value.clone());
            }
        }
    }
}

/// Frozen `DeadEngine.run` beside the native Python heuristic: the configured C++ policies and,
/// without a native C++ dead check (no C++ scope), the empty C++ outcomes frozen records
/// (`CppUnusedFunctionOutcome(mode="not-applicable")`, the linker `off`/`not-applicable`
/// outcome, NOT_APPLICABLE scope evidence); the summary counts every dead symbol.
fn complete_dead(engine: &mut Value, context: &Context<'_>) {
    let unused_policy = setting(context, "cpp_unused").unwrap_or("auto").to_owned();
    let linker_policy = setting(context, "cpp_linker").unwrap_or("off").to_owned();
    let checks = checks(engine);
    let cpp = checks.iter().any(|check| check["check_id"] == "cpp.dead");
    let python: Vec<&Value> = checks
        .iter()
        .filter(|check| check["check_id"] == "python.dead")
        .flat_map(check_blocks)
        .collect();
    let extra = &mut engine["extra"];
    extra["cpp_unused_policy"] = json!(unused_policy);
    extra["cpp_linker_policy"] = json!(linker_policy);
    if !cpp {
        // Frozen `_analyze_cpp_scope`/`_analyze_linker_scope` without C++ candidates.
        let linker_mode = if linker_policy == "off" {
            "off"
        } else {
            "not-applicable"
        };
        for (key, value) in [
            ("cpp_unused_mode", json!("not-applicable")),
            ("cpp_unused_functions_count", json!(0)),
            ("cpp_unused_configurations_checked", json!(0)),
            ("cpp_unused_sources_checked", json!(0)),
            ("cpp_unused_non_tu_diagnostics_excluded", json!(0)),
            ("cpp_unused_warnings", json!([])),
            ("cpp_unused_details", json!([])),
            ("cpp_linker_mode", json!(linker_mode)),
            ("cpp_linker_symbols_count", json!(0)),
            ("cpp_linker_targets_checked", json!(0)),
            ("cpp_linker_sources_checked", json!(0)),
            ("cpp_linker_discarded_sections_observed", json!(0)),
            ("cpp_linker_ambiguous_sections_excluded", json!(0)),
            ("cpp_linker_sections_kept_by_another_target", json!(0)),
            ("cpp_linker_warnings", json!([])),
            ("cpp_linker_details", json!([])),
            (
                "cpp_scope_evidence",
                json!({"compiler_unused": "NOT_APPLICABLE", "gnu_elf_linker": "NOT_APPLICABLE"}),
            ),
        ] {
            extra[key] = value;
        }
        if extra["language_evidence"].is_object() {
            extra["language_evidence"]["cpp"] = json!("NOT_APPLICABLE");
        }
        // The measured source snapshot of the Python half is the whole frozen inventory.
        for block in &python {
            for key in [
                "source_files_analyzed",
                "source_files_snapshotted",
                "source_bytes_analyzed",
                "source_files_excluded",
                "source_exclusion_counts",
            ] {
                if let Some(value) = block.get(key) {
                    extra[key] = value.clone();
                }
            }
        }
    }
    if cpp {
        complete_cpp_dead(engine, &linker_policy, &python);
    }
    let extra = &mut engine["extra"];
    if let Some(count) = extra["dead_symbols_count"].as_u64() {
        extra["metrics_summary"] = json!(format!("{count} dead symbols"));
    }
}

/// Frozen dead-code C++ outcome fields recorded by the native `cpp.dead` check: the compiler
/// scope's checked sources and excluded non-TU diagnostics, the unavailable scope's warning,
/// the disabled linker outcome (`cpp_linker = "off"`: `CppLinkerDeadOutcome(mode="off")`), the
/// per-scope evidence (`_cpp_evidence`/`_linker_evidence`) and the dead symbol total.
fn complete_cpp_dead(engine: &mut Value, linker_policy: &str, python: &[&Value]) {
    let checks = checks(engine);
    let cpp = check(&checks, "cpp.dead").cloned().unwrap_or_default();
    let targets = targets(engine);
    let named = |name: &str| targets.iter().find(|target| target["target_name"] == name);
    let extra = &mut engine["extra"];
    let mode = extra["cpp_unused_mode"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    if let Some(sources) = cpp["sources_checked"].as_u64() {
        extra["cpp_unused_sources_checked"] = json!(sources);
    } else if mode == "unavailable" {
        extra["cpp_unused_sources_checked"] = json!(0);
    }
    if let Some(excluded) = cpp["non_tu_diagnostics_excluded"].as_u64() {
        extra["cpp_unused_non_tu_diagnostics_excluded"] = json!(excluded);
    } else if mode == "unavailable" {
        extra["cpp_unused_non_tu_diagnostics_excluded"] = json!(0);
    }
    if mode == "unavailable" {
        // `run_cpp_unused_functions` without a usable database: no configuration, one warning
        // (the unavailable target's message), no detail.
        if let Some(target) = named("C++UnusedFunctionsUnavailable") {
            extra["cpp_unused_warnings"] = json!([target["message"]]);
        }
        extra["cpp_unused_configurations_checked"] = json!(0);
        extra["cpp_unused_details"] = json!([]);
    }
    if linker_policy == "off" {
        for (key, value) in [
            ("cpp_linker_mode", json!("off")),
            ("cpp_linker_symbols_count", json!(0)),
            ("cpp_linker_targets_checked", json!(0)),
            ("cpp_linker_sources_checked", json!(0)),
            ("cpp_linker_discarded_sections_observed", json!(0)),
            ("cpp_linker_ambiguous_sections_excluded", json!(0)),
            ("cpp_linker_sections_kept_by_another_target", json!(0)),
            ("cpp_linker_warnings", json!([])),
            ("cpp_linker_details", json!([])),
        ] {
            extra[key] = value;
        }
        let compiler = match mode.as_str() {
            "exact" => "MEASURED",
            _ => "NOT_RUN",
        };
        extra["cpp_scope_evidence"] =
            json!({"compiler_unused": compiler, "gnu_elf_linker": "NOT_RUN"});
        let python_evidence = python
            .iter()
            .find_map(|block| block["language_evidence"]["python"].as_str())
            .unwrap_or("NOT_APPLICABLE");
        extra["language_evidence"] = json!({"python": python_evidence, "cpp": compiler});
        let python_issues: u64 = python
            .iter()
            .filter_map(|block| block["python_issues"].as_u64())
            .sum();
        if let Some(functions) = extra["cpp_unused_functions_count"].as_u64() {
            extra["dead_symbols_count"] = json!(python_issues + functions);
        }
    }
}

/// Frozen `TestEngine.run` of a project without a test file (`_mark_zero_tests`): the counts and
/// suites of the located FAIL targets, the empty coverage rows, no coverage policy/totals and the
/// quality block of a run that attempted nothing (`empty_quality_info()` with the profile).
fn complete_test(engine: &mut Value, context: &Context<'_>) {
    if engine["extra"].get("no_tests_collected").is_none() {
        complete_optional_coverage(engine, context.profile);
        return;
    }
    let targets = targets(engine);
    let mut suites: Vec<Value> = vec![];
    for target in &targets {
        let name = target["target_name"].as_str().unwrap_or_default();
        if name.starts_with("Coverage:") || name.starts_with("[test-quality]") {
            continue;
        }
        let file = target["file_path"].clone();
        let position = suites
            .iter()
            .position(|suite| suite["file"] == file)
            .unwrap_or_else(|| {
                suites.push(json!({"file": file, "passed": 0, "failed": 0, "skipped": 0,
                    "total": 0, "tests": []}));
                suites.len() - 1
            });
        let cases = target["metrics"]["test_cases"].as_u64().unwrap_or(1);
        let suite = &mut suites[position];
        let key = match target["status"].as_str() {
            Some("PASS") => "passed",
            Some("SKIP") => "skipped",
            _ => "failed",
        };
        suite["total"] = json!(suite["total"].as_u64().unwrap_or(0) + cases);
        suite[key] = json!(suite[key].as_u64().unwrap_or(0) + cases);
        suite["tests"].as_array_mut().unwrap().push(json!({
            "name": target["target_name"], "status": target["status"],
            "message": target["message"],
        }));
    }
    let skipped: u64 = targets
        .iter()
        .filter(|target| {
            target["status"] == "SKIP"
                && !target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("[test-quality]"))
        })
        .map(|target| target["metrics"]["test_cases"].as_u64().unwrap_or(1))
        .sum();
    let passed = engine["extra"]["passed_tests"].as_u64().unwrap_or(0);
    let total = engine["extra"]["total_tests"].as_u64().unwrap_or(0);
    let quality = frozen_quality(None, context.profile);
    let extra = &mut engine["extra"];
    extra["skipped_tests"] = json!(skipped);
    // `calculate_tem`: passed over total, 0.0 without a test.
    extra["pass_rate"] = json!(if total == 0 {
        0.0
    } else {
        passed as f64 / total as f64
    });
    extra["test_suites"] = json!(suites);
    extra["line_coverage"] = Value::Null;
    extra["coverage_files"] = json!([]);
    extra["function_rows"] = json!([]);
    extra["coverage_totals"] = Value::Null;
    extra["coverage_policy"] = Value::Null;
    for key in [
        "repeat_runs",
        "repeat_reruns",
        "repeat_cases",
        "repeat_unavailable",
        "repeat_timeouts",
        "flaky_tests",
        "flaky_test_inventory",
        "slow_tests",
        "slow_tests_observed",
        "slow_test_inventory",
        "mutation_probes",
        "mutation_available",
        "mutation_unavailable",
        "mutation_status",
    ] {
        extra[key] = quality[key].clone();
    }
    extra["quality_enabled"] = json!(false);
    extra["test_quality"] = quality;
}

/// Frozen `TestEngine.run` whose optional coverage module is missing: the measured suite of the
/// native `python.test` check (counts, pass rate, suites, quality block) and no measured coverage
/// rows/totals/policy; frozen's coverage estimate and TEM stay absent.
/// Frozen `empty_quality_info()` with the analysis profile, overlaid with the values the native
/// quality block measured for the same frozen keys (native-only keys are not frozen's).
fn frozen_quality(native: Option<&Value>, profile: &str) -> Value {
    let mut quality = json!({
        "enabled": false, "mode": "report", "profile": profile,
        "repeat_runs": 0, "repeat_reruns": 0, "repeat_cases": 0, "repeat_unavailable": 0,
        "repeat_timeouts": 0, "flaky_tests": 0, "flaky_test_inventory": [], "slow_tests": 0,
        "slow_tests_observed": 0, "slow_test_inventory": [], "mutation_probes": 0,
        "mutation_available": 0, "mutation_unavailable": 0, "mutation_status": "disabled",
    });
    if let (Some(native), Some(fields)) = (native, quality.as_object_mut()) {
        for (key, value) in fields.iter_mut() {
            if key != "profile"
                && let Some(measured) = native.get(key)
            {
                *value = measured.clone();
            }
        }
    }
    quality
}

fn complete_optional_coverage(engine: &mut Value, profile: &str) {
    if !engine["summary"]
        .as_str()
        .is_some_and(|summary| summary.contains("; coverage not measured (optional): "))
    {
        return;
    }
    let checks = checks(engine);
    let Some(test) = checks
        .iter()
        .find(|check| check["check_id"] == "python.test" && check["state"] == "SUCCEEDED")
        .and_then(|check| check_blocks(check).into_iter().next())
        .cloned()
    else {
        return;
    };
    let extra = &mut engine["extra"];
    for key in [
        "passed_tests",
        "total_tests",
        "skipped_tests",
        "pass_rate",
        "test_suites",
        "quality_enabled",
        "repeat_runs",
        "repeat_reruns",
        "repeat_cases",
        "repeat_unavailable",
        "repeat_timeouts",
        "flaky_tests",
        "flaky_test_inventory",
        "slow_tests",
        "slow_tests_observed",
        "slow_test_inventory",
        "mutation_probes",
        "mutation_available",
        "mutation_unavailable",
        "mutation_status",
    ] {
        if let Some(value) = test.get(key).filter(|value| !value.is_null()) {
            extra[key] = value.clone();
        }
    }
    extra["test_quality"] = frozen_quality(test.get("test_quality"), profile);
    extra["line_coverage"] = Value::Null;
    extra["coverage_files"] = json!([]);
    extra["function_rows"] = json!([]);
    extra["coverage_totals"] = Value::Null;
    extra["coverage_policy"] = Value::Null;
}

/// Frozen hybrid resource/security engines report the Python analysis extras only (the C++
/// half adds no extra key), so the Python check's direct keys are the engine's.
fn complete_python_half(family: &str, engine: &mut Value) {
    let checks = checks(engine);
    if checks.len() < 2 {
        return;
    }
    let id = format!("python.{family}");
    let Some(python) = checks.iter().find(|check| check["check_id"] == id.as_str()) else {
        return;
    };
    let blocks = check_blocks(python);
    for block in blocks {
        let Some(fields) = block.as_object() else {
            continue;
        };
        for (key, value) in fields {
            if !matches!(
                key.as_str(),
                "native_checks"
                    | "target_scopes"
                    | "tool_evidence"
                    | "legacy_findings"
                    | "legacy_artifact_manifests"
            ) {
                insert_absent(&mut engine["extra"], key, value.clone());
            }
        }
    }
}
