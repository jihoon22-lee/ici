//! Per-engine keys of the historical JSON reports (`<engine>_report.json`, `verify_report.json`)
//! against the frozen CLI (`tests/rust-parity/python/capture_legacy_report_keys.py`, CPython
//! 3.10.21, the pinned ruff/mypy linked into one PATH directory).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-report-keys-oracle.json"
    ))
    .unwrap()
}

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("required native fixture tool {name} is missing"))
}

/// One PATH directory holding the captured ruff and mypy, checked against the captured versions.
fn tool_dir(oracle: &Value) -> tempfile::TempDir {
    let tools = tempfile::tempdir().unwrap();
    for (name, variable) in [
        ("ruff", "ICI_TEST_RUFF"),
        ("mypy", "ICI_TEST_MYPY"),
        ("python3", "ICI_TEST_PYTHON"),
    ] {
        let target = required(variable);
        let version = std::process::Command::new(&target)
            .arg("--version")
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8_lossy(&version.stdout).trim(),
            oracle["tool_versions"][name],
            "{variable} is not the captured {name}"
        );
        std::os::unix::fs::symlink(target, tools.path().join(name)).unwrap();
    }
    tools
}

fn project(oracle: &Value, case: &Value) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    let config = format!(
        "{}{}",
        oracle["base"].as_str().unwrap(),
        case["config"].as_str().unwrap()
    );
    fs::write(root.path().join("ici.toml"), config).unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    root
}

fn run(root: &Path, path: &str, args: &[&str]) -> (i32, Value) {
    let invocation = registry::parse(std::iter::once("ici").chain(args.iter().copied())).unwrap();
    let mut environment = BTreeMap::from([("PATH".to_string(), path.to_string())]);
    if args[0] == "verify" {
        // Frozen verify ran `-m mypy` with its own interpreter (`sys.executable`); under the pyz
        // that is the launcher's choice, which ICI_PYTHON pins to the same captured CPython.
        environment.insert("ICI_PYTHON".into(), required("ICI_TEST_PYTHON"));
    }
    let output = commands::dispatch(&invocation, root, &environment).unwrap();
    let name = if args[0] == "verify" {
        "verify_report.json".to_owned()
    } else {
        format!("{}_report.json", args[0].replace('-', "_"))
    };
    let report = root.join(name);
    let value = serde_json::from_slice(&fs::read(&report).unwrap()).unwrap();
    fs::remove_file(report).unwrap();
    (output.exit_code, value)
}

/// Entry-level differences of two serialized support matrices (empty when equal).
fn support_differences(name: &str, actual: &Value, frozen: &Value) -> Vec<String> {
    let mut found = vec![];
    for key in ["project_languages", "project_frameworks"] {
        if actual[key] != frozen[key] {
            found.push(format!("{name}: {key} {} != {}", actual[key], frozen[key]));
        }
    }
    let empty = vec![];
    let actual_entries = actual["entries"].as_array().unwrap_or(&empty);
    let frozen_entries = frozen["entries"].as_array().unwrap_or(&empty);
    if actual_entries.len() != frozen_entries.len() {
        found.push(format!(
            "{name}: {} entries != {}",
            actual_entries.len(),
            frozen_entries.len()
        ));
    }
    for (left, right) in actual_entries.iter().zip(frozen_entries) {
        if left != right {
            let fields: Vec<_> = right
                .as_object()
                .unwrap()
                .iter()
                .filter(|(key, value)| left[key.as_str()] != **value)
                .map(|(key, value)| format!("{key}: {} != {value}", left[key.as_str()]))
                .collect();
            found.push(format!(
                "{name}: {}/{}: {}",
                right["engine_name"],
                right["language"],
                fields.join("; ")
            ));
        }
    }
    found
}

/// Engine evidence that differs from frozen for a reason outside the report projection.
const KNOWN_ENGINE_EVIDENCE: [(&str, &str, &str); 1] = [(
    "configured-hybrid/line",
    "NOT_RUN",
    "cross-area planning: `type='hybrid'` without C++ sources plans a cpp.line check that is \
     BLOCKED (no sources in scope), so the line engine reads NOT_RUN and the run exits 3; \
     frozen counted the Python sources (MEASURED, exit 0)",
)];

#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn single_engine_reports_carry_the_frozen_support_matrix() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let tools = tool_dir(&oracle);
    let with_tools = format!("{}:/usr/bin:/bin", tools.path().display());
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 21);
    let mut failures: Vec<String> = vec![];
    for (name, case) in cases {
        let root = project(&oracle, case);
        let path = if case["tools"] == true {
            with_tools.as_str()
        } else {
            "/usr/bin:/bin"
        };
        let (_, report) = run(
            root.path(),
            path,
            &[case["command"].as_str().unwrap(), "--report"],
        );
        let frozen = &case["frozen"];
        if let Some((_, native, _)) = KNOWN_ENGINE_EVIDENCE
            .iter()
            .find(|(known, _, _)| known == name)
        {
            // The engine's own evidence differs for a recorded reason; the project facts the
            // matrix is built from must still be frozen's.
            assert_eq!(report["evidence"], *native, "{name}: known native evidence");
            for key in ["project_languages", "project_frameworks"] {
                assert_eq!(
                    report["support_matrix"][key], frozen["support_matrix"][key],
                    "{name}: {key}"
                );
            }
            continue;
        }
        if report["evidence"] != frozen["evidence"] {
            failures.push(format!(
                "{name}: engine evidence {} != {}",
                report["evidence"], frozen["evidence"]
            ));
        }
        failures.extend(support_differences(
            name,
            &report["support_matrix"],
            &frozen["support_matrix"],
        ));
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn verify_reports_carry_the_frozen_suite_support_matrix_and_null_engine_matrices() {
    let oracle = oracle();
    let tools = tool_dir(&oracle);
    let with_tools = format!("{}:/usr/bin:/bin", tools.path().display());
    let mut failures: Vec<String> = vec![];
    for (name, case) in oracle["verify"].as_object().unwrap() {
        let root = project(&oracle, case);
        let profile = case["profile"].as_str().unwrap();
        let frozen = &case["frozen"]["first"];
        let (_, report) = run(
            root.path(),
            &with_tools,
            &["verify", "--report", "--profile", profile],
        );
        failures.extend(support_differences(
            name,
            &report["support_matrix"],
            &frozen["support_matrix"],
        ));
        for result in report["results"].as_array().unwrap() {
            let engine = result["engine_name"].as_str().unwrap();
            if result["support_matrix"] != Value::Null {
                failures.push(format!(
                    "{name}/{engine}: result support_matrix is not null"
                ));
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
