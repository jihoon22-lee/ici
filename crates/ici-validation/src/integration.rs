use crate::{CapturedArtifacts, ValidationError, ValidationPlan, planning, python::finding};
use ici_domain::{AnalysisOutput, InspectionTarget, Measurement, TaskKind};
use ici_execution::TaskOutcome;
use serde_json::json;
use std::{collections::BTreeMap, path::Path};
pub(crate) fn plan(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    let request = &plan.context;
    if request.options.argv.is_empty() || Path::new(&request.options.argv[0]) != request.tool.path {
        return Err(ValidationError(
            "integration argv must name its selected executable identity".into(),
        ));
    }
    if request
        .options
        .integration_case_name
        .as_ref()
        .is_some_and(|name| {
            name.trim().is_empty()
                || name.chars().count() > 128
                || name.chars().any(char::is_control)
        })
    {
        return Err(ValidationError(
            "integration case name must be bounded printable text".into(),
        ));
    }
    if !(0..=255).contains(&request.options.expected_exit) {
        return Err(ValidationError(
            "integration expected exit must be 0..255".into(),
        ));
    }
    let mut task = planning::task(request, "case", request.options.argv[1..].to_vec());
    task.kind = TaskKind::Test;
    task.output_limit_bytes =
        Some(request.options.output_limit_bytes.unwrap_or(64 * 1024) as usize);
    task.requires = request.options.requires.clone();
    for output in &request.options.outputs {
        let path = if output.path.is_absolute() {
            output.path.clone()
        } else {
            request.cwd.join(&output.path)
        };
        if !path.starts_with(&request.root)
            || path.components().any(|part| {
                !matches!(
                    part,
                    std::path::Component::RootDir | std::path::Component::Normal(_)
                )
            })
        {
            return Err(ValidationError(
                "integration output must be normalized inside workspace".into(),
            ));
        }
        task.output_specs.push(path.to_string_lossy().into_owned());
    }
    plan.tasks.push(task);
    if !request.options.requires.is_empty() {
        plan.limitations.push(format!(
            "case depended on declared services/resources: {}",
            request.options.requires.join(", ")
        ));
    }
    Ok(())
}
pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    artifacts: &CapturedArtifacts,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let id = format!("{}.case", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("integration case did not run".into()))?;
    if !planning::completed(outcome) {
        return Err(ValidationError(
            "integration case did not complete; process contract is unverified".into(),
        ));
    }
    let stdout = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("integration stdout is not UTF-8".into()))?;
    let stderr = std::str::from_utf8(&outcome.stderr)
        .map_err(|_| ValidationError("integration stderr is not UTF-8".into()))?;
    let anchor = plan
        .context
        .targets
        .first()
        .cloned()
        .unwrap_or_else(|| "ici.toml".into());
    let mut checks = vec![(
        "exit".to_string(),
        outcome.exit_code == Some(plan.context.options.expected_exit),
        format!(
            "expected exit {}, got {:?}",
            plan.context.options.expected_exit, outcome.exit_code
        ),
    )];
    for (name, text, must, must_not) in [
        (
            "stdout",
            stdout,
            &plan.context.options.stdout_contains,
            &plan.context.options.stdout_not_contains,
        ),
        (
            "stderr",
            stderr,
            &plan.context.options.stderr_contains,
            &plan.context.options.stderr_not_contains,
        ),
    ] {
        for needle in must {
            checks.push((
                format!("{name}-contains"),
                text.contains(needle),
                format!("{name} must contain {needle:?}"),
            ));
        }
        for needle in must_not {
            checks.push((
                format!("{name}-not-contains"),
                !text.contains(needle),
                format!("{name} must not contain {needle:?}"),
            ));
        }
    }
    for (index, assertion) in plan.context.options.outputs.iter().enumerate() {
        let key = format!("integration-output:{index}");
        let witness = artifacts.outputs.get(&key);
        let passed = witness.is_some_and(|witness| {
            witness.fresh && witness.size >= assertion.min_size && witness.producer_task_id == id
        });
        checks.push((
            "output".into(),
            passed,
            format!(
                "output {} must be a fresh regular file of at least {} bytes{}",
                assertion.path.display(),
                assertion.min_size,
                artifacts
                    .errors
                    .get(&key)
                    .map(|error| format!(": {error}"))
                    .unwrap_or_default()
            ),
        ));
    }
    if let Some(name) = &plan.context.options.integration_case_name {
        return observe_named_case(plan, output, outcome, name, &anchor, &checks);
    }
    for (aspect, passed, detail) in &checks {
        let mut target = InspectionTarget::at(&anchor, 1);
        target.target_name = format!("Process contract: {aspect}");
        target.status = if *passed { "PASS" } else { "FAIL" }.into();
        target.message = detail.clone();
        output.targets.push(target);
        if !passed {
            output.observation.findings.push(finding(
                plan,
                &format!("integration.{aspect}"),
                detail,
                &anchor,
                1,
            ));
        }
    }
    let mut metric = Measurement::measured("integration.assertions", checks.len() as f64);
    metric.numerator = Some(checks.iter().filter(|(_, passed, _)| *passed).count() as u64);
    metric.denominator = Some(checks.len() as u64);
    output.observation.measurements.push(metric);
    output.extra["assertions"] = json!(checks);
    output.summary = format!(
        "{} process contract assertions evaluated",
        output.targets.len()
    );
    Ok(())
}

fn observe_named_case(
    plan: &ValidationPlan,
    output: &mut AnalysisOutput,
    outcome: &TaskOutcome,
    name: &str,
    anchor: &str,
    checks: &[(String, bool, String)],
) -> Result<(), ValidationError> {
    let aliases = [
        ("exit", "exit_code"),
        ("stdout-contains", "stdout_contains"),
        ("stderr-contains", "stderr_contains"),
        ("stdout-not-contains", "stdout_not_contains"),
        ("stderr-not-contains", "stderr_not_contains"),
        ("output", "output_artifacts"),
    ];
    let assertions = aliases
        .iter()
        .map(|(aspect, original)| {
            (
                (*original).to_owned(),
                checks
                    .iter()
                    .filter(|(name, _, _)| name == aspect)
                    .all(|(_, passed, _)| *passed),
            )
        })
        .collect::<BTreeMap<_, _>>();
    let failed = aliases
        .iter()
        .filter_map(|(_, name)| (!assertions[*name]).then_some(*name))
        .collect::<Vec<_>>();
    let mut target = InspectionTarget::at(anchor, 1);
    target.target_name = format!("Integration:{name}");
    target.status = if failed.is_empty() {
        "PASS"
    } else if plan.context.options.integration_required {
        "FAIL"
    } else {
        "WARN"
    }
    .into();
    target.message = if failed.is_empty() {
        "All process and output artifact assertions passed".into()
    } else {
        format!("Failed assertions: {}", failed.join(", "))
    };
    if let Some(first) = failed.first() {
        let message = format!(
            "Integration case {} failed assertions: {}",
            repr(name),
            failed.join(", ")
        );
        let mut item = finding(plan, "ici.hybrid.process-contract", &message, anchor, 1);
        item.primary_location.label = name.into();
        item.category = "correctness".into();
        item.confidence = "exact".into();
        item.native_rule_id = format!("process.{}", first.replace('_', "-"));
        if !plan.context.options.integration_required {
            item.severity = "medium".into();
            item.tags.push("advisory".into());
        }
        output.extra["finding_details"][&item.fingerprint] = json!({"legacy_rule_id":"ici.hybrid.process-contract","legacy_label":name,"legacy_category":"correctness","legacy_severity":item.severity,"legacy_confidence":"exact","tool_name":"ici integration runner","tool_rule_id":item.native_rule_id,"explanation":"A declared cross-language process assertion was not satisfied.","remediation":"Correct the producer/consumer contract or its declared expected result."});
        output.observation.findings.push(item);
    }
    output.extra["integration"] = json!({"state":"MEASURED","cases":[{"name":name,"status":target.status,"returncode":outcome.exit_code,"duration_seconds":outcome.duration_seconds,"stdout_truncated":outcome.truncated,"stderr_truncated":outcome.truncated,"assertions":assertions}]});
    output.extra["assertions"] = json!(checks);
    output.summary = format!(
        "Integration contracts: 1 run, {} violation(s)",
        usize::from(!failed.is_empty())
    );
    output.targets.push(target);
    let mut metric = Measurement::measured("integration.assertions", assertions.len() as f64);
    metric.numerator = Some(assertions.values().filter(|value| **value).count() as u64);
    metric.denominator = Some(assertions.len() as u64);
    output.observation.measurements.push(metric);
    Ok(())
}
fn repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    format!(
        "{quote}{}{quote}",
        value
            .replace('\\', "\\\\")
            .replace(quote, &format!("\\{quote}"))
    )
}
