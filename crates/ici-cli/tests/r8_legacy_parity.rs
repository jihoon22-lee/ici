//! R8 rehearsal blockers replayed against frozen v0.13.0 captures
//! (tests/rust-parity/runtime/r8-legacy-oracle.json, capture_r8_legacy.py). Every run is
//! in-process with an isolated HOME/XDG_CACHE_HOME and PATH=/usr/bin:/bin; no case needs a
//! project interpreter or an ICI_TEST_* tool.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/runtime/r8-legacy-oracle.json"
    ))
    .unwrap()
}
fn environment(base: &Path) -> BTreeMap<String, String> {
    for directory in ["home", "cache"] {
        fs::create_dir_all(base.join(directory)).unwrap();
    }
    BTreeMap::from([
        (
            "HOME".into(),
            base.join("home").to_string_lossy().into_owned(),
        ),
        (
            "XDG_CACHE_HOME".into(),
            base.join("cache").to_string_lossy().into_owned(),
        ),
        ("PATH".into(), "/usr/bin:/bin".into()),
        ("LANG".into(), "C.UTF-8".into()),
    ])
}
fn run(argv: &[&str], cwd: &Path, base: &Path) -> commands::CommandOutput {
    let invocation = registry::parse(std::iter::once("ici").chain(argv.iter().copied())).unwrap();
    commands::dispatch(&invocation, cwd, &environment(base)).unwrap()
}
fn strings(value: &Value) -> Vec<&str> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap())
        .collect()
}
fn engines(report: &Value) -> BTreeMap<String, Value> {
    report["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|engine| {
            let targets: Vec<Value> = engine["targets"]
                .as_array()
                .unwrap()
                .iter()
                .map(|target| {
                    json!({"target_name":target["target_name"],"status":target["status"],
                    "message":target["message"],"file_path":target["file_path"],
                    "start_line":target["start_line"]})
                })
                .collect();
            (
                engine["engine_name"].as_str().unwrap().to_owned(),
                json!({"status":engine["status"],"evidence":engine["evidence"],
                "required":engine["required"],"summary":engine["summary"],"targets":targets}),
            )
        })
        .collect()
}
fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), &target).unwrap();
        }
    }
}

/// Blocker 5: a configuration-less `ici verify` whose only Python file lies outside the default
/// `src` scope. Frozen reports compile_db, sanitize and (deep) thread_sanitize as not-applicable
/// SKIPs and the suite FAILs on "No tests collected" (exit 1); native once left those three as
/// required NOT_RUN engines, making the suite ERROR (exit 3).
#[test]
fn configless_verify_without_selected_sources_matches_frozen_engines_and_exit() {
    let oracle = oracle();
    for name in ["verify/root-file-standard", "verify/root-file-deep"] {
        let case = &oracle["cases"][name];
        let base = tempfile::tempdir().unwrap();
        let project = base.path().join("project");
        fs::create_dir_all(project.join(".git")).unwrap();
        for (path, text) in case["tree"].as_object().unwrap() {
            fs::write(project.join(path), text.as_str().unwrap()).unwrap();
        }
        let output = run(&strings(&case["argv"]), &project, base.path());
        // Positive: the frozen suite verdict and exit.
        assert_eq!(
            output.exit_code, case["exit_code"],
            "{name}: {}",
            output.stderr
        );
        let report: Value =
            serde_json::from_slice(&fs::read(project.join("verify_report.json")).unwrap()).unwrap();
        assert_eq!(report["suite_status"], case["suite_status"], "{name}");
        let native = engines(&report);
        let frozen: BTreeMap<String, Value> = case["engines"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        assert_eq!(
            native.keys().collect::<Vec<_>>(),
            frozen.keys().collect::<Vec<_>>(),
            "{name}"
        );
        for (engine, expected) in &frozen {
            let mut actual = native[engine].clone();
            if engine == "test" {
                // Recorded intended difference (runtime.json legacy-command row, validation
                // test.py:611/774): native never invents frozen's estimated `(est)` coverage, so
                // its "No tests collected" verdict is MEASURED with a coverage-free summary.
                assert_eq!(actual["evidence"], "MEASURED");
                assert_eq!(actual["summary"], "0/0 Tests Passed; No tests collected");
                actual["evidence"] = expected["evidence"].clone();
                actual["summary"] = expected["summary"].clone();
            }
            // Negative: every other engine field and target equals frozen.
            assert_eq!(&actual, expected, "{name}: {engine}");
        }
        // Failure boundary: no not-applicable engine is reported as missing evidence.
        assert!(
            report["results"]
                .as_array()
                .unwrap()
                .iter()
                .all(|engine| engine["evidence"] != "NOT_RUN"),
            "{name}"
        );
    }
}

/// Blockers 2/3: the quality-zoo `python.dead-private-function` scenario. Its legacy dead
/// findings carry frozen's empty tool fields (native once wrote the target name as
/// `tool_rule_id` and `ici.deadcode` as `tool_name`), and the suite carries frozen
/// `analysis_metadata` (policy digests equal frozen's) and a frozen-shaped capability snapshot.
#[test]
fn zoo_dead_scenario_findings_and_suite_metadata_match_frozen() {
    let oracle = oracle();
    let case = &oracle["cases"]["dead/zoo-private-function"];
    let base = tempfile::tempdir().unwrap();
    let project = base.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(case["scenario"].as_str().unwrap()),
        &project,
    );
    let output = run(&strings(&case["argv"]), &project, base.path());
    assert_eq!(output.exit_code, case["exit_code"], "{}", output.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(project.join("verify_report.json")).unwrap()).unwrap();
    assert_eq!(report["suite_status"], case["suite_status"]);
    let dead = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "dead")
        .unwrap();
    let findings: Vec<Value> = dead["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            let mut row = serde_json::Map::new();
            for key in case["findings"][0].as_object().unwrap().keys() {
                let value = match key.as_str() {
                    "path" | "start_line" | "label" => finding["primary_location"][key].clone(),
                    _ => finding[key].clone(),
                };
                row.insert(key.clone(), value);
            }
            Value::Object(row)
        })
        .collect();
    // Positive: every compared field of both findings, fingerprints included.
    assert_eq!(Value::Array(findings), case["findings"]);
    // Negative: the dead target's name stays the label, never the tool rule.
    assert!(
        dead["findings"]
            .as_array()
            .unwrap()
            .iter()
            .all(|finding| finding["tool_rule_id"] == "" && finding["tool_name"] == "")
    );
    let metadata = &report["analysis_metadata"];
    let frozen = &case["analysis_metadata"];
    assert_eq!(
        metadata.as_object().unwrap().keys().collect::<Vec<_>>(),
        strings(&case["analysis_metadata_keys"])
    );
    for key in ["fingerprint_version", "policy_digest", "tool_policy_digest"] {
        assert_eq!(metadata[key], frozen[key], "{key}");
    }
    // The producer is this build (frozen wrote 0.13.0).
    assert_eq!(metadata["producer_version"], env!("CARGO_PKG_VERSION"));
    let inventory = &report["capability_inventory"];
    assert_eq!(inventory["schema_version"], "ici.capabilities/v1");
    let tools = inventory["tools"].as_array().unwrap();
    assert_eq!(inventory["counts"]["total"], tools.len());
    // Failure boundary: every tool row names one frozen state consistent with its probe.
    for tool in tools {
        let state = tool["state"].as_str().unwrap();
        assert!(
            matches!(state, "ready" | "incomplete" | "unavailable"),
            "{tool}"
        );
        assert_eq!(state == "ready", tool["complete"] == true, "{tool}");
    }
}
