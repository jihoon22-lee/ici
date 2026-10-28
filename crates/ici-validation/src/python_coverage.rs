//! Visible optional coverage capability and mutually exclusive suite branches.
use crate::{StepPrerequisite, ValidationError, ValidationKind, ValidationPlan, planning};
use ici_domain::{AnalysisOutput, InspectionTarget, TaskKind};
use ici_execution::TaskOutcome;
use serde_json::json;
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub enum Capability {
    Available(String),
    Missing,
}
pub fn suite_id(plan: &ValidationPlan) -> String {
    plan.context
        .options
        .shared_test_task_id
        .clone()
        .unwrap_or_else(|| format!("{}.suite", plan.context.task_id))
}
fn enabled(plan: &ValidationPlan) -> bool {
    plan.context.options.coverage_module_probe
        && (plan.context.options.collect_coverage || plan.kind == ValidationKind::PythonCoverage)
}
pub fn probe_id(plan: &ValidationPlan) -> Option<String> {
    enabled(plan).then(|| {
        format!(
            "{}.coverage-probe",
            suite_id(plan)
                .strip_suffix(".suite")
                .unwrap_or(&plan.context.task_id)
        )
    })
}
pub fn fallback_id(plan: &ValidationPlan) -> String {
    format!(
        "{}.uncovered-suite",
        suite_id(plan)
            .strip_suffix(".suite")
            .unwrap_or(&plan.context.task_id)
    )
}
pub fn capability(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<Option<Capability>, ValidationError> {
    let Some(id) = probe_id(plan) else {
        return Ok(None);
    };
    let Some(outcome) = outcomes.get(&id) else {
        return Ok(None);
    };
    if outcome.task_id != id || !planning::completed(outcome) {
        return Err(ValidationError(
            "coverage capability probe did not complete under its bounded contract".into(),
        ));
    }
    let stdout = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("coverage capability output is not UTF-8".into()))?;
    let stderr = std::str::from_utf8(&outcome.stderr)
        .map_err(|_| ValidationError("coverage capability error output is not UTF-8".into()))?;
    if outcome.exit_code == Some(0) {
        let version =
            regex::Regex::new(r"(?i)\bcoverage(?:\.py)?,?\s+version\s+([0-9][0-9A-Za-z.+-]*)")
                .expect("constant regex");
        let text = format!("{stdout}\n{stderr}");
        let version = version
            .captures(&text)
            .map(|capture| capture[1].to_owned())
            .ok_or_else(|| {
                ValidationError(
                    "coverage capability probe returned no recognized version evidence".into(),
                )
            })?;
        return Ok(Some(Capability::Available(version)));
    }
    let missing = stderr.lines().any(|line| {
        let Some((executable, module)) = line.trim().rsplit_once(": No module named ") else {
            return false;
        };
        std::path::Path::new(executable) == plan.context.tool.path
            && module.trim_matches(['\'', '"']) == "coverage"
    });
    if outcome.exit_code == Some(1) && missing && !stderr.contains("Traceback (") {
        return Ok(Some(Capability::Missing));
    }
    Err(ValidationError("coverage is present-but-broken or its capability output is malformed; no fallback was selected".into()))
}
pub fn configure(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    let Some(probe) = probe_id(plan) else {
        return Ok(());
    };
    let suite = suite_id(plan);
    if plan.context.options.shared_test_task_id.is_none() {
        let covered = plan
            .tasks
            .iter()
            .find(|task| task.id == suite)
            .ok_or_else(|| ValidationError("covered suite missing from conditional plan".into()))?
            .clone();
        if covered
            .argv
            .get(1..7)
            .is_none_or(|args| args[0..5] != ["-m", "coverage", "run", "--branch", "--data-file"])
        {
            return Err(ValidationError(
                "covered suite has no recognized coverage invocation".into(),
            ));
        }
        let mut fallback = covered;
        fallback.id = fallback_id(plan);
        fallback.argv.drain(1..7);
        fallback.output_specs.clear();
        fallback.provider = "pytest-uncovered".into();
        let mut query = planning::task(
            &plan.context,
            "coverage-probe",
            vec!["-m".into(), "coverage".into(), "--version".into()],
        );
        query.id = probe.clone();
        query.kind = TaskKind::Probe;
        query.timeout_seconds = Some(30.0);
        query.output_limit_bytes = Some(8192);
        plan.prerequisites.insert(
            suite.clone(),
            vec![StepPrerequisite {
                task_id: probe.clone(),
                accepted_exits: vec![0],
                require_test_evidence: false,
            }],
        );
        plan.prerequisites.insert(
            fallback.id.clone(),
            vec![StepPrerequisite {
                task_id: probe.clone(),
                accepted_exits: vec![1],
                require_test_evidence: false,
            }],
        );
        plan.conditional_steps.insert(
            suite.clone(),
            crate::StepCondition {
                probe_task_id: probe.clone(),
                coverage_available: true,
            },
        );
        plan.conditional_steps.insert(
            fallback.id.clone(),
            crate::StepCondition {
                probe_task_id: probe.clone(),
                coverage_available: false,
            },
        );
        plan.tasks.insert(0, query);
        plan.tasks.push(fallback);
    }
    if plan.kind == ValidationKind::PythonCoverage {
        plan.conditional_steps.insert(
            format!("{}.report", plan.context.task_id),
            crate::StepCondition {
                probe_task_id: probe.clone(),
                coverage_available: true,
            },
        );
    }
    plan.optional_task_ids.push(probe);
    plan.limitations.push("coverage capability selects one visible suite branch; only an exact missing-module diagnostic permits plain pytest fallback, and missing coverage is never measured as zero or estimated coverage".into());
    Ok(())
}
pub fn condition_ready(
    plan: &ValidationPlan,
    task: &str,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<bool, ValidationError> {
    let Some(condition) = plan.conditional_steps.get(task) else {
        return Ok(true);
    };
    let Some(capability) = capability(plan, outcomes)? else {
        return Ok(false);
    };
    let available = matches!(capability, Capability::Available(_));
    let active = condition.coverage_available == available;
    if !active && outcomes.contains_key(task) {
        return Err(ValidationError(
            "an inactive conditional coverage branch was executed".into(),
        ));
    }
    Ok(active)
}
pub fn actual_suite_id(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<String, ValidationError> {
    let suite = suite_id(plan);
    match capability(plan, outcomes)? {
        Some(Capability::Missing) => {
            if outcomes.contains_key(&suite) {
                return Err(ValidationError(
                    "covered suite executed despite missing coverage capability".into(),
                ));
            }
            Ok(fallback_id(plan))
        }
        Some(Capability::Available(_)) => {
            if outcomes.contains_key(&fallback_id(plan)) {
                return Err(ValidationError(
                    "plain fallback executed despite available coverage capability".into(),
                ));
            }
            Ok(suite)
        }
        None => Ok(suite),
    }
}
pub fn prerequisite(
    plan: &ValidationPlan,
    id: &str,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<String, ValidationError> {
    if id == suite_id(plan) {
        actual_suite_id(plan, outcomes)
    } else {
        Ok(id.into())
    }
}
pub fn annotate(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) {
    let Some(probe) = probe_id(plan) else {
        return;
    };
    let state = match capability(plan, outcomes) {
        Ok(Some(Capability::Available(version))) => {
            output
                .observation
                .tool_versions
                .push(("coverage".into(), version.clone()));
            json!({"availability":"available","version":version})
        }
        Ok(Some(Capability::Missing)) => {
            json!({"availability":"missing","coverage_evidence":"NOT_RUN"})
        }
        Ok(None) => json!({"availability":"not_observed"}),
        Err(error) => json!({"availability":"error","reason":error.to_string()}),
    };
    output.extra["coverage_capability"] = state;
    output.extra["coverage_capability"]["probe_task_id"] = json!(probe);
    output.extra["coverage_capability"]["active_suite"] =
        json!(actual_suite_id(plan, outcomes).ok());
}
pub fn unavailable(
    plan: &ValidationPlan,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    output.extra["coverage_policy"] = json!(crate::coverage_baseline::unavailable(
        plan,
        "coverage module is unavailable for the selected project interpreter"
    )?);
    let path = plan
        .context
        .source_files
        .first()
        .map(String::as_str)
        .unwrap_or("ici.toml");
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = "Coverage capability".into();
    target.status = "SKIP".into();
    target.message="coverage is unavailable; actual plain pytest evidence is retained separately and coverage was not measured".into();
    target
        .metrics
        .insert("coverage_available".into(), json!(false));
    output.targets.push(target);
    Err(ValidationError(
        "coverage module is unavailable; no coverage or TEM evidence was fabricated".into(),
    ))
}
