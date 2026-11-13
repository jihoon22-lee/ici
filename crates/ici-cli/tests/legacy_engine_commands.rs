//! The legacy Python-family and source engine commands (`ici python-compat`, `ici resource`,
//! `ici security`, `ici type`, `ici exception`, `ici dup`) on C++ trees beyond the verify-matrix
//! fixture, against tests/rust-parity/cpp/engine-commands-oracle.json
//! (tests/rust-parity/cpp/capture_engine_commands.py, frozen v0.13.0, two captures
//! byte-identical). Each command runs with `--report` (exit, status, evidence, summary, score,
//! required, every frozen `extra` key and value (native may add its named evidence keys:
//! decisions.md "legacy `extra` 안 native 증거 키"), every target and every finding's rule,
//! message, severity, confidence, category, location and fingerprint) and without it (the exit
//! and the console text exactly). A required engine whose evidence did not complete (frozen
//! ERROR/NOT_RUN, exit 1) exits 3 natively (accepted policy incomplete-exit-3, finding_site
//! exception.py:150 and legacy_cpp_metric_sources.rs); its console text is still frozen's. Trees: the
//! legacy-scope fixture; C++ clone groups and exception violations under default and configured
//! keys (dup `warn_pct`/`fail_pct`/`min_window`/`mode`, exception `mode`/`required`, a disabled
//! engine that the typed command re-enables, required flips); a C-only tree; a C++ source that
//! is not UTF-8 (the source-input failure path of dup and exception); a hybrid tree whose Python
//! half has resource and security findings.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/engine-commands-oracle.json");
const COMMANDS: [&str; 6] = [
    "python-compat",
    "resource",
    "security",
    "type",
    "exception",
    "dup",
];
const TARGET_FIELDS: [&str; 6] = [
    "file_path",
    "start_line",
    "end_line",
    "target_name",
    "status",
    "message",
];
const FINDING_FIELDS: [&str; 6] = [
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "fingerprint",
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

fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|at| u8::from_str_radix(&text[at..at + 2], 16).unwrap())
        .collect()
}

/// The frozen capture's report view (`capture_engine_commands.report_view`).
fn report_view(report: &Value) -> Value {
    let pick = |item: &Value, fields: &[&str]| -> Value {
        Value::Object(
            fields
                .iter()
                .map(|key| ((*key).to_owned(), item[*key].clone()))
                .collect(),
        )
    };
    let mut findings: Vec<Value> = report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            let mut view = pick(finding, &FINDING_FIELDS);
            view["path"] = finding["primary_location"]["path"].clone();
            view["start_line"] = finding["primary_location"]["start_line"].clone();
            view
        })
        .collect();
    findings.sort_by(|a, b| a["fingerprint"].as_str().cmp(&b["fingerprint"].as_str()));
    json!({
        "status": report["status"], "evidence": report["evidence"], "summary": report["summary"],
        "score": report["score"], "required": report["required"], "extra": report["extra"],
        "targets": report["targets"].as_array().unwrap().iter()
            .map(|target| pick(target, &TARGET_FIELDS)).collect::<Vec<_>>(),
        "findings": findings,
    })
}

/// The native limitation an ICI_PYTHON setting adds (the legacy launcher rule's own note,
/// ici-config resolve.rs).
const PINNED_INTERPRETER_NOTE: &str = "ICI_PYTHON selects the historical pyz interpreter and is \
    not a Rust runtime setting; declare project python.executable when needed";

/// The replay pin of the hybrid tree: ICI_PYTHON is an explicit pin of a replay input that
/// depends on frozen's `sys.executable` (frozen python-compat probes its own interpreter's
/// version and compiles with it, engines/python_compat.py:305-309), so it must be the runtime that
/// ran frozen and nothing else. `ICI_TEST_PYTHON` must be the capture's recorded runtime
/// (`capture_python`), at the version frozen's `PythonRuntime:Verified` target names, and must
/// still import `compileall` (what that check runs). No third-party module is involved: frozen's
/// `ici type` found no mypy on PATH (its AST fallback), whatever the interpreter. Returns it.
fn assert_capture_runtime(oracle: &Value) -> String {
    let python =
        std::env::var("ICI_TEST_PYTHON").expect("the frozen capture runtime (ICI_TEST_PYTHON)");
    assert_eq!(
        oracle["capture_python"].as_str(),
        Some(python.as_str()),
        "ICI_TEST_PYTHON is not the frozen capture runtime"
    );
    let message = oracle["runs"]["python-compat:hybrid:report"]["report"]["targets"]
        .as_array()
        .unwrap()
        .iter()
        .find(|target| target["target_name"] == "PythonRuntime:Verified")
        .and_then(|target| target["message"].as_str())
        .expect("the frozen PythonRuntime:Verified target");
    let version = message
        .strip_prefix("<PYTHON> (")
        .and_then(|rest| rest.split_once(')'))
        .map(|(version, _)| version)
        .unwrap_or_else(|| panic!("frozen runtime message: {message}"));
    let neutral = tempfile::tempdir().unwrap();
    let output = std::process::Command::new(&python)
        .args([
            "-I",
            "-c",
            "import compileall, sys; print('.'.join(map(str, sys.version_info[:3])))",
        ])
        .current_dir(neutral.path())
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim_end(),
        version,
        "the capture runtime changed"
    );
    python
}

/// The pin's own trace, on the FULL saved report before its view is extracted (the view keeps no
/// `native_run`): the ICI_PYTHON note is the native run's limitation exactly once when the replay
/// set ICI_PYTHON and appears nowhere when it did not. The count is over the whole report text,
/// and the run's own `/native_run/limitations` must exist, so it is never taken over nothing.
fn pinned_interpreter_note(label: &str, text: &str, pinned: bool) -> Vec<String> {
    let mut differences = vec![];
    let total = text.matches(PINNED_INTERPRETER_NOTE).count();
    if total != usize::from(pinned) {
        differences.push(format!(
            "{label}: the ICI_PYTHON note appears {total} times in the report (pinned: {pinned})"
        ));
    }
    let report: Value = serde_json::from_str(text).unwrap();
    match report
        .pointer("/native_run/limitations")
        .and_then(Value::as_array)
    {
        None => differences.push(format!("{label}: no native run with limitations")),
        Some(limitations) => {
            let at = limitations
                .iter()
                .filter(|item| *item == PINNED_INTERPRETER_NOTE)
                .count();
            if at != usize::from(pinned) {
                differences.push(format!(
                    "{label}: /native_run/limitations holds the note {at} times (pinned: {pinned})"
                ));
            }
        }
    }
    differences
}

/// Run `ici <command> [--report]` on one captured tree; `(exit, report view or console text,
/// pin-note differences of the saved report)`.
fn run(
    tree: &Value,
    command: &str,
    report: bool,
    python: Option<&str>,
) -> (i64, Value, Vec<String>) {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    match tree["files"].as_object() {
        None => copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
            &root,
        ),
        Some(files) => {
            let binary: Vec<&str> = tree["binary"]
                .as_array()
                .unwrap()
                .iter()
                .map(|path| path.as_str().unwrap())
                .collect();
            for (relative, text) in files {
                let path = root.join(relative);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                let text = text.as_str().unwrap();
                if binary.contains(&relative.as_str()) {
                    fs::write(path, hex(text)).unwrap();
                } else {
                    fs::write(path, text).unwrap();
                }
            }
        }
    }
    fs::write(root.join("ici.toml"), tree["config"].as_str().unwrap()).unwrap();
    let root = root.canonicalize().unwrap();
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let mut environment = BTreeMap::from([
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        ("LC_CTYPE".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            work.path().display().to_string(),
        ),
    ]);
    if let Some(python) = python {
        // Explicit pin of a replay input that depends on frozen's `sys.executable`: frozen's
        // python-compat probes its own interpreter, the capture runtime
        // (`assert_capture_runtime`); native names it as ICI_PYTHON (decisions.md "legacy
        // capability inventory의 Python 모듈 도구", replay pin (A)/(A')).
        environment.insert("ICI_PYTHON".into(), python.into());
    }
    let mut arguments = vec!["ici", command];
    if report {
        arguments.push("--report");
    }
    let invocation = registry::parse(arguments).unwrap();
    let result = commands::dispatch(&invocation, &root, &environment).unwrap();
    let normalize = |text: &str| {
        let text = text.replace(&root.display().to_string(), "<ROOT>");
        match python {
            Some(python) => text.replace(python, "<PYTHON>"),
            None => text,
        }
    };
    let mut notes = vec![];
    let view = if report {
        let path = root.join(format!("{}_report.json", command.replace('-', "_")));
        match fs::read_to_string(path) {
            Ok(text) => {
                // Counted before the view is extracted.
                notes = pinned_interpreter_note(command, &text, python.is_some());
                report_view(&serde_json::from_str(&normalize(&text)).unwrap())
            }
            // Every captured run saved a report; the view comparison names the missing one.
            Err(_) => Value::Null,
        }
    } else {
        json!(normalize(&result.stdout))
    };
    (i64::from(result.exit_code), view, notes)
}

/// Frozen literals per tree, so a recapture that drifts fails here too:
/// (tree, command, exit, status).
const EXPECTED: [(&str, &str, i64, &str); 42] = [
    ("scope", "python-compat", 2, "SKIP"),
    ("scope", "resource", 2, "SKIP"),
    ("scope", "security", 2, "SKIP"),
    ("scope", "type", 2, "SKIP"),
    ("scope", "exception", 1, "FAIL"),
    ("scope", "dup", 0, "PASS"),
    ("clones", "python-compat", 2, "SKIP"),
    ("clones", "resource", 2, "SKIP"),
    ("clones", "security", 2, "SKIP"),
    ("clones", "type", 2, "SKIP"),
    ("clones", "exception", 1, "FAIL"),
    ("clones", "dup", 0, "WARN"),
    ("clones-configured", "python-compat", 2, "SKIP"),
    ("clones-configured", "resource", 2, "SKIP"),
    ("clones-configured", "security", 2, "SKIP"),
    ("clones-configured", "type", 2, "SKIP"),
    ("clones-configured", "exception", 0, "WARN"),
    ("clones-configured", "dup", 1, "FAIL"),
    ("clones-wide-window", "python-compat", 2, "SKIP"),
    ("clones-wide-window", "resource", 2, "SKIP"),
    ("clones-wide-window", "security", 2, "SKIP"),
    ("clones-wide-window", "type", 2, "SKIP"),
    ("clones-wide-window", "exception", 1, "FAIL"),
    ("clones-wide-window", "dup", 0, "PASS"),
    ("c-only", "python-compat", 2, "SKIP"),
    ("c-only", "resource", 2, "SKIP"),
    ("c-only", "security", 2, "SKIP"),
    ("c-only", "type", 2, "SKIP"),
    ("c-only", "exception", 0, "PASS"),
    ("c-only", "dup", 0, "PASS"),
    ("not-utf8", "python-compat", 2, "SKIP"),
    ("not-utf8", "resource", 2, "SKIP"),
    ("not-utf8", "security", 2, "SKIP"),
    ("not-utf8", "type", 2, "SKIP"),
    ("not-utf8", "exception", 1, "ERROR"),
    ("not-utf8", "dup", 1, "ERROR"),
    ("hybrid", "python-compat", 0, "PASS"),
    ("hybrid", "resource", 0, "WARN"),
    ("hybrid", "security", 0, "WARN"),
    ("hybrid", "type", 0, "WARN"),
    ("hybrid", "exception", 1, "FAIL"),
    ("hybrid", "dup", 0, "WARN"),
];

fn replay(trees: &[&str], python: Option<&str>) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    assert_eq!(
        oracle["runs"].as_object().unwrap().len(),
        EXPECTED.len() * 2
    );
    let mut mismatches = vec![];
    for (tree, command, exit, status) in EXPECTED {
        if !trees.contains(&tree) {
            continue;
        }
        assert!(COMMANDS.contains(&command));
        let report = &oracle["runs"][format!("{command}:{tree}:report")];
        let console = &oracle["runs"][format!("{command}:{tree}:console")];
        assert_eq!(report["exit_code"], exit, "{tree} {command}: frozen exit");
        assert_eq!(console["exit_code"], exit, "{tree} {command}: frozen exit");
        assert_eq!(
            report["report"]["status"], status,
            "{tree} {command}: frozen"
        );
        let definition = &oracle["trees"][tree];
        for (saved, frozen) in [(true, report), (false, console)] {
            let (native_exit, mut view, notes) = run(definition, command, saved, python);
            mismatches.extend(
                notes
                    .into_iter()
                    .map(|note| format!("{tree} {command} report={saved}: {note}")),
            );
            let incomplete = report["report"]["status"] == "ERROR"
                && report["report"]["evidence"] == "NOT_RUN"
                && report["report"]["required"] == true;
            let wanted = if incomplete && exit == 1 { 3 } else { exit };
            if native_exit != wanted {
                mismatches.push(format!(
                    "{tree} {command} report={saved}: exit native {native_exit} expected {wanted} (frozen {exit})"
                ));
            }
            let expected = if saved {
                &frozen["report"]
            } else {
                &frozen["stdout"]
            };
            if saved && view.is_object() {
                // Frozen keys and values; a native evidence key beside them is not frozen's.
                let frozen_extra = expected["extra"].as_object().cloned().unwrap_or_default();
                if let Some(extra) = view["extra"].as_object_mut() {
                    extra.retain(|key, _| frozen_extra.contains_key(key));
                }
            }
            if &view != expected {
                mismatches.push(format!(
                    "{tree} {command} report={saved}:\n native {view}\n frozen {expected}"
                ));
            }
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}

/// The C/C++-only trees: positive (clean and measured clone/exception verdicts, a C-only tree),
/// negative (the Python-family engines' not-applicable SKIP, exit 2) and failure (the non-UTF-8
/// source's dup and exception ERROR, exit 1). No interpreter is run.
#[test]
fn legacy_engine_commands_on_cpp_trees_equal_frozen_reports_and_console() {
    replay(
        &[
            "scope",
            "clones",
            "clones-configured",
            "clones-wide-window",
            "c-only",
            "not-utf8",
        ],
        None,
    );
}

/// The hybrid tree: python-compat probes the capture runtime, resource and security report the
/// Python half's findings, type is the Mypy-unavailable AST fallback beside the C++ type SKIPs.
#[test]
#[ignore = "explicit capture runtime CPython (ICI_TEST_PYTHON) that frozen ran as sys.executable"]
fn legacy_engine_commands_on_a_hybrid_tree_equal_frozen_reports_and_console() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let python = assert_capture_runtime(&oracle);
    replay(&["hybrid"], Some(&python));
}
