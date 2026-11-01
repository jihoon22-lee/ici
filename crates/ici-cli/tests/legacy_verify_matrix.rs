//! Legacy `ici verify` on a C++ project: engine selection by profile, `enabled`, `mode` and
//! `required` keys against the frozen capture tests/rust-parity/cpp/verify-matrix-oracle.json.
use ici_cli::{commands, registry};
use serde_json::Value;
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

fn run(config: &str, environment: &BTreeMap<String, String>) -> (i32, Value) {
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
        root.path(),
    );
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let invocation = registry::parse(["ici", "verify", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, root.path(), environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("verify_report.json")).unwrap()).unwrap();
    (output.exit_code, report)
}

fn compiler_environment(oracle: &Value) -> BTreeMap<String, String> {
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let version = std::process::Command::new(&compiler)
        .arg("--version")
        .output()
        .unwrap();
    // The lint fallback wording is compiler-version specific: another g++ fails, never passes.
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str(),
        "the verify matrix was captured with another g++"
    );
    let directory = Path::new(&compiler).parent().unwrap().display().to_string();
    for tool in ["clang-tidy", "clazy-standalone"] {
        for path in [directory.as_str(), "/usr/bin", "/bin"] {
            assert!(!Path::new(path).join(tool).exists(), "{tool} in {path}");
        }
    }
    BTreeMap::from([("PATH".to_string(), format!("{directory}:/usr/bin:/bin"))])
}

#[test]
#[ignore = "explicit actual g++ legacy verify: ICI_TEST_CXX must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone"]
fn legacy_cpp_verify_engine_keys_and_profiles_match_the_frozen_suite() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/verify-matrix-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let environment = compiler_environment(&oracle);
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 7);
    for (name, frozen) in cases {
        let config = format!("{base}{}", oracle["configs"][name].as_str().unwrap());
        let (exit_code, report) = run(&config, &environment);
        let engines = report["results"].as_array().unwrap();
        let expected = frozen["engines"].as_array().unwrap();
        let names = |list: &[Value]| -> Vec<Value> {
            list.iter()
                .map(|engine| engine["engine_name"].clone())
                .collect()
        };
        // Profile and `enabled` select exactly the frozen engines in frozen order.
        assert_eq!(names(engines), names(expected), "{name}: engine set");
        for (actual, expected) in engines.iter().zip(expected) {
            let engine = expected["engine_name"].as_str().unwrap();
            for key in ["status", "required"] {
                assert_eq!(actual[key], expected[key], "{name}: {engine}.{key}");
            }
            // Recorded validation difference: the no-test verdict is MEASURED where frozen kept
            // the ESTIMATED label of its fabricated optional coverage estimate.
            if engine == "test" && expected["status"] == "FAIL" {
                assert_eq!(actual["evidence"], "MEASURED", "{name}: test evidence");
            } else {
                assert_eq!(actual["evidence"], expected["evidence"], "{name}: {engine}");
            }
            let targets: Vec<Value> = actual["targets"]
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
            assert_eq!(
                &Value::from(targets),
                &expected["targets"],
                "{name}: {engine}"
            );
        }
        let incomplete = report["native_run"]["execution"]["required_complete"] == false;
        if report["suite_status"] == frozen["suite_status"] && exit_code == frozen["exit_code"] {
            continue;
        }
        // Accepted policy: a run whose required evidence stays incomplete keeps an ERROR suite
        // and exits 3 where frozen exited 1 with the same ERROR suite.
        assert!(incomplete, "{name}: complete run differs from frozen");
        assert_eq!(report["suite_status"], "ERROR", "{name}: suite");
        assert_eq!(frozen["suite_status"], "ERROR", "{name}: frozen suite");
        assert_eq!(
            (exit_code, &frozen["exit_code"]),
            (3, &Value::from(1)),
            "{name}: exit"
        );
    }
}
