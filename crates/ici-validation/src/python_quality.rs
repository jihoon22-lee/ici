//! Bounded optional quality observations retain the independently completed base suite.
use crate::{
    ValidationError, ValidationPlan, planning,
    python::{TestEvidence, finding, node_location},
};
use ici_domain::{AnalysisOutput, InspectionTarget, Measurement, sha256};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
fn suite<'a>(
    plan: &ValidationPlan,
    outcomes: &'a BTreeMap<String, TaskOutcome>,
) -> Result<&'a TaskOutcome, ValidationError> {
    let id = crate::python_coverage::actual_suite_id(plan, outcomes)?;
    outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("quality base suite is absent".into()))
}
pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    initial: &TestEvidence,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let mut valid_runs = vec![
        initial
            .cases
            .iter()
            .map(|(node, case)| (node.clone(), case.verdict.clone()))
            .collect::<BTreeMap<_, _>>(),
    ];
    let mut locations = initial.cases.clone();
    let mut repeat_observations = vec![];
    let mut unavailable = 0usize;
    let mut timeouts = 0usize;
    let mut attempted = 1usize;
    // Frozen order: slow-test targets precede the repeat/flaky observations.
    let quality_start = output.targets.len();
    // Frozen `_run_deep_test_quality` (test_quality.py:375-392): a base run without per-test
    // outcomes (the legacy summary-count reading) is one unavailable run and no flaky baseline.
    let base_available = !initial.cases.is_empty();
    if !base_available && plan.context.options.repeat_runs > 1 {
        unavailable += 1;
        let mut target = InspectionTarget::at("tests", 1);
        target.target_name = "[test-quality] Base run outcomes".into();
        target.status = "WARN".into();
        target.message =
            "base pytest run had no per-test outcome evidence; flaky comparison is unavailable"
                .into();
        target.metrics.insert("run".into(), json!(1));
        output.targets.push(target);
    }
    for index in 0..plan.context.options.repeat_runs.saturating_sub(1) {
        let id = format!("{}.repeat{}", plan.context.task_id, index + 1);
        let outcome = outcomes.get(&id);
        attempted += usize::from(outcome.is_some());
        let evidence = outcome
            .filter(|outcome| {
                planning::completed(outcome) && matches!(outcome.exit_code, Some(0 | 1))
            })
            .and_then(|outcome| std::str::from_utf8(&outcome.stdout).ok())
            .and_then(|text| crate::python::parse_pytest(text, &plan.context).ok())
            .filter(|evidence| !evidence.collection_error && !evidence.cases.is_empty());
        let available = evidence.is_some();
        let timed_out = outcome.is_some_and(|outcome| outcome.timed_out);
        repeat_observations.push(json!({"task_id":id,"run":index+2,"available":available,"state":outcome.map(|outcome| outcome.state),"exit_code":outcome.and_then(|outcome|outcome.exit_code),"timed_out":timed_out,"truncated":outcome.is_some_and(|outcome|outcome.truncated),"cancelled":outcome.is_some_and(|outcome|outcome.cancelled)}));
        if let Some(evidence) = evidence {
            valid_runs.push(
                evidence
                    .cases
                    .iter()
                    .map(|(node, case)| (node.clone(), case.verdict.clone()))
                    .collect(),
            );
            locations.extend(evidence.cases);
        } else {
            // Frozen `_observe_quality` distinguishes a timeout, an unbounded process (truncated,
            // signalled or exit outside 0/1) and a bounded run without per-test verdicts.
            let bounded = outcome.is_some_and(|outcome| {
                planning::completed(outcome) && matches!(outcome.exit_code, Some(0 | 1))
            });
            if timed_out {
                timeouts += 1;
            } else {
                unavailable += 1;
            }
            let mut target = InspectionTarget::at("tests", 1);
            target.target_name = format!("[test-quality] Repeat run {}", index + 2);
            target.status = "WARN".into();
            target.metrics.insert("run".into(), json!(index + 2));
            if timed_out {
                target.message = "pytest repeat run timed out".into();
                target.metrics.insert(
                    "timeout".into(),
                    json!(plan.context.options.quality_timeout_seconds),
                );
            } else if bounded {
                target.message = "pytest repeat run had no per-test collection evidence".into();
            } else {
                target.message = "pytest repeat run did not produce bounded evidence".into();
                let returncode = outcome.and_then(|outcome| {
                    outcome
                        .exit_code
                        .or_else(|| outcome.signal.map(|signal| -signal))
                });
                target
                    .metrics
                    .insert("returncode".into(), json!(returncode));
            }
            output.targets.push(target);
        }
    }
    // test_quality.py:445-447: without base outcomes there is no repeat case or flaky target.
    let nodes = valid_runs
        .iter()
        .filter(|_| base_available)
        .flat_map(|run| run.keys().cloned())
        .collect::<BTreeSet<_>>();
    let mut flaky = 0usize;
    let mut flaky_inventory = vec![];
    for node in &nodes {
        let verdicts = valid_runs
            .iter()
            .map(|run| run.get(node).map(String::as_str).unwrap_or("NOT_COLLECTED"))
            .collect::<Vec<_>>();
        if verdicts.iter().all(|verdict| verdict == &verdicts[0]) {
            continue;
        }
        let case = &locations[node];
        flaky += 1;
        let mut target = InspectionTarget::at(&case.path, case.line);
        target.target_name = format!("[test-quality] Flaky test: {node}");
        target.status = "WARN".into();
        target.message = format!(
            "pytest verdict changed across bounded repeat runs: {}",
            verdicts
                .iter()
                .enumerate()
                .map(|(index, verdict)| format!("run {}={verdict}", index + 1))
                .collect::<Vec<_>>()
                .join(", ")
        );
        let passes = verdicts
            .iter()
            .filter(|verdict| matches!(**verdict, "PASSED" | "XFAIL"))
            .count();
        let failures = verdicts
            .iter()
            .filter(|verdict| matches!(**verdict, "FAILED" | "ERROR" | "XPASS"))
            .count();
        target.metrics.insert("runs".into(), json!(verdicts.len()));
        target.metrics.insert("statuses".into(), json!(verdicts));
        target.metrics.insert("passes".into(), json!(passes));
        target.metrics.insert("failures".into(), json!(failures));
        let mut item = finding(
            plan,
            "ici.test.flaky-test",
            &target.message,
            &case.path,
            case.line,
        );
        item.fingerprint = sha256(format!(
            "pytest\0ici.test.flaky-test\0{}\0{}\0{node}",
            case.path, case.line
        ));
        if plan.context.options.quality_mode == "report" {
            item.tags.push("advisory".into());
        }
        crate::finding_details::quality(&item, &target, output);
        flaky_inventory.push(
            json!({"nodeid":node,"file_path":case.path,"start_line":case.line,"statuses":verdicts}),
        );
        output.targets.push(target);
        output.observation.findings.push(item);
    }
    output.extra["test_quality"] = json!({"mode":plan.context.options.quality_mode,"repeat_runs":attempted,"repeat_reruns":attempted.saturating_sub(1),"repeat_cases":nodes.len(),"repeat_unavailable":unavailable,"repeat_timeouts":timeouts,"flaky_tests":flaky,"flaky_test_inventory":flaky_inventory,"repeat_observations":repeat_observations});
    if plan.context.options.repeat_runs > 1 {
        output
            .observation
            .measurements
            .push(Measurement::measured("test-quality.flaky", flaky as f64));
    }
    if let Some(threshold) = plan
        .context
        .options
        .slow_threshold_seconds
        .filter(|threshold| *threshold > 0.0)
    {
        let duration =
            Regex::new(r"^\s*((?:\d+(?:\.\d+)?|\.\d+))s\s+([A-Za-z][A-Za-z0-9_-]*)\s+(\S.*?)\s*$")
                .expect("constant regex");
        let primary = suite(plan, outcomes)?;
        let mut seen = BTreeMap::<(String, String), (f64, String, u32)>::new();
        // Frozen `_pytest_duration_summary` strips ANSI colour before matching rows.
        let ansi = Regex::new(r"\x1b\[[0-?]*[ -/]*[@-~]").expect("constant regex");
        let stdout = String::from_utf8_lossy(&primary.stdout);
        for line in ansi.replace_all(&stdout, "").lines() {
            let Some(parts) = duration.captures(line) else {
                continue;
            };
            if parts[1].len() > 32 || !parts[3].contains("::") {
                continue;
            }
            let Ok(seconds) = parts[1].parse::<f64>() else {
                continue;
            };
            if !seconds.is_finite() || seconds < threshold {
                continue;
            }
            let (path, line) = node_location(&parts[3], &plan.context)?;
            let key = (parts[3].to_string(), parts[2].to_string());
            let value = seen.entry(key).or_insert((seconds, path, line));
            value.0 = value.0.max(seconds);
        }
        let mut rows = seen.into_iter().collect::<Vec<_>>();
        // Frozen order: slowest first, then file path, line, node id and phase.
        rows.sort_by(|a, b| {
            b.1.0
                .total_cmp(&a.1.0)
                .then_with(|| a.1.1.cmp(&b.1.1))
                .then_with(|| a.1.2.cmp(&b.1.2))
                .then_with(|| a.0.cmp(&b.0))
        });
        let observed = rows.len();
        rows.truncate(plan.context.options.max_slow_tests);
        let slow_start = output.targets.len();
        for ((node, phase), (seconds, path, line)) in &rows {
            let mut target = InspectionTarget::at(path, *line);
            target.target_name = format!("[test-quality] Slow test: {node} ({phase})");
            target.status = "WARN".into();
            target.message =
                format!("pytest {phase} phase took {seconds:.3}s (threshold {threshold:.3}s)");
            target.metrics.insert("duration".into(), json!(seconds));
            target.metrics.insert("threshold".into(), json!(threshold));
            target.metrics.insert("phase".into(), json!(phase));
            let mut item = finding(plan, "ici.test.slow-test", &target.message, path, *line);
            item.fingerprint = sha256(format!(
                "pytest\0ici.test.slow-test\0{path}\0{line}\0{node}\0{phase}"
            ));
            item.severity = "medium".into();
            if plan.context.options.quality_mode == "report" {
                item.tags.push("advisory".into());
            }
            crate::finding_details::quality(&item, &target, output);
            output.targets.push(target);
            output.observation.findings.push(item);
        }
        let slow: Vec<_> = output.targets.drain(slow_start..).collect();
        output.targets.splice(quality_start..quality_start, slow);
        output.extra["test_quality"]["slow_tests"] = json!(rows.len());
        output.extra["test_quality"]["slow_tests_observed"] = json!(observed);
        output.extra["slow_tests"]=json!(rows.iter().map(|((node,phase),(duration,path,line))|json!({"nodeid":node,"phase":phase,"duration":duration,"file_path":path,"start_line":line})).collect::<Vec<_>>());
        output
            .observation
            .measurements
            .push(Measurement::measured("test-quality.slow", observed as f64));
    }
    mutation(plan, outcomes, output);
    Ok(())
}
/// Frozen `_run_deep_test_quality` after `_mark_zero_tests("Python")`: the deep pass without a
/// base run ([`observe_without_base`]); the check keeps its no-tests reason as a blocked
/// verdict, since no suite ran.
pub(crate) fn observe_zero_tests(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    reason: &str,
    output: &mut AnalysisOutput,
) {
    observe_without_base(plan, outcomes, output);
    output.observation.state = ici_domain::TaskState::Blocked;
    output.observation.limitations.insert(0, reason.into());
    output.status = "SKIP".into();
    output.summary = reason.into();
}
/// Frozen `_run_deep_test_quality` whose base run has no outcomes (test_quality.py:368-453):
/// with `repeat_runs` > 1 the base is one unavailable run ('[test-quality] Base run outcomes'),
/// each rerun is judged as in [`observe`] (a timeout, an unbounded process, or a bounded run
/// without per-test verdicts) and nothing is compared for flakiness; the mutation probe follows.
pub(crate) fn observe_without_base(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) {
    let repeats = plan.context.options.repeat_runs.saturating_sub(1);
    let mut attempted = 1usize;
    let mut unavailable = 0usize;
    let mut timeouts = 0usize;
    let mut observations = vec![];
    if repeats > 0 {
        unavailable += 1;
        let mut target = InspectionTarget::at("tests", 1);
        target.target_name = "[test-quality] Base run outcomes".into();
        target.status = "WARN".into();
        target.message =
            "base pytest run had no per-test outcome evidence; flaky comparison is unavailable"
                .into();
        target.metrics.insert("run".into(), json!(1));
        output.targets.push(target);
    }
    for index in 0..repeats {
        let id = format!("{}.repeat{}", plan.context.task_id, index + 1);
        let Some(outcome) = outcomes.get(&id) else {
            continue;
        };
        attempted += 1;
        let run = index + 2;
        let bounded = planning::completed(outcome) && matches!(outcome.exit_code, Some(0 | 1));
        let verdicts = bounded
            .then(|| std::str::from_utf8(&outcome.stdout).ok())
            .flatten()
            .and_then(|text| crate::python::parse_pytest(text, &plan.context).ok())
            .is_some_and(|evidence| !evidence.collection_error && !evidence.cases.is_empty());
        observations.push(json!({"task_id":id,"run":run,"available":verdicts,"state":outcome.state,"exit_code":outcome.exit_code,"timed_out":outcome.timed_out,"truncated":outcome.truncated,"cancelled":outcome.cancelled}));
        if verdicts {
            continue;
        }
        let mut target = InspectionTarget::at("tests", 1);
        target.target_name = format!("[test-quality] Repeat run {run}");
        target.status = "WARN".into();
        target.metrics.insert("run".into(), json!(run));
        if outcome.timed_out {
            timeouts += 1;
            target.message = "pytest repeat run timed out".into();
            target.metrics.insert(
                "timeout".into(),
                json!(plan.context.options.quality_timeout_seconds),
            );
        } else if bounded {
            unavailable += 1;
            target.message = "pytest repeat run had no per-test collection evidence".into();
        } else {
            unavailable += 1;
            target.message = "pytest repeat run did not produce bounded evidence".into();
            let returncode = outcome
                .exit_code
                .or_else(|| outcome.signal.map(|signal| -signal));
            target
                .metrics
                .insert("returncode".into(), json!(returncode));
        }
        output.targets.push(target);
    }
    output.extra["test_quality"] = json!({"mode":plan.context.options.quality_mode,"repeat_runs":attempted,"repeat_reruns":attempted - 1,"repeat_cases":0,"repeat_unavailable":unavailable,"repeat_timeouts":timeouts,"flaky_tests":0,"flaky_test_inventory":[],"repeat_observations":observations});
    mutation(plan, outcomes, output);
}
fn mutation(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) {
    if !plan.context.options.mutation_enabled {
        output.extra["mutation"] = json!({"mutation_status":"disabled","mutation_probes":0,"mutation_available":0,"mutation_unavailable":0});
        return;
    }
    let id = format!("{}.mutation", plan.context.task_id);
    let probe = outcomes.get(&id);
    let available =
        probe.is_some_and(|outcome| planning::completed(outcome) && outcome.exit_code == Some(0));
    // Frozen `_probe_mutation_capability`: one target at `tests` naming the selected tool.
    let tool = plan.context.options.mutation_label.clone();
    let mut target = InspectionTarget::at("tests", 1);
    target.target_name = "[test-quality] Mutation capability".into();
    target.status = if available { "PASS" } else { "SKIP" }.into();
    target.message = match (probe, available) {
        (_, true) => format!("Mutation tool capability available: {tool}"),
        (None, _)
            if plan.context.options.mutation_tool.is_none()
                && !plan.context.options.mutation_command =>
        {
            "No supported mutation tool was found; base test gate unchanged".into()
        }
        _ => "Mutation capability probe unavailable; base test gate unchanged".into(),
    };
    target.metrics.insert("available".into(), json!(available));
    target.metrics.insert("tool".into(), json!(tool));
    if plan.context.options.mutation_tool.is_some() {
        target.metrics.insert(
            "returncode".into(),
            json!(probe.and_then(|outcome| outcome.exit_code)),
        );
    } else if plan.context.options.mutation_command {
        // Frozen runs the configured command; an absent executable is its runner's -1.
        target.metrics.insert("returncode".into(), json!(-1));
    }
    output.targets.push(target);
    output.extra["mutation"] = json!({"mutation_status":if available{"available"}else{"unavailable"},"mutation_probes":1,"mutation_available":u8::from(available),"mutation_unavailable":u8::from(!available),"tool":plan.context.options.mutation_tool.as_ref().map(|tool|&tool.id),"argv":plan.tasks.iter().find(|task|task.id==id).map(|task|&task.argv)});
}
