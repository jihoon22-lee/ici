//! Legacy `ici verify` on the ici-owned quality-zoo C++ scenarios against frozen v0.13.0 runs
//! (`tests/rust-parity/cpp/zoo-cpp-oracle.json`, `capture_zoo_cpp.py`): exit code, suite status
//! and every engine's status, evidence, summary, targets and findings. The zoo expectations are
//! not read; the scenario trees are copied like the zoo runner does (no git checkout).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        if entry.file_name() == "expectations" {
            continue;
        }
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// Recorded difference: a legacy C++ syntax target of a clean unit is named by its compilation
/// configuration digest, which hashes the unit's directory and argv; native owned CMake shadows
/// live under `.ici/rust-v1/build/` (frozen `build/ici-cmake-build`), so the 12-digit suffix
/// differs while the target, its file and message are frozen's.
fn masked(name: &str) -> String {
    match name.strip_prefix("C++Syntax:") {
        Some(suffix) if suffix.len() == 12 && suffix.bytes().all(|b| b.is_ascii_hexdigit()) => {
            "C++Syntax:<configuration>".into()
        }
        _ => name.into(),
    }
}

/// OPEN (recorded on capability:legacy-command-config-result-projection): the legacy adapter
/// build and binary_compat engines carry no tool evidence natively, where frozen records the
/// backend selection, the `make clean`/`make build` processes and one `readelf binary
/// compatibility` process per artifact; their adapted findings therefore name no tool. The
/// test requires the native list to stay empty here (a fix must update this list) and compares
/// every other engine's tool-evidence names and return codes.
const OPEN_TOOL_EVIDENCE: [(&str, &str); 2] = [
    ("make-elf-integration", "build"),
    ("make-elf-integration", "binary_compat"),
];

/// Recorded (finding_site cycles.py:98:12 / compiler.rs): the native legacy cycle evidence names
/// each `compiler include trace` and its compiler but carries no return code.
fn evidence(engine: &str, records: &Value) -> Value {
    json!(
        records
            .as_array()
            .unwrap()
            .iter()
            .map(|record| {
                let returncode = if engine == "cycle" {
                    Value::Null
                } else {
                    record["returncode"].clone()
                };
                json!({"name": record["name"], "returncode": returncode})
            })
            .collect::<Vec<_>>()
    )
}

/// Frozen sorts an engine's findings by fingerprint, and the clean-unit syntax finding's
/// fingerprint hashes its configuration digest (see `masked`), so findings are compared in a
/// fingerprint-free order.
fn ordered(findings: &mut Value) {
    findings
        .as_array_mut()
        .unwrap()
        .sort_by_key(|finding| finding.to_string());
}

fn engines(report: &Value) -> Value {
    let mut engines = serde_json::Map::new();
    for engine in report["results"].as_array().unwrap() {
        let targets: Vec<Value> = engine["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({
                    "target_name": masked(target["target_name"].as_str().unwrap()),
                    "status": target["status"], "message": target["message"],
                    "file_path": target["file_path"], "start_line": target["start_line"],
                })
            })
            .collect();
        let mut findings: Value = engine["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                json!({
                    "rule_id": finding["rule_id"], "message": finding["message"],
                    "severity": finding["severity"], "confidence": finding["confidence"],
                    "tool_name": finding["tool_name"],
                    "tool_rule_id": masked(finding["tool_rule_id"].as_str().unwrap()),
                    "path": finding["primary_location"]["path"],
                    "start_line": finding["primary_location"]["start_line"],
                })
            })
            .collect::<Vec<_>>()
            .into();
        ordered(&mut findings);
        let name = engine["engine_name"].as_str().unwrap();
        engines.insert(
            name.to_owned(),
            json!({
                "status": engine["status"], "evidence": engine["evidence"],
                "summary": engine["summary"], "targets": targets, "findings": findings,
                "tool_evidence": evidence(name, &engine["tool_evidence"]),
            }),
        );
    }
    Value::Object(engines)
}

fn frozen_engines(name: &str, case: &Value) -> Value {
    let mut engines = case["engines"].clone();
    for (engine_name, engine) in engines.as_object_mut().unwrap() {
        for target in engine["targets"].as_array_mut().unwrap() {
            target["target_name"] = json!(masked(target["target_name"].as_str().unwrap()));
        }
        let open = OPEN_TOOL_EVIDENCE.contains(&(name, engine_name.as_str()));
        if open {
            assert!(!engine["tool_evidence"].as_array().unwrap().is_empty());
        }
        for finding in engine["findings"].as_array_mut().unwrap() {
            finding["tool_rule_id"] = json!(masked(finding["tool_rule_id"].as_str().unwrap()));
            if open {
                finding["tool_name"] = json!("");
            }
        }
        ordered(&mut engine["findings"]);
        engine["tool_evidence"] = if open {
            json!([])
        } else {
            evidence(engine_name, &engine["tool_evidence"])
        };
    }
    engines
}

/// Positive: a Make release build whose ELF output answers the `{artifact:app}` placeholder
/// (frozen keys manifest records by their shadow path) passes binary_compat and integration.
/// Failure: a compilation database with one non-object row is the frozen measured
/// `ici.compile-db.invalid-entry` FAIL beside the covered unit. Negative/partial: a CMake
/// context whose unbuilt unit includes a generated header keeps the other include traces, its
/// `CppIncludeUnresolved` WARN and the frozen `CppCycle:2`, and the compiler syntax replay runs
/// in the C locale (ASCII quotes) whatever the caller's locale.
#[test]
#[ignore = "explicit actual C++ build tools: /usr/bin/g++ (captured version), make, cmake and readelf"]
fn quality_zoo_cpp_scenarios_match_frozen_verify() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/zoo-cpp-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let gxx = Command::new("/usr/bin/g++")
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8(gxx.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str()
    );
    let zoo = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("quality-zoo/scenarios/cpp");
    let mut mismatches = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy(&zoo.join(name), &root);
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let argv: Vec<&str> = std::iter::once("ici")
            .chain(
                case["command"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|item| item.as_str().unwrap()),
            )
            .chain(["--report"])
            .collect();
        let invocation = registry::parse(argv).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let text = fs::read_to_string(root.join("verify_report.json"))
            .unwrap()
            .replace(&root.display().to_string(), "<root>");
        let report: Value = serde_json::from_str(&text).unwrap();
        let actual = json!({"exit_code": output.exit_code, "suite_status": report["suite_status"],
            "engines": engines(&report)});
        let expected = json!({"exit_code": case["exit_code"], "suite_status": case["suite_status"],
            "engines": frozen_engines(name, case)});
        if actual != expected {
            eprintln!("== {name}\nnative: {actual}\nfrozen: {expected}");
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
