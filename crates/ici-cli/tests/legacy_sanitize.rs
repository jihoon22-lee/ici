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
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(
            root.path()
                .join(format!("{}_report.json", command.replace('-', "_"))),
        )
        .unwrap(),
    )
    .unwrap();
    let targets: Vec<Value> = report["targets"]
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
    // Nothing is prepared: no project build tree and no owned sanitizer build directory.
    let owned_builds = root.path().join(".ici/rust-v1/build");
    assert!(
        !owned_builds.exists() || fs::read_dir(&owned_builds).unwrap().next().is_none(),
        "{fixture} {command}: a sanitizer build directory was created"
    );
    json!({
        "exit_code": output.exit_code,
        "status": report["status"],
        "summary": report["summary"],
        "built": root.path().join("build").exists(),
        "targets": targets,
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
    ]);
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 8);
    for (name, expected) in cases {
        let (command, config) = name.split_once(':').unwrap();
        let mut actual = run(
            fixtures[config],
            command,
            oracle["configs"][config].as_str().unwrap(),
        );
        if expected["status"] == "ERROR" {
            // Open reporting difference: the ERROR projection prefixes the check id
            // (`cpp.sanitize: ` / `cpp.tsan: `) to the frozen summary.
            let check = if command == "sanitize" {
                "cpp.sanitize"
            } else {
                "cpp.tsan"
            };
            let summary = actual["summary"].as_str().unwrap().to_owned();
            assert_eq!(
                summary,
                format!("{check}: {}", expected["summary"].as_str().unwrap()),
                "{name}"
            );
            actual["summary"] = expected["summary"].clone();
        }
        assert_eq!(&actual, expected, "{name}");
    }
}
