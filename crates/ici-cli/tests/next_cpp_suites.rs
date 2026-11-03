//! Frozen per-suite C++ test planning (`application/testing.expand_cpp_tests`) against the
//! native next path: `tests/rust-parity/cpp/capture_next_cpp_suites.py` captured whole frozen
//! `ici next verify --json` documents over fake `ctest`/QTest executables (shell scripts that
//! print fixed suite output), so the replay needs no compiler, CMake or Qt and runs the same
//! bytes the capture ran.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-cpp-suites-oracle.json"
    ))
    .unwrap()
}

fn write(path: &Path, text: &str, executable: bool) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, text).unwrap();
    if executable {
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }
}

/// Native `ici next verify --json` with PATH pinned to the case's fake tool directory
/// (`<project>/tools`), HOME and the cache isolated, exactly like the capture.
fn run(case: &Value) -> (i32, Value) {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
    // Inside the workspace like the capture, so frozen `process_fingerprint` spells the tool
    // `${workspace}/tools/ctest` on both sides.
    let bin = project.join("tools");
    fs::create_dir_all(&bin).unwrap();
    let root = project.display().to_string();
    for (name, text) in case["files"].as_object().unwrap() {
        write(&project.join(name), text.as_str().unwrap(), false);
    }
    for (name, text) in case["executables"].as_object().unwrap() {
        let text = text.as_str().unwrap().replace("{root}", &root);
        write(&project.join(name), &text, true);
    }
    for (name, text) in case["tools"].as_object().unwrap() {
        let text = text.as_str().unwrap().replace("{root}", &root);
        write(&bin.join(name), &text, true);
    }
    write(
        &project.join("ici.toml"),
        case["config"].as_str().unwrap(),
        false,
    );
    let environment = BTreeMap::from([
        ("PATH".to_string(), bin.display().to_string()),
        ("HOME".to_string(), work.path().display().to_string()),
        (
            "XDG_CACHE_HOME".to_string(),
            work.path().join("cache").display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "next", "verify", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &project, &environment).unwrap();
    let text = output
        .stdout
        .replace(&root, "{root}")
        .replace(&work.path().display().to_string(), "{work}");
    (
        output.exit_code,
        serde_json::from_str(&text).unwrap_or_else(|error| panic!("{error}: {text}")),
    )
}

/// Frozen's per-suite `cpp.test` records folded into native's one check (decisions.md "next
/// `cpp.diagnostics`·`cpp.test` 실행 단위"): BLOCKED when a suite is blocked, else FAILED when
/// one failed, else SUCCEEDED; complete only when every suite is.
fn folded_check(frozen: &[Value]) -> Value {
    let states: Vec<&str> = frozen
        .iter()
        .map(|check| check["state"].as_str().unwrap())
        .collect();
    let state = ["BLOCKED", "FAILED"]
        .into_iter()
        .find(|state| states.contains(state))
        .unwrap_or("SUCCEEDED");
    let mut check = frozen[0].clone();
    check["task_id"] = json!("app.cpp.test");
    check["state"] = json!(state);
    check["evidence_complete"] = json!(frozen.iter().all(|item| item["evidence_complete"] == true));
    check
}

/// The native document's difference from the frozen one, path by path. Every allowance is
/// named here: run identity (run id, producer, identity digests, check scope digests) is
/// incidental; the suite task ids fold into the check id by the 실행 단위 row; the native
/// `native_rule_id` finding key and the run-wide "dynamic evidence is never reused" limitation
/// are native additions. Everything else must be equal.
fn differences(name: &str, frozen: &Value, native: &Value) -> Vec<String> {
    let mut found = vec![];
    let suite = |id: &str| id.starts_with("app.cpp.test.");
    let fold = |id: &Value| -> Value {
        let id = id.as_str().unwrap();
        json!(if suite(id) { "app.cpp.test" } else { id })
    };
    let keys = |document: &Value| -> Vec<String> {
        document.as_object().unwrap().keys().cloned().collect()
    };
    if keys(frozen) != keys(native) {
        found.push(format!("keys {:?} vs {:?}", keys(native), keys(frozen)));
    }
    for key in ["schema_id", "schema_version", "scope", "publication"] {
        if frozen[key] != native[key] {
            found.push(format!("{key}: {} vs {}", native[key], frozen[key]));
        }
    }
    // Checks: the suite records fold into one; the scope digest is a native identity.
    let strip = |check: &Value| {
        let mut check = check.clone();
        check.as_object_mut().unwrap().remove("scope_digest");
        check
    };
    let frozen_checks: Vec<Value> = frozen["checks"].as_array().unwrap().clone();
    let suites: Vec<Value> = frozen_checks
        .iter()
        .filter(|check| suite(check["task_id"].as_str().unwrap()))
        .cloned()
        .collect();
    let mut expected: Vec<Value> = frozen_checks
        .iter()
        .filter(|check| !suite(check["task_id"].as_str().unwrap()))
        .map(strip)
        .collect();
    expected.insert(0, strip(&folded_check(&suites)));
    let actual: Vec<Value> = native["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(strip)
        .collect();
    if actual != expected {
        found.push(format!("checks {} vs {}", json!(actual), json!(expected)));
    }
    // Execution: suite ids fold into the check id.
    for key in ["blocked_task_ids", "failed_task_ids"] {
        let mut expected: Vec<Value> = vec![];
        for id in frozen["execution"][key].as_array().unwrap() {
            let id = fold(id);
            if !expected.contains(&id) {
                expected.push(id);
            }
        }
        if native["execution"][key] != json!(expected) {
            found.push(format!(
                "execution.{key}: {} vs {}",
                native["execution"][key],
                json!(expected)
            ));
        }
    }
    for key in ["required_complete", "cancelled", "reused_task_ids"] {
        if native["execution"][key] != frozen["execution"][key] {
            found.push(format!("execution.{key}"));
        }
    }
    // Gate: one reason for the folded check, the first suite reason (frozen's first limitation
    // rule); every other reason equal.
    let mut reasons: Vec<Value> = vec![];
    let mut folded = false;
    for reason in frozen["gate"]["reasons"].as_array().unwrap() {
        let text = reason.as_str().unwrap();
        match text.split_once(": ") {
            Some((task, rest)) if suite(task) => {
                if !folded {
                    reasons.push(json!(format!("app.cpp.test: {rest}")));
                    folded = true;
                }
            }
            _ => reasons.push(reason.clone()),
        }
    }
    let mut gate = frozen["gate"].clone();
    gate["reasons"] = json!(reasons);
    // `gate.workspace` is the application-level verdict (frozen NOT_EVALUATED without
    // --require-full, native the selected verdict of a complete full run): not a C++ suite
    // fact, recorded for the root on cpp.json `command:ici next verify`.
    let mut actual_gate = native["gate"].clone();
    actual_gate["workspace"] = gate["workspace"].clone();
    if actual_gate != gate {
        found.push(format!("gate {} vs {gate}", native["gate"]));
    }
    // Findings: every key, the task id folded; `native_rule_id` is the native rule spelling.
    let findings = |document: &Value, native: bool| -> Vec<Value> {
        document["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                let mut finding = finding.clone();
                finding["task_id"] = fold(&finding["task_id"]);
                if native {
                    let object = finding.as_object_mut().unwrap();
                    if object.get("native_rule_id") == object.get("rule_id") {
                        object.remove("native_rule_id");
                    }
                }
                finding
            })
            .collect()
    };
    if findings(native, true) != findings(frozen, false) {
        found.push(format!(
            "findings {} vs {}",
            json!(findings(native, true)),
            json!(findings(frozen, false))
        ));
    }
    if native["metrics"] != frozen["metrics"] {
        found.push(format!(
            "metrics {} vs {}",
            native["metrics"], frozen["metrics"]
        ));
    }
    // Limitations: frozen's list (deduplicated natively, assemble) plus the native run note.
    let mut expected: Vec<Value> = vec![];
    for limitation in frozen["limitations"].as_array().unwrap() {
        if !expected.contains(limitation) {
            expected.push(limitation.clone());
        }
    }
    let actual: Vec<Value> = native["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| *item != "dynamic evidence is never reused from a previous run")
        .cloned()
        .collect();
    if actual != expected {
        found.push(format!(
            "limitations {} vs {}",
            json!(actual),
            json!(expected)
        ));
    }
    if !found.is_empty() {
        found.insert(0, format!("== {name}"));
    }
    found
}

/// Recorded native difference (cpp.json `finding_site:src/ici/adapters/providers/cpptest.py:
/// 144:12`, R05): a CTest suite with a case that did not run is refused (FAILED, "N CTest
/// suites were not run") where frozen next keeps it as a limitation of a measured run. The
/// frozen document is rewritten to exactly that shape; findings and metrics stay frozen's.
fn refused_not_run(frozen: &mut Value, exit: &mut Value) {
    let document = &mut frozen["document"];
    let check = &mut document["checks"][0];
    check["state"] = json!("FAILED");
    check["evidence_complete"] = json!(false);
    let task = check["task_id"].clone();
    document["execution"]["failed_task_ids"] = json!([task]);
    document["execution"]["required_complete"] = json!(false);
    document["scope"]["kind"] = json!("partial");
    document["scope"]["full_required_satisfied"] = json!(false);
    document["gate"]["selected"] = json!("INCOMPLETE");
    document["gate"]["reasons"] = json!([format!(
        "{}: 1 CTest suites were not run",
        task.as_str().unwrap()
    )]);
    document["limitations"][0] = json!("1 CTest suites were not run");
    *exit = json!(3);
}

#[test]
fn next_cpp_suites_match_frozen_full_results_except_named_allowances() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut mismatches = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let (exit, native) = run(case);
        let mut frozen = case["frozen"].clone();
        let mut expected_exit = frozen["exit_code"].clone();
        if name == "ctest-fail" {
            assert_eq!(
                frozen["document"]["limitations"][0], "1 case(s) did not run: off",
                "the recorded difference applies to exactly this frozen limitation"
            );
            refused_not_run(&mut frozen, &mut expected_exit);
        }
        if json!(exit) != expected_exit {
            mismatches.push(format!("== {name}: exit {exit} vs {expected_exit}"));
        }
        mismatches.extend(differences(name, &frozen["document"], &native));
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
