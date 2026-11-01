//! Legacy lint C++ syntax global time budget (frozen `_cpp_lint._GLOBAL_TIMEOUT_SECONDS`,
//! `_run_exact` and `_run_fallback`) against the frozen CLI with that constant replaced
//! (`tests/rust-parity/cpp/capture_lint_budget.py`): a budget spent before the first unit and one
//! spent while a fake `g++` sleeps on `slow.cpp`. The native 600 s budget is replaced through the
//! process-local test hook; nothing on the command line reaches it.
use ici_cli::{commands, cpp::set_legacy_syntax_budget_for_tests, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, time::Duration};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
];

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn run(oracle: &Value, case: &Value) -> Value {
    use std::os::unix::fs::PermissionsExt;
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    let bin = work.path().join("bin");
    fs::create_dir_all(&bin).unwrap();
    fs::write(bin.join("g++"), oracle["fake"].as_str().unwrap()).unwrap();
    fs::set_permissions(bin.join("g++"), fs::Permissions::from_mode(0o755)).unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/lint-branches"),
        &root,
    );
    fs::write(root.join("ici.toml"), oracle["base"].as_str().unwrap()).unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(
            path,
            text.as_str()
                .unwrap()
                .replace("{root}", &root.display().to_string()),
        )
        .unwrap();
    }
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([(
        "PATH".to_string(),
        format!("{}:/usr/bin:/bin", bin.display()),
    )]);
    set_legacy_syntax_budget_for_tests(Some(Duration::from_secs_f64(
        case["budget_seconds"].as_f64().unwrap(),
    )));
    let output = commands::dispatch(&invocation, &root, &environment);
    set_legacy_syntax_budget_for_tests(None);
    let output = output.unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.join(format!("{command}_report.json"))).unwrap())
            .unwrap();
    let engine = if command == "verify" {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "lint")
            .unwrap()
            .clone()
    } else {
        report.clone()
    };
    let targets: Vec<Value> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            Value::Object(
                FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), target[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    let mut captured = json!({
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": targets,
        "findings": engine["findings"],
    });
    if command != "verify" {
        captured["exit_code"] = json!(output.exit_code);
    }
    let text = serde_json::to_string(&captured)
        .unwrap()
        .replace(&root.display().to_string(), "{root}")
        .replace(&bin.display().to_string(), "{bin}");
    serde_json::from_str(&text).unwrap()
}

/// Positive: a budget spent while `slow.cpp` runs times that unit out at the remaining budget
/// (not the 120 s unit limit) and stops before `warn1.cpp` ("left 1 unit(s) unexamined").
/// Failure: a budget spent before the first unit replays nothing ("left 8 unit(s)").
/// Negative: the 600 s budget is not reached by the other lint tests in this crate, whose
/// frozen outcomes carry no budget target (`legacy_lint_branches.rs`, `legacy_lint.rs`).
#[test]
#[ignore = "explicit actual g++ behind a fake g++ driver: /usr/bin/g++ must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone; the mid-run cases wait out a 20 s budget"]
fn legacy_cpp_lint_global_time_budget_matches_frozen_targets_summaries_and_evidence() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-budget-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let version = std::process::Command::new("/usr/bin/g++")
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str(),
        "the budget oracle was captured with another g++"
    );
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 4);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let actual = run(&oracle, case);
        if actual != case["frozen"] {
            eprintln!(
                "== {name}\nactual: {}\nfrozen: {}",
                serde_json::to_string(&actual).unwrap(),
                serde_json::to_string(&case["frozen"]).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    for name in ["fallback-budget-mid", "exact-budget-mid"] {
        let targets = cases[name]["frozen"]["targets"].as_array().unwrap();
        assert!(targets.iter().any(|target| target["message"]
            == "C++ syntax global time budget left 1 unit(s) unexamined"
            && target["file_path"] == "src/warn1.cpp"));
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
