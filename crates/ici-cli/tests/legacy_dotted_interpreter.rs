//! Legacy `verify` launched through an `ICI_PYTHON` that spells `..`, through the real CLI's saved
//! results (frozen capture: `tests/rust-parity/python/capture_dotted_interpreter.py`). Frozen's
//! launcher execs the value as given, so `sys.executable` keeps the dots and the run is the same
//! run as under the clean spelling. Native used to refuse it: test, python_compat and sanitize
//! became "validation paths must be normalized" and the suite exited 3.
use serde_json::Value;
use std::{fs, path::Path, process::Command};

fn venv() -> std::path::PathBuf {
    std::env::var("ICI_TEST_PYTHON")
        .map(|python| Path::new(&python).parent().unwrap().to_path_buf())
        .unwrap_or_else(|_| Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.venv/bin"))
        .canonicalize()
        .expect("the repository .venv (uv sync --frozen) or ICI_TEST_PYTHON")
        .parent()
        .unwrap()
        .to_path_buf()
}

fn verify(tree: &Value, interpreter: &Path) -> (i32, Value) {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let project = base.join("project");
    for (relative, text) in tree.as_object().unwrap() {
        let path = project.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["verify", "--report", "--no-cache"])
        .current_dir(&project)
        .env_clear()
        .env("HOME", &base)
        .env("PATH", "/usr/bin:/bin")
        .env("ICI_PYTHON", interpreter)
        .env("GIT_CEILING_DIRECTORIES", &base)
        .output()
        .unwrap();
    let text = fs::read_to_string(project.join("verify_report.json"))
        .unwrap_or_else(|error| panic!("{error}: {}", String::from_utf8_lossy(&output.stderr)));
    let text = text.replace(&*base.to_string_lossy(), "<base>");
    (
        output.status.code().unwrap(),
        serde_json::from_str(&text).unwrap(),
    )
}

/// Timing, run identity and the temporary tree differ between any two runs; nothing else may.
/// The checks that run the interpreter hash its spelling into their scope; the others do not.
fn comparable(value: &mut Value, run_id: &str, dotted: &str, clean: &str) {
    match value {
        Value::Object(map) => {
            if map
                .get("check_id")
                .and_then(Value::as_str)
                .is_some_and(|check| INTERPRETER_CHECKS.contains(&check))
            {
                map.remove("scope_digest");
            }
            map.retain(|key, _| !VOLATILE.contains(&key.as_str()));
            map.values_mut()
                .for_each(|item| comparable(item, run_id, dotted, clean));
        }
        Value::Array(items) => items
            .iter_mut()
            .for_each(|item| comparable(item, run_id, dotted, clean)),
        Value::String(text) => *text = text.replace(dotted, clean).replace(run_id, "<run>"),
        _ => {}
    }
}
const VOLATILE: &[&str] = &[
    "duration",
    "duration_seconds",
    "cache_key",
    "run_id",
    "group",
];
const INTERPRETER_CHECKS: &[&str] = &[
    "python.type",
    "python.test",
    "python.coverage",
    "python.compat-runtime",
    "python.sanitize",
];

fn engines(report: &Value) -> &[Value] {
    report["results"].as_array().unwrap()
}

#[test]
fn a_dotted_interpreter_is_the_same_legacy_run_as_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/dotted-interpreter-oracle.json"
    ))
    .unwrap();
    let venv = venv();
    let clean = venv.join("bin/python");
    let dotted = venv.join("bin/../bin/python");
    let (dotted_prefix, clean_prefix) = (
        format!("{}/bin/../bin/", venv.display()),
        format!("{}/bin/", venv.display()),
    );
    let mut runs = vec![];
    for (case, interpreter) in [("clean", &clean), ("dotted", &dotted)] {
        let (exit, report) = verify(&oracle["tree"], interpreter);
        let frozen = &oracle["cases"][case];
        assert_eq!(
            i64::from(exit),
            frozen["exit_code"].as_i64().unwrap(),
            "{case}"
        );
        for engine in engines(&report) {
            let name = engine["engine_name"].as_str().unwrap();
            let expected = &frozen["engines"][name];
            assert_eq!(
                engine["status"], expected["status"],
                "{case} {name}: {engine:#}"
            );
            assert_eq!(engine["evidence"], expected["evidence"], "{case} {name}");
            assert_eq!(engine["summary"], expected["summary"], "{case} {name}");
            // The tool runs under the spelling it was given, as frozen's `sys.executable` did.
            if let Some(spelled) = expected["interpreter"].as_str() {
                let spelled = spelled.replace("<venv>", &venv.display().to_string());
                assert!(
                    engine["tool_evidence"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|record| record["argv"][0] == spelled.as_str()),
                    "{case} {name} ran under another spelling than {spelled}: {engine:#}"
                );
            }
        }
        runs.push(report);
    }
    let mut dotted_run = runs.pop().unwrap();
    let mut clean_run = runs.pop().unwrap();
    for run in [&mut dotted_run, &mut clean_run] {
        let run_id = run["native_run"]["run_id"].as_str().unwrap().to_owned();
        comparable(run, &run_id, &dotted_prefix, &clean_prefix);
    }
    assert_eq!(dotted_run, clean_run);
}
