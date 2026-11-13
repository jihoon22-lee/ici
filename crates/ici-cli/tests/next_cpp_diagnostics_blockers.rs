//! `ici next verify --json` and `ici next plan --json` against frozen v0.13.0 captures
//! (`tests/rust-parity/cpp/next-diagnostics-blockers-oracle.json`,
//! `capture_next_diagnostics_blockers.py`) for every `application/gates.py` reason a
//! `cpp.diagnostics` translation unit is refused before replay. Frozen's per-TU tasks fold into
//! native's one check (decisions.md "next `cpp.diagnostics`·`cpp.test` 실행 단위"); the gate keeps
//! the step ids and reasons ("next C++ suite step의 gate 사유"). The `replay-*` cases cover what
//! frozen `CompilerDiagnosticsProvider.parse` and `COMPILER_CONTRACT` do after a unit ran (a GCC
//! wrapper beside the project): exit 3 fails the step, exit 1 without a diagnostic succeeds,
//! unparseable output and a killed compiler fail it; `outside-header-warning` locates a warning
//! in a header outside the workspace at `[external]`.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/cpp/next-diagnostics-blockers-oracle.json");

fn run(oracle: &Value, case: &Value) -> [(i32, Value); 2] {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    fs::create_dir_all(&base).unwrap();
    fs::create_dir(base.parent().unwrap().join(".hg")).unwrap();
    fs::write(base.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    for (path, text) in case["files"].as_object().unwrap() {
        let target = base.join(path);
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        fs::write(&target, text.as_str().unwrap()).unwrap();
        // The capture's GCC wrapper beside the project (`../tools/g++`) is executable.
        if path.starts_with("../tools/") {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&target, fs::Permissions::from_mode(0o755)).unwrap();
        }
    }
    let database = match &case["database"] {
        Value::String(text) => text.clone(),
        rows => rows.to_string(),
    };
    let root = base.display().to_string();
    fs::write(
        base.join("compile_commands.json"),
        database.replace("{root}", &root),
    )
    .unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("LC_ALL".to_owned(), "C".to_owned()),
        ("LANG".to_owned(), "C".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.parent().unwrap().display().to_string(),
        ),
    ]);
    ["plan", "verify"].map(|command| {
        let invocation = registry::parse(["ici", "next", command, "--json"]).unwrap();
        let output = commands::dispatch(&invocation, &base, &environment).unwrap();
        let text = output.stdout.replace(&root, "{root}");
        (output.exit_code, serde_json::from_str(&text).unwrap())
    })
}

const STEP: &str = "app.cpp.diagnostics.";

/// Frozen's per-unit records folded into one check: BLOCKED when a unit is blocked, else FAILED
/// when one failed, else SUCCEEDED; complete only when every unit is.
fn folded_check(frozen: &Value) -> Value {
    let units: Vec<&Value> = frozen["checks"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|check| {
            let id = check["task_id"].as_str().unwrap();
            id == "app.cpp.diagnostics" || id.starts_with(STEP)
        })
        .collect();
    let any = |state: &str| units.iter().any(|check| check["state"] == state);
    json!({
        "state": if any("BLOCKED") { "BLOCKED" } else if any("FAILED") { "FAILED" } else { "SUCCEEDED" },
        "evidence_complete": units.iter().all(|check| check["evidence_complete"] == true),
    })
}

fn native_check(native: &Value) -> Value {
    let check = native["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["task_id"] == "app.cpp.diagnostics")
        .unwrap();
    json!({"state": check["state"], "evidence_complete": check["evidence_complete"]})
}

fn folded_ids(frozen: &Value) -> Vec<Value> {
    let mut ids: Vec<Value> = vec![];
    for id in frozen.as_array().unwrap() {
        let folded = if id.as_str().unwrap().starts_with(STEP) {
            json!("app.cpp.diagnostics")
        } else {
            id.clone()
        };
        if !ids.contains(&folded) {
            ids.push(folded);
        }
    }
    ids
}

/// The planned steps' blocked reasons: frozen's blocked tasks, native's `blocked_steps` or the
/// check's own reason when nothing else of it can run.
fn plan_reasons(plan: &Value) -> BTreeMap<String, String> {
    let mut reasons = BTreeMap::new();
    for component in plan["plans"].as_array().unwrap() {
        for check in component["checks"].as_array().unwrap() {
            let id = check["task_id"].as_str().unwrap();
            if id != "app.cpp.diagnostics" && !id.starts_with(STEP) {
                continue;
            }
            if let Some(steps) = check["blocked_steps"].as_array() {
                for step in steps {
                    reasons.insert(
                        step["task_id"].as_str().unwrap().to_owned(),
                        step["reason"].as_str().unwrap().to_owned(),
                    );
                }
            } else if let Some(reason) = check["blocked"].as_str() {
                reasons.insert(id.to_owned(), reason.to_owned());
            }
        }
    }
    reasons
}

/// What native's step ids must reproduce in the plan: a check-level block over several steps
/// lists them instead, so only the step reasons frozen planned are compared.
fn differences(name: &str, case: &Value, plan: &Value, exit: i32, native: &Value) -> Vec<String> {
    let frozen = &case["verify"];
    let mut found = vec![];
    if exit != case["exit_code"] {
        found.push(format!("exit {exit} vs {}", case["exit_code"]));
    }
    if native_check(native) != folded_check(frozen) {
        found.push(format!(
            "check {} vs {}",
            native_check(native),
            folded_check(frozen)
        ));
    }
    if native["gate"] != frozen["gate"] {
        found.push(format!(
            "gate\n  native {}\n  frozen {}",
            native["gate"], frozen["gate"]
        ));
    }
    for key in ["blocked_task_ids", "failed_task_ids"] {
        if native["execution"][key].as_array().unwrap() != &folded_ids(&frozen["execution"][key]) {
            found.push(format!(
                "execution.{key}: {} vs {}",
                native["execution"][key], frozen["execution"][key]
            ));
        }
    }
    // decisions.md "next 실행의 run limitation 중복": each distinct sentence once.
    let mut distinct: Vec<Value> = vec![];
    for limitation in frozen["limitations"].as_array().unwrap() {
        if !distinct.contains(limitation) {
            distinct.push(limitation.clone());
        }
    }
    if native["limitations"].as_array().unwrap() != &distinct {
        found.push(format!(
            "limitations\n  native {}\n  frozen {distinct:?}",
            native["limitations"]
        ));
    }
    // A blocked unit keeps the other units' findings.
    let findings = |document: &Value| -> Vec<Value> {
        document["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|finding| {
                finding["task_id"]
                    .as_str()
                    .unwrap()
                    .starts_with("app.cpp.diagnostics")
            })
            .map(|finding| {
                json!({
                    "rule_id": finding["rule_id"], "message": finding["message"],
                    "severity": finding["severity"], "confidence": finding["confidence"],
                    "primary_location": finding["primary_location"],
                })
            })
            .collect()
    };
    if findings(native) != findings(frozen) {
        found.push(format!(
            "findings\n  native {:?}\n  frozen {:?}",
            findings(native),
            findings(frozen)
        ));
    }
    let frozen_reasons = plan_reasons(&case["plan"]);
    if plan_reasons(plan) != frozen_reasons {
        found.push(format!(
            "plan\n  native {:?}\n  frozen {:?}",
            plan_reasons(plan),
            frozen_reasons
        ));
    }
    found
        .into_iter()
        .map(|item| format!("{name}: {item}"))
        .collect()
}

/// The cpp30 replay-outcome cases (native behaviour not yet reviewed against them).
const REPLAY_OUTCOMES: [&str; 5] = [
    "replay-exit-three",
    "replay-exit-one-silent",
    "replay-unparseable",
    "replay-signal",
    "outside-header-warning",
];

#[test]
fn next_diagnostics_refuse_only_the_unit_frozen_refuses_with_its_step_id_and_reason() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let mut found = vec![];
    let mut compared = 0;
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if REPLAY_OUTCOMES.contains(&name.as_str()) {
            continue;
        }
        let [(_, plan), (exit, verify)] = run(&oracle, case);
        found.extend(differences(name, case, &plan, exit, &verify));
        compared += 1;
    }
    assert_eq!(compared, 35);
    assert!(found.is_empty(), "{}", found.join("\n"));
}

/// Frozen `CompilerDiagnosticsProvider.parse` and `COMPILER_CONTRACT` after a unit ran, each
/// beside a clean unit: exit 3 and a killed compiler fail the step, exit 1 without a diagnostic
/// succeeds, unparseable output fails it, a warning in a header outside the workspace is located
/// at `[external]`.
#[test]
fn next_diagnostics_replay_outcomes_match_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    // Independent literals from the frozen capture.
    let reasons = |case: &str| oracle["cases"][case]["verify"]["gate"]["reasons"].clone();
    assert!(reasons("replay-exit-three").as_array().unwrap().iter().any(|reason| reason
        == "app.cpp.diagnostics.src-exit3-cpp-1: app.cpp.diagnostics.src-exit3-cpp-1 failed with exit code 3"));
    assert!(reasons("replay-signal").as_array().unwrap().iter().any(|reason| reason
        == "app.cpp.diagnostics.src-signal-cpp-1: app.cpp.diagnostics.src-signal-cpp-1 signalled: killed by signal 9"));
    assert!(reasons("replay-unparseable").as_array().unwrap().iter().any(|reason| reason
        == "app.cpp.diagnostics.src-garbage-cpp-1: unrecognized compiler output line: 'this is not a compiler diagnostic'"));
    assert_eq!(
        oracle["cases"]["outside-header-warning"]["verify"]["findings"][0]["primary_location"]["path"],
        "[external]"
    );
    let mut found = vec![];
    for name in REPLAY_OUTCOMES {
        let case = &oracle["cases"][name];
        let [(_, plan), (exit, verify)] = run(&oracle, case);
        found.extend(differences(name, case, &plan, exit, &verify));
    }
    assert!(found.is_empty(), "{}", found.join("\n"));
}
