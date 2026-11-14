//! Root ruling 2026-11-14 (b6(b), d4): frozen ran every stable command under CPython 3.10, which
//! coerced a C locale to LC_CTYPE=C.UTF-8 in its own environment (PEP 538), so every child
//! inherited it. `tests/rust-parity/cpp/capture_c_locale.py` (cpp36) captured `ici test` under no
//! locale variable and, as the control, under LC_ALL=C: a direct g++ build/test whose failed
//! compile's first message line carries g++'s locale-dependent quotes, and a pytest case that
//! passes only when its process was started with LC_CTYPE=C.UTF-8 (/proc/self/environ). Native
//! runs the binary with a cleared environment, so its own coercion (`commands::dispatch`) is
//! what the children inherit. Written by cpp36 and not run before the batch run.
use serde_json::{Value, json};
use std::{fs, process::Command};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/c-locale-oracle.json");

/// Root ruling 2026-11-14 (b6(a)): a failed compile's excerpt is compared by its first line; a
/// test case's line is its definition natively (`legacy_test_parity.rs`), so it is not compared.
/// A direct g++ tree with a failed test compile compares only its `[C++] ` case targets, sorted:
/// frozen walks tests/ with an unsorted `rglob` (engines/test.py:547, the order the filesystem
/// returns, `legacy_cpp_test.rs` direct-broken), and native measures the compiled library where
/// frozen's failed coverage compile left the coverage estimated (decisions.md "실패한 test
/// compile이 남긴 notes", the approved frozen-defect correction), so native alone has the coverage
/// targets.
fn targets(report: &Value) -> Vec<Value> {
    let all = report["targets"].as_array().unwrap();
    let compile_failed = all.iter().any(|target| {
        target["message"]
            .as_str()
            .is_some_and(|text| text.starts_with("Compilation Error: "))
    });
    let mut picked: Vec<Value> = all
        .iter()
        .filter(|target| {
            !compile_failed
                || target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("[C++] "))
        })
        .map(|target| {
            let name = target["target_name"].as_str().unwrap_or_default();
            let text = target["message"].as_str().unwrap_or_default();
            let message = if text.starts_with("Compilation Error: ") {
                json!(text.lines().next().unwrap_or_default())
            } else {
                target["message"].clone()
            };
            json!({
                "target_name": target["target_name"],
                "status": target["status"],
                "file_path": target["file_path"],
                "start_line": if name.starts_with("tests/") { Value::Null } else { target["start_line"].clone() },
                "message": message,
            })
        })
        .collect();
    if compile_failed {
        picked.sort_by_key(|target| {
            target["target_name"]
                .as_str()
                .unwrap_or_default()
                .to_owned()
        });
    }
    picked
}

fn run(case: &Value, python: &str) -> (i32, Value) {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    for (relative, text) in case["files"].as_object().unwrap() {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir_all(root.join(".git")).unwrap();
    fs::write(
        root.join("ici.toml"),
        case["config"].as_str().unwrap().replace("<PYTHON>", python),
    )
    .unwrap();
    let mut ici = Command::new(env!("CARGO_BIN_EXE_ici"));
    ici.args(["test", "--report"])
        .current_dir(&root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", work.path())
        .env("GIT_CEILING_DIRECTORIES", work.path());
    for (name, value) in case["locale"].as_object().unwrap() {
        ici.env(name, value.as_str().unwrap());
    }
    let output = ici.output().unwrap();
    let text = fs::read_to_string(root.join("test_report.json"))
        .unwrap_or_else(|error| panic!("no native report: {error}"))
        .replace(&root.display().to_string(), "<root>")
        .replace(python, "<PYTHON>");
    (
        output.status.code().unwrap(),
        serde_json::from_str(&text).unwrap(),
    )
}

/// Positive (`c-locale`, no locale variable): g++ quotes `‘int main()’` (UTF-8) in the failed
/// compile and the pytest case passes, as frozen. Negative control (`lc-all-c`, LC_ALL=C, which
/// PEP 538 leaves alone): ASCII quotes and the pytest case fails on both sides, so the replay
/// tells the coerced locale from the caller's. Failure: the broken test's compile (FAIL, exit 1)
/// and, under the control, the failing pytest case.
#[test]
#[ignore = "explicit actual g++ 15.2.0 and gcov on /usr/bin and the capture CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn legacy_children_inherit_the_coerced_c_locale_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let python = std::env::var("ICI_TEST_PYTHON")
        .expect("explicit ICI_TEST_PYTHON (the capture CPython with pytest and coverage)");
    assert_eq!(
        oracle["capture_python"].as_str(),
        Some(python.as_str()),
        "ICI_TEST_PYTHON is not the capture runtime"
    );
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 4);
    // Independent frozen literals: the locale decides g++'s quotes and the inherited LC_CTYPE.
    let first_line = |name: &str| -> String {
        let message = cases[name]["frozen"]["targets"][0]["message"]
            .as_str()
            .unwrap();
        message.lines().next().unwrap().to_owned()
    };
    assert!(first_line("gxx:c-locale").ends_with("In function \u{2018}int main()\u{2019}:"));
    assert!(first_line("gxx:lc-all-c").ends_with("In function 'int main()':"));
    for (name, status) in [("python:c-locale", "PASS"), ("python:lc-all-c", "FAIL")] {
        assert_eq!(
            cases[name]["frozen"]["targets"][0]["status"], status,
            "{name}"
        );
    }
    let mut mismatches = vec![];
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let (exit, report) = run(case, &python);
        let expected = json!({"exit_code": frozen["exit_code"], "status": frozen["status"], "targets": targets(frozen)});
        let actual =
            json!({"exit_code": exit, "status": report["status"], "targets": targets(&report)});
        if actual != expected {
            eprintln!(
                "== {name}\nfrozen: {}\nnative: {}",
                serde_json::to_string_pretty(&expected).unwrap(),
                serde_json::to_string_pretty(&actual).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
