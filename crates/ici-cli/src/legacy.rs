//! Historical commands select common checks; they do not own a second analysis implementation.
use crate::registry::Invocation;
use ici_domain::{CheckDefinition, Profile};
use std::collections::{BTreeMap, BTreeSet};

pub fn engine(command: &str) -> Option<&'static str> {
    Some(match command {
        "line" => "line",
        "lint" => "lint",
        "test" => "test",
        "type" => "type",
        "complexity" => "complexity",
        "cognitive" => "cognitive",
        "cycle" => "cycle",
        "resource" => "resource",
        "security" => "security",
        "dead" => "dead",
        "dup" => "dup",
        "exception" => "exception",
        "python-compat" => "python_compat",
        "sanitize" => "sanitize",
        "thread-sanitize" => "thread_sanitize",
        "build" => "build",
        _ => return None,
    })
}
pub fn is_legacy(invocation: &Invocation) -> bool {
    invocation.command() == "verify" || engine(&invocation.command()).is_some()
}

/// Historical WARN is a completed, non-failing policy outcome. Retain its measured diagnostics
/// and mark their gate role separately; modern checks retain their fail-on-violation contract.
pub fn apply_gate_policy(
    prepared: &crate::planning::PreparedPlan,
    outputs: &mut BTreeMap<String, ici_domain::AnalysisOutput>,
) {
    for check in &prepared.plan.checks {
        let Some(engine_name) = ici_application::plan::engine_name(&check.check.id) else {
            continue;
        };
        if !prepared.legacy_projection
            && prepared.project.schema_family != ici_config::SchemaFamily::Legacy
            && !explicit_mode_for(prepared, engine_name)
        {
            continue;
        }
        let Some(output) = outputs.get_mut(&check.task_id) else {
            continue;
        };
        if !output.observation.evidence_is_complete() {
            continue;
        }
        let component = crate::inputs::component(prepared, check);
        let mode = component
            .settings
            .get("engines")
            .and_then(|v| v.get(engine_name))
            .and_then(|settings| settings.get("mode"))
            .and_then(serde_json::Value::as_str)
            .unwrap_or("pass_warn_fail");
        if matches!(
            check.check.id.as_str(),
            "python.test" | "python.coverage" | "cpp.test" | "cpp.coverage"
        ) {
            apply_test_warning_policy(output, mode);
        }
        if matches!(output.status.as_str(), "FAIL" | "WARN") {
            if mode == "pass_warn" {
                output.status = "WARN".into();
            } else if mode == "pass_fail" {
                output.status = "FAIL".into();
            }
        }
        if matches!(output.status.as_str(), "PASS" | "WARN") {
            for finding in &mut output.observation.findings {
                for tag in ["advisory", "legacy-policy-advisory"] {
                    if !finding.tags.iter().any(|value| value == tag) {
                        finding.tags.push(tag.into());
                    }
                }
            }
        }
    }
}

fn explicit_mode_for(prepared: &crate::planning::PreparedPlan, engine: &str) -> bool {
    prepared
        .project
        .origins
        .get(&format!("engines.{engine}.mode"))
        .is_some_and(|origin| origin.layer != "defaults")
}

fn apply_test_warning_policy(output: &mut ici_domain::AnalysisOutput, mode: &str) {
    let findings = &output.observation.findings;
    if findings.is_empty() {
        return;
    }
    let quality_rule = |rule: &str| matches!(rule, "ici.test.flaky-test" | "ici.test.slow-test");
    let quality_mode = output
        .extra
        .get(&output.observation.task_id)
        .unwrap_or(&output.extra)["test_quality"]["mode"]
        .as_str();
    let quality_report_only = quality_mode == Some("report")
        && findings
            .iter()
            .all(|finding| quality_rule(&finding.rule_id));
    if quality_report_only {
        // Frozen TestEngine records report-mode quality targets without entering has_warn.
        output.status = "PASS".into();
        return;
    }
    if mode == "pass_warn_fail"
        && output.status == "FAIL"
        && findings.iter().all(|finding| {
            finding.rule_id.starts_with("ici.test.coverage.")
                || (quality_rule(&finding.rule_id) && quality_mode == Some("warn"))
        })
    {
        output.status = "WARN".into();
    }
}
pub fn select(
    invocation: &Invocation,
    project: &mut ici_config::ResolvedProject,
    checks: &[CheckDefinition],
) -> Vec<String> {
    let Some(family) = engine(&invocation.command()) else {
        return vec![];
    };
    let mut selected: BTreeSet<_> = checks
        .iter()
        .filter(|check| ici_application::plan::engine_name(&check.id) == Some(family))
        .map(|check| check.id.clone())
        .collect();
    loop {
        let needs: BTreeSet<_> = checks
            .iter()
            .filter(|check| selected.contains(&check.id))
            // Frozen `ici lint` has no analysis context: its C++ syntax check never consumes
            // recorded compilation inputs, so it pulls in no compile/build prerequisite.
            .filter(|check| !(family == "lint" && check.language == "cpp"))
            .flat_map(|check| check.needs.iter().cloned())
            .collect();
        let old = selected.len();
        selected.extend(
            checks
                .iter()
                .filter(|check| {
                    check
                        .provides
                        .iter()
                        .any(|provided| needs.contains(provided))
                })
                .map(|check| check.id.clone()),
        );
        if selected.len() == old {
            break;
        }
    }
    // Under `ici build` the build checks are the command's whole subject: they are required for
    // this invocation whatever engines.build.required says, so a failed build is never a PASS gate.
    let subject: BTreeSet<String> = if family == "build" {
        checks
            .iter()
            .filter(|check| ici_application::plan::engine_name(&check.id) == Some(family))
            .map(|check| check.id.clone())
            .collect()
    } else {
        BTreeSet::new()
    };
    // Typing an individual command explicitly selects that family even when omitted from verify.
    for id in &selected {
        let mut settings = project
            .checks
            .get(id)
            .cloned()
            .unwrap_or(ici_config::CheckSetting {
                enabled: true,
                required: true,
                exemptions: BTreeMap::new(),
                settings: serde_json::json!({}),
            });
        settings.enabled = true;
        settings.settings["enabled"] = true.into();
        if subject.contains(id) {
            settings.required = true;
            settings.settings["required"] = true.into();
        }
        project.checks.insert(id.clone(), settings);
        for component in &mut project.components {
            if let Some(setting) = component.checks.get_mut(id) {
                setting.enabled = true;
                setting.settings["enabled"] = true.into();
                if subject.contains(id) {
                    setting.required = true;
                    setting.settings["required"] = true.into();
                }
            }
        }
    }
    selected.into_iter().collect()
}
pub fn individual_profile(invocation: &Invocation) -> Option<Profile> {
    engine(&invocation.command()).map(|_| Profile::Deep)
}

/// Estimated annotation evidence is independent of required external type checking.
pub fn annotation_check(
    invocation: &Invocation,
    project: &mut ici_config::ResolvedProject,
) -> Option<CheckDefinition> {
    if !is_legacy(invocation) {
        return None;
    }
    let mut definition = ici_domain::builtin_catalog()
        .checks
        .into_iter()
        .find(|check| check.id == "python.type")?;
    definition.id = "python.annotations".into();
    definition.title = "Estimated Python return annotation inspection".into();
    definition.tool = None;
    definition.required = false;
    definition.needs.clear();
    definition.provides.clear();
    if let Some(mut setting) = project.checks.get("python.type").cloned() {
        setting.required = false;
        setting.settings["required"] = false.into();
        project.checks.insert(definition.id.clone(), setting);
    }
    for component in &mut project.components {
        if let Some(mut setting) = component.checks.get("python.type").cloned() {
            setting.required = false;
            setting.settings["required"] = false.into();
            component.checks.insert(definition.id.clone(), setting);
        }
    }
    Some(definition)
}

/// Frozen `TypeCheckEngine._check_python_types` ran its AST annotation pass only when mypy did
/// not finish cleanly (findings, failure or no mypy), skipped unparsable files silently, and
/// never emitted a per-file inventory. The legacy-only `python.annotations` check keeps exactly
/// that: nothing beside a clean mypy run, only its missing-annotation targets otherwise, measured
/// when mypy ran (the AST facts are exact; only the missing type checker made frozen ESTIMATED).
pub fn annotation_fallback(
    prepared: &crate::planning::PreparedPlan,
    outputs: &mut BTreeMap<String, ici_domain::AnalysisOutput>,
) {
    if !prepared.legacy_projection {
        return;
    }
    for check in &prepared.plan.checks {
        if check.check.id != "python.annotations" {
            continue;
        }
        let mypy = prepared
            .plan
            .checks
            .iter()
            .find(|other| {
                other.check.id == "python.type" && other.component_id == check.component_id
            })
            .and_then(|other| outputs.get(&other.task_id))
            .and_then(|output| output.extra.get("legacy_mypy"))
            .map(|marker| marker["clean"] == true);
        let Some(output) = outputs.get_mut(&check.task_id) else {
            continue;
        };
        let keep = mypy != Some(true);
        output.targets.retain(|target| {
            keep && target.status == "WARN" && target.target_name != "AnnotationCoverage"
        });
        output
            .observation
            .findings
            .retain(|finding| keep && finding.rule_id == "type.missing-return-annotation");
        if mypy.is_some() {
            for finding in &mut output.observation.findings {
                finding.evidence = ici_domain::EvidenceLevel::Measured;
            }
        }
        if output.observation.state == ici_domain::TaskState::Failed {
            output.observation.state = ici_domain::TaskState::Succeeded;
            output.observation.limitations.push(
                "unparsable Python sources were skipped by the historical annotation pass".into(),
            );
        }
        output.status = if output.targets.is_empty() {
            "PASS"
        } else {
            "WARN"
        }
        .into();
    }
}

/// Frozen `LintEngine._check_python_syntax` beside Ruff: every selected Python file that does not
/// parse is one `SyntaxError` FAIL target after the Ruff check and format targets. It belongs to
/// the historical lint view only (the modern checks report syntax through their own findings).
pub fn lint_syntax(
    prepared: &crate::planning::PreparedPlan,
    outputs: &mut BTreeMap<String, ici_domain::AnalysisOutput>,
) {
    if !prepared.legacy_projection {
        return;
    }
    for component in &prepared.project.components {
        let ran = |id: &str| {
            prepared
                .plan
                .checks
                .iter()
                .find(|check| check.check.id == id && check.component_id == component.id)
                .map(|check| check.task_id.clone())
                .filter(|task| {
                    outputs
                        .get(task)
                        .is_some_and(|output| output.extra.get("legacy_ruff").is_some())
                })
        };
        let Some(task) = ran("python.format").or_else(|| ran("python.lint")) else {
            continue;
        };
        let targets = python_syntax_targets(&prepared.project.root, &component.files);
        let output = outputs.get_mut(&task).expect("checked output");
        if !targets.is_empty() && output.status != "ERROR" {
            output.status = "FAIL".into();
        }
        output.targets.extend(targets);
    }
}

/// `SyntaxError` targets for the selected `.py` files, in their sorted order.
pub fn python_syntax_targets(
    root: &std::path::Path,
    files: &[String],
) -> Vec<ici_domain::InspectionTarget> {
    files
        .iter()
        .filter(|path| path.ends_with(".py"))
        .filter_map(|path| {
            let bytes = std::fs::read(root.join(path)).ok()?;
            let input = ici_domain::SourceInput {
                path: path.clone(),
                bytes,
            };
            let (line, message) = ici_analysis::lint_syntax_error(&input)?;
            let mut target = ici_domain::InspectionTarget::at(path, line);
            target.target_name = "SyntaxError".into();
            target.status = "FAIL".into();
            target.message = message;
            Some(target)
        })
        .collect()
}

/// Binary ABI checks inspect published artifact bytes independently of source language.
pub fn python_binary_compat_check() -> CheckDefinition {
    let mut definition = ici_domain::builtin_catalog()
        .checks
        .into_iter()
        .find(|check| check.id == "cpp.binary-compat")
        .expect("built-in binary compatibility check");
    definition.id = "python.binary-compat".into();
    definition.title = "Published binary ABI compatibility".into();
    definition.language = "python".into();
    definition.needs.clear();
    definition
}

#[cfg(test)]
mod test_policy_tests {
    use super::apply_test_warning_policy;
    use ici_domain::{AnalysisOutput, Finding, SourceSpan};
    use serde_json::json;

    fn output(rule: &str, quality_mode: &str) -> AnalysisOutput {
        let mut result = AnalysisOutput::new("suite", "pytest");
        result.status = "FAIL".into();
        result.extra["test_quality"] = json!({"mode":quality_mode});
        result.observation.findings.push(Finding::new(
            rule,
            "test",
            SourceSpan::line("tests/test_example.py", 1),
            "pytest",
        ));
        result
    }

    #[test]
    fn report_only_quality_never_enters_test_warning_gate() {
        let mut result = output("ici.test.flaky-test", "report");
        apply_test_warning_policy(&mut result, "pass_fail");
        assert_eq!(result.status, "PASS");
        assert_eq!(result.observation.findings.len(), 1);
    }

    #[test]
    fn warning_policy_keeps_thresholds_distinct_from_base_failures() {
        let mut warning = output("ici.test.coverage.changed-lines", "warn");
        apply_test_warning_policy(&mut warning, "pass_warn_fail");
        assert_eq!(warning.status, "WARN");
        let mut failure = output("pytest.failed", "warn");
        failure
            .observation
            .findings
            .push(warning.observation.findings[0].clone());
        apply_test_warning_policy(&mut failure, "pass_warn_fail");
        assert_eq!(failure.status, "FAIL");
    }
}
