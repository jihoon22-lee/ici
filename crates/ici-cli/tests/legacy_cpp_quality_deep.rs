//! Legacy `[engines.test.quality]` on a C++-only project against frozen v0.13.0
//! (`tests/rust-parity/cpp/quality-cpp-deep-oracle.json`, `capture_quality_cpp_deep.py`):
//! frozen `TestQualityMixin._run_deep_test_quality` runs only for a deep python/hybrid analysis,
//! so a `type='cpp'` project with every quality key and a marker-writing mutation command
//! repeats nothing, measures no slow test and runs no mutation command under `--profile deep`
//! or the standard profile; the counters keep `empty_quality_info()` with the profile.
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
const QUALITY_KEYS: [&str; 16] = [
    "test_quality",
    "quality_enabled",
    "repeat_runs",
    "repeat_reruns",
    "repeat_cases",
    "repeat_unavailable",
    "repeat_timeouts",
    "flaky_tests",
    "slow_tests",
    "slow_tests_observed",
    "slow_test_inventory",
    "flaky_test_inventory",
    "mutation_probes",
    "mutation_available",
    "mutation_unavailable",
    "mutation_status",
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

/// Frozen lists the direct tests in `tests_root.rglob` directory order (the capture tree's
/// filesystem order); the subject here is the quality pass, so targets compare by name.
fn by_name(mut targets: Vec<Value>) -> Vec<Value> {
    targets.sort_by_key(|target| target["target_name"].to_string());
    targets
}

/// Positive: with quality enabled under `--profile deep` the C++ suite runs once and every
/// quality counter stays at frozen's empty set with profile "deep". Negative: the same project
/// with quality disabled, and the standard profile, report the same counters. Failure: the
/// configured mutation command is never started (no marker file) and no `[test-quality]`
/// target appears.
#[test]
#[ignore = "explicit actual /usr/bin/g++ and gcov direct C++ tests (the captured g++ version)"]
fn legacy_cpp_only_project_runs_no_test_quality_pass_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/quality-cpp-deep-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let marker = oracle["marker"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 3);
    let mut failures = vec![];
    for (name, frozen) in cases {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp/test-direct/mixed"),
            &root,
        );
        fs::create_dir(root.join(".git")).unwrap();
        fs::write(
            root.join("ici.toml"),
            format!(
                "{}{}{}",
                oracle["head"].as_str().unwrap(),
                frozen["quality"].as_str().unwrap(),
                oracle["off"].as_str().unwrap()
            ),
        )
        .unwrap();
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("XDG_CONFIG_HOME".to_owned(), home.display().to_string()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let argv = frozen["argv"].as_array().unwrap();
        let invocation = registry::parse(
            std::iter::once("ici").chain(argv.iter().map(|item| item.as_str().unwrap())),
        )
        .unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let text = fs::read_to_string(root.join("verify_report.json"))
            .unwrap()
            .replace(&root.display().to_string(), "<root>");
        let report: Value = serde_json::from_str(&text).unwrap();
        let test = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "test")
            .unwrap();
        let targets: Vec<Value> = test["targets"]
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
        if targets.iter().any(|target| {
            target["target_name"]
                .as_str()
                .is_some_and(|name| name.starts_with("[test-quality]"))
        }) {
            failures.push(format!("{name}: a [test-quality] target"));
        }
        let actual = json!({
            "exit_code": output.exit_code,
            "status": test["status"],
            "targets": by_name(targets),
            "quality_extra": Value::Object(QUALITY_KEYS.iter().map(|key| ((*key).to_owned(), test["extra"][*key].clone())).collect()),
            "mutation_marker": root.join(marker).exists(),
        });
        let expected = json!({
            "exit_code": frozen["exit_code"],
            "status": frozen["status"],
            "targets": by_name(frozen["targets"].as_array().unwrap().clone()),
            "quality_extra": frozen["quality_extra"],
            "mutation_marker": frozen["mutation_marker"],
        });
        if actual != expected {
            failures.push(format!("{name}\nnative: {actual:#}\nfrozen: {expected:#}"));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
