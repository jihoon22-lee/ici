//! `cpp.artifact` declared-output contract (`[builds.<id>] artifacts`) against the frozen
//! `ici next verify` oracle (`tests/rust-parity/cpp/capture_artifact_contract.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn config(case: &Value) -> String {
    let artifacts: Vec<String> = case["artifacts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|pattern| pattern.to_string())
        .collect();
    let off: String = ["compile", "diagnostics", "tidy", "test", "coverage"]
        .iter()
        .map(|check| format!("[checks.\"cpp.{check}\"]\nenabled = false\n"))
        .collect();
    format!(
        "schema_version = 1\n[workspace]\nname = \"product\"\n[builds.release]\nsystem = \"cmake\"\nproject = \"CMakeLists.txt\"\ndirectory = \"{}\"\nvariant = \"release\"\nartifacts = [{}]\n[[components]]\nid = \"app\"\nroot = \"app\"\nlanguages = [\"cpp\"]\nbuild = \"release\"\n{off}{}",
        case["directory"].as_str().unwrap(),
        artifacts.join(", "),
        case["extra"].as_str().unwrap()
    )
}

fn materialize(project: &Path, layout: &Value) {
    fs::write(project.parent().unwrap().join("outside"), "outside\n").unwrap();
    for entry in layout.as_array().unwrap() {
        let (kind, rest) = entry.as_str().unwrap().split_once(':').unwrap();
        match kind {
            "link" => {
                let (path, target) = rest.split_once("->").unwrap();
                fs::create_dir_all(project.join(path).parent().unwrap()).unwrap();
                std::os::unix::fs::symlink(target, project.join(path)).unwrap();
            }
            "dir" => fs::create_dir_all(project.join(rest)).unwrap(),
            _ => {
                fs::create_dir_all(project.join(rest).parent().unwrap()).unwrap();
                fs::write(project.join(rest), b"\x7fELF fake").unwrap();
            }
        }
    }
}

fn run(case: &Value) -> (i32, Option<Value>, String) {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
    fs::create_dir_all(project.join("app")).unwrap();
    fs::write(
        project.join("app/main.cpp"),
        include_str!("../../../parity-fixtures/cpp/artifact-contract/app/main.cpp"),
    )
    .unwrap();
    fs::write(project.join("ici.toml"), config(case)).unwrap();
    materialize(&project, &case["layout"]);
    let invocation = registry::parse(["ici", "next", "verify"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    match commands::dispatch(&invocation, &project, &environment) {
        Ok(output) => {
            let document: Value = serde_json::from_slice(
                &fs::read(project.join(".ici/rust-v1/result.json")).unwrap(),
            )
            .unwrap();
            (output.exit_code, Some(document), output.stdout)
        }
        Err(error) => (2, None, error.to_string()),
    }
}

#[test]
fn declared_artifact_contract_findings_match_frozen_messages_locations_and_raw_fingerprints() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/artifact-contract-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let (exit, document, output) = run(case);
        if name == "root-directory" {
            // Open config difference: frozen rejects `directory = "."` for a v1 build
            // (exit 2); Rust accepts it and locates contract findings at the declaration file.
            assert_eq!(frozen["exit_code"], 2);
            assert_eq!(exit, 0, "{output}");
            let document = document.unwrap();
            let missing: Vec<_> = document["findings"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|finding| finding["rule_id"] == "artifact.missing")
                .collect();
            assert_eq!(missing.len(), 1);
            assert_eq!(missing[0]["primary_location"]["path"], "ici.toml");
            continue;
        }
        let document = document.unwrap_or_else(|| panic!("{name}: {output}"));
        assert_eq!(json!(exit), frozen["exit_code"], "{name}: {output}");
        assert_eq!(document["gate"]["selected"], frozen["gate"], "{name}");
        let findings: Vec<Value> = document["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|finding| {
                finding["rule_id"]
                    .as_str()
                    .is_some_and(|rule| rule.starts_with("artifact."))
            })
            .map(|finding| {
                let mut projected = json!({});
                for key in [
                    "rule_id",
                    "message",
                    "primary_location",
                    "severity",
                    "confidence",
                    "evidence",
                    "provider",
                    "component_id",
                    "task_id",
                ] {
                    projected[key] = finding[key].clone();
                }
                projected
            })
            .collect();
        let expected: Vec<Value> = frozen["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                let mut finding = finding.clone();
                finding.as_object_mut().unwrap().remove("raw_fingerprint");
                // Recomputed by the capture with a unit frozen never attributes; the frozen
                // envelope fingerprint itself is compared by the identity test below.
                finding
                    .as_object_mut()
                    .unwrap()
                    .remove("fingerprint_with_unit");
                finding
            })
            .collect();
        assert_eq!(findings, expected, "{name}");
        let metrics: Vec<Value> = document["metrics"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|metric| {
                metric["name"]
                    .as_str()
                    .is_some_and(|metric| metric.starts_with("artifacts."))
            })
            .map(|metric| {
                json!({"name":metric["name"],"value":metric["value"],"numerator":metric["numerator"],"denominator":metric["denominator"]})
            })
            .collect();
        assert_eq!(json!(metrics), frozen["metrics"], "{name}");
    }
}

fn projected(items: &Value, keys: &[&str]) -> Vec<Value> {
    let mut projected: Vec<Value> = items
        .as_array()
        .unwrap()
        .iter()
        .map(|item| {
            Value::Object(
                keys.iter()
                    .map(|key| {
                        (
                            (*key).to_owned(),
                            item.get(*key).cloned().unwrap_or(Value::Null),
                        )
                    })
                    .collect(),
            )
        })
        .collect();
    projected.sort_by_key(|item| item.to_string());
    projected
}

/// Frozen `finding_identity.attribute` gives an internal check's findings no analysis unit or
/// variant (it has no task), so their envelope fingerprints are frozen's exactly; frozen
/// `check_scope` still names the component unit and that unit's build variant on the record
/// (`tests/rust-parity/reporting/capture_artifact_identity.py`).
#[test]
fn internal_check_findings_and_records_carry_the_frozen_unit_variant_and_envelope_fingerprint() {
    let golden: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/artifact-identity.json"
    ))
    .unwrap();
    let contract: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/artifact-contract-oracle.json"
    ))
    .unwrap();
    assert_eq!(golden["oracle"], contract["oracle"]);
    let cases = golden["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 2);
    for (name, frozen) in cases {
        let (_, document, output) = run(&contract["cases"][name]);
        let document = document.unwrap_or_else(|| panic!("{name}: {output}"));
        let finding_keys = [
            "rule_id",
            "fingerprint",
            "component_id",
            "analysis_unit_id",
            "variant",
            "task_id",
        ];
        assert_eq!(
            projected(&document["findings"], &finding_keys),
            projected(&frozen["findings"], &finding_keys),
            "{name}"
        );
        // Task ids are left out: frozen expands `cpp.binary-compat` into one task per declared
        // ELF artifact (`app.cpp.binary-compat.release-1`, `-2`), a cpp planning difference
        // outside this identity contract.
        let check_keys = ["check_id", "analysis_unit_id", "variant"];
        let mut native = projected(&document["checks"], &check_keys);
        native.dedup();
        let mut expected = projected(&frozen["checks"], &check_keys);
        expected.dedup();
        assert_eq!(native, expected, "{name}");
    }
}
