//! Frozen `TestEngine.run` over a project that measured both languages: one result, not two.
//!
//! Frozen pools the Python and C++ coverage rows (`build_coverage_summary`) and function rows
//! (`_measure_coverage`), then evaluates the coverage policy, TEM and threshold breaches once over
//! the pool, so a language under a minimum can pass and every language over a minimum can fail.
//! The native checks keep their per-language evaluation (`extra.native_checks`) and the next path
//! keeps its per-language contract; this rebuilds only the legacy reading, from what those checks
//! measured (`tests/rust-parity/reporting/hybrid-test-oracle.json`).
use crate::legacy_extra::{self, Context};
use ici_domain::python_round_one as round1;
use ici_domain::tem::{TemInputs, calculate};
use serde_json::{Map, Value, json};
use std::collections::BTreeSet;

/// The checks of a project that measured both languages.
const CHECKS: [&str; 4] = ["python.test", "python.coverage", "cpp.test", "cpp.coverage"];

/// The `extra` keys of frozen's quality pass, which only observes the Python half.
const QUALITY_KEYS: [&str; 16] = [
    "test_quality",
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
];

/// What part of the result a target is, in frozen's target order.
#[derive(Clone, Copy, PartialEq)]
enum Part {
    Case,
    Quality,
    Pooled,
    Coverage,
}

fn part(target: &Value) -> Part {
    let name = target["target_name"].as_str().unwrap_or_default();
    if name.starts_with("[test-quality]") {
        Part::Quality
    } else if matches!(name, "Coverage:Overall line" | "Coverage:Functions")
        || name.starts_with("Threshold: ")
    {
        Part::Pooled
    } else if name.starts_with("Coverage:") {
        Part::Coverage
    } else {
        Part::Case
    }
}

/// One language's legacy test view: its own checks and targets, read as a single-language engine.
fn half(engine: &Value, language: &str) -> Option<Value> {
    let task = format!(".{language}.");
    let check = format!("{language}.");
    let targets = legacy_extra::targets(engine);
    let mut own = vec![];
    for scope in engine["extra"]["target_scopes"].as_array()? {
        if scope["task_id"].as_str()?.contains(&task) {
            let index = usize::try_from(scope["target_index"].as_u64()?).ok()?;
            own.push(targets.get(index)?.clone());
        }
    }
    let mut alone = engine.clone();
    let extra = alone["extra"].as_object_mut()?;
    for key in [
        "line_coverage",
        "branch_coverage",
        "coverage_totals",
        "coverage_suites",
        "test_execution",
        "test_aggregation_limitation",
    ] {
        extra.remove(key);
    }
    let checks: Vec<Value> = extra
        .get("native_checks")?
        .as_array()?
        .iter()
        .filter(|item| {
            item["check_id"]
                .as_str()
                .is_some_and(|id| id.starts_with(&check))
        })
        .cloned()
        .collect();
    extra.insert("native_checks".into(), Value::Array(checks));
    alone["targets"] = Value::Array(own);
    alone["status"] = json!("FAIL");
    ici_reporting::legacy_test_view(&mut alone).ok()?;
    (alone["extra"].get("test_aggregation_limitation").is_none()
        && alone["extra"]["coverage_totals"].is_object())
    .then_some(alone)
}

fn percent(numerator: u64, denominator: u64) -> Option<f64> {
    // Frozen's order of operations: `covered / total * 100.0`, whose binary value decides the
    // rounding (23 of 80 is 28.749999999999996, so 28.7).
    (denominator > 0).then(|| numerator as f64 / denominator as f64 * 100.0)
}

fn sum(rows: &[Value], key: &str) -> u64 {
    rows.iter().filter_map(|row| row[key].as_u64()).sum()
}

fn minimum(context: &Context<'_>, key: &str, default: f64) -> f64 {
    context.settings[key].as_f64().unwrap_or(default)
}

/// Frozen `_threshold_target` at the project root.
fn gate(name: &str, label: &str, actual: f64, threshold: f64, metrics: Value) -> Value {
    let passed = actual >= threshold;
    let mut values = json!({"actual": round1(actual), "threshold": threshold, "gated": true});
    for (key, value) in metrics.as_object().into_iter().flatten() {
        values[key] = value.clone();
    }
    json!({"file_path": ".", "start_line": 1, "end_line": null, "start_column": null,
        "end_column": null, "target_name": name, "status": if passed { "PASS" } else { "WARN" },
        "message": format!(
            "{label} {actual:.1}% {} the configured minimum {threshold:.1}%",
            if passed { "meets" } else { "is below" }
        ),
        "snippet": "", "metrics": values})
}

/// Frozen `_threshold_breaches` for the TEM score of a run with no test measured: a target when
/// the score is below the minimum.
pub(crate) fn tem_breach(score: f64, threshold: f64) -> Option<Value> {
    (score < threshold).then(|| {
        breach(
            "TEM score",
            format!("TEM score {score:.2} is below the configured minimum {threshold:.2}"),
            score,
            threshold,
        )
    })
}

/// Frozen `_threshold_breaches`: a target only for a breach.
fn breach(name: &str, message: String, actual: f64, threshold: f64) -> Value {
    json!({"file_path": ".", "start_line": 1, "end_line": null, "start_column": null,
        "end_column": null, "target_name": format!("Threshold: {name}"), "status": "WARN",
        "message": message, "snippet": "",
        "metrics": {"actual": actual, "threshold": threshold}})
}

/// Rebuild the legacy view of a project whose two languages both measured coverage. Returns
/// whether it did; any other shape keeps the view it has.
pub(crate) fn complete(engine: &mut Value, context: &Context<'_>) -> bool {
    let checks = legacy_extra::checks(engine);
    if !legacy_extra::has_checks(engine, &CHECKS)
        || checks
            .iter()
            .any(|check| check["evidence_complete"] != true)
    {
        return false;
    }
    let all = legacy_extra::targets(engine);
    if all
        .iter()
        .any(|target| target["status"] == "ERROR" && !policy_error(target))
    {
        return false;
    }
    let (Some(mut python), Some(mut cpp)) = (half(engine, "python"), half(engine, "cpp")) else {
        return false;
    };
    legacy_extra::frozen_python_coverage(&mut python);
    crate::cpp::frozen_cpp_test_extra(&mut cpp, context);
    let (python, cpp) = (&python["extra"], &cpp["extra"]);
    let (Some(passed), Some(total)) = (
        engine["extra"]["passed_tests"].as_u64(),
        engine["extra"]["total_tests"].as_u64(),
    ) else {
        return false;
    };
    let mut files: Vec<Value> = [python, cpp]
        .iter()
        .flat_map(|half| {
            half["coverage_files"]
                .as_array()
                .cloned()
                .unwrap_or_default()
        })
        .collect();
    files.sort_by(|left, right| {
        left["cover"]
            .as_f64()
            .unwrap_or(f64::NEG_INFINITY)
            .total_cmp(&right["cover"].as_f64().unwrap_or(f64::NEG_INFINITY))
            .then_with(|| left["file"].as_str().cmp(&right["file"].as_str()))
    });
    let function_rows: Vec<Value> = [python, cpp]
        .iter()
        .flat_map(|half| {
            half["function_rows"]
                .as_array()
                .cloned()
                .unwrap_or_default()
        })
        .collect();
    let (statements, covered) = (sum(&files, "stmts"), sum(&files, "covered"));
    let (branches, covered_branches) = (sum(&files, "nb"), sum(&files, "cb"));
    let cover = percent(covered, statements).map(round1);
    let branch_cover = percent(covered_branches, branches).map(round1);
    let covered_functions = function_rows
        .iter()
        .filter(|row| row["covered"] == true)
        .count() as u64;
    let function = percent(covered_functions, function_rows.len() as u64).unwrap_or(100.0);
    let branch = branch_cover.unwrap_or(100.0);
    let overall = percent(covered, statements).unwrap_or(100.0);
    let tem = calculate(&TemInputs {
        passed,
        total,
        line_coverage: cover,
        branch_coverage: branch_cover,
        function_coverage: Some(function),
        estimated_coverage: (cover.is_none() && branch_cover.is_none()).then_some(branch),
    });
    let (Some(score), Some(shown)) = (tem.score, tem.coverage_shown) else {
        return false;
    };
    // Frozen `_result_status` over every collected case skipped is a verdict of its own, which no
    // capture of a project with both languages reaches.
    let has_failure = all
        .iter()
        .filter(|target| part(target) == Part::Case)
        .any(|target| target["status"] == "FAIL");
    if !has_failure && total > 0 && engine["extra"]["skipped_tests"].as_u64() >= Some(total) {
        return false;
    }

    let mode = context.settings["mode"].as_str().unwrap_or("pass_fail");
    let mut targets: Vec<Value> = vec![];
    let mut positions: Vec<Option<usize>> = vec![None; all.len()];
    let keep = |wanted: &dyn Fn(&Value) -> bool,
                targets: &mut Vec<Value>,
                positions: &mut Vec<Option<usize>>| {
        for (index, target) in all.iter().enumerate() {
            if wanted(target) {
                positions[index] = Some(targets.len());
                targets.push(target.clone());
            }
        }
    };
    let cpp_case = |target: &Value| {
        target["target_name"]
            .as_str()
            .is_some_and(|name| name.starts_with("[C++] "))
    };
    keep(
        &|target| part(target) == Part::Case && !cpp_case(target),
        &mut targets,
        &mut positions,
    );
    keep(
        &|target| part(target) == Part::Case && cpp_case(target),
        &mut targets,
        &mut positions,
    );
    keep(
        &|target| part(target) == Part::Quality,
        &mut targets,
        &mut positions,
    );
    let cases = targets.clone();
    let named = |target: &Value, name: &str| target["target_name"] == name;
    let coverage: Vec<(usize, &Value)> = all
        .iter()
        .enumerate()
        .filter(|(_, target)| part(target) == Part::Coverage)
        .collect();
    let mut place = |selected: Vec<(usize, &Value)>, targets: &mut Vec<Value>| {
        for (index, target) in selected {
            positions[index] = Some(targets.len());
            targets.push(target.clone());
        }
    };
    // Frozen `_measure_coverage`: a refused changed-line selection is the one policy target (each
    // half refused the same selection in the same words).
    let refusal: Vec<(usize, &Value)> = coverage
        .iter()
        .copied()
        .filter(|(_, target)| named(target, "Coverage:Policy configuration"))
        .take(1)
        .collect();
    let refused = !refusal.is_empty();
    place(refusal, &mut targets);
    if !refused && (statements > 0 || !files.is_empty()) {
        targets.push(gate(
            "Coverage:Overall line",
            "Overall line coverage",
            overall,
            minimum(context, "min_line_cov", 80.0),
            json!({"covered_lines": covered, "executable_lines": statements}),
        ));
    }
    let mut file_targets: Vec<(usize, &Value)> = coverage
        .iter()
        .copied()
        .filter(|(_, target)| !refused && named(target, "Coverage:File"))
        .collect();
    file_targets.sort_by(|left, right| {
        left.1["file_path"]
            .as_str()
            .cmp(&right.1["file_path"].as_str())
    });
    let mut policy_files = Map::new();
    for (_, target) in &file_targets {
        if let Some(path) = target["file_path"].as_str() {
            policy_files.insert(path.to_owned(), target["metrics"]["actual"].clone());
        }
    }
    place(file_targets, &mut targets);
    if !refused && (!files.is_empty() || !function_rows.is_empty()) {
        targets.push(gate(
            "Coverage:Functions",
            "Aggregate function coverage",
            function,
            minimum(context, "min_func_cov", 90.0),
            json!({"covered_functions": covered_functions, "functions": function_rows.len()}),
        ));
    }
    let mut function_targets: Vec<(usize, &Value)> = coverage
        .iter()
        .copied()
        .filter(|(_, target)| {
            !refused
                && target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("Coverage:Function:"))
        })
        .collect();
    function_targets.sort_by_key(|(_, target)| {
        (
            target["file_path"].as_str().unwrap_or_default().to_owned(),
            target["start_line"].as_u64().unwrap_or(1),
            target["start_column"].as_u64().unwrap_or(0),
            target["target_name"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
        )
    });
    place(function_targets, &mut targets);
    let mut changed: Vec<(usize, &Value)> = coverage
        .iter()
        .copied()
        .filter(|(_, target)| !refused && named(target, "Coverage:Changed lines"))
        .collect();
    changed.sort_by(|left, right| {
        left.1["file_path"]
            .as_str()
            .cmp(&right.1["file_path"].as_str())
    });
    // Frozen evaluates one merged line map: each half reports every selected path, so a path the
    // other half measured is not unmeasured, and one no half measured is one ERROR.
    let measured: BTreeSet<&str> = changed
        .iter()
        .filter(|(_, target)| !unmeasured_changed_lines(target))
        .filter_map(|(_, target)| target["file_path"].as_str())
        .collect();
    let mut reported = BTreeSet::new();
    changed.retain(|(_, target)| {
        let path = target["file_path"].as_str().unwrap_or_default();
        !unmeasured_changed_lines(target) || (!measured.contains(path) && reported.insert(path))
    });
    let changed_counts = changed
        .iter()
        .fold((0, 0), |(covered, executable), (_, target)| {
            (
                covered + target["metrics"]["covered_lines"].as_u64().unwrap_or(0),
                executable + target["metrics"]["executable_lines"].as_u64().unwrap_or(0),
            )
        });
    place(changed, &mut targets);
    let policy_warn = targets.iter().any(|target| {
        target["status"] == "WARN"
            && target["metrics"]["gated"] == true
            && part(target) != Part::Case
            && part(target) != Part::Quality
    });
    let mut breaches = vec![];
    if !has_failure {
        let threshold = minimum(context, "min_tem_score", 4.0);
        if score < threshold {
            breaches.push(breach(
                "TEM score",
                format!("TEM score {score:.2} is below the configured minimum {threshold:.2}"),
                score,
                threshold,
            ));
        }
        let threshold = minimum(context, "min_branch_cov", 80.0);
        if branch < threshold {
            breaches.push(breach(
                "Branch coverage",
                format!(
                    "Branch coverage {branch:.1}% is below the configured minimum {threshold:.1}%"
                ),
                branch,
                threshold,
            ));
        }
    }
    let quality_warn = python["test_quality"]["mode"] == "warn"
        && cases
            .iter()
            .any(|target| part(target) == Part::Quality && target["status"] == "WARN");
    let has_warn = !breaches.is_empty() || policy_warn || quality_warn;
    targets.extend(breaches);
    let status = legacy_extra::evaluate_status(has_failure, has_warn, mode);

    let label = tem.coverage_label.as_str();
    let suffix = if tem.estimated { " (est)" } else { "" };
    let mut summary = format!(
        "{passed}/{total} Tests Passed | {label}: {shown:.1}%{suffix}, Func: {function:.1}% -> TEM: {score:.2} / 5.0"
    );
    if label != "Branch" {
        summary = summary.replacen(" -> TEM:", &format!(", Branch: {branch:.1}% -> TEM:"), 1);
    }
    // Frozen's snapshot reads line and function from their policy targets.
    let mut metrics = if refused {
        json!({"branch": round1(branch)})
    } else {
        json!({
            "branch": round1(branch),
            "function": round1(function),
            "line": round1(overall),
        })
    };
    if changed_counts.1 > 0 {
        metrics["changed_line"] = json!(round1(
            percent(changed_counts.0, changed_counts.1).unwrap_or(0.0)
        ));
    }
    let tolerance = context.settings["max_coverage_regression"].as_f64();
    let mut included: Vec<&Value> = files.iter().map(|row| &row["file"]).collect();
    included.sort_by(|left, right| left.as_str().cmp(&right.as_str()));
    let mut scope = cpp["coverage_provenance"]["scope"].clone();
    scope["included_sources"] = json!(included);
    let test_suites = legacy_extra::frozen_test_suites(&cases);
    let quality = python.clone();

    let extra = &mut engine["extra"];
    extra["coverage_files"] = json!(files);
    extra["coverage_totals"] = json!({"stmts": statements, "miss": sum(&files, "miss"),
        "cover": cover, "branch_cover": branch_cover});
    extra["coverage_source"] = json!("coverage.py/gcov");
    extra["coverage_provenance"] = json!({"cpp": cpp["coverage_provenance"]["cpp"],
        "python": python["coverage_provenance"]["python"], "scope": scope});
    extra["coverage_policy"] = json!({"metrics": metrics, "files": policy_files,
        "regression_tolerance": tolerance});
    extra["function_rows"] = json!(function_rows);
    extra["function_coverage"] = json!(function);
    extra["branch_coverage"] = json!(branch);
    extra["line_coverage"] = json!(cover);
    extra["pass_rate"] = json!(tem.pass_rate);
    extra["tem_score"] = json!(score);
    extra["metrics_summary"] = json!(format!(
        "TEM: {score:.2}/5.0 ({label}: {shown:.0}%, Func: {function:.0}%, PassRate: {:.0}%)",
        tem.pass_rate * 100.0
    ));
    extra["test_suites"] = json!(test_suites);
    for key in QUALITY_KEYS {
        if let Some(value) = quality.get(key) {
            extra[key] = value.clone();
        }
    }
    if extra["test_quality"].is_object() {
        extra["test_quality"]["profile"] = json!(context.profile);
    }
    engine["score"] = json!(score);
    engine["max_score"] = json!(5.0);
    match coverage_policy_errors(&targets) {
        Some(errors) => finish(engine, "ERROR", "NOT_RUN", &errors, targets, &positions),
        None => finish(engine, status, "MEASURED", &summary, targets, &positions),
    }
}

/// Frozen `TestEngine.run` records every ERROR coverage-policy target (`Coverage:Policy
/// configuration`, an unmeasured `Coverage:Changed lines`) as a tool error: the engine is ERROR,
/// NOT_RUN, and `_result_summary` joins the first three messages.
pub(crate) fn coverage_policy_errors(targets: &[Value]) -> Option<String> {
    let messages: Vec<&str> = targets
        .iter()
        .filter(|target| {
            target["status"] == "ERROR"
                && target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("Coverage:"))
        })
        .filter_map(|target| target["message"].as_str())
        .take(3)
        .collect();
    (!messages.is_empty()).then(|| messages.join("; "))
}

/// A single-language legacy test result with an ERROR coverage-policy target: frozen's tool-error
/// verdict, its findings adapted again under that evidence. Returns whether it changed.
pub(crate) fn single_language_policy_errors(engine: &mut Value) -> bool {
    if engine["status"] == "ERROR" {
        return false;
    }
    let targets = legacy_extra::targets(engine);
    let Some(errors) = coverage_policy_errors(&targets) else {
        return false;
    };
    // Frozen's snapshot reads line, function, file and changed-line values from the policy targets
    // a refused selection replaced.
    if targets
        .iter()
        .any(|target| target["target_name"] == "Coverage:Policy configuration")
        && let Some(policy) = engine["extra"]["coverage_policy"].as_object_mut()
    {
        if let Some(metrics) = policy.get_mut("metrics").and_then(Value::as_object_mut) {
            metrics.retain(|key, _| key == "branch");
        }
        if policy.contains_key("files") {
            policy.insert("files".into(), json!({}));
        }
    }
    let positions: Vec<Option<usize>> = (0..targets.len()).map(Some).collect();
    finish(engine, "ERROR", "NOT_RUN", &errors, targets, &positions)
}

/// Frozen's verdict over the rebuilt targets: the explicit findings stay, every other finding is
/// adapted again, and the native maps keep pointing at what is left.
fn finish(
    engine: &mut Value,
    status: &str,
    evidence: &str,
    summary: &str,
    targets: Vec<Value>,
    positions: &[Option<usize>],
) -> bool {
    remap_scopes(engine, positions);
    let explicit: Vec<Value> = engine["findings"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|finding| finding["rule_id"] != "ici.legacy.test.target")
        .cloned()
        .collect();
    if ici_reporting::legacy_verdict(engine, status, evidence, summary, targets).is_err() {
        return false;
    }
    merge_explicit(engine, explicit);
    let fingerprints: Vec<Value> = engine["findings"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|finding| finding["fingerprint"].clone())
        .collect();
    if let Some(mappings) = engine["extra"]["finding_identity_map"].as_array_mut() {
        mappings.retain(|mapping| fingerprints.contains(&mapping["legacy_fingerprint"]));
    }
    true
}

/// Frozen `TestEngine.run` when `_run_project_tests` found C++ sources and no `tests/**/*.cpp`:
/// `_mark_zero_tests("C++")` beside the measured Python half. Python's coverage, TEM and score
/// stand, the missing gcov evidence is the optional warning, so no coverage policy or threshold is
/// evaluated, and the C++ half failing decides the verdict. Under `coverage_required` that warning
/// is a tool error instead. Only the exact state a project without a C++ test leaves qualifies:
/// a C++ runtime check blocked for any other reason keeps its incomplete result.
/// How the Python coverage half of a zero-C++-test project ended (`zero_cpp_tests`).
#[derive(Clone, Copy, PartialEq)]
enum ZeroCppCoverage {
    Measured,
    JsonFailed,
    ModuleMissing,
}

/// The exact state a project with C/C++ sources and no `tests/**/*.cpp` leaves beside a complete
/// Python suite: both C++ runtime checks blocked for no runnable suite, and how the Python
/// coverage half ended. `None` for any other state.
fn zero_cpp_state(engine: &Value, context: &Context<'_>) -> Option<ZeroCppCoverage> {
    let checks = legacy_extra::checks(engine);
    let state = |id: &str, wanted: &dyn Fn(&Value) -> bool| {
        checks
            .iter()
            .any(|check| check["check_id"] == id && wanted(check))
    };
    let complete =
        |check: &Value| check["state"] == "SUCCEEDED" && check["evidence_complete"] == true;
    let unbuilt = |check: &Value| {
        check["state"] == "BLOCKED"
            && check["summary"]
                .as_str()
                .is_some_and(crate::cpp_validation::no_runnable_cpp_suite)
    };
    let json_failure = |check: &Value| {
        check["state"] == "FAILED"
            && check["summary"]
                .as_str()
                .is_some_and(ici_validation::python_coverage::legacy_coverage_json_failure)
    };
    let module_missing = |check: &Value| {
        check["state"] == "FAILED"
            && check["summary"]
                .as_str()
                .is_some_and(|summary| summary.starts_with(COVERAGE_MODULE_MISSING))
    };
    let coverage = if state("python.coverage", &complete) {
        ZeroCppCoverage::Measured
    } else if state("python.coverage", &json_failure) {
        ZeroCppCoverage::JsonFailed
    } else if state("python.coverage", &module_missing) {
        ZeroCppCoverage::ModuleMissing
    } else {
        return None;
    };
    (context.cpp_scope
        && state("python.test", &complete)
        && state("cpp.test", &unbuilt)
        && state("cpp.coverage", &unbuilt)
        && crate::legacy_execute::tests_have_cpp_sources(context.root) == Some(false))
    .then_some(coverage)
}

pub(crate) fn zero_cpp_tests(engine: &mut Value, context: &Context<'_>) -> bool {
    let Some(coverage) = zero_cpp_state(engine, context) else {
        return false;
    };
    let coverage_json_failed = coverage == ZeroCppCoverage::JsonFailed;
    let coverage_missing = coverage == ZeroCppCoverage::ModuleMissing;
    if coverage_json_failed {
        return zero_cpp_tests_beside_coverage_json_error(engine, context);
    }
    if coverage_missing {
        return zero_cpp_tests_beside_missing_coverage(engine, context);
    }
    let Some(mut python) = half(engine, "python") else {
        return false;
    };
    legacy_extra::frozen_python_coverage(&mut python);
    let measured = python["summary"].as_str().map(str::to_owned);
    let python = &python["extra"];
    let Some(measured) = measured.filter(|_| python["tem_score"].is_number()) else {
        return false;
    };
    let all = legacy_extra::targets(engine);
    let mut targets: Vec<Value> = vec![];
    let mut positions: Vec<Option<usize>> = vec![None; all.len()];
    let mut keep = |wanted: Part, targets: &mut Vec<Value>| {
        for (index, target) in all.iter().enumerate() {
            if part(target) == wanted {
                positions[index] = Some(targets.len());
                targets.push(target.clone());
            }
        }
    };
    keep(Part::Case, &mut targets);
    targets.push(
        json!({"file_path": "tests", "start_line": 1, "end_line": null,
        "start_column": null, "end_column": null, "target_name": "[C++] Tests", "status": "FAIL",
        "message": "No tests collected", "snippet": "", "metrics": {}}),
    );
    let cases = targets.clone();
    keep(Part::Quality, &mut targets);
    let mut included: Vec<Value> = python["coverage_files"]
        .as_array()
        .map(|rows| rows.iter().map(|row| row["file"].clone()).collect())
        .unwrap_or_default();
    included.sort_by(|left, right| left.as_str().cmp(&right.as_str()));
    let provenance = json!({"python": python["coverage_provenance"]["python"],
        "scope": crate::cpp::frozen_project_scope(included, context)});
    let score = python["tem_score"].clone();
    let extra = &mut engine["extra"];
    for key in QUALITY_KEYS.iter().copied().chain([
        "coverage_files",
        "coverage_totals",
        "function_rows",
        "function_coverage",
        "branch_coverage",
        "line_coverage",
        "pass_rate",
        "passed_tests",
        "total_tests",
        "skipped_tests",
        "tem_score",
        "metrics_summary",
    ]) {
        if let Some(value) = python.get(key) {
            extra[key] = value.clone();
        }
    }
    if extra["test_quality"].is_object() {
        extra["test_quality"]["profile"] = json!(context.profile);
    }
    extra["coverage_source"] = json!("coverage.py (partial)");
    extra["coverage_provenance"] = provenance;
    extra["coverage_policy"] = Value::Null;
    extra["test_suites"] = json!(legacy_extra::frozen_test_suites(&cases));
    if let Some(fields) = extra.as_object_mut() {
        fields.remove("test_aggregation_limitation");
    }
    engine["score"] = score;
    engine["max_score"] = json!(5.0);
    if context.settings["coverage_required"] == true {
        return finish(
            engine,
            "ERROR",
            "NOT_RUN",
            "C++ gcov coverage evidence was unavailable or malformed",
            targets,
            &positions,
        );
    }
    let mode = context.settings["mode"].as_str().unwrap_or("pass_fail");
    let status = legacy_extra::evaluate_status(true, true, mode);
    let summary =
        format!("{measured}; Coverage evidence ESTIMATED (optional; not threshold evidence)");
    finish(engine, status, "ESTIMATED", &summary, targets, &positions)
}

/// Frozen `TestEngine.run` when `_run_project_tests` found C++ sources and no `tests/**/*.cpp`
/// beside a Python suite whose `coverage json` failed (`_generate_coverage_json`'s tool error, a
/// `--source` directory holding no Python, for instance): the tool error is the ERROR verdict and
/// its summary, and `_mark_zero_tests("C++")`'s FAIL target follows the Python cases. The Python
/// half keeps native's single-language view of that error (its counts, no invented coverage or
/// TEM: decisions.md "legacy coverage.py의 --source"). `coverage_required` adds frozen's missing
/// evidence messages to the tool errors; that reading is not captured, so it keeps the native view.
fn zero_cpp_tests_beside_coverage_json_error(engine: &mut Value, context: &Context<'_>) -> bool {
    if context.settings["coverage_required"] == true {
        return false;
    }
    shape_zero_cpp_json_error(engine, context, false)
}

/// The same frozen reading under `coverage_required` (`_apply_coverage_policy`): the `coverage
/// json` tool error, then `_coverage_failure_messages`' missing Python and C++ evidence, ERROR /
/// NOT_RUN with the Python cases and `[C++] Tests` FAIL (frozen `d-*-required` capture). Only the
/// verdict's shape is frozen's: the result stays an unreplaced view, so the exit is the native
/// gate's incomplete one for a required coverage tool error (decisions.md "legacy 종료 코드 3의
/// 범위", `covjson-exit7-required`; frozen 1, recorded as a difference under review).
pub(crate) fn zero_cpp_tests_required_coverage_json_error(
    engine: &mut Value,
    context: &Context<'_>,
) -> bool {
    context.settings["coverage_required"] == true
        && zero_cpp_state(engine, context) == Some(ZeroCppCoverage::JsonFailed)
        && shape_zero_cpp_json_error(engine, context, true)
}

fn shape_zero_cpp_json_error(engine: &mut Value, context: &Context<'_>, required: bool) -> bool {
    let Some(summary) = legacy_extra::checks(engine)
        .iter()
        .find(|check| check["check_id"] == "python.coverage")
        .and_then(|check| check["summary"].as_str().map(str::to_owned))
    else {
        return false;
    };
    legacy_extra::complete_test(engine, context);
    let all = legacy_extra::targets(engine);
    let mut targets: Vec<Value> = vec![];
    let mut positions: Vec<Option<usize>> = vec![None; all.len()];
    let mut keep = |wanted: Part, targets: &mut Vec<Value>| {
        for (index, target) in all.iter().enumerate() {
            if part(target) == wanted {
                positions[index] = Some(targets.len());
                targets.push(target.clone());
            }
        }
    };
    keep(Part::Case, &mut targets);
    targets.push(
        json!({"file_path": "tests", "start_line": 1, "end_line": null,
        "start_column": null, "end_column": null, "target_name": "[C++] Tests", "status": "FAIL",
        "message": "No tests collected", "snippet": "", "metrics": {}}),
    );
    let cases = targets.clone();
    keep(Part::Quality, &mut targets);
    engine["extra"]["test_suites"] = json!(legacy_extra::frozen_test_suites(&cases));
    let summary = if required {
        format!(
            "{summary}; Python coverage evidence was unavailable or malformed; \
             C++ gcov coverage evidence was unavailable or malformed"
        )
    } else {
        summary
    };
    finish(engine, "ERROR", "NOT_RUN", &summary, targets, &positions)
}

/// The native python.coverage reason when the project interpreter has no coverage module.
const COVERAGE_MODULE_MISSING: &str = "coverage module is unavailable";

/// Native's `Coverage capability` SKIP target for a missing coverage module.
fn coverage_capability(target: &Value) -> bool {
    target["target_name"] == "Coverage capability" && target["status"] == "SKIP"
}

/// Frozen `TestEngine.run` when `_find_coverage_cmd`'s `coverage --version` probe fails (a project
/// interpreter with pytest and without coverage) beside C/C++ sources and no `tests/**/*.cpp`:
/// the plain pytest run's cases, then `_mark_zero_tests("C++")`'s FAIL. Frozen adds no target for
/// the missing module (decisions.md "pytest는 있으나 coverage가 없는 legacy test"), so native's
/// `Coverage capability` SKIP is dropped. The missing coverage is the optional ESTIMATED warning
/// and the C++ half failing decides the (mode-aware) verdict, exit 1. Frozen's summary carries
/// its invented estimate (45%/50%, TEM 1.41); native never invents it (decisions.md "legacy
/// coverage.py의 --source", R9) and names the cases it ran and what it did not measure.
/// `coverage_required` is not captured for this state and keeps the native view.
fn zero_cpp_tests_beside_missing_coverage(engine: &mut Value, context: &Context<'_>) -> bool {
    if context.settings["coverage_required"] == true {
        return false;
    }
    let checks = legacy_extra::checks(engine);
    let Some(reason) = checks
        .iter()
        .find(|check| check["check_id"] == "python.coverage")
        .and_then(|check| check["summary"].as_str().map(str::to_owned))
    else {
        return false;
    };
    let Some(block) = python_test_block(&checks) else {
        return false;
    };
    let counts = (
        block["legacy_case_counts"]["passed"]
            .as_u64()
            .or_else(|| block["passed_tests"].as_u64()),
        block["legacy_case_counts"]["total"]
            .as_u64()
            .or_else(|| block["total_tests"].as_u64()),
    );
    let (Some(passed), Some(total)) = counts else {
        return false;
    };
    legacy_extra::drop_targets(engine, coverage_capability);
    legacy_extra::complete_test(engine, context);
    let all = legacy_extra::targets(engine);
    let mut targets: Vec<Value> = vec![];
    let mut positions: Vec<Option<usize>> = vec![None; all.len()];
    for (index, target) in all.iter().enumerate() {
        if part(target) == Part::Case {
            positions[index] = Some(targets.len());
            targets.push(target.clone());
        }
    }
    targets.push(
        json!({"file_path": "tests", "start_line": 1, "end_line": null,
        "start_column": null, "end_column": null, "target_name": "[C++] Tests", "status": "FAIL",
        "message": "No tests collected", "snippet": "", "metrics": {}}),
    );
    let cases = targets.clone();
    for (index, target) in all.iter().enumerate() {
        if part(target) == Part::Quality {
            positions[index] = Some(targets.len());
            targets.push(target.clone());
        }
    }
    let extra = &mut engine["extra"];
    extra["test_suites"] = json!(legacy_extra::frozen_test_suites(&cases));
    extra["passed_tests"] = json!(passed);
    extra["total_tests"] = json!(total);
    extra["coverage_policy"] = Value::Null;
    if let Some(fields) = extra.as_object_mut() {
        for key in [
            "branch_coverage",
            "function_coverage",
            "line_coverage",
            "tem_score",
            "coverage_source",
            "metrics_summary",
        ] {
            fields.remove(key);
        }
    }
    engine["score"] = Value::Null;
    let mode = context.settings["mode"].as_str().unwrap_or("pass_fail");
    let status = legacy_extra::evaluate_status(true, true, mode);
    let summary =
        format!("{passed}/{total} Tests Passed; coverage not measured (optional): {reason}");
    finish(engine, status, "ESTIMATED", &summary, targets, &positions)
}

/// The Python test check's own block (its counts, legacy tool error and quality keys).
fn python_test_block(checks: &[Value]) -> Option<Value> {
    let check = checks
        .iter()
        .find(|check| check["check_id"] == "python.test")?;
    legacy_extra::check_blocks(check)
        .into_iter()
        .find(|block| block.get("test_execution").is_some())
        .cloned()
}

/// Frozen `TestEngine.run` over a project whose C++ half measured its tests and gcov coverage
/// while the Python half produced no coverage data: frozen `_generate_coverage_json` refused the
/// report ("Python coverage JSON was missing or malformed", a coverage error) or
/// `_parse_pytest_result` recorded a tool error ("Pytest failed with exit code N", which skips the
/// coverage report). Frozen still pools what it measured: the Python cases (`legacy_case_counts`)
/// beside the C++ ones, coverage, TEM and score from the gcov rows alone, `coverage_source`
/// "gcov (partial)" (`_build_coverage_summary`). The missing Python evidence is an optional
/// coverage warning, so no coverage policy (`coverage_complete` is false) or threshold breach
/// (`_threshold_breaches` with `optional`) is evaluated; `_result_status` makes a run without a
/// failure WARN in every mode, and a tool error is ERROR with its own text as the summary.
/// `coverage_required` turns the warning into tool errors (`_apply_coverage_policy`: the coverage
/// error, then `_coverage_failure_messages`' missing evidence), so the run is ERROR, and the
/// warning no longer suppresses the threshold breaches of a run without a failure.
pub(crate) fn python_coverage_unmeasured(engine: &mut Value, context: &Context<'_>) -> bool {
    let checks = legacy_extra::checks(engine);
    let check = |id: &str| checks.iter().find(|check| check["check_id"] == id);
    let complete = |id: &str| {
        check(id).is_some_and(|check| {
            check["state"] == "SUCCEEDED" && check["evidence_complete"] == true
        })
    };
    if checks.len() != CHECKS.len()
        || !legacy_extra::has_checks(engine, &CHECKS)
        || !complete("cpp.test")
        || !complete("cpp.coverage")
    {
        return false;
    }
    let (Some(test), Some(coverage)) = (check("python.test"), check("python.coverage")) else {
        return false;
    };
    let Some(block) = python_test_block(&checks) else {
        return false;
    };
    // The coverage report refused after a complete suite (a coverage error), or a pytest run frozen
    // reads as a tool error, whose coverage check never ran.
    let refused = complete("python.test")
        && coverage["state"] == "FAILED"
        && coverage["summary"] == ici_validation::python_coverage::LEGACY_COVERAGE_MALFORMED;
    // `_generate_coverage_json`'s own tool error after a complete suite (no coverage read).
    let json_failed = complete("python.test")
        && coverage["state"] == "FAILED"
        && coverage["summary"]
            .as_str()
            .is_some_and(ici_validation::python_coverage::legacy_coverage_json_failure);
    let tool_error = if json_failed {
        coverage["summary"].as_str().map(str::to_owned)
    } else {
        block["legacy_tool_error"].as_str().map(str::to_owned)
    };
    let errored =
        test["state"] == "FAILED" && tool_error.is_some() && coverage["state"] == "BLOCKED";
    // `_find_coverage_cmd`'s failed `coverage --version` probe (pytest without coverage): the
    // plain suite ran and no coverage was read, no coverage error. `coverage_required` is not
    // captured for this state and keeps the native view.
    let module_missing = complete("python.test")
        && coverage["state"] == "FAILED"
        && coverage["summary"]
            .as_str()
            .is_some_and(|summary| summary.starts_with(COVERAGE_MODULE_MISSING))
        && context.settings["coverage_required"] != true;
    if !refused && !errored && !json_failed && !module_missing {
        return false;
    }
    let tool_error = tool_error.filter(|_| !module_missing);
    if module_missing {
        // Frozen adds no target for the missing module.
        legacy_extra::drop_targets(engine, coverage_capability);
    }
    let (Some(passed), Some(total)) = (
        block["legacy_case_counts"]["passed"].as_u64(),
        block["legacy_case_counts"]["total"].as_u64(),
    ) else {
        return false;
    };
    let Some(mut cpp) = half(engine, "cpp") else {
        return false;
    };
    crate::cpp::frozen_cpp_test_extra(&mut cpp, context);
    let pool = Pool {
        python: None,
        cpp: &cpp["extra"],
        quality: &block,
        python_cases: (passed, total),
        tool_error,
        coverage_errors: refused
            .then(|| ici_validation::python_coverage::LEGACY_COVERAGE_MALFORMED.to_owned())
            .into_iter()
            .collect(),
        missing: Some("Python coverage evidence was unavailable or malformed"),
        source: "gcov (partial)",
    };
    pooled_verdict(engine, context, &pool)
}

/// Frozen `TestEngine.run` over a measured Python half beside a C++ suite that ran with no
/// production C/C++ source to measure (a `type = "python"` project's `tests/**/*.cpp`, test.py:268,
/// beside Python sources only): the C++ cases pass or fail, gcov reads no row, and frozen pools
/// what it measured: the Python cases beside the C++ ones, coverage, TEM and score from the
/// coverage.py rows alone, `coverage_source` "coverage.py (partial)" (`_build_coverage_summary`).
/// The missing C++ evidence is the optional coverage warning, so no coverage policy or threshold
/// is evaluated and the run is WARN (legacy-test-scope-oracle.json `open`
/// `python-src-python-tests-cpp`, `ici test` and `verify`). `coverage_required` is not captured
/// for this state and keeps the native view.
pub(crate) fn cpp_coverage_without_production_sources(
    engine: &mut Value,
    context: &Context<'_>,
) -> bool {
    let checks = legacy_extra::checks(engine);
    let check = |id: &str| checks.iter().find(|check| check["check_id"] == id);
    let complete = |id: &str| {
        check(id).is_some_and(|check| {
            check["state"] == "SUCCEEDED" && check["evidence_complete"] == true
        })
    };
    // The adapter's reason, or the direct g++ path's (a tests/*.cpp suite without a build
    // system, frozen's `g++ test compile` + `C++ test`).
    let unmeasured = check("cpp.coverage").is_some_and(|check| {
        check["state"] == "BLOCKED"
            && check["summary"].as_str().is_some_and(|summary| {
                summary == crate::cpp_validation::NO_PRODUCTION_COVERAGE
                    || summary == crate::cpp_validation::DIRECT_NO_PRODUCTION_COVERAGE
            })
    });
    if checks.len() != CHECKS.len()
        || !legacy_extra::has_checks(engine, &CHECKS)
        || !complete("python.test")
        || !complete("python.coverage")
        || !complete("cpp.test")
        || !unmeasured
        || context.settings["coverage_required"] == true
    {
        return false;
    }
    let Some(mut python) = half(engine, "python") else {
        return false;
    };
    legacy_extra::frozen_python_coverage(&mut python);
    let python = python["extra"].take();
    let (Some(python_passed), Some(python_total)) = (
        python["passed_tests"].as_u64(),
        python["total_tests"].as_u64(),
    ) else {
        return false;
    };
    // The C++ half measured cases only: one target per case, no gcov row.
    let cpp_cases: Vec<Value> = legacy_extra::targets(engine)
        .into_iter()
        .filter(|target| {
            part(target) == Part::Case
                && target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("[C++] "))
        })
        .collect();
    if cpp_cases.is_empty() {
        return false;
    }
    let mut included: Vec<Value> = python["coverage_files"]
        .as_array()
        .map(|rows| rows.iter().map(|row| row["file"].clone()).collect())
        .unwrap_or_default();
    included.sort_by(|left, right| left.as_str().cmp(&right.as_str()));
    let cpp = json!({
        "passed_tests": cpp_cases.iter().filter(|target| target["status"] == "PASS").count(),
        "total_tests": cpp_cases.len(),
        "coverage_files": [],
        "function_rows": [],
        "coverage_provenance": {"cpp": Value::Null,
            "scope": crate::cpp::frozen_project_scope(included, context)},
    });
    let pool = Pool {
        python: Some(&python),
        cpp: &cpp,
        quality: &python,
        python_cases: (python_passed, python_total),
        tool_error: None,
        coverage_errors: vec![],
        missing: Some("C++ gcov coverage evidence was unavailable or malformed"),
        source: "coverage.py (partial)",
    };
    pooled_verdict(engine, context, &pool)
}

/// Frozen `TestEngine.run` when every C++ test failed to compile or link (`_run_cpp_test_case`'s
/// "C++ gcov coverage compilation failed", a coverage error): the production objects were
/// compiled, so gcov reads their notes as executed-zero rows beside whatever Python measured, and
/// the coverage error makes the run an optional warning (a tool error under `coverage_required`).
pub(crate) fn cpp_tests_not_built(engine: &mut Value, context: &Context<'_>) -> bool {
    let checks = legacy_extra::checks(engine);
    let check = |id: &str| checks.iter().find(|check| check["check_id"] == id);
    let complete = |id: &str| {
        check(id).is_some_and(|check| {
            check["state"] == "SUCCEEDED" && check["evidence_complete"] == true
        })
    };
    let compile_block = check("cpp.test").is_some_and(|check| {
        check["state"] == "SUCCEEDED"
            && check["extra"].get("project.cpp.test.compile").is_some()
            && legacy_extra::check_blocks(check).len() == 1
    });
    let hybrid = checks.len() == CHECKS.len()
        && legacy_extra::has_checks(engine, &CHECKS)
        && complete("python.test")
        && complete("python.coverage");
    if !compile_block
        || !complete("cpp.coverage")
        || !(hybrid || (checks.len() == 2 && legacy_extra::has_checks(engine, &CHECKS[2..])))
    {
        return false;
    }
    // The compile failures are the C++ suite's complete observation here: every test ran no code.
    let mut measured = engine.clone();
    if let Some(items) = measured["extra"]["native_checks"].as_array_mut() {
        for item in items
            .iter_mut()
            .filter(|item| item["check_id"] == "cpp.test")
        {
            item["evidence_complete"] = json!(true);
        }
    }
    let Some(mut cpp) = half(&measured, "cpp") else {
        return false;
    };
    crate::cpp::frozen_cpp_test_extra(&mut cpp, context);
    let mut python = if hybrid {
        let Some(mut python) = half(engine, "python") else {
            return false;
        };
        legacy_extra::frozen_python_coverage(&mut python);
        Some(python)
    } else {
        None
    };
    let python_extra = python.as_mut().map(|python| python["extra"].take());
    let python_cases = python_extra.as_ref().map_or((0, 0), |python| {
        (
            python["passed_tests"].as_u64().unwrap_or(0),
            python["total_tests"].as_u64().unwrap_or(0),
        )
    });
    // Frozen's order: the failed compile, then what the gcov text reading refused.
    let coverage_errors = std::iter::once("C++ gcov coverage compilation failed".to_owned())
        .chain(check("cpp.coverage").into_iter().flat_map(|coverage| {
            std::iter::once(&coverage["extra"])
                .chain(legacy_extra::check_blocks(coverage))
                .find_map(|block| block.get("legacy_coverage_error")?.as_str())
                .map(str::to_owned)
        }))
        .collect();
    let pool = Pool {
        python: python_extra.as_ref(),
        cpp: &cpp["extra"],
        quality: python_extra.as_ref().unwrap_or(&cpp["extra"]),
        python_cases,
        tool_error: None,
        coverage_errors,
        missing: None,
        source: if hybrid { "coverage.py/gcov" } else { "gcov" },
    };
    pooled_verdict(engine, context, &pool)
}

/// Frozen `TestEngine.run` over a C++ test whose run or compile did not finish, when gcov still
/// read rows: the tool error stays the verdict and the rows are real, so the TEM of those rows
/// and the executed counts are reported, with `_threshold_breaches` unless a case failed.
pub(crate) fn cpp_incomplete_test_measured(engine: &mut Value, context: &Context<'_>) {
    let checks = legacy_extra::checks(engine);
    let state = |id: &str| {
        checks
            .iter()
            .find(|check| check["check_id"] == id)
            .map(|check| check["state"].clone())
    };
    let extra = &engine["extra"];
    if engine["status"] != "ERROR"
        || checks.len() != 2
        || state("cpp.test") != Some(json!("FAILED"))
        || state("cpp.coverage") != Some(json!("SUCCEEDED"))
        || !extra["coverage_totals"].is_object()
        || extra.get("tem_score").is_some()
    {
        return;
    }
    let (Some(passed), Some(total), Some(files)) = (
        extra["passed_tests"].as_u64(),
        extra["total_tests"].as_u64(),
        extra["coverage_files"].as_array(),
    ) else {
        return;
    };
    let (statements, covered) = (sum(files, "stmts"), sum(files, "covered"));
    let (branches, covered_branches) = (sum(files, "nb"), sum(files, "cb"));
    let cover = percent(covered, statements).map(round1);
    let branch_cover = percent(covered_branches, branches).map(round1);
    let functions = extra["function_rows"]
        .as_array()
        .map_or(&[][..], Vec::as_slice);
    let covered_functions = functions
        .iter()
        .filter(|row| row["covered"] == true)
        .count() as u64;
    // Frozen `_measure_coverage` over a measured run: no row is a vacuous 100.0.
    let function = percent(covered_functions, functions.len() as u64).unwrap_or(100.0);
    let branch = branch_cover.unwrap_or(100.0);
    let tem = calculate(&TemInputs {
        passed,
        total,
        line_coverage: cover,
        branch_coverage: branch_cover,
        function_coverage: Some(function),
        estimated_coverage: None,
    });
    let (Some(score), Some(shown)) = (tem.score, tem.coverage_shown) else {
        return;
    };
    let label = tem.coverage_label.as_str();
    let summary = format!(
        "TEM: {score:.2}/5.0 ({label}: {shown:.0}%, Func: {function:.0}%, PassRate: {:.0}%)",
        tem.pass_rate * 100.0
    );
    let extra = &mut engine["extra"];
    extra["branch_coverage"] = json!(branch);
    extra["function_coverage"] = json!(function);
    extra["coverage_source"] = json!("gcov");
    extra["tem_score"] = json!(score);
    extra["metrics_summary"] = json!(summary);
    engine["score"] = json!(score);
    engine["max_score"] = json!(5.0);
    let mut targets = legacy_extra::targets(engine);
    let mut positions: Vec<Option<usize>> = (0..targets.len()).map(Some).collect();
    let failed = targets
        .iter()
        .any(|target| part(target) == Part::Case && target["status"] == "FAIL");
    // A compile that did not finish is one of frozen's `_coverage_errors`, so its run has no
    // coverage policy (`coverage_complete` is false): the rows stay, their gates do not.
    let compile_incomplete = targets.iter().any(|target| {
        matches!(
            target["message"].as_str(),
            Some("Compiler process terminated before reporting results")
                | Some("Compilation output was incomplete")
        )
    });
    if compile_incomplete {
        engine["extra"]["coverage_policy"] = Value::Null;
        let mut kept = 0;
        for (position, target) in positions.iter_mut().zip(&targets) {
            *position = (part(target) == Part::Case).then(|| {
                kept += 1;
                kept - 1
            });
        }
        targets.retain(|target| part(target) == Part::Case);
    } else if !failed {
        let threshold = minimum(context, "min_tem_score", 4.0);
        if score < threshold {
            targets.push(breach(
                "TEM score",
                format!("TEM score {score:.2} is below the configured minimum {threshold:.2}"),
                score,
                threshold,
            ));
        }
        let threshold = minimum(context, "min_branch_cov", 80.0);
        if branch < threshold {
            targets.push(breach(
                "Branch coverage",
                format!(
                    "Branch coverage {branch:.1}% is below the configured minimum {threshold:.1}%"
                ),
                branch,
                threshold,
            ));
        }
    }
    let verdict = engine["summary"].as_str().unwrap_or_default().to_owned();
    finish(engine, "ERROR", "NOT_RUN", &verdict, targets, &positions);
}

/// What frozen's run measured when its coverage evidence is incomplete.
struct Pool<'a> {
    /// The Python half's measured extra, when coverage.py reported.
    python: Option<&'a Value>,
    /// The C++ half's frozen extra.
    cpp: &'a Value,
    /// The extra frozen's quality keys come from.
    quality: &'a Value,
    /// Python's (passed, total) cases.
    python_cases: (u64, u64),
    /// `_tool_errors` before the coverage policy.
    tool_error: Option<String>,
    /// `_coverage_errors`, in order.
    coverage_errors: Vec<String>,
    /// `_coverage_failure_messages`' missing-evidence message.
    missing: Option<&'a str>,
    /// `_build_coverage_summary`'s label.
    source: &'a str,
}

/// Frozen `TestEngine.run` over incomplete coverage evidence: the measured rows pooled once, no
/// coverage policy (`coverage_complete` is false), and the missing evidence an optional warning
/// (no threshold breach; `_result_status` makes a run without a failure WARN in every mode) or,
/// under `coverage_required`, tool errors in `_apply_coverage_policy`'s order.
fn pooled_verdict(engine: &mut Value, context: &Context<'_>, pool: &Pool<'_>) -> bool {
    let cpp = pool.cpp;
    let (Some(cpp_passed), Some(cpp_total)) =
        (cpp["passed_tests"].as_u64(), cpp["total_tests"].as_u64())
    else {
        return false;
    };
    let (passed, total) = (
        pool.python_cases.0 + cpp_passed,
        pool.python_cases.1 + cpp_total,
    );
    let halves: Vec<&Value> = pool.python.into_iter().chain([cpp]).collect();
    let collect = |key: &str| -> Vec<Value> {
        halves
            .iter()
            .flat_map(|half| half[key].as_array().cloned().unwrap_or_default())
            .collect()
    };
    let mut files = collect("coverage_files");
    files.sort_by(|left, right| {
        left["cover"]
            .as_f64()
            .unwrap_or(f64::NEG_INFINITY)
            .total_cmp(&right["cover"].as_f64().unwrap_or(f64::NEG_INFINITY))
            .then_with(|| left["file"].as_str().cmp(&right["file"].as_str()))
    });
    if files.is_empty() {
        return false;
    }
    let function_rows = collect("function_rows");
    let (statements, covered) = (sum(&files, "stmts"), sum(&files, "covered"));
    let (branches, covered_branches) = (sum(&files, "nb"), sum(&files, "cb"));
    let cover = percent(covered, statements).map(round1);
    let branch_cover = percent(covered_branches, branches).map(round1);
    let covered_functions = function_rows
        .iter()
        .filter(|row| row["covered"] == true)
        .count() as u64;
    // Frozen `_measure_coverage` over a measured run: no row is a vacuous 100.0.
    let function = percent(covered_functions, function_rows.len() as u64).unwrap_or(100.0);
    let branch = branch_cover.unwrap_or(100.0);
    let tem = calculate(&TemInputs {
        passed,
        total,
        line_coverage: cover,
        branch_coverage: branch_cover,
        function_coverage: Some(function),
        estimated_coverage: (cover.is_none() && branch_cover.is_none()).then_some(branch),
    });
    let (Some(score), Some(shown)) = (tem.score, tem.coverage_shown) else {
        return false;
    };
    let all = legacy_extra::targets(engine);
    let has_failure = all
        .iter()
        .filter(|target| part(target) == Part::Case)
        .any(|target| target["status"] == "FAIL");
    let skipped: u64 = all
        .iter()
        .filter(|target| part(target) == Part::Case && target["status"] == "SKIP")
        .map(|target| target["metrics"]["test_cases"].as_u64().unwrap_or(1))
        .sum();
    // Frozen `no_tests_executed` is a verdict of its own, which no capture of this state reaches.
    if !has_failure && total > 0 && skipped >= total {
        return false;
    }
    let mut targets: Vec<Value> = vec![];
    let mut positions: Vec<Option<usize>> = vec![None; all.len()];
    let cpp_case = |target: &Value| {
        target["target_name"]
            .as_str()
            .is_some_and(|name| name.starts_with("[C++] "))
    };
    let wanted: [&dyn Fn(&Value) -> bool; 3] = [
        &|target| part(target) == Part::Case && !cpp_case(target),
        &|target| part(target) == Part::Case && cpp_case(target),
        &|target| part(target) == Part::Quality,
    ];
    for wanted in wanted {
        for (index, target) in all.iter().enumerate() {
            if wanted(target) {
                positions[index] = Some(targets.len());
                targets.push(target.clone());
            }
        }
    }
    let cases: Vec<Value> = targets
        .iter()
        .filter(|target| part(target) == Part::Case)
        .cloned()
        .collect();
    let mode = context.settings["mode"].as_str().unwrap_or("pass_fail");
    let label = tem.coverage_label.as_str();
    let suffix = if tem.estimated { " (est)" } else { "" };
    let mut summary = format!(
        "{passed}/{total} Tests Passed | {label}: {shown:.1}%{suffix}, Func: {function:.1}% -> TEM: {score:.2} / 5.0"
    );
    if label != "Branch" {
        summary = summary.replacen(" -> TEM:", &format!(", Branch: {branch:.1}% -> TEM:"), 1);
    }
    let required = context.settings["coverage_required"] == true;
    let errors: Vec<&str> = pool
        .tool_error
        .iter()
        .map(String::as_str)
        .chain(
            pool.coverage_errors
                .iter()
                .map(String::as_str)
                .filter(|_| required),
        )
        .chain(pool.missing.filter(|_| required))
        .take(3)
        .collect();
    let optional =
        format!("{summary}; Coverage evidence ESTIMATED (optional; not threshold evidence)");
    let (status, evidence, summary) = if !errors.is_empty() {
        ("ERROR", "NOT_RUN", errors.join("; "))
    } else if !has_failure {
        ("WARN", "ESTIMATED", optional)
    } else {
        // The optional coverage warning is itself a warning beside the failure.
        (
            legacy_extra::evaluate_status(true, true, mode),
            "ESTIMATED",
            optional,
        )
    };
    // Frozen `_threshold_breaches`: skipped only by an optional warning or a failure.
    if required && !has_failure {
        let threshold = minimum(context, "min_tem_score", 4.0);
        if score < threshold {
            targets.push(breach(
                "TEM score",
                format!("TEM score {score:.2} is below the configured minimum {threshold:.2}"),
                score,
                threshold,
            ));
        }
        let threshold = minimum(context, "min_branch_cov", 80.0);
        if branch < threshold {
            targets.push(breach(
                "Branch coverage",
                format!(
                    "Branch coverage {branch:.1}% is below the configured minimum {threshold:.1}%"
                ),
                branch,
                threshold,
            ));
        }
    }
    let mut included: Vec<&Value> = files.iter().map(|row| &row["file"]).collect();
    included.sort_by(|left, right| left.as_str().cmp(&right.as_str()));
    let mut scope = cpp["coverage_provenance"]["scope"].clone();
    scope["included_sources"] = json!(included);
    let mut provenance = json!({"cpp": cpp["coverage_provenance"]["cpp"], "scope": scope});
    if let Some(python) = pool.python {
        provenance["python"] = python["coverage_provenance"]["python"].clone();
    }
    let test_suites = legacy_extra::frozen_test_suites(&cases);

    let extra = &mut engine["extra"];
    extra["passed_tests"] = json!(passed);
    extra["total_tests"] = json!(total);
    extra["skipped_tests"] = json!(skipped);
    extra["coverage_files"] = json!(files);
    extra["coverage_totals"] = json!({"stmts": statements, "miss": sum(&files, "miss"),
        "cover": cover, "branch_cover": branch_cover});
    extra["coverage_source"] = json!(pool.source);
    extra["coverage_provenance"] = provenance;
    extra["coverage_policy"] = Value::Null;
    extra["function_rows"] = json!(function_rows);
    extra["function_coverage"] = json!(function);
    extra["branch_coverage"] = json!(branch);
    extra["line_coverage"] = json!(cover);
    extra["pass_rate"] = json!(tem.pass_rate);
    extra["tem_score"] = json!(score);
    extra["metrics_summary"] = json!(format!(
        "TEM: {score:.2}/5.0 ({label}: {shown:.0}%, Func: {function:.0}%, PassRate: {:.0}%)",
        tem.pass_rate * 100.0
    ));
    extra["test_suites"] = json!(test_suites);
    for key in QUALITY_KEYS {
        if let Some(value) = pool.quality.get(key) {
            extra[key] = value.clone();
        }
    }
    if let Some(quality) = extra["test_quality"].as_object_mut() {
        // The check's native quality evidence stays in its own block (frozen keys only here).
        quality.remove("evidence");
        quality.remove("repeat_observations");
        quality.insert("profile".into(), json!(context.profile));
    }
    if let Some(fields) = extra.as_object_mut() {
        fields.remove("test_aggregation_limitation");
    }
    engine["score"] = json!(score);
    engine["max_score"] = json!(5.0);
    finish(engine, status, evidence, &summary, targets, &positions)
}

/// Native target scopes index the targets; a pooled target spans both languages' tasks.
fn remap_scopes(engine: &mut Value, positions: &[Option<usize>]) {
    if let Some(scopes) = engine["extra"]["target_scopes"].as_array_mut() {
        scopes.retain_mut(|scope| {
            match scope["target_index"]
                .as_u64()
                .and_then(|index| positions.get(index as usize).copied().flatten())
            {
                Some(index) => {
                    scope["target_index"] = json!(index);
                    true
                }
                None => false,
            }
        });
    }
}

/// Frozen `findings_for_result`: an explicit finding replaces the target adapter of its observation.
fn merge_explicit(engine: &mut Value, explicit: Vec<Value>) {
    if explicit.is_empty() {
        return;
    }
    let Some(findings) = engine["findings"].as_array_mut() else {
        return;
    };
    for finding in explicit {
        let place = |other: &Value| {
            let location = |value: &Value| {
                (
                    value["primary_location"]["path"].clone(),
                    value["primary_location"]["start_line"].clone(),
                    value["message"].clone(),
                )
            };
            location(other) == location(&finding)
        };
        match findings.iter().position(place) {
            Some(index) => findings[index] = finding,
            None => findings.push(finding),
        }
    }
    findings.sort_by_key(|finding| {
        (
            finding["fingerprint"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            finding["primary_location"]["path"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            finding["primary_location"]["start_line"]
                .as_u64()
                .unwrap_or(1),
            finding["message"].as_str().unwrap_or_default().to_owned(),
        )
    });
}

/// An ERROR coverage-policy target frozen also reports: the refused selection, or an unmeasured
/// changed-line selection.
fn policy_error(target: &Value) -> bool {
    target["target_name"] == "Coverage:Policy configuration" || unmeasured_changed_lines(target)
}

/// A changed-line selection a half did not measure: frozen's ERROR when no half measured it.
fn unmeasured_changed_lines(target: &Value) -> bool {
    target["target_name"] == "Coverage:Changed lines"
        && target["status"] == "ERROR"
        && target["message"] == "Changed-line coverage has no executable measured lines"
}
