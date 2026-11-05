//! Frozen legacy public-result parity: every frozen v0.13.0 verify/engine capture with a full
//! saved result is replayed end to end through `commands::dispatch` (the real CLI), and the
//! written `verify_report.json` / `<engine>_report.json` is compared with frozen's in full: every
//! key at every depth, JSON types, list lengths and values, with absent key, `null`, `""`, `[]`
//! and `{}` all distinct; plus the exit code. Each allowed difference is one normalized path in
//! `support/legacy_result_allowances.rs` (incidental, approved with its citation, or open), and
//! a stale allowance fails. (Historical name: the `extra` restoration started this harness.)
//!
//! Captures replayed here (the interpreter is pinned to the one the capture used):
//! * `tests/rust-parity/reporting/legacy-summary-oracle.json`, all four `verify` cases;
//! * `tests/rust-parity/reporting/legacy-annotations-oracle.json` (`verify` under Actions);
//! * `tests/rust-parity/python/legacy-inventory-oracle.json`, every engine report (Python
//!   complexity/cycle; hybrid test/resource/security; hybrid lint needs the frozen clang tool
//!   absence and runs with the C++ cases);
//! * `tests/rust-parity/reporting/legacy-extras-oracle.json` (`capture_legacy_extras.py`): C++
//!   default/deep/compilation-database `verify`, hybrid `verify` (explicit g++), and a Python
//!   project whose test interpreter lacks coverage (explicit `ICI_TEST_PYTHON_NEW`); engine
//!   command reports: standalone heuristic `ici cycle`, and `ici lint` / lint-only `ici verify`
//!   behind a project-contained `bin/g++` first on PATH (every source replay rejected, explicit
//!   g++).
//!
//! Elsewhere: the C++ `verify` key sets of `tests/rust-parity/cpp/verify-matrix-oracle.json` are
//! the key sets of the `legacy-extras-oracle.json` C++ default/deep cases (same fixture, g++ and
//! statuses); the zoo answers (filtered `EXTRA_KEYS`) are compared by `legacy_zoo_cpp.rs`; the
//! single-key captures (legacy-engines, legacy-sweep, source-scope, semantic-integration,
//! legacy-test, legacy-python-compat, pytest-views, the C++ lint family) by their own replays.
//! The `legacy-sarif-oracle.json` end-to-end suites are trimmed to what the SARIF writer reads
//! and carry no `extra` (nothing to compare).
//!
//! A frozen key native lacks, or a different value, must be a reviewed difference (intended) or
//! an open C++ item (the native C++ check does not record the value yet; listed so it is never
//! silently accepted); a native-only key must be its own per-path allowance; a reviewed or open
//! entry that no replay observes is stale and fails.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    sync::OnceLock,
};

const SUMMARY: &str =
    include_str!("../../../tests/rust-parity/reporting/legacy-summary-oracle.json");
const ANNOTATIONS: &str =
    include_str!("../../../tests/rust-parity/reporting/legacy-annotations-oracle.json");
const INVENTORY: &str =
    include_str!("../../../tests/rust-parity/python/legacy-inventory-oracle.json");
const EXTRAS: &str = include_str!("../../../tests/rust-parity/reporting/legacy-extras-oracle.json");
const SARIF: &str = include_str!("../../../tests/rust-parity/reporting/legacy-sarif-oracle.json");
const THRESHOLDS: &str =
    include_str!("../../../tests/rust-parity/reporting/python-threshold-oracle.json");
const HYBRID_DUP: &str =
    include_str!("../../../tests/rust-parity/reporting/hybrid-dup-oracle.json");
const HEURISTIC: &str =
    include_str!("../../../tests/rust-parity/cpp/heuristic-metrics-oracle.json");

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Kind {
    /// Frozen wrote the key, native does not.
    Missing,
    /// Native wrote a key frozen does not.
    NativeOnly,
    /// Different JSON types (`null` vs a value included).
    Type,
    /// Lists of different lengths.
    Len,
    /// Same type, different value.
    Value,
}
use Kind::{Len, Missing, NativeOnly, Type, Value as Differs};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Class {
    Incidental,
    Approved,
}
use Class::{Approved, Incidental};

/// (capture or exact replay label, normalized path, kind, class, reason).
type Allowance = (&'static str, &'static str, Kind, Class, &'static str);
/// (text projection label, side (`Missing` frozen line / `NativeOnly` native line), line prefix,
/// `line$` or `head*tail` pattern after the duration mask, class, reason).
type TextAllowance = (&'static str, Kind, &'static str, Class, &'static str);

include!("support/legacy_result_allowances.rs");

thread_local! {
    static HITS: RefCell<BTreeSet<usize>> = const { RefCell::new(BTreeSet::new()) };
}

/// Where the Python captures' project interpreter lived on the capture host.
const CAPTURE_PYTHON: &str = "/home/jihoon/projects/ici/.venv/bin/python";

/// The project interpreter the Python captures ran under (Python 3.10.21 with pytest, coverage
/// and mypy): `ICI_TEST_PYTHON`, else the workspace `.venv` CI creates, without `..` segments.
/// Its location is host data: `replay_with` writes it as `CAPTURE_PYTHON`.
fn project_python() -> String {
    std::env::var("ICI_TEST_PYTHON").unwrap_or_else(|_| {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .unwrap()
            .join(".venv/bin/python")
            .display()
            .to_string()
    })
}

/// The project interpreter's `platform.python_version()` when it differs from the captures'
/// (`python` of the summary oracle), else `None`. The patch release is host data like the
/// interpreter's location: `replay_with` writes it as the captured version.
fn host_python_version() -> Option<String> {
    static VERSION: OnceLock<Option<String>> = OnceLock::new();
    VERSION
        .get_or_init(|| {
            let captured: Value = serde_json::from_str(SUMMARY).unwrap();
            let output = std::process::Command::new(project_python())
                .args(["-c", "import platform; print(platform.python_version())"])
                .output()
                .ok()?;
            let version = String::from_utf8(output.stdout).ok()?.trim().to_owned();
            (output.status.success() && !version.is_empty() && version != captured["python"])
                .then_some(version)
        })
        .clone()
}

fn write_tree(base: &Path, tree: &Value) {
    for (name, text) in tree.as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
}

fn environment(home: &Path, actions: bool) -> BTreeMap<String, String> {
    let mut environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), home.display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("ICI_PYTHON".to_owned(), project_python()),
        (
            "GITHUB_STEP_SUMMARY".to_owned(),
            home.join("summary.md").display().to_string(),
        ),
    ]);
    if actions {
        environment.insert("GITHUB_ACTIONS".into(), "true".into());
    }
    environment
}

/// What one replay saved and printed: the exit codes, the report of the last step (project
/// root as `<root>`, project interpreter as `CAPTURE_PYTHON`), the last step's stdout and the
/// `GITHUB_STEP_SUMMARY` file afterwards.
struct Saved {
    codes: Vec<i32>,
    report: Value,
    stdout: String,
    summary: Option<String>,
}

/// Run the capture's steps in a fresh `project` (with `.git`), applying `edit` before the last;
/// returns the exit codes and the saved report of the last step, project root as `<root>`.
fn replay(
    tree: &Value,
    edit: &Value,
    steps: &[Vec<String>],
    report: &str,
    actions: bool,
) -> (Vec<i32>, Value) {
    let saved = replay_with(tree, edit, steps, report, actions, false, None);
    (saved.codes, saved.report)
}

/// `replay`; with `wrapper`, the capture's project-contained `bin/g++` (delegating to
/// /usr/bin/g++) is first on PATH; `existing` is the step summary file's content before the
/// last step (the capture wrote it there).
fn replay_with(
    tree: &Value,
    edit: &Value,
    steps: &[Vec<String>],
    report: &str,
    actions: bool,
    wrapper: bool,
    existing: Option<&str>,
) -> Saved {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().join("project");
    write_tree(&base, tree);
    fs::create_dir_all(base.join(".git")).unwrap();
    let mut environment = environment(home.path(), actions);
    if wrapper {
        use std::os::unix::fs::PermissionsExt;
        let program = base.join("bin/g++");
        fs::create_dir_all(program.parent().unwrap()).unwrap();
        fs::write(&program, "#!/bin/sh\nexec /usr/bin/g++ \"$@\"\n").unwrap();
        fs::set_permissions(&program, fs::Permissions::from_mode(0o755)).unwrap();
        environment.insert(
            "PATH".into(),
            format!("{}:/usr/bin:/bin", base.join("bin").display()),
        );
    }
    let summary = home.path().join("summary.md");
    let mut codes = vec![];
    let mut stdout = String::new();
    for (index, argv) in steps.iter().enumerate() {
        if index + 1 == steps.len() {
            if edit.is_object() {
                write_tree(&base, edit);
            }
            if let Some(existing) = existing {
                fs::write(&summary, existing).unwrap();
            }
        }
        let invocation =
            registry::parse(std::iter::once("ici").chain(argv.iter().map(String::as_str))).unwrap();
        let output = commands::dispatch(&invocation, &base, &environment).unwrap();
        codes.push(output.exit_code);
        stdout = output.stdout;
    }
    let root = base.display().to_string();
    let python = project_python();
    let captured: Value = serde_json::from_str(SUMMARY).unwrap();
    let version = host_python_version();
    let host = |text: &str| {
        let text = text
            .replace(&root, "<root>")
            .replace(&python, CAPTURE_PYTHON);
        match &version {
            Some(version) => text.replace(version, captured["python"].as_str().unwrap()),
            None => text,
        }
    };
    let text = fs::read_to_string(base.join(report)).unwrap();
    Saved {
        codes,
        report: serde_json::from_str(&host(&text)).unwrap(),
        stdout: host(&stdout),
        summary: fs::read_to_string(&summary).ok().map(|text| host(&text)),
    }
}

fn verify() -> Vec<Vec<String>> {
    vec![vec!["verify".into(), "--report".into()]]
}

fn allowed(label: &str, path: &str, kind: Kind) -> bool {
    let found = ALLOWANCES.iter().position(|entry| {
        (entry.0 == label || label.starts_with(&format!("{}/", entry.0)))
            && entry.1 == path
            && entry.2 == kind
            && !entry.4.is_empty()
    });
    if let Some(index) = found {
        HITS.with(|hits| hits.borrow_mut().insert(index));
    }
    found.is_some()
}

/// Positions are normalized (`[3]` -> `[]`) so an allowance names one path of every element.
fn walk(frozen: &Value, native: &Value, path: &str, out: &mut Vec<(String, Kind)>) {
    // The capability inventory is compared in full except its row set: frozen probed every
    // registered tool, native only the policy's (config.json capability:doctor-env-cache (3)).
    // Policy rows (required or optional by an engine) are keyed by tool name and walked field by
    // field; a frozen row no engine uses is one `tools[unused]` path.
    if path.ends_with("capability_inventory.tools")
        && let (Value::Array(left), Value::Array(right)) = (frozen, native)
    {
        let rows: BTreeMap<&str, &Value> = right
            .iter()
            .map(|row| (row["name"].as_str().unwrap_or(""), row))
            .collect();
        let used = |row: &Value| row["required"] == true || row["optional"] == true;
        // A probed executable's location is compared through its symlinks (`/bin` is
        // `/usr/bin` on a merged-/usr host); value differences name the tool.
        let canonical = |row: &Value| {
            let mut row = row.clone();
            if let Some(found) = row["path"].as_str().filter(|path| path.starts_with('/'))
                && let Ok(resolved) = std::fs::canonicalize(found)
            {
                row["path"] = json!(resolved.display().to_string());
            }
            row
        };
        for row in left {
            let name = row["name"].as_str().unwrap_or("");
            match rows.get(name) {
                Some(other) => {
                    let mut found = vec![];
                    walk(
                        &canonical(row),
                        &canonical(other),
                        &format!("{path}[]"),
                        &mut found,
                    );
                    out.extend(found.into_iter().map(|(field, kind)| match kind {
                        Missing | NativeOnly => (field, kind),
                        _ => (field.replacen("[]", &format!("[{name}]"), 1), kind),
                    }));
                }
                None if used(row) => out.push((format!("{path}[{name}]"), Missing)),
                None => out.push((format!("{path}[unused]"), Missing)),
            }
        }
        // The rows both sides list keep frozen's order (its probe registry order).
        let order = |rows: &[Value], other: &BTreeMap<&str, &Value>| -> Vec<String> {
            rows.iter()
                .filter_map(|row| row["name"].as_str())
                .filter(|name| other.contains_key(name))
                .map(str::to_owned)
                .collect()
        };
        let frozen_rows: BTreeMap<&str, &Value> = left
            .iter()
            .map(|row| (row["name"].as_str().unwrap_or(""), row))
            .collect();
        if order(left, &rows) != order(right, &frozen_rows) {
            out.push((format!("{path}[order]"), Differs));
        }
        let names: BTreeSet<&str> = left.iter().filter_map(|row| row["name"].as_str()).collect();
        for row in right {
            if !names.contains(row["name"].as_str().unwrap_or("")) {
                out.push((format!("{path}[native]"), NativeOnly));
            }
        }
        return;
    }
    // Support-matrix rows are keyed by engine and language, never by position.
    if path.ends_with("support_matrix.entries")
        && let (Value::Array(left), Value::Array(right)) = (frozen, native)
    {
        let key = |row: &Value| {
            format!(
                "{}/{}",
                row["engine_name"].as_str().unwrap_or(""),
                row["language"].as_str().unwrap_or("")
            )
        };
        let rows: BTreeMap<String, &Value> = right.iter().map(|row| (key(row), row)).collect();
        if left.len() != right.len() {
            out.push((path.to_owned(), Len));
        }
        for row in left {
            match rows.get(&key(row)) {
                Some(other) => walk(row, other, &format!("{path}[{}]", key(row)), out),
                None => out.push((format!("{path}[{}]", key(row)), Missing)),
            }
        }
        return;
    }
    match (frozen, native) {
        (Value::Object(left), Value::Object(right)) => {
            for (key, value) in left {
                let child = format!("{path}.{key}");
                match right.get(key) {
                    Some(other) => walk(value, other, &child, out),
                    None => out.push((child, Missing)),
                }
            }
            for key in right.keys().filter(|key| !left.contains_key(*key)) {
                out.push((format!("{path}.{key}"), NativeOnly));
            }
        }
        (Value::Array(left), Value::Array(right)) => {
            if left.len() != right.len() {
                out.push((path.to_owned(), Len));
            }
            for (a, b) in left.iter().zip(right) {
                walk(a, b, &format!("{path}[]"), out);
            }
        }
        (Value::Number(left), Value::Number(right)) => {
            // An integer and a float are different JSON types (`0` is not `0.0`).
            if left.is_f64() != right.is_f64() {
                out.push((path.to_owned(), Type));
            } else if left.as_f64() != right.as_f64() {
                out.push((path.to_owned(), Differs));
            }
        }
        (left, right) if std::mem::discriminant(left) != std::mem::discriminant(right) => {
            out.push((path.to_owned(), Type));
        }
        (left, right) => {
            if left != right {
                out.push((path.to_owned(), Differs));
            }
        }
    }
}

/// Every difference of a saved legacy result (a suite, or one engine report when `suite` is
/// false) that no allowance names.
fn result_differences(label: &str, frozen: &Value, native: &Value) -> Vec<String> {
    let mut out = vec![];
    if frozen.get("results").is_some() {
        let (left, right) = (frozen.as_object().unwrap(), native.as_object().unwrap());
        for (key, value) in left.iter().filter(|(key, _)| *key != "results") {
            match right.get(key) {
                Some(other) => walk(value, other, key, &mut out),
                None => out.push((key.clone(), Missing)),
            }
        }
        for key in right.keys().filter(|key| !left.contains_key(*key)) {
            out.push((key.clone(), NativeOnly));
        }
        let names = |suite: &Value| -> Vec<Value> {
            results(suite)
                .iter()
                .map(|engine| engine["engine_name"].clone())
                .collect()
        };
        assert_eq!(names(native), names(frozen), "{label}: engines");
        for (left, right) in results(frozen).iter().zip(results(native)) {
            let path = format!("results[{}]", left["engine_name"].as_str().unwrap());
            walk(left, right, &path, &mut out);
        }
    } else {
        walk(frozen, native, "", &mut out);
    }
    out.into_iter()
        .filter(|(path, kind)| !allowed(label, path, *kind))
        .map(|(path, kind)| format!("{label}: {path} ({kind:?})"))
        .collect()
}

/// A wall-clock `<n>.<nn>s` (the step summary's run and engine durations) is incidental (R4).
fn mask_seconds(line: &str) -> String {
    let bytes = line.as_bytes();
    let mut out = String::new();
    let mut index = 0;
    while index < bytes.len() {
        let start = index;
        let boundary = start == 0 || !bytes[start - 1].is_ascii_alphanumeric();
        let mut end = start;
        while end < bytes.len() && bytes[end].is_ascii_digit() {
            end += 1;
        }
        let seconds = boundary
            && end > start
            && bytes.get(end) == Some(&b'.')
            && bytes.get(end + 1).is_some_and(u8::is_ascii_digit)
            && bytes.get(end + 2).is_some_and(u8::is_ascii_digit)
            && bytes.get(end + 3) == Some(&b's')
            && !bytes.get(end + 4).is_some_and(u8::is_ascii_alphanumeric);
        if seconds {
            out.push_str("<seconds>");
            index = end + 4;
        } else {
            let next = line[start..].chars().next().unwrap();
            out.push(next);
            index = start + next.len_utf8();
        }
    }
    out
}

/// Every line one side has and the other lacks (a longest-common-subsequence alignment of the
/// two texts after the incidental duration mask) that no text allowance names: `Missing` for a
/// frozen line, `NativeOnly` for a native one.
fn text_differences(label: &str, frozen: &str, native: &str) -> Vec<String> {
    let left: Vec<String> = frozen.lines().map(mask_seconds).collect();
    let right: Vec<String> = native.lines().map(mask_seconds).collect();
    let (n, m) = (left.len(), right.len());
    let mut table = vec![vec![0u32; m + 1]; n + 1];
    for i in (0..n).rev() {
        for j in (0..m).rev() {
            table[i][j] = if left[i] == right[j] {
                table[i + 1][j + 1] + 1
            } else {
                table[i + 1][j].max(table[i][j + 1])
            };
        }
    }
    let (mut i, mut j, mut out) = (0, 0, vec![]);
    while i < n || j < m {
        if i < n && j < m && left[i] == right[j] {
            i += 1;
            j += 1;
        } else if j == m || (i < n && table[i + 1][j] >= table[i][j + 1]) {
            out.push((Missing, &left[i]));
            i += 1;
        } else {
            out.push((NativeOnly, &right[j]));
            j += 1;
        }
    }
    out.into_iter()
        .filter(|(kind, line)| !text_allowed(label, line, *kind))
        .map(|(kind, line)| format!("{label}: {kind:?} line {line:?}"))
        .collect()
}

thread_local! {
    static TEXT_HITS: RefCell<BTreeSet<usize>> = const { RefCell::new(BTreeSet::new()) };
}

fn text_allowed(label: &str, line: &str, kind: Kind) -> bool {
    let found = TEXT_ALLOWANCES.iter().position(|entry| {
        (entry.0 == label || label.starts_with(&format!("{}/", entry.0)))
            && entry.1 == kind
            && line_matches(line, entry.2)
            && !entry.4.is_empty()
    });
    if let Some(index) = found {
        TEXT_HITS.with(|hits| hits.borrow_mut().insert(index));
    }
    found.is_some()
}

/// A text allowance names a line prefix; `line$` names exactly that line; `head*tail` names a
/// line starting with `head` whose rest contains `tail`.
fn line_matches(line: &str, pattern: &str) -> bool {
    if let Some(exact) = pattern.strip_suffix('$') {
        return line == exact;
    }
    match pattern.split_once('*') {
        Some((head, tail)) => line
            .strip_prefix(head)
            .is_some_and(|rest| rest.contains(tail)),
        None => line.starts_with(pattern),
    }
}

fn assert_text_not_stale(scope: impl Fn(&TextAllowance) -> bool) {
    let stale: Vec<String> = TEXT_HITS.with(|hits| {
        let hits = hits.borrow();
        TEXT_ALLOWANCES
            .iter()
            .enumerate()
            .filter(|(index, entry)| scope(entry) && !hits.contains(index))
            .map(|(_, entry)| format!("{} {:?} {:?}", entry.0, entry.1, entry.2))
            .collect()
    });
    assert!(stale.is_empty(), "stale text allowances: {stale:#?}");
}

fn results(suite: &Value) -> &[Value] {
    suite["results"].as_array().unwrap()
}

/// Frozen exit vs native: equal, or the accepted incomplete exit (reporting.json `command:ici
/// verify` difference_reason): native 3 where frozen exited 1, with a required engine whose
/// evidence both frozen and native report NOT_RUN (ERROR).
fn assert_exit(
    label: &str,
    native: &[i32],
    frozen: &[i32],
    frozen_engines: &[Value],
    native_engines: &[Value],
) {
    if native == frozen {
        return;
    }
    assert_eq!(
        (native.last(), frozen.last()),
        (Some(&3), Some(&1)),
        "{label}: exits {native:?} vs frozen {frozen:?}"
    );
    let not_run = |engines: &[Value]| -> BTreeSet<String> {
        engines
            .iter()
            .filter(|engine| {
                engine["required"] == true
                    && engine["status"] == "ERROR"
                    && engine["evidence"] == "NOT_RUN"
            })
            .map(|engine| engine["engine_name"].as_str().unwrap().to_owned())
            .collect()
    };
    let shared: Vec<String> = not_run(frozen_engines)
        .intersection(&not_run(native_engines))
        .cloned()
        .collect();
    assert!(
        !shared.is_empty(),
        "{label}: exit 3 without a required NOT_RUN engine frozen also reported"
    );
}

fn codes(value: &Value) -> Vec<i32> {
    match value {
        Value::Array(items) => items
            .iter()
            .map(|code| code.as_i64().unwrap() as i32)
            .collect(),
        code => vec![code.as_i64().unwrap() as i32],
    }
}

fn steps(case: &Value) -> Vec<Vec<String>> {
    case["steps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|step| {
            step.as_array()
                .unwrap()
                .iter()
                .map(|argument| argument.as_str().unwrap().to_owned())
                .collect()
        })
        .collect()
}

/// One `legacy-extras-oracle.json` verify case (`substitute` rewrites a capture-host path).
fn extras_case(name: &str, substitute: Option<(&str, &str)>) -> Vec<String> {
    let oracle: Value = serde_json::from_str(EXTRAS).unwrap();
    let case = &oracle["cases"][name];
    let mut tree = case["tree"].clone();
    if let Some((from, to)) = substitute {
        for text in tree.as_object_mut().unwrap().values_mut() {
            *text = Value::from(text.as_str().unwrap().replace(from, to));
        }
    }
    let label = format!("extras/{name}");
    let Some(argv) = case.get("argv") else {
        let (exits, native) = replay(&tree, &Value::Null, &verify(), "verify_report.json", false);
        let frozen = &case["suite"];
        assert_exit(
            &label,
            &exits,
            &codes(&case["exit_code"]),
            results(frozen),
            results(&native),
        );
        return result_differences(&label, frozen, &native);
    };
    // An engine-command case: the saved `<command>_report.json` (a suite for `verify`).
    let argv: Vec<String> = argv
        .as_array()
        .unwrap()
        .iter()
        .map(|argument| argument.as_str().unwrap().to_owned())
        .collect();
    let report = format!("{}_report.json", argv[0]);
    let Saved {
        codes: exits,
        report: native,
        ..
    } = replay_with(
        &tree,
        &Value::Null,
        &[argv],
        &report,
        false,
        case["wrapper"] == true,
        None,
    );
    let frozen = case.get("suite").unwrap_or(&case["report"]);
    assert_eq!(exits, codes(&case["exit_code"]), "{label}: exit");
    result_differences(&label, frozen, &native)
}

/// Every allowance in `scope` was observed by the replays of this test.
fn assert_not_stale(scope: impl Fn(&Allowance) -> bool) {
    let stale: Vec<String> = HITS.with(|hits| {
        let hits = hits.borrow();
        ALLOWANCES
            .iter()
            .enumerate()
            .filter(|(index, entry)| scope(entry) && !hits.contains(index))
            .map(|(_, entry)| format!("{} {} ({:?})", entry.0, entry.1, entry.2))
            .collect()
    });
    assert!(stale.is_empty(), "stale allowances: {stale:#?}");
}

#[test]
fn legacy_public_results_equal_frozen_except_allowed() {
    let mut differences = vec![];
    let summary: Value = serde_json::from_str(SUMMARY).unwrap();
    for (name, case) in summary["cases"].as_object().unwrap() {
        let saved = replay_with(
            &case["tree"],
            &case["edit"],
            &steps(case),
            "verify_report.json",
            false,
            false,
            case["existing"].as_str(),
        );
        let (exits, native) = (&saved.codes, &saved.report);
        let label = format!("summary/{name}");
        assert_exit(
            &label,
            exits,
            &codes(&case["exit_codes"]),
            results(&case["suite"]),
            results(native),
        );
        differences.extend(result_differences(&label, &case["suite"], native));
        // The step summary file the real CLI appended to, compared line by line in full.
        let text = text_differences(
            &format!("{label}/summary.md"),
            case["summary"].as_str().unwrap(),
            saved.summary.as_deref().unwrap_or_default(),
        );
        if !text.is_empty() {
            // Name the native tool rows behind a changed capability snapshot line.
            for tool in native["capability_inventory"]["tools"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|tool| tool["required"] == true || tool["name"] == "pytest")
            {
                differences.push(format!("{label}: native capability row {tool}"));
            }
        }
        differences.extend(text);
    }
    let annotations: Value = serde_json::from_str(ANNOTATIONS).unwrap();
    let mut argv = verify();
    argv[0].push("--github-summary".into());
    let saved = replay_with(
        &annotations["tree"],
        &Value::Null,
        &argv,
        "verify_report.json",
        true,
        false,
        None,
    );
    // Every workflow command the real CLI printed, in order (frozen recorded the `::` lines).
    let printed: Vec<&str> = saved
        .stdout
        .lines()
        .filter(|line| line.starts_with("::"))
        .collect();
    let frozen: Vec<&str> = annotations["annotations"]
        .as_array()
        .unwrap()
        .iter()
        .map(|line| line.as_str().unwrap())
        .collect();
    differences.extend(text_differences(
        "annotations/stdout",
        &frozen.join("\n"),
        &printed.join("\n"),
    ));
    let (exits, native) = (saved.codes, saved.report);
    assert_exit(
        "annotations",
        &exits,
        &codes(&annotations["exit_code"]),
        results(&annotations["suite"]),
        results(&native),
    );
    differences.extend(result_differences(
        "annotations",
        &annotations["suite"],
        &native,
    ));
    let inventory: Value = serde_json::from_str(INVENTORY).unwrap();
    for (case, tree) in [("cases", "tree"), ("hybrid", "hybrid_tree")] {
        for (engine, frozen) in inventory[case].as_object().unwrap() {
            if engine == "lint" {
                // Its clang tool modes need the frozen tool absence (explicit C++ test).
                continue;
            }
            let (exits, native) = replay(
                &inventory[tree],
                &Value::Null,
                &[vec![engine.clone(), "--report".into()]],
                &format!("{engine}_report.json"),
                false,
            );
            assert_eq!(exits, codes(&frozen["exit_code"]), "{case}/{engine}: exit");
            let label = format!("inventory/{case}/{engine}");
            differences.extend(result_differences(&label, &frozen["report"], &native));
        }
    }
    // Standalone heuristic `ici cycle` (no compiler runs).
    differences.extend(extras_case("cycle-standalone", None));
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| {
        matches!(
            entry.0,
            "summary" | "annotations" | "inventory" | "extras/cycle-standalone"
        ) || entry.0.starts_with("summary/")
            || (entry.0.starts_with("inventory/") && entry.0 != "inventory/hybrid/lint")
    });
    assert_text_not_stale(|entry| {
        entry.0.starts_with("summary") || entry.0.starts_with("annotations")
    });
}

/// The C++ captures replay with the g++ they were captured with and without clang-tidy/clazy on
/// PATH (frozen recorded both tools unavailable), as `legacy_verify_matrix.rs` requires.
fn cpp_environment_matches(oracle: &Value) {
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler (ICI_TEST_CXX)");
    let version = std::process::Command::new(&compiler)
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str(),
        "the legacy extras were captured with another g++"
    );
    assert_eq!(compiler, "/usr/bin/g++", "the capture ran the PATH g++");
    for tool in ["clang-tidy", "clazy-standalone"] {
        for path in ["/usr/bin", "/bin"] {
            assert!(!Path::new(path).join(tool).exists(), "{tool} in {path}");
        }
    }
}

#[test]
#[ignore = "explicit actual g++ legacy verify: ICI_TEST_CXX=/usr/bin/g++ must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone"]
fn legacy_cpp_and_hybrid_public_results_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(EXTRAS).unwrap();
    cpp_environment_matches(&oracle);
    let mut differences = vec![];
    for name in [
        "cpp-defaults",
        "cpp-deep",
        "cpp-database",
        "hybrid",
        "lint-fallback-verify",
        "lint-fallback-lint",
    ] {
        differences.extend(extras_case(name, None));
    }
    let inventory: Value = serde_json::from_str(INVENTORY).unwrap();
    let frozen = &inventory["hybrid"]["lint"];
    let (exits, native) = replay(
        &inventory["hybrid_tree"],
        &Value::Null,
        &[vec!["lint".into(), "--report".into()]],
        "lint_report.json",
        false,
    );
    assert_eq!(exits, codes(&frozen["exit_code"]), "hybrid lint exit");
    differences.extend(result_differences(
        "inventory/hybrid/lint",
        &frozen["report"],
        &native,
    ));
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| {
        entry.0.starts_with("extras/cpp")
            || entry.0 == "extras/hybrid"
            || entry.0.starts_with("extras/lint-fallback")
            || entry.0 == "inventory/hybrid/lint"
    });
}

/// Failure mode (and fix): a project interpreter without the optional coverage module keeps
/// frozen's WARN/ESTIMATED test verdict, WARN suite and exit 0, never the incomplete ERROR
/// suite and exit 3 (the measured suite stands; frozen's coverage estimate stays absent).
#[test]
#[ignore = "explicit actual pytest-capable interpreter without coverage, as captured (ICI_TEST_PYTHON_NEW)"]
fn optional_coverage_absence_keeps_the_frozen_warn_suite_and_exit() {
    let python = std::env::var("ICI_TEST_PYTHON_NEW").expect("ICI_TEST_PYTHON_NEW");
    let differences = extras_case(
        "python-optional-coverage",
        Some(("/usr/bin/python3.14", &python)),
    );
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0 == "extras/python-optional-coverage");
}

fn line_extra(suite: &Value) -> &Value {
    &results(suite)
        .iter()
        .find(|engine| engine["engine_name"] == "line")
        .expect("line engine")["extra"]
}

/// Decisions.md "`verify --baseline`의 line 집계" (the R13 allowances): the `--baseline` input is
/// not project content, so native drops exactly its frozen row. Every other row equals frozen in
/// full (all keys and values, frozen order); the totals are frozen's minus that row; the top
/// five are the remaining rows' first five, as frozen `files_data[:5]` over the same order.
#[test]
fn baseline_input_line_row_is_the_only_difference_from_frozen() {
    let summary: Value = serde_json::from_str(SUMMARY).unwrap();
    let case = &summary["cases"]["baseline"];
    let (_, native) = replay(
        &case["tree"],
        &case["edit"],
        &steps(case),
        "verify_report.json",
        false,
    );
    let (frozen, native) = (line_extra(&case["suite"]), line_extra(&native));
    let baseline = |row: &&Value| row["path"] == "base.json";
    let rows = |extra: &Value, key: &str| -> Vec<Value> { extra[key].as_array().unwrap().clone() };
    let dropped: Vec<Value> = rows(frozen, "files_data")
        .into_iter()
        .filter(|row| baseline(&row))
        .collect();
    assert_eq!(dropped.len(), 1, "frozen counted the baseline input once");
    let kept: Vec<Value> = rows(frozen, "files_data")
        .into_iter()
        .filter(|row| !baseline(&row))
        .collect();
    assert!(!kept.is_empty());
    assert_eq!(rows(native, "files_data"), kept);
    assert_eq!(rows(native, "top_files_all"), kept[..kept.len().min(5)]);
    for key in ["blank", "code", "comment", "total"] {
        assert_eq!(
            native["all"][key].as_u64(),
            Some(frozen["all"][key].as_u64().unwrap() - dropped[0][key].as_u64().unwrap()),
            "all.{key}"
        );
    }
    assert_eq!(native["all"]["files"], json!(kept.len()));
    // The source-scope counters never included the extra-scope input.
    for key in [
        "blank",
        "code",
        "comment",
        "total",
        "top_files",
        "metrics_summary",
    ] {
        assert_eq!(native[key], frozen[key], "{key}");
    }
}

/// Negative case: a project JSON file no option names is still extra-scope project content
/// under `--baseline`; only the named input is excluded.
#[test]
fn an_unnamed_project_json_file_is_still_counted_under_baseline() {
    let summary: Value = serde_json::from_str(SUMMARY).unwrap();
    let case = &summary["cases"]["baseline"];
    let mut tree = case["tree"].clone();
    tree["data.json"] = json!("{\n  \"a\": 1,\n\n  \"b\": 2\n}\n");
    let (_, native) = replay(
        &tree,
        &case["edit"],
        &steps(case),
        "verify_report.json",
        false,
    );
    let rows = line_extra(&native)["files_data"].as_array().unwrap();
    let paths: Vec<&str> = rows
        .iter()
        .map(|row| row["path"].as_str().unwrap())
        .collect();
    assert!(!paths.contains(&"base.json"), "{paths:?}");
    let data = rows
        .iter()
        .find(|row| row["path"] == "data.json")
        .expect("unnamed JSON file counted");
    assert_eq!(
        (
            &data["scope"],
            &data["lang"],
            &data["code"],
            &data["blank"],
            &data["total"]
        ),
        (
            &json!("extra"),
            &json!("JSON"),
            &json!(4),
            &json!(1),
            &json!(5)
        )
    );
}

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

/// Every difference of a SARIF document the real CLI wrote from frozen's, in full: the document
/// and run keys, the driver, each rule (by id) and each result (by its `ici/v3` fingerprint,
/// path `runs.results[<ruleId>]`) at every depth, plus the result order.
fn sarif_differences(label: &str, frozen: &Value, native: &Value) -> Vec<String> {
    let mut out = vec![];
    let without = |value: &Value, keys: &[&str]| -> Value {
        let mut value = value.clone();
        for key in keys {
            value.as_object_mut().unwrap().remove(*key);
        }
        value
    };
    walk(
        &without(frozen, &["runs"]),
        &without(native, &["runs"]),
        "",
        &mut out,
    );
    let (left, right) = (&frozen["runs"][0], &native["runs"][0]);
    assert_eq!(native["runs"].as_array().map(Vec::len), Some(1), "{label}");
    walk(
        &without(left, &["tool", "results"]),
        &without(right, &["tool", "results"]),
        "runs",
        &mut out,
    );
    walk(
        &without(&left["tool"]["driver"], &["rules"]),
        &without(&right["tool"]["driver"], &["rules"]),
        "runs.tool.driver",
        &mut out,
    );
    let keyed = |items: &Value, key: &dyn Fn(&Value) -> String| -> BTreeMap<String, Value> {
        items
            .as_array()
            .unwrap()
            .iter()
            .map(|item| (key(item), item.clone()))
            .collect()
    };
    let rule = |item: &Value| item["id"].as_str().unwrap().to_owned();
    let fingerprint = |item: &Value| item["fingerprints"]["ici/v3"].as_str().unwrap().to_owned();
    for (items, key, path) in [
        (
            ("tool", "rules"),
            &rule as &dyn Fn(&Value) -> String,
            "runs.tool.driver.rules",
        ),
        (("results", ""), &fingerprint, "runs.results"),
    ] {
        let pick = |run: &Value| -> Value {
            if items.1.is_empty() {
                run[items.0].clone()
            } else {
                run[items.0]["driver"][items.1].clone()
            }
        };
        let (frozen_items, native_items) = (keyed(&pick(left), key), keyed(&pick(right), key));
        let name = |item: &Value| {
            item["id"]
                .as_str()
                .or_else(|| item["ruleId"].as_str())
                .unwrap()
                .to_owned()
        };
        for (id, item) in &frozen_items {
            match native_items.get(id) {
                Some(other) => walk(item, other, &format!("{path}[{}]", name(item)), &mut out),
                None => out.push((format!("{path}[{}]", name(item)), Missing)),
            }
        }
        for (id, item) in &native_items {
            if !frozen_items.contains_key(id) {
                out.push((format!("{path}[{}]", name(item)), NativeOnly));
            }
        }
    }
    let order = |run: &Value| -> Vec<String> {
        run["results"]
            .as_array()
            .unwrap()
            .iter()
            .map(fingerprint)
            .collect()
    };
    if order(left) != order(right) {
        out.push(("runs.results".to_owned(), Differs));
    }
    out.into_iter()
        .filter(|(path, kind)| !allowed(label, path, *kind))
        .map(|(path, kind)| format!("{label}: {path} ({kind:?})"))
        .collect()
}

/// The SARIF projection of a C++ `verify` (legacy-sarif-oracle `end_to_end.cpp`: the
/// parity-fixtures lint-warnings project, explicit g++ and no clang tools as captured), the file
/// the real CLI wrote compared with frozen's in full.
#[test]
#[ignore = "explicit actual g++ legacy verify: ICI_TEST_CXX=/usr/bin/g++ must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone"]
fn cpp_verify_sarif_equals_frozen_except_allowed() {
    cpp_environment_matches(&serde_json::from_str(EXTRAS).unwrap());
    let oracle: Value = serde_json::from_str(SARIF).unwrap();
    let case = &oracle["end_to_end"]["cpp"];
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .unwrap()
            .join(case["copied"].as_str().unwrap()),
        &base,
    );
    fs::create_dir_all(base.join(".git")).unwrap();
    let environment = environment(home.path(), false);
    let mut exits = vec![];
    for argv in steps(case) {
        let invocation =
            registry::parse(std::iter::once("ici").chain(argv.iter().map(String::as_str))).unwrap();
        exits.push(
            commands::dispatch(&invocation, &base, &environment)
                .unwrap()
                .exit_code,
        );
    }
    assert_eq!(exits, codes(&case["exit_codes"]), "exit");
    let text = fs::read_to_string(base.join("out/report.sarif")).unwrap();
    let native: Value =
        serde_json::from_str(&text.replace(&base.display().to_string(), "<root>")).unwrap();
    let differences = sarif_differences("sarif/cpp", &case["sarif"], &native);
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0 == "sarif/cpp");
}

const CPP_VALIDATION: &str =
    include_str!("../../../tests/rust-parity/cpp/validation-full-oracle.json");

/// What one `validation-full-oracle.json` replay saved: the exit code, the stdout and the
/// report (`null` when none was written), project root as `<root>` and the compiler-only PATH
/// directory as `<compiler-bin>`.
fn cpp_validation_replay(case: &Value) -> (i32, String, Value) {
    let home = tempfile::tempdir().unwrap();
    // The capture's `mkdtemp(prefix="ici-cppval-capture-")` path length: frozen cuts compiler
    // stderr at 200 characters of absolute paths, so the cut point follows the root's length.
    let directory = tempfile::Builder::new()
        .prefix("ici-cppval-capture-")
        .rand_bytes(8)
        .tempdir_in("/tmp")
        .unwrap();
    let base = directory.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .unwrap()
            .join("parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        &base,
    );
    fs::create_dir_all(base.join(".git")).unwrap();
    // An empty config keeps the fixture's own ici.toml (the package fixtures).
    if let Some(config) = case["config"].as_str().filter(|config| !config.is_empty()) {
        fs::write(base.join("ici.toml"), config).unwrap();
    }
    for (relative, text) in case["files"].as_object().into_iter().flatten() {
        fs::write(base.join(relative), text.as_str().unwrap()).unwrap();
    }
    let bin = directory.path().join("bin");
    // The capture's PATH scripts: a recorded `scripts` map, else the cut readelf.
    let write_scripts = |bin: &Path| {
        let mut scripts: Vec<(String, String)> = case["scripts"]
            .as_object()
            .into_iter()
            .flatten()
            .map(|(name, text)| (name.clone(), text.as_str().unwrap().to_owned()))
            .collect();
        if case["path"] == "short-readelf" {
            scripts.push((
                "readelf".into(),
                "#!/bin/sh\n/usr/bin/readelf \"$@\" | head -n 4\nexit 0\n".into(),
            ));
        }
        for (name, text) in scripts {
            let script = bin.join(name);
            fs::write(&script, text).unwrap();
            let mut permissions = fs::metadata(&script).unwrap().permissions();
            std::os::unix::fs::PermissionsExt::set_mode(&mut permissions, 0o755);
            fs::set_permissions(&script, permissions).unwrap();
        }
    };
    let path = if matches!(
        case["path"].as_str(),
        Some("no-readelf" | "short-readelf" | "conflict-readelf")
    ) {
        // /usr/bin and /bin as symlinks without readelf, with the capture's scripts first.
        fs::create_dir_all(&bin).unwrap();
        write_scripts(&bin);
        for parent in ["/usr/bin", "/bin"] {
            let mut entries: Vec<_> = fs::read_dir(parent)
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect();
            entries.sort();
            for entry in entries {
                let link = bin.join(&entry);
                if entry != "readelf" && fs::symlink_metadata(&link).is_err() {
                    std::os::unix::fs::symlink(Path::new(parent).join(&entry), link).unwrap();
                }
            }
        }
        bin.display().to_string()
    } else if case["path"] == "wrapped-gxx" {
        // The capture's wrapper scripts first, every /usr/bin and /bin entry as a symlink.
        fs::create_dir_all(&bin).unwrap();
        write_scripts(&bin);
        for parent in ["/usr/bin", "/bin"] {
            let mut entries: Vec<_> = fs::read_dir(parent)
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect();
            entries.sort();
            for entry in entries {
                let link = bin.join(&entry);
                if fs::symlink_metadata(&link).is_err() {
                    std::os::unix::fs::symlink(Path::new(parent).join(&entry), link).unwrap();
                }
            }
        }
        bin.display().to_string()
    } else if case["path"] == "wrapped-gxx-compiler" {
        // The capture's wrapper scripts with the compiler-only assembler and linker.
        fs::create_dir_all(&bin).unwrap();
        write_scripts(&bin);
        for tool in ["as", "ld", "x86_64-linux-gnu-as", "x86_64-linux-gnu-ld"] {
            let program = Path::new("/usr/bin").join(tool);
            if program.exists() {
                std::os::unix::fs::symlink(program, bin.join(tool)).unwrap();
            }
        }
        bin.display().to_string()
    } else if case["path"] == "compiler" {
        fs::create_dir_all(&bin).unwrap();
        for tool in [
            "g++",
            "as",
            "ld",
            "x86_64-linux-gnu-as",
            "x86_64-linux-gnu-ld",
        ] {
            let program = Path::new("/usr/bin").join(tool);
            if program.exists() {
                std::os::unix::fs::symlink(program, bin.join(tool)).unwrap();
            }
        }
        bin.display().to_string()
    } else {
        "/usr/bin:/bin".to_owned()
    };
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), path),
        ("LC_CTYPE".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let command = case["command"].as_str().unwrap();
    let host = |text: &str| {
        text.replace(&base.display().to_string(), "<root>")
            .replace(&bin.display().to_string(), "<compiler-bin>")
    };
    // `max_coverage_regression` (capture BASELINE_EDITS): write the baseline, apply the edit,
    // then compare (`--fail-on-new` for the gate case). The written baseline and its exit are
    // returned beside the compared report.
    let baseline = command.starts_with("verify-baseline").then(|| {
        let invocation =
            registry::parse(["ici", "verify", "--write-baseline", "base.json"]).unwrap();
        let written = commands::dispatch(&invocation, &base, &environment).unwrap();
        let report: Value = fs::read_to_string(base.join("base.json"))
            .map(|text| serde_json::from_str(&host(&text)).unwrap())
            .unwrap_or(Value::Null);
        for (relative, text) in case["edit"].as_object().into_iter().flatten() {
            fs::write(base.join(relative), text.as_str().unwrap()).unwrap();
        }
        json!({"exit_code": written.exit_code, "report": report})
    });
    // Frozen `ici build` takes no `--report` and saves nothing: its stdout is the result.
    let argv: &[&str] = match command {
        "verify-baseline" => &["ici", "verify", "--report", "--baseline", "base.json"],
        "verify-baseline-gate" => &[
            "ici",
            "verify",
            "--report",
            "--baseline",
            "base.json",
            "--fail-on-new",
        ],
        "build" => &["ici", "build"],
        "verify-deep" => &["ici", "verify", "--profile", "deep", "--report"],
        _ => &["ici", command, "--report"],
    };
    let invocation = registry::parse(argv.iter().copied()).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let saved = if command == "verify-deep" || baseline.is_some() {
        "verify".to_owned()
    } else {
        command.replace('-', "_")
    };
    let report = fs::read_to_string(base.join(format!("{saved}_report.json")))
        .map(|text| serde_json::from_str(&host(&text)).unwrap())
        .unwrap_or(Value::Null);
    if command == "verify-deep" {
        // The capture keeps only the build and binary_compat engine results.
        let engines: serde_json::Map<String, Value> = report["results"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|engine| {
                let name = engine["engine_name"].as_str()?;
                ["build", "binary_compat"]
                    .contains(&name)
                    .then(|| (name.to_owned(), engine.clone()))
            })
            .collect();
        return (output.exit_code, String::new(), Value::Object(engines));
    }
    if let Some(baseline) = baseline {
        return (
            output.exit_code,
            host(&output.stdout),
            json!({"baseline": baseline, "report": report}),
        );
    }
    (output.exit_code, host(&output.stdout), report)
}

/// Frozen C++ validation commands (`capture_validation_full.py`): the saved `ici test` (and,
/// per family, `sanitize`/`thread-sanitize`/`build`) report of each case equals frozen's in
/// full, plus the exit code. Positive: passing direct, CMake and Make suites; negative:
/// failing cases and threshold breaches; failure: compile errors, a signal, a failed build and
/// missing gcov. `ICI_CPPVAL_CASES` (comma-separated case prefixes) narrows a development run
/// and then skips the stale-allowance check.
#[test]
#[ignore = "explicit actual g++ 15.2.0, gcov, cmake 4.2.3 and make, as captured (ICI_TEST_CXX=/usr/bin/g++)"]
fn legacy_cpp_validation_results_equal_frozen_except_allowed() {
    let only: Vec<String> = std::env::var("ICI_CPPVAL_CASES")
        .map(|value| value.split(',').map(str::to_owned).collect())
        .unwrap_or_default();
    let differences = cpp_validation_differences(|name| {
        !CPPVAL_SLOW.contains(&name)
            && (only.is_empty() || only.iter().any(|prefix| name.starts_with(prefix)))
    });
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    if only.is_empty() {
        let slow = |label: &str| {
            CPPVAL_SLOW
                .iter()
                .any(|name| label == format!("cppval/{name}"))
        };
        assert_not_stale(|entry| entry.0.starts_with("cppval") && !slow(entry.0));
        assert_text_not_stale(|entry| entry.0.starts_with("cppval") && !slow(entry.0));
    }
}

/// Captured cases kept out of the default replay because they wait out a frozen bound: a direct
/// C++ test sleeping past run_process's 300 s DEFAULT_TIMEOUT.
const CPPVAL_SLOW: [&str; 1] = ["test:direct-timeout"];

/// The slow captured cases (`CPPVAL_SLOW`), in full like the default replay: a direct C++ test
/// that outlives the 300 s bound is frozen `_append_cpp_tool_target` 'Execution output was
/// incomplete' ERROR (as the output-bound case `test:direct-flood`).
#[test]
#[ignore = "explicit actual g++ 15.2.0 and gcov, waits out the 300 s test bound (ICI_TEST_CXX=/usr/bin/g++)"]
fn legacy_cpp_test_timeout_equals_frozen() {
    let differences = cpp_validation_differences(|name| CPPVAL_SLOW.contains(&name));
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    let slow = |label: &str| {
        CPPVAL_SLOW
            .iter()
            .any(|name| label == format!("cppval/{name}"))
    };
    assert_not_stale(|entry| slow(entry.0));
    assert_text_not_stale(|entry| slow(entry.0));
}

fn cpp_validation_differences(selected: impl Fn(&str) -> bool) -> Vec<String> {
    let oracle: Value = serde_json::from_str(CPP_VALIDATION).unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler (ICI_TEST_CXX)");
    assert_eq!(compiler, "/usr/bin/g++", "the capture ran the PATH g++");
    for (tool, key) in [(compiler.as_str(), "gxx"), ("/usr/bin/cmake", "cmake")] {
        let version = std::process::Command::new(tool)
            .arg("--version")
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8(version.stdout).unwrap().lines().next(),
            oracle[key].as_str(),
            "captured with another {tool}"
        );
    }
    let mut differences = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if !selected(name) {
            continue;
        }
        let label = format!("cppval/{name}");
        let (exit, stdout, mut native) = cpp_validation_replay(case);
        if let Ok(directory) = std::env::var("ICI_CPPVAL_DUMP") {
            fs::write(
                Path::new(&directory).join(format!("{}.json", name.replace(':', "_"))),
                serde_json::to_string_pretty(
                    &json!({"exit": exit, "report": native, "stdout": stdout}),
                )
                .unwrap(),
            )
            .unwrap();
        }
        if !case["baseline"].is_null() {
            differences.extend(result_differences(
                &format!("{label}/baseline"),
                &case["baseline"],
                &native["baseline"],
            ));
            native = native["report"].take();
        }
        // A deep verify's suite exit covers every deep engine (their own rows); the capture
        // keeps only the build and binary_compat results.
        let Some(frozen_exit) = case["exit_code"].as_i64().map(|code| code as i32) else {
            differences.extend(result_differences(&label, &case["report"], &native));
            continue;
        };
        // decisions.md "legacy 종료 코드 3의 범위": native 3 where frozen exited 1 only when the
        // required engine's evidence is NOT_RUN (ERROR) in both reports.
        let not_run = |report: &Value| {
            report["required"] == true
                && report["status"] == "ERROR"
                && report["evidence"] == "NOT_RUN"
        };
        if exit != frozen_exit
            && !((exit, frozen_exit) == (3, 1) && not_run(&native) && not_run(&case["report"]))
        {
            differences.push(format!("{label}: exit {exit} vs frozen {frozen_exit}"));
        }
        if case["report"].is_null() {
            differences.extend(text_differences(
                &format!("{label}/stdout"),
                case["stdout"].as_str().unwrap(),
                &stdout,
            ));
        } else {
            differences.extend(result_differences(&label, &case["report"], &native));
        }
    }
    differences
}

/// Frozen `TestEngine._threshold_breaches` for Python coverage (`capture_python_thresholds.py`):
/// the saved `ici test --report` of each case equals frozen's in full. Positive (`breach`): the
/// one breached branch minimum is a `Threshold: Branch coverage` WARN target and the met TEM
/// minimum adds none; negative (`met`): no threshold target; failure (`failure`): a failing test
/// is never judged against the thresholds; process failure (`coverage-failure`): the `coverage
/// pytest` process fails (a missing coverage plugin) and its tool evidence, returncode 1 with
/// the empty `error` of an exited process, equals frozen's.
#[test]
#[ignore = "explicit actual pytest and coverage interpreter, as captured (ICI_TEST_PYTHON)"]
fn python_coverage_threshold_breaches_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(THRESHOLDS).unwrap();
    let mut differences = vec![];
    for (name, expected) in [
        ("breach", 1),
        ("met", 0),
        ("failure", 0),
        ("coverage-failure", 0),
    ] {
        let case = &oracle["cases"][name];
        let (exits, native) = replay(
            &case["tree"],
            &Value::Null,
            &[vec!["test".into(), "--report".into()]],
            "test_report.json",
            false,
        );
        // decisions.md "legacy 종료 코드 3의 범위": the required test engine's evidence stays
        // NOT_RUN, so native ends with 3 where frozen ended with 1.
        let frozen_exit = codes(&case["exit_code"]);
        let expected_exit = if name == "coverage-failure" {
            vec![3]
        } else {
            frozen_exit.clone()
        };
        assert_eq!(
            exits, expected_exit,
            "{name}: exit (frozen {frozen_exit:?})"
        );
        let evidence = |report: &Value| -> Vec<(Value, Value, Value)> {
            report["tool_evidence"]
                .as_array()
                .unwrap()
                .iter()
                .map(|record| {
                    (
                        record["name"].clone(),
                        record["returncode"].clone(),
                        record["error"].clone(),
                    )
                })
                .collect()
        };
        assert_eq!(
            evidence(&native),
            evidence(&case["report"]),
            "{name}: tool evidence"
        );
        let thresholds: Vec<&Value> = native["targets"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|target| {
                target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("Threshold: "))
            })
            .collect();
        assert_eq!(thresholds.len(), expected, "{name}: {thresholds:?}");
        differences.extend(result_differences(
            &format!("thresholds/{name}"),
            &case["report"],
            &native,
        ));
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0.starts_with("thresholds"));
}

/// Frozen `DupEngine.run` verdict of a hybrid project (`capture_hybrid_dup.py`): one scan over
/// both languages compared with `warn_pct`/`fail_pct`. Positive (`under`, `fail-alone`): the
/// Python file alone crosses a threshold the merged rate does not, so the engine and its clone
/// targets are PASS; negative (`merged`): the merged rate crosses `warn_pct`, WARN; `both`: clone
/// groups in both languages are numbered and listed in frozen's one-scan order. Each saved `ici
/// dup --report` equals frozen's in full.
#[test]
fn hybrid_dup_verdicts_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(HYBRID_DUP).unwrap();
    let mut differences = vec![];
    for (name, status) in [
        ("under", "PASS"),
        ("fail-alone", "PASS"),
        ("merged", "WARN"),
        ("both", "WARN"),
    ] {
        let case = &oracle["cases"][name];
        let (exits, native) = replay(
            &case["tree"],
            &Value::Null,
            &[vec!["dup".into(), "--report".into()]],
            "dup_report.json",
            false,
        );
        assert_eq!(exits, codes(&case["exit_code"]), "{name}: exit");
        assert_eq!(native["status"], status, "{name}: verdict");
        differences.extend(result_differences(
            &format!("hybrid-dup/{name}"),
            &case["report"],
            &native,
        ));
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0.starts_with("hybrid-dup"));
}

/// Failure mode of the text and SARIF projections: a changed, missing or added line is reported
/// on its side unless an allowance names that line (durations are masked first); a SARIF result
/// only one side has, or a different result order, is a difference.
/// One `heuristic-metrics-oracle.json` tree as `capture_heuristic_metrics.py` wrote it: a
/// committed fixture directory or a generated tree, without any `ici.toml`.
fn heuristic_tree(name: &str) -> Value {
    fn read(base: &Path, directory: &Path, tree: &mut serde_json::Map<String, Value>) {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                read(base, &path, tree);
            } else {
                let relative = path.strip_prefix(base).unwrap().display().to_string();
                // `write_tree` writes text; the one non-UTF-8 fixture is copied separately.
                tree.insert(
                    relative,
                    json!(String::from_utf8_lossy(&fs::read(&path).unwrap())),
                );
            }
        }
    }
    let ok = "int ok(int x) {\n  if (x) { return 1; }\n  return 0;\n}\n";
    let mut tree = serde_json::Map::new();
    match name {
        "lambda-limit" => {
            tree.insert(
                "src/lambda.cpp".into(),
                json!(format!("void f() {{\n{}\n}}\n", "[]{};".repeat(100_001))),
            );
        }
        "too-large" => {
            tree.insert(
                "src/big.cpp".into(),
                json!(format!("// {}\n", "x".repeat(8 * 1024 * 1024))),
            );
        }
        "nul" => {
            tree.insert(
                "src/nul.cpp".into(),
                json!(
                    "int nul(int a) {\n  char c = '\0';\n  if (a) { return c; }\n  return 0;\n}\n"
                ),
            );
        }
        _ => {
            let fixture = match name {
                "zoo" | "errors" => format!("heuristic-metrics/{name}"),
                _ => format!("metric-sources/{name}"),
            };
            let base = Path::new(env!("CARGO_MANIFEST_DIR"))
                .ancestors()
                .nth(2)
                .unwrap()
                .join("parity-fixtures/cpp")
                .join(fixture);
            read(&base, &base, &mut tree);
            tree.remove("ici.toml");
            return Value::Object(tree);
        }
    }
    tree.insert("src/ok.cpp".into(), json!(ok));
    Value::Object(tree)
}

/// Frozen legacy `ici complexity` / `ici cognitive` over the brace-heuristic function inventory
/// (`tests/rust-parity/cpp/heuristic-metrics-oracle.json`, `capture_heuristic_metrics.py`):
/// every saved engine report in full through the real CLI, with no compilation database (no
/// tool runs, PATH=/usr/bin:/bin). Covers operators, constructors, lambdas, templates,
/// preprocessor conditionals, macro-generated functions and both thresholds (`zoo`), bodies the
/// cognitive parser rejects (`errors`), an unterminated body, a non-UTF-8 source, an 8 MiB
/// source, a NUL inside a body and the bounded lambda-body limit.
#[test]
fn legacy_cpp_heuristic_metrics_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(HEURISTIC).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut differences = vec![];
    for (key, case) in oracle["cases"].as_object().unwrap() {
        let (command, name) = key.split_once(':').unwrap();
        if command == "analyze" {
            continue;
        }
        let mut tree = heuristic_tree(name);
        tree["ici.toml"] = oracle["config"].clone();
        if name == "unreadable" {
            // The frozen non-UTF-8 source (`metric-sources/unreadable/src/latin.cpp`).
            tree.as_object_mut().unwrap().remove("src/latin.cpp");
        }
        let report = format!("{command}_report.json");
        let argv = [command.to_owned(), "--report".to_owned()];
        let home = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().join("project");
        write_tree(&base, &tree);
        if name == "unreadable" {
            fs::copy(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .ancestors()
                    .nth(2)
                    .unwrap()
                    .join("parity-fixtures/cpp/metric-sources/unreadable/src/latin.cpp"),
                base.join("src/latin.cpp"),
            )
            .unwrap();
        }
        fs::create_dir_all(base.join(".git")).unwrap();
        let invocation =
            registry::parse(std::iter::once("ici").chain(argv.iter().map(String::as_str))).unwrap();
        let output =
            commands::dispatch(&invocation, &base, &environment(home.path(), false)).unwrap();
        let text = fs::read_to_string(base.join(&report)).unwrap();
        let native: Value =
            serde_json::from_str(&text.replace(&base.display().to_string(), "<root>")).unwrap();
        let label = format!("heuristic/{name}/{command}");
        assert_exit(
            &label,
            &[output.exit_code],
            &codes(&case["exit_code"]),
            std::slice::from_ref(&case["report"]),
            std::slice::from_ref(&native),
        );
        differences.extend(result_differences(&label, &case["report"], &native));
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0.starts_with("heuristic"));
}

#[test]
fn an_unallowed_projection_difference_is_reported() {
    let differences = text_differences(
        "probe/summary.md",
        "# Title\n| a | 1.23s |\nold row\n",
        "# Title\n| a | 0.04s |\nnew row\nextra\n",
    );
    assert_eq!(
        differences,
        [
            "probe/summary.md: Missing line \"old row\"",
            "probe/summary.md: NativeOnly line \"new row\"",
            "probe/summary.md: NativeOnly line \"extra\"",
        ]
    );
    assert!(line_matches(
        "| <code>gcc</code> | <code>ready</code> | x",
        "| <code>*</code> | <code>ready</code> | "
    ));
    assert!(!line_matches(
        "| <code>HIGH</code> | <code>exception</code> | x",
        "| <code>*</code> | <code>ready</code> | "
    ));
    // A full-line allowance does not accept a native header that adds a (fabricated) TEM.
    let header = "## 🛑 `ici` Verification Report — **`ERROR`**$";
    assert!(line_matches(
        "## 🛑 `ici` Verification Report — **`ERROR`**",
        header
    ));
    assert!(!line_matches(
        "## 🛑 `ici` Verification Report — **`ERROR`** (TEM: **`4.75 / 5.0`**)",
        header
    ));
    let result = |fingerprint: &str, rule: &str| json!({"ruleId": rule, "fingerprints": {"ici/v3": fingerprint}, "level": "note"});
    let document = |results: Vec<Value>| {
        json!({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "ici", "rules": [
            {"id": "r", "properties": {"confidence": "high"}}]}}, "results": results}]})
    };
    let frozen = document(vec![result("a", "r"), result("b", "r")]);
    let mut native = document(vec![result("b", "r"), result("c", "r")]);
    native["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["confidence"] = json!("low");
    let mut differences = sarif_differences("probe/sarif", &frozen, &native);
    differences.sort();
    assert_eq!(
        differences,
        [
            "probe/sarif: runs.results (Value)",
            "probe/sarif: runs.results[r] (Missing)",
            "probe/sarif: runs.results[r] (NativeOnly)",
            "probe/sarif: runs.tool.driver.rules[r].properties.confidence (Value)",
        ]
    );
}

/// Failure mode: every unallowed difference is reported with its normalized path and kind; an
/// absent key, `null`, `""`, `[]` and `{}` are all distinct; an allowance names one path only.
#[test]
fn an_unallowed_difference_is_reported() {
    let frozen = serde_json::json!({"engine_name": "type", "status": "PASS", "score": null,
        "summary": "", "targets": [], "duration": 1.0,
        "extra": {"type_issues": 0, "mypy_profile": "project", "rows": [1, 2], "map": {}}});
    let native = serde_json::json!({"engine_name": "type", "status": "WARN", "score": 0,
        "targets": {}, "duration": 2.0,
        "extra": {"type_issues": 0.0, "rows": [1], "map": null, "native_checks": [],
            "invented": true}});
    let mut differences = result_differences("probe/case", &frozen, &native);
    differences.sort();
    assert_eq!(
        differences,
        [
            "probe/case: .duration (Value)",
            "probe/case: .extra.invented (NativeOnly)",
            "probe/case: .extra.map (Type)",
            "probe/case: .extra.mypy_profile (Missing)",
            "probe/case: .extra.native_checks (NativeOnly)",
            "probe/case: .extra.rows (Len)",
            "probe/case: .extra.type_issues (Type)",
            "probe/case: .score (Type)",
            "probe/case: .status (Value)",
            "probe/case: .summary (Missing)",
            "probe/case: .targets (Type)",
        ]
    );
}
