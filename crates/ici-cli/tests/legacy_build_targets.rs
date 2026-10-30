//! Legacy `ici build` BuildEngine targets against the frozen CLI's own rendered engine result
//! (`tests/rust-parity/cpp/capture_build_targets.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

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

/// Compile durations are wall-clock values on both sides.
fn normalized(message: &str) -> String {
    let Some(start) = message.find("Compiled C++17 binary (") else {
        return message.to_owned();
    };
    let tail = &message[start + "Compiled C++17 binary (".len()..];
    let seconds = tail.strip_suffix("s)").expect("frozen duration suffix");
    let (whole, fraction) = seconds.split_once('.').expect("two decimals");
    assert!(
        whole.chars().all(|c| c.is_ascii_digit())
            && fraction.len() == 2
            && fraction.chars().all(|c| c.is_ascii_digit()),
        "{message}"
    );
    format!("{}Compiled C++17 binary (<seconds>s)", &message[..start])
}

fn run(case: &Value, environment: &BTreeMap<String, String>) -> Value {
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        root.path(),
    );
    for (relative, text) in case["changes"].as_object().unwrap() {
        let path = root.path().join(relative);
        match text.as_str() {
            None => fs::remove_file(path).unwrap(),
            Some(text) => {
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(path, text).unwrap();
            }
        }
    }
    let invocation = registry::parse(["ici", "build"]).unwrap();
    let output = commands::dispatch(&invocation, root.path(), environment).unwrap();
    let runs = root.path().join(".ici/rust-v1/details/result.json");
    let run = fs::read_dir(&runs)
        .unwrap_or_else(|_| panic!("no details: {}", output.stdout))
        .next()
        .unwrap()
        .unwrap()
        .path();
    let details: Value =
        serde_json::from_slice(&fs::read(run.join("details.json")).unwrap()).unwrap();
    let engine = details["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "build")
        .unwrap_or_else(|| panic!("no build engine: {}", output.stdout))
        .clone();
    let targets: Vec<Value> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            json!({
                "target_name": target["target_name"],
                "status": target["status"],
                "message": normalized(target["message"].as_str().unwrap()),
                "start_line": target["start_line"],
                "file_path": target["file_path"],
            })
        })
        .collect();
    json!({
        "exit_code": output.exit_code,
        "status": engine["status"],
        "summary": engine["summary"],
        "targets": targets,
    })
}

#[test]
#[ignore = "explicit actual Make/g++/CMake legacy build targets (ICI_TEST_MAKE, ICI_TEST_CXX, ICI_TEST_CMAKE)"]
fn legacy_build_engine_targets_match_the_frozen_rendered_engine_result() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/build-targets-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let tools = ["ICI_TEST_MAKE", "ICI_TEST_CXX", "ICI_TEST_CMAKE"]
        .map(|name| std::env::var(name).unwrap_or_else(|_| panic!("explicit {name}")));
    let path = tools
        .iter()
        .map(|tool| Path::new(tool).parent().unwrap().display().to_string())
        .chain(["/usr/bin".into(), "/bin".into()])
        .collect::<Vec<_>>()
        .join(":");
    let environment = BTreeMap::from([("PATH".to_string(), path)]);
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 10);
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let actual = run(case, &environment);
        match name.as_str() {
            // Open: frozen `ici build` has no shared analysis context, so any
            // `[build.artifacts]` declaration is a BuildError; Rust records the declared files.
            "declared-artifacts" => {
                assert_eq!(frozen["status"], "ERROR");
                assert_eq!(actual["exit_code"], 0);
                assert_eq!(
                    actual["targets"].as_array().unwrap().last().unwrap()["target_name"],
                    "DeclaredArtifacts"
                );
                continue;
            }
            // Open: Rust compiles objects and reports each located compiler diagnostic; frozen
            // reports one CppBinary FAIL at the package path with the first 200 bytes of
            // stderr (absolute paths). Exit, status and summary match.
            "direct-compile-failure" => {
                for key in ["exit_code", "status", "summary"] {
                    assert_eq!(actual[key], frozen[key], "{name} {key}");
                }
                assert_eq!(frozen["targets"][0]["target_name"], "CppBinary");
                assert_eq!(actual["targets"][0]["file_path"], "src/main.cpp");
                continue;
            }
            // Open (Python/config): a legacy `type='python'` project without sources has no
            // component, so Rust stops before the BuildEngine's `BuildArtifacts` FAIL.
            "no-artifacts" => {
                assert_eq!(frozen["targets"][0]["target_name"], "BuildArtifacts");
                assert_eq!(actual["exit_code"], 3);
                continue;
            }
            _ => {}
        }
        let mut expected = frozen.clone();
        expected.as_object_mut().unwrap().remove("stdout");
        assert_eq!(actual, expected, "{name}");
    }
}
