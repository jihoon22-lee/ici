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
/// Frozen legacy `TestOutputMixin._parse_pytest_result` (engines/test_output.py:64-109) over one
/// finished pytest run: `_parse_pytest_stdout`'s counts (every verdict line, else the last
/// `N <label>` of each summary kind, test_output.py:203-234), the `collected N items` total, the
/// skipped cases and the tool error frozen records.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct FrozenPytestResult {
    pub passed: u64,
    pub total: u64,
    pub skipped: u64,
    pub has_failure: bool,
    /// The summary-count targets frozen adds when no verdict line was printed: `(target name,
    /// status, message, test_cases)` in frozen order (Passed, XFail, Skipped, Failed, XPass).
    pub summary_targets: Vec<(String, &'static str, &'static str, u64)>,
    pub tool_error: Option<String>,
    exit: i32,
}
impl FrozenPytestResult {
    /// Frozen read the run without a tool error: a nonzero total and exit 0 or 1.
    pub fn accepted(&self) -> bool {
        self.tool_error.is_none() && self.total > 0 && matches!(self.exit, 0 | 1)
    }
    /// Frozen `_run_coverage_tests` (engines/test_coverage.py:63-74) generates the coverage
    /// JSON only without a tool error, with a nonzero total, exit 0 or 1 and not every case
    /// skipped (`skipped >= total > 0` is "Python tests were collected but not executed").
    pub fn coverage_ready(&self) -> bool {
        self.accepted() && self.skipped < self.total
    }
}
pub fn frozen_pytest_result(
    stdout: &str,
    stderr: &str,
    exit: i32,
    evidence: &TestEvidence,
) -> FrozenPytestResult {
    // `_parse_pytest_stdout(result.stdout + ("\n" + result.stderr if result.stderr else ""))`;
    // the `collected` and evidence searches read `result.stdout + "\n" + result.stderr`.
    let out = if stderr.is_empty() {
        stdout.to_owned()
    } else {
        format!("{stdout}\n{stderr}")
    };
    let output = format!("{stdout}\n{stderr}");
    let failing = |verdict: &str| matches!(verdict, "FAILED" | "ERROR" | "XPASS");
    let (mut passed, mut total, mut skipped) = evidence.legacy_counts();
    let mut has_failure = evidence
        .legacy_lines
        .iter()
        .any(|(_, verdict)| failing(verdict));
    let mut summary_targets = vec![];
    if total == 0 {
        // `_parse_pytest_stdout`'s summary-count fallback (the last `N <label>` of each kind).
        let last = |label: &str| {
            Regex::new(&format!(r"\b(\d+)\s+{label}\b"))
                .expect("constant regex")
                .captures_iter(&out)
                .last()
                .and_then(|capture| capture[1].parse::<u64>().ok())
                .unwrap_or(0)
        };
        let ordinary = last("passed");
        let failed = last("failed") + last("errors?");
        let xfailed = last("xfailed");
        let xpassed = last("xpassed");
        skipped = last("skipped");
        passed = ordinary + xfailed;
        total = passed + failed + skipped + xpassed;
        has_failure = failed > 0 || xpassed > 0;
        for (label, count, status, message) in [
            ("Passed", ordinary, "PASS", "Pytest summary: passed"),
            ("XFail", xfailed, "PASS", "Pytest summary: expected failure"),
            ("Skipped", skipped, "SKIP", "Pytest summary: not executed"),
            ("Failed", failed, "FAIL", "Pytest summary: failed"),
            ("XPass", xpassed, "FAIL", "Pytest summary: unexpected pass"),
        ] {
            if count > 0 {
                summary_targets.push((
                    format!("[Python] {label} ({count})"),
                    status,
                    message,
                    count,
                ));
            }
        }
    }
    if total == 0
        && let Some(capture) = Regex::new(r"\bcollected\s+(\d+)\s+items?\b")
            .expect("constant regex")
            .captures(&output)
    {
        total = capture[1].parse().unwrap_or(0);
    }
    // coverage_support.py:70-76 `pytest_result_has_evidence`.
    let evidence_shown = Regex::new(r"::[^\r\n]*\s+(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
        .expect("constant regex")
        .is_match(&output)
        || Regex::new(r"\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b")
            .expect("constant regex")
            .is_match(&output);
    let tool_error = if exit == 5 || (total == 0 && exit == 0) {
        has_failure = true;
        None
    } else if exit == 0 && !evidence_shown && !(total > 0 && skipped >= total) {
        Some("Pytest returned success without parseable test results".into())
    } else if !matches!(exit, 0 | 1) {
        Some(format!("Pytest failed with exit code {exit}"))
    } else if exit == 1 && !has_failure {
        Some("Pytest returned failure without parseable diagnostics".into())
    } else {
        if total == 0 {
            has_failure = true;
        }
        None
    };
    FrozenPytestResult {
        passed,
        total,
        skipped,
        has_failure,
        summary_targets,
        tool_error,
        exit,
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
    frozen_pytest_result(stdout, stderr, exit_code?, evidence).tool_error
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
    if let Some(reason) = &plan.context.options.legacy_zero_tests {
        crate::python_quality::observe_zero_tests(plan, outcomes, reason, output);
        return Ok(());
    }
    let result = observe_base_suite(plan, outcomes, output);
    // Frozen legacy `_run_deep_test_quality` after a base run without outcome evidence.
    if result.is_err()
        && plan.context.options.legacy_quality_reruns
        && (plan.context.options.repeat_runs > 1 || plan.context.options.mutation_enabled)
        && output.extra.get("test_quality").is_none()
    {
        crate::python_quality::observe_without_base(plan, outcomes, output);
    }
    result
}
/// Frozen's tool error for a legacy Python process that gave no usable result, in its check
/// order: timed out, truncated, could not start (`returncode == -1`), killed by a signal.
pub(crate) struct LegacyProcessWords {
    pub timed_out: &'static str,
    pub truncated: &'static str,
    pub unavailable: Option<&'static str>,
    pub terminated: Option<&'static str>,
}
pub(crate) const LEGACY_PYTEST_WORDS: LegacyProcessWords = LegacyProcessWords {
    timed_out: "Pytest timed out",
    truncated: "Pytest output was truncated",
    unavailable: Some("Pytest executable was unavailable"),
    terminated: Some("Pytest process terminated before reporting results"),
};
/// `_run_coverage_tests` reads every negative return code, -1 included, as terminated.
pub(crate) const LEGACY_COVERAGE_RUN_WORDS: LegacyProcessWords = LegacyProcessWords {
    timed_out: "Coverage test run timed out",
    truncated: "Coverage test output was truncated",
    unavailable: Some("Coverage test process terminated before reporting results"),
    terminated: Some("Coverage test process terminated before reporting results"),
};
pub(crate) fn legacy_process_problem(
    outcome: &TaskOutcome,
    words: &LegacyProcessWords,
) -> Option<String> {
    if outcome.cancelled {
        return None;
    }
    let word = if outcome.timed_out {
        Some(words.timed_out)
    } else if outcome.truncated {
        Some(words.truncated)
    } else if outcome.signal.is_some() {
        words.terminated
    } else if outcome.exit_code.is_none() {
        words.unavailable
    } else {
        None
    };
    word.map(str::to_owned)
}
fn observe_base_suite(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    if crate::python_unittest::missing_pytest(plan, outcomes)? {
        return crate::python_unittest::observe(plan, outcomes, output);
    }
    let outcome = suite(plan, outcomes)?;
    if plan.context.options.legacy_pytest_result {
        let covered = plan.tasks.iter().any(|task| {
            task.id == outcome.task_id
                && task
                    .argv
                    .windows(3)
                    .any(|words| words == ["-m", "coverage", "run"])
        });
        let words = if covered {
            &LEGACY_COVERAGE_RUN_WORDS
        } else {
            &LEGACY_PYTEST_WORDS
        };
        if let Some(problem) = legacy_process_problem(outcome, words) {
            return Err(ValidationError(problem));
        }
    }
    let text = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("pytest output is not UTF-8".into()))?;
    let evidence = parse_pytest(text, &plan.context)?;
    // Frozen legacy `_parse_pytest_result` of the finished run (legacy `ici test` only).
    let frozen =
        (plan.context.options.legacy_pytest_result && planning::completed(outcome)).then(|| {
            frozen_pytest_result(
                text,
                &String::from_utf8_lossy(&outcome.stderr),
                outcome.exit_code.unwrap_or(-1),
                &evidence,
            )
        });
    // The run frozen accepted without a per-node verdict (its summary counts or `collected N
    // items` total): the legacy view reads it like frozen instead of refusing it.
    let frozen_summary = frozen
        .as_ref()
        .filter(|frozen| evidence.cases.is_empty() && frozen.total > 0);
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
    // test_output.py:217-234: without a verdict line frozen reports the summary counts as
    // `[Python] <kind> (<n>)` targets at `tests`:1.
    let mut summary_failed = None;
    for (name, status, message, count) in frozen
        .as_ref()
        .map(|frozen| frozen.summary_targets.as_slice())
        .unwrap_or_default()
    {
        let mut target = InspectionTarget::at("tests", 1);
        target.target_name = name.clone();
        target.status = (*status).into();
        target.message = (*message).into();
        target.metrics.insert("test_cases".into(), json!(count));
        output.targets.push(target);
        if *status == "FAIL" && summary_failed.is_none() {
            summary_failed = Some((name.clone(), *message));
        }
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
        // Frozen legacy reads no collection error of its own: the failure is the summary
        // count's `[Python] Failed (<n>)` target, which already carries it.
        if let Some((_, message)) = &summary_failed {
            output.extra["finding_details"][&item.fingerprint] =
                json!({"legacy_path":"tests","legacy_message":message});
        }
        output.observation.findings.push(item);
    }
    // A failing summary count without a collection error: the issue is the frozen target's.
    if let Some((name, message)) = &summary_failed
        && evidence.collection_errors.is_empty()
    {
        let item = finding(
            plan,
            "pytest.summary-failed",
            &format!("pytest summary {name}"),
            "tests",
            1,
        );
        output.extra["finding_details"][&item.fingerprint] = json!({"legacy_message":message});
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
    if let Some(frozen) = frozen_summary {
        // The frozen counts of a run without a per-node verdict (`TestEngine.run` reports them
        // also beside a tool error).
        output.extra["legacy_case_counts"] =
            json!({"passed":frozen.passed,"total":frozen.total,"skipped":frozen.skipped});
    }
    let frozen_accepts = frozen_summary.is_some_and(FrozenPytestResult::accepted);
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
        && !frozen_accepts
        && outcome.exit_code == Some(0)
        && reported.is_match(&String::from_utf8_lossy(&outcome.stdout))
    {
        return Err(ValidationError(
            "pytest reported test cases without per-test verdicts; suite evidence is incomplete"
                .into(),
        ));
    }
    if let Some(frozen) = frozen_summary.filter(|_| frozen_accepts) {
        // The frozen summary-count reading: the counts above are the evidence, without the
        // native per-node metric or its no-green limitation.
        output.summary = format!("{}/{} pytest cases passed", frozen.passed, frozen.total);
        crate::python_quality::observe(plan, outcomes, &evidence, output)?;
        return Ok(());
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
/// Frozen `_generate_coverage_json`'s tool errors. It sets no deadline on this step
/// (`_coverage_process` with an environment and no timeout), so "Coverage JSON generation timed
/// out" is not reachable there; a native timeout reads the same way.
fn legacy_coverage_json_problem(outcome: &TaskOutcome) -> Option<String> {
    if outcome.cancelled {
        return None;
    }
    if outcome.timed_out {
        return Some("Coverage JSON generation timed out".into());
    }
    if outcome.truncated {
        return Some("Coverage JSON output was truncated".into());
    }
    if let Some(signal) = outcome.signal {
        return Some(format!(
            "Coverage JSON generation failed with exit code -{signal}"
        ));
    }
    match outcome.exit_code {
        None => Some("Coverage JSON executable was unavailable".into()),
        Some(0) => None,
        Some(code) => Some(format!(
            "Coverage JSON generation failed with exit code {code}"
        )),
    }
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
    // Frozen legacy `_run_coverage_tests` measures every run it generated the JSON for, also a
    // run without a per-node verdict (`FrozenPytestResult::coverage_ready`).
    let frozen = plan
        .context
        .options
        .legacy_pytest_result
        .then(|| {
            frozen_pytest_result(
                text,
                &String::from_utf8_lossy(&suite.stderr),
                suite.exit_code.unwrap_or(-1),
                &tests,
            )
        })
        .filter(|frozen| tests.cases.is_empty() && frozen.coverage_ready());
    if (tests.collection_error || tests.cases.is_empty()) && frozen.is_none() {
        return Err(ValidationError(
            "coverage lacks a completed test scope".into(),
        ));
    }
    let report_task = format!("{}.report", plan.context.task_id);
    let report_outcome = outcomes
        .get(&report_task)
        .ok_or_else(|| ValidationError("coverage report step absent".into()))?;
    if plan.context.options.legacy_pytest_result
        && let Some(problem) = legacy_coverage_json_problem(report_outcome)
    {
        return Err(ValidationError(problem));
    }
    if !planning::completed(report_outcome) || report_outcome.exit_code != Some(0) {
        // A next run also says what coverage itself reported when it is the one fact ici
        // recognizes; no other tool text is copied.
        let mut message = "coverage JSON command did not complete successfully".to_owned();
        if !plan.context.options.legacy_pytest_result
            && [&report_outcome.stdout, &report_outcome.stderr]
                .iter()
                .any(|stream| String::from_utf8_lossy(stream).contains("No data to report."))
        {
            message.push_str(": coverage reported \"No data to report.\"");
        }
        return Err(ValidationError(message));
    }
    // Frozen reads whatever the finished step left: no report is the same refusal as a bad one.
    if plan.context.options.legacy_pytest_result && !artifacts.files.contains_key("coverage-json") {
        return Err(ValidationError(
            crate::python_coverage::LEGACY_COVERAGE_MALFORMED.into(),
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
    // Frozen legacy `_generate_coverage_json`: any report `parse_coverage_json` refuses is this
    // one coverage error (test_coverage.py:131-133).
    let report = coverage::parse_python(&artifact.bytes, &plan.context).map_err(|error| {
        if plan.context.options.legacy_pytest_result {
            ValidationError(crate::python_coverage::LEGACY_COVERAGE_MALFORMED.into())
        } else {
            error
        }
    })?;
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
    // Frozen TEM reads the legacy case counts (every verdict line; PASSED and XFAIL pass), or
    // frozen's summary counts of a run without a verdict line.
    let (passed, total) = frozen.as_ref().map_or_else(
        || {
            let (passed, total, _) = tests.legacy_counts();
            (passed, total)
        },
        |frozen| (frozen.passed, frozen.total),
    );
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
    // Frozen legacy TEM reads `_measure_coverage`'s function coverage (vacuous 100.0 without a
    // function row under `legacy_coverage_inputs`).
    let legacy_tem = tem::calculate(&tem::legacy_inputs(&tem::TemInputs {
        function_coverage: tem::coverage_inputs(
            &report.counts,
            plan.context.options.legacy_coverage_inputs,
        )
        .0,
        ..inputs.clone()
    }));
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
    let suite_failed = frozen.as_ref().map_or_else(
        || {
            tests
                .cases
                .values()
                .any(|case| matches!(case.verdict.as_str(), "FAILED" | "ERROR" | "XPASS"))
        },
        |frozen| frozen.has_failure,
    );
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
    if let Some(label) = &plan.context.options.legacy_interpreter_label {
        output.extra["legacy_interpreter_label"] = label.as_str().into();
    }
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
        // A legacy run compiles whole source directories (frozen `_compile_targets`), so a
        // diagnostic may name a file under one of them that was not selected.
        let compiled = plan.context.source_files.contains(&path)
            || plan
                .context
                .options
                .legacy_compile_targets
                .iter()
                .any(|target| {
                    target == "." || path == *target || path.starts_with(&format!("{target}/"))
                });
        if !compiled {
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
mod legacy_timeout_words {
    use super::*;

    /// Frozen's coverage run timeout wording, measured with a real 1200 s run; see
    /// `tests/unittest_fallback.rs` for the bound.
    #[test]
    fn a_timed_out_coverage_run_reads_like_frozen() {
        let mut outcome = TaskOutcome::empty("suite");
        outcome.timed_out = true;
        assert_eq!(
            legacy_process_problem(&outcome, &LEGACY_COVERAGE_RUN_WORDS).as_deref(),
            Some("Coverage test run timed out")
        );
        assert_eq!(
            legacy_process_problem(&outcome, &LEGACY_PYTEST_WORDS).as_deref(),
            Some("Pytest timed out")
        );
    }
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
