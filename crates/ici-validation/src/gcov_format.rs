//! A complete native help/version probe selects one visible GCOV output format.
use crate::{
    ArtifactPattern, GcovFormat, StepCondition, StepPrerequisite, ValidationError, ValidationPlan,
    planning,
};
use ici_domain::TaskKind;
use ici_execution::TaskOutcome;
use std::collections::BTreeMap;

pub(crate) fn configure(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    if !plan.context.options.gcov_capability_probe {
        return Ok(());
    }
    let report_id = format!("{}.report", plan.context.task_id);
    let report = plan
        .tasks
        .iter()
        .find(|task| task.id == report_id)
        .ok_or_else(|| ValidationError("GCOV JSON report task is absent".into()))?
        .clone();
    let mut text = report;
    text.id = format!("{}.text-report", plan.context.task_id);
    text.argv.retain(|argument| argument != "--json-format");
    text.argv.insert(1, "-c".into());
    text.output_specs = vec!["*.gcov".into()];
    let capability_id = format!("{}.capability", plan.context.task_id);
    let version_id = format!("{}.version", plan.context.task_id);
    for (suffix, argument, maximum) in [
        ("capability", "--help", 64 * 1024),
        ("version", "--version", 8192),
    ] {
        let mut probe = planning::task(&plan.context, suffix, vec![argument.into()]);
        probe.kind = TaskKind::Probe;
        probe.timeout_seconds = Some(30.0);
        probe.output_limit_bytes = Some(maximum);
        probe.env_overlay.insert("LC_ALL".into(), "C".into());
        probe.env_overlay.insert("LANG".into(), "C".into());
        plan.tasks.insert(0, probe);
    }
    let dependencies = plan
        .prerequisites
        .get(&report_id)
        .cloned()
        .unwrap_or_default();
    plan.prerequisites.insert(text.id.clone(), dependencies);
    for (id, format) in [(&report_id, GcovFormat::Json), (&text.id, GcovFormat::Text)] {
        plan.conditional_steps.insert(
            id.clone(),
            StepCondition {
                probe_task_id: capability_id.clone(),
                coverage_available: false,
                missing_module: None,
                gcov_format: Some(format),
            },
        );
        plan.prerequisites.entry(id.clone()).or_default().extend(
            [&capability_id, &version_id]
                .into_iter()
                .map(|id| StepPrerequisite {
                    task_id: id.clone(),
                    accepted_exits: vec![0],
                    require_test_evidence: false,
                }),
        );
    }
    plan.artifact_patterns.push(ArtifactPattern {
        id_prefix: "gcov-text".into(),
        suffix: ".gcov".into(),
        producer_task_id: text.id.clone(),
        max_files: 4096,
        max_bytes_each: 64 * 1024 * 1024,
        max_bytes_total: 256 * 1024 * 1024,
    });
    plan.tasks.push(text);
    plan.limitations.push("a complete GCOV help/version probe selects one report format; legacy text has source-unit function anchors and ordered branch identities, with unavailable geometry stated explicitly".into());
    Ok(())
}

pub(crate) fn selected(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<Option<(GcovFormat, Option<String>)>, ValidationError> {
    if !plan.context.options.gcov_capability_probe {
        return Ok(Some((GcovFormat::Json, plan.context.tool.version.clone())));
    }
    let read = |suffix: &str| -> Result<Option<String>, ValidationError> {
        let id = format!("{}.{suffix}", plan.context.task_id);
        let Some(outcome) = outcomes.get(&id) else {
            return Ok(None);
        };
        if outcome.task_id != id || !planning::completed(outcome) || outcome.exit_code != Some(0) {
            return Err(ValidationError(format!(
                "GCOV {suffix} probe did not complete; no report format was selected"
            )));
        }
        let stdout = std::str::from_utf8(&outcome.stdout)
            .map_err(|_| ValidationError("GCOV probe stdout is not UTF-8".into()))?;
        let stderr = std::str::from_utf8(&outcome.stderr)
            .map_err(|_| ValidationError("GCOV probe stderr is not UTF-8".into()))?;
        Ok(Some(format!("{stdout}\n{stderr}")))
    };
    let Some(help) = read("capability")? else {
        return Ok(None);
    };
    let Some(version) = read("version")? else {
        return Ok(None);
    };
    let version_pattern = regex::Regex::new(
        r"(?im)^gcov\s+\([^\r\n)]+\)\s+([0-9]+(?:\.[0-9]+)+(?:[A-Za-z0-9.+~-]*))\b",
    )
    .expect("constant regex");
    let version = version_pattern
        .captures(&version)
        .map(|capture| capture[1].to_owned())
        .ok_or_else(|| {
            ValidationError("GCOV version probe returned no recognized version identity".into())
        })?;
    if !help
        .lines()
        .any(|line| line.contains("gcov") && line.contains("["))
        || !help.contains("-b")
    {
        return Err(ValidationError(
            "GCOV capability help is unrecognized; legacy text was not selected".into(),
        ));
    }
    Ok(Some((
        if help.contains("--json-format") {
            GcovFormat::Json
        } else {
            GcovFormat::Text
        },
        Some(version),
    )))
}

pub(crate) fn condition_ready(
    plan: &ValidationPlan,
    task: &str,
    format: GcovFormat,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<bool, ValidationError> {
    let Some((selected, _)) = selected(plan, outcomes)? else {
        return Ok(false);
    };
    let active = selected == format;
    if !active && outcomes.contains_key(task) {
        return Err(ValidationError(
            "an inactive GCOV report format was executed".into(),
        ));
    }
    Ok(active)
}
