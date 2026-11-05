//! A complete native help/version probe selects one visible GCOV output format.
use crate::{
    ArtifactPattern, GcovFormat, StepCondition, StepPrerequisite, ValidationError, ValidationPlan,
    planning,
};
use ici_domain::TaskKind;
use ici_execution::TaskOutcome;
use std::collections::BTreeMap;

/// Frozen's tool error for a `gcov --help` probe that failed, timed out or was truncated.
pub(crate) const LEGACY_PROBE_INCOMPLETE: &str = "gcov JSON capability probe was incomplete";
/// Frozen `run_process` defaults (300 s, 1,000,000 characters per stream), under which frozen
/// runs both `gcov --help` and the gcov report.
const LEGACY_TIMEOUT_SECONDS: f64 = 300.0;
const LEGACY_OUTPUT_LIMIT: usize = 1_000_000;

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
    let legacy = plan.context.options.legacy_gcov_source_wording;
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
        if legacy && suffix == "capability" {
            probe.timeout_seconds = Some(LEGACY_TIMEOUT_SECONDS);
            probe.output_limit_bytes = Some(LEGACY_OUTPUT_LIMIT);
        }
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
    let text_id = text.id.clone();
    plan.tasks.push(text);
    if legacy {
        for task in plan
            .tasks
            .iter_mut()
            .filter(|task| task.id == report_id || task.id == text_id)
        {
            task.timeout_seconds = Some(LEGACY_TIMEOUT_SECONDS);
            task.output_limit_bytes = Some(LEGACY_OUTPUT_LIMIT);
        }
    }
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
            // Frozen `gcov_json_capability` returns None for a failed, timed-out or truncated
            // `gcov --help`, which `_collect_cpp_coverage`/`collect_coverage` word as below.
            if plan.context.options.legacy_gcov_source_wording && suffix == "capability" {
                return Err(ValidationError(LEGACY_PROBE_INCOMPLETE.into()));
            }
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
    let selected = match selected(plan, outcomes) {
        // Frozen records the incomplete probe as a tool error and runs no gcov report; the
        // observation then reports it, as a failed (not blocked) coverage check.
        Err(failure) if failure.0 == LEGACY_PROBE_INCOMPLETE => return Ok(false),
        other => other?,
    };
    let Some((selected, _)) = selected else {
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
