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
const INPUTS_CHANGED: &str =
    "inputs changed while running; the saved findings are partial evidence";
const PY: &str = "project.python.test: ";
/// decisions.md "legacy 종료 코드 3의 범위": each case with frozen's exit 1 and native's 3, and
/// the exact reasons the native gate is INCOMPLETE. The cause decides, per case: a required
/// check's own failure, or input stability (`covjson-gone`, the fixture removes the interpreter
/// mid-run; decisions.md "분석 중 소스가 바뀐 legacy `ici dead` 링커 범위").
const LADDER_EXIT_THREE: [(&str, &[&str]); 18] = [
    (
        "covjson-exit7-required",
        &["project.python.coverage: Coverage JSON generation failed with exit code 7"],
    ),
    ("cov-over-stderr", &["Coverage test output was truncated"]),
    ("cov-over-stdout", &["Coverage test output was truncated"]),
    (
        "cov-signal",
        &["Coverage test process terminated before reporting results"],
    ),
    ("covjson-gone", &[]),
    (
        "cpp-test-overflow",
        &["project.cpp.test: plain C++ executable did not complete; no test count was inferred"],
    ),
    ("pytest-gone", &["Pytest executable was unavailable"]),
    ("pytest-over-stderr", &["Pytest output was truncated"]),
    ("pytest-over-stdout", &["Pytest output was truncated"]),
    (
        "pytest-signal",
        &["Pytest process terminated before reporting results"],
    ),
    (
        "sanitize-marker-only",
        &["project.cpp.sanitize: Sanitizer output had a report marker but no complete diagnostic"],
    ),
    (
        "test-gxx-signal",
        &["project.cpp.test: C++ test compile did not finish: tests/test_calc.cpp"],
    ),
    (
        "test-gxx-trunc",
        &["project.cpp.test: C++ test compile did not finish: tests/test_calc.cpp"],
    ),
    ("unittest-exit3", &["Unittest failed with exit code 3"]),
    ("unittest-gone", &["Unittest executable was unavailable"]),
    ("unittest-over-stderr", &["Unittest output was truncated"]),
    ("unittest-over-stdout", &["Unittest output was truncated"]),
    ("unittest-signal", &["Unittest failed with exit code -9"]),
];

/// The gate reasons `LADDER_EXIT_THREE` expects for a case, in the gate's order.
fn assert_ladder_gate(name: &str, own: &[&str], gate: &Value) {
    assert_eq!(gate["selected"], "INCOMPLETE", "{name}: {gate}");
    let mut expected: Vec<String> = own
        .iter()
        .map(|reason| {
            if reason.starts_with("project.") {
                (*reason).to_owned()
            } else {
                format!("{PY}{reason}")
            }
        })
        .collect();
    if name.ends_with("-gone") {
        expected.push(INPUTS_CHANGED.to_owned());
    }
    assert_eq!(gate["reasons"], json!(expected), "{name}");
}

/// A failed `coverage json` step beside a complete test run: the optional coverage check does
/// not make the native gate incomplete, so the exit is the legacy class 1 (frozen measured 1).
fn assert_optional_coverage_gate(name: &str, native: &Value) {
    assert_eq!(
        native["gate"]["selected"], "PASS",
        "{name}: {}",
        native["gate"]
    );
    let check = |id: &str| {
        native["checks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|check| check["check_id"] == id)
            .unwrap()
            .clone()
    };
    assert_eq!(check("python.test")["state"], "SUCCEEDED", "{name}");
    assert_eq!(check("python.test")["evidence_complete"], true, "{name}");
    assert_eq!(check("python.coverage")["state"], "FAILED", "{name}");
}

const FAILURE_LADDER: &str =
    include_str!("../../../tests/rust-parity/validation/failure-ladder-oracle.json");
const HYBRID_TEST: &str =
    include_str!("../../../tests/rust-parity/reporting/hybrid-test-oracle.json");
const PYTHON_SANITIZE: &str =
    include_str!("../../../tests/rust-parity/validation/python-sanitize-full-oracle.json");
const THRESHOLDS: &str =
    include_str!("../../../tests/rust-parity/reporting/python-threshold-oracle.json");
const HYBRID_DUP: &str =
    include_str!("../../../tests/rust-parity/reporting/hybrid-dup-oracle.json");
const HEURISTIC: &str =
    include_str!("../../../tests/rust-parity/cpp/heuristic-metrics-oracle.json");
const DUP_AUDIT: &str = include_str!("../../../tests/rust-parity/cpp/dup-audit-oracle.json");
const NO_SELECTED_SOURCE: &str =
    include_str!("../../../tests/rust-parity/cpp/no-selected-source-oracle.json");
const CPP_DECLARED_PYTHON: &str =
    include_str!("../../../tests/rust-parity/cpp/cpp-declared-python-oracle.json");
const UNUSED_PROBE: &str =
    include_str!("../../../tests/rust-parity/cpp/unused-probe-branches-oracle.json");

include!("support/legacy_result_harness.rs");

include!("support/legacy_result_allowances.rs");

/// Where the Python captures' project interpreter lived on the capture host.
const CAPTURE_PYTHON: &str = "/home/jihoon/projects/ici/.venv/bin/python";
/// The file the capture interpreter resolved to on the capture host. A legacy python-compat run
/// executes and records the resolved interpreter, like frozen (decisions.md "legacy python-compat
/// 인터프리터 경로"), so the resolved location is host data too.
const CAPTURE_PYTHON_RESOLVED: &str =
    "/home/jihoon/.local/share/uv/python/cpython-3.10.21-linux-x86_64-gnu/bin/python3.10";

/// `text` with this host's project interpreter written as the capture host's: its resolved file
/// first, then the launched path.
fn as_captured(text: &str, python: &str) -> String {
    let resolved = Path::new(python)
        .canonicalize()
        .map(|path| path.display().to_string())
        .unwrap_or_else(|_| python.to_owned());
    let text = if resolved == python {
        text.to_owned()
    } else {
        text.replace(&resolved, CAPTURE_PYTHON_RESOLVED)
    };
    text.replace(python, CAPTURE_PYTHON)
}

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

/// Writes a tree's `.wrap/` entries (`capture_failure_ladder.py`) beside the project and returns
/// the rest with the wrapper directory substituted, and whether the tree used wrappers at all.
fn place_wrappers(tree: &Value, wrappers: &Path) -> (Value, bool) {
    use std::os::unix::fs::PermissionsExt;
    let Some(entries) = tree.as_object() else {
        return (tree.clone(), false);
    };
    let used = entries.iter().any(|(name, text)| {
        name.starts_with(".wrap/")
            || text
                .as_str()
                .is_some_and(|text| text.contains("CAPTURE_WRAP"))
    });
    if !used {
        return (tree.clone(), false);
    }
    fs::create_dir_all(wrappers).unwrap();
    let directory = wrappers.display().to_string();
    let mut placed = serde_json::Map::new();
    for (name, text) in entries {
        let Some(body) = text.as_str() else {
            placed.insert(name.clone(), text.clone());
            continue;
        };
        let body = body
            .replace("CAPTURE_PYTHON", &project_python())
            .replace("CAPTURE_WRAP", &directory);
        if let Some(script) = name.strip_prefix(".wrap/") {
            let path = wrappers.join(script);
            fs::write(&path, body).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        } else {
            placed.insert(name.clone(), Value::String(body));
        }
    }
    (Value::Object(placed), true)
}

fn write_tree(base: &Path, tree: &Value) {
    for (name, text) in tree.as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        // `capture_dup_audit.py` trees: raw bytes, a symbolic link, or a repeated text.
        if let Some(raw) = text["base64"].as_str() {
            fs::write(path, base64_decode(raw)).unwrap();
        } else if let Some(link) = text["symlink"].as_str() {
            std::os::unix::fs::symlink(link, path).unwrap();
        } else if let Some(repeat) = text["repeat"].as_array() {
            let times = repeat[1].as_u64().unwrap() as usize;
            fs::write(path, repeat[0].as_str().unwrap().repeat(times)).unwrap();
        } else {
            fs::write(path, text.as_str().unwrap()).unwrap();
        }
    }
}

fn base64_decode(text: &str) -> Vec<u8> {
    let value = |byte: u8| -> u32 {
        match byte {
            b'A'..=b'Z' => u32::from(byte - b'A'),
            b'a'..=b'z' => u32::from(byte - b'a') + 26,
            b'0'..=b'9' => u32::from(byte - b'0') + 52,
            b'+' => 62,
            _ => 63,
        }
    };
    let mut out = vec![];
    for chunk in text.as_bytes().chunks(4) {
        let padding = chunk.iter().filter(|byte| **byte == b'=').count();
        let word = chunk.iter().fold(0u32, |word, byte| {
            (word << 6) | if *byte == b'=' { 0 } else { value(*byte) }
        });
        out.extend_from_slice(&word.to_be_bytes()[1..4 - padding]);
    }
    out
}

fn environment(home: &Path, actions: bool) -> BTreeMap<String, String> {
    let mut environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), home.display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        // Frozen's interpreter coerced the capture's C locale (PEP 538) and exported this to the
        // tools it ran, so a compiler diagnostic in a report has UTF-8 quotes.
        ("LC_CTYPE".to_owned(), "C.UTF-8".to_owned()),
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
    /// Whether a fixture's wrapper or test reached its end (`.wrap/completed`).
    completed: bool,
    report: Value,
    /// The native run's saved result (gate and checks), when the run wrote one.
    native: Value,
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
    replay_with_env(tree, edit, steps, report, actions, wrapper, existing, &[])
}

/// [`replay_with`] with extra environment entries for the run (test-only settings).
#[allow(clippy::too_many_arguments)]
fn replay_with_env(
    tree: &Value,
    edit: &Value,
    steps: &[Vec<String>],
    report: &str,
    actions: bool,
    wrapper: bool,
    existing: Option<&str>,
    extra_environment: &[(&str, &str)],
) -> Saved {
    let home = tempfile::tempdir().unwrap();
    // The capture's `TemporaryDirectory()` path length: frozen cuts compiler stderr at 200
    // characters of absolute paths, so the cut point follows the root's length.
    let directory = tempfile::Builder::new()
        .prefix("tmp")
        .rand_bytes(8)
        .tempdir_in("/tmp")
        .unwrap();
    let base = directory.path().join("project");
    let wrappers = directory.path().join("wrap");
    let (placed, wrappers_used) = place_wrappers(tree, &wrappers);
    write_tree(&base, &placed);
    fs::create_dir_all(base.join(".git")).unwrap();
    let mut environment = environment(home.path(), actions);
    environment.extend(
        extra_environment
            .iter()
            .map(|(key, value)| ((*key).to_owned(), (*value).to_owned())),
    );
    // `capture_failure_ladder.py` fixtures: `.wrap/` scripts come first on PATH, or alone.
    if wrappers_used {
        let directory = wrappers.display().to_string();
        environment.insert(
            "PATH".into(),
            if wrappers.join(".path-only").exists() {
                directory
            } else {
                format!("{directory}:/usr/bin:/bin")
            },
        );
    }
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
            .replace(&wrappers.display().to_string(), "<wrap>");
        let text = as_captured(&text, &python);
        match &version {
            Some(version) => text.replace(version, captured["python"].as_str().unwrap()),
            None => text,
        }
    };
    let text = fs::read_to_string(base.join(report)).unwrap();
    Saved {
        completed: wrappers.join("completed").exists(),
        codes,
        report: serde_json::from_str(&host(&text)).unwrap(),
        native: fs::read_to_string(base.join(".ici/rust-v1/result.json"))
            .ok()
            .map_or(Value::Null, |text| serde_json::from_str(&text).unwrap()),
        stdout: host(&stdout),
        summary: fs::read_to_string(&summary).ok().map(|text| host(&text)),
    }
}

/// One `sanitize --report` in a project whose root has `length` bytes: frozen's public snippet is
/// cut at 300 characters of output naming the root, so the two roots have one length.
fn replay_sanitize_at_length(tree: &Value, length: usize, caller: &Value) -> (i32, Value) {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let parent = directory.path().canonicalize().unwrap();
    let padding = length
        .checked_sub(parent.display().to_string().len() + 1)
        .filter(|padding| *padding > 0)
        .expect("TMPDIR is too long for the captured root length");
    let base = parent.join("p".repeat(padding));
    write_tree(&base, tree);
    fs::create_dir_all(base.join(".git")).unwrap();
    let mut environment = environment(home.path(), false);
    for (key, value) in caller.as_object().into_iter().flatten() {
        environment.insert(key.clone(), value.as_str().unwrap().to_owned());
    }
    let invocation = registry::parse(["ici", "sanitize", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let captured: Value = serde_json::from_str(SUMMARY).unwrap();
    let text = fs::read_to_string(base.join("sanitize_report.json"))
        .unwrap()
        .replace(&base.display().to_string(), "<root>");
    let mut text = as_captured(&text, &project_python());
    if let Some(version) = host_python_version() {
        text = text.replace(&version, captured["python"].as_str().unwrap());
    }
    (output.exit_code, serde_json::from_str(&text).unwrap())
}

fn verify() -> Vec<Vec<String>> {
    vec![vec!["verify".into(), "--report".into()]]
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

/// A project where no source is selected (no `ici.toml`, nothing under a source directory): the
/// whole saved report equals frozen, including what each engine reports for its empty scope
/// (`capture_no_selected_source.py`). Positive: Python and C++ at the root, shallow and deep.
/// Negative: nothing to analyze at all. Failure: the `test` engine's verdict on no suite.
/// Host-aware: a policy tool row's host data (installed or not, path, version, probe state and
/// details) is the running host's, while names, required/optional policy and every other field
/// stay exact, and the native counts must agree with the native rows. The cases replay on the
/// host's PATH and again with stub clang tools first (another host's tool set). The exact
/// comparison with the capture host's tools is `..._with_the_capture_tool_set`.
#[test]
fn projects_without_a_selected_source_report_like_frozen() {
    let stubs = tempfile::tempdir().unwrap();
    for name in ["clang", "clang++", "clang-tidy", "clazy-standalone"] {
        let path = stubs.path().join(name);
        fs::write(
            &path,
            "#!/bin/sh\necho 'Ubuntu clang version 18.1.3 (1ubuntu1)'\necho 'Target: x86_64-pc-linux-gnu'\n",
        )
        .unwrap();
        let mut permissions = fs::metadata(&path).unwrap().permissions();
        std::os::unix::fs::PermissionsExt::set_mode(&mut permissions, 0o755);
        fs::set_permissions(&path, permissions).unwrap();
    }
    let other_host = format!("{}:/usr/bin:/bin", stubs.path().display());
    for path in ["/usr/bin:/bin", other_host.as_str()] {
        let differences =
            saved_report_cases("no-selected-source", NO_SELECTED_SOURCE, None, path, true);
        assert!(
            differences.is_empty(),
            "PATH={path}\n{}",
            differences.join("\n")
        );
    }
}

/// The exact comparison, inventory host data included, on the capture host's tool set.
#[test]
#[ignore = "host tool set as captured: /usr/bin has g++/gcc (Ubuntu 15.2.0) and cmake 4.2.3 and lacks clang, clang++, clang-tidy and clazy-standalone"]
fn projects_without_a_selected_source_report_like_frozen_with_the_capture_tool_set() {
    let differences = saved_report_cases(
        "no-selected-source",
        NO_SELECTED_SOURCE,
        None,
        "/usr/bin:/bin",
        false,
    );
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0 == "no-selected-source");
}

/// The fields of a capability row that describe the probing host rather than the policy.
const HOST_FIELDS: [&str; 7] = [
    "available",
    "complete",
    "path",
    "version",
    "state",
    "error",
    "details",
];

/// Frozen's report with each policy tool row's host data, and the availability counts, taken from
/// the native report after checking that the native counts agree with the native rows.
fn host_aware(label: &str, frozen: &Value, native: &Value, out: &mut Vec<String>) -> Value {
    let rows = native["capability_inventory"]["tools"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let counts = &native["capability_inventory"]["counts"];
    let count = |test: &dyn Fn(&Value) -> bool| rows.iter().filter(|row| test(row)).count();
    for (key, actual) in [
        ("total", rows.len()),
        (
            "ready",
            count(&|row| row["available"] == true && row["complete"] == true),
        ),
        ("unavailable", count(&|row| row["available"] != true)),
        (
            "incomplete",
            count(&|row| row["available"] == true && row["complete"] != true),
        ),
    ] {
        if counts[key] != json!(actual) {
            out.push(format!(
                "{label}: native counts.{key} {} vs its rows {actual}",
                counts[key]
            ));
        }
    }
    let mut frozen = frozen.clone();
    // The availability counts follow the host's rows (checked above); the total is the policy's.
    for key in ["ready", "unavailable", "incomplete"] {
        frozen["capability_inventory"]["counts"][key] = counts[key].clone();
    }
    for row in frozen["capability_inventory"]["tools"]
        .as_array_mut()
        .into_iter()
        .flatten()
    {
        if row["required"] != true && row["optional"] != true {
            continue;
        }
        let Some(own) = rows.iter().find(|other| other["name"] == row["name"]) else {
            continue;
        };
        for field in HOST_FIELDS {
            match own.get(field) {
                Some(value) => row[field] = value.clone(),
                None => {
                    if let Some(object) = row.as_object_mut() {
                        object.remove(field);
                    }
                }
            }
        }
    }
    frozen
}

/// Runs each captured `verify` case through the real CLI on its recorded tree and compares the
/// exit code and the whole saved report (the project root recorded as `<root>`).
fn saved_report_cases(
    label: &str,
    oracle: &str,
    python: Option<&str>,
    path: &str,
    host: bool,
) -> Vec<String> {
    let oracle: Value = serde_json::from_str(oracle).unwrap();
    let mut differences = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let home = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap().join("project");
        fs::create_dir_all(base.join(".git")).unwrap();
        for (path, text) in case["tree"].as_object().unwrap() {
            let target = base.join(path);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(target, text.as_str().unwrap()).unwrap();
        }
        let mut environment = BTreeMap::from([
            ("HOME".to_owned(), home.path().display().to_string()),
            (
                "XDG_CONFIG_HOME".to_owned(),
                home.path().display().to_string(),
            ),
            ("PATH".to_owned(), path.to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
            (
                "GIT_CEILING_DIRECTORIES".to_owned(),
                directory
                    .path()
                    .canonicalize()
                    .unwrap()
                    .display()
                    .to_string(),
            ),
        ]);
        if let Some(python) = python {
            environment.insert("ICI_PYTHON".to_owned(), python.to_owned());
        }
        let argv: Vec<&str> = case["argv"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap())
            .collect();
        let invocation = registry::parse(std::iter::once("ici").chain(argv)).unwrap();
        let output = commands::dispatch(&invocation, &base, &environment).unwrap();
        let text = fs::read_to_string(base.join("verify_report.json"))
            .unwrap()
            .replace(&base.display().to_string(), "<root>");
        let native: Value = serde_json::from_str(&text).unwrap();
        // Equal, or the accepted incomplete exit (decisions.md "legacy 종료 코드 3의 범위").
        let frozen_exit = i32::try_from(case["exit_code"].as_i64().unwrap()).unwrap();
        assert_exit(
            &format!("{label}/{name}"),
            &[output.exit_code],
            &[frozen_exit],
            results(&case["report"]),
            results(&native),
        );
        let frozen = if host {
            host_aware(
                &format!("{label}/{name}"),
                &case["report"],
                &native,
                &mut differences,
            )
        } else {
            case["report"].clone()
        };
        differences.extend(result_differences(
            &format!("{label}/{name}"),
            &frozen,
            &native,
        ));
    }
    differences
}

/// A legacy `type = "cpp"` project keeps the Python files under its source directories in the
/// Python engines, like frozen `project_python_sources` (`capture_cpp_declared_python.py`).
/// Positive: a clean Python file beside the C++ source, standard and deep. Failure: Python
/// security and type findings in the same C++ project, reported like frozen. Frozen ran under
/// the capture interpreter, which `ICI_PYTHON` names to the native legacy launcher rule.
#[test]
#[ignore = "needs the capture interpreter with mypy, pytest and coverage (ICI_TEST_PYTHON) and the capture host's C/C++ tools (/usr/bin without clang, clang++, clang-tidy, clazy-standalone)"]
fn a_cpp_project_reports_its_python_sources_like_frozen() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let differences = saved_report_cases(
        "cpp-declared-python",
        CPP_DECLARED_PYTHON,
        Some(&python),
        "/usr/bin:/bin",
        false,
    );
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0 == "cpp-declared-python");
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
/// directory as `<compiler-bin>`. `binary` runs the built `ici` executable instead of the
/// in-process dispatch: a qmake capture re-enters the running executable as its recorder.
fn cpp_validation_replay(case: &Value, binary: bool) -> (i32, String, String, Value) {
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
    // The captures' VCS bound: a `.hg` marker at the case directory, which is also the
    // GIT_CEILING_DIRECTORIES bound (an empty `.git` is not a repository).
    fs::create_dir_all(directory.path().join(".hg")).unwrap();
    // An empty config keeps the fixture's own ici.toml (the package fixtures).
    if let Some(config) = case["config"].as_str().filter(|config| !config.is_empty()) {
        fs::write(base.join("ici.toml"), config).unwrap();
    }
    for (relative, text) in case["files"].as_object().into_iter().flatten() {
        fs::create_dir_all(base.join(relative).parent().unwrap()).unwrap();
        // `{"symlink": target}` is a relative symbolic link to another project file.
        if let Some(target) = text["symlink"].as_str() {
            std::os::unix::fs::symlink(target, base.join(relative)).unwrap();
            continue;
        }
        // `{"latin1": ...}` spells raw bytes (a non-UTF-8 source) as Latin-1 text.
        let bytes: Vec<u8> = match text["latin1"].as_str() {
            Some(latin1) => latin1.chars().map(|character| character as u8).collect(),
            None => text.as_str().unwrap().as_bytes().to_vec(),
        };
        fs::write(base.join(relative), bytes).unwrap();
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
        Some(
            "no-readelf"
                | "short-readelf"
                | "conflict-readelf"
                | "readelf-exit1"
                | "readelf-flood"
                | "no-cmake"
        )
    ) {
        // /usr/bin and /bin as symlinks without readelf (or cmake), with the capture's scripts
        // first.
        let missing = if case["path"] == "no-cmake" {
            "cmake"
        } else {
            "readelf"
        };
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
                if entry != missing && fs::symlink_metadata(&link).is_err() {
                    std::os::unix::fs::symlink(Path::new(parent).join(&entry), link).unwrap();
                }
            }
        }
        bin.display().to_string()
    } else if case["path"]
        .as_str()
        .is_some_and(|kind| kind.starts_with("wrapped-") && kind != "wrapped-gxx-compiler")
    {
        // The capture's wrapper scripts first, every /usr/bin and /bin entry as a symlink.
        fs::create_dir_all(&bin).unwrap();
        write_scripts(&bin);
        if case["path"] == "wrapped-ninja" {
            // The real Ninja the capture linked (ICI_CAPTURE_NINJA).
            let ninja = std::env::var("ICI_TEST_NINJA").expect("a real ninja (ICI_TEST_NINJA)");
            std::os::unix::fs::symlink(ninja, bin.join("ninja")).unwrap();
        }
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
    let mut environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), path),
        ("LC_CTYPE".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            directory.path().display().to_string(),
        ),
    ]);
    // A case's own environment (the capture's `ENVIRONMENTS`, e.g. CMAKE_GENERATOR).
    for (key, value) in case["environment"].as_object().into_iter().flatten() {
        environment.insert(key.clone(), value.as_str().unwrap().to_owned());
    }
    // The Python-project captures (`capture_python_cpp_metrics.py`) pinned the project
    // interpreter with ICI_PYTHON; its location and patch release are host data.
    let python = (case["fixture"] == "python-with-cpp").then(project_python);
    if let Some(python) = &python {
        environment.insert("ICI_PYTHON".into(), python.clone());
        environment.insert("XDG_CONFIG_HOME".into(), home.path().display().to_string());
    }
    let command = case["command"].as_str().unwrap();
    let captured: Value = serde_json::from_str(SUMMARY).unwrap();
    let version = host_python_version();
    let host = |text: &str| {
        let text = text
            .replace(&base.display().to_string(), "<root>")
            .replace(&bin.display().to_string(), "<compiler-bin>");
        match &python {
            Some(python) => {
                let text = as_captured(&text, python);
                match &version {
                    Some(version) => text.replace(version, captured["python"].as_str().unwrap()),
                    None => text,
                }
            }
            None => text,
        }
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
        "export-compilation-context" => &["ici", "export-compilation-context", "--prepare"],
        "verify-deep" => &["ici", "verify", "--profile", "deep", "--report"],
        _ => &["ici", command, "--report"],
    };
    let (exit_code, stdout, stderr) = if binary {
        let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(&argv[1..])
            .current_dir(&base)
            .env_clear()
            .envs(&environment)
            .output()
            .unwrap();
        (
            output.status.code().unwrap_or(-1),
            String::from_utf8(output.stdout).unwrap(),
            String::from_utf8(output.stderr).unwrap(),
        )
    } else {
        let invocation = registry::parse(argv.iter().copied()).unwrap();
        let output = commands::dispatch(&invocation, &base, &environment).unwrap();
        (output.exit_code, output.stdout, output.stderr)
    };
    let saved = if command == "verify-deep" || baseline.is_some() {
        "verify".to_owned()
    } else {
        command.replace('-', "_")
    };
    let report = fs::read_to_string(base.join(format!("{saved}_report.json")))
        .map(|text| serde_json::from_str(&host(&text)).unwrap())
        .unwrap_or(Value::Null);
    if command == "verify-deep" && python.is_none() && !case["report"]["results"].is_array() {
        // The C++ validation capture keeps only the build and binary_compat engine results
        // (the preparation captures keep the whole saved report).
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
        return (
            exit_code,
            String::new(),
            host(&stderr),
            Value::Object(engines),
        );
    }
    if let Some(baseline) = baseline {
        return (
            exit_code,
            host(&stdout),
            host(&stderr),
            json!({"baseline": baseline, "report": report}),
        );
    }
    (exit_code, host(&stdout), host(&stderr), report)
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
        // The gzip size of a gcov report names a random temporary path, so it can equal frozen's by
        // chance, and an allowance that can go unused is no stale one.
        assert_not_stale(|entry| {
            entry.0.starts_with("cppval")
                && !slow(entry.0)
                && !entry.1.ends_with("compressed_bytes")
        });
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
    cpp_oracle_differences(CPP_VALIDATION, "cppval", false, selected)
}

const CPP_PREPARATION: &str =
    include_str!("../../../tests/rust-parity/cpp/preparation-failures-oracle.json");

/// Frozen canonical compile-database preparation (`capture_preparation_failures.py`): the saved
/// `ici verify` report (only compile_db enabled) equals frozen's in full, plus the exit code.
/// Positive: a complete qmake capture and a configured CMake database; negative: a qmake capture
/// that misses a production unit (`qmake-capture-incomplete`); failure: a failing qmake
/// configure, capture build or compiler resolution, a failing CMake configure and no `cmake` on
/// PATH, after which frozen keeps analysing without a database and reports the preparation
/// diagnostic (`verify-sources:` cases with lint, cycle, dead and complexity beside it), and
/// `verify --profile deep` whose BuildEngine reports the failed configure (BuildError) beside a
/// not-applicable binary_compat, alone and with every default engine (the test, sanitizer and
/// thread-sanitizer sessions fail at their own configure, so nothing publishes a manifest).
/// `ICI_CPPVAL_CASES` narrows a development run.
#[test]
#[ignore = "explicit actual g++ 15.2.0, cmake 4.2.3, qmake6 and make, as captured (ICI_TEST_CXX=/usr/bin/g++)"]
fn legacy_cpp_preparation_failures_equal_frozen_except_allowed() {
    let only: Vec<String> = std::env::var("ICI_CPPVAL_CASES")
        .map(|value| value.split(',').map(str::to_owned).collect())
        .unwrap_or_default();
    let differences = cpp_oracle_differences(CPP_PREPARATION, "cppprep", true, |name| {
        !name.contains(REAL_NINJA)
            && (only.is_empty() || only.iter().any(|prefix| name.starts_with(prefix)))
    });
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    if only.is_empty() {
        assert_not_stale(|entry| entry.0.starts_with("cppprep") && !entry.0.contains(REAL_NINJA));
        assert_text_not_stale(|entry| {
            entry.0.starts_with("cppprep") && !entry.0.contains(REAL_NINJA)
        });
    }
}

/// The preparation cases that need a real Ninja on PATH.
const REAL_NINJA: &str = "cmake-ninja-";

/// Frozen CMake preparation with `CMAKE_GENERATOR=Ninja Multi-Config` in the environment and a
/// real Ninja on PATH: the canonical configure completes with a multi-configuration generator,
/// which cannot prove the single Release configuration (`cmake-generator-unsupported`), under
/// `ici verify` and `ici export-compilation-context --prepare`. The same refusal from a cache
/// fixture is `verify:cmake-generator-unsupported` in the test above.
#[test]
#[ignore = "explicit actual g++ 15.2.0, cmake 4.2.3 and a real ninja, as captured (ICI_TEST_CXX=/usr/bin/g++, ICI_TEST_NINJA)"]
fn legacy_cpp_preparation_with_a_multi_config_ninja_equals_frozen() {
    let differences = cpp_oracle_differences(CPP_PREPARATION, "cppprep", true, |name| {
        name.contains(REAL_NINJA)
    });
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0.contains(REAL_NINJA));
}

/// The canonical CMake analysis database is configured, not built (decisions.md "canonical CMake
/// 분석 DB 준비"): every `cmake` the run starts goes through a PATH wrapper that logs its argv.
/// Positive: plain `verify` and `export-compilation-context --prepare` of a buildable tree run
/// `cmake --version` and the configure only; a database that names a source the build generates
/// under the native shadow builds that shadow once (`--build <shadow> --parallel 2`), also when
/// that build fails (`cmake-generation-failed`, frozen's exit 1); `verify --profile deep`, whose
/// BuildEngine consumes the release build, builds it once. Negative: a project with its own
/// `compile_commands.json` configures nothing for analysis (deep: the BuildEngine's one
/// configure and build, without `-DCMAKE_UNITY_BUILD=OFF`).
#[test]
#[ignore = "explicit actual g++ 15.2.0 and cmake 4.2.3, as captured (ICI_TEST_CXX=/usr/bin/g++)"]
fn legacy_cmake_analysis_database_builds_only_to_generate_sources() {
    let oracle: Value = serde_json::from_str(CPP_PREPARATION).unwrap();
    let log_directory = tempfile::tempdir().unwrap();
    let wrapper = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$ICI_TRACE_CMAKE_LOG\"\nexec /usr/bin/cmake \"$@\"\n";
    let run = |name: &str, label: &str| {
        let log = log_directory.path().join(label);
        let mut case = oracle["cases"][name].clone();
        case["path"] = json!("wrapped-cmake-trace");
        case["scripts"] = json!({"cmake": wrapper});
        case["environment"] = json!({"ICI_TRACE_CMAKE_LOG": log.display().to_string()});
        let (exit, _, stderr, _) = cpp_validation_replay(&case, false);
        let lines: Vec<String> = fs::read_to_string(&log)
            .unwrap_or_default()
            .lines()
            .map(str::to_owned)
            .collect();
        (exit, lines, stderr)
    };
    let builds = |lines: &[String]| {
        lines
            .iter()
            .filter(|line| line.starts_with("--build "))
            .cloned()
            .collect::<Vec<_>>()
    };
    let native_shadow = |line: &str| {
        let shadow = line
            .strip_prefix("--build ")
            .and_then(|rest| rest.strip_suffix(" --parallel 2"))
            .unwrap_or_else(|| panic!("not the native generation build: {line}"));
        let (_, owned) = shadow
            .split_once("/.ici/rust-v1/build/legacy-")
            .unwrap_or_else(|| panic!("not the native shadow: {line}"));
        assert!(owned.ends_with("/release"), "{line}");
    };
    for (name, frozen_exit) in [
        ("verify:cmake-configured", 0),
        ("export:cmake-configured", 0),
    ] {
        let (exit, lines, stderr) = run(name, name);
        assert_eq!(exit, frozen_exit, "{name}: {stderr}");
        assert_eq!(
            lines.len(),
            2,
            "{name}: --version and the configure only: {lines:?}"
        );
        assert_eq!(lines[0], "--version", "{name}");
        assert!(lines[1].starts_with("-S "), "{name}: {lines:?}");
        assert!(builds(&lines).is_empty(), "{name}: {lines:?}");
    }
    for name in [
        "verify:cmake-generated-source",
        "verify:cmake-generation-failed",
        "verify:cmake-generated-then-build-failure",
        "verify-deep:cmake-generated-then-build-failure",
        "verify-deep:cmake-build-failure",
    ] {
        let (exit, lines, stderr) = run(name, name);
        assert_eq!(
            json!(exit),
            oracle["cases"][name]["exit_code"],
            "{name}: {stderr}"
        );
        let built = builds(&lines);
        assert_eq!(built.len(), 1, "{name}: one build: {lines:?}");
        native_shadow(&built[0]);
    }
    // The project's own database wins: nothing is configured or built for analysis, and the
    // deep BuildEngine builds its release artifacts once (without the analysis flag).
    for (name, configures) in [
        ("verify:cmake-own-database", 0),
        ("verify-deep:cmake-own-database", 1),
    ] {
        let (exit, lines, stderr) = run(name, name);
        assert_eq!(
            json!(exit),
            oracle["cases"][name]["exit_code"],
            "{name}: {stderr}"
        );
        let configured: Vec<_> = lines
            .iter()
            .filter(|line| line.starts_with("-S "))
            .collect();
        assert_eq!(configured.len(), configures, "{name}: {lines:?}");
        assert!(
            configured
                .iter()
                .all(|line| !line.contains("-DCMAKE_UNITY_BUILD=OFF")),
            "{name}: {lines:?}"
        );
        assert_eq!(builds(&lines).len(), configures, "{name}: {lines:?}");
    }
}

const PYTHON_CPP: &str =
    include_str!("../../../tests/rust-parity/cpp/python-cpp-metrics-oracle.json");

/// Frozen legacy `type = "python"` projects with C/C++ sources (`capture_python_cpp_metrics.py`):
/// the saved full `ici verify` report and the standalone `ici complexity` / `ici cognitive`
/// reports equal frozen's in full through the real CLI, plus the exit code. Positive: functions
/// under the thresholds and a `.cpp` source every C/C++-reading engine measures; negative:
/// functions over them, a `.cc`-only tree complexity's `*.cpp` rglob leaves unmeasured, a
/// `*.cpp` outside `source_dirs` that opens it, a header-only `include/` tree (C/C++ scope for
/// cycle, dup, dead and the support matrix only; a Qt `Q_OBJECT` header there stays outside
/// lint's Qt codegen scan), `verify --profile deep` (the deep Python test-quality pass, also
/// declared `type = "hybrid"`, and its reruns and mutation probe after `_mark_zero_tests`) and
/// a `tests/` holding only a C++ suite (the empty-pytest FAIL beside the measured C++ suite);
/// failure: a C++ body the scanner cannot close, reruns pytest exits 4 on and a failing
/// mutation probe. `ICI_CPPVAL_CASES`
/// (comma-separated case prefixes) narrows a development run and then skips the stale check.
#[test]
#[ignore = "explicit actual g++ 15.2.0 lint fallback and pytest/mypy interpreter, as captured (ICI_TEST_CXX=/usr/bin/g++, ICI_TEST_PYTHON)"]
fn legacy_python_project_cpp_sources_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(PYTHON_CPP).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let only: Vec<String> = std::env::var("ICI_CPPVAL_CASES")
        .map(|value| value.split(',').map(str::to_owned).collect())
        .unwrap_or_default();
    let differences = cpp_oracle_differences(PYTHON_CPP, "pycpp", false, |name| {
        only.is_empty() || only.iter().any(|prefix| name.starts_with(prefix))
    });
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    if only.is_empty() {
        assert_not_stale(|entry| entry.0.starts_with("pycpp"));
        assert_text_not_stale(|entry| entry.0.starts_with("pycpp"));
    }
}

fn cpp_oracle_differences(
    text: &str,
    prefix: &str,
    binary: bool,
    selected: impl Fn(&str) -> bool,
) -> Vec<String> {
    let oracle: Value = serde_json::from_str(text).unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler (ICI_TEST_CXX)");
    assert_eq!(compiler, "/usr/bin/g++", "the capture ran the PATH g++");
    for (tool, key) in [
        (compiler.as_str(), "gxx"),
        ("/usr/bin/cmake", "cmake"),
        ("/usr/bin/qmake6", "qmake"),
    ] {
        if oracle[key].is_null() {
            continue;
        }
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
        let label = format!("{prefix}/{name}");
        let (exit, stdout, stderr, mut native) = cpp_validation_replay(case, binary);
        let mut folded;
        let case = if prefix == "cppval" && CPPVAL_TEXT_DUPLICATE.contains(&name.as_str()) {
            folded = case.clone();
            differences.extend(fold_duplicate_text_rows(
                &label,
                &mut folded["report"],
                &native,
            ));
            &folded
        } else {
            case
        };
        // A case that recorded its stderr (the export refusals) compares it exactly.
        if let Some(frozen) = case["stderr"].as_str() {
            differences.extend(text_differences(
                &format!("{label}/stderr"),
                frozen,
                &stderr,
            ));
        }
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
            differences.extend(tool_evidence_kinds(
                &format!("{label}/baseline"),
                &case["baseline"],
                &native["baseline"],
                "",
            ));
            differences.extend(result_differences(
                &format!("{label}/baseline"),
                &case["baseline"],
                &native["baseline"],
            ));
            native = native["report"].take();
        }
        if prefix == "cppprep" {
            differences.extend(legacy_session_configure(
                &label,
                &case["report"],
                &mut native,
            ));
        }
        differences.extend(tool_evidence_kinds(&label, &case["report"], &native, ""));
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
        // A verify suite: a required engine both reports leave NOT_RUN (ERROR).
        let shared_not_run = || {
            case["report"]["results"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|engine| not_run(engine))
                .any(|engine| {
                    native["results"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .any(|other| {
                            other["engine_name"] == engine["engine_name"] && not_run(other)
                        })
                })
        };
        if exit != frozen_exit
            && !((exit, frozen_exit) == (3, 1)
                && ((not_run(&native) && not_run(&case["report"])) || shared_not_run()))
        {
            differences.push(format!("{label}: exit {exit} vs frozen {frozen_exit}"));
        }
        // A JSON payload on stdout (the export) is compared structurally.
        if case["report"].is_null()
            && let (Ok(frozen), Ok(native)) = (
                serde_json::from_str::<Value>(case["stdout"].as_str().unwrap()),
                serde_json::from_str::<Value>(&stdout),
            )
        {
            differences.extend(result_differences(
                &format!("{label}/stdout"),
                &frozen,
                &native,
            ));
        } else if case["report"].is_null() {
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

/// decisions.md "native C++ shadow build 위치" (CPPPREP_SHADOW): a legacy CMake session's
/// `cmake configure` record is frozen's argv with the native shadow of the same variant as `-B`
/// (`<root>/.ici/rust-v1/build/legacy-<16 hex>/<variant>` for frozen's
/// `build/ici-cmake{-build,,-asan,-tsan}`), and the release session (the deep BuildEngine reuses
/// the analysis-database preparation) has exactly one more, trailing, `-DCMAKE_UNITY_BUILD=OFF`.
/// Its `cmake build` record is frozen's `cmake --build <shadow> --parallel` with the same native
/// shadow and the job bound native always passes (`--parallel 2`; decisions.md "canonical CMake
/// 분석 DB 준비"), one argument more.
/// Each such record is checked here element by element and then carries frozen's argv; any other
/// argv difference in any record stays a difference.
fn legacy_session_configure(label: &str, frozen: &Value, native: &mut Value) -> Vec<String> {
    const SESSIONS: [(&str, &str, Option<&str>); 4] = [
        (
            "release",
            "<root>/build/ici-cmake-build",
            Some("-DCMAKE_UNITY_BUILD=OFF"),
        ),
        ("coverage", "<root>/build/ici-cmake", None),
        ("sanitize", "<root>/build/ici-cmake-asan", None),
        ("thread-sanitize", "<root>/build/ici-cmake-tsan", None),
    ];
    let mut out = vec![];
    // The manifest rows record the same build argv, workspace-relative.
    let manifests = |frozen: &Value, native: &mut Value| {
        let pairs = native["artifact_manifests"]
            .as_array_mut()
            .into_iter()
            .flatten()
            .zip(
                frozen["artifact_manifests"]
                    .as_array()
                    .into_iter()
                    .flatten(),
            );
        for (manifest, frozen_manifest) in pairs {
            let rows = manifest["artifacts"]
                .as_array_mut()
                .into_iter()
                .flatten()
                .zip(
                    frozen_manifest["artifacts"]
                        .as_array()
                        .into_iter()
                        .flatten(),
                );
            for (row, frozen_row) in rows {
                let command = row["command"].as_array().cloned().unwrap_or_default();
                let shadow = command.get(2).and_then(Value::as_str).unwrap_or("");
                let native_shadow = shadow
                    .strip_prefix(".ici/rust-v1/build/legacy-")
                    .is_some_and(|rest| rest.ends_with("/release"));
                let expected = [
                    json!("cmake"),
                    json!("--build"),
                    json!(shadow),
                    json!("--parallel"),
                    json!("2"),
                ];
                if native_shadow
                    && command == expected
                    && frozen_row["command"]
                        == json!(["cmake", "--build", "build/ici-cmake-build", "--parallel"])
                {
                    row["command"] = frozen_row["command"].clone();
                }
            }
        }
    };
    manifests(&frozen["analysis_context"], &mut native["analysis_context"]);
    let frozen_build = frozen["results"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|other| other["engine_name"] == "build")
        .cloned()
        .unwrap_or(Value::Null);
    let Some(results) = native["results"].as_array_mut() else {
        return out;
    };
    for result in results {
        if result["engine_name"] == "build" {
            manifests(&frozen_build, result);
        }
        let engine = result["engine_name"].clone();
        let Some(frozen_records) = frozen["results"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|other| other["engine_name"] == engine)
            .and_then(|other| other["tool_evidence"].as_array())
        else {
            continue;
        };
        let Some(records) = result["tool_evidence"].as_array_mut() else {
            continue;
        };
        for (record, frozen_record) in records.iter_mut().zip(frozen_records) {
            let build = record["name"] == "cmake build" && frozen_record["name"] == "cmake build";
            if !build
                && (record["name"] != "cmake configure"
                    || frozen_record["name"] != "cmake configure")
            {
                continue;
            }
            // `cmake -S <root> -B <shadow> …` or `cmake --build <shadow> --parallel`.
            let at = if build { 2 } else { 4 };
            let argv = record["argv"].as_array().cloned().unwrap_or_default();
            let shadow = argv.get(at).and_then(Value::as_str).unwrap_or("");
            let Some((_, frozen_shadow, extra)) = shadow
                .strip_prefix("<root>/.ici/rust-v1/build/legacy-")
                .and_then(|rest| rest.split_once('/'))
                .filter(|(digest, _)| {
                    digest.len() == 16 && digest.chars().all(|c| c.is_ascii_hexdigit())
                })
                .and_then(|(_, variant)| SESSIONS.iter().find(|(name, _, _)| *name == variant))
            else {
                continue;
            };
            let mut expected = frozen_record["argv"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            if expected.get(at).and_then(Value::as_str) != Some(*frozen_shadow) {
                out.push(format!(
                    "{label}: results[{engine}] {}: frozen shadow is not {frozen_shadow}",
                    record["name"]
                ));
                continue;
            }
            expected[at] = json!(shadow);
            if build {
                expected.push(json!("2"));
            } else if !label.contains("own-database") {
                // A project's own database has no analysis configure to reuse.
                expected.extend(extra.map(|flag| json!(flag)));
            }
            if argv != expected {
                out.push(format!(
                    "{label}: results[{engine}] {} argv {argv:?} is not frozen's \
                     {expected:?} (the native {shadow} shadow)",
                    record["name"]
                ));
                continue;
            }
            record["argv"] = frozen_record["argv"].clone();
        }
    }
    out
}

/// decisions.md "C++ 도구 증거는 실제 실행 기록" (legacy gcov text consequence, approved after
/// these captures): frozen `parse_gcov_dir` keeps one `coverage_files` row per `.gcov` report
/// (coverage_support.py:393), native sums the reports of one source into one row. For these
/// cases (GCOV_TEXT_DUP_WRAPPER copies the src/calc.cpp report under a second name) frozen's
/// rows of one source, its `included_sources` entries and the `Coverage:File` targets and
/// findings they produce are folded the way native sums them: counters added, ratios and
/// messages kept (the copies have equal ratios, messages and fingerprints, checked). Each fold
/// is visible: it happens only where a case allowance names `<list>{fold}` (Len), so a fold
/// that stops happening is a stale allowance and an unallowed one is the raw list difference.
/// The folded finding keeps frozen's fingerprint, so its difference from native's (which
/// digests the summed metrics) is the `.findings[].fingerprint` case allowance; frozen's
/// findings are ordered by native's fingerprint only to align that finding, and every other
/// finding's fingerprint must equal native's (checked here, beside the allowance). Everything
/// else, including line/branch/function coverage, `coverage_totals`, every function row and
/// TEM, is compared in full.
const CPPVAL_TEXT_DUPLICATE: [&str; 2] = [
    "test:direct-gcov-text-duplicate",
    "test:cmake-gcov-text-duplicate",
];

fn fold_duplicate_text_rows(label: &str, frozen: &mut Value, native: &Value) -> Vec<String> {
    fn add(into: &mut Value, from: &Value, keys: &[&str]) {
        for key in keys {
            let sum = into[*key].as_u64().unwrap() + from[*key].as_u64().unwrap();
            into[*key] = json!(sum);
        }
    }
    fn fold(
        rows: &mut Vec<Value>,
        same: impl Fn(&Value, &Value) -> bool,
        merge: impl Fn(&mut Value, &Value),
    ) {
        let mut kept: Vec<Value> = vec![];
        for row in rows.drain(..) {
            match kept.iter_mut().find(|other| same(other, &row)) {
                Some(other) => merge(other, &row),
                None => kept.push(row),
            }
        }
        *rows = kept;
    }
    let extra = &mut frozen["extra"];
    if allowed(label, ".extra.coverage_files{fold}", Len) {
        fold(
            extra["coverage_files"].as_array_mut().unwrap(),
            |left, right| left["file"] == right["file"],
            |into, from| {
                assert_eq!(
                    (&into["cover"], &into["branch_cover"]),
                    (&from["cover"], &from["branch_cover"]),
                    "folded reports must have equal ratios"
                );
                add(into, from, &["stmts", "covered", "miss", "nb", "cb"]);
                let mut missing: Vec<u64> = into["missing_lines"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .chain(from["missing_lines"].as_array().unwrap())
                    .map(|line| line.as_u64().unwrap())
                    .collect();
                missing.sort_unstable();
                missing.dedup();
                missing.truncate(30);
                into["missing_lines"] = json!(missing);
            },
        );
    }
    if allowed(
        label,
        ".extra.coverage_provenance.scope.included_sources{fold}",
        Len,
    ) {
        fold(
            extra["coverage_provenance"]["scope"]["included_sources"]
                .as_array_mut()
                .unwrap(),
            |left, right| left == right,
            |_, _| {},
        );
    }
    let lines = ["covered_lines", "executable_lines", "missed_lines"];
    if allowed(label, ".targets{fold}", Len) {
        fold(
            frozen["targets"].as_array_mut().unwrap(),
            |left, right| {
                left["target_name"] == "Coverage:File"
                    && right["target_name"] == "Coverage:File"
                    && left["file_path"] == right["file_path"]
            },
            |into, from| {
                assert_eq!(into["message"], from["message"]);
                add(&mut into["metrics"], &from["metrics"], &lines);
            },
        );
    }
    let mut out = vec![];
    if !allowed(label, ".findings{fold}", Len) {
        return out;
    }
    let file = |finding: &Value| finding["tool_rule_id"] == "Coverage:File";
    let findings = frozen["findings"].as_array_mut().unwrap();
    let mut folded_paths = vec![];
    for (index, finding) in findings
        .iter()
        .enumerate()
        .filter(|(_, finding)| file(finding))
    {
        let path = &finding["primary_location"]["path"];
        if findings[..index]
            .iter()
            .any(|other| file(other) && other["primary_location"]["path"] == *path)
            && !folded_paths.contains(path)
        {
            folded_paths.push(path.clone());
        }
    }
    fold(
        findings,
        |left, right| {
            file(left)
                && file(right)
                && left["primary_location"]["path"] == right["primary_location"]["path"]
        },
        |into, from| {
            assert_eq!(into["message"], from["message"]);
            assert_eq!(
                into["fingerprint"], from["fingerprint"],
                "frozen's duplicate Coverage:File findings share one fingerprint"
            );
            for key in lines {
                let sum = into["metrics"][key]["value"].as_u64().unwrap()
                    + from["metrics"][key]["value"].as_u64().unwrap();
                into["metrics"][key]["value"] = json!(sum);
            }
        },
    );
    // Alignment only: a folded finding sorts where native's (summed-metrics) fingerprint puts
    // it; the value compared stays frozen's.
    let native_findings: Vec<Value> = native["findings"].as_array().cloned().unwrap_or_default();
    let key = |finding: &Value| -> String {
        let own = finding["fingerprint"]
            .as_str()
            .unwrap_or_default()
            .to_owned();
        if !file(finding) {
            return own;
        }
        native_findings
            .iter()
            .find(|other| {
                file(other)
                    && other["primary_location"]["path"] == finding["primary_location"]["path"]
            })
            .and_then(|other| other["fingerprint"].as_str())
            .map_or(own, str::to_owned)
    };
    findings.sort_by_key(|finding| key(finding));
    for (left, right) in findings.iter().zip(&native_findings) {
        if !(file(left) && folded_paths.contains(&left["primary_location"]["path"]))
            && left["fingerprint"] != right["fingerprint"]
        {
            out.push(format!(
                "{label}: .findings[].fingerprint outside the folded Coverage:File findings (Value)"
            ));
        }
    }
    out
}

/// CPPVAL_TOOLS lets the record count, order and argv of C++ tool evidence differ (native
/// compiles each source once, runs one `gcov`); a count difference must not hide missing
/// evidence, so the record kinds (names) of every engine result are compared on their own: a
/// kind on one side only is a `{prefix}.tool_evidence{kind:<name>}` difference (Missing: frozen
/// only, NativeOnly: native only) that needs its own allowance, and an empty native list where
/// frozen recorded a process is always a difference.
fn tool_evidence_kinds(label: &str, frozen: &Value, native: &Value, prefix: &str) -> Vec<String> {
    let mut out = vec![];
    if let Some(engines) = frozen["results"].as_array() {
        for engine in engines {
            let name = engine["engine_name"].as_str().unwrap_or_default();
            let other = native["results"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|other| other["engine_name"] == engine["engine_name"])
                .unwrap_or(&Value::Null);
            out.extend(tool_evidence_kinds(
                label,
                engine,
                other,
                &if prefix.is_empty() {
                    format!("results[{name}]")
                } else {
                    format!("{prefix}.results[{name}]")
                },
            ));
        }
        return out;
    }
    let Some(records) = frozen["tool_evidence"].as_array() else {
        for (key, value) in frozen.as_object().into_iter().flatten() {
            if value.is_object() {
                out.extend(tool_evidence_kinds(
                    label,
                    value,
                    &native[key],
                    &format!("{prefix}.{key}"),
                ));
            }
        }
        return out;
    };
    let theirs = native["tool_evidence"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let process = |record: &Value| {
        record["argv"]
            .as_array()
            .is_some_and(|argv| !argv.is_empty())
    };
    if theirs.is_empty() && records.iter().any(process) {
        out.push(format!(
            "{label}: {prefix}.tool_evidence: native recorded no process where frozen recorded {}",
            records.len()
        ));
    }
    let kinds = |list: &[Value]| -> BTreeSet<String> {
        list.iter()
            .map(|record| record["name"].as_str().unwrap_or_default().to_owned())
            .collect()
    };
    let (left, right) = (kinds(records), kinds(&theirs));
    for (kind, side) in left
        .difference(&right)
        .map(|kind| (kind, Missing))
        .chain(right.difference(&left).map(|kind| (kind, NativeOnly)))
    {
        let path = format!("{prefix}.tool_evidence{{kind:{kind}}}");
        if !allowed(label, &path, side) {
            out.push(format!("{label}: {path} ({side:?})"));
        }
    }
    out
}

/// Frozen `TestEngine._threshold_breaches` for Python coverage (`capture_python_thresholds.py`):
/// the saved `ici test --report` of each case equals frozen's in full. Positive (`breach`): the
/// one breached branch minimum is a `Threshold: Branch coverage` WARN target and the met TEM
/// minimum adds none; negative (`met`): no threshold target; failure (`failure`): a failing test
/// is never judged against the thresholds; process failure (`coverage-failure`): the `coverage
/// pytest` process fails (a missing coverage plugin) and its tool evidence, returncode 1 with
/// the empty `error` of an exited process, equals frozen's. Frozen `pytest_result_has_evidence`
/// (coverage_support.py:70-76) and the `_parse_pytest_result` ERROR branches (test_output.py:
/// 91-107): positive (`pytest-summary-only`): with the terminal reporter removed a lone `1
/// passed` line is evidence and the run is measured, and a lone `1 failed` line of a failing suite
/// (`pytest-summary-failed`) is its `[Python] Failed (1)` FAIL target of a measured run; negative
/// (`pytest-success-unparseable`): exit 0 with only `collected 1 items` is 'Pytest returned
/// success without parseable test results' with frozen's 0/1 counts; failure: exit 1 without a
/// failure (`pytest-exit-one-no-failure`, 1/1) and a collection error's exit 2
/// (`pytest-exit-outside-contract`, its `[Python] Failed (1)` summary target, 0/1) keep frozen's
/// tool errors and counts. Frozen `compute_python_function_coverage` (coverage_support.py:
/// 408-450, `functions-order`): the function rows in `ast.walk` breadth-first order (nested
/// functions, methods and `if`/`elif`/`else`/`try`/`except` bodies by their `ast` depth).
/// Frozen `_measure_coverage` (test_coverage.py:353-363, `functions-two-of-three`): 2 of 3
/// functions covered is the unrounded 66.666... function coverage the TEM (3.33), the summary and
/// `extra.function_coverage` read, and the branchless scope's vacuous 100.0 branch coverage meets
/// the branch minimum (no `Threshold: Branch coverage` target, `Branch: 100.0%` in the summary).
/// Frozen `compute_python_function_coverage` (coverage_support.py:436-447, `functions-one-line`):
/// a one-line function's body starts on its `def` line, which runs at import, so the called
/// `called` and the uncalled `uncalled` are both covered rows and only the two-line `spread` is
/// not (2 of 3).
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
        ("pytest-success-unparseable", 0),
        ("pytest-summary-only", 1),
        ("pytest-summary-failed", 0),
        ("functions-order", 1),
        ("functions-two-of-three", 0),
        ("functions-one-line", 0),
        ("coverage-no-statements", 0),
        ("coverage-required-no-statements", 0),
        ("coverage-extra-file", 0),
        ("pytest-exit-one-no-failure", 0),
        ("pytest-exit-outside-contract", 0),
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
        let expected_exit = if case["report"]["evidence"] == "NOT_RUN" {
            vec![3]
        } else {
            frozen_exit.clone()
        };
        if exits != expected_exit {
            differences.push(format!(
                "thresholds/{name}: exit {exits:?} (frozen {frozen_exit:?})"
            ));
        }
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
        if evidence(&native) != evidence(&case["report"]) {
            differences.push(format!(
                "thresholds/{name}: tool evidence {:?} (frozen {:?})",
                evidence(&native),
                evidence(&case["report"])
            ));
        }
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
        if thresholds.len() != expected {
            differences.push(format!("thresholds/{name}: {thresholds:?}"));
        }
        differences.extend(result_differences(
            &format!("thresholds/{name}"),
            &case["report"],
            &native,
        ));
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0.starts_with("thresholds"));
}

/// Frozen `_check_python_resource_warnings` through the real CLI's saved report
/// (`capture_python_sanitize_full.py`): the plain pytest command, a project `addopts` that does not
/// apply, the output snippet in the summary, target, finding and tool evidence, the verdict read
/// from the summary counts, and every real traceback footer its own target. Three outputs only
/// quote a ResourceWarning: frozen counted them, native corrects them to the tool failure.
#[test]
#[ignore = "explicit actual pytest interpreter, as captured (ICI_TEST_PYTHON)"]
fn python_sanitize_saved_reports_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(PYTHON_SANITIZE).unwrap();
    let length = oracle["root_length"].as_u64().unwrap() as usize;
    let mut differences = vec![];
    let mut compared = 0;
    let mut corrected = 0;
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let (exit, native) = replay_sanitize_at_length(&case["tree"], length, &case["environment"]);
        if ["located-outside", "located-outside-only", "unlocated"].contains(&name.as_str()) {
            // decisions.md "pytest 출력을 ResourceWarning으로 잘못 읽은 판정": frozen counted a line
            // these tests only print or raise as a warning (and two of its reports never saved).
            // Pytest failed and reported no ResourceWarning, so this is its tool failure, without
            // a defect and without a success.
            assert_eq!(exit, 3, "{name}");
            assert_eq!(native["status"], "ERROR", "{name}");
            assert_eq!(native["evidence"], "NOT_RUN", "{name}");
            let targets = native["targets"].as_array().unwrap();
            assert_eq!(targets.len(), 1, "{name}: {targets:?}");
            assert_eq!(targets[0]["file_path"], "tests", "{name}");
            assert_eq!(targets[0]["status"], "ERROR", "{name}");
            assert!(
                targets[0]["message"]
                    .as_str()
                    .unwrap()
                    .starts_with("Pytest ResourceWarning check failed with exit code 1: "),
                "{name}: {}",
                targets[0]
            );
            // The one finding is the legacy view of that ERROR target, not a resource defect.
            let findings = native["findings"].as_array().unwrap();
            assert_eq!(findings.len(), 1, "{name}: {findings:?}");
            assert_eq!(
                findings[0]["rule_id"], "ici.legacy.sanitize.target",
                "{name}"
            );
            assert_eq!(findings[0]["primary_location"]["path"], "tests", "{name}");
            assert_eq!(findings[0]["message"], targets[0]["message"], "{name}");
            // No external path stays anywhere in the report's verdict data.
            for part in [
                &native["targets"],
                &native["findings"],
                &native["summary"],
                &native["extra"]["native_checks"][0]["limitations"],
            ] {
                assert!(!part.to_string().contains("/usr/lib/outside"), "{part}");
            }
            corrected += 1;
            continue;
        }
        compared += 1;
        // decisions.md "legacy 종료 코드 3의 범위".
        let frozen_exit = case["exit_code"].as_i64().unwrap();
        let expected_exit = if case["report"]["evidence"] == "NOT_RUN" {
            3
        } else {
            frozen_exit
        };
        if i64::from(exit) != expected_exit {
            differences.push(format!(
                "python-sanitize/{name}: exit {exit} (frozen {frozen_exit})"
            ));
        }
        differences.extend(result_differences(
            &format!("python-sanitize/{name}"),
            &case["report"],
            &native,
        ));
    }
    assert!(compared >= 16 && corrected == 3, "{compared} {corrected}");
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| entry.0.starts_with("python-sanitize"));
}

/// Frozen `TestEngine.run` over a project that measured both languages, one result through the
/// real CLI's saved report (`capture_hybrid_test.py`). Positive: `pooled-passes-a-language-minimum`
/// passes though C++ alone misses the minimum. Negative: `pooled-misses-the-minimum` and
/// `pooled-fails-tem-each-language-meets` fail though a language, or each language, meets it.
/// Failure: a failing test of either language withholds every threshold breach.
#[test]
#[ignore = "explicit actual pytest interpreter, as captured (ICI_TEST_PYTHON), and g++/gcov"]
fn hybrid_test_results_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(HYBRID_TEST).unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    let mut differences = vec![];
    for (name, status) in [
        ("measured", "FAIL"),
        ("thresholds-low", "PASS"),
        ("every-line-covered", "PASS"),
        ("pooled-passes-a-language-minimum", "PASS"),
        ("pooled-misses-the-minimum", "FAIL"),
        ("pooled-fails-tem-each-language-meets", "FAIL"),
        ("python-failing", "FAIL"),
        ("cpp-failing", "FAIL"),
        ("no-python-test", "FAIL"),
        ("file-minimum", "FAIL"),
        ("deep", "FAIL"),
        ("warn-mode", "WARN"),
        ("not-required", "FAIL"),
        ("pooled-rounding", "PASS"),
        ("python-only-rounding", "PASS"),
        ("no-cpp-test", "FAIL"),
        ("no-cpp-test-required", "ERROR"),
        ("verify-deep", "FAIL"),
        // decisions.md "legacy 변경 줄 coverage 선택": frozen's selection and its refusal.
        ("changed-lines", "FAIL"),
        ("changed-non-source-file", "ERROR"),
        ("changed-test-file", "ERROR"),
        ("changed-outside-source-dirs", "ERROR"),
        ("python-only-changed-non-source", "ERROR"),
        ("cpp-only-changed-non-source", "ERROR"),
        ("changed-missing-file", "ERROR"),
        ("changed-directory", "ERROR"),
        ("changed-outside-root", "ERROR"),
        ("changed-overlap-in-non-source", "ERROR"),
        // decisions.md "legacy coverage.py의 --source": coverage.py omits a non-package file, so
        // the Python coverage is unmeasured and optional, as in frozen.
        ("python-only-coverage-refused", "WARN"),
        // The same refusal beside a measured C++ half: frozen pools the gcov rows alone
        // ("gcov (partial)") as an optional warning, no policy or threshold target.
        ("python-coverage-refused", "WARN"),
        // A pytest tool error beside a measured C++ half: frozen's ERROR still pools the C++
        // coverage and both languages' cases (exit 1, not the incomplete 3).
        ("python-collection-error", "ERROR"),
        // `coverage_required`: the missing Python evidence is a tool error; the refused report's
        // run has no failure, so its threshold breaches are evaluated.
        ("python-coverage-refused-required", "ERROR"),
        ("python-collection-error-required", "ERROR"),
        // Every C++ test failed to compile or link: the compiled library's notes are measured as
        // executed-zero rows beside Python's (coordinator ruling 2026-11-08).
        ("cpp-test-does-not-compile", "FAIL"),
        ("cpp-test-without-main", "FAIL"),
        ("cpp-test-does-not-compile-required", "ERROR"),
        ("cpp-test-without-main-required", "ERROR"),
    ] {
        let case = &cases[name];
        let steps: Vec<String> = match case["command"].as_array() {
            Some(argv) => argv
                .iter()
                .map(|arg| arg.as_str().unwrap().into())
                .collect(),
            None => vec!["test".into(), "--report".into()],
        };
        let report = format!("{}_report.json", steps[0]);
        let (exits, mut native) = replay(&case["tree"], &Value::Null, &[steps], &report, false);
        if native.get("results").is_some() {
            native = results(&native)
                .iter()
                .find(|engine| engine["engine_name"] == "test")
                .unwrap()
                .clone();
        }
        assert_eq!(exits, codes(&case["exit_code"]), "{name}: exit");
        assert_eq!(native["status"], status, "{name}: verdict");
        differences.extend(result_differences(
            &format!("hybrid-test/{name}"),
            &case["report"],
            &native,
        ));
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    // A gzip size can equal frozen's by chance.
    assert_not_stale(|entry| {
        entry.0.starts_with("hybrid-test") && !entry.1.ends_with("compressed_bytes")
    });
}

/// `capture_failure_ladder.py`: every way a test, coverage or sanitizer tool can give no usable
/// result, through the real CLI. The saved result equals frozen's except the listed allowances,
/// and a process whose output hit the bound ran to its end on both sides.
#[test]
#[ignore = "explicit actual pytest interpreter, as captured (ICI_TEST_PYTHON), and g++/gcov"]
fn tool_failure_ladder_equals_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(FAILURE_LADDER).unwrap();
    let filter = std::env::var("ICI_LADDER_CASES").ok();
    let wanted = |name: &str| {
        filter
            .as_deref()
            .is_none_or(|list| list.split(',').any(|item| item == name))
    };
    let mut differences = vec![];
    let mut exits = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if !wanted(name) {
            continue;
        }
        let steps: Vec<String> = case["command"]
            .as_array()
            .unwrap()
            .iter()
            .map(|arg| arg.as_str().unwrap().into())
            .collect();
        let report = format!("{}_report.json", steps[0]);
        let saved = replay_with(
            &case["tree"],
            &Value::Null,
            &[steps],
            &report,
            false,
            false,
            None,
        );
        let frozen = case["exit_code"].as_i64().unwrap() as i32;
        let three = LADDER_EXIT_THREE.iter().find(|(case, _)| *case == name);
        let expected = if let Some((_, own)) = three {
            // The exit difference applies to this state only, not to the listed name.
            assert_eq!(frozen, 1, "{name}");
            for report in [&case["report"], &saved.report] {
                assert_eq!(report["required"], true, "{name}");
                assert_eq!(report["status"], "ERROR", "{name}");
                assert_eq!(report["evidence"], "NOT_RUN", "{name}");
            }
            assert_ladder_gate(name, own, &saved.native["gate"]);
            3
        } else {
            frozen
        };
        if saved.codes[0] != expected {
            exits.push(format!(
                "{name}: exit {} (expected {expected}, frozen {frozen})",
                saved.codes[0]
            ));
        }
        if matches!(
            name.as_str(),
            "covjson-exit7" | "covjson-over-stdout" | "covjson-over-stderr"
        ) {
            assert_optional_coverage_gate(name, &saved.native);
        }
        if Value::Bool(saved.completed) != case["completed"] {
            differences.push(format!(
                "{name}: ran to its end {} (frozen {})",
                saved.completed, case["completed"]
            ));
        }
        differences.extend(result_differences(
            &format!("failure-ladder/{name}"),
            &case["report"],
            &saved.report,
        ));
    }
    assert!(exits.is_empty(), "{}", exits.join("\n"));
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    if filter.is_none() {
        assert_not_stale(|entry| entry.0.starts_with("failure-ladder"));
    }
}

/// The `covjson-timeout` chain without waiting out 1800 s (decisions.md "legacy `coverage json`의
/// 기한"): the same real pipeline as `covjson-exit7` and `covjson-exit7-required` with the debug
/// build's test-only short step deadline and a `coverage json` that hangs. The timed-out step is
/// that step's own failure, not the suite's: a complete required test keeps the native gate PASS
/// and legacy exit 1 (frozen `covjson-exit7`'s optional tool error class). With
/// `coverage_required` the same failure is required evidence missing, exit 3 ("legacy 종료
/// 코드 3의 범위"), and a really failing test is still exit 1, as that ruling's priority says.
/// The 1800 s measurement of `w-cppval11-timeout-family-native` is not re-run; its result.json
/// was not read directly (only the recorded summary), which this test does not replace.
#[test]
#[ignore = "explicit actual pytest and coverage (ICI_TEST_PYTHON), as captured, with a 3 s test-only deadline"]
fn a_timed_out_coverage_json_step_is_the_optional_or_required_coverage_failure_in_full() {
    let oracle: Value = serde_json::from_str(FAILURE_LADDER).unwrap();
    let hang = |case: &str| {
        let mut tree = oracle["cases"][case]["tree"].clone();
        let wrapper = tree[".wrap/python"]
            .as_str()
            .unwrap()
            .replace("exit 7", "sleep 60");
        assert!(wrapper.contains("sleep 60"));
        tree[".wrap/python"] = json!(wrapper);
        tree
    };
    let run = |tree: &Value| {
        replay_with_env(
            tree,
            &Value::Null,
            &[vec!["test".to_owned(), "--report".to_owned()]],
            "test_report.json",
            false,
            false,
            None,
            &[("ICI_TEST_ONLY_STEP_DEADLINE_SECONDS", "3")],
        )
    };
    let optional = run(&hang("covjson-exit7"));
    assert_eq!(optional.codes[0], 1, "optional: exit");
    assert_optional_coverage_gate("covjson-timeout", &optional.native);
    assert_eq!(optional.report["status"], "ERROR");
    assert_eq!(optional.report["evidence"], "NOT_RUN");
    assert_eq!(
        optional.report["summary"],
        "Coverage JSON generation timed out"
    );
    let check = |id: &str| {
        optional.report["extra"]["native_checks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|check| check["check_id"] == id)
            .unwrap()
            .clone()
    };
    assert_eq!(check("python.coverage")["state"], "FAILED");
    assert_eq!(
        check("python.coverage")["summary"],
        "Coverage JSON generation timed out"
    );
    assert_eq!(check("python.test")["state"], "SUCCEEDED");

    let required = run(&hang("covjson-exit7-required"));
    assert_eq!(required.codes[0], 3, "required: exit");
    assert_eq!(required.report["status"], "ERROR");
    assert_eq!(required.report["evidence"], "NOT_RUN");
    assert_eq!(
        required.report["summary"],
        "Coverage JSON generation timed out; Python coverage evidence was unavailable or malformed"
    );
    assert_ladder_gate(
        "covjson-timeout-required",
        &["project.python.coverage: Coverage JSON generation timed out"],
        &required.native["gate"],
    );

    // Priority: a test that really failed is exit 1 with the same required coverage failure.
    let mut failing = hang("covjson-exit7-required");
    failing["tests/test_core.py"] =
        json!("from src.core import f\n\n\ndef test_f():\n    assert f(1) == 3\n");
    let failing = run(&failing);
    assert_eq!(
        failing.codes[0], 1,
        "failing test beside required coverage: exit"
    );
    assert_eq!(failing.report["status"], "ERROR");
    assert_eq!(failing.report["evidence"], "NOT_RUN");
    assert_eq!(
        failing.report["summary"],
        "Coverage JSON generation timed out; Python coverage evidence was unavailable or malformed"
    );
    assert!(
        failing.report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .any(|target| target["status"] == "FAIL")
    );
    // The same priority for `verify`, whose suite keeps no other required NOT_RUN engine.
    let mut suite = hang("covjson-exit7-required");
    suite["tests/test_core.py"] =
        json!("from src.core import f\n\n\ndef test_f():\n    assert f(1) == 3\n");
    let config = suite["ici.toml"].as_str().unwrap().to_owned();
    suite["ici.toml"] = json!(format!("{config}[engines.sanitize]\nenabled = false\n"));
    let verify = replay_with_env(
        &suite,
        &Value::Null,
        &[vec!["verify".to_owned(), "--report".to_owned()]],
        "verify_report.json",
        false,
        false,
        None,
        &[("ICI_TEST_ONLY_STEP_DEADLINE_SECONDS", "3")],
    );
    assert_eq!(verify.codes[0], 1, "verify beside a failing test: exit");
}

/// The exit-1 exception for a really failing test does not erase an independent input change
/// (decisions.md "legacy 종료 코드 3의 범위"): the failing test and the required coverage error
/// stay in the report, the tool executable that disappeared during the run keeps the input
/// stability cause, and the exit is 3.
#[test]
#[ignore = "explicit actual pytest and coverage (ICI_TEST_PYTHON), as captured"]
fn a_failing_test_beside_a_required_coverage_error_keeps_an_input_change_at_exit_three() {
    let oracle: Value = serde_json::from_str(FAILURE_LADDER).unwrap();
    let mut tree = oracle["cases"]["covjson-gone"]["tree"].clone();
    assert!(tree[".wrap/python"].as_str().unwrap().contains(".gone"));
    tree["tests/test_core.py"] =
        json!("from src.core import f\n\n\ndef test_f():\n    assert f(1) == 3\n");
    let config = tree["ici.toml"].as_str().unwrap().to_owned();
    tree["ici.toml"] = json!(format!("{config}coverage_required = true\n"));
    let saved = replay_with(
        &tree,
        &Value::Null,
        &[vec!["test".to_owned(), "--report".to_owned()]],
        "test_report.json",
        false,
        false,
        None,
    );
    assert_eq!(saved.codes[0], 3, "{}", saved.native["gate"]);
    assert_eq!(saved.native["gate"]["selected"], "INCOMPLETE");
    assert!(
        saved.native["gate"]["reasons"]
            .as_array()
            .unwrap()
            .iter()
            .any(|reason| reason == INPUTS_CHANGED),
        "{}",
        saved.native["gate"]
    );
    assert_eq!(saved.report["status"], "ERROR");
    assert_eq!(saved.report["evidence"], "NOT_RUN");
    assert!(
        saved.report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .any(|target| target["status"] == "FAIL"),
        "{}",
        saved.report["targets"]
    );
}

const BINARY_COMPAT_VANISHED: &str =
    include_str!("../../../tests/rust-parity/cpp/binary-compat-vanished-oracle.json");

/// `capture_binary_compat_vanished.py` (decisions.md "분석 중 사라진 legacy binary_compat
/// 대상"): an artifact that was present before the check and disappears during it is an input
/// stability failure. Frozen reports exit 1 with a generic read error at `.`; native keeps the
/// other artifact's result, reports `AnalysisUnavailable` at the vanished artifact's own path
/// with its cause, and exits 3 with that cause in the gate. Two stable artifacts and a readelf
/// that fails on every artifact are not this rule: they equal frozen's targets, summary, status
/// and exit.
#[test]
#[ignore = "explicit actual g++, make and readelf, as captured"]
fn a_vanished_binary_compat_artifact_is_input_stability_and_keeps_the_other_results() {
    let oracle: Value = serde_json::from_str(BINARY_COMPAT_VANISHED).unwrap();
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/package-make");
    let run = |name: &str| {
        let case = &oracle["cases"][name];
        let tree = json!({
            "ici.toml": oracle["base"],
            "Makefile": oracle["makefile"],
            "pyproject.toml": fs::read_to_string(fixture.join("pyproject.toml")).unwrap(),
            "src/main.cpp": fs::read_to_string(fixture.join("src/main.cpp")).unwrap(),
            ".wrap/readelf": case["readelf"],
        });
        let saved = replay_with(
            &tree,
            &Value::Null,
            &[vec![
                "verify".to_owned(),
                "--profile".to_owned(),
                "deep".to_owned(),
                "--report".to_owned(),
            ]],
            "verify_report.json",
            false,
            false,
            None,
        );
        let engine = saved.report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "binary_compat")
            .unwrap()
            .clone();
        (saved.codes[0], engine, saved.native)
    };
    for name in ["stable-two-artifacts", "readelf-tool-error"] {
        let frozen = &oracle["cases"][name];
        let (exit, engine, _) = run(name);
        assert_eq!(exit, frozen["exit_code"], "{name}: exit");
        for key in ["status", "evidence", "summary", "targets"] {
            assert_eq!(engine[key], frozen["report"][key], "{name}: {key}");
        }
    }

    let frozen = &oracle["cases"]["vanished"];
    assert_eq!(frozen["exit_code"], 1);
    assert_eq!(frozen["report"]["targets"][1]["file_path"], ".");
    let (exit, engine, native) = run("vanished");
    assert_eq!(exit, 3, "{}", native["gate"]);
    assert_eq!(engine["status"], "ERROR");
    assert_eq!(engine["evidence"], "NOT_RUN");
    let cause = ici_validation::VANISHED_CAUSE;
    let vanished = "build/ici-make/tool";
    let targets = engine["targets"].as_array().unwrap();
    assert_eq!(targets.len(), 2, "{targets:?}");
    assert_eq!(
        targets[0], frozen["report"]["targets"][0],
        "`app` is preserved"
    );
    assert_eq!(
        targets[1]["target_name"],
        "BinaryCompatibility:AnalysisUnavailable"
    );
    assert_eq!(targets[1]["status"], "ERROR");
    assert_eq!(targets[1]["file_path"], vanished);
    assert_eq!(targets[1]["message"], format!("{vanished}: {cause}"));
    assert_eq!(
        engine["summary"],
        format!("Binary compatibility analysis incomplete: {vanished}: {cause}")
    );
    assert_eq!(native["gate"]["selected"], "INCOMPLETE");
    assert_eq!(native["gate"]["reasons"], json!([INPUTS_CHANGED]));
}

/// The zero-C++-tests rebuild applies only to the state that capture left: a project with a C++
/// test, a schema 2 project and a declared build that fails keep the result they had, and none
/// reports frozen's `[C++] Tests` or a partial coverage source.
#[test]
#[ignore = "explicit actual pytest interpreter, as captured (ICI_TEST_PYTHON), and g++/gcov"]
fn zero_cpp_tests_rebuild_keeps_to_its_captured_state() {
    let oracle: Value = serde_json::from_str(HYBRID_TEST).unwrap();
    let measured = &oracle["cases"]["no-cpp-test"]["tree"];
    let with_test = &oracle["cases"]["cpp-test-without-main"]["tree"];
    let mut schema_two = measured.clone();
    schema_two["ici.toml"] = json!(format!(
        "schema_version=2\n[python]\nexecutable={}\n[[components]]\nid='project'\nroot='.'\n\
         languages=['python','cpp']\nsources=['src/**/*.py','src/**/*.cpp']\n",
        json!(project_python())
    ));
    let mut failing_build = measured.clone();
    failing_build["ici.toml"] = json!(format!(
        "{}[build.make]\nenabled=true\nbuild_argv=['make','all']\n\
         coverage_build_argv=['make','all']\n",
        measured["ici.toml"].as_str().unwrap()
    ));
    failing_build["Makefile"] = json!("all:\n\tfalse\n");
    // The failing build keeps the located `[C++] build` FAIL and its exit 1, as it had before.
    for (name, tree, exit) in [
        ("a C++ test", with_test, 1),
        ("schema 2", &schema_two, 3),
        ("a failing declared build", &failing_build, 1),
    ] {
        let home = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().join("project");
        write_tree(&base, tree);
        fs::create_dir_all(base.join(".git")).unwrap();
        let invocation = registry::parse(["ici", "test", "--report"]).unwrap();
        let output =
            commands::dispatch(&invocation, &base, &environment(home.path(), false)).unwrap();
        let report = fs::read_to_string(base.join("test_report.json")).unwrap_or_default();
        assert_eq!(output.exit_code, exit, "{name}: {}{report}", output.stdout);
        assert!(!report.contains("[C++] Tests"), "{name}: {report}");
        assert!(!report.contains("(partial)"), "{name}: {report}");
    }
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

/// The frozen `DuplicateEngine` audit (`capture_dup_audit.py`, `check:cpp.dup`): windowing,
/// normalization, Python character classes and line breaks, the fallback function regions and
/// directive segments, thresholds and mode, vendor/generated ownership and the frozen source walk,
/// every failure target, and the one scan over Python and C++. Each saved `ici dup --report`
/// equals frozen's in full; a native exit 3 needs a required NOT_RUN engine on both sides.
#[test]
fn cpp_duplicate_engine_audit_equals_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(DUP_AUDIT).unwrap();
    let mut differences = vec![];
    // ICI_TEST_CASE: optional comma-separated case names (development reruns).
    let filter = std::env::var("ICI_TEST_CASE").ok();
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if filter
            .as_deref()
            .is_some_and(|filter| !filter.split(',').any(|item| item == name))
        {
            continue;
        }
        let (exits, native) = replay(
            &case["tree"],
            &Value::Null,
            &[vec!["dup".into(), "--report".into()]],
            "dup_report.json",
            false,
        );
        assert_exit(
            name,
            &exits,
            &codes(&case["exit_code"]),
            std::slice::from_ref(&case["report"]),
            std::slice::from_ref(&native),
        );
        differences.extend(result_differences(
            &format!("dup-audit/{name}"),
            &case["report"],
            &native,
        ));
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    if filter.is_none() {
        assert_not_stale(|entry| entry.0 == "dup-audit");
    }
}

/// Every frozen `_compiler_result_failure` and `_located_unused_diagnostics` branch of the C++
/// unused-function probe (`capture_unused_probe_branches.py`): a fake `g++` beside the project
/// (`project-compiler`: inside it, which frozen refuses), with the version probes delegated to the
/// real compiler. Positive `control` and `non-tu-diagnostic`, negative `exit-code*`, failures
/// signal, truncated, unparseable, error with exit 0, timeout and the rejected outputs.
#[test]
#[ignore = "explicit actual /usr/bin/g++ unused-function probes (the captured g++ version)"]
fn cpp_unused_probe_branches_equal_frozen_except_allowed() {
    let oracle: Value = serde_json::from_str(UNUSED_PROBE).unwrap();
    let fake = oracle["fake"].as_str().unwrap();
    let mut differences = vec![];
    // ICI_TEST_CASE: optional comma-separated case names (development reruns).
    let filter = std::env::var("ICI_TEST_CASE").ok();
    for (name, case) in oracle["cases"].as_object().unwrap() {
        if filter
            .as_deref()
            .is_some_and(|filter| !filter.split(',').any(|item| item == name))
        {
            continue;
        }
        let home = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().join("project");
        write_tree(&base, &case["tree"]);
        fs::create_dir_all(base.join(".git")).unwrap();
        let tools = if name == "project-compiler" {
            base.join("bin")
        } else {
            directory.path().join("tools")
        };
        fs::create_dir_all(&tools).unwrap();
        {
            use std::os::unix::fs::PermissionsExt;
            fs::write(tools.join("g++"), fake).unwrap();
            // The fake `g++` and any driver the tree adds beside it (`../tools/gcc`).
            for entry in fs::read_dir(&tools).unwrap() {
                fs::set_permissions(entry.unwrap().path(), fs::Permissions::from_mode(0o755))
                    .unwrap();
            }
        }
        let mut environment = environment(home.path(), false);
        environment.insert("PATH".into(), format!("{}:/usr/bin:/bin", tools.display()));
        let argv = [
            "ici",
            "verify",
            "--profile",
            "deep",
            "--no-cache",
            "--report",
        ];
        let invocation = registry::parse(argv.iter().copied()).unwrap();
        // `budget_seconds`: frozen ran with `_GLOBAL_TIMEOUT_SECONDS` replaced (this project only).
        let budget = case["budget_seconds"]
            .as_f64()
            .map(std::time::Duration::from_secs_f64);
        ici_cli::cpp::set_legacy_unused_budget_for_tests(&base, budget);
        let output = commands::dispatch(&invocation, &base, &environment);
        ici_cli::cpp::set_legacy_unused_budget_for_tests(&base, None);
        let output = output.unwrap();
        // Markers of configurations that ran, beside the fake compiler.
        let mut listed: Vec<String> = fs::read_dir(&tools)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        listed.sort();
        if json!(listed) != case["tools"] {
            differences.push(format!(
                "unused-probe/{name}: tools native {listed:?} frozen {}",
                case["tools"]
            ));
        }
        let text = fs::read_to_string(base.join("verify_report.json")).unwrap();
        let native: Value = serde_json::from_str(
            &text
                .replace(tools.to_str().unwrap(), "<tools>")
                .replace(base.to_str().unwrap(), "<root>"),
        )
        .unwrap();
        let (frozen_engines, native_engines) = (results(&case["report"]), results(&native));
        // The dead evidence argv is native's own command (RCPP_TOOLS), but which configuration
        // ran, in which order, is not: each record's `-D` definitions equal frozen's.
        let definitions = |report: &Value| -> Vec<Vec<String>> {
            report["results"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|engine| engine["engine_name"] == "dead")
                .flat_map(|engine| {
                    engine["tool_evidence"]
                        .as_array()
                        .cloned()
                        .unwrap_or_default()
                })
                .map(|record| {
                    record["argv"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .filter(|argument| argument.starts_with("-D"))
                        .map(str::to_owned)
                        .collect()
                })
                .collect()
        };
        if definitions(&native) != definitions(&case["report"]) {
            differences.push(format!(
                "unused-probe/{name}: dead evidence definitions native {:?} frozen {:?}",
                definitions(&native),
                definitions(&case["report"])
            ));
        }
        // Every case's differences are reported together, an exit difference among them.
        if output.exit_code != case["exit_code"]
            && std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                assert_exit(
                    name,
                    &[output.exit_code],
                    &codes(&case["exit_code"]),
                    frozen_engines,
                    native_engines,
                )
            }))
            .is_err()
        {
            differences.push(format!(
                "unused-probe/{name}: exit native {} frozen {}",
                output.exit_code, case["exit_code"]
            ));
        }
        differences.extend(result_differences(
            &format!("unused-probe/{name}"),
            &case["report"],
            &native,
        ));
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    if filter.is_none() {
        assert_not_stale(|entry| entry.0 == "unused-probe");
    }
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
        "many-sources" => {
            for index in 0..2049 {
                tree.insert(
                    format!("src/s{index:04}.cpp"),
                    json!(format!("int s{index}() {{ return {index}; }}\n")),
                );
            }
            return Value::Object(tree);
        }
        "inventory-bytes" => {
            for index in 0..9 {
                tree.insert(
                    format!("src/big{index}.cpp"),
                    json!(format!("// {}\n", "x".repeat(15 * 512 * 1024))),
                );
            }
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
/// source, a NUL inside a body, the bounded lambda-body limit and frozen's 2,048-source and
/// 64 MiB inventory bounds (`many-sources`, `inventory-bytes`).
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
