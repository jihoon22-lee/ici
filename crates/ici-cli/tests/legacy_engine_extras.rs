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
//!   project whose test interpreter lacks coverage (explicit `ICI_TEST_PYTHON_NEW`).
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
//! silently accepted); a native-only key must be a reviewed native extra; a reviewed or open
//! entry that no replay observes is stale and fails.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

#[path = "support/native_extras.rs"]
mod native_extras;
use native_extras::{NATIVE_COUNTERS, NATIVE_EXTRAS, NATIVE_STEPS};

const SUMMARY: &str =
    include_str!("../../../tests/rust-parity/reporting/legacy-summary-oracle.json");
const ANNOTATIONS: &str =
    include_str!("../../../tests/rust-parity/reporting/legacy-annotations-oracle.json");
const INVENTORY: &str =
    include_str!("../../../tests/rust-parity/python/legacy-inventory-oracle.json");
const EXTRAS: &str = include_str!("../../../tests/rust-parity/reporting/legacy-extras-oracle.json");

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
    Open,
}
use Class::{Approved, Incidental, Open};

/// (capture or exact replay label, normalized path, kind, class, reason).
type Allowance = (&'static str, &'static str, Kind, Class, &'static str);

include!("support/legacy_result_allowances.rs");

/// Native-only C++ check evidence kept under native names beside the frozen-named projection
/// (include graph and its trace, boundary counters, compile context, exact dead-code details).
const CPP_NATIVE: [&str; 21] = [
    "boundary_mode",
    "configuration_graphs",
    "configurations_checked",
    "context",
    "cycles",
    "cycles_reported",
    "cycles_truncated",
    "estimated_boundaries",
    "evidence",
    "exact_boundaries",
    "finding_details",
    "graph",
    "include_diagnostics",
    "include_diagnostics_truncated",
    "lambdas_excluded",
    "mode",
    "non_tu_diagnostics_excluded",
    "resolution",
    "resolved_edges",
    "scope_counts",
    "sources_checked",
];

thread_local! {
    static HITS: RefCell<BTreeSet<usize>> = const { RefCell::new(BTreeSet::new()) };
    static NATIVE_ONLY: RefCell<BTreeSet<String>> = const { RefCell::new(BTreeSet::new()) };
}

/// The project interpreter the Python captures ran under (Python 3.10.21 with pytest, coverage
/// and mypy): `ICI_TEST_PYTHON`, else the workspace `.venv` CI creates, without `..` segments.
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

/// Run the capture's steps in a fresh `project` (with `.git`), applying `edit` before the last;
/// returns the exit codes and the saved report of the last step, project root as `<root>`.
fn replay(
    tree: &Value,
    edit: &Value,
    steps: &[Vec<String>],
    report: &str,
    actions: bool,
) -> (Vec<i32>, Value) {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().join("project");
    write_tree(&base, tree);
    fs::create_dir_all(base.join(".git")).unwrap();
    let environment = environment(home.path(), actions);
    let mut codes = vec![];
    for (index, argv) in steps.iter().enumerate() {
        if index + 1 == steps.len() && edit.is_object() {
            write_tree(&base, edit);
        }
        let invocation =
            registry::parse(std::iter::once("ici").chain(argv.iter().map(String::as_str))).unwrap();
        codes.push(
            commands::dispatch(&invocation, &base, &environment)
                .unwrap()
                .exit_code,
        );
    }
    let text = fs::read_to_string(base.join(report)).unwrap();
    let report = serde_json::from_str(&text.replace(&base.display().to_string(), "<root>"));
    (codes, report.unwrap())
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

fn native_extra(key: &str) -> bool {
    NATIVE_EXTRAS.contains(&key)
        || NATIVE_COUNTERS.contains(&key)
        || NATIVE_STEPS.contains(&key)
        || CPP_NATIVE.contains(&key)
}

/// Positions are normalized (`[3]` -> `[]`) so an allowance names one path of every element.
fn walk(frozen: &Value, native: &Value, path: &str, out: &mut Vec<(String, Kind)>) {
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
                let extra = path.ends_with(".extra") && native_extra(key);
                if extra {
                    // The shared reviewed native extras (support/native_extras.rs).
                    NATIVE_ONLY.with(|seen| seen.borrow_mut().insert(key.clone()));
                } else {
                    out.push((format!("{path}.{key}"), NativeOnly));
                }
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
            if left.as_f64() != right.as_f64() {
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
    let (exits, native) = replay(&tree, &Value::Null, &verify(), "verify_report.json", false);
    let label = format!("extras/{name}");
    let frozen = &case["suite"];
    assert_exit(
        &label,
        &exits,
        &codes(&case["exit_code"]),
        results(frozen),
        results(&native),
    );
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
        let (exits, native) = replay(
            &case["tree"],
            &case["edit"],
            &steps(case),
            "verify_report.json",
            false,
        );
        let label = format!("summary/{name}");
        assert_exit(
            &label,
            &exits,
            &codes(&case["exit_codes"]),
            results(&case["suite"]),
            results(&native),
        );
        differences.extend(result_differences(&label, &case["suite"], &native));
    }
    let annotations: Value = serde_json::from_str(ANNOTATIONS).unwrap();
    let mut argv = verify();
    argv[0].push("--github-summary".into());
    let (exits, native) = replay(
        &annotations["tree"],
        &Value::Null,
        &argv,
        "verify_report.json",
        true,
    );
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
    assert!(differences.is_empty(), "{}", differences.join("\n"));
    assert_not_stale(|entry| {
        matches!(entry.0, "summary" | "annotations" | "inventory")
            || entry.0.starts_with("summary/")
            || (entry.0.starts_with("inventory/") && entry.0 != "inventory/hybrid/lint")
    });
    // Every shared Python native extra is observed by these replays (a stale entry fails).
    NATIVE_ONLY.with(|seen| {
        let seen = seen.borrow();
        for key in NATIVE_EXTRAS.iter().chain(NATIVE_COUNTERS.iter()) {
            assert!(seen.contains(*key), "unobserved native extra {key}");
        }
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
    for name in ["cpp-defaults", "cpp-deep", "cpp-database", "hybrid"] {
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
            "probe/case: .extra.rows (Len)",
            "probe/case: .score (Type)",
            "probe/case: .status (Value)",
            "probe/case: .summary (Missing)",
            "probe/case: .targets (Type)",
        ]
    );
}
