//! Frozen next-path C++ coverage over declared suites (`application/gates.gate_cpp`,
//! `application/testing.plan_cpp_coverage`, `adapters/providers/gcov.py`) against the native
//! next path: `tests/rust-parity/cpp/capture_next_cpp_coverage.py` captured whole frozen
//! `ici next plan --json` and `ici next verify --json` documents over fake QTest/ctest suites
//! and a fake `gcov` (shell scripts; the gcov one writes a fixed gzip report with `printf`), so
//! the replay needs no compiler, CMake, Qt or gcov and runs the same bytes the capture ran.
//!
//! Compared in full; every allowance is named in `verify_differences`/`plan_differences`. The
//! per-suite `cpp.test` tasks fold into native's one check with frozen-id steps (decisions.md
//! "next `cpp.diagnostics`·`cpp.test` 실행 단위"), so frozen's coverage edges to every suite
//! task are native's one edge to `app.cpp.test` ("next `cpp.binary-compat`·`cpp.coverage` 실행
//! 단위").
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-cpp-coverage-oracle.json"
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

/// Native `ici next <command> --json` for both commands with PATH pinned to the case's fake
/// tool directory (`<project>/tools`), HOME and the cache isolated, exactly like the capture.
fn run(case: &Value) -> BTreeMap<&'static str, (i32, Value)> {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
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
    let mut captured = BTreeMap::new();
    for command in ["plan", "verify"] {
        let invocation = registry::parse(["ici", "next", command, "--json"]).unwrap();
        let output = commands::dispatch(&invocation, &project, &environment).unwrap();
        let text = output
            .stdout
            .replace(&root, "{root}")
            .replace(&work.path().display().to_string(), "{work}");
        captured.insert(
            command,
            (
                output.exit_code,
                serde_json::from_str(&text).unwrap_or_else(|error| panic!("{error}: {text}")),
            ),
        );
    }
    captured
}

const DYNAMIC_NOTE: &str = "dynamic evidence is never reused from a previous run";

fn suite(id: &str) -> bool {
    id.starts_with("app.cpp.test.")
}

fn fold(id: &Value) -> Value {
    let id = id.as_str().unwrap();
    json!(if suite(id) { "app.cpp.test" } else { id })
}

fn folded_check(frozen: &[&Value]) -> Value {
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

/// Folds frozen's per-suite records of one list (`key` names the id field) into one record.
fn fold_records(records: &[Value], key: &str, fold_one: impl Fn(&[&Value]) -> Value) -> Vec<Value> {
    let suites: Vec<&Value> = records
        .iter()
        .filter(|record| suite(record[key].as_str().unwrap()))
        .collect();
    let mut folded = vec![];
    for record in records {
        if !suite(record[key].as_str().unwrap()) {
            folded.push(record.clone());
        } else if std::ptr::eq(record, suites[0]) {
            folded.push(fold_one(&suites));
        }
    }
    folded
}

/// Verify: run identity (run id, producer, identity, scope digests) is incidental; suite ids
/// fold (decisions.md "next `cpp.binary-compat`·`cpp.coverage` 실행 단위"); `native_rule_id`
/// equal to the rule id and the run-wide dynamic evidence note are native additions (as
/// next_cpp_suites.rs); a repeated limitation is listed once ("next 실행의 run limitation
/// 중복"). The verify-side coverage verdict and reasons equal frozen, including a suite that
/// cannot run ("실행할 수 없는 suite의 next coverage 차단 시점"). Everything else is equal.
fn verify_differences(frozen: &Value, native: &Value) -> Vec<String> {
    let mut found = vec![];
    for key in [
        "schema_id",
        "schema_version",
        "scope",
        "publication",
        "gate",
        "metrics",
    ] {
        if frozen[key] != native[key] {
            found.push(format!("{key}: {} vs {}", native[key], frozen[key]));
        }
    }
    let strip = |checks: Vec<Value>| -> Vec<Value> {
        checks
            .into_iter()
            .map(|mut check| {
                check.as_object_mut().unwrap().remove("scope_digest");
                check
            })
            .collect()
    };
    let expected = strip(fold_records(
        frozen["checks"].as_array().unwrap(),
        "task_id",
        folded_check,
    ));
    let actual = strip(native["checks"].as_array().unwrap().clone());
    if actual != expected {
        found.push(format!("checks {} vs {}", json!(actual), json!(expected)));
    }
    for key in ["blocked_task_ids", "failed_task_ids"] {
        let mut expected: Vec<Value> = vec![];
        for id in frozen["execution"][key].as_array().unwrap() {
            if !expected.contains(&fold(id)) {
                expected.push(fold(id));
            }
        }
        if native["execution"][key] != json!(expected) {
            found.push(format!("execution.{key}: {}", native["execution"][key]));
        }
    }
    for key in ["required_complete", "cancelled", "reused_task_ids"] {
        if native["execution"][key] != frozen["execution"][key] {
            found.push(format!("execution.{key}"));
        }
    }
    let findings = |document: &Value| -> Vec<Value> {
        document["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                let mut finding = finding.clone();
                finding["task_id"] = fold(&finding["task_id"]);
                let object = finding.as_object_mut().unwrap();
                if object.get("native_rule_id") == object.get("rule_id") {
                    object.remove("native_rule_id");
                }
                finding
            })
            .collect()
    };
    if findings(native) != findings(frozen) {
        found.push(format!(
            "findings {} vs {}",
            json!(findings(native)),
            json!(findings(frozen))
        ));
    }
    let actual: Vec<&Value> = native["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| *item != DYNAMIC_NOTE)
        .collect();
    let mut expected: Vec<&Value> = vec![];
    for limitation in frozen["limitations"].as_array().unwrap() {
        if !expected.contains(&limitation) {
            expected.push(limitation);
        }
    }
    if actual != expected {
        found.push(format!(
            "limitations {} vs {}",
            json!(actual),
            json!(expected)
        ));
    }
    found
}

/// Plan: suite entries fold into one `cpp.test` entry (next_cpp_suites.rs
/// `folded_plan_entry`), steps compared by id and argv. Approved differences (decisions.md):
/// the gcov notes argument `-x` where frozen passes `-p` and the owned gcov directory
/// (`work_dirs`; frozen `.ici/cache/gcov/<component>`) ("next C++ coverage의 gcov report 이름과
/// 위치"); the static preparation preflight note ("`next plan`의 static preparation
/// preflight"); native `graph.shared` entries for needs-edges ("next 실행 graph의 공유 표기와
/// 동일 task"); a coverage read whose suite cannot run blocked in the plan already ("실행할 수
/// 없는 suite의 next coverage 차단 시점").
fn plan_differences(frozen: &Value, native: &Value) -> Vec<String> {
    let mut found = vec![];
    for key in [
        "schema_id",
        "schema_version",
        "root",
        "scope",
        "builds",
        "coverage_gaps",
    ] {
        if frozen[key] != native[key] {
            found.push(format!("{key}: {} vs {}", native[key], frozen[key]));
        }
    }
    let actual: Vec<&Value> = native["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| {
            !item
                .as_str()
                .unwrap()
                .ends_with("(static preparation preflight; nothing was built)")
        })
        .collect();
    if actual
        != frozen["limitations"]
            .as_array()
            .unwrap()
            .iter()
            .collect::<Vec<_>>()
    {
        found.push(format!("limitations {}", json!(actual)));
    }
    let fold_entry = |entries: &[&Value]| -> Value {
        let runnable: Vec<&&Value> = entries
            .iter()
            .filter(|entry| entry["kind"] == "process")
            .collect();
        let blocked: Vec<Value> = entries
            .iter()
            .filter(|entry| entry["kind"] == "blocked")
            .map(|entry| json!({"task_id": entry["task_id"], "reason": entry["blocked"]}))
            .collect();
        let mut entry = json!({"task_id": "app.cpp.test", "check": "cpp.test"});
        if runnable.is_empty() {
            entry["kind"] = json!("blocked");
            entry["blocked"] = blocked[0]["reason"].clone();
        } else {
            entry["kind"] = json!("process");
            entry["argv"] = runnable[0]["argv"].clone();
            entry["steps"] = json!(
                runnable
                    .iter()
                    .map(|task| json!({"id": task["task_id"], "argv": task["argv"]}))
                    .collect::<Vec<_>>()
            );
        }
        if !blocked.is_empty() {
            entry["blocked_steps"] = json!(blocked);
        }
        entry
    };
    let mut expected = fold_records(
        frozen["plans"][0]["checks"].as_array().unwrap(),
        "task_id",
        fold_entry,
    );
    // Approved difference ("실행할 수 없는 suite의 next coverage 차단 시점"): a suite that
    // cannot run already blocks the coverage read in the native plan, with the reason frozen's
    // run gives it ("prerequisite <suite> did not run"); frozen plans list it as a process and
    // blocks it only when the graph runs.
    let first_blocked = frozen["plans"][0]["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|entry| entry["check"] == "cpp.test" && entry["kind"] == "blocked")
        .map(|entry| entry["task_id"].as_str().unwrap().to_owned());
    for entry in &mut expected {
        if entry["check"] == "cpp.coverage"
            && entry["kind"] == "process"
            && let Some(suite) = &first_blocked
        {
            *entry = json!({
                "task_id": entry["task_id"],
                "check": "cpp.coverage",
                "kind": "blocked",
                "blocked": format!("prerequisite {suite} did not run"),
            });
        }
        let object = entry.as_object_mut().unwrap();
        if object["check"] == "cpp.coverage" && object["kind"] == "process" {
            object.remove("work_dirs");
            let mut argv = object["argv"].clone();
            assert_eq!(argv[3], "-p");
            argv[3] = json!("-x");
            object.insert("argv".into(), argv.clone());
            object.insert(
                "steps".into(),
                json!([{"id": "app.cpp.coverage.report", "argv": argv}]),
            );
        }
    }
    let actual: Vec<Value> = native["plans"][0]["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| {
            let mut entry = entry.clone();
            let object = entry.as_object_mut().unwrap();
            object.remove("work_dirs");
            if let Some(steps) = object.get_mut("steps") {
                *steps = json!(
                    steps
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|step| json!({"id": step["id"], "argv": step["argv"]}))
                        .collect::<Vec<_>>()
                );
            }
            entry
        })
        .collect();
    if actual != expected {
        found.push(format!(
            "plan checks {} vs {}",
            json!(actual),
            json!(expected)
        ));
    }
    let mut edges = serde_json::Map::new();
    for (consumer, dependencies) in frozen["graph"]["edges"].as_object().unwrap() {
        let mut folded: Vec<Value> = vec![];
        for dependency in dependencies.as_array().unwrap() {
            if !folded.contains(&fold(dependency)) {
                folded.push(fold(dependency));
            }
        }
        edges.insert(consumer.clone(), json!(folded));
    }
    if native["graph"]["edges"] != Value::Object(edges.clone()) {
        found.push(format!(
            "graph.edges {} vs {}",
            native["graph"]["edges"],
            json!(edges)
        ));
    }
    if native["graph"]["mutating"] != frozen["graph"]["mutating"] {
        found.push("graph.mutating".into());
    }
    let mut shared = native["graph"]["shared"].as_object().unwrap().clone();
    shared.retain(|dependency, consumers| {
        !(edges
            .values()
            .any(|needs| needs.as_array().unwrap().contains(&json!(dependency)))
            && consumers.as_array().unwrap().contains(&json!(dependency)))
    });
    if Value::Object(shared.clone()) != frozen["graph"]["shared"] {
        found.push(format!("graph.shared {}", json!(shared)));
    }
    found
}

/// Every frozen case, both documents and exit codes. The replay only needs `/bin/sh`.
#[test]
fn next_cpp_coverage_matches_frozen_plan_and_verify_documents_except_named_allowances() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut mismatches = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let native = run(case);
        for command in ["plan", "verify"] {
            let frozen = &case["frozen"][command];
            let (exit, document) = &native[command];
            let mut found = vec![];
            if json!(exit) != frozen["exit_code"] {
                found.push(format!("exit {exit} vs {}", frozen["exit_code"]));
            }
            found.extend(if command == "plan" {
                plan_differences(&frozen["document"], document)
            } else {
                verify_differences(&frozen["document"], document)
            });
            if !found.is_empty() {
                mismatches.push(format!("== {name} {command}\n{}", found.join("\n")));
            }
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
