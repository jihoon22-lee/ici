//! Legacy `verify` launched through an `ICI_PYTHON` that spells `..`, through the real CLI's saved
//! results (frozen capture: `tests/rust-parity/python/capture_dotted_interpreter.py`). Frozen's
//! launcher execs the value as given, so `sys.executable` keeps the dots and the run is the same
//! run as under the clean spelling. Native used to refuse it: test, python_compat and sanitize
//! became "validation paths must be normalized" and the suite exited 3.
//!
//! What may differ between the spellings is decisions.md "`ICI_PYTHON` 표기와 실행 identity": the
//! recorded argv, and the scope digest of exactly the checks that run the interpreter. Findings,
//! fingerprints and a baseline across spellings may not.
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    process::Command,
};

/// The native checks whose invocation runs the interpreter; no other check's scope may move.
const INTERPRETER_CHECKS: [&str; 5] = [
    "python.type",
    "python.test",
    "python.coverage",
    "python.compat-runtime",
    "python.sanitize",
];
/// The engines those checks project into: they run the interpreter, so they are never reused.
const INTERPRETER_ENGINES: [&str; 4] = ["type", "test", "python_compat", "sanitize"];

fn venv() -> PathBuf {
    std::env::var("ICI_TEST_PYTHON")
        .map(|python| Path::new(&python).parent().unwrap().to_path_buf())
        .unwrap_or_else(|_| Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.venv/bin"))
        .canonicalize()
        .expect("the repository .venv (uv sync --frozen) or ICI_TEST_PYTHON")
        .parent()
        .unwrap()
        .to_path_buf()
}

struct Project {
    _directory: tempfile::TempDir,
    base: PathBuf,
    root: PathBuf,
}
impl Project {
    fn new(tree: &Value) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        fs::write(base.join(".hg"), "").unwrap();
        let root = base.join("project");
        for (relative, text) in tree.as_object().unwrap() {
            let path = root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text.as_str().unwrap()).unwrap();
        }
        Self {
            _directory: directory,
            base,
            root,
        }
    }
    fn verify(&self, interpreter: &Path, options: &[&str]) -> (i32, Value) {
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(["verify", "--report"])
            .args(options)
            .current_dir(&self.root)
            .env_clear()
            .env("HOME", &self.base)
            .env("PATH", "/usr/bin:/bin")
            .env("ICI_PYTHON", interpreter)
            .env("GIT_CEILING_DIRECTORIES", &self.base)
            .output()
            .unwrap();
        let text = fs::read_to_string(self.root.join("verify_report.json"))
            .unwrap_or_else(|error| panic!("{error}: {}", String::from_utf8_lossy(&output.stderr)));
        (
            output.status.code().unwrap(),
            serde_json::from_str(&text).unwrap(),
        )
    }
}

fn engines(report: &Value) -> &[Value] {
    report["results"].as_array().unwrap()
}

fn scope_digests(report: &Value) -> BTreeMap<String, String> {
    report["native_run"]["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            (
                check["check_id"].as_str().unwrap().to_owned(),
                check["scope_digest"].as_str().unwrap().to_owned(),
            )
        })
        .collect()
}

/// Only timing and per-process identity go: the run id, and the probe's process group number.
/// The scope digest stays compared here except for the checks `scopes_move_only_with_the_run`
/// proves separately.
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
            map.retain(|key, item| {
                !(matches!(key.as_str(), "duration" | "duration_seconds" | "run_id")
                    || (key == "group" && item.is_number()))
            });
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

fn scopes_move_only_with_the_run(clean: &Value, again: &Value, dotted: &Value) {
    let (first, second, other) = (
        scope_digests(clean),
        scope_digests(again),
        scope_digests(dotted),
    );
    assert_eq!(first, second, "the same spelling keeps every scope");
    let moved: BTreeSet<&str> = first
        .iter()
        .filter(|(check, digest)| other[*check] != **digest)
        .map(|(check, _)| check.as_str())
        .collect();
    assert_eq!(moved, BTreeSet::from(INTERPRETER_CHECKS));
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
    let project = Project::new(&oracle["tree"]);
    let mut runs = vec![];
    for (case, interpreter) in [("clean", &clean), ("dotted", &dotted)] {
        let (exit, report) = project.verify(interpreter, &["--no-cache"]);
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
            let fingerprints: BTreeSet<&str> = engine["findings"]
                .as_array()
                .unwrap()
                .iter()
                .map(|finding| finding["fingerprint"].as_str().unwrap())
                .collect();
            let frozen_fingerprints: BTreeSet<&str> = expected["fingerprints"]
                .as_array()
                .unwrap()
                .iter()
                .map(|fingerprint| fingerprint.as_str().unwrap())
                .collect();
            assert_eq!(fingerprints, frozen_fingerprints, "{case} {name}");
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
    let (_, again) = project.verify(&clean, &["--no-cache"]);
    scopes_move_only_with_the_run(&runs[0], &again, &runs[1]);

    let mut dotted_run = runs.pop().unwrap();
    let mut clean_run = runs.pop().unwrap();
    for run in [&mut dotted_run, &mut clean_run] {
        let run_id = run["native_run"]["run_id"].as_str().unwrap().to_owned();
        comparable(run, &run_id, &dotted_prefix, &clean_prefix);
    }
    assert_eq!(dotted_run, clean_run);

    // The moved scope is not part of the legacy baseline: it pairs the same findings as frozen.
    let (written, _) = project.verify(&clean, &["--no-cache", "--write-baseline", "base.json"]);
    assert_eq!(written, 0);
    let (exit, compared) = project.verify(&dotted, &["--no-cache", "--baseline", "base.json"]);
    let frozen = &oracle["cases"]["baseline"];
    let comparison = &compared["baseline_comparison"];
    assert_eq!(i64::from(exit), frozen["exit_code"].as_i64().unwrap());
    assert_eq!(comparison["gate_failed"], frozen["gate_failed"]);
    let counts = json!({
        "moved_count": comparison["moved_count"],
        "new_count": comparison["new_count"],
        "resolved_count": comparison["resolved_count"],
        "unchanged_count": comparison["unchanged_count"],
    });
    assert_eq!(counts, frozen["counts"]);
}

/// Frozen's key covers `sys.executable`, so another spelling recomputes every engine; the native
/// observation key covers what the check runs (decisions.md row "identity"). An engine that does
/// not run the interpreter is reused across spellings with the same result, and one that does is
/// never reused. Cold results are compared above; this pins that reuse changes nothing.
#[test]
fn a_cached_run_under_another_spelling_reuses_only_what_does_not_run_the_interpreter() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/dotted-interpreter-oracle.json"
    ))
    .unwrap();
    let frozen = &oracle["cases"]["cache"];
    assert_eq!(frozen["other_spelling_hits"], json!([]));
    let venv = venv();
    let (clean, dotted) = (venv.join("bin/python"), venv.join("bin/../bin/python"));
    let project = Project::new(&oracle["tree"]);
    let hits = |report: &Value| -> BTreeSet<String> {
        engines(report)
            .iter()
            .filter(|engine| engine["cache_hit"] == true)
            .map(|engine| engine["engine_name"].as_str().unwrap().to_owned())
            .collect()
    };
    let keys = |report: &Value| -> BTreeMap<String, Value> {
        engines(report)
            .iter()
            .map(|engine| {
                (
                    engine["engine_name"].as_str().unwrap().to_owned(),
                    engine["cache_key"].clone(),
                )
            })
            .collect()
    };
    project.verify(&clean, &[]);
    let (_, warm) = project.verify(&clean, &[]);
    let (exit, other) = project.verify(&dotted, &[]);
    assert!(!hits(&warm).is_empty(), "the control run reuses something");
    assert_eq!(hits(&other), hits(&warm));
    for engine in INTERPRETER_ENGINES {
        assert!(!hits(&other).contains(engine), "{engine} was reused");
    }
    let (warm_keys, other_keys) = (keys(&warm), keys(&other));
    for engine in hits(&other) {
        assert_eq!(warm_keys[&engine], other_keys[&engine], "{engine}");
    }
    // Reuse does not change a verdict or a finding against frozen's recomputed run.
    let cold = &oracle["cases"]["dotted"];
    assert_eq!(i64::from(exit), cold["exit_code"].as_i64().unwrap());
    for engine in engines(&other) {
        let name = engine["engine_name"].as_str().unwrap();
        assert_eq!(engine["status"], cold["engines"][name]["status"], "{name}");
        let fingerprints: BTreeSet<&str> = engine["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| finding["fingerprint"].as_str().unwrap())
            .collect();
        let expected: BTreeSet<&str> = cold["engines"][name]["fingerprints"]
            .as_array()
            .unwrap()
            .iter()
            .map(|fingerprint| fingerprint.as_str().unwrap())
            .collect();
        assert_eq!(fingerprints, expected, "{name}");
    }
}
