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

/// Inputs of the completion: the engine's `[engines.<family>]` table, the profile name and the
/// number of Python sources in scope (frozen `project_python_sources`).
pub(crate) struct Context<'a> {
    pub settings: &'a Value,
    pub profile: &'a str,
    pub python_sources: usize,
    /// The project root and its C++ sources (frozen `project_cpp_sources`, relative).
    pub root: &'a std::path::Path,
    pub cpp_sources: &'a [String],
    /// Whether frozen `proj_type` has a C++ facet with sources (`_run_project_tests`).
    pub cpp_scope: bool,
}

pub(crate) fn checks(engine: &Value) -> Vec<Value> {
    engine["extra"]["native_checks"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}

/// A native check's own extra block (direct, or under its task id when merged).
pub(crate) fn check_blocks(check: &Value) -> Vec<&Value> {
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

pub(crate) fn targets(engine: &Value) -> Vec<Value> {
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

/// Complete the frozen `extra` keys of one legacy engine (legacy schema only). Returns whether
/// the engine verdict (status, evidence or summary) was replaced by the frozen one of a hybrid
/// engine, so the suite counters are recomputed.
pub(crate) fn complete(family: &str, engine: &mut Value, context: &Context<'_>) -> bool {
    if !engine["extra"].is_object() {
        engine["extra"] = json!({});
    }
    match family {
        "type" => complete_type(engine, context),
        "sanitize" | "thread_sanitize" => complete_sanitize(family, engine),
        "dup" => return complete_dup(engine, context),
        "dead" => {
            let required_restored = complete_dead(engine, context);
            return dead_verdict(engine, context) || required_restored;
        }
        "complexity" => {
            complete_boundaries(engine);
            merge_complexity(engine, context);
        }
        // The rebuilt verdict replaces the native ERROR (its exit class is the frozen one).
        "test" if empty_pytest_beside_cpp_suite(engine, context) => return true,
        "test" if crate::legacy_hybrid::complete(engine, context) => return true,
        "test" if crate::legacy_hybrid::zero_cpp_tests(engine, context) => return true,
        "test" if crate::legacy_hybrid::python_coverage_unmeasured(engine, context) => {
            return true;
        }
        "test" if crate::legacy_hybrid::cpp_tests_not_built(engine, context) => return true,
        "test" => {
            complete_test(engine, context);
            if crate::legacy_hybrid::single_language_policy_errors(engine) {
                return true;
            }
        }
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
        "cycle" => complete_cycle(engine, context),
        "cognitive" => {
            complete_boundaries(engine);
            merge_cognitive(engine, context);
        }
        "binary_compat" => crate::cpp::frozen_binary_compat_extra(engine),
        _ => {}
    }
    false
}

/// Frozen `BaseEngine.evaluate_status`: `pass_fail` fails on any issue, `pass_warn` caps every
/// issue at WARN, `pass_warn_fail` (any other mode) keeps FAIL and WARN apart.
pub(crate) fn evaluate_status(has_fail: bool, has_warn: bool, mode: &str) -> &'static str {
    match mode {
        "pass_fail" if has_fail || has_warn => "FAIL",
        "pass_warn" if has_fail || has_warn => "WARN",
        "pass_fail" | "pass_warn" => "PASS",
        _ if has_fail => "FAIL",
        _ if has_warn => "WARN",
        _ => "PASS",
    }
}

/// The engine has a native check of each id (a hybrid Python + C++ engine).
pub(crate) fn has_checks(engine: &Value, ids: &[&str]) -> bool {
    let checks = checks(engine);
    ids.iter()
        .all(|id| checks.iter().any(|check| check["check_id"] == *id))
}

/// Frozen `TypeCheckEngine.run` tool warnings beside the targets: the missing-mypy fallback
/// warning (`mypy_missing`), then `_mark_cpp_type_check_skipped`'s warning for the C++ sources
/// of a project that also has Python (one `C++TypeCheck` SKIP target per source). Every warning
/// counts as a warning, the first two join the summary, the verdict follows the configured mode
/// and the evidence is ESTIMATED, which the adapted targets' confidence follows (`medium`).
/// Without a warning, or for a refused/errored (`ERROR`) or not-applicable (`SKIP`) engine whose
/// mypy ran, the native verdict stands. Returns whether the verdict was replaced.
pub(crate) fn type_verdict(engine: &mut Value, mode: &str, mypy_missing: bool) -> bool {
    let targets = targets(engine);
    let cpp_sources = targets
        .iter()
        .filter(|target| {
            target["target_name"] == "C++TypeCheck"
                && target["status"] == "SKIP"
                && target["file_path"] != "."
        })
        .count();
    let mut warnings = vec![];
    if mypy_missing {
        warnings.push("Mypy is unavailable; AST fallback is ESTIMATED".to_owned());
    }
    if cpp_sources > 0 {
        warnings.push(format!(
            "C++ type checking is skipped for {cpp_sources} source file(s); evidence is ESTIMATED"
        ));
    }
    if warnings.is_empty()
        || (!mypy_missing && matches!(engine["status"].as_str(), Some("ERROR" | "SKIP")))
    {
        return false;
    }
    let count = |status: &str| {
        targets
            .iter()
            .filter(|target| target["status"] == status)
            .count()
    };
    let (fail, warn) = (count("FAIL"), count("WARN") + warnings.len());
    let status = evaluate_status(fail > 0, warn > 0, mode);
    let mut parts = vec![format!("{fail} Type Findings, {warn} Warnings")];
    parts.extend(warnings.into_iter().take(2));
    engine["status"] = json!(status);
    engine["evidence"] = json!("ESTIMATED");
    engine["summary"] = json!(if status == "PASS" {
        "Static Type Check Passed".to_owned()
    } else {
        parts.join("; ")
    });
    for finding in engine["findings"].as_array_mut().into_iter().flatten() {
        if finding["rule_id"] == "ici.legacy.type.target" {
            finding["confidence"] = json!("medium");
        }
    }
    true
}

/// Frozen `ComplexityEngine.run` is one pass over both languages: the score is the highest
/// cyclomatic complexity (a float) and the summary counts every function target and every
/// WARN/FAIL/ERROR target of both. A single-language engine keeps its check's own values; an
/// errored engine keeps the native error summary.
fn merge_complexity(engine: &mut Value, context: &Context<'_>) {
    if !has_checks(engine, &["python.complexity", "cpp.complexity"]) || engine["status"] == "ERROR"
    {
        return;
    }
    let targets = targets(engine);
    let functions: Vec<u64> = targets
        .iter()
        .filter_map(|target| target["metrics"]["complexity"].as_u64())
        .collect();
    let issues = targets
        .iter()
        .filter(|target| status_in(target, &["WARN", "FAIL", "ERROR"]))
        .count();
    let peak = functions.iter().copied().max().unwrap_or(0);
    let limit = context
        .settings
        .get("warn_cc")
        .filter(|limit| limit.is_number())
        .map_or_else(|| "15".to_owned(), Value::to_string);
    engine["score"] = json!(peak as f64);
    engine["summary"] = json!(format!(
        "Max Cyclomatic Complexity: {peak} (limit {limit}) across {} functions ({issues} issues)",
        functions.len()
    ));
}

/// Frozen `CognitiveEngine.run` is one pass over both languages (engines/cognitive.py:129-136):
/// the summary names the highest cognitive score and the function count of both, then the fail
/// or warn threshold any target reached. A single-language engine keeps its check's own summary;
/// an errored engine keeps the native error summary.
fn merge_cognitive(engine: &mut Value, context: &Context<'_>) {
    if !has_checks(engine, &["python.cognitive", "cpp.cognitive"]) || engine["status"] == "ERROR" {
        return;
    }
    let targets = targets(engine);
    let scores: Vec<u64> = targets
        .iter()
        .filter_map(|target| target["metrics"]["cognitive"].as_u64())
        .collect();
    let limit = |key: &str, default: i64| {
        context
            .settings
            .get(key)
            .and_then(Value::as_i64)
            .unwrap_or(default)
    };
    let mut summary = format!(
        "Max cognitive complexity: {} across {} functions",
        scores.iter().copied().max().unwrap_or(0),
        scores.len()
    );
    if targets.iter().any(|target| status_in(target, &["FAIL"])) {
        summary.push_str(&format!("; fail threshold {} exceeded", limit("fail", 60)));
    } else if targets.iter().any(|target| status_in(target, &["WARN"])) {
        summary.push_str(&format!(
            "; warn threshold {} or nesting threshold {} reached",
            limit("warn", 30),
            limit("warn_nesting", 4)
        ));
    }
    engine["summary"] = json!(summary);
}

/// Frozen `DeadEngine._result_state` for an engine with Python sources beside a native C++ dead
/// check: the evidence is ESTIMATED (the Python heuristic), a C++ scope whose enabled exact
/// analysis was unavailable (no `exact` outcome) is a warning, the verdict follows the configured
/// mode, and the summary is the PASS line, the partial-C++ line or the per-scope counts. An
/// errored engine, or one without Python sources (the C++-only verdicts), keeps its verdict.
/// Returns whether the verdict was replaced.
fn dead_verdict(engine: &mut Value, context: &Context<'_>) -> bool {
    if !has_checks(engine, &["python.dead", "cpp.dead"])
        || engine["status"] == "ERROR"
        || engine["extra"]["language_evidence"]["python"] != "ESTIMATED"
    {
        return false;
    }
    let extra = &engine["extra"];
    let mode_of = |key: &str| extra[key].as_str().unwrap_or_default();
    let policy_of = |key: &str| extra[key].as_str().unwrap_or("off");
    let exact = mode_of("cpp_unused_mode") == "exact" || mode_of("cpp_linker_mode") == "exact";
    let partial = !exact
        && ((policy_of("cpp_unused_policy") != "off"
            && mode_of("cpp_unused_mode") == "unavailable")
            || (policy_of("cpp_linker_policy") != "off"
                && mode_of("cpp_linker_mode") == "unavailable"));
    let targets = targets(engine);
    let has_fail = targets.iter().any(|target| target["status"] == "FAIL");
    let has_warn = partial || targets.iter().any(|target| target["status"] == "WARN");
    let python_issues = targets
        .iter()
        .filter(|target| {
            target["file_path"]
                .as_str()
                .is_some_and(|path| path.ends_with(".py") || path.ends_with(".pyi"))
                && status_in(target, &["WARN", "FAIL"])
        })
        .count() as u64;
    let cpp_issues = extra["cpp_unused_functions_count"].as_u64().unwrap_or(0);
    let linker_issues = extra["cpp_linker_symbols_count"].as_u64().unwrap_or(0);
    let status = evaluate_status(
        has_fail,
        has_warn,
        setting(context, "mode").unwrap_or("pass_warn"),
    );
    let summary = if status == "PASS" {
        "No dead-code findings detected in the analyzed scopes".to_owned()
    } else if partial {
        "Python dead-code analysis completed, but exact C++ analysis was unavailable".to_owned()
    } else {
        format!(
            "{} dead-code finding(s): {python_issues} heuristic Python, {cpp_issues} \
             compiler-local C/C++, {linker_issues} target-local GNU ELF",
            python_issues + cpp_issues + linker_issues
        )
    };
    let before = (
        engine["status"].clone(),
        engine["evidence"].clone(),
        engine["summary"].clone(),
    );
    engine["status"] = json!(status);
    engine["evidence"] = json!("ESTIMATED");
    engine["summary"] = json!(summary);
    before
        != (
            engine["status"].clone(),
            engine["evidence"].clone(),
            engine["summary"].clone(),
        )
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
fn complete_cycle(engine: &mut Value, context: &Context<'_>) {
    let checks = checks(engine);
    // Frozen `CycleEngine` evidence (cycle.py:123-129): NOT_RUN on a C++ include error, MEASURED
    // when the include graph is exact or no translation unit is in scope (`not cpp_sources`: a
    // header-only tree's suffix-resolved graph), else ESTIMATED.
    if context.cpp_sources.is_empty()
        && engine["evidence"] == "ESTIMATED"
        && checks.iter().any(|check| check["check_id"] == "cpp.cycle")
        && checks.iter().all(|check| check["state"] == "SUCCEEDED")
    {
        engine["evidence"] = json!("MEASURED");
    }
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
        if id == "cpp.cognitive" {
            // Frozen `analyze_cpp_cognitive` over no C/C++ source (cognitive.py:98 always runs
            // it): the heuristic boundary pass with nothing checked.
            for (key, value) in [
                ("cpp_boundary_mode", json!("heuristic")),
                ("cpp_exact_boundaries", json!(0)),
                ("cpp_estimated_boundaries", json!(0)),
                ("cpp_boundary_configurations_checked", json!(0)),
                ("cpp_boundary_sources_checked", json!(0)),
                ("cpp_boundary_warnings", json!([])),
                ("cpp_boundary_errors", json!([])),
                (
                    "cpp_scope_exclusions",
                    json!({"lambda":0,"macro_generated_function":0}),
                ),
            ] {
                insert_absent(&mut engine["extra"], key, value);
            }
        }
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
    // The C++ check's frozen scope exclusions (also beside a Python check in a hybrid engine)
    // and, when it recorded them, its boundary run counters, warnings and errors.
    for key in [
        "cpp_scope_exclusions",
        "cpp_boundary_configurations_checked",
        "cpp_boundary_sources_checked",
        "cpp_boundary_warnings",
        "cpp_boundary_errors",
    ] {
        if let Some(value) = cpp.get(key) {
            extra[key] = value.clone();
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
    crate::cpp::frozen_sanitizer_extra(engine);
}

/// Frozen `DupEngine.run`: the summary line, the algorithm/tokenizer/region/signal policy
/// constants, and the semantic evidence of a project without Python sources
/// (`_semantic_evidence(None, ...)`: mode `off` or `not-applicable`, all counters zero).
fn complete_dup(engine: &mut Value, context: &Context<'_>) -> bool {
    let policy = setting(context, "python_semantic").unwrap_or("auto");
    let replaced = merge_dup_checks(engine, context);
    // The finding details served the projection of the clone findings; frozen has no such key.
    if let Some(extra) = engine["extra"].as_object_mut() {
        extra.remove("finding_details");
    }
    // Frozen `DuplicateEngine.run` stops (intake fault, no owned source, normalization or
    // comparison limit) return only `_source_evidence` (an empty inventory after an intake
    // fault), never the policy, algorithm or semantic keys of a completed scan.
    let stopped = targets(engine).iter().any(|target| {
        matches!(
            target["target_name"].as_str(),
            Some("SourceInputError" | "SourceTokenizationError" | "DuplicateComparisonLimit")
        ) || (target["target_name"] == "DuplicateCode" && target["status"] == "SKIP")
    });
    if stopped {
        let extra = &mut engine["extra"];
        for (key, value) in [
            (
                "analysis_provenance",
                json!("language-lexical-region-heuristic"),
            ),
            ("source_files_analyzed", json!(0)),
            ("source_bytes_analyzed", json!(0)),
            ("source_files_excluded", json!(0)),
            ("source_exclusion_counts", json!({})),
        ] {
            insert_absent(extra, key, value);
        }
        return replaced;
    }
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
    replaced
}

/// Frozen `DupEngine.run` is one scan over every language: with a Python and a C++ check the
/// engine's counters are their sums (`duplication_pct` = duplicated over total code lines), the
/// semantic evidence is the Python check's, and the shared provenance/algorithm stay. Byte and
/// exclusion counters are summed only when every check measured them; clone groups of both
/// languages are renumbered in frozen's one-scan order (`frozen_clone_groups`). The verdict is frozen's over the merged rate (`dup_verdict`); returns
/// whether it replaced the native one.
fn merge_dup_checks(engine: &mut Value, context: &Context<'_>) -> bool {
    let checks = checks(engine);
    let blocks: Vec<&Value> = ["python.dup", "cpp.dup"]
        .iter()
        .filter_map(|id| check(&checks, id))
        .collect();
    if blocks.len() < 2 {
        return false;
    }
    if dup_one_scan_stop(engine, &blocks) {
        return true;
    }
    // Frozen scans one inventory: a language whose sources were all excluded adds its exclusion
    // counters and nothing else (no `DuplicateCode` SKIP beside the other language's scan).
    let all_blocks = blocks;
    let blocks: Vec<&Value> = all_blocks
        .iter()
        .filter(|block| !block["duplicated_lines"].is_null())
        .copied()
        .collect();
    let mut one_language_excluded = false;
    if blocks.len() == 1 {
        one_language_excluded = true;
        retain_engine_targets(engine, |target| target["target_name"] != "DuplicateCode");
    }
    let extra = &mut engine["extra"];
    let sum_of = |blocks: &[&Value], key: &str| -> Option<u64> {
        blocks
            .iter()
            .map(|block| block[key].as_u64())
            .sum::<Option<u64>>()
    };
    let sum = |key: &str| sum_of(&blocks, key);
    for key in [
        "clone_groups_count",
        "source_files_analyzed",
        "source_bytes_analyzed",
    ] {
        if let Some(total) = sum(key) {
            insert_absent(extra, key, json!(total));
        }
    }
    if let Some(total) = sum_of(&all_blocks, "source_files_excluded") {
        insert_absent(extra, "source_files_excluded", json!(total));
    }
    if let (Some(duplicated), Some(total)) = (sum("duplicated_lines"), sum("total_code_lines")) {
        let rate = if total > 0 {
            duplicated as f64 / total as f64 * 100.0
        } else {
            0.0
        };
        insert_absent(extra, "duplication_pct", json!(rate));
    }
    if all_blocks
        .iter()
        .all(|block| block["source_exclusion_counts"].is_object())
    {
        let mut reasons = Map::new();
        for block in &all_blocks {
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
    let renumbered = if blocks.iter().all(|block| block["clone_groups"].is_array()) {
        if groups.len() <= 1 {
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
            None
        } else {
            let (merged, numbers) = frozen_clone_groups(&checks);
            insert_absent(extra, "clone_groups", json!(merged));
            Some(numbers)
        }
    } else {
        None
    };
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
    if one_language_excluded {
        engine["status"] = json!("PASS");
        engine["evidence"] = json!("ESTIMATED");
    } else if matches!(engine["status"].as_str(), Some("ERROR" | "SKIP")) {
        return false;
    }
    let mut replaced = one_language_excluded;
    // `score=dup_pct` and the summary over the merged scan (the rate is a float; the group count
    // includes the Python AST-shape groups), and frozen's verdict over that rate.
    if let (Some(rate), Some(groups)) = (
        engine["extra"]["duplication_pct"].as_f64(),
        engine["extra"]["clone_groups_count"].as_u64(),
    ) {
        replaced |= dup_verdict(engine, context, rate);
        let semantic = engine["extra"]["python_semantic_groups_reported"]
            .as_u64()
            .unwrap_or(0);
        engine["score"] = json!(rate);
        engine["summary"] = json!(format!(
            "Code Duplication Rate: {rate:.1}% ({groups} distinct clone groups found, \
             {semantic} from Python AST shapes)"
        ));
    }
    // Frozen appends a `DuplicateScan` target for every unrepresented file of the one scan in
    // its path order (`ordered_sources`), after the clone and semantic targets; the checks list
    // them per language. With clone groups in both languages the groups are renumbered in
    // frozen's one-scan order and their targets follow it.
    if let Some(numbers) = &renumbered {
        renumber_clone_targets(engine, numbers);
    }
    let targets = targets(engine);
    let group = |index: usize| -> u64 {
        if renumbered.is_some() {
            targets[index]["metrics"]["clone_group"]
                .as_u64()
                .unwrap_or(0)
        } else {
            0
        }
    };
    let mut order: Vec<usize> = (0..targets.len()).collect();
    order.sort_by(|left, right| {
        let scan = |index: &usize| targets[*index]["target_name"] == "DuplicateScan";
        match (scan(left), scan(right)) {
            (true, true) => targets[*left]["file_path"]
                .as_str()
                .cmp(&targets[*right]["file_path"].as_str()),
            (false, false) => group(*left).cmp(&group(*right)),
            (left, right) => left.cmp(&right),
        }
    });
    reorder_targets(engine, &order);
    replaced
}

/// Keep the engine targets `keep` accepts and the adapted findings located at them.
fn retain_engine_targets(engine: &mut Value, keep: impl Fn(&Value) -> bool) {
    let targets = targets(engine);
    let order: Vec<usize> = (0..targets.len())
        .filter(|index| keep(&targets[*index]))
        .collect();
    let kept: Vec<(Value, Value)> = order
        .iter()
        .map(|index| {
            (
                targets[*index]["file_path"].clone(),
                targets[*index]["target_name"].clone(),
            )
        })
        .collect();
    reorder_targets(engine, &order);
    if let Some(findings) = engine["findings"].as_array_mut() {
        findings.retain(|finding| {
            let location = &finding["primary_location"];
            kept.iter()
                .any(|(path, label)| &location["path"] == path && &location["label"] == label)
        });
    }
}

/// Frozen `DuplicateEngine.run` is one scan that stops at its first fault, in this order:
/// intake over every language in path order (no inventory), no owned source at all, lexical
/// normalization in path order (the whole inventory), the required Python AST-shape analysis,
/// then the bounded comparison. The native checks run per language, so a stop in one must drop
/// the other's targets, findings and completed-scan keys. Returns whether the scan stopped.
fn dup_one_scan_stop(engine: &mut Value, blocks: &[&Value]) -> bool {
    let all = targets(engine);
    let first = |name: &str| -> Option<Value> {
        all.iter()
            .filter(|target| target["target_name"] == name)
            .min_by_key(|target| target["file_path"].as_str().unwrap_or("").to_owned())
            .cloned()
    };
    let sum = |key: &str| -> u64 { blocks.iter().filter_map(|block| block[key].as_u64()).sum() };
    let exclusions = || {
        let mut reasons = Map::new();
        for block in blocks {
            for (reason, count) in block["source_exclusion_counts"]
                .as_object()
                .into_iter()
                .flatten()
            {
                let previous = reasons.get(reason).and_then(Value::as_u64).unwrap_or(0);
                reasons.insert(
                    reason.clone(),
                    json!(previous + count.as_u64().unwrap_or(0)),
                );
            }
        }
        Value::Object(reasons)
    };
    let inventory = |engine: &mut Value, empty: bool| {
        let extra = &mut engine["extra"];
        for (key, value) in [
            (
                "source_files_analyzed",
                json!(if empty {
                    0
                } else {
                    sum("source_files_analyzed")
                }),
            ),
            (
                "source_bytes_analyzed",
                json!(if empty {
                    0
                } else {
                    sum("source_bytes_analyzed")
                }),
            ),
            (
                "source_files_excluded",
                json!(if empty {
                    0
                } else {
                    sum("source_files_excluded")
                }),
            ),
            (
                "source_exclusion_counts",
                if empty { json!({}) } else { exclusions() },
            ),
        ] {
            extra[key] = value;
        }
    };
    let stop = |engine: &mut Value, name: &str, target: &Value, summary: String, empty: bool| {
        let (path, message) = (target["file_path"].clone(), target["message"].clone());
        retain_engine_targets(engine, |candidate| {
            candidate["target_name"] == name
                && candidate["file_path"] == path
                && candidate["message"] == message
        });
        inventory(engine, empty);
        engine["status"] = json!("ERROR");
        engine["evidence"] = json!("NOT_RUN");
        engine["summary"] = json!(summary);
    };
    if let Some(target) = first("SourceInputError") {
        let message = target["message"].as_str().unwrap_or("");
        let summary = message
            .split_once(": ")
            .map_or(message, |(_, text)| text)
            .to_owned();
        stop(engine, "SourceInputError", &target, summary, true);
        return true;
    }
    let skipped =
        |target: &Value| target["target_name"] == "DuplicateCode" && target["status"] == "SKIP";
    if all.iter().filter(|target| skipped(target)).count() >= 2 {
        let message = all.iter().find(|target| skipped(target)).unwrap()["message"].clone();
        let mut seen = false;
        retain_engine_targets(engine, |target| target["message"] == message);
        if let Some(targets) = engine["targets"].as_array_mut() {
            targets.retain(|_| !std::mem::replace(&mut seen, true));
        }
        // Frozen has one such target, so its adapted finding's identity is the symbol alone
        // (`findings_for_result`), not the region identity of a repeated (path, name).
        if let Some(findings) = engine["findings"].as_array_mut() {
            findings.truncate(1);
            if let Some(finding) = findings.first_mut()
                && let Ok(identity) = ici_reporting::canonical_json(&json!({
                    "version": "ici-fingerprint/v1",
                    "rule_id": finding["rule_id"],
                    "path": ".",
                    "symbol": "DuplicateCode",
                    "region": null,
                }))
            {
                finding["fingerprint"] = json!(ici_domain::sha256(identity));
            }
        }
        inventory(engine, false);
        return true;
    }
    if let Some(target) = first("SourceTokenizationError") {
        let summary = format!(
            "Duplicate analysis could not normalize {}",
            target["file_path"].as_str().unwrap_or("")
        );
        stop(engine, "SourceTokenizationError", &target, summary, false);
        return true;
    }
    if all
        .iter()
        .any(|target| target["target_name"] == "PythonSemanticShapeError")
    {
        retain_engine_targets(engine, |target| {
            target["target_name"] == "PythonSemanticShapeError"
        });
        inventory(engine, false);
        engine["status"] = json!("ERROR");
        engine["evidence"] = json!("NOT_RUN");
        engine["summary"] = json!("Required Python AST-shape duplicate analysis was incomplete");
        return true;
    }
    if let Some(target) = first("DuplicateComparisonLimit") {
        let summary = "Duplicate analysis exceeded a bounded comparison limit".to_owned();
        stop(engine, "DuplicateComparisonLimit", &target, summary, false);
        return true;
    }
    false
}

/// Frozen one-scan clone groups (`cluster_matches` then `_cluster_python_semantic_regions`):
/// every language's lexical groups ordered by (longest match, most occurrences, occurrence
/// locations) and numbered from 1, then the Python AST-shape groups numbered after them in
/// their own order. Returns the groups and the renumbering `(check_id, old id) -> new id`.
fn frozen_clone_groups(checks: &[Value]) -> (Vec<Value>, Vec<(String, u64, u64)>) {
    let mut lexical = vec![];
    let mut semantic = vec![];
    for id in ["python.dup", "cpp.dup"] {
        let Some(block) = check(checks, id) else {
            continue;
        };
        for group in block["clone_groups"].as_array().into_iter().flatten() {
            if group["detection"] == "type2-lexical-region" {
                lexical.push((id, group.clone()));
            } else {
                semantic.push((id, group.clone()));
            }
        }
    }
    let locations = |group: &Value| -> Vec<(String, u64, u64)> {
        group["occurrences"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|occurrence| {
                (
                    occurrence["file_path"]
                        .as_str()
                        .unwrap_or_default()
                        .to_owned(),
                    occurrence["start_line"].as_u64().unwrap_or(0),
                    occurrence["end_line"].as_u64().unwrap_or(0),
                )
            })
            .collect()
    };
    lexical.sort_by(|(_, left), (_, right)| {
        let size = |group: &Value| {
            (
                std::cmp::Reverse(group["lines_count"].as_u64().unwrap_or(0)),
                std::cmp::Reverse(group["occurrences_count"].as_u64().unwrap_or(0)),
            )
        };
        size(left)
            .cmp(&size(right))
            .then_with(|| locations(left).cmp(&locations(right)))
    });
    semantic.sort_by_key(|(_, group)| group["id"].as_u64().unwrap_or(0));
    let mut merged = vec![];
    let mut numbers = vec![];
    for (index, (id, mut group)) in lexical.into_iter().chain(semantic).enumerate() {
        let number = index as u64 + 1;
        numbers.push(((*id).to_owned(), group["id"].as_u64().unwrap_or(0), number));
        group["id"] = json!(number);
        merged.push(group);
    }
    (merged, numbers)
}

/// Rename the clone targets (`CloneGroup#<n>`, `metrics.clone_group`) and their adapted findings
/// (`primary_location.label`, `metrics.clone_group`) by `numbers`; a target's check is its
/// native target scope's task.
fn renumber_clone_targets(engine: &mut Value, numbers: &[(String, u64, u64)]) {
    let checks = checks(engine);
    let check_of = |task: &Value| {
        checks
            .iter()
            .find(|check| &check["task_id"] == task)
            .and_then(|check| check["check_id"].as_str())
            .map(str::to_owned)
    };
    let scopes = engine["extra"]["target_scopes"].clone();
    let mut renamed = vec![];
    if let Some(targets) = engine["targets"].as_array_mut() {
        for (index, target) in targets.iter_mut().enumerate() {
            let Some(old) = target["metrics"]["clone_group"].as_u64() else {
                continue;
            };
            let Some(owner) = scopes
                .as_array()
                .into_iter()
                .flatten()
                .find(|scope| scope["target_index"].as_u64() == Some(index as u64))
                .and_then(|scope| check_of(&scope["task_id"]))
            else {
                continue;
            };
            let Some((_, _, new)) = numbers
                .iter()
                .find(|(check, id, _)| *check == owner && *id == old)
            else {
                continue;
            };
            let previous = target["target_name"].clone();
            target["target_name"] = json!(format!("CloneGroup#{new}"));
            target["metrics"]["clone_group"] = json!(new);
            renamed.push((
                target["file_path"].clone(),
                target["start_line"].clone(),
                previous,
                *new,
            ));
        }
    }
    if let Some(findings) = engine["findings"].as_array_mut() {
        for finding in findings.iter_mut() {
            let location = &finding["primary_location"];
            let Some((_, _, _, new)) = renamed.iter().find(|(path, line, label, _)| {
                &location["path"] == path
                    && &location["start_line"] == line
                    && &location["label"] == label
            }) else {
                continue;
            };
            let new = *new;
            finding["primary_location"]["label"] = json!(format!("CloneGroup#{new}"));
            if finding["metrics"]["clone_group"].is_object() {
                finding["metrics"]["clone_group"]["value"] = json!(new);
            }
        }
    }
}

/// Frozen `DupEngine.run` verdict of the one scan: `evaluate_status(rate > fail_pct, rate >
/// warn_pct, mode)`; under both thresholds the WARN clone targets become PASS (their findings
/// `info`), so one language crossing a threshold alone does not decide the engine. Returns
/// whether the native verdict or a target changed.
fn dup_verdict(engine: &mut Value, context: &Context<'_>, rate: f64) -> bool {
    let number = |key: &str, default: f64| {
        context
            .settings
            .get(key)
            .and_then(Value::as_f64)
            .unwrap_or(default)
    };
    let fail = rate > number("fail_pct", 15.0);
    let warn = rate > number("warn_pct", 5.0);
    let status = evaluate_status(fail, warn, setting(context, "mode").unwrap_or("pass_warn"));
    let mut changed = engine["status"] != status;
    engine["status"] = json!(status);
    if fail || warn {
        return changed;
    }
    let mut realigned = vec![];
    if let Some(targets) = engine["targets"].as_array_mut() {
        for target in targets
            .iter_mut()
            .filter(|target| target["status"] == "WARN")
        {
            target["status"] = json!("PASS");
            realigned.push((
                target["file_path"].clone(),
                target["start_line"].clone(),
                target["target_name"].clone(),
                target["message"].clone(),
            ));
        }
    }
    if let Some(findings) = engine["findings"].as_array_mut() {
        for finding in findings.iter_mut() {
            let location = &finding["primary_location"];
            let key = (
                location["path"].clone(),
                location["start_line"].clone(),
                location["label"].clone(),
                finding["message"].clone(),
            );
            if realigned.contains(&key) {
                finding["severity"] = json!("info");
            }
        }
    }
    changed |= !realigned.is_empty();
    changed
}

/// Keep the engine's targets listed in `order` (old indices, in their new order) and keep the
/// native target scopes naming the same targets (`target_scopes[].target_index`); the scope of a
/// dropped target is dropped with it.
fn reorder_targets(engine: &mut Value, order: &[usize]) {
    let targets = targets(engine);
    if order.len() == targets.len() && order.iter().enumerate().all(|(new, old)| new == *old) {
        return;
    }
    engine["targets"] = json!(
        order
            .iter()
            .map(|old| targets[*old].clone())
            .collect::<Vec<_>>()
    );
    let position: std::collections::BTreeMap<u64, usize> = order
        .iter()
        .enumerate()
        .map(|(new, old)| (*old as u64, new))
        .collect();
    if let Some(scopes) = engine["extra"]["target_scopes"].as_array_mut() {
        scopes.retain_mut(|scope| match scope["target_index"].as_u64() {
            Some(old) => match position.get(&old) {
                Some(new) => {
                    scope["target_index"] = json!(new);
                    true
                }
                None => false,
            },
            None => true,
        });
    }
}

/// Drop the targets `matches` selects with their adapted findings (same path, line and message)
/// and their native target scopes.
pub(crate) fn drop_targets(engine: &mut Value, matches: impl Fn(&Value) -> bool) {
    let targets = targets(engine);
    let (dropped, kept): (Vec<usize>, Vec<usize>) =
        (0..targets.len()).partition(|index| matches(&targets[*index]));
    if dropped.is_empty() {
        return;
    }
    for index in &dropped {
        let target = &targets[*index];
        if let Some(findings) = engine["findings"].as_array_mut() {
            findings.retain(|finding| {
                let location = &finding["primary_location"];
                !(location["path"] == target["file_path"]
                    && location["start_line"] == target["start_line"]
                    && finding["message"] == target["message"])
            });
        }
    }
    reorder_targets(engine, &kept);
}

/// Frozen `DeadEngine.run` beside the native Python heuristic: the configured C++ policies and,
/// without a native C++ dead check (no C++ scope), the empty C++ outcomes frozen records
/// (`CppUnusedFunctionOutcome(mode="not-applicable")`, the linker `off`/`not-applicable`
/// outcome, NOT_APPLICABLE scope evidence); the summary counts every dead symbol.
fn complete_dead(engine: &mut Value, context: &Context<'_>) -> bool {
    let unused_policy = setting(context, "cpp_unused").unwrap_or("auto").to_owned();
    let linker_policy = setting(context, "cpp_linker").unwrap_or("off").to_owned();
    let mut required_restored = false;
    let checks = checks(engine);
    // A `cpp.dead` check blocked for want of any C/C++ source is frozen's scope without C++
    // candidates.
    let cpp = checks.iter().any(|check| {
        check["check_id"] == "cpp.dead" && check["summary"] != "no sources in scope for cpp"
    });
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
        } else if python.is_empty() {
            // Frozen `DeadCodeEngine.run` with no source of either language.
            extra["language_evidence"] =
                json!({"python": "NOT_APPLICABLE", "cpp": "NOT_APPLICABLE"});
            insert_absent(extra, "dead_symbols_count", json!(0));
        }
        // The measured source snapshot of the Python half is the whole frozen inventory (an
        // empty one without any source).
        for (key, value) in [
            ("source_files_analyzed", json!(0)),
            ("source_files_snapshotted", json!(0)),
            ("source_bytes_analyzed", json!(0)),
            ("source_files_excluded", json!(0)),
            ("source_exclusion_counts", json!({})),
        ] {
            insert_absent(extra, key, value);
        }
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
        // Frozen `_effective_required`: a linker scope error is an analysis failure, so an
        // engine planned optional for an unavailable automatic scope keeps its configuration.
        if engine["extra"]["cpp_linker_mode"] == "error"
            && engine["required"] == false
            && context
                .settings
                .get("required")
                .and_then(Value::as_bool)
                .unwrap_or(true)
        {
            engine["required"] = json!(true);
            required_restored = true;
        }
        complete_cpp_dead(engine, &linker_policy, &python);
        if unused_policy == "off" && linker_policy == "off" {
            disabled_scope_finding(engine);
            disabled_beside_excluded_python(engine, &python);
        }
    }
    // Frozen `_collect_sources` keeps its empty `AnalysisSourceInventory((), (), 0)` when
    // `read_analysis_sources` raises: a source intake stop reports nothing read or excluded.
    if targets(engine)
        .iter()
        .any(|target| target["target_name"] == "SourceInputError" && target["status"] == "ERROR")
    {
        let extra = &mut engine["extra"];
        for key in [
            "source_files_analyzed",
            "source_files_snapshotted",
            "source_bytes_analyzed",
            "source_files_excluded",
        ] {
            extra[key] = json!(0);
        }
        extra["source_exclusion_counts"] = json!({});
    }
    let extra = &mut engine["extra"];
    if let Some(count) = extra["dead_symbols_count"].as_u64() {
        extra["metrics_summary"] = json!(format!("{count} dead symbols"));
    }
    required_restored
}

/// Frozen `_analyze_cpp_scope` with both C++ scopes off: the `C++UnusedFunctionsDisabled`
/// target's finding is the bounded compiler scope record (low confidence, its own explanation and
/// remediation), not the generic adapted target finding.
fn disabled_scope_finding(engine: &mut Value) {
    if let Some(findings) = engine["findings"].as_array_mut() {
        for finding in findings {
            if finding["primary_location"]["label"] == "C++UnusedFunctionsDisabled" {
                finding["confidence"] = json!("low");
                finding["explanation"] = json!(
                    "This target records the bounded TU-local compiler unused-function scope."
                );
                finding["remediation"] = json!(
                    "Restore a complete, safe compilation context when exact analysis did not run."
                );
            }
        }
    }
}

/// Frozen `DeadCodeEngine.run` with both C++ scopes off beside a Python scope whose every source
/// was excluded as vendor/generated: `_append_empty_scope_target` adds no target when both modes
/// are off, the evidence is NOT_RUN, the summary names the disabled policy, and the inventory
/// counts the excluded sources (`source_exclusion_counts`, nothing analyzed or snapshotted).
fn disabled_beside_excluded_python(engine: &mut Value, python: &[&Value]) {
    const EXCLUDED: &str = "All selected sources were excluded by the generated/vendor policy; \
                            dead-code analysis was not run";
    let excluded = |item: &Value| {
        item["target_name"] == "DeadCode" && item["file_path"] == "." && item["message"] == EXCLUDED
    };
    if !targets(engine).iter().any(excluded) {
        return;
    }
    if let Some(targets) = engine["targets"].as_array_mut() {
        targets.retain(|target| !excluded(target));
    }
    if let Some(findings) = engine["findings"].as_array_mut() {
        findings.retain(|finding| {
            !(finding["primary_location"]["label"] == "DeadCode"
                && finding["primary_location"]["path"] == "."
                && finding["message"] == EXCLUDED)
        });
    }
    let mut counts = serde_json::Map::new();
    for block in python {
        if let Some(reasons) = block["excluded_source_counts"].as_object() {
            for (reason, count) in reasons {
                let total = counts.get(reason).and_then(Value::as_u64).unwrap_or(0)
                    + count.as_u64().unwrap_or(0);
                counts.insert(reason.clone(), json!(total));
            }
        }
    }
    let total: u64 = counts.values().filter_map(Value::as_u64).sum();
    engine["status"] = json!("SKIP");
    engine["evidence"] = json!("NOT_RUN");
    engine["summary"] = json!("C++ dead-code analysis disabled by policy");
    let extra = &mut engine["extra"];
    extra["source_exclusion_counts"] = Value::Object(counts);
    extra["source_files_excluded"] = json!(total);
    for key in [
        "source_files_analyzed",
        "source_files_snapshotted",
        "source_bytes_analyzed",
    ] {
        extra[key] = json!(0);
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
    // Frozen `_analyze_linker_scope`: an analysis error before it leaves the scope not run.
    let linker_mode = if linker_policy == "off" {
        "off"
    } else {
        "not-applicable"
    };
    for (key, value) in [
        ("cpp_linker_mode", json!(linker_mode)),
        ("cpp_linker_symbols_count", json!(0)),
        ("cpp_linker_targets_checked", json!(0)),
        ("cpp_linker_sources_checked", json!(0)),
        ("cpp_linker_discarded_sections_observed", json!(0)),
        ("cpp_linker_ambiguous_sections_excluded", json!(0)),
        ("cpp_linker_sections_kept_by_another_target", json!(0)),
        ("cpp_linker_warnings", json!([])),
        ("cpp_linker_details", json!([])),
    ] {
        insert_absent(extra, key, value);
    }
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
    // Frozen `DeadCodeEngine.run` reads one inventory over both languages: beside a Python
    // heuristic the source counters are the sums of the Python and C/C++ halves.
    if !python.is_empty() {
        // The compiler scope's own counters stay its own (only one check's extra is lifted).
        for (key, default) in [
            ("cpp_unused_configurations_checked", json!(0)),
            ("cpp_unused_sources_checked", json!(0)),
            ("cpp_unused_non_tu_diagnostics_excluded", json!(0)),
            ("cpp_unused_warnings", json!([])),
            ("cpp_unused_details", json!([])),
        ] {
            let value = cpp.get(key).cloned().unwrap_or(default);
            insert_absent(extra, key, value);
        }
        let blocks: Vec<&Value> = python.iter().copied().chain([&cpp]).collect();
        for key in [
            "source_files_analyzed",
            "source_files_snapshotted",
            "source_bytes_analyzed",
            "source_files_excluded",
        ] {
            if let Some(total) = blocks
                .iter()
                .map(|block| block[key].as_u64())
                .sum::<Option<u64>>()
            {
                extra[key] = json!(total);
            }
        }
        if blocks
            .iter()
            .all(|block| block["source_exclusion_counts"].is_object())
        {
            let mut counts = serde_json::Map::new();
            for block in &blocks {
                for (reason, count) in block["source_exclusion_counts"]
                    .as_object()
                    .into_iter()
                    .flatten()
                {
                    let total = counts.get(reason).and_then(Value::as_u64).unwrap_or(0)
                        + count.as_u64().unwrap_or(0);
                    counts.insert(reason.clone(), json!(total));
                }
            }
            extra["source_exclusion_counts"] = Value::Object(counts);
        }
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
    // Frozen `_cpp_evidence`/`_linker_evidence` without readable C++ sources: NOT_RUN after an
    // intake fault, NOT_APPLICABLE when every candidate was excluded.
    let intake_fault = named("SourceInputError").is_some();
    let without_sources = |state: &'static str| if intake_fault { "NOT_RUN" } else { state };
    if mode == "not-applicable" {
        // `CppUnusedFunctionOutcome(mode="not-applicable")`: nothing configured or checked.
        for (key, value) in [
            ("cpp_unused_configurations_checked", json!(0)),
            ("cpp_unused_sources_checked", json!(0)),
            ("cpp_unused_non_tu_diagnostics_excluded", json!(0)),
            ("cpp_unused_warnings", json!([])),
            ("cpp_unused_details", json!([])),
        ] {
            insert_absent(extra, key, value);
        }
    }
    if mode == "off" {
        // Frozen `_analyze_cpp_scope` with `cpp_unused = "off"`: the disabled outcome.
        for (key, value) in [
            ("cpp_unused_configurations_checked", json!(0)),
            ("cpp_unused_sources_checked", json!(0)),
            ("cpp_unused_non_tu_diagnostics_excluded", json!(0)),
            ("cpp_unused_warnings", json!([])),
            ("cpp_unused_details", json!([])),
        ] {
            extra[key] = value;
        }
    }
    if linker_policy != "off" {
        // Frozen `DeadCodeEngine.run` beside a GNU ELF linker scope: per-scope and language
        // evidence and every dead symbol of both C++ scopes.
        let measured = |exact: bool| if exact { "MEASURED" } else { "NOT_RUN" };
        let compiler = measured(mode == "exact");
        let linker = measured(extra["cpp_linker_mode"] == "exact");
        extra["cpp_scope_evidence"] =
            json!({"compiler_unused": compiler, "gnu_elf_linker": linker});
        let python_evidence = python
            .iter()
            .find_map(|block| block["language_evidence"]["python"].as_str())
            .unwrap_or("NOT_APPLICABLE");
        let cpp_evidence = if compiler == "MEASURED" || linker == "MEASURED" {
            "MEASURED"
        } else {
            "NOT_RUN"
        };
        extra["language_evidence"] = json!({"python": python_evidence, "cpp": cpp_evidence});
        let python_issues: u64 = python
            .iter()
            .filter_map(|block| block["python_issues"].as_u64())
            .sum();
        let functions = extra["cpp_unused_functions_count"].as_u64().unwrap_or(0);
        let symbols = extra["cpp_linker_symbols_count"].as_u64().unwrap_or(0);
        extra["dead_symbols_count"] = json!(python_issues + functions + symbols);
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
            "not-applicable" => without_sources("NOT_APPLICABLE"),
            _ => "NOT_RUN",
        };
        extra["cpp_scope_evidence"] =
            json!({"compiler_unused": compiler, "gnu_elf_linker": "NOT_RUN"});
        let python_evidence = python
            .iter()
            .find_map(|block| block["language_evidence"]["python"].as_str())
            .unwrap_or("NOT_APPLICABLE");
        // The language axis stays NOT_RUN for a C++ candidate set whose scope did not measure.
        let language = if mode == "exact" {
            "MEASURED"
        } else {
            "NOT_RUN"
        };
        extra["language_evidence"] = json!({"python": python_evidence, "cpp": language});
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
/// Frozen `TestQualityMixin._build_test_suites`: one suite per target file in target order,
/// coverage and test-quality targets excluded.
pub(crate) fn frozen_test_suites(targets: &[Value]) -> Vec<Value> {
    let mut suites: Vec<Value> = vec![];
    for target in targets {
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
    suites
}

/// Frozen `_coverage_policy_snapshot` values of a measured Python coverage run: the line and
/// function metrics and the per-file line coverage are the policy targets' (rounded) actuals,
/// the branch metric rounded to one decimal; native's own policy keys stay beside them.
pub(crate) fn frozen_coverage_policy(engine: &mut Value) {
    let targets = targets(engine);
    let policy = &mut engine["extra"]["coverage_policy"];
    if !policy["metrics"].is_object() {
        return;
    }
    let mut files = Map::new();
    for target in &targets {
        let actual = target["metrics"]["actual"].as_f64();
        match (target["target_name"].as_str(), actual) {
            (Some("Coverage:Overall line"), Some(actual)) => {
                policy["metrics"]["line"] = json!(actual)
            }
            (Some("Coverage:Functions"), Some(actual)) => {
                policy["metrics"]["function"] = json!(actual)
            }
            (Some("Coverage:File"), Some(actual)) => {
                if let Some(path) = target["file_path"].as_str() {
                    files.insert(path.to_owned(), json!(actual));
                }
            }
            _ => {}
        }
    }
    if let Some(branch) = policy["metrics"]["branch"].as_f64() {
        policy["metrics"]["branch"] =
            json!(format!("{branch:.1}").parse::<f64>().unwrap_or(branch));
    }
    if !files.is_empty() || policy["files"].is_object() {
        policy["files"] = Value::Object(files);
    }
}

/// Frozen-key shape of a measured legacy Python coverage result (decisions.md "측정된 legacy
/// Python coverage 결과의 native 키" and "statement가 없는 파일의 coverage_files 비율"):
/// * `coverage_policy.metrics.branch`: frozen's `round(branch_cov, 1)`;
/// * `coverage_files`: a file without statements has frozen's `cover` 100.0 (frozen
///   `_python_coverage_rows`; coverage.py reports the same percentage), rows in frozen's
///   (cover, file) order;
/// * `coverage_policy`, `coverage_totals` and `test_quality` keep only frozen's keys and
///   `coverage_provenance` is frozen's `python`/`scope` shape. The native values stay in the
///   native checks' own blocks (`native_checks[].extra`, decisions.md "legacy `extra` 안 native
///   증거 키"), which keep them under native names.
pub(crate) fn frozen_python_coverage(engine: &mut Value) {
    let extra = &mut engine["extra"];
    // Frozen `_coverage_policy_snapshot` (test_coverage.py:440): `metrics.branch` is always
    // `round(branch_cov, 1)`, the vacuous 100.0 of a measured scope without a branch included.
    if let (Some(branch), Some(metrics)) = (
        extra["branch_coverage"].as_f64(),
        extra
            .get_mut("coverage_policy")
            .and_then(|policy| policy.get_mut("metrics"))
            .and_then(Value::as_object_mut),
    ) {
        metrics.insert("branch".into(), json!(ici_domain::python_round_one(branch)));
    }
    if let Some(rows) = extra
        .get_mut("coverage_files")
        .and_then(Value::as_array_mut)
    {
        for row in rows.iter_mut() {
            if row["cover"].is_null() && row["stmts"].as_u64() == Some(0) {
                row["cover"] = json!(100.0);
            }
        }
        rows.sort_by(|left, right| {
            let cover = |row: &Value| row["cover"].as_f64().unwrap_or(f64::NEG_INFINITY);
            cover(left)
                .total_cmp(&cover(right))
                .then_with(|| left["file"].as_str().cmp(&right["file"].as_str()))
        });
    }
    let keep = |extra: &mut Value, name: &str, keys: &[&str]| {
        if let Some(fields) = extra.get_mut(name).and_then(Value::as_object_mut) {
            fields.retain(|key, _| keys.contains(&key.as_str()));
        }
    };
    keep(
        extra,
        "coverage_policy",
        &["metrics", "files", "regression_tolerance"],
    );
    keep(
        extra,
        "coverage_totals",
        &["stmts", "miss", "cover", "branch_cover"],
    );
    if let Some(quality) = extra.get_mut("test_quality").and_then(Value::as_object_mut) {
        quality.remove("evidence");
        quality.remove("repeat_observations");
    }
    // Measured coverage only (`coverage_totals`); an unmeasured run keeps frozen's scope alone.
    let present = |key: &str| extra.get(key).is_some_and(Value::is_object);
    if present("coverage_provenance") && present("coverage_totals") {
        let mut sources: Vec<Value> = extra["coverage_files"]
            .as_array()
            .map(|rows| rows.iter().map(|row| row["file"].clone()).collect())
            .unwrap_or_default();
        sources.sort_by(|left, right| left.as_str().cmp(&right.as_str()));
        // Frozen measured coverage only when coverage.py's files equal the discovered Python
        // sources (`parse_coverage_json` expected files), so every count is the row count.
        let count = sources.len();
        extra["coverage_provenance"] = json!({
            "python": {
                "format": "coverage.py-json", "expected_sources": count,
                "covered_sources": count, "function_geometry": "python-ast",
                "source_mapping": "project-relative-exact",
            },
            "scope": frozen_coverage_scope(sources, count),
        });
    }
}

/// Frozen `TestEngine.run` of a Python project whose suite did not complete (a failed coverage
/// or pytest process without parseable results): the counters of a run that measured nothing,
/// no coverage rows, the empty quality block and the coverage scope over the discovered Python
/// sources. Frozen's estimated coverage, TEM, score and metrics summary stay absent (validation
/// .json finding_site:src/ici/engines/test.py:352:12, no fabricated values).
fn frozen_incomplete_python_run(engine: &mut Value, context: &Context<'_>) {
    let checks = checks(engine);
    let python = !checks.is_empty()
        && checks.iter().all(|check| {
            check["check_id"]
                .as_str()
                .is_some_and(|id| id.starts_with("python."))
        });
    let unmeasured = checks
        .iter()
        .any(|check| check["check_id"] == "python.test" && check["state"] != "SUCCEEDED");
    if !python || !unmeasured || engine["extra"].get("passed_tests").is_some() {
        return;
    }
    // Frozen `TestEngine.run` reports `_parse_pytest_result`'s counts also beside its tool error
    // (test_output.py:64-109): the python.test check's frozen legacy case counts, else none.
    let (passed, total, skipped) = checks
        .iter()
        .filter(|check| check["check_id"] == "python.test")
        .flat_map(check_blocks)
        .chain(
            checks
                .iter()
                .filter(|check| check["check_id"] == "python.test")
                .filter_map(|check| check["extra"].as_object())
                .flat_map(|fields| fields.values()),
        )
        .find_map(|extra| {
            let counts = &extra["legacy_case_counts"];
            Some((
                counts["passed"].as_u64()?,
                counts["total"].as_u64()?,
                counts["skipped"].as_u64().unwrap_or(0),
            ))
        })
        .unwrap_or((0, 0, 0));
    let quality = frozen_quality(None, context.profile);
    let extra = &mut engine["extra"];
    for (key, value) in [
        ("passed_tests", json!(passed)),
        ("total_tests", json!(total)),
        ("skipped_tests", json!(skipped)),
        // `calculate_tem`: passed over total, 0.0 without a test.
        (
            "pass_rate",
            json!(if total == 0 {
                0.0
            } else {
                passed as f64 / total as f64
            }),
        ),
        ("line_coverage", Value::Null),
        ("coverage_files", json!([])),
        ("function_rows", json!([])),
        ("coverage_totals", Value::Null),
        ("coverage_policy", Value::Null),
        ("quality_enabled", json!(false)),
    ] {
        extra[key] = value;
    }
    for (key, value) in quality.as_object().into_iter().flatten() {
        if !matches!(key.as_str(), "enabled" | "mode" | "profile") {
            extra[key] = value.clone();
        }
    }
    extra["test_quality"] = quality;
    insert_absent(extra, "test_suites", json!([]));
    extra["coverage_provenance"] = json!({
        "scope": frozen_coverage_scope(vec![], context.python_sources),
    });
}

/// Frozen `_record_coverage_scope` of a project without C++ sources.
pub(crate) fn frozen_coverage_scope(included: Vec<Value>, python_sources: usize) -> Value {
    json!({
        "included_sources": included,
        "excluded_entry_points": [],
        "exclusion_rules": [
            {"kind": "tests",
             "basis": "tests are execution inputs, not production coverage sources"},
            {"kind": "generated-vendor-cache",
             "basis": "bounded project source discovery excludes ignored directories"},
            {"kind": "entry-point",
             "basis": "C++ main definitions are excluded from the test-link coverage set"},
        ],
        "python_context": "aggregate-project-suite",
        "cpp_context": "aggregate-test-binaries",
        "discovered_python_sources": python_sources,
        "discovered_cpp_sources": 0,
    })
}

/// Frozen `TestEngine.run` when pytest collected nothing (`_parse_pytest_result`: exit 5 adds the
/// located `[Python] Tests` FAIL) beside a measured C++ suite (`tests/**/*.cpp`, test.py:268):
/// the C++ counts, gcov rows and TEM stand, while the missing Python coverage is the optional
/// warning (`_coverage_failure_messages`), so neither coverage policy nor threshold targets are
/// evaluated (`coverage_complete` false), the verdict is `evaluate_status(has_failure,
/// has_warn, mode)`, the evidence ESTIMATED and the summary carries the optional suffix
/// (test.py:331). Native keeps its incomplete aggregate; only this legacy view is rebuilt from
/// the measured C++ half.
fn empty_pytest_beside_cpp_suite(engine: &mut Value, context: &Context<'_>) -> bool {
    let checks = checks(engine);
    let complete =
        |check: &Value| check["state"] == "SUCCEEDED" && check["evidence_complete"] == true;
    let empty_pytest = |check: &Value| {
        check["check_id"] == "python.test"
            && check["state"] == "FAILED"
            && matches!(
                check["summary"].as_str(),
                Some(
                    "pytest did not report a completed nonempty suite (exit 5)"
                        | "pytest did not report a completed nonempty suite (exit 0)"
                )
            )
    };
    let shape = checks.iter().all(|check| match check["check_id"].as_str() {
        Some("python.test") => empty_pytest(check),
        Some("python.coverage") => {
            check["state"] == "BLOCKED"
                && check["summary"]
                    .as_str()
                    .is_some_and(|summary| summary.ends_with("has no complete evidence"))
        }
        Some("cpp.test" | "cpp.coverage") => complete(check),
        _ => false,
    });
    if !shape
        || !checks.iter().any(empty_pytest)
        || !has_checks(engine, &["cpp.test", "cpp.coverage"])
    {
        return false;
    }
    let all = targets(engine);
    let Some(python) = all
        .iter()
        .find(|target| {
            target["target_name"] == "[Python] Tests"
                && target["message"] == "No tests collected"
                && target["status"] == "FAIL"
        })
        .cloned()
    else {
        return false;
    };
    let cases: Vec<Value> = all
        .into_iter()
        .filter(|target| {
            target["target_name"]
                .as_str()
                .is_some_and(|name| name.starts_with("[C++] "))
        })
        .collect();
    let mut alone = engine.clone();
    alone["extra"]["native_checks"] = json!(
        checks
            .iter()
            .filter(|check| check["check_id"]
                .as_str()
                .is_some_and(|id| id.starts_with("cpp.")))
            .collect::<Vec<_>>()
    );
    if let Some(extra) = alone["extra"].as_object_mut() {
        extra.remove("test_aggregation_limitation");
    }
    alone["targets"] = json!(cases);
    alone["status"] = json!("FAIL");
    if ici_reporting::legacy_test_view(&mut alone).is_err()
        || alone["extra"].get("test_aggregation_limitation").is_some()
        || !alone["score"].is_number()
    {
        return false;
    }
    crate::cpp::frozen_cpp_test_extra(&mut alone, context);
    let mut located = vec![python];
    located.extend(cases);
    // The C++ half's frozen keys; its native aggregate bookkeeping stays out of the legacy view.
    for (key, value) in alone["extra"].as_object().into_iter().flatten() {
        if !matches!(
            key.as_str(),
            "native_checks" | "test_execution" | "coverage_suites"
        ) {
            engine["extra"][key] = value.clone();
        }
    }
    let extra = &mut engine["extra"];
    extra["coverage_policy"] = Value::Null;
    // `_build_coverage_summary`: the attempted Python half measured no coverage.
    if let Some(source) = extra["coverage_source"]
        .as_str()
        .filter(|source| *source != "estimated" && !source.ends_with(" (partial)"))
    {
        extra["coverage_source"] = json!(format!("{source} (partial)"));
    }
    extra["test_suites"] = json!(frozen_test_suites(&located));
    // The deep pass after a pytest run (`_python_test_attempted`) without outcome evidence.
    if extra["test_quality"].is_object() {
        let mut quality = extra["test_quality"].clone();
        zero_tests_quality(&mut quality, context);
        for key in ["enabled", "repeat_runs"] {
            extra[if key == "enabled" {
                "quality_enabled"
            } else {
                key
            }] = quality[key].clone();
        }
        extra["test_quality"] = quality;
    }
    engine["score"] = alone["score"].clone();
    engine["max_score"] = alone["max_score"].clone();
    let summary = format!(
        "{}; Coverage evidence ESTIMATED (optional; not threshold evidence)",
        alone["summary"].as_str().unwrap_or_default()
    );
    let mode = setting(context, "mode").unwrap_or("pass_fail");
    let status = evaluate_status(true, true, mode);
    ici_reporting::legacy_verdict(engine, status, "ESTIMATED", &summary, located).is_ok()
}

fn complete_test(engine: &mut Value, context: &Context<'_>) {
    if engine["extra"].get("no_tests_collected").is_none() {
        complete_optional_coverage(engine, context.profile);
        frozen_incomplete_python_run(engine, context);
        let python = has_checks(engine, &["python.test"]);
        if python {
            // In target (pytest execution) order. The threshold breaches are not test cases
            // (accepted on validation.json finding_site:src/ici/engines/test_quality.py:87:16:
            // frozen `_build_test_suites` counted them as failed tests of a `.` suite).
            let cases: Vec<Value> = targets(engine)
                .into_iter()
                .filter(|target| {
                    !target["target_name"]
                        .as_str()
                        .is_some_and(|name| name.starts_with("Threshold: "))
                })
                .collect();
            engine["extra"]["test_suites"] = json!(frozen_test_suites(&cases));
            if engine["extra"]["test_quality"].is_object() {
                insert_absent(
                    &mut engine["extra"]["test_quality"],
                    "profile",
                    json!(context.profile),
                );
            }
        }
        if has_checks(engine, &["python.coverage"]) && !has_checks(engine, &["cpp.coverage"]) {
            frozen_coverage_policy(engine);
            frozen_python_coverage(engine);
        }
        if !python && has_checks(engine, &["cpp.test"]) {
            crate::cpp::frozen_cpp_test_extra(engine, context);
            crate::legacy_hybrid::cpp_incomplete_test_measured(engine, context);
        }
        return;
    }
    let targets = targets(engine);
    let suites = frozen_test_suites(&targets);
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
    let mut quality = frozen_quality(None, context.profile);
    let python = engine["extra"]["no_tests_collected"]
        .as_array()
        .is_some_and(|languages| languages.iter().any(|language| language == "Python"));
    if python {
        zero_tests_quality(&mut quality, context);
        zero_tests_observed(&mut quality, engine);
    }
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
    extra["quality_enabled"] = quality["enabled"].clone();
    extra["test_quality"] = quality;
    // An empty list only marks collected-but-unexecuted tests for this completion; nothing
    // measured coverage, so frozen's provenance is the empty project scope alone.
    if extra["no_tests_collected"] == json!([]) {
        extra["coverage_provenance"] = if crate::cpp::records_coverage_scope(context) {
            json!({"scope": crate::cpp::frozen_project_scope(vec![], context)})
        } else {
            json!({})
        };
        if let Some(fields) = extra.as_object_mut() {
            fields.remove("no_tests_collected");
        }
    }
}

/// Frozen `_run_deep_test_quality` after `_mark_zero_tests("Python")` (a Python/hybrid project
/// whose Python facet has no test file: `_python_test_attempted` is set, no pytest output or
/// outcomes exist). Under the deep profile with `[engines.test.quality] enabled` (default true,
/// a non-bool reads true) the block is enabled in `mode` (`report`/`warn`, else `report`) with
/// the base run counted (`repeat_runs` 1); nothing else is observed.
fn zero_tests_quality(quality: &mut Value, context: &Context<'_>) {
    let settings = &context.settings["quality"];
    if context.profile != "deep" || settings["enabled"] == false {
        return;
    }
    quality["enabled"] = json!(true);
    quality["mode"] = match settings["mode"].as_str() {
        Some(mode @ ("report" | "warn")) => json!(mode),
        _ => json!("report"),
    };
    quality["repeat_runs"] = json!(1);
}

/// The reruns and mutation probe python.test still ran for frozen's deep pass after a run
/// without test outcomes (`ici_validation` `observe_without_base`): their measured counters.
fn zero_tests_observed(quality: &mut Value, engine: &Value) {
    let checks = checks(engine);
    let Some(block) = checks
        .iter()
        .find(|check| check["check_id"] == "python.test")
        .and_then(|check| check_blocks(check).into_iter().next())
    else {
        return;
    };
    for (source, keys) in [
        (
            &block["test_quality"],
            &[
                "repeat_runs",
                "repeat_reruns",
                "repeat_cases",
                "repeat_unavailable",
                "repeat_timeouts",
                "flaky_tests",
                "flaky_test_inventory",
            ][..],
        ),
        (
            &block["mutation"],
            &[
                "mutation_probes",
                "mutation_available",
                "mutation_unavailable",
                "mutation_status",
            ][..],
        ),
    ] {
        for key in keys {
            if let Some(value) = source.get(*key).filter(|value| !value.is_null()) {
                quality[*key] = value.clone();
            }
        }
    }
}

/// Frozen `TestEngine.run` whose optional coverage module is missing: the measured suite of the
/// native `python.test` check (counts, pass rate, suites, quality block) and no measured coverage
/// rows/totals/policy; frozen's coverage estimate and TEM stay absent.
/// Frozen `empty_quality_info()` with the analysis profile, overlaid with the values the native
/// quality block measured for the same frozen keys (native-only keys are not frozen's).
pub(crate) fn frozen_quality(native: Option<&Value>, profile: &str) -> Value {
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

/// Frozen `coverage_required` summary of a coverage report `parse_coverage_json` refused.
pub(crate) const REQUIRED_PYTHON_COVERAGE_MALFORMED: &str = "Python coverage JSON was missing or malformed; Python coverage evidence was unavailable or malformed";

fn complete_optional_coverage(engine: &mut Value, profile: &str) {
    if !engine["summary"].as_str().is_some_and(|summary| {
        summary.contains("; coverage not measured (optional): ")
            || summary == REQUIRED_PYTHON_COVERAGE_MALFORMED
            || ici_validation::python_coverage::legacy_coverage_json_failure(
                summary.split("; ").next().unwrap_or(summary),
            )
    }) {
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

#[cfg(test)]
mod hybrid_tests {
    use super::*;

    fn engine(name: &str, checks: &[&str], targets: Value) -> Value {
        let checks: Vec<Value> = checks
            .iter()
            .map(|id| json!({"check_id": id, "extra": {}}))
            .collect();
        json!({"engine_name": name, "status": "PASS", "evidence": "MEASURED", "summary": "",
            "score": null, "targets": targets, "findings": [],
            "extra": {"native_checks": checks}})
    }

    fn context(settings: &Value) -> Context<'_> {
        Context {
            settings,
            profile: "",
            python_sources: 0,
            root: std::path::Path::new("."),
            cpp_sources: &[],
            cpp_scope: false,
        }
    }

    #[test]
    fn hybrid_type_adds_the_cpp_skip_warning_and_estimates() {
        let mut value = engine(
            "type",
            &["python.type", "cpp.type"],
            json!([{"file_path": "src/a.py", "target_name": "MypyError", "status": "FAIL"},
                {"file_path": "src/c.cpp", "target_name": "C++TypeCheck", "status": "SKIP"}]),
        );
        value["status"] = json!("FAIL");
        value["findings"] = json!([{"rule_id": "ici.legacy.type.target", "confidence": "high"}]);
        assert!(type_verdict(&mut value, "pass_warn", false));
        assert_eq!(value["status"], "WARN");
        assert_eq!(value["evidence"], "ESTIMATED");
        assert_eq!(
            value["summary"],
            "1 Type Findings, 1 Warnings; C++ type checking is skipped for 1 source file(s); \
             evidence is ESTIMATED"
        );
        assert_eq!(value["findings"][0]["confidence"], "medium");
        // A clean Python half is still a warning (frozen counts the tool warning).
        let mut clean = engine(
            "type",
            &["python.type", "cpp.type"],
            json!([{"file_path": "src/c.cpp", "target_name": "C++TypeCheck", "status": "SKIP"}]),
        );
        assert!(type_verdict(&mut clean, "pass_warn_fail", false));
        assert_eq!(clean["status"], "WARN");
    }

    #[test]
    fn type_without_cpp_or_with_nothing_applicable_keeps_its_verdict() {
        let mut python = engine(
            "type",
            &["python.type"],
            json!([{"file_path": "src/a.py", "target_name": "MypyError", "status": "FAIL"}]),
        );
        assert!(!type_verdict(&mut python, "pass_warn", false));
        assert_eq!(python["evidence"], "MEASURED");
        // Frozen `nothing_applies` (no Python file) is SKIP; a tool error stays ERROR.
        for status in ["SKIP", "ERROR"] {
            let mut value = engine(
                "type",
                &["cpp.type"],
                json!([{"file_path": "src/c.cpp", "target_name": "C++TypeCheck", "status": "SKIP"}]),
            );
            value["status"] = json!(status);
            assert!(!type_verdict(&mut value, "pass_warn", false));
            assert_eq!(value["status"], status);
        }
        // The missing-mypy fallback keeps both warnings in frozen order.
        let mut fallback = engine(
            "type",
            &["python.type", "cpp.type"],
            json!([{"file_path": "src/c.cpp", "target_name": "C++TypeCheck", "status": "SKIP"},
                {"file_path": "src/d.cpp", "target_name": "C++TypeCheck", "status": "SKIP"}]),
        );
        fallback["status"] = json!("ERROR");
        assert!(type_verdict(&mut fallback, "pass_fail", true));
        assert_eq!(fallback["status"], "FAIL");
        assert_eq!(
            fallback["summary"],
            "0 Type Findings, 2 Warnings; Mypy is unavailable; AST fallback is ESTIMATED; \
             C++ type checking is skipped for 2 source file(s); evidence is ESTIMATED"
        );
    }

    fn dead(cpp_mode: &str, python: &str, targets: Value) -> Value {
        let mut value = engine("dead", &["python.dead", "cpp.dead"], targets);
        value["evidence"] = json!("NOT_RUN");
        value["summary"] = json!("No dead-code findings detected in the analyzed scopes");
        value["extra"]["language_evidence"] = json!({"python": python, "cpp": "NOT_RUN"});
        value["extra"]["cpp_unused_policy"] = json!("auto");
        value["extra"]["cpp_unused_mode"] = json!(cpp_mode);
        value["extra"]["cpp_linker_policy"] = json!("off");
        value["extra"]["cpp_linker_mode"] = json!("off");
        value["extra"]["cpp_unused_functions_count"] = json!(0);
        value["extra"]["cpp_linker_symbols_count"] = json!(0);
        value
    }

    #[test]
    fn hybrid_dead_with_unavailable_cpp_is_a_python_estimate_warning() {
        let settings = json!({});
        let mut value = dead(
            "unavailable",
            "ESTIMATED",
            json!([{"file_path": "src/a.py", "target_name": "DeadCode", "status": "PASS"}]),
        );
        assert!(dead_verdict(&mut value, &context(&settings)));
        assert_eq!(
            (&value["status"], &value["evidence"], &value["summary"]),
            (
                &json!("WARN"),
                &json!("ESTIMATED"),
                &json!(
                    "Python dead-code analysis completed, but exact C++ analysis was unavailable"
                )
            )
        );
        // `pass_fail` turns the partial-C++ warning into FAIL.
        let settings = json!({"mode": "pass_fail"});
        let mut strict = dead("unavailable", "ESTIMATED", json!([]));
        assert!(dead_verdict(&mut strict, &context(&settings)));
        assert_eq!(strict["status"], "FAIL");
    }

    #[test]
    fn hybrid_dead_without_a_partial_scope_counts_or_passes() {
        let settings = json!({});
        // An exact C++ outcome is no partial scope: the counts summary for Python findings.
        let mut exact = dead(
            "exact",
            "ESTIMATED",
            json!([{"file_path": "src/a.py", "target_name": "unused", "status": "WARN"}]),
        );
        exact["extra"]["cpp_unused_functions_count"] = json!(2);
        assert!(dead_verdict(&mut exact, &context(&settings)));
        assert_eq!(exact["status"], "WARN");
        assert_eq!(
            exact["summary"],
            "3 dead-code finding(s): 1 heuristic Python, 2 compiler-local C/C++, 0 target-local GNU ELF"
        );
        // A disabled C++ policy is no partial scope either: a clean Python half passes.
        let mut off = dead("unavailable", "ESTIMATED", json!([]));
        off["extra"]["cpp_unused_policy"] = json!("off");
        dead_verdict(&mut off, &context(&settings));
        assert_eq!(off["status"], "PASS");
        // No Python source (C++-only verdicts) or an analysis error: unchanged.
        let mut cpp_only = dead("unavailable", "NOT_APPLICABLE", json!([]));
        assert!(!dead_verdict(&mut cpp_only, &context(&settings)));
        assert_eq!(cpp_only["evidence"], "NOT_RUN");
        let mut errored = dead("unavailable", "ESTIMATED", json!([]));
        errored["status"] = json!("ERROR");
        assert!(!dead_verdict(&mut errored, &context(&settings)));
        assert_eq!(errored["status"], "ERROR");
    }

    #[test]
    fn hybrid_complexity_scores_and_counts_both_languages() {
        let settings = json!({"warn_cc": 10});
        let targets = json!([
            {"file_path": "src/a.py", "status": "PASS", "metrics": {"complexity": 2}},
            {"file_path": "src/a.py", "status": "WARN", "metrics": {"complexity": 11}},
            {"file_path": "src/c.cpp", "status": "PASS", "metrics": {"complexity": 1}},
            {"file_path": "src/c.cpp", "status": "WARN", "metrics": {}}]);
        let mut value = engine(
            "complexity",
            &["python.complexity", "cpp.complexity"],
            targets.clone(),
        );
        merge_complexity(&mut value, &context(&settings));
        assert!(value["score"].is_f64());
        assert_eq!(value["score"], json!(11.0));
        assert_eq!(
            value["summary"],
            "Max Cyclomatic Complexity: 11 (limit 10) across 3 functions (2 issues)"
        );
        // One language: the check's own score and summary stand.
        let mut python = engine("complexity", &["python.complexity"], targets);
        merge_complexity(&mut python, &context(&settings));
        assert!(python["score"].is_null());
        assert_eq!(python["summary"], "");
    }

    #[test]
    fn dropped_targets_take_their_findings_and_scopes() {
        let mut value = engine(
            "test",
            &[],
            json!([
                {"file_path": "tests/t.py", "start_line": 4, "target_name": "case", "message": "ok"},
                {"file_path": "src/a.py", "start_line": 1, "target_name": "Coverage capability",
                    "status": "SKIP", "message": "missing"},
                {"file_path": "tests/u.py", "start_line": 2, "target_name": "case2", "message": "ok"}]),
        );
        value["findings"] = json!([
            {"primary_location": {"path": "tests/t.py", "start_line": 4}, "message": "ok"},
            {"primary_location": {"path": "src/a.py", "start_line": 1}, "message": "missing"},
            {"primary_location": {"path": "src/a.py", "start_line": 1}, "message": "other"}]);
        value["extra"]["target_scopes"] = json!([
            {"target_index": 0}, {"target_index": 1}, {"target_index": 2}]);
        let coverage = |target: &Value| target["target_name"] == "Coverage capability";
        drop_targets(&mut value, coverage);
        assert_eq!(value["targets"].as_array().unwrap().len(), 2);
        assert_eq!(value["targets"][1]["file_path"], "tests/u.py");
        assert_eq!(
            value["findings"],
            json!([{"primary_location": {"path": "tests/t.py", "start_line": 4}, "message": "ok"},
                {"primary_location": {"path": "src/a.py", "start_line": 1}, "message": "other"}])
        );
        assert_eq!(
            value["extra"]["target_scopes"],
            json!([{"target_index": 0}, {"target_index": 1}])
        );
        // Nothing selected: unchanged.
        let before = value.clone();
        drop_targets(&mut value, coverage);
        assert_eq!(value, before);
    }

    #[test]
    fn hybrid_dup_orders_scans_by_path_and_keeps_scopes_aligned() {
        let mut value = engine(
            "dup",
            &[],
            json!([
                {"file_path": "src/b.py", "target_name": "DuplicateCode", "status": "PASS"},
                {"file_path": "src/z.py", "target_name": "DuplicateScan", "status": "PASS"},
                {"file_path": "src/a.cpp", "target_name": "DuplicateScan", "status": "PASS"}]),
        );
        let block = |groups: u64, lines: u64, total: u64| {
            json!({"clone_groups_count": groups, "duplicated_lines": lines,
                "total_code_lines": total, "clone_groups": []})
        };
        value["extra"]["native_checks"] = json!([
            {"check_id": "python.dup", "extra": block(1, 6, 50)},
            {"check_id": "cpp.dup", "extra": block(0, 0, 50)}]);
        value["extra"]["target_scopes"] = json!([
            {"target_index": 0, "task_id": "p"}, {"target_index": 1, "task_id": "p"},
            {"target_index": 2, "task_id": "c"}]);
        let settings = json!({});
        let context = Context {
            settings: &settings,
            profile: "",
            python_sources: 0,
            root: std::path::Path::new("."),
            cpp_sources: &[],
            cpp_scope: false,
        };
        merge_dup_checks(&mut value, &context);
        let paths: Vec<&str> = value["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| target["file_path"].as_str().unwrap())
            .collect();
        assert_eq!(paths, ["src/b.py", "src/a.cpp", "src/z.py"]);
        // The C++ check's target moved to position 1, the Python scan to 2.
        let scopes: Vec<(u64, &str)> = value["extra"]["target_scopes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|scope| {
                (
                    scope["target_index"].as_u64().unwrap(),
                    scope["task_id"].as_str().unwrap(),
                )
            })
            .collect();
        assert_eq!(scopes, [(0, "p"), (2, "p"), (1, "c")]);
        assert_eq!(value["score"], json!(6.0));
        assert_eq!(
            value["summary"],
            "Code Duplication Rate: 6.0% (1 distinct clone groups found, 0 from Python AST shapes)"
        );
        // An errored scan keeps its targets and has no score.
        let mut errored = value.clone();
        errored["status"] = json!("ERROR");
        errored["score"] = Value::Null;
        errored["extra"]
            .as_object_mut()
            .unwrap()
            .remove("duplication_pct");
        merge_dup_checks(&mut errored, &context);
        assert!(errored["score"].is_null());
    }

    #[test]
    fn measured_python_coverage_keeps_frozen_keys_and_the_empty_file_row() {
        let mut value = engine("test", &[], json!([]));
        value["extra"] = json!({
            "coverage_files": [
                {"file": "src/calc.py", "stmts": 6, "cover": 50.0},
                {"file": "src/__init__.py", "stmts": 0, "cover": null}],
            "coverage_policy": {"metrics": {}, "files": {}, "regression_tolerance": null,
                "raw_files": {}, "schema_id": "ici.coverage-policy"},
            "coverage_totals": {"stmts": 6, "miss": 3, "cover": 50.0, "branch_cover": 25.0,
                "nb": 4, "cb": 1, "covered": 3, "functions": null, "covered_functions": null},
            "coverage_provenance": {"format": "coverage.py-json", "included_sources": []},
            "test_quality": {"enabled": false, "evidence": "NOT_SELECTED",
                "repeat_observations": []}});
        frozen_python_coverage(&mut value);
        let extra = &value["extra"];
        // coverage.py reports 100.0 for a file without statements; (cover, file) order.
        assert_eq!(extra["coverage_files"][0]["file"], "src/calc.py");
        assert_eq!(extra["coverage_files"][1]["cover"], json!(100.0));
        assert_eq!(
            extra["coverage_policy"],
            json!({"metrics": {}, "files": {}, "regression_tolerance": null})
        );
        assert_eq!(
            extra["coverage_totals"],
            json!({"stmts": 6, "miss": 3, "cover": 50.0, "branch_cover": 25.0})
        );
        assert_eq!(extra["test_quality"], json!({"enabled": false}));
        assert_eq!(
            extra["coverage_provenance"]["python"]["expected_sources"],
            json!(2)
        );
        assert_eq!(
            extra["coverage_provenance"]["scope"]["included_sources"],
            json!(["src/__init__.py", "src/calc.py"])
        );
        // An unmeasured run (no totals) keeps its scope-only provenance.
        let mut unmeasured = engine("test", &[], json!([]));
        unmeasured["extra"] = json!({"coverage_totals": null,
            "coverage_provenance": {"scope": {"included_sources": []}}});
        let before = unmeasured.clone();
        frozen_python_coverage(&mut unmeasured);
        assert_eq!(unmeasured, before);
    }

    #[test]
    fn hybrid_dup_clone_groups_of_both_languages_follow_the_one_scan_order() {
        let group = |id: u64, detection: &str, lines: u64, path: &str, start: u64| {
            json!({"id": id, "detection": detection, "lines_count": lines,
                "occurrences_count": 2, "occurrences": [
                    {"file_path": path, "start_line": start, "end_line": start + lines},
                    {"file_path": path, "start_line": start + 20, "end_line": start + 20 + lines}]})
        };
        let checks = vec![
            json!({"check_id": "python.dup", "extra": {"clone_groups": [
                group(1, "type2-lexical-region", 7, "src/d.py", 2),
                group(2, "python-ast-semantic-shape", 8, "src/d.py", 1)]}}),
            json!({"check_id": "cpp.dup", "extra": {"clone_groups": [
                group(1, "type2-lexical-region", 11, "src/l.cpp", 45)]}}),
        ];
        let (merged, numbers) = frozen_clone_groups(&checks);
        let ids: Vec<(u64, &str)> = merged
            .iter()
            .map(|group| {
                (
                    group["id"].as_u64().unwrap(),
                    group["occurrences"][0]["file_path"].as_str().unwrap(),
                )
            })
            .collect();
        // The longer C++ lexical group first, the Python lexical, then the AST shape.
        assert_eq!(ids, [(1, "src/l.cpp"), (2, "src/d.py"), (3, "src/d.py")]);
        assert_eq!(
            numbers,
            [
                ("cpp.dup".to_owned(), 1, 1),
                ("python.dup".to_owned(), 1, 2),
                ("python.dup".to_owned(), 2, 3)
            ]
        );
    }

    #[test]
    fn hybrid_dup_verdict_is_the_merged_rate_against_the_thresholds() {
        let block = |lines: u64, total: u64| {
            json!({"clone_groups_count": u64::from(lines > 0), "duplicated_lines": lines,
                "total_code_lines": total, "clone_groups": []})
        };
        let hybrid = || {
            let mut value = engine(
                "dup",
                &[],
                json!([
                    {"file_path": "src/d.py", "start_line": 1, "target_name": "CloneGroup#1",
                     "status": "WARN", "message": "clone"},
                    {"file_path": "src/a.cpp", "start_line": 1, "target_name": "DuplicateScan",
                     "status": "PASS", "message": "scan"}]),
            );
            value["status"] = json!("WARN");
            value["findings"] = json!([
                {"primary_location": {"path": "src/d.py", "start_line": 1,
                    "label": "CloneGroup#1"}, "message": "clone", "severity": "medium"},
                {"primary_location": {"path": "src/a.cpp", "start_line": 1,
                    "label": "DuplicateScan"}, "message": "scan", "severity": "info"}]);
            // Python alone 8/16 = 50%, the one scan 8/100 = 8%.
            value["extra"]["native_checks"] = json!([
                {"check_id": "python.dup", "extra": block(8, 16)},
                {"check_id": "cpp.dup", "extra": block(0, 84)}]);
            value
        };
        let run = |settings: Value| {
            let mut value = hybrid();
            let context = Context {
                settings: &settings,
                profile: "",
                python_sources: 0,
                root: std::path::Path::new("."),
                cpp_sources: &[],
                cpp_scope: false,
            };
            let replaced = merge_dup_checks(&mut value, &context);
            (replaced, value)
        };
        // Under both thresholds: PASS, the clone target and its finding realigned.
        let (replaced, value) = run(json!({"warn_pct": 40.0, "fail_pct": 90.0}));
        assert!(replaced);
        assert_eq!(value["status"], "PASS");
        assert_eq!(value["targets"][0]["status"], "PASS");
        assert_eq!(value["findings"][0]["severity"], "info");
        // The merged rate crosses warn_pct: WARN stays, targets untouched.
        let (replaced, value) = run(json!({"warn_pct": 5.0, "fail_pct": 90.0}));
        assert!(!replaced);
        assert_eq!(value["status"], "WARN");
        assert_eq!(value["findings"][0]["severity"], "medium");
        // Over fail_pct under pass_warn_fail: FAIL, though no language check failed.
        let (replaced, value) = run(json!({"warn_pct": 2.0, "fail_pct": 6.0,
            "mode": "pass_warn_fail"}));
        assert!(replaced);
        assert_eq!(value["status"], "FAIL");
    }
}
