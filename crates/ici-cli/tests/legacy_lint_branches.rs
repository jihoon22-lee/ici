//! Legacy lint C++ syntax execution branches (frozen `_cpp_lint._evaluate_process`, `_run_exact`
//! context/replay/budget errors) against the frozen CLI oracle
//! (`tests/rust-parity/cpp/capture_lint_branches.py`): a fake `g++` first on PATH kills, exits,
//! floods or sleeps per source name.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

#[path = "support/host_tools.rs"]
mod host_tools;

#[path = "support/lint_extra.rs"]
mod lint_extra;

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

fn run(oracle: &Value, case: &Value, system_path: &str, ruff: &str) -> Value {
    use std::os::unix::fs::PermissionsExt;
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    let bin = work.path().join("bin");
    fs::create_dir_all(&bin).unwrap();
    fs::write(bin.join("g++"), oracle["fake"].as_str().unwrap()).unwrap();
    fs::set_permissions(bin.join("g++"), fs::Permissions::from_mode(0o755)).unwrap();
    std::os::unix::fs::symlink(ruff, bin.join("ruff")).unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        &root,
    );
    // A configuration tail that starts with `[project]` replaces the C++ base.
    let config = case["config"].as_str().unwrap();
    let base = if config.starts_with("[project]") {
        ""
    } else {
        oracle["base"].as_str().unwrap()
    };
    fs::write(root.join("ici.toml"), format!("{base}{config}")).unwrap();
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
    // The work directory bounds VCS discovery (the project sits directly inside it).
    let environment = BTreeMap::from([
        (
            "PATH".to_string(),
            format!("{}:{system_path}", bin.display()),
        ),
        (
            "GIT_CEILING_DIRECTORIES".to_string(),
            work.path().display().to_string(),
        ),
    ]);
    let output = commands::dispatch(&invocation, &root, &environment).unwrap();
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
        "extra": engine["extra"],
        "tool_evidence": engine["tool_evidence"],
    });
    if command != "verify" {
        captured["exit_code"] = json!(output.exit_code);
    } else {
        let database = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "compile_db")
            .unwrap();
        captured["compile_db"] = json!({
            "status": database["status"],
            "summary": database["summary"],
            "targets": database["targets"]
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
                .collect::<Vec<_>>(),
        });
    }
    let text = serde_json::to_string(&captured)
        .unwrap()
        .replace(&root.display().to_string(), "{root}")
        .replace(&bin.display().to_string(), "{bin}");
    serde_json::from_str(&text).unwrap()
}

#[test]
#[ignore = "explicit actual g++ behind a fake g++ driver: /usr/bin/g++ must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone; ICI_TEST_RUFF (the captured Ruff); the fallback case waits out the frozen 120 s per-unit timeout"]
fn legacy_cpp_lint_execution_branches_match_frozen_targets_summaries_and_evidence() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-branches-oracle.json"
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
        "the branch oracle was captured with another g++"
    );
    replay(
        &oracle,
        "/usr/bin:/bin",
        &std::env::var("ICI_TEST_RUFF").expect("explicit ICI_TEST_RUFF"),
        false,
    );
}

/// The CI twin with the host's own g++ (any release, as on the GitHub runner) behind the same
/// fake driver and a mirror of `/usr/bin` and `/bin` without clang*/clazy* (the capture host had
/// none; `support/host_tools.rs`), and the repository `.venv` Ruff that `uv.lock` pins to the
/// captured release (asserted). Incidental masks (`host_view`), applied to both sides: only the
/// g++ version banner (`tool_evidence[].version` and the g++ findings' `tool_version`). Every
/// other field, including the fake driver's exits, signals and floods, stays compared. The
/// slow.cpp cases wait out frozen's 120 s per-unit timeout here too.
#[test]
fn legacy_cpp_lint_execution_branches_match_frozen_targets_summaries_and_evidence_with_the_host_gxx()
 {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-branches-oracle.json"
    ))
    .unwrap();
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    // ICI_TEST_RUFF where the checkout has no `.venv`; `replay` asserts the captured release.
    let ruff = std::env::var("ICI_TEST_RUFF").unwrap_or_else(|_| {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../.venv/bin/ruff")
            .display()
            .to_string()
    });
    replay(&oracle, &host.path, &ruff, true);
}

fn replay(oracle: &Value, system_path: &str, ruff: &str, host: bool) {
    let version = std::process::Command::new(ruff)
        .arg("--version")
        .output()
        .unwrap_or_else(|error| panic!("{ruff}: {error}"));
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().trim(),
        oracle["ruff"].as_str().unwrap(),
        "the branch oracle was captured with another Ruff"
    );
    let cases = oracle["cases"].as_object().unwrap();
    let filter = std::env::var("ICI_TEST_CASE").ok();
    let mut mismatches = vec![];
    for (name, case) in cases {
        // ICI_TEST_CASE: optional comma-separated case names (development reruns).
        if filter
            .as_deref()
            .is_some_and(|filter| !filter.split(',').any(|item| item == name))
        {
            continue;
        }
        let mut actual = run(oracle, case, system_path, ruff);
        let mut frozen = case["frozen"].clone();
        if host {
            host_view(&mut actual);
            host_view(&mut frozen);
        }
        // The compile_db engine of the same verify run is compared on its own.
        if let (Some(native), Some(oracle)) = (
            actual
                .as_object_mut()
                .and_then(|item| item.remove("compile_db")),
            frozen
                .as_object_mut()
                .and_then(|item| item.remove("compile_db")),
        ) && native != oracle
        {
            eprintln!("== {name}/compile_db\nactual: {native}\nfrozen: {oracle}");
            mismatches.push(format!("{name}/compile_db"));
        }
        let differences = lint_extra::split(&mut actual, &mut frozen);
        if !differences.is_empty() {
            eprintln!(
                "== {name} (extra/tool_evidence)\n{}",
                differences.join("\n")
            );
            mismatches.push(format!("{name}/extra"));
        }
        if actual != frozen {
            eprintln!(
                "== {name}\nactual: {}\nfrozen: {}",
                serde_json::to_string(&actual).unwrap(),
                serde_json::to_string(&frozen).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// Mask only the g++ release banner (see the host twin's doc).
fn host_view(view: &mut Value) {
    for record in view["tool_evidence"].as_array_mut().into_iter().flatten() {
        if record["name"] == "g++" && record["version"] != "" {
            record["version"] = json!("<host>");
        }
    }
    for finding in view["findings"].as_array_mut().into_iter().flatten() {
        if finding["tool_name"] == "g++" && finding["tool_version"] != "" {
            finding["tool_version"] = json!("<host>");
        }
    }
}
