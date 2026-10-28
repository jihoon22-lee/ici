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
            if timed_out {
                timeouts += 1;
            } else {
                unavailable += 1;
            }
            let mut target = InspectionTarget::at(
                plan.context
                    .targets
                    .first()
                    .map(String::as_str)
                    .unwrap_or("tests"),
                1,
            );
            target.target_name = format!("[test-quality] Repeat run {}", index + 2);
            target.status = "WARN".into();
            target.message = if timed_out {
                "pytest repeat run timed out"
            } else {
                "pytest repeat run did not produce bounded per-test evidence"
            }
            .into();
            target.metrics.insert("run".into(), json!(index + 2));
            target.metrics.insert(
                "returncode".into(),
                json!(outcome.and_then(|outcome| outcome.exit_code)),
            );
            target.metrics.insert(
                "timeout".into(),
                json!(plan.context.options.quality_timeout_seconds),
            );
            output.targets.push(target);
        }
    }
    let nodes = valid_runs
        .iter()
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
        for line in String::from_utf8_lossy(&primary.stdout).lines() {
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
        rows.sort_by(|a, b| b.1.0.total_cmp(&a.1.0).then_with(|| a.0.cmp(&b.0)));
        let observed = rows.len();
        rows.truncate(plan.context.options.max_slow_tests);
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
    let mut target = InspectionTarget::at(
        plan.context
            .targets
            .first()
            .filter(|path| *path != ".")
            .map(String::as_str)
            .unwrap_or("tests"),
        1,
    );
    target.target_name = "[test-quality] Mutation capability".into();
    target.status = if available { "PASS" } else { "SKIP" }.into();
    target.message = if available {
        "explicit mutation capability probe completed; mutations were not performed"
    } else {
        "mutation capability is unavailable; base test gate is unchanged"
    }
    .into();
    target.metrics.insert("available".into(), json!(available));
    target.metrics.insert(
        "returncode".into(),
        json!(probe.and_then(|outcome| outcome.exit_code)),
    );
    output.targets.push(target);
    output.extra["mutation"] = json!({"mutation_status":if available{"available"}else{"unavailable"},"mutation_probes":1,"mutation_available":u8::from(available),"mutation_unavailable":u8::from(!available),"tool":plan.context.options.mutation_tool.as_ref().map(|tool|&tool.id),"argv":plan.tasks.iter().find(|task|task.id==id).map(|task|&task.argv)});
}
