//! Frozen legacy Python tool runs replayed from
//! `tests/rust-parity/python/legacy-python-argv-oracle.json` (`capture_legacy_python_argv.py`).
//!
//! Each case keeps frozen's exit code, engine status, every target (path, status, name) and every
//! finding (rule, path, message), and what the tools were given where it changes the result:
//! - `ici test` clears the project's pytest `addopts`, so `-k`, `-x` or an unknown option set
//!   there neither deselects tests, stops early, nor turns a test failure into a tool error;
//! - `ici python-compat` hands compileall the selected sources' configured source directories
//!   (frozen `_compile_targets`), so a broken file there that discovery did not select fails it.
//!
//! The native argv adds its own isolation options (documented in decisions.md "legacy Python 도구
//! argv"); only the parts that carry these meanings are compared, never the whole argv string.
use serde_json::Value;
use std::{fs, path::Path, process::Command, process::Stdio};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/python/legacy-python-argv-oracle.json");

struct Run {
    exit: i64,
    status: String,
    targets: Vec<Value>,
    findings: Vec<Value>,
    argv: Vec<(String, Vec<String>)>,
}

/// The interpreter's own version (`<python> (3.10.21)`) is a fact of the capturing machine.
fn version_free(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(start) = rest.find(" (3.") {
        out.push_str(&rest[..start]);
        let tail = &rest[start + 2..];
        match tail.find(')') {
            Some(end) if tail[..end].chars().all(|c| c.is_ascii_digit() || c == '.') => {
                out.push_str(" (<version>)");
                rest = &tail[end + 1..];
            }
            _ => {
                out.push_str(" (");
                rest = tail;
            }
        }
    }
    out.push_str(rest);
    out
}

/// Machine paths in a value, longest first: the fixture's venv, the project, the base interpreter
/// the venv or the test Python resolves to, and the test Python itself.
type Names = Vec<(String, &'static str)>;

fn normalize(value: &Value, names: &Names) -> Value {
    match value {
        Value::String(text) => {
            let mut text = text.clone();
            for (path, name) in names {
                text = text.replace(path.as_str(), name);
            }
            Value::String(version_free(&text))
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|item| normalize(item, names)).collect())
        }
        other => other.clone(),
    }
}

/// A pip-less virtualenv over `python` holding one module only it can import (as captured).
fn venv(python: &str, base: &Path) -> String {
    let directory = base.join("venv");
    let status = Command::new(python)
        .args(["-m", "venv", "--without-pip"])
        .arg(&directory)
        .status()
        .unwrap();
    assert!(status.success());
    let site = fs::read_dir(directory.join("lib"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path()
        .join("site-packages");
    fs::write(site.join("venvonly.py"), "VALUE = 1\n").unwrap();
    directory.join("bin/python").display().to_string()
}

fn run(case: &Value, python: &str) -> Run {
    let base = tempfile::tempdir().unwrap();
    let base_path = base.path().canonicalize().unwrap();
    let root = base_path.join("project");
    let launched = if case["venv"] == true {
        venv(python, &base_path)
    } else {
        python.to_owned()
    };
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut text = text.as_str().unwrap().to_owned();
        if name == "ici.toml" {
            text.push_str(
                &case["engine"]
                    .as_str()
                    .unwrap_or("")
                    .replace("<venv-python>", &launched),
            );
        }
        fs::write(path, text).unwrap();
    }
    let bin = Path::new(python).parent().unwrap().display().to_string();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args([case["command"].as_str().unwrap(), "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", base.path())
        .env("PATH", format!("{bin}:/usr/bin:/bin"))
        .env("ICI_PYTHON", &launched)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let reports: Vec<_> = fs::read_dir(&root)
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.to_string_lossy().ends_with("_report.json"))
        .collect();
    assert_eq!(
        reports.len(),
        1,
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_str(&fs::read_to_string(&reports[0]).unwrap()).unwrap();
    let names: Names = vec![
        (
            base_path.join("venv/bin/python").display().to_string(),
            "<venv-python>",
        ),
        (root.display().to_string(), "<root>"),
        (
            Path::new(python)
                .canonicalize()
                .unwrap()
                .display()
                .to_string(),
            "<base-python>",
        ),
        (python.to_owned(), "<python>"),
    ];
    let row = |value: &Value, keys: &[&str]| {
        Value::Array(
            keys.iter()
                .map(|key| {
                    normalize(
                        &Value::String(value[*key].as_str().unwrap_or("").into()),
                        &names,
                    )
                })
                .collect(),
        )
    };
    let mut targets: Vec<_> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| row(target, &["file_path", "status", "target_name"]))
        .collect();
    targets.sort_by_key(ToString::to_string);
    let mut findings: Vec<_> = report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| row(finding, &["rule_id", "file_path", "message"]))
        .collect();
    findings.sort_by_key(ToString::to_string);
    let argv = report["tool_evidence"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|record| {
            (
                record["name"].as_str().unwrap_or("").to_owned(),
                record["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|part| normalize(part, &names).as_str().unwrap().to_owned())
                    .collect(),
            )
        })
        .collect();
    Run {
        exit: i64::from(output.status.code().unwrap()),
        status: report["status"].as_str().unwrap().into(),
        targets,
        findings,
        argv,
    }
}

fn sorted(value: &Value) -> Vec<Value> {
    let mut rows: Vec<_> = value
        .as_array()
        .unwrap()
        .iter()
        .map(|row| normalize(row, &Vec::new()))
        .collect();
    rows.sort_by_key(ToString::to_string);
    rows
}

fn argv<'a>(argv: &'a [(String, Vec<String>)], name: &str) -> Option<&'a [String]> {
    argv.iter()
        .find(|(tool, _)| tool == name)
        .map(|(_, argv)| argv.as_slice())
}

#[test]
#[ignore = "needs a Python with pytest and coverage (ICI_TEST_PYTHON)"]
fn legacy_python_tools_are_given_what_frozen_gave_them() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for (name, case) in oracle.as_object().unwrap() {
        let frozen = &case["frozen"];
        let native = run(case, &python);
        assert_eq!(native.exit, frozen["exit"].as_i64().unwrap(), "{name}");
        assert_eq!(native.status, frozen["status"].as_str().unwrap(), "{name}");
        assert_eq!(native.targets, sorted(&frozen["targets"]), "{name}");
        assert_eq!(native.findings, sorted(&frozen["findings"]), "{name}");
        let frozen_argv: Vec<(String, Vec<String>)> = frozen["tool_evidence"]
            .as_array()
            .unwrap()
            .iter()
            .map(|record| {
                (
                    record["name"].as_str().unwrap().to_owned(),
                    record["argv"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|part| part.as_str().unwrap().to_owned())
                        .collect(),
                )
            })
            .collect();
        if let Some(expected) = argv(&frozen_argv, "python -m compileall") {
            let given = argv(&native.argv, "python -m compileall")
                .unwrap_or_else(|| panic!("{name}: no compileall run: {:?}", native.argv));
            let targets = |argv: &[String]| {
                argv.iter()
                    .skip_while(|part| *part != "-f")
                    .skip(1)
                    .cloned()
                    .collect::<Vec<_>>()
            };
            assert_eq!(targets(given), targets(expected), "{name}");
        }
        // The interpreter that ran: frozen resolves it (a venv's python runs as its base).
        for tool in ["python -m compileall", "python import smoke"] {
            if let Some(expected) = argv(&frozen_argv, tool) {
                let given = argv(&native.argv, tool)
                    .unwrap_or_else(|| panic!("{name}: no {tool} run: {:?}", native.argv));
                assert_eq!(given.first(), expected.first(), "{name}: {tool}");
            }
        }
        if let Some(expected) = argv(&frozen_argv, "coverage pytest") {
            let given = argv(&native.argv, "coverage pytest")
                .unwrap_or_else(|| panic!("{name}: no coverage pytest run: {:?}", native.argv));
            assert!(
                given.windows(2).any(|pair| pair == ["-o", "addopts="]),
                "{name}: {given:?}"
            );
            // Native names the test directory absolutely; frozen relative to the same cwd.
            let resolved = |argv: &[String]| {
                argv.last()
                    .map(|target| target.strip_prefix("<root>/").unwrap_or(target).to_owned())
            };
            assert_eq!(resolved(given), resolved(expected), "{name}");
        }
    }
}

fn compat(root: &Path, python: &str) -> (i32, Value, Value) {
    let bin = Path::new(python).parent().unwrap().display().to_string();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["python-compat", "--report"])
        .current_dir(root)
        .env_clear()
        .env("HOME", root.parent().unwrap())
        .env("PATH", format!("{bin}:/usr/bin:/bin"))
        .env("ICI_PYTHON", python)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let read = |name: &str| {
        fs::read_to_string(root.join(name))
            .map(|text| serde_json::from_str(&text).unwrap())
            .unwrap_or(Value::Null)
    };
    (
        output.status.code().unwrap(),
        read(".ici/rust-v1/result.json"),
        read("python_compat_report.json"),
    )
}

/// What compileall reads under a legacy target directory is a run input: it is in the run's
/// source identity, so a change to it is seen, although ici's own discovery did not select it. A
/// file there that resolves outside the project cannot be recorded, so the runtime check is
/// refused instead of compiling it. A schema 2 project keeps its resolved selection.
#[test]
#[ignore = "needs a Python with pytest and coverage (ICI_TEST_PYTHON)"]
fn compileall_reads_are_recorded_run_inputs() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let base = tempfile::tempdir().unwrap();
    let base = base.path().canonicalize().unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src/build")).unwrap();
    fs::write(root.join("src/a.py"), "def one():\n    return 1\n").unwrap();
    fs::write(root.join("src/build/n.py"), "X = 1\n").unwrap();
    fs::write(
        root.join("pyproject.toml"),
        "[project]\nname = 'q'\nversion = '1'\nrequires-python = '>=3.10'\n",
    )
    .unwrap();
    fs::write(
        root.join("ici.toml"),
        "[project]\ntype = 'python'\nsource_dirs = ['src']\n",
    )
    .unwrap();
    let (_, first, _) = compat(&root, &python);
    assert_eq!(
        first["identity"]["source"]["files"],
        serde_json::json!(["src/a.py", "src/build/n.py"])
    );
    fs::write(root.join("src/build/n.py"), "X = 2\n").unwrap();
    let (_, second, _) = compat(&root, &python);
    assert_ne!(
        first["identity"]["source"]["digest"],
        second["identity"]["source"]["digest"]
    );
    // Outside the project: refused, not compiled.
    fs::write(base.join("outside.py"), "Y = 1\n").unwrap();
    std::os::unix::fs::symlink("../../outside.py", root.join("src/link.py")).unwrap();
    let (exit, _, report) = compat(&root, &python);
    assert_eq!(exit, 1);
    assert_eq!(report["status"], "ERROR");
    assert!(
        report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .any(|target| target["message"]
                .as_str()
                .unwrap()
                .contains("compileall would read src/link.py, which resolves outside the project")),
        "{report}"
    );
    assert!(
        !report["tool_evidence"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|record| record["name"] == "python -m compileall"),
        "{report}"
    );
    // Schema 2: the resolved selection, not the directory, is compiled and recorded.
    fs::remove_file(root.join("src/link.py")).unwrap();
    fs::remove_dir_all(root.join(".ici")).unwrap();
    fs::write(root.join("src/build/n.py"), "def broken(:\n").unwrap();
    fs::write(root.join(".hg"), "").unwrap();
    fs::write(
        root.join("ici.toml"),
        format!(
            "schema_version = 2\n[python]\nexecutable = {python:?}\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"python\"]\nsources = [\"src/**\"]\n"
        ),
    )
    .unwrap();
    let (exit, v2, _) = compat(&root, &python);
    assert_eq!(exit, 0, "{v2}");
    assert_eq!(
        v2["identity"]["source"]["files"],
        serde_json::json!(["src/a.py"])
    );
}
