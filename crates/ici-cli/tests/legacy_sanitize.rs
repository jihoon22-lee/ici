//! Legacy `ici sanitize` / `ici thread-sanitize` decisions taken before any build, against the
//! frozen CLI oracle (`tests/rust-parity/cpp/capture_sanitize_cli.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
    "file_path",
];

const FINDING_FIELDS: [&str; 11] = [
    "rule_id",
    "category",
    "severity",
    "confidence",
    "message",
    "primary_location",
    "related_locations",
    "explanation",
    "remediation",
    "tool_name",
    "tool_rule_id",
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

fn run(fixture: &str, command: &str, config: &str) -> Value {
    run_with(fixture, command, config, "/usr/bin:/bin", false)
}

fn run_with(fixture: &str, command: &str, config: &str, path: &str, builds: bool) -> Value {
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(fixture),
        root.path(),
    );
    fs::write(
        root.path().join("ici.toml"),
        format!("[project]\ntype='cpp'\nsource_dirs=['src']\n{config}"),
    )
    .unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), path.to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(
            root.path()
                .join(format!("{}_report.json", command.replace('-', "_"))),
        )
        .unwrap(),
    )
    .unwrap();
    let mut targets: Vec<Value> = report["targets"]
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
    targets.sort_by_key(|target| {
        (
            target["target_name"].as_str().unwrap().to_owned(),
            target["file_path"].as_str().unwrap().to_owned(),
        )
    });
    // The legacy findings normalized from sanitizer reports (frozen `_normalized_findings`).
    let diagnostic_findings: Vec<Value> = report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| {
            finding["explanation"].as_str().is_some_and(|text| {
                text.starts_with("Normalized from a bounded runtime-sanitizer report")
            })
        })
        .map(|finding| {
            Value::Object(
                FINDING_FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), finding[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    // Nothing is prepared: no project build tree and no owned sanitizer build directory.
    let owned_builds = root.path().join(".ici/rust-v1/build");
    assert!(
        builds || !owned_builds.exists() || fs::read_dir(&owned_builds).unwrap().next().is_none(),
        "{fixture} {command}: a sanitizer build directory was created"
    );
    json!({
        "exit_code": output.exit_code,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "built": root.path().join("build").exists(),
        "targets": targets,
        "diagnostic_findings": diagnostic_findings,
    })
}

#[test]
fn legacy_sanitizers_skip_without_cpp_tests_and_error_on_missing_sanitizer_argv_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/sanitize-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let fixtures = BTreeMap::from([
        ("testless-direct", "sanitize-testless"),
        ("testless-make", "sanitize-testless"),
        ("testless-make-required", "sanitize-testless"),
        ("tested-make-without-sanitizer-argv", "sanitize-tested"),
        ("noscope", "sanitize-noscope"),
    ]);
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 18);
    for (name, expected) in cases
        .iter()
        .filter(|(name, _)| !name.contains(":direct-") && !name.contains(":cmake-"))
    {
        let (command, config) = name.split_once(':').unwrap();
        let actual = run(
            fixtures[config],
            command,
            oracle["configs"][config].as_str().unwrap(),
        );
        // Frozen tool/configuration errors: ERROR with NOT_RUN evidence and exit 1.
        assert_eq!(&actual, expected, "{name}");
    }
}

#[test]
#[ignore = "explicit actual g++ and CMake/ctest sanitizer runs (ICI_TEST_CXX, ICI_TEST_CMAKE, ICI_TEST_CTEST)"]
fn legacy_direct_sanitizer_runs_report_frozen_targets_and_summaries() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/sanitize-oracle.json"
    ))
    .unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let cmake = std::env::var("ICI_TEST_CMAKE").expect("explicit CMake (ICI_TEST_CMAKE)");
    let ctest = std::env::var("ICI_TEST_CTEST").expect("explicit CTest (ICI_TEST_CTEST)");
    let path = [&compiler, &cmake, &ctest]
        .iter()
        .map(|tool| Path::new(tool).parent().unwrap().display().to_string())
        .chain(["/usr/bin".into(), "/bin".into()])
        .collect::<Vec<_>>()
        .join(":");
    let fixtures = BTreeMap::from([
        ("direct-clean", "sanitize-direct-clean"),
        ("direct-defect", "sanitize-direct"),
        ("cmake-plain", "test-cmake/plain"),
        ("cmake-defect", "test-cmake/sanitize"),
    ]);
    let mut seen = 0;
    for (name, expected) in oracle["cases"].as_object().unwrap() {
        let (command, config) = name.split_once(':').unwrap();
        let Some(fixture) = fixtures.get(config) else {
            continue;
        };
        seen += 1;
        let mut actual = run_with(fixture, command, "", &path, true);
        let mut expected = expected.clone();
        if config.starts_with("cmake-") {
            // Intended: frozen CMake configures into the project's `build/`; Rust builds the
            // sanitizer variant in its owned shadow under .ici and leaves the project tree alone.
            assert_eq!(expected["built"], true, "{name}");
            assert_eq!(actual["built"], false, "{name}");
            actual["built"] = Value::Null;
            expected["built"] = Value::Null;
        }
        assert_eq!(actual, expected, "{name}");
    }
    assert_eq!(seen, 8);
}
