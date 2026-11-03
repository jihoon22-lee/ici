use crate::{
    CapturedArtifacts, ValidationError, ValidationKind, ValidationPlan, ValidationRequest,
    coverage, planning, tem,
};
use ici_domain::{
    AnalysisOutput, Finding, InspectionTarget, Measurement, SourceSpan, TaskState, sha256,
};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub struct TestCase {
    pub node: String,
    pub verdict: String,
    pub path: String,
    pub line: u32,
}
#[derive(Clone, Debug, Default)]
pub struct TestEvidence {
    pub cases: BTreeMap<String, TestCase>,
    pub collection_errors: Vec<String>,
    /// The path exactly as pytest printed it, paired with `collection_errors` (frozen identity).
    pub collection_sources: Vec<String>,
    pub collection_error: bool,
    /// Frozen legacy `TestOutputMixin._parse_pytest_stdout` view: every verdict line in
    /// execution order (repeats kept), named by the line's first token, with its verdict.
    pub legacy_lines: Vec<(String, String)>,
}
impl TestEvidence {
    /// Frozen legacy counts (passed, total, skipped): every verdict line counts once, PASSED and
    /// XFAIL lines pass. The next `pytest.cases` metric instead counts unique nodes and PASSED.
    pub fn legacy_counts(&self) -> (u64, u64, u64) {
        let count = |accepted: &[&str]| {
            self.legacy_lines
                .iter()
                .filter(|(_, verdict)| accepted.contains(&verdict.as_str()))
                .count() as u64
        };
        (
            count(&["PASSED", "XFAIL"]),
            self.legacy_lines.len() as u64,
            count(&["SKIPPED"]),
        )
    }
}
/// Frozen legacy `TestOutputMixin._parse_pytest_result` tool errors (the stable engine's ERROR
/// summary) for one finished pytest run, from the same stdout+stderr; `None` where frozen
/// recorded none (a green/failed suite, or the "No tests collected" verdict).
pub fn frozen_legacy_tool_error(
    stdout: &str,
    stderr: &str,
    exit_code: Option<i32>,
    evidence: &TestEvidence,
) -> Option<String> {
    let exit = exit_code?;
    let output = format!("{stdout}\n{stderr}");
    let failing = |verdict: &str| matches!(verdict, "FAILED" | "ERROR" | "XPASS");
    let mut total = evidence.legacy_lines.len() as u64;
    let mut skipped = evidence.legacy_counts().2;
    let mut has_failure = evidence
        .legacy_lines
        .iter()
        .any(|(_, verdict)| failing(verdict));
    if total == 0 {
        // `_parse_pytest_stdout`'s summary-count fallback (the last `N <label>` of each kind).
        let last = |label: &str| {
            Regex::new(&format!(r"\b(\d+)\s+{label}\b"))
                .expect("constant regex")
                .captures_iter(&output)
                .last()
                .and_then(|capture| capture[1].parse::<u64>().ok())
                .unwrap_or(0)
        };
        let failed = last("failed") + last("errors?");
        let xpassed = last("xpassed");
        skipped = last("skipped");
        total = last("passed") + last("xfailed") + failed + skipped + xpassed;
        has_failure = failed > 0 || xpassed > 0;
    }
    if total == 0
        && let Some(capture) = Regex::new(r"\bcollected\s+(\d+)\s+items?\b")
            .expect("constant regex")
            .captures(&output)
    {
        total = capture[1].parse().unwrap_or(0);
    }
    let evidence_shown = Regex::new(r"::[^\r\n]*\s+(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
        .expect("constant regex")
        .is_match(&output)
        || Regex::new(r"\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b")
            .expect("constant regex")
            .is_match(&output);
    if exit == 5 || (total == 0 && exit == 0) {
        None
    } else if exit == 0 && !evidence_shown && !(total > 0 && skipped >= total) {
        Some("Pytest returned success without parseable test results".into())
    } else if !matches!(exit, 0 | 1) {
        Some(format!("Pytest failed with exit code {exit}"))
    } else if exit == 1 && !has_failure {
        Some("Pytest returned failure without parseable diagnostics".into())
    } else {
        None
    }
}
const VERDICTS: [&str; 6] = ["PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"];
/// Frozen `_parse_pytest_stdout` over the raw (unstripped) output: lines with `::`, the first
/// whitespace-separated token after the name that is a verdict.
fn legacy_lines(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter(|line| line.contains("::"))
        .filter_map(|line| {
            let mut parts = line.split_whitespace();
            let name = parts.next()?;
            let verdict = parts.find(|part| VERDICTS.contains(part))?;
            Some((name.to_owned(), verdict.to_owned()))
        })
        .collect()
}

pub fn parse_pytest(
    text: &str,
    request: &ValidationRequest,
) -> Result<TestEvidence, ValidationError> {
    let raw = text;
    let ansi = Regex::new(r"\x1b\[[0-?]*[ -/]*[@-~]").expect("constant regex");
    let text = ansi.replace_all(text, "");
    let verdict =
        Regex::new(r"\b(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b").expect("constant regex");
    let collection = Regex::new(r"^[=\s_]*(?:ERROR|FAILED)\s+(?:collecting\s+)(\S+\.py)")
        .expect("constant regex");
    let mut result = TestEvidence::default();
    for line in text.lines() {
        if let Some(capture) = collection.captures(line) {
            // Frozen `_relative`: an absolute path inside the workspace is made relative; one
            // outside it names no finding (the banner still marks the collection failed).
            let path = match planning::relative(&capture[1], &request.root, &request.cwd) {
                Ok(path) => Some(path),
                Err(_) if std::path::Path::new(&capture[1]).is_absolute() => None,
                Err(error) => return Err(error),
            };
            if let Some(path) = path
                && !result.collection_errors.contains(&path)
            {
                result.collection_errors.push(path);
                result.collection_sources.push(capture[1].to_owned());
            }
            result.collection_error = true;
        }
        if !line.contains("::") {
            continue;
        }
        let Some(matched) = verdict.find_iter(line).last() else {
            continue;
        };
        let node = line[..matched.start()].trim();
        if node.is_empty() {
            continue;
        }
        let (path, start_line) = node_location(node, request)?;
        result.cases.insert(
            node.into(),
            TestCase {
                node: node.into(),
                verdict: matched.as_str().into(),
                path,
                line: start_line,
            },
        );
        if result.cases.len() > 100000 {
            return Err(ValidationError("pytest case limit exceeded".into()));
        }
    }
    result.legacy_lines = legacy_lines(raw);
    if result.legacy_lines.len() > 100000 {
        return Err(ValidationError("pytest case limit exceeded".into()));
    }
    Ok(result)
}
pub(crate) fn node_location(
    node: &str,
    request: &ValidationRequest,
) -> Result<(String, u32), ValidationError> {
    let mut segments = node.split("::");
    let raw_path = segments.next().unwrap_or("tests");
    let explicit = raw_path.rsplit_once(':').and_then(|(path, line)| {
        line.parse::<u32>()
            .ok()
            .filter(|line| *line > 0)
            .map(|line| (path, line))
    });
    let (raw_path, mut line) = explicit.unwrap_or((raw_path, 1));
    // Frozen analysis/pytest_output.py `_node_location`: a malformed or escaping node path is
    // reduced to the stable `tests` scope instead of pointing outside the project.
    let Ok(path) = planning::relative(raw_path, &request.root, &request.cwd) else {
        return Ok(("tests".into(), 1));
    };
    if explicit.is_none() {
        let names: Vec<_> = segments
            .map(|part| part.split('[').next().unwrap_or(part))
            .collect();
        let name = names.join(".");
        if let Some(function) = request
            .functions
            .iter()
            .find(|fact| fact.path == path && fact.qualified_name == name)
        {
            line = function.start_line;
        }
    }
    Ok((path, line))
}
pub fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &CapturedArtifacts,
) -> AnalysisOutput {
    observe_for(plan, plan.kind, outcomes, artifacts)
}
pub fn observe_for(
    plan: &ValidationPlan,
    kind: ValidationKind,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &CapturedArtifacts,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&plan.context.task_id, kind.provider());
    output.observation.limitations.clone_from(&plan.limitations);
    let result = if !plan.runtime_plans.is_empty() {
        crate::runtime::observe(plan, outcomes, artifacts, &mut output)
    } else {
        match kind {
            ValidationKind::PythonTest => observe_suite(plan, outcomes, &mut output),
            ValidationKind::PythonSanitize => {
                crate::python_sanitize::observe(plan, outcomes, &mut output)
            }
            ValidationKind::PythonCoverage => {
                observe_coverage(plan, outcomes, artifacts, &mut output)
            }
            ValidationKind::PythonRuntime => observe_runtime(plan, outcomes, &mut output),
            ValidationKind::PythonImports => observe_imports(plan, outcomes, &mut output),
            ValidationKind::Ctest => crate::cpp::observe_ctest(plan, outcomes, &mut output),
            ValidationKind::Qtest => crate::cpp::observe_qtest(plan, outcomes, &mut output),
            ValidationKind::MakeCheck => {
                crate::cpp::observe_make_check(plan, outcomes, &mut output)
            }
            ValidationKind::CppExecutable => {
                crate::standalone::observe(plan, outcomes, &mut output)
            }
            ValidationKind::BinaryCompatibility => {
                crate::cpp::observe_elf(plan, outcomes, &mut output)
            }
            ValidationKind::Sanitizer | ValidationKind::ThreadSanitizer => {
                crate::sanitizer::observe(plan, outcomes, &mut output)
            }
            ValidationKind::Integration => {
                crate::integration::observe(plan, outcomes, artifacts, &mut output)
            }
            ValidationKind::Gcov => crate::gcov::observe(plan, outcomes, artifacts, &mut output),
        }
    };
    let mut ids = plan
        .tasks
        .iter()
        .map(|task| task.id.clone())
        .collect::<std::collections::BTreeSet<_>>();
    ids.extend(
        plan.prerequisites
            .values()
            .flatten()
            .map(|item| item.task_id.clone()),
    );
    ids.extend(crate::python_coverage::probe_id(plan));
    if let Ok(id) = crate::python_coverage::actual_suite_id(plan, outcomes) {
        ids.insert(id);
    }
    let relevant: Vec<_> = ids.iter().filter_map(|id| outcomes.get(id)).collect();
    let required: Vec<_> = relevant
        .iter()
        .copied()
        .filter(|outcome| {
            !plan.optional_task_ids.contains(&outcome.task_id)
                && !crate::python_unittest::inactive_pytest(plan, &outcome.task_id, outcomes)
        })
        .collect();
    output.observation.duration_seconds = Some(
        relevant
            .iter()
            .map(|outcome| outcome.duration_seconds)
            .sum(),
    );
    output.observation.timed_out = required.iter().any(|outcome| outcome.timed_out);
    output.observation.truncated = required.iter().any(|outcome| outcome.truncated);
    output.observation.exit_code = required.last().and_then(|outcome| outcome.exit_code);
    output.observation.signal = required
        .iter()
        .find_map(|outcome| outcome.signal.map(|signal| signal.to_string()));
    if let Err(error) = result {
        output.observation.state = if relevant.iter().any(|outcome| outcome.cancelled) {
            TaskState::Cancelled
        } else {
            TaskState::Failed
        };
        // The failure is the check's first limitation: the gate reason names it, not the
        // run-wide note that dynamic evidence is never reused (frozen first limitation).
        output.observation.limitations.insert(0, error.to_string());
        output.status = "INCOMPLETE".into();
        output.summary = error.to_string();
    } else if !output.observation.findings.is_empty() {
        output.status = if output
            .observation
            .findings
            .iter()
            .all(|finding| finding.tags.iter().any(|tag| tag == "advisory"))
        {
            "WARN"
        } else {
            "FAIL"
        }
        .into();
    }
    let provenance = plan
        .tasks
        .iter()
        .map(|task| {
            let outcome = outcomes.get(&task.id);
            let mut step = json!({"task_id":task.id,"argv":task.argv,"cwd":task.cwd,"state":outcome.map(|o|o.state),"exit_code":outcome.and_then(|o|o.exit_code)});
            // Bounded-process problems stay distinguishable from an exit status (frozen
            // `_process_problem`: timed out, output exceeded the bound, exit code N).
            for (flag, set) in [
                ("timed_out", outcome.is_some_and(|o| o.timed_out)),
                ("truncated", outcome.is_some_and(|o| o.truncated)),
            ] {
                if set {
                    step[flag] = json!(true);
                }
            }
            step
        })
        .collect::<Vec<_>>();
    output.extra["steps"] = json!(provenance);
    output.extra["conditional_steps"] = json!(plan.conditional_steps);
    crate::python_coverage::annotate(plan, outcomes, &mut output);
    output.extra["optional_steps"]=json!(plan.optional_task_ids.iter().map(|id|json!({"task_id":id,"state":outcomes.get(id).map(|outcome|outcome.state),"exit_code":outcomes.get(id).and_then(|outcome|outcome.exit_code),"timed_out":outcomes.get(id).is_some_and(|outcome|outcome.timed_out),"truncated":outcomes.get(id).is_some_and(|outcome|outcome.truncated),"cancelled":outcomes.get(id).is_some_and(|outcome|outcome.cancelled)})).collect::<Vec<_>>());
    crate::test_metrics::attach(plan, outcomes, &mut output);
    output
}
fn suite<'a>(
    plan: &ValidationPlan,
    outcomes: &'a BTreeMap<String, TaskOutcome>,
) -> Result<&'a TaskOutcome, ValidationError> {
    let id = crate::python_coverage::actual_suite_id(plan, outcomes)?;
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError(format!("suite step {id} is absent")))?;
    if outcome.task_id != id {
        return Err(ValidationError(
            "pytest suite outcome identity mismatch".into(),
        ));
    }
    Ok(outcome)
}
fn observe_suite(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    if crate::python_unittest::missing_pytest(plan, outcomes)? {
        return crate::python_unittest::observe(plan, outcomes, output);
    }
    let outcome = suite(plan, outcomes)?;
    let text = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("pytest output is not UTF-8".into()))?;
    let evidence = parse_pytest(text, &plan.context)?;
    // Root decision E3: the legacy view keeps frozen `_parse_pytest_stdout`'s targets — every
    // verdict line in execution order, repeats kept — while findings and the next metric stay
    // per unique node (sorted, as frozen `parse_pytest_outcomes`).
    for (name, verdict) in &evidence.legacy_lines {
        let (path, line) = node_location(name, &plan.context)?;
        let mut target = InspectionTarget::at(&path, line);
        target.target_name = name.clone();
        target.status = match verdict.as_str() {
            "PASSED" | "XFAIL" => "PASS",
            "SKIPPED" => "SKIP",
            _ => "FAIL",
        }
        .into();
        target.message = case_message(verdict).into();
        output.targets.push(target);
    }
    for case in evidence.cases.values() {
        let message = case_message(&case.verdict);
        if matches!(case.verdict.as_str(), "FAILED" | "ERROR" | "XPASS") {
            let mut finding = finding(
                plan,
                &format!("pytest.{}", case.verdict.to_lowercase()),
                &format!("{} {}", case.node, case.verdict.to_lowercase()),
                &case.path,
                case.line,
            );
            finding.fingerprint = sha256(format!("pytest\0{}\0{}", case.node, case.verdict));
            // The historical view keeps one issue per case: the verdict target already carries it.
            output.extra["finding_details"][&finding.fingerprint] =
                json!({"legacy_message":message});
            output.observation.findings.push(finding);
        }
    }
    for (path, source) in evidence
        .collection_errors
        .iter()
        .zip(&evidence.collection_sources)
    {
        let mut item = finding(
            plan,
            "pytest.collection-error",
            &format!("collection error in {path}"),
            path,
            1,
        );
        // Frozen provider identity: sha256("pytest\0collect:<printed path>\0error").
        item.fingerprint = sha256(format!("pytest\0collect:{source}\0error"));
        output.observation.findings.push(item);
    }
    // Root decision E5: the legacy view's ERROR summary is frozen's own tool-error text; the
    // modern check keeps its native refusal wording.
    if planning::completed(outcome)
        && let Some(error) = frozen_legacy_tool_error(
            text,
            &String::from_utf8_lossy(&outcome.stderr),
            outcome.exit_code,
            &evidence,
        )
    {
        output.extra["legacy_tool_error"] = json!(error);
    }
    let total = evidence.cases.len() as u64;
    let passed = evidence
        .cases
        .values()
        .filter(|case| matches!(case.verdict.as_str(), "PASSED" | "XFAIL"))
        .count() as u64;
    if total > 0 {
        // Frozen next provider: the numerator counts PASSED nodes only (an xfail is no pass).
        let green = evidence
            .cases
            .values()
            .filter(|case| case.verdict == "PASSED")
            .count() as u64;
        output
            .observation
            .measurements
            .push(pair("pytest.cases", green, total));
        let (legacy_passed, legacy_total, legacy_skipped) = evidence.legacy_counts();
        output.extra["legacy_case_counts"] =
            json!({"passed":legacy_passed,"total":legacy_total,"skipped":legacy_skipped});
    }
    output.extra["test_outcomes"] = json!(
        evidence
            .cases
            .iter()
            .map(|(node, case)| (node, &case.verdict))
            .collect::<BTreeMap<_, _>>()
    );
    output.summary = format!("{passed}/{total} pytest cases passed");
    if !planning::completed(outcome) {
        return Err(ValidationError(
            "pytest suite did not complete; retained case evidence covers only observed scope"
                .into(),
        ));
    }
    if evidence.collection_error {
        return Err(ValidationError(
            "pytest collection failed; suite scope is incomplete".into(),
        ));
    }
    // pytest ran cases but printed no per-node verdict (e.g. a quiet `-q`/`verbose=-1` run): the
    // frozen summary-count fallback is not per-test evidence, so the suite stays incomplete and is
    // never reported as "No tests collected".
    let reported = Regex::new(
        r"\b(?:collected\s+([1-9]\d*)\s+items?|([1-9]\d*)\s+(?:passed|failed|errors?|skipped|xfailed|xpassed))\b",
    )
    .expect("constant regex");
    if total == 0
        && outcome.exit_code == Some(0)
        && reported.is_match(&String::from_utf8_lossy(&outcome.stdout))
    {
        return Err(ValidationError(
            "pytest reported test cases without per-test verdicts; suite evidence is incomplete"
                .into(),
        ));
    }
    if total == 0 && matches!(outcome.exit_code, Some(0 | 5)) {
        // Frozen `_parse_pytest_result`: the located descriptor-level verdict for an empty suite.
        let mut target = InspectionTarget::at("tests", 1);
        target.target_name = "[Python] Tests".into();
        target.status = "FAIL".into();
        target.message = "No tests collected".into();
        output.targets.push(target);
    }
    if total == 0 || !matches!(outcome.exit_code, Some(0 | 1)) {
        // The exit code as a number (`none` when a signal ended the run), never Rust Debug text.
        return Err(ValidationError(format!(
            "pytest did not report a completed nonempty suite (exit {})",
            outcome
                .exit_code
                .map_or_else(|| "none".to_owned(), |code| code.to_string())
        )));
    }
    // Frozen `adapters/providers/pytest.py` `_parse`: a collected suite in which no case passed
    // (every case skipped, xfailed or errored) is still an answer, but nothing was verified green,
    // so the run says so instead of presenting the check as verified evidence (root decision D2).
    let green = evidence
        .cases
        .values()
        .filter(|case| case.verdict == "PASSED")
        .count();
    if green == 0 {
        let skipped = evidence
            .cases
            .values()
            .filter(|case| case.verdict == "SKIPPED")
            .count();
        output.observation.limitations.push(format!(
            "no test ran green ({skipped} skipped of {total} collected)"
        ));
    }
    if outcome.exit_code == Some(1) && output.observation.findings.is_empty() {
        return Err(ValidationError(
            "pytest exit1 carried no attributable failure".into(),
        ));
    }
    crate::python_quality::observe(plan, outcomes, &evidence, output)?;
    Ok(())
}
fn observe_coverage(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &CapturedArtifacts,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    if crate::python_unittest::missing_pytest(plan, outcomes)? {
        let mut tests = AnalysisOutput::new(&plan.context.task_id, "unittest");
        crate::python_unittest::observe(plan, outcomes, &mut tests)?;
        output.extra["unittest_fallback"] = json!({"summary":tests.summary,"test_outcomes":tests.extra["test_outcomes"],"test_framework":tests.extra["test_framework"]});
        return crate::python_coverage::unavailable_for(
            plan,
            output,
            "pytest module is unavailable; fallback unittest does not collect coverage",
        );
    }
    match crate::python_coverage::capability(plan, outcomes)? {
        Some(crate::python_coverage::Capability::Missing) => {
            return crate::python_coverage::unavailable(plan, output);
        }
        Some(crate::python_coverage::Capability::Failed(reason)) => {
            return Err(ValidationError(reason));
        }
        _ => {}
    }
    let suite = suite(plan, outcomes)?;
    if !planning::completed(suite) || !matches!(suite.exit_code, Some(0 | 1)) {
        return Err(ValidationError(
            "coverage suite exited outside test contract".into(),
        ));
    }
    let text = std::str::from_utf8(&suite.stdout)
        .map_err(|_| ValidationError("pytest output is not UTF-8".into()))?;
    let tests = parse_pytest(text, &plan.context)?;
    if tests.collection_error || tests.cases.is_empty() {
        return Err(ValidationError(
            "coverage lacks a completed test scope".into(),
        ));
    }
    let report_task = format!("{}.report", plan.context.task_id);
    let report_outcome = outcomes
        .get(&report_task)
        .ok_or_else(|| ValidationError("coverage report step absent".into()))?;
    if !planning::completed(report_outcome) || report_outcome.exit_code != Some(0) {
        return Err(ValidationError(
            "coverage JSON command did not complete successfully".into(),
        ));
    }
    let artifact = artifacts.files.get("coverage-json").ok_or_else(|| {
        ValidationError(format!(
            "fresh coverage artifact absent: {:?}",
            artifacts.errors.get("coverage-json")
        ))
    })?;
    if artifact.producer_task_id != report_task || artifact.digest != sha256(&artifact.bytes) {
        return Err(ValidationError(
            "coverage artifact provenance/digest mismatch".into(),
        ));
    }
    let report = coverage::parse_python(&artifact.bytes, &plan.context)?;
    for (name, a, b) in [
        (
            "coverage.lines",
            Some(report.counts.covered_lines),
            Some(report.counts.total_lines),
        ),
        (
            "coverage.branches",
            Some(report.counts.covered_branches),
            Some(report.counts.total_branches),
        ),
        (
            "coverage.functions",
            report.counts.covered_functions,
            report.counts.total_functions,
        ),
    ] {
        if let Some((covered, total)) = a.zip(b).filter(|(_, total)| *total > 0) {
            output
                .observation
                .measurements
                .push(coverage_percent(name, covered, total));
        }
    }
    // With a file gate the policy owns the per-file inventory (frozen `Coverage:File`).
    for file in report
        .files
        .values()
        .filter(|_| plan.context.options.min_file_cov.is_none())
    {
        let mut target = InspectionTarget::at(&file.path, 1);
        target.target_name = format!("Coverage: {}", file.path);
        target
            .metrics
            .insert("covered_lines".into(), json!(file.counts.covered_lines));
        target
            .metrics
            .insert("total_lines".into(), json!(file.counts.total_lines));
        output.targets.push(target);
    }
    // Frozen TEM reads the legacy case counts (every verdict line; PASSED and XFAIL pass).
    let (passed, total, _) = tests.legacy_counts();
    let inputs = tem::TemInputs {
        passed,
        total,
        line_coverage: tem::percentage(report.counts.covered_lines, report.counts.total_lines),
        branch_coverage: tem::percentage(
            report.counts.covered_branches,
            report.counts.total_branches,
        ),
        function_coverage: report
            .counts
            .covered_functions
            .zip(report.counts.total_functions)
            .and_then(|(a, b)| tem::percentage(a, b)),
        estimated_coverage: None,
    };
    let score = tem::calculate(&inputs);
    let legacy_tem = tem::calculate(&tem::legacy_inputs(&inputs));
    output.extra["legacy_tem"] = json!(legacy_tem);
    output.score = score.score;
    output.max_score = Some(5.0);
    output.extra["tem"] = json!(score);
    output.extra["coverage"] = json!(report);
    crate::coverage_baseline::attach(plan, &report, output)?;
    output
        .observation
        .limitations
        .extend(report.limitations.iter().cloned());
    output.summary = format!(
        "coverage: {}/{} lines, {}/{} branches",
        report.counts.covered_lines,
        report.counts.total_lines,
        report.counts.covered_branches,
        report.counts.total_branches
    );
    crate::test_metrics::coverage(plan, &report, output);
    let suite_failed = tests
        .cases
        .values()
        .any(|case| matches!(case.verdict.as_str(), "FAILED" | "ERROR" | "XPASS"));
    crate::policy::evaluate_suite(plan, &report, legacy_tem.score, suite_failed, output)
}
/// Frozen `TestOutputMixin._parse_pytest_stdout` case messages.
pub(crate) fn case_message(verdict: &str) -> &'static str {
    match verdict {
        "PASSED" => "Test passed successfully",
        "XFAIL" => "Expected failure was exercised",
        "SKIPPED" => "Test was collected but not executed",
        "XPASS" => "Unexpected pass violated the expected-failure contract",
        _ => "Test assertion failed",
    }
}
fn observe_runtime(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let mut errors = vec![];
    if let Err(error) = observe_runtime_metadata(plan, outcomes, output) {
        errors.push(error.to_string());
    }
    if plan.context.options.compile_sources
        && let Err(error) = observe_compilation(plan, outcomes, output)
    {
        errors.push(error.to_string());
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(ValidationError(errors.join("; ")))
    }
}
fn observe_runtime_metadata(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let id = format!("{}.metadata", plan.context.task_id);
    let metadata = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("runtime metadata step absent".into()))?;
    if !planning::completed(metadata) || metadata.exit_code != Some(0) {
        return Err(ValidationError(
            "project interpreter metadata failed".into(),
        ));
    }
    let value: Value = serde_json::from_slice(&metadata.stdout)
        .map_err(|e| ValidationError(format!("runtime metadata: {e}")))?;
    let version = value["version"]
        .as_array()
        .filter(|v| v.len() == 3 && v.iter().all(|n| n.as_u64().is_some()))
        .ok_or_else(|| ValidationError("runtime version triple missing".into()))?;
    for key in ["implementation", "platform", "machine"] {
        if !value[key].is_string() {
            return Err(ValidationError(format!("runtime metadata {key} missing")));
        }
    }
    for key in ["soabi", "ext_suffix"] {
        if !value[key].is_null() && !value[key].is_string() {
            return Err(ValidationError(format!(
                "runtime ABI metadata {key} malformed"
            )));
        }
    }
    let version = version
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join(".");
    output
        .observation
        .tool_versions
        .push(("project-python".into(), version.clone()));
    // Frozen `PythonVersionProvider.parse` limitations: the measured runtime, then the floor verdict.
    output
        .observation
        .limitations
        .push(format!("measured runtime: Python {version}"));
    if let Some(requirement) = &plan.context.options.requires_python {
        if ici_analysis::python_version_allowed(requirement, &version).map_err(ValidationError)? {
            output.observation.limitations.push(format!(
                "runtime satisfies requires-python {}",
                crate::integration::repr(requirement)
            ));
        } else {
            output.observation.findings.push(finding(
                plan,
                "python.compat.runtime-version",
                &format!(
                    "declared interpreter reports Python {version}, outside requires-python {}",
                    crate::integration::repr(requirement)
                ),
                "pyproject.toml",
                1,
            ));
        }
    } else {
        output
            .observation
            .limitations
            .push("no requires-python floor is declared — the measured runtime was not judged against a floor".into());
    }
    output.extra["runtime"] = value;
    let mut target = InspectionTarget::at("pyproject.toml", 1);
    target.target_name = "Project Python runtime".into();
    target.message = version.clone();
    target.status = if output.observation.findings.is_empty() {
        "PASS"
    } else {
        "FAIL"
    }
    .into();
    output.targets.push(target);
    output.summary = format!("project Python {version}");
    Ok(())
}

fn observe_compilation(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let id = format!("{}.compile", plan.context.task_id);
    let compile = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("runtime compile step absent".into()))?;
    if !planning::completed(compile) {
        return Err(ValidationError(
            "runtime compilation did not complete".into(),
        ));
    }
    if compile.exit_code == Some(0) {
        // Frozen `CompileallProvider.parse`: the compiled file count.
        let mut checked =
            Measurement::measured("compileall.checked", plan.context.source_files.len() as f64);
        checked.unit = "files".into();
        output.observation.measurements.push(checked);
        for path in &plan.context.source_files {
            let mut target = InspectionTarget::at(path, 1);
            target.target_name = "Project runtime compilation".into();
            output.targets.push(target);
        }
        return Ok(());
    }
    let stdout = std::str::from_utf8(&compile.stdout)
        .map_err(|_| ValidationError("compileall output is not UTF-8".into()))?;
    let stderr = std::str::from_utf8(&compile.stderr)
        .map_err(|_| ValidationError("compileall output is not UTF-8".into()))?;
    let text = format!("{stdout}\n{stderr}");
    let markers = Regex::new(r#"(?m)^\*\*\* Error compiling (?:'([^']+)'|"([^"]+)")"#)
        .expect("constant regex");
    let location = Regex::new(r#"File "([^"]+)", line ([1-9]\d*)"#).expect("constant regex");
    let message = Regex::new(r"(?m)^\s*(\w+Error):\s*(.+)$").expect("constant regex");
    let matches = markers.captures_iter(&text).collect::<Vec<_>>();
    let mut count = 0;
    for (index, marker) in matches.iter().enumerate() {
        let raw = marker
            .get(1)
            .or_else(|| marker.get(2))
            .expect("matched file")
            .as_str();
        let path = planning::relative(raw, &plan.context.root, &plan.context.cwd)?;
        if !plan.context.source_files.contains(&path) {
            return Err(ValidationError(
                "compileall diagnostic is outside declared source scope".into(),
            ));
        }
        let start = marker.get(0).expect("matched block").start();
        let end = matches
            .get(index + 1)
            .and_then(|next| next.get(0))
            .map_or(text.len(), |next| next.start());
        let block = &text[start..end];
        let mut row = 1;
        for entry in location.captures_iter(block) {
            if planning::relative(&entry[1], &plan.context.root, &plan.context.cwd)
                .is_ok_and(|candidate| candidate == path)
            {
                row = entry[2]
                    .parse::<u32>()
                    .map_err(|_| ValidationError("compileall line overflow".into()))?;
                break;
            }
        }
        let detail = message
            .captures(block)
            .map(|entry| format!("{}: {}", &entry[1], &entry[2]))
            .unwrap_or_else(|| "the declared interpreter could not compile this file".into());
        compile_finding(plan, output, &path, row, &detail);
        count += 1;
    }
    let sorry =
        Regex::new(r"(?m)^\*\*\* Sorry:\s*(\w+Error):\s*(.+?)\s*\((.+), line ([1-9]\d*)\)\s*$")
            .expect("constant regex");
    for entry in sorry.captures_iter(&text) {
        let raw = &entry[3];
        let direct = planning::relative(raw, &plan.context.root, &plan.context.cwd).ok();
        let mut candidates = plan
            .context
            .source_files
            .iter()
            .filter(|path| {
                direct.as_deref() == Some(path.as_str())
                    || std::path::Path::new(path).file_name()
                        == std::path::Path::new(raw).file_name()
            })
            .collect::<Vec<_>>();
        candidates.sort();
        candidates.dedup();
        if candidates.len() != 1 {
            return Err(ValidationError(
                "compileall Sorry diagnostic source is missing or ambiguous".into(),
            ));
        }
        let row = entry[4]
            .parse::<u32>()
            .map_err(|_| ValidationError("compileall source line overflow".into()))?;
        compile_finding(
            plan,
            output,
            candidates[0],
            row,
            &format!("{}: {}", &entry[1], &entry[2]),
        );
        count += 1;
    }
    if count == 0 {
        return Err(ValidationError(format!(
            "compileall exited {} but named no failed file — the failure cannot be attributed",
            compile
                .exit_code
                .map_or_else(|| "none".to_owned(), |code| code.to_string())
        )));
    }
    Ok(())
}
fn compile_finding(
    plan: &ValidationPlan,
    output: &mut AnalysisOutput,
    path: &str,
    row: u32,
    detail: &str,
) {
    output.observation.findings.push(finding(
        plan,
        "python.compat.compile-failure",
        detail,
        path,
        row,
    ));
    let mut target = InspectionTarget::at(path, row);
    target.target_name = "Project runtime compilation".into();
    target.status = "FAIL".into();
    target.message = detail.into();
    output.targets.push(target);
}

fn observe_imports(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let id = format!("{}.imports", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("import-only step absent".into()))?;
    if !planning::completed(outcome) || !matches!(outcome.exit_code, Some(0 | 1)) {
        return Err(ValidationError(
            "import-only process did not complete".into(),
        ));
    }
    // Project imports may print; the fixed probe emits its sole JSON response on the last line.
    let text = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("import-only output is not UTF-8".into()))?;
    let response = text
        .lines()
        .last()
        .ok_or_else(|| ValidationError("import-only response absent".into()))?;
    let document: Value = serde_json::from_str(response)
        .map_err(|e| ValidationError(format!("import-only JSON: {e}")))?;
    let items = document["imports"]
        .as_array()
        .filter(|v| v.len() == plan.context.options.modules.len())
        .ok_or_else(|| ValidationError("import-only response scope mismatch".into()))?;
    for (item, module) in items.iter().zip(&plan.context.options.modules) {
        if item["module"] != *module || !item["ok"].is_boolean() {
            return Err(ValidationError(
                "import-only module verdict malformed".into(),
            ));
        }
        let ok = item["ok"] == true;
        let path = plan
            .context
            .source_files
            .iter()
            .find(|path| path.ends_with(&format!("{}.py", module.replace('.', "/"))))
            .cloned()
            .unwrap_or_else(|| "pyproject.toml".into());
        let mut target = InspectionTarget::at(&path, 1);
        target.target_name = format!("Import: {module}");
        target.status = if ok { "PASS" } else { "FAIL" }.into();
        output.targets.push(target);
        if !ok {
            let error = item["error"]
                .as_str()
                .ok_or_else(|| ValidationError("import error detail missing".into()))?;
            output.observation.findings.push(finding(
                plan,
                "python.compat.import",
                &format!("import {module}: {error}"),
                &path,
                1,
            ));
        }
    }
    if (outcome.exit_code == Some(0)) != output.observation.findings.is_empty() {
        return Err(ValidationError("import response/exit disagreement".into()));
    }
    output.summary = format!("{} declared imports checked", items.len());
    output.extra["imports"] = document;
    Ok(())
}
pub(crate) fn finding(
    plan: &ValidationPlan,
    rule: &str,
    message: &str,
    path: &str,
    line: u32,
) -> Finding {
    let mut item = Finding::new(
        rule,
        message,
        SourceSpan::line(path, line),
        plan.kind.provider(),
    );
    item.task_id = Some(plan.context.task_id.clone());
    item.component_id = Some(plan.context.component_id.clone());
    item.native_rule_id = rule.into();
    if rule.starts_with("python.compat.") {
        item.category = "compatibility".into();
    }
    item.severity = "high".into();
    item.fingerprint = sha256(format!(
        "{}\0{rule}\0{path}\0{line}\0{message}",
        plan.kind.provider()
    ));
    item
}
/// Frozen adapters/providers/coverage.py reports every percentage as `round(percent, 1)`.
fn coverage_percent(name: &str, covered: u64, total: u64) -> Measurement {
    let mut metric = pair(name, covered, total);
    metric.value = ici_domain::python_round_one(metric.value);
    metric
}
pub(crate) fn pair(name: &str, numerator: u64, denominator: u64) -> Measurement {
    let mut metric = Measurement::measured(
        name,
        if name.ends_with(".cases") {
            denominator as f64
        } else {
            numerator as f64 / denominator as f64 * 100.0
        },
    );
    metric.numerator = Some(numerator);
    metric.denominator = Some(denominator);
    metric.unit = if name.ends_with(".cases") {
        "cases"
    } else {
        "%"
    }
    .into();
    metric
}
#[cfg(test)]
mod coverage_percent_tests {
    #[test]
    fn coverage_percentages_round_to_one_decimal_like_python() {
        for (covered, total, expected) in [
            (1, 3, 33.3),
            (2, 3, 66.7),
            (1, 8, 12.5),
            (1, 16, 6.2),
            (5, 5, 100.0),
        ] {
            let metric = super::coverage_percent("coverage.lines", covered, total);
            assert_eq!(metric.value, expected);
            assert_eq!(
                (metric.numerator, metric.denominator),
                (Some(covered), Some(total))
            );
        }
    }
}
