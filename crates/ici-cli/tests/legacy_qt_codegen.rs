//! Legacy lint Qt generated-code linkage and Qt-major verification (frozen
//! `analysis/_qt_codegen.py` through `engines/lint.py`) against the development-only oracle
//! `tests/rust-parity/cpp/qt-codegen-oracle.json` (`capture_qt_codegen.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

const FIELDS: [&str; 7] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
    "metrics",
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

fn query(variable: &str, key: &str) -> String {
    let qmake = std::env::var(variable).unwrap_or_else(|_| panic!("explicit {variable}"));
    let output = Command::new(qmake).args(["-query", key]).output().unwrap();
    String::from_utf8(output.stdout).unwrap().trim().to_owned()
}

fn write_inputs(root: &Path, oracle: &Value, case: &Value, markers: &[(String, String)]) {
    let files = case["files"].as_object().unwrap();
    let expand = |text: &str| {
        let mut text = text.replace("{root}", &root.display().to_string());
        for (marker, value) in markers {
            text = text.replace(marker, value);
        }
        text
    };
    if !files.is_empty() {
        for (name, text) in oracle["generated"].as_object().unwrap() {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text.as_str().unwrap()).unwrap();
        }
    }
    if let Some(database) = files.get("build/compile_commands.json")
        && !database.as_str().unwrap().contains("-Ibuild/gen")
    {
        fs::remove_dir_all(root.join("build/gen")).unwrap();
        fs::remove_dir_all(root.join("build/autogen")).unwrap();
    }
    for (name, text) in files {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, expand(text.as_str().unwrap())).unwrap();
    }
    for (directory, spec) in case["generate"].as_object().unwrap() {
        let (suffix, count, text) = (
            spec[0].as_str().unwrap(),
            spec[1].as_u64().unwrap(),
            spec[2].as_str().unwrap(),
        );
        fs::create_dir_all(root.join(directory)).unwrap();
        for index in 0..count {
            fs::write(
                root.join(directory).join(format!("item{index:04}{suffix}")),
                text,
            )
            .unwrap();
        }
    }
    for (name, size) in case["sizes"].as_object().unwrap() {
        fs::write(
            root.join(name),
            "/".repeat(usize::try_from(size.as_u64().unwrap()).unwrap()),
        )
        .unwrap();
    }
}

fn run(oracle: &Value, case: &Value, markers: &[(String, String)], path: &str) -> Value {
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/qt-codegen"),
        root.path(),
    );
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "{}{}",
            oracle["base"].as_str().unwrap(),
            case["config"].as_str().unwrap()
        ),
    )
    .unwrap();
    write_inputs(root.path(), oracle, case, markers);
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), path.to_owned())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
    )
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
        report
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
    captured
}

#[test]
#[ignore = "explicit actual g++ and Qt 5/6 headers: ICI_TEST_CXX (the oracle g++), ICI_TEST_QMAKE and ICI_TEST_QMAKE6 (QT_INSTALL_HEADERS); /usr/bin must lack clang-tidy/clazy-standalone"]
fn legacy_qt_codegen_and_major_evidence_match_frozen_lint_targets_and_findings() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/qt-codegen-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let version = Command::new(&compiler).arg("--version").output().unwrap();
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str(),
        "the Qt oracle was captured with another g++"
    );
    for (variable, key) in [("ICI_TEST_QMAKE", "qt5"), ("ICI_TEST_QMAKE6", "qt6")] {
        assert_eq!(
            query(variable, "QT_VERSION"),
            oracle["qt_versions"][key].as_str().unwrap(),
            "{variable} is not the captured Qt"
        );
    }
    let markers = vec![
        (
            "{qt5}".to_owned(),
            query("ICI_TEST_QMAKE", "QT_INSTALL_HEADERS"),
        ),
        (
            "{qt6}".to_owned(),
            query("ICI_TEST_QMAKE6", "QT_INSTALL_HEADERS"),
        ),
    ];
    let directory = Path::new(&compiler).parent().unwrap().display().to_string();
    let path = format!("{directory}:/usr/bin:/bin");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 14);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let actual = run(&oracle, case, &markers, &path);
        if actual != case["frozen"] {
            mismatches.push(name.clone());
            eprintln!(
                "== {name}\nactual: {}\nfrozen: {}",
                serde_json::to_string(&actual).unwrap(),
                serde_json::to_string(&case["frozen"]).unwrap()
            );
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
