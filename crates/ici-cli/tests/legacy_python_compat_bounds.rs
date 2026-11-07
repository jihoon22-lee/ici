//! Frozen legacy python_compat bounds and process problems through the binary (F2):
//! `tests/rust-parity/validation/legacy-python-compat-bounds-oracle.json`
//! (`capture_legacy_python_compat_bounds.py`). AST per-file/aggregate node bounds and the
//! 128-target compileall bound are frozen `AnalysisUnavailable` errors at pyproject.toml; a
//! version probe past its 10 s bound and compileall/import-smoke output past 128,000 characters
//! are the frozen `_process_problem` texts.
use serde_json::{Value, json};
use std::{fs, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-python-compat-bounds-oracle.json");
const APP: &str = "import json\n\n\ndef f():\n    return json.dumps({})\n";
const FLOOD: &str = "head -c 200000 /dev/zero | tr '\\0' a; exit 0";

/// (files, source_dirs, [version, compileall, imports] wrapper actions, extra config lines).
type Scenario = (
    Vec<(String, String)>,
    Vec<String>,
    [&'static str; 3],
    &'static str,
);

fn scenario(name: &str) -> Scenario {
    let app = vec![("app.py".to_owned(), APP.to_owned())];
    let dot = vec![".".to_owned()];
    match name {
        "ast_file_bound" => (
            [app, vec![("wide.py".into(), "x = 1\n".repeat(26_000))]].concat(),
            dot,
            [":"; 3],
            "",
        ),
        "ast_aggregate_bound" => (
            [
                app,
                (0..11)
                    .map(|index| (format!("tall{index:02}.py"), "x = 1\n".repeat(23_000)))
                    .collect(),
            ]
            .concat(),
            dot,
            [":"; 3],
            "",
        ),
        "compile_targets_bound" => (
            (0..129)
                .map(|index| (format!("d{index:03}/m.py"), APP.to_owned()))
                .collect(),
            (0..129).map(|index| format!("d{index:03}")).collect(),
            [":"; 3],
            "",
        ),
        "version_timeout" => (app, dot, ["sleep 15", ":", ":"], ""),
        "compile_truncated" => (app, dot, [":", FLOOD, ":"], ""),
        "imports_truncated" => (app, dot, [":", ":", FLOOD], "imports = [\"app\"]\n"),
        _ => unreachable!("{name}"),
    }
}

fn run(name: &str, bare: &str) -> Value {
    let (files, source_dirs, [version, compile, imports], extra) = scenario(name);
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    let bin = work.path().join("bin");
    fs::create_dir(&bin).unwrap();
    for (relative, text) in files {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    let wrapper = bin.join("python3");
    fs::write(
        &wrapper,
        format!(
            "#!/bin/sh\ncase \"$1 $2 $3\" in\n  \"-VV  \"*|\"-I -c \"*) {version} ;;\n  \"-B -m compileall\") {compile} ;;\n  \"-I -B -c\") {imports} ;;\nesac\nexec {bare} \"$@\"\n"
        ),
    )
    .unwrap();
    fs::set_permissions(
        &wrapper,
        std::os::unix::fs::PermissionsExt::from_mode(0o755),
    )
    .unwrap();
    fs::write(
        root.join("ici.toml"),
        format!(
            "[ici]\nprofile = \"standard\"\n[project]\ntype = \"python\"\nsource_dirs = {}\n[engines.python_compat]\nenabled = true\ninterpreters = [\"{w}\"]\nrequired_interpreters = [\"{w}\"]\n{extra}",
            serde_json::to_string(&source_dirs).unwrap(),
            w = wrapper.display()
        ),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["python-compat", "--report"])
        .current_dir(&root)
        .env("HOME", work.path())
        .output()
        .unwrap();
    let engine: Value =
        serde_json::from_slice(&fs::read(root.join("python_compat_report.json")).unwrap()).unwrap();
    let targets = engine["targets"].as_array().unwrap();
    let text = json!({
        "exit_code": output.status.code(),
        "engine": {"status": engine["status"], "summary": engine["summary"], "evidence": engine["evidence"]},
        "targets": targets.iter().filter(|target| target["target_name"] != "Compatibility:StaticScan").map(|target| json!({
            "file_path": target["file_path"], "start_line": target["start_line"],
            "target_name": target["target_name"], "status": target["status"], "message": target["message"],
        })).collect::<Vec<_>>(),
        "static_scans": targets.iter().filter(|target| target["target_name"] == "Compatibility:StaticScan").count(),
        "ast_nodes": engine["extra"]["ast_nodes"],
    })
    .to_string()
    .replace(root.to_str().unwrap(), "<root>")
    .replace(bin.to_str().unwrap(), "<bin>");
    serde_json::from_str(&text).unwrap()
}

fn compare(names: &[&str]) {
    let bare = std::env::var("ICI_TEST_PYTHON_BARE").expect("ICI_TEST_PYTHON_BARE (CPython 3.12)");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for name in names {
        assert_eq!(run(name, &bare), oracle["cases"][*name], "{name}");
    }
}

#[test]
#[ignore = "explicit actual project interpreter (ICI_TEST_PYTHON_BARE)"]
fn ast_and_compileall_target_bounds_are_the_frozen_analysis_errors() {
    compare(&[
        "ast_file_bound",
        "ast_aggregate_bound",
        "compile_targets_bound",
    ]);
}

#[test]
#[ignore = "explicit actual project interpreter (ICI_TEST_PYTHON_BARE)"]
fn version_timeout_and_truncated_compileall_or_import_output_are_the_frozen_problems() {
    compare(&["version_timeout", "compile_truncated", "imports_truncated"]);
}
