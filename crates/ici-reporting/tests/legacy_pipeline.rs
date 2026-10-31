use ici_domain::{
    AnalysisOutput, CheckRecord, EvidenceLevel, Finding, InspectionTarget, Measurement, RunResult,
    TaskState,
};
use ici_reporting::{ProjectionContext, legacy_projection};
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[test]
fn not_applicable_facets_do_not_mask_measured_or_estimated_sibling_evidence() {
    for (levels, expected) in [
        (vec![EvidenceLevel::NotApplicable], "NOT_APPLICABLE"),
        (
            vec![EvidenceLevel::NotApplicable, EvidenceLevel::NotApplicable],
            "NOT_APPLICABLE",
        ),
        (
            vec![EvidenceLevel::NotApplicable, EvidenceLevel::Measured],
            "MEASURED",
        ),
        (
            vec![EvidenceLevel::Measured, EvidenceLevel::NotApplicable],
            "MEASURED",
        ),
        (
            vec![EvidenceLevel::NotApplicable, EvidenceLevel::Estimated],
            "ESTIMATED",
        ),
    ] {
        let mut run = RunResult::from_json(include_str!(
            "../../../tests/fixtures/ici-next/run-success.json"
        ))
        .unwrap();
        run.schema_version = 2;
        let mut checks = vec![];
        let mut outputs = BTreeMap::new();
        for (index, level) in levels.into_iter().enumerate() {
            let id = format!("task-{index}");
            checks.push(CheckRecord {
                task_id: id.clone(),
                check_id: "python.test".into(),
                component_id: "tool-a".into(),
                language: "python".into(),
                scope_digest: ici_domain::sha256("scope"),
                state: TaskState::Succeeded,
                evidence_complete: true,
                analysis_unit_id: None,
                variant: None,
            });
            let mut output = AnalysisOutput::new(&id, "native");
            output.status = if level == EvidenceLevel::NotApplicable {
                "SKIP"
            } else {
                "PASS"
            }
            .into();
            output.observation.measurements.push(Measurement {
                evidence: level,
                ..Measurement::measured("applicability", 0.0)
            });
            outputs.insert(id, output);
        }
        run.checks = Some(checks);
        let projected = legacy_projection(&run, &outputs, &ProjectionContext::default()).unwrap();
        assert_eq!(projected.as_value()["results"][0]["evidence"], expected);
    }
}
#[test]
fn original_full_pipeline_fingerprints_inventory_and_native_replacement_are_preserved() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-pipeline.json"
    ))
    .unwrap();
    for case in corpus["cases"].as_array().unwrap() {
        let mut run = RunResult::from_json(include_str!(
            "../../../tests/fixtures/ici-next/run-success.json"
        ))
        .unwrap();
        run.schema_version = 2;
        run.checks = Some(vec![CheckRecord {
            task_id: "task".into(),
            check_id: case["check_id"].as_str().unwrap().into(),
            component_id: "tool-a".into(),
            language: if case["check_id"].as_str().unwrap().starts_with("cpp.") {
                "cpp"
            } else {
                "python"
            }
            .into(),
            scope_digest: format!("sha256:{}", "a".repeat(64)),
            state: TaskState::Succeeded,
            evidence_complete: true,
            analysis_unit_id: None,
            variant: None,
        }]);
        let mut output = AnalysisOutput::new("task", "frozen");
        output.status = case["status"].as_str().unwrap().into();
        output.targets =
            serde_json::from_value::<Vec<InspectionTarget>>(case["targets"].clone()).unwrap();
        if case["evidence"] == "ESTIMATED" {
            output.observation.measurements.push(Measurement {
                evidence: EvidenceLevel::Estimated,
                ..Measurement::measured("source-rule-estimate", 1.0)
            });
        }
        output.extra = json!({"finding_details":{}});
        for native in case["explicit"].as_array().unwrap() {
            let mut finding: Finding = serde_json::from_value(native["modern"].clone()).unwrap();
            finding.task_id = Some("task".into());
            finding.component_id = Some("tool-a".into());
            output.extra["finding_details"][finding.fingerprint.clone()] =
                native["details"].clone();
            run.findings.push(finding);
        }
        for target in output
            .targets
            .iter()
            .filter(|target| target.status != "PASS" && target.status != "SKIP")
        {
            if run.findings.iter().any(|finding| {
                finding.primary_location.path == target.file_path
                    && finding.primary_location.start_line == target.start_line
                    && finding.message == target.message
            }) {
                continue;
            }
            let mut finding = Finding::new(
                "modern.target",
                "placeholder",
                ici_domain::SourceSpan::line(&target.file_path, target.start_line),
                "native",
            );
            finding.message = target.message.clone();
            finding.fingerprint = format!("modern-{}", target.start_line);
            finding.task_id = Some("task".into());
            run.findings.push(finding);
        }
        let context = ProjectionContext {
            required_checks: BTreeMap::from([("task".into(), true)]),
            ..Default::default()
        };
        let projected =
            legacy_projection(&run, &BTreeMap::from([("task".into(), output)]), &context).unwrap();
        assert_eq!(
            projected.as_value()["results"][0]["findings"],
            case["expected_findings"],
            "{}",
            case["name"]
        );
        let expected = case["targets"].as_array().unwrap().len();
        assert_eq!(
            projected.as_value()["results"][0]["targets"]
                .as_array()
                .unwrap()
                .len(),
            expected
        );
        assert_eq!(projected.as_value()["native_run"], run.to_value().unwrap());
    }
}

#[test]
fn unknown_configuration_closure_does_not_erase_successful_tool_evidence() {
    for status in ["PASS", "FAIL"] {
        let mut run = RunResult::from_json(include_str!(
            "../../../tests/fixtures/ici-next/run-success.json"
        ))
        .unwrap();
        run.schema_version = 2;
        run.execution.required_complete = false;
        run.gate.selected = ici_domain::GateVerdict::Incomplete;
        run.gate.workspace = ici_domain::GateVerdict::Incomplete;
        run.gate.reasons = vec!["Configuration closure was not proven".into()];
        run.checks = Some(vec![CheckRecord {
            task_id: "task".into(),
            check_id: "python.lint".into(),
            component_id: "tool-a".into(),
            language: "python".into(),
            scope_digest: format!("sha256:{}", "a".repeat(64)),
            state: TaskState::Succeeded,
            evidence_complete: false,
            analysis_unit_id: None,
            variant: None,
        }]);
        let mut output = AnalysisOutput::new("task", "ruff");
        output.status = status.into();
        output.summary = "Actual external tool completed".into();
        output
            .targets
            .push(InspectionTarget::at("src/inspected.py", 1));
        let projected = legacy_projection(
            &run,
            &BTreeMap::from([("task".into(), output)]),
            &ProjectionContext {
                required_checks: BTreeMap::from([("task".into(), true)]),
                ..Default::default()
            },
        )
        .unwrap();
        let value = projected.as_value();
        assert_eq!(value["results"][0]["status"], status);
        assert_eq!(value["results"][0]["evidence"], "MEASURED");
        assert_eq!(
            value["results"][0]["extra"]["native_checks"][0]["evidence_complete"],
            false
        );
        assert_eq!(value["results"][0]["findings"][0]["severity"], "info");
        assert_eq!(value["native_run"]["gate"]["selected"], "INCOMPLETE");
        assert_eq!(value["suite_status"], "ERROR");
    }
}
