//! The selected project's stdlib unittest runner is a visible, narrowly conditioned fallback.
use crate::{
    StepCondition, StepPrerequisite, ValidationError, ValidationKind, ValidationPlan, planning,
    python_coverage,
};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskKind, sha256};
use ici_execution::TaskOutcome;
use regex::Regex;
use serde_json::json;
use std::collections::BTreeMap;

fn enabled(plan: &ValidationPlan) -> bool {
    plan.context.options.unittest_fallback
        && matches!(
            plan.kind,
            ValidationKind::PythonTest | ValidationKind::PythonCoverage
        )
}
fn owner_id(plan: &ValidationPlan) -> String {
    python_coverage::suite_id(plan)
        .strip_suffix(".suite")
        .unwrap_or(&plan.context.task_id)
        .to_owned()
}
fn task_ids(plan: &ValidationPlan) -> Vec<String> {
    (0..plan.context.targets.len())
        .map(|index| format!("{}.unittest{index}", owner_id(plan)))
        .collect()
}
pub fn missing_pytest(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<bool, ValidationError> {
    if !enabled(plan) {
        return Ok(false);
    }
    let id = python_coverage::actual_suite_id(plan, outcomes)?;
    let missing = if let Some(outcome) = outcomes
        .get(&id)
        .filter(|outcome| planning::completed(outcome) && outcome.exit_code == Some(1))
    {
        let stdout = std::str::from_utf8(&outcome.stdout)
            .map_err(|_| ValidationError("test capability output is not UTF-8".into()))?;
        let stderr = std::str::from_utf8(&outcome.stderr)
            .map_err(|_| ValidationError("test capability error output is not UTF-8".into()))?;
        let plain = stdout.trim().is_empty()
            && stderr.trim()
                == format!(
                    "{}: No module named pytest",
                    plan.context.tool.path.display()
                );
        // Coverage.py owns this exact alternate diagnostic; its version is already proven.
        let covered = matches!(
            python_coverage::capability(plan, outcomes)?,
            Some(python_coverage::Capability::Available(_))
        ) && stdout.trim() == "No module named 'pytest'"
            && stderr.trim().is_empty();
        plain || covered
    } else {
        false
    };
    if !missing && task_ids(plan).iter().any(|id| outcomes.contains_key(id)) {
        return Err(ValidationError(
            "an inactive unittest fallback was executed".into(),
        ));
    }
    Ok(missing)
}

pub fn skip_pytest_followup(
    plan: &ValidationPlan,
    task: &str,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<bool, ValidationError> {
    Ok(enabled(plan)
        && (task == format!("{}.report", plan.context.task_id)
            || task.starts_with(&format!("{}.repeat", plan.context.task_id)))
        && missing_pytest(plan, outcomes)?)
}
pub fn configure(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    if !enabled(plan) || plan.context.options.shared_test_task_id.is_some() {
        return Ok(());
    }
    let suite = python_coverage::suite_id(plan);
    for (index, target) in plan.context.targets.iter().enumerate() {
        let absolute = plan.context.root.join(target);
        let mut args = vec![
            "-m".into(),
            "unittest".into(),
            "discover".into(),
            "-s".into(),
        ];
        if absolute
            .extension()
            .is_some_and(|extension| extension == "py")
        {
            args.push(
                absolute
                    .parent()
                    .unwrap_or(&plan.context.root)
                    .to_string_lossy()
                    .into_owned(),
            );
            args.extend([
                "-p".into(),
                absolute
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned(),
            ]);
        } else {
            args.push(absolute.to_string_lossy().into_owned());
        }
        args.push("-v".into());
        let mut task = planning::task(&plan.context, &format!("unittest{index}"), args);
        task.id = format!("{}.unittest{index}", owner_id(plan));
        task.provider = "unittest".into();
        task.kind = TaskKind::Test;
        plan.conditional_steps.insert(
            task.id.clone(),
            StepCondition {
                probe_task_id: suite.clone(),
                coverage_available: false,
                missing_module: Some("pytest".into()),
            },
        );
        plan.prerequisites.insert(
            task.id.clone(),
            vec![StepPrerequisite {
                task_id: suite.clone(),
                accepted_exits: vec![1],
                require_test_evidence: false,
            }],
        );
        plan.tasks.push(task);
    }
    plan.limitations.push("only an exact missing-pytest diagnostic activates the visible project unittest runner; coverage and TEM remain unavailable and no ici Python backend is invoked".into());
    Ok(())
}
pub fn prerequisite(
    plan: &ValidationPlan,
    id: &str,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<String, ValidationError> {
    if id.starts_with(&format!("{}.repeat", plan.context.task_id))
        && missing_pytest(plan, outcomes)?
    {
        return task_ids(plan)
            .last()
            .cloned()
            .ok_or_else(|| ValidationError("unittest fallback has no declared target".into()));
    }
    python_coverage::prerequisite(plan, id, outcomes)
}
pub fn inactive_pytest(
    plan: &ValidationPlan,
    id: &str,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> bool {
    missing_pytest(plan, outcomes).unwrap_or(false)
        && python_coverage::actual_suite_id(plan, outcomes).is_ok_and(|suite| suite == id)
}
pub fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let mut total = 0u64;
    let mut passed = 0u64;
    let mut executed = 0u64;
    let mut nodes = BTreeMap::new();
    let ran = Regex::new(r"(?m)^Ran ([0-9]+) tests? in [0-9.]+s\s*$").expect("constant regex");
    let terminal = Regex::new(r"(?m)^(?:OK(?: \([^\r\n]*\))?|FAILED \([^\r\n]*\))\s*$")
        .expect("constant regex");
    for (index, id) in task_ids(plan).iter().enumerate() {
        let outcome = outcomes
            .get(id)
            .ok_or_else(|| ValidationError(format!("unittest fallback task {id} did not run")))?;
        if !planning::completed(outcome) || !matches!(outcome.exit_code, Some(0 | 1)) {
            return Err(ValidationError(
                "unittest fallback did not complete under its exit contract".into(),
            ));
        }
        let stdout = std::str::from_utf8(&outcome.stdout)
            .map_err(|_| ValidationError("unittest output is not UTF-8".into()))?;
        let stderr = std::str::from_utf8(&outcome.stderr)
            .map_err(|_| ValidationError("unittest error output is not UTF-8".into()))?;
        let text = format!("{stdout}\n{stderr}");
        let mut observed = 0u64;
        let mut failures = 0u64;
        let evidence = parse_cases(&text)?;
        output.extra["unittest_subtests"][id] = json!(evidence.subtests);
        for (name, (state, message)) in evidence.cases {
            let state = state.as_str();
            let message = message.as_str();
            let status = match state {
                "PASSED" | "XFAIL" => "PASS",
                "SKIPPED" => "SKIP",
                _ => "FAIL",
            };
            let node = format!("unittest:{name}");
            if nodes.insert(node.clone(), state.to_owned()).is_some() || nodes.len() > 100000 {
                return Err(ValidationError(
                    "unittest scope contains duplicate nodes or exceeds 100000 cases".into(),
                ));
            }
            observed += 1;
            total += 1;
            passed += u64::from(matches!(state, "PASSED" | "XFAIL"));
            executed += u64::from(state != "SKIPPED");
            failures += u64::from(status == "FAIL");
            let anchor = &plan.context.targets[index];
            let mut target = InspectionTarget::at(anchor, 1);
            target.target_name = name.clone();
            target.status = status.into();
            target.message = message.into();
            if status == "FAIL" {
                let mut item = crate::python::finding(
                    plan,
                    &format!("unittest.{}", state.to_lowercase()),
                    message,
                    anchor,
                    1,
                );
                item.primary_location.label = name.clone();
                item.fingerprint = sha256(format!("unittest\0{node}\0{state}"));
                output.observation.findings.push(item);
            }
            output.targets.push(target);
        }
        let counts = ran.captures_iter(&text).collect::<Vec<_>>();
        let count = counts
            .first()
            .and_then(|capture| capture[1].parse::<u64>().ok());
        if counts.len() != 1
            || count != Some(observed)
            || !terminal.is_match(&text)
            || observed == 0
        {
            return Err(ValidationError(
                "unittest fallback has no complete nonempty reconciled suite evidence".into(),
            ));
        }
        if (outcome.exit_code == Some(0)) != (failures == 0) {
            return Err(ValidationError(
                "unittest terminal verdict and process exit disagree".into(),
            ));
        }
    }
    output.extra["test_outcomes"] = json!(nodes);
    output.extra["test_framework"] = json!({"selected":"unittest","reason":"pytest module is unavailable","coverage_evidence":"NOT_RUN","quality":"unavailable without pytest"});
    output
        .observation
        .measurements
        .push(crate::python::pair("unittest.cases", passed, total));
    output.summary = format!("{passed}/{total} unittest cases passed");
    if executed == 0 {
        return Err(ValidationError(
            "all unittest cases skipped; nothing was verified".into(),
        ));
    }
    Ok(())
}

struct UnittestEvidence {
    cases: BTreeMap<String, (String, String)>,
    subtests: BTreeMap<String, String>,
}
fn parse_cases(text: &str) -> Result<UnittestEvidence, ValidationError> {
    if text.len() > 8 * 1024 * 1024 || text.contains('\0') {
        return Err(ValidationError(
            "unittest text exceeds its bounded UTF-8/NUL contract".into(),
        ));
    }
    let header = Regex::new(r"^(\S+ \([^\r\n]+?\))(?: (\([^\r\n]+\)))?(?: \.\.\.\s*(.*))?$")
        .expect("constant regex");
    let mut pending = None;
    let mut cases = BTreeMap::<String, (String, String)>::new();
    let mut subtests = BTreeMap::new();
    let mut terminals = std::collections::BTreeSet::new();
    let marker =
        Regex::new(r"\S+ \([^\r\n]+?\)(?: \([^\r\n]+?\))? \.\.\.").expect("constant regex");
    let failure = Regex::new(r"^(FAIL|ERROR): (\S+ \([^\r\n]+?\))(?: (\([^\r\n]+?\)))?$")
        .expect("constant regex");
    let mut lines = Vec::new();
    for line in text.lines() {
        let starts = marker
            .find_iter(line)
            .map(|item| item.start())
            .collect::<Vec<_>>();
        if starts.len() > 1 {
            for (index, start) in starts.iter().enumerate() {
                lines.push(
                    line[*start..starts.get(index + 1).copied().unwrap_or(line.len())].trim(),
                );
            }
        } else {
            lines.push(line.trim());
        }
    }
    for line in lines {
        if let Some(capture) = failure.captures(line) {
            let state = if &capture[1] == "ERROR" {
                "ERROR"
            } else {
                "FAILED"
            };
            let name = capture[2].to_owned();
            if let Some(subtest) = capture.get(3) {
                record_subtest(
                    &mut cases,
                    &mut subtests,
                    name,
                    subtest.as_str(),
                    state,
                    "Unittest assertion failure",
                )?;
            } else if let Some(previous) = cases.get(&name) {
                if previous.0 != state {
                    return Err(ValidationError(
                        "unittest tracebacks contradict the terminal case verdict".into(),
                    ));
                }
            } else {
                return Err(ValidationError(
                    "unittest traceback has no declared terminal case record".into(),
                ));
            }
            continue;
        }
        let (name, subtest, verdict) = if let Some(capture) = header.captures(line) {
            let name = capture[1].to_owned();
            let Some(verdict) = capture
                .get(3)
                .map(|value| value.as_str())
                .filter(|value| !value.is_empty())
            else {
                pending = Some(name);
                continue;
            };
            (
                name,
                capture.get(2).map(|value| value.as_str().to_owned()),
                verdict,
            )
        } else if let Some((_, verdict)) = line.split_once(" ... ") {
            let Some(name) = pending.take() else {
                continue;
            };
            (name, None, verdict.trim())
        } else {
            continue;
        };
        if name.is_empty() || name.len() > 4096 {
            return Err(ValidationError("unittest node name is malformed".into()));
        }
        let (state, message) = match verdict {
            "ok" => ("PASSED", "Unittest passed"),
            value if value.starts_with("expected failure") => {
                ("XFAIL", "Expected failure was exercised")
            }
            value if value.starts_with("skipped") => ("SKIPPED", value),
            value if value.starts_with("unexpected success") => (
                "XPASS",
                "Unexpected success violated the expected-failure contract",
            ),
            value if value.starts_with("FAIL") => ("FAILED", "Unittest assertion failure"),
            value if value.starts_with("ERROR") => ("ERROR", "Unittest assertion failure"),
            _ => continue,
        };
        if let Some(subtest) = subtest {
            record_subtest(&mut cases, &mut subtests, name, &subtest, state, message)?;
        } else {
            if !terminals.insert(name.clone()) || cases.contains_key(&name) {
                return Err(ValidationError(
                    "unittest node was duplicated or contradicted its subtest records".into(),
                ));
            }
            cases.insert(name, (state.into(), message.into()));
            pending = None;
        }
        if cases.len() > 100000 || subtests.len() > 100000 {
            return Err(ValidationError(
                "unittest case inventory exceeds 100000 records".into(),
            ));
        }
    }
    Ok(UnittestEvidence { cases, subtests })
}

fn record_subtest(
    cases: &mut BTreeMap<String, (String, String)>,
    subtests: &mut BTreeMap<String, String>,
    name: String,
    subtest: &str,
    state: &str,
    message: &str,
) -> Result<(), ValidationError> {
    let key = format!("{name} {subtest}");
    if let Some(previous) = subtests.insert(key, state.into())
        && previous != state
    {
        return Err(ValidationError(
            "unittest subtest verdicts contradict one another".into(),
        ));
    }
    let rank = |value: &str| match value {
        "ERROR" => 5,
        "FAILED" => 4,
        "XPASS" => 3,
        "SKIPPED" => 2,
        _ => 1,
    };
    let entry = cases.entry(name).or_insert((state.into(), message.into()));
    if rank(state) > rank(&entry.0) {
        *entry = (state.into(), message.into());
    }
    if cases.len() > 100000 || subtests.len() > 100000 {
        return Err(ValidationError(
            "unittest case inventory exceeds 100000 records".into(),
        ));
    }
    Ok(())
}
