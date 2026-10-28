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
    pub collection_error: bool,
}

pub fn parse_pytest(
    text: &str,
    request: &ValidationRequest,
) -> Result<TestEvidence, ValidationError> {
    let ansi = Regex::new(r"\x1b\[[0-?]*[ -/]*[@-~]").expect("constant regex");
    let text = ansi.replace_all(text, "");
    let verdict =
        Regex::new(r"\b(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b").expect("constant regex");
    let collection = Regex::new(r"^[=\s_]*(?:ERROR|FAILED)\s+(?:collecting\s+)(\S+\.py)")
        .expect("constant regex");
    let mut result = TestEvidence::default();
    for line in text.lines() {
        if let Some(capture) = collection.captures(line) {
            let path = planning::relative(&capture[1], &request.root, &request.cwd)?;
            if !result.collection_errors.contains(&path) {
                result.collection_errors.push(path);
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
    let path = planning::relative(raw_path, &request.root, &request.cwd)?;
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
        .filter(|outcome| !plan.optional_task_ids.contains(&outcome.task_id))
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
        output.observation.limitations.push(error.to_string());
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
    let provenance=plan.tasks.iter().map(|task|json!({"task_id":task.id,"argv":task.argv,"cwd":task.cwd,"state":outcomes.get(&task.id).map(|o|o.state),"exit_code":outcomes.get(&task.id).and_then(|o|o.exit_code)})).collect::<Vec<_>>();
    output.extra["steps"] = json!(provenance);
    output.extra["conditional_steps"] = json!(plan.conditional_steps);
    crate::python_coverage::annotate(plan, outcomes, &mut output);
    output.extra["optional_steps"]=json!(plan.optional_task_ids.iter().map(|id|json!({"task_id":id,"state":outcomes.get(id).map(|outcome|outcome.state),"exit_code":outcomes.get(id).and_then(|outcome|outcome.exit_code),"timed_out":outcomes.get(id).is_some_and(|outcome|outcome.timed_out),"truncated":outcomes.get(id).is_some_and(|outcome|outcome.truncated),"cancelled":outcomes.get(id).is_some_and(|outcome|outcome.cancelled)})).collect::<Vec<_>>());
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
    let outcome = suite(plan, outcomes)?;
    let text = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("pytest output is not UTF-8".into()))?;
    let evidence = parse_pytest(text, &plan.context)?;
    for case in evidence.cases.values() {
        let mut target = InspectionTarget::at(&case.path, case.line);
        target.target_name = case.node.clone();
        target.status = match case.verdict.as_str() {
            "PASSED" => "PASS",
            "SKIPPED" | "XFAIL" => "SKIP",
            _ => "FAIL",
        }
        .into();
        target.message = case.verdict.clone();
        output.targets.push(target);
        if matches!(case.verdict.as_str(), "FAILED" | "ERROR" | "XPASS") {
            let mut finding = finding(
                plan,
                &format!("pytest.{}", case.verdict.to_lowercase()),
                &format!("{} {}", case.node, case.verdict.to_lowercase()),
                &case.path,
                case.line,
            );
            finding.fingerprint = sha256(format!("pytest\0{}\0{}", case.node, case.verdict));
            output.observation.findings.push(finding);
        }
    }
    for path in &evidence.collection_errors {
        output.observation.findings.push(finding(
            plan,
            "pytest.collection-error",
            &format!("collection error in {path}"),
            path,
            1,
        ));
    }
    let total = evidence.cases.len() as u64;
    let passed = evidence
        .cases
        .values()
        .filter(|case| case.verdict == "PASSED")
        .count() as u64;
    if total > 0 {
        output
            .observation
            .measurements
            .push(pair("pytest.cases", passed, total));
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
    if total == 0 || !matches!(outcome.exit_code, Some(0 | 1)) {
        return Err(ValidationError(format!(
            "pytest did not report a completed nonempty suite (exit {:?})",
            outcome.exit_code
        )));
    }
    if passed == 0
        && evidence
            .cases
            .values()
            .all(|case| matches!(case.verdict.as_str(), "SKIPPED" | "XFAIL"))
    {
        return Err(ValidationError(
            "all pytest cases skipped or xfailed; nothing was verified".into(),
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
    if matches!(
        crate::python_coverage::capability(plan, outcomes)?,
        Some(crate::python_coverage::Capability::Missing)
    ) {
        return crate::python_coverage::unavailable(plan, output);
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
                .push(pair(name, covered, total));
        }
    }
    for file in report.files.values() {
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
    let passed = tests
        .cases
        .values()
        .filter(|case| case.verdict == "PASSED")
        .count() as u64;
    let score = tem::calculate(&tem::TemInputs {
        passed,
        total: tests.cases.len() as u64,
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
    });
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
    crate::policy::evaluate(plan, &report, score.score, output)
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
    if let Some(requirement) = &plan.context.options.requires_python {
        if !ici_analysis::python_version_allowed(requirement, &version).map_err(ValidationError)? {
            output.observation.findings.push(finding(
                plan,
                "python.compat.runtime-version",
                &format!("project Python {version} is outside requires-python {requirement}"),
                "pyproject.toml",
                1,
            ));
        }
    } else {
        output
            .observation
            .limitations
            .push("no requires-python constraint is declared".into());
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
            .unwrap_or_else(|| "project runtime could not compile this source".into());
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
        return Err(ValidationError(
            "runtime compile failed without attributable source diagnostics".into(),
        ));
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
        "python.compat.runtime-syntax",
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
