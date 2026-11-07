//! Measured legacy-facing fields with physical suite identities and independent coverage axes.
use crate::{ValidationKind, ValidationPlan, coverage::CoverageReport, python_coverage, tem};
use ici_domain::{AnalysisOutput, TaskState};
use ici_execution::TaskOutcome;
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub(crate) fn attach(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) {
    let phase = match plan.kind {
        ValidationKind::PythonTest
        | ValidationKind::Ctest
        | ValidationKind::Qtest
        | ValidationKind::MakeCheck
        | ValidationKind::CppExecutable => "test",
        ValidationKind::PythonCoverage | ValidationKind::Gcov => "coverage",
        _ => return,
    };
    let canonical = plan
        .context
        .options
        .shared_test_task_id
        .clone()
        .unwrap_or_else(|| crate::next_suite::step_id(&plan.context));
    let framework = match plan.kind {
        ValidationKind::PythonTest | ValidationKind::PythonCoverage => {
            if crate::python_unittest::missing_pytest(plan, outcomes).unwrap_or(false) {
                "unittest"
            } else {
                "pytest"
            }
        }
        ValidationKind::Ctest => "ctest",
        ValidationKind::Qtest => "qtest",
        ValidationKind::MakeCheck => "make-check",
        ValidationKind::CppExecutable => "cpp-executable",
        ValidationKind::Gcov => plan
            .context
            .options
            .shared_test_kind
            .map_or("unknown", ValidationKind::provider),
        _ => unreachable!(),
    };
    let mut coverage_evidence = AnalysisOutput::new(&plan.context.task_id, framework);
    if phase == "coverage" {
        let evidence = &mut coverage_evidence;
        let result = if matches!(plan.kind, ValidationKind::Gcov) {
            if !plan.context.options.shared_cpp_test_sources.is_empty() {
                crate::cpp::collective_test_evidence(plan, outcomes)
                    .ok()
                    .map(|tests| *evidence = tests)
            } else {
                outcomes
                    .get(&canonical)
                    .and_then(|outcome| crate::cpp::test_evidence(plan, &canonical, outcome).ok())
                    .map(|tests| *evidence = tests)
            }
        } else if framework == "unittest" {
            crate::python_unittest::observe(plan, outcomes, evidence).ok()
        } else {
            python_coverage::actual_suite_id(plan, outcomes)
                .ok()
                .and_then(|id| outcomes.get(&id))
                .and_then(|outcome| {
                    let text = std::str::from_utf8(&outcome.stdout).ok()?;
                    let tests = crate::python::parse_pytest(text, &plan.context).ok()?;
                    let total = tests.cases.len() as u64;
                    // Frozen legacy's summary-count reading of a run without a verdict line,
                    // when frozen generated its coverage JSON.
                    let frozen = plan
                        .context
                        .options
                        .legacy_pytest_result
                        .then(|| {
                            crate::python::frozen_pytest_result(
                                text,
                                &String::from_utf8_lossy(&outcome.stderr),
                                outcome.exit_code.unwrap_or(-1),
                                &tests,
                            )
                        })
                        .filter(|frozen| total == 0 && frozen.coverage_ready());
                    if let Some(frozen) = &frozen {
                        evidence.extra["legacy_case_counts"] = json!({"passed":frozen.passed,"total":frozen.total,"skipped":frozen.skipped});
                        evidence.extra["test_outcomes"] = json!({});
                    }
                    if total > 0 {
                        // The shared suite's legacy counts (frozen `_parse_pytest_stdout`).
                        let (passed, lines, skipped) = tests.legacy_counts();
                        evidence.extra["legacy_case_counts"] =
                            json!({"passed":passed,"total":lines,"skipped":skipped});
                        evidence.extra["test_outcomes"] = json!(
                            tests
                                .cases
                                .iter()
                                .map(|(node, case)| (node, &case.verdict))
                                .collect::<BTreeMap<_, _>>()
                        );
                        for case in tests.cases.values() {
                            let mut target =
                                ici_domain::InspectionTarget::at(&case.path, case.line);
                            target.target_name = case.node.clone();
                            target.status = match case.verdict.as_str() {
                                "PASSED" | "XFAIL" => "PASS",
                                "SKIPPED" => "SKIP",
                                _ => "FAIL",
                            }
                            .into();
                            target.message = case.verdict.clone();
                            evidence.targets.push(target);
                        }
                    }
                    (crate::planning::completed(outcome)
                        && matches!(outcome.exit_code, Some(0 | 1))
                        && (frozen.is_some()
                            || (!tests.collection_error
                                && total > 0
                                && tests.cases.values().any(|case| case.verdict != "SKIPPED"))))
                    .then_some(())
                })
        };
        if result.is_none() {
            evidence.observation.state = TaskState::Failed;
        }
    }
    let evidence = if phase == "coverage" {
        &coverage_evidence
    } else {
        &*output
    };
    let suite_rows = suites(evidence);
    // A pytest suite reports its frozen legacy counts (every verdict line, XFAIL passing);
    // other frameworks report their `.cases` metric.
    let legacy = evidence.extra.get("legacy_case_counts");
    let counts = legacy
        .and_then(|counts| counts["passed"].as_u64().zip(counts["total"].as_u64()))
        .or_else(|| {
            evidence
                .observation
                .measurements
                .iter()
                .find(|metric| metric.name.ends_with(".cases"))
                .and_then(|metric| metric.numerator.zip(metric.denominator))
        });
    let skipped = legacy
        .and_then(|counts| counts["skipped"].as_u64())
        .or_else(|| {
            evidence
                .extra
                .get("test_outcomes")
                .and_then(Value::as_object)
                .map(|nodes| {
                    nodes
                        .values()
                        .filter(|state| state.as_str() == Some("SKIPPED"))
                        .count() as u64
                })
        });
    let actual_ids = if framework == "unittest" {
        (0..plan.context.targets.len())
            .map(|index| {
                format!(
                    "{}.unittest{index}",
                    canonical.strip_suffix(".suite").unwrap_or(&canonical)
                )
            })
            .collect::<Vec<_>>()
    } else if matches!(
        plan.kind,
        ValidationKind::PythonTest | ValidationKind::PythonCoverage
    ) {
        python_coverage::actual_suite_id(plan, outcomes)
            .ok()
            .into_iter()
            .collect()
    } else if !plan.context.options.shared_cpp_test_sources.is_empty() {
        plan.context
            .options
            .shared_cpp_test_sources
            .keys()
            .cloned()
            .collect()
    } else {
        vec![canonical.clone()]
    };
    let complete = evidence.observation.state == TaskState::Succeeded && counts.is_some();
    let physical_suites = if !plan.context.options.shared_cpp_test_sources.is_empty() {
        plan.context.options.shared_cpp_test_sources.keys().map(|id| {
            let measured=outcomes.get(id).and_then(|outcome|crate::cpp::test_evidence(plan,id,outcome).ok());
            let counts=measured.as_ref().and_then(|output|output.observation.measurements.iter().find(|metric|metric.name.ends_with(".cases")).and_then(|metric|metric.numerator.zip(metric.denominator)));
            json!({"canonical_suite_id":id,"complete":counts.is_some(),"passed_tests":counts.map(|(passed,_)|passed),"total_tests":counts.map(|(_,total)|total),"skipped_tests":counts.map(|_|0),"framework":"cpp-executable"})
        }).collect::<Vec<_>>()
    } else {
        vec![
            json!({"canonical_suite_id":canonical,"complete":complete,"passed_tests":counts.map(|(passed,_)|passed),"total_tests":counts.map(|(_,total)|total),"skipped_tests":skipped,"framework":framework}),
        ]
    };
    output.extra["physical_test_suites"] = json!(physical_suites);
    output.extra["test_execution"] = json!({"canonical_suite_id":canonical,"actual_suite_ids":actual_ids,"phase":phase,"framework":framework,"complete":complete,"passed_tests":counts.map(|(passed,_)|passed),"total_tests":counts.map(|(_,total)|total),"skipped_tests":skipped,"pass_rate":counts.filter(|(_,total)|*total>0).map(|(passed,total)|passed as f64/total as f64)});
    for key in ["passed_tests", "total_tests", "skipped_tests", "pass_rate"] {
        output.extra[key] = output.extra["test_execution"][key].clone();
    }
    output.extra["test_suites"] = if counts.is_some() {
        suite_rows
    } else {
        Value::Null
    };
    for key in [
        "branch_coverage",
        "function_coverage",
        "line_coverage",
        "tem_score",
        "coverage_totals",
        "coverage_files",
        "function_rows",
        "coverage_source",
        "coverage_provenance",
    ] {
        if output.extra.get(key).is_none() {
            output.extra[key] = Value::Null;
        }
    }
    if phase == "test" {
        quality(plan, output);
    }
}
fn suites(evidence: &AnalysisOutput) -> Value {
    let Some(nodes) = evidence
        .extra
        .get("test_outcomes")
        .and_then(Value::as_object)
    else {
        return Value::Null;
    };
    let mut groups = BTreeMap::new();
    let targets = evidence
        .targets
        .iter()
        .map(|target| (target.target_name.as_str(), target))
        .collect::<BTreeMap<_, _>>();
    for (node, state) in nodes {
        let name = node
            .strip_prefix("unittest:")
            .or_else(|| node.strip_prefix("ctest:"))
            .or_else(|| node.strip_prefix("qtest:"))
            .or_else(|| node.strip_prefix("cpp-executable:"))
            .unwrap_or(node);
        let target = targets.get(name).copied();
        let path = target.map_or("tests", |target| target.file_path.as_str());
        let state = state.as_str().unwrap_or("UNKNOWN");
        let status = match state {
            "PASSED" | "XFAIL" => "PASS",
            "SKIPPED" => "SKIP",
            _ => "FAIL",
        };
        let group = groups.entry(path.to_owned()).or_insert_with(
            || json!({"file":path,"passed":0,"failed":0,"skipped":0,"total":0,"tests":[]}),
        );
        let key = match status {
            "PASS" => "passed",
            "SKIP" => "skipped",
            _ => "failed",
        };
        group[key] = json!(group[key].as_u64().expect("counter") + 1);
        group["total"] = json!(group["total"].as_u64().expect("counter") + 1);
        group["tests"].as_array_mut().expect("array").push(json!({"name":name,"status":status,"message":target.map(|target|target.message.as_str()).unwrap_or(state)}));
    }
    json!(groups.into_values().collect::<Vec<_>>())
}
fn round_one(value: Option<f64>) -> Option<f64> {
    value.map(ici_domain::python_round_one)
}
pub(crate) fn coverage(
    plan: &ValidationPlan,
    report: &CoverageReport,
    output: &mut AnalysisOutput,
) {
    let counts = &report.counts;
    let line = round_one(tem::percentage(counts.covered_lines, counts.total_lines));
    let legacy = plan.context.options.legacy_coverage_inputs;
    let (function, branch) = tem::coverage_inputs(counts, legacy);
    // Frozen legacy `extra.branch_coverage` is the rows' one-decimal `branch_cover` (or the
    // vacuous 100.0) and `extra.function_coverage` the unrounded `_measure_coverage` ratio.
    let branch = round_one(branch);
    let function = if legacy {
        function
    } else {
        round_one(function)
    };
    // `coverage_totals.branch_cover` stays the measured ratio (frozen `None` without a branch).
    let branch_cover = round_one(tem::percentage(
        counts.covered_branches,
        counts.total_branches,
    ));
    output.extra["coverage_totals"] = json!({"stmts":counts.total_lines,"covered":counts.covered_lines,"miss":counts.total_lines-counts.covered_lines,"nb":counts.total_branches,"cb":counts.covered_branches,"cover":line,"branch_cover":branch_cover,"covered_functions":counts.covered_functions,"functions":counts.total_functions});
    let mut files=report.files.values().map(|file|json!({"file":file.path,"stmts":file.counts.total_lines,"covered":file.counts.covered_lines,"miss":file.counts.total_lines-file.counts.covered_lines,"cover":round_one(tem::percentage(file.counts.covered_lines,file.counts.total_lines)),"branch_cover":round_one(tem::percentage(file.counts.covered_branches,file.counts.total_branches)),"nb":file.counts.total_branches,"cb":file.counts.covered_branches,"missing_lines":file.missing.iter().take(30).collect::<Vec<_>>()})).collect::<Vec<_>>();
    files.sort_by(|a, b| {
        a["cover"]
            .as_f64()
            .unwrap_or(f64::INFINITY)
            .total_cmp(&b["cover"].as_f64().unwrap_or(f64::INFINITY))
            .then_with(|| a["file"].as_str().cmp(&b["file"].as_str()))
    });
    output.extra["coverage_files"] = json!(files);
    output.extra["function_rows"] = json!(report.function_rows);
    output.extra["branch_coverage"] = json!(branch);
    output.extra["function_coverage"] = json!(function);
    output.extra["line_coverage"] = json!(line);
    output.extra["tem_score"] = json!(output.score);
    output.extra["coverage_source"] = json!(if plan.kind == ValidationKind::Gcov {
        "gcov"
    } else {
        "coverage.py"
    });
    output.extra["coverage_provenance"] = json!({"format":if plan.kind==ValidationKind::Gcov{if output.extra.get("gcov_text_info").is_some(){"gcov-text"}else{"gcov-json"}}else{"coverage.py-json"},"canonical_suite_id":plan.context.options.shared_test_task_id.clone().unwrap_or_else(||crate::next_suite::step_id(&plan.context)),"included_sources":report.files.keys().collect::<Vec<_>>(),"excluded_sources":report.excluded_files,"limitations":report.limitations});
}
fn quality(plan: &ValidationPlan, output: &mut AnalysisOutput) {
    let enabled = plan.context.options.repeat_runs > 0
        || plan.context.options.slow_threshold_seconds.is_some()
        || plan.context.options.mutation_enabled;
    output.extra["quality_enabled"] = json!(enabled);
    let observed = output.extra.get("test_quality").is_some();
    let slow_inventory = output
        .extra
        .get("slow_tests")
        .filter(|value| value.is_array())
        .cloned();
    let mut quality = output
        .extra
        .get("test_quality")
        .cloned()
        .unwrap_or_else(|| json!({}));
    quality["enabled"] = json!(enabled);
    quality["mode"] = json!(plan.context.options.quality_mode);
    quality["evidence"] = json!(if !enabled {
        "NOT_SELECTED"
    } else if observed {
        "MEASURED"
    } else {
        "NOT_RUN"
    });
    for key in [
        "repeat_runs",
        "repeat_reruns",
        "repeat_cases",
        "repeat_unavailable",
        "repeat_timeouts",
        "flaky_tests",
        "slow_tests",
        "slow_tests_observed",
    ] {
        let value = if enabled {
            quality
                .get(key)
                .cloned()
                .unwrap_or_else(|| if observed { json!(0) } else { Value::Null })
        } else {
            json!(0)
        };
        quality[key] = value.clone();
        output.extra[key] = value;
    }
    for (key, source) in [
        ("slow_test_inventory", "slow_tests"),
        ("flaky_test_inventory", "flaky_test_inventory"),
    ] {
        let value = if source == "slow_tests" {
            slow_inventory.clone()
        } else {
            quality.get(source).cloned()
        }
        .unwrap_or_else(|| {
            if !enabled || observed {
                json!([])
            } else {
                Value::Null
            }
        });
        quality[key] = value.clone();
        output.extra[key] = value;
    }
    let mutation = output
        .extra
        .get("mutation")
        .cloned()
        .unwrap_or_else(|| json!({}));
    for key in [
        "mutation_probes",
        "mutation_available",
        "mutation_unavailable",
        "mutation_status",
    ] {
        let value = mutation.get(key).cloned().unwrap_or_else(|| {
            if key == "mutation_status" {
                json!(if plan.context.options.mutation_enabled {
                    "not_run"
                } else {
                    "disabled"
                })
            } else {
                json!(0)
            }
        });
        quality[key] = value.clone();
        output.extra[key] = value;
    }
    output.extra["test_quality"] = quality;
}
