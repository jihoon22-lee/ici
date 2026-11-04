//! Frozen next-path C++ binary compatibility (`application/testing.expand_cpp_binary_compat`,
//! `adapters/providers/binarycompat.py`) against the native next path:
//! `tests/rust-parity/cpp/capture_next_binary_compat.py` captured whole frozen
//! `ici next plan --json` and `ici next verify --json` documents over a fake `readelf` (a shell
//! script printing a fixed transcript per artifact) and artifact files that start with the ELF
//! magic, so the replay needs no binutils or compiler and runs the same bytes the capture ran.
//!
//! Compared in full, with every allowance named in `verify_differences`/`plan_differences`:
//! frozen's per-ELF tasks `<task>.<build>-<n>` fold into native's one `cpp.binary-compat`
//! check whose steps keep those ids (decisions.md "next `cpp.binary-compat`·`cpp.coverage` 실행
//! 단위"); the gate is compared whole, so each failing step keeps its own
//! `<step id>: <reason>` ("next C++ suite step의 gate 사유").
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-binary-compat-oracle.json"
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

/// Native `ici next <command> --json` for each command, PATH pinned to the case's fake tool
/// directory (`<project>/tools`), HOME and the cache isolated, exactly like the capture.
fn run(case: &Value) -> (String, BTreeMap<&'static str, (i32, Value)>) {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
    let bin = project.join("tools");
    fs::create_dir_all(&bin).unwrap();
    let root = project.display().to_string();
    for key in ["files", "artifacts"] {
        for (name, text) in case[key].as_object().unwrap() {
            write(&project.join(name), text.as_str().unwrap(), false);
        }
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
    (root, captured)
}

/// The capture's project directory (`capture_next_binary_compat.py` `WORK / "project"`), which
/// frozen hashed into the identity of a finding whose message names a workspace path.
const CAPTURE_ROOT: &str = "/tmp/ici-cpp-next-binary-compat-capture/project";
const DYNAMIC_NOTE: &str = "dynamic evidence is never reused from a previous run";
const CHECK: &str = "app.cpp.binary-compat";

fn step(id: &str) -> bool {
    id.starts_with("app.cpp.binary-compat.")
}

fn fold(id: &Value) -> Value {
    let id = id.as_str().unwrap();
    json!(if step(id) { CHECK } else { id })
}

/// Frozen `core/findings.finding_fingerprint(rule, SourceLocation(path, 1))`.
fn raw_fingerprint(rule: &str, path: &str) -> String {
    ici_domain::sha256(
        ici_domain::canonical_json(&json!({
            "path": path,
            "region": {"end_column": null, "end_line": null, "start_column": null, "start_line": 1},
            "rule_id": rule,
            "symbol": "",
            "version": "ici-fingerprint/v1",
        }))
        .unwrap(),
    )
}

/// Frozen `application/finding_identity._fingerprint` of a binary-compat finding (one analysis
/// unit, no task variant) with `{root}` in its message spelled as `root`.
fn attributed(finding: &Value, root: &str) -> String {
    let path = finding["primary_location"]["path"].as_str().unwrap();
    let rule = finding["rule_id"].as_str().unwrap();
    let mut item = ici_domain::Finding::new(
        rule,
        finding["message"].as_str().unwrap().replace("{root}", root),
        ici_domain::SourceSpan::line(path, 1),
        finding["provider"].as_str().unwrap(),
    );
    item.fingerprint = raw_fingerprint(rule, path);
    item.component_id = Some("app".into());
    item.analysis_unit_id = Some("app.cpp".into());
    ici_domain::attributed_fingerprint(&item, &["app.cpp".to_string()]).unwrap()
}

/// Frozen's per-ELF `cpp.binary-compat` records folded into native's one check: BLOCKED when a
/// step is blocked, else FAILED when one failed, else SUCCEEDED; complete only when every step is.
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
    check["task_id"] = json!(CHECK);
    check["state"] = json!(state);
    check["evidence_complete"] = json!(frozen.iter().all(|item| item["evidence_complete"] == true));
    check
}

fn keys(document: &Value) -> Vec<String> {
    document.as_object().unwrap().keys().cloned().collect()
}

/// The verify document's difference from frozen. Allowances: run identity (run id, producer,
/// identity digests, check scope digests) is incidental; per-ELF records, finding task ids and
/// execution ids fold into the check id (decisions.md "next `cpp.binary-compat`·`cpp.coverage`
/// 실행 단위"); `native_rule_id` equal to the rule id and the run-wide "dynamic evidence is
/// never reused" note are native additions (as next_cpp_suites.rs); a repeated limitation is
/// listed once ("next 실행의 run limitation 중복"); a finding of a check frozen does not
/// require carries native's `advisory` tag ("비필수 next 검사 finding의 `advisory` tag"); a
/// finding whose message names a workspace path is hashed with the replay's root, so its
/// fingerprint is recomputed with the frozen formula on both roots (incidental). Everything
/// else must be equal.
fn verify_differences(name: &str, root: &str, frozen: &Value, native: &Value) -> Vec<String> {
    let mut found = vec![];
    if keys(frozen) != keys(native) {
        found.push(format!("keys {:?} vs {:?}", keys(native), keys(frozen)));
    }
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
    let strip = |check: &Value| {
        let mut check = check.clone();
        check.as_object_mut().unwrap().remove("scope_digest");
        check
    };
    let frozen_checks = frozen["checks"].as_array().unwrap();
    let steps: Vec<Value> = frozen_checks
        .iter()
        .filter(|check| step(check["task_id"].as_str().unwrap()))
        .cloned()
        .collect();
    let mut expected: Vec<Value> = vec![];
    for check in frozen_checks {
        if !step(check["task_id"].as_str().unwrap()) {
            expected.push(strip(check));
        } else if check == &steps[0] {
            expected.push(strip(&folded_check(&steps)));
        }
    }
    let actual: Vec<Value> = native["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(strip)
        .collect();
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
    let required = frozen["gate"]["selected"] != "PASS" || name.starts_with("required");
    let frozen_findings: Vec<Value> = frozen["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            let mut finding = finding.clone();
            finding["task_id"] = fold(&finding["task_id"]);
            let message = finding["message"].as_str().unwrap().to_owned();
            if finding["provider"] == "binary-compat" && message.contains("{root}") {
                assert_eq!(
                    finding["fingerprint"],
                    attributed(&finding, CAPTURE_ROOT),
                    "{name}: the frozen identity formula reproduces the capture"
                );
                finding["fingerprint"] = json!(attributed(&finding, root));
            }
            finding
        })
        .collect();
    let native_findings: Vec<Value> = native["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            let mut finding = finding.clone();
            let object = finding.as_object_mut().unwrap();
            if object.get("native_rule_id") == object.get("rule_id") {
                object.remove("native_rule_id");
            }
            if !required && object.get("tags") == Some(&json!(["advisory"])) {
                object.remove("tags");
            }
            finding
        })
        .collect();
    if native_findings != frozen_findings {
        found.push(format!(
            "findings {} vs {}",
            json!(native_findings),
            json!(frozen_findings)
        ));
    }
    let actual: Vec<&Value> = native["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| *item != DYNAMIC_NOTE)
        .collect();
    // Native `assemble` lists a repeated limitation once (two patterns matching one ELF).
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

/// The plan document's difference from frozen. Allowances: frozen's per-ELF plan entries fold
/// into native's one check entry, whose `argv` is the first step's and whose native `steps`
/// keep every frozen task id and argv in order (native `work_dirs` is the step model's owned
/// run directory); graph edges fold their keys the same way. Approved differences: the native
/// `next plan` static preparation preflight note (decisions.md "`next plan`의 static
/// preparation preflight"), native `graph.shared` entries for needs-edges (`cpp.artifact` → its
/// consumers) and frozen's shared process unit for two identical readelf tasks, where native
/// runs every step ("next 실행 graph의 공유 표기와 동일 task"). Everything else must be equal.
fn plan_differences(frozen: &Value, native: &Value) -> Vec<String> {
    let mut found = vec![];
    if keys(frozen) != keys(native) {
        found.push(format!("keys {:?} vs {:?}", keys(native), keys(frozen)));
    }
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
            let item = item.as_str().unwrap();
            !(item.contains(": declared release build cannot be prepared")
                && item.ends_with("(static preparation preflight; nothing was built)"))
        })
        .collect();
    let expected: Vec<&Value> = frozen["limitations"].as_array().unwrap().iter().collect();
    if actual != expected {
        found.push(format!(
            "limitations {} vs {}",
            json!(actual),
            json!(expected)
        ));
    }
    let plans = |document: &Value| -> Vec<(Value, Vec<Value>)> {
        document["plans"]
            .as_array()
            .unwrap()
            .iter()
            .map(|plan| {
                (
                    plan["component"].clone(),
                    plan["checks"].as_array().unwrap().clone(),
                )
            })
            .collect()
    };
    let (frozen_plans, native_plans) = (plans(frozen), plans(native));
    if frozen_plans.len() != native_plans.len() {
        found.push("plan components".into());
    }
    for ((component, frozen_checks), (native_component, native_checks)) in
        frozen_plans.iter().zip(&native_plans)
    {
        if component != native_component {
            found.push(format!("plan component {native_component} vs {component}"));
        }
        let tasks: Vec<&Value> = frozen_checks
            .iter()
            .filter(|entry| step(entry["task_id"].as_str().unwrap()))
            .collect();
        let mut expected: Vec<Value> = vec![];
        for entry in frozen_checks {
            if !step(entry["task_id"].as_str().unwrap()) {
                expected.push(entry.clone());
            } else if entry == tasks[0] {
                expected.push(json!({
                    "task_id": CHECK,
                    "check": "cpp.binary-compat",
                    "kind": "process",
                    "argv": tasks[0]["argv"],
                    "steps": tasks
                        .iter()
                        .map(|task| json!({"id": task["task_id"], "argv": task["argv"]}))
                        .collect::<Vec<_>>(),
                }));
            }
        }
        let actual: Vec<Value> = native_checks
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
    }
    let mut edges = serde_json::Map::new();
    for (consumer, dependencies) in frozen["graph"]["edges"].as_object().unwrap() {
        let key = fold(&json!(consumer)).as_str().unwrap().to_owned();
        edges.entry(key).or_insert_with(|| dependencies.clone());
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
    let mut frozen_shared = frozen["graph"]["shared"].as_object().unwrap().clone();
    frozen_shared.retain(|unit, consumers| {
        !(step(unit)
            && consumers
                .as_array()
                .unwrap()
                .iter()
                .all(|consumer| step(consumer.as_str().unwrap())))
    });
    if shared != frozen_shared {
        found.push(format!(
            "graph.shared {} vs {}",
            json!(shared),
            json!(frozen_shared)
        ));
    }
    found
}

/// Every frozen case, both documents, both exit codes. The replay only needs `/bin/sh`.
#[test]
fn next_binary_compat_matches_frozen_plan_and_verify_documents_except_named_allowances() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut mismatches = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let (root, native) = run(case);
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
                verify_differences(name, &root, &frozen["document"], document)
            });
            if !found.is_empty() {
                mismatches.push(format!("== {name} {command}\n{}", found.join("\n")));
            }
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
