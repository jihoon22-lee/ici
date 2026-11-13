//! Frozen legacy `TestEngine._run_project_tests` (engines/test.py:249-276) attempts the Python
//! suite for `tests/**/test_*.py` whatever the project type (or a python/hybrid type with a
//! selected Python source) and, decided separately, the C++ suite for `tests/**/*.cpp` or a
//! cpp/hybrid type with a selected C/C++ source; an attempted suite without tests is its
//! "[<language>] Tests" FAIL, neither attempted is "[Project] Tests". Native used to plan the root
//! component's test checks from its languages: a `type = "cpp"` project whose only Python is
//! `tests/test_*.py` ended SKIP NOT_RUN "C++ runtime checks require a declared component build",
//! exit 3, where frozen runs pytest.
//!
//! Planning (always run): which of `python.test`/`python.coverage`/`cpp.test`/`cpp.coverage` the
//! scope puts in play, under `ici test` and `ici verify`, with the next command, a schema 2 project,
//! the disabled engine and the fast profile as negatives/omissions. Replay (explicit project CPython
//! with pytest and coverage, g++ on /usr/bin): `legacy-test-scope-oracle.json`
//! (`capture_legacy_test_scope.py`, frozen v0.13.0) compared in full except the standing rulings:
//! native never invents frozen's estimated coverage/TEM (decisions.md "legacy coverage.py의
//! --source", R9; the no-tests summary of `legacy_no_tests.rs`), a test case's location is its
//! definition (`legacy_test_parity.rs`), native's pytest bookkeeping arguments and C++ direct-build
//! steps. `verify` compares the engine set, the test and sanitize verdicts and the exit; frozen 1
//! is native 3 only where the sanitize engine is required ERROR/NOT_RUN on both sides (decisions.md
//! "legacy 종료 코드 3의 범위").
use ici_cli::{planning, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-test-scope-oracle.json");
const TEST: &str = "def test_add():\n    assert 1 + 2 == 3\n";
const CPP: &str = "int twice(int value) { return value * 2; }\n";

fn legacy_config(kind: &str, extra: &str, profile: &str) -> String {
    format!(
        "[ici]\nprofile = \"{profile}\"\n[project]\ntype = '{kind}'\nsource_dirs = [\"src\"]\n[engines.test]\n{extra}"
    )
}

struct Planned {
    /// (check id, blocked) of every planned test-descriptor check.
    checks: Vec<(String, bool)>,
    /// (check id, reason) of every omitted test-descriptor check.
    omitted: Vec<(String, String)>,
    languages: Vec<Vec<String>>,
}

fn descriptor(id: &str) -> bool {
    matches!(
        id,
        "python.test" | "python.coverage" | "cpp.test" | "cpp.coverage"
    )
}

fn plan(files: &[(&str, &str)], config: &str, argv: &[&str]) -> Planned {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir(dir.path().join(".hg")).unwrap();
    for (path, text) in files {
        if let Some(directory) = path.strip_suffix('/') {
            fs::create_dir_all(dir.path().join(directory)).unwrap();
            continue;
        }
        let path = dir.path().join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    fs::write(dir.path().join("ici.toml"), config).unwrap();
    let prepared = planning::prepare(
        &registry::parse(argv.iter().copied()).unwrap(),
        dir.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
        false,
    )
    .unwrap_or_else(|error| panic!("{argv:?} {files:?}: {error:?}"));
    let mut checks: Vec<_> = prepared
        .plan
        .checks
        .iter()
        .filter(|check| descriptor(&check.check.id))
        .map(|check| {
            (
                check.check.id.clone(),
                matches!(check.work, ici_application::plan::Work::Blocked { .. }),
            )
        })
        .collect();
    checks.sort();
    let mut omitted: Vec<_> = prepared
        .plan
        .omitted
        .iter()
        .filter(|omitted| descriptor(&omitted.check_id))
        .map(|omitted| (omitted.check_id.clone(), omitted.reason.clone()))
        .collect();
    omitted.sort();
    Planned {
        checks,
        omitted,
        languages: prepared
            .project
            .components
            .iter()
            .map(|component| component.languages.clone())
            .collect(),
    }
}

fn owned(rows: &[(&str, bool)]) -> Vec<(String, bool)> {
    rows.iter()
        .map(|(id, blocked)| ((*id).to_owned(), *blocked))
        .collect()
}

const PYTHON_ONLY: &[(&str, bool)] = &[("python.coverage", false), ("python.test", false)];
/// Neither suite attempted: the C++ checks carry frozen's "[Project] Tests" (no runnable suite).
const NO_SUITE: &[(&str, bool)] = &[("cpp.coverage", true), ("cpp.test", true)];

#[test]
fn tests_python_alone_plans_the_python_suite_without_a_cpp_runtime_check() {
    for tests in ["tests/test_calc.py", "tests/a/b/test_x.py"] {
        for kind in ["cpp", "python", "hybrid"] {
            for (argv, profile) in [
                (&["ici", "test"][..], "deep"),
                (&["ici", "verify"][..], "deep"),
                (&["ici", "verify"][..], "standard"),
            ] {
                let planned = plan(
                    &[(tests, TEST), ("tools/helper.py", "X = 1\n")],
                    &legacy_config(kind, "", profile),
                    argv,
                );
                assert_eq!(
                    planned.checks,
                    owned(PYTHON_ONLY),
                    "{tests} {kind} {argv:?} {profile}"
                );
            }
        }
    }
    // The component's languages are not widened.
    let planned = plan(
        &[("tests/test_calc.py", TEST)],
        &legacy_config("cpp", "", "deep"),
        &["ici", "test"],
    );
    assert_eq!(planned.languages, [["cpp"]]);
}

#[test]
fn a_cpp_scope_keeps_its_checks_beside_or_without_the_python_suite() {
    // A C/C++ source beside tests/test_*.py: both suites; the C++ one has no tests, so its checks
    // are the blocked no-runnable-suite carrier of frozen's "[C++] Tests".
    let planned = plan(
        &[("src/twice.cpp", CPP), ("tests/test_calc.py", TEST)],
        &legacy_config("cpp", "", "deep"),
        &["ici", "test"],
    );
    assert_eq!(
        planned.checks,
        owned(&[
            ("cpp.coverage", true),
            ("cpp.test", true),
            ("python.coverage", false),
            ("python.test", false)
        ])
    );
    // tests/*.cpp of a cpp project whose selected sources are Python only: the C++ suite is in
    // play (planned as the legacy direct-build reading; `legacy_test_follows_the_frozen_suite_scope`
    // runs it), the Python one is not.
    let planned = plan(
        &[
            ("src/calc.py", "X = 1\n"),
            ("tests/test_main.cpp", "int main() { return 0; }\n"),
        ],
        &legacy_config("cpp", "", "deep"),
        &["ici", "test"],
    );
    let ids: Vec<&str> = planned.checks.iter().map(|(id, _)| id.as_str()).collect();
    assert_eq!(ids, ["cpp.coverage", "cpp.test"]);
    assert_eq!(planned.languages, [["python"]]);
}

#[test]
fn neither_suite_attempted_keeps_only_the_no_tests_carrier() {
    // A helper, an empty tests/, Python outside tests/, and a cpp project's selected Python
    // sources (with or without a tests path): frozen "[Project] Tests", no Python suite.
    for files in [
        &[("tests/helper.py", "X = 1\n")][..],
        &[("tests/", "")][..],
        &[("tools/helper.py", "X = 1\n")][..],
        &[("src/calc.py", "X = 1\n")][..],
        &[("src/calc.py", "X = 1\n"), ("tests/data.txt", "x\n")][..],
    ] {
        for argv in [&["ici", "test"][..], &["ici", "verify"][..]] {
            let planned = plan(files, &legacy_config("cpp", "", "deep"), argv);
            assert_eq!(planned.checks, owned(NO_SUITE), "{files:?} {argv:?}");
        }
    }
}

#[test]
fn the_next_path_schema_two_disabled_engine_and_fast_profile_are_unchanged() {
    let only_tests = [("tests/test_calc.py", TEST)];
    // The next command keeps the declared scope: no Python suite for a cpp component.
    let planned = plan(
        &only_tests,
        &legacy_config("cpp", "", "deep"),
        &["ici", "next", "verify"],
    );
    assert!(
        planned.checks.iter().all(|(id, _)| id.starts_with("cpp.")),
        "{:?}",
        planned.checks
    );
    // A schema 2 project declares its languages; the historical command keeps them.
    let planned = plan(
        &[("src/a.cpp", "int a;\n"), ("tests/test_calc.py", TEST)],
        "schema_version=2\n[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['src/a.cpp']\n",
        &["ici", "test"],
    );
    assert!(
        planned.checks.iter().all(|(id, _)| id.starts_with("cpp.")),
        "{:?}",
        planned.checks
    );
    // `verify` omits the disabled engine and the fast profile's test descriptor, like frozen.
    let disabled = plan(
        &only_tests,
        &legacy_config("cpp", "enabled = false\n", "deep"),
        &["ici", "verify"],
    );
    assert_eq!(disabled.checks, []);
    assert_eq!(
        disabled.omitted,
        [
            ("python.coverage".to_owned(), "disabled".to_owned()),
            ("python.test".to_owned(), "disabled".to_owned())
        ]
    );
    let fast = plan(
        &only_tests,
        &legacy_config("cpp", "", "fast"),
        &["ici", "verify"],
    );
    assert_eq!(fast.checks, []);
    assert_eq!(
        fast.omitted,
        [
            (
                "python.coverage".to_owned(),
                "not in profile 'fast'".to_owned()
            ),
            ("python.test".to_owned(), "not in profile 'fast'".to_owned())
        ]
    );
    // A typed `ici test` selects its engine on the disabled project and under `fast`.
    for config in [
        legacy_config("cpp", "enabled = false\n", "deep"),
        legacy_config("cpp", "", "fast"),
    ] {
        let planned = plan(&only_tests, &config, &["ici", "test"]);
        assert_eq!(planned.checks, owned(PYTHON_ONLY), "{config}");
    }
}

/// Frozen `capture_legacy_test_scope.config`.
fn config(python: &str, frozen: &Value) -> String {
    if frozen["type"] == "next" {
        return "schema_version = 1\n[workspace]\nname = \"demo\"\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\n".into();
    }
    format!(
        "[ici]\nprofile = \"{}\"\n[project]\ntype = '{}'\nsource_dirs = [\"src\"]\n[engines.test]\npython = {}\n{}",
        frozen["profile"].as_str().unwrap(),
        frozen["type"].as_str().unwrap(),
        serde_json::to_string(python).unwrap(),
        frozen["extra_config"].as_str().unwrap()
    )
}

struct Run {
    exit: i32,
    /// `verify`: every engine's [name, status, evidence, required], sorted.
    engines: Option<Vec<Value>>,
    engine: Option<Value>,
}

/// The capture's interpreter with pytest and without a coverage module (the oracle records its
/// path, `<PYTHON_NO_COVERAGE>`); the host must still have it in that state.
fn no_coverage_python() -> String {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let python = oracle["no_coverage_python"].as_str().unwrap().to_owned();
    let probe = Command::new(&python)
        .args([
            "-I",
            "-c",
            "import pytest, importlib.util as u; raise SystemExit(u.find_spec('coverage') is not None)",
        ])
        .status()
        .unwrap_or_else(|error| panic!("{python}: {error}"));
    assert!(
        probe.success(),
        "{python} must import pytest and have no coverage module"
    );
    python
}

/// The native limitation an ICI_PYTHON setting adds (the legacy launcher rule's own note,
/// ici-config resolve.rs).
const PINNED_INTERPRETER_NOTE: &str = "ICI_PYTHON selects the historical pyz interpreter and is \
    not a Rust runtime setting; declare project python.executable when needed";

/// The frozen modules `verify` probes from its own `sys.executable` (core/toolchain.py:440-449;
/// no tree has a project .venv): the capability_inventory rows the capture recorded.
const CAPTURE_MODULES: [&str; 3] = ["mypy", "pytest", "coverage"];

/// The replay pin of `verify` (decisions.md "legacy capability inventory의 Python 모듈 도구"):
/// ICI_PYTHON is an explicit pin of a replay input that depends on frozen's `sys.executable`
/// (the interpreter frozen's capability inventory probes mypy/pytest/coverage from), so it must be
/// the runtime that ran frozen and nothing else. `ICI_TEST_PYTHON` must be the capture's recorded
/// runtime (`capture_python`); every `verify` view's frozen mypy/pytest/coverage row names it
/// (`<PYTHON>`) with one value per module, available and complete; and it must still import each
/// module, from an empty directory with PYTHONSAFEPATH as the native probe runs it, at the
/// captured version. The no-coverage trees keep that inventory (frozen probes its own runtime);
/// their test engine's interpreter is their own `[engines.test] python` (`compare`). Returns it.
fn assert_capture_runtime(oracle: &Value) -> String {
    let python =
        std::env::var("ICI_TEST_PYTHON").expect("the frozen capture runtime (ICI_TEST_PYTHON)");
    assert_eq!(
        oracle["capture_python"].as_str(),
        Some(python.as_str()),
        "ICI_TEST_PYTHON is not the frozen capture runtime"
    );
    let views: Vec<(&String, &Value)> = oracle["verify"]
        .as_object()
        .unwrap()
        .iter()
        .chain(
            oracle["open"]
                .as_object()
                .unwrap()
                .iter()
                .filter(|(name, _)| name.starts_with("verify:")),
        )
        .filter(|(_, view)| view["engines"].is_array())
        .collect();
    assert!(!views.is_empty(), "no frozen verify view with a report");
    let neutral = tempfile::tempdir().unwrap();
    for module in CAPTURE_MODULES {
        let mut rows = std::collections::BTreeSet::new();
        for (name, view) in &views {
            let found: Vec<&Value> = view["capability_inventory"]["tools"]
                .as_array()
                .unwrap_or_else(|| panic!("{name}: no frozen capability_inventory rows"))
                .iter()
                .filter(|row| row["name"] == module)
                .collect();
            assert_eq!(found.len(), 1, "{name}: one frozen {module} row expected");
            rows.insert(found[0].to_string());
        }
        assert_eq!(
            rows.len(),
            1,
            "{module}: one frozen row value expected, {rows:?}"
        );
        let row: Value = serde_json::from_str(rows.first().unwrap()).unwrap();
        assert_eq!(
            row["path"], "<PYTHON>",
            "{module}: frozen probed another interpreter"
        );
        assert!(
            row["available"] == true && row["complete"] == true,
            "{module}: the capture saw it unavailable"
        );
        let output = Command::new(&python)
            .args(["-m", module, "--version"])
            .current_dir(neutral.path())
            .env("PYTHONSAFEPATH", "1")
            .output()
            .unwrap();
        assert!(output.status.success(), "{module}: {output:?}");
        assert_eq!(
            String::from_utf8(output.stdout).unwrap().lines().next(),
            row["version"].as_str(),
            "{module}: the capture runtime changed"
        );
    }
    python
}

/// Frozen saved a report for this case: a `verify` view records its engines whether or not the
/// test engine is among them (the disabled engine, `fast`); a next project saves none.
fn frozen_saved_report(frozen: &Value, command: &str) -> bool {
    if command == "verify" {
        frozen["engines"].is_array()
    } else {
        frozen["report"] == true
    }
}

/// The pin's own trace, on the FULL saved report before any view is extracted (the view keeps
/// no `native_run`): the ICI_PYTHON note is the native run's limitation exactly once when the
/// replay set ICI_PYTHON and appears nowhere when it did not. The count is over the whole report
/// text, so no other copy can hide outside the extracted view, and the run's own
/// `/native_run/limitations` must exist, so the count is never taken over nothing.
fn assert_pinned_interpreter_note(name: &str, text: &str, report: &Value, pinned: bool) {
    let total = text.matches(PINNED_INTERPRETER_NOTE).count();
    assert_eq!(
        total,
        usize::from(pinned),
        "{name}: the ICI_PYTHON note appears {total} times in the report (pinned: {pinned})"
    );
    let limitations = report
        .pointer("/native_run/limitations")
        .and_then(Value::as_array)
        .unwrap_or_else(|| panic!("{name}: no native run with limitations"));
    assert_eq!(
        limitations
            .iter()
            .filter(|item| *item == PINNED_INTERPRETER_NOTE)
            .count(),
        usize::from(pinned),
        "{name}: /native_run/limitations"
    );
}

fn run(label: &str, frozen: &Value, command: &str, pin: Option<&str>) -> Run {
    // The case's own project interpreter (`[engines.test] python`): the capture's `--python`, or
    // its interpreter without coverage. Not the replay pin.
    let capture_python = std::env::var("ICI_TEST_PYTHON")
        .expect("explicit ICI_TEST_PYTHON (project CPython with pytest and coverage)");
    let no_coverage = (frozen["interpreter"] == "no-coverage").then(no_coverage_python);
    let python = no_coverage
        .clone()
        .unwrap_or_else(|| capture_python.clone());
    let work = tempfile::tempdir().unwrap();
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let root = work.path().join("project");
    fs::create_dir_all(&root).unwrap();
    for (name, text) in frozen["files"].as_object().unwrap() {
        match text.as_str() {
            Some(text) => {
                fs::create_dir_all(root.join(name).parent().unwrap()).unwrap();
                fs::write(root.join(name), text).unwrap();
            }
            None => fs::create_dir_all(root.join(name)).unwrap(),
        }
    }
    let root = root.canonicalize().unwrap();
    fs::write(root.join("ici.toml"), config(&python, frozen)).unwrap();
    // The capture's `gcov` stub first on PATH (section `adapter_gcov_failure`), as captured.
    let mut path = "/usr/bin:/bin".to_owned();
    if let Some(stub) = frozen["gcov_stub"].as_str() {
        use std::os::unix::fs::PermissionsExt;
        let bin = work.path().join("bin");
        fs::create_dir_all(&bin).unwrap();
        fs::write(bin.join("gcov"), stub).unwrap();
        fs::set_permissions(bin.join("gcov"), fs::Permissions::from_mode(0o755)).unwrap();
        path = format!("{}:{path}", bin.display());
    }
    let mut ici = Command::new(env!("CARGO_BIN_EXE_ici"));
    ici.args([command, "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &home)
        .env("PATH", &path);
    if frozen["type"] == "next" {
        // A `[workspace]` `verify` is the native cutover, not a stable command: it passes the
        // caller's locale, so the frozen interpreter's coerced LC_CTYPE is given explicitly. The
        // stable commands coerce it themselves (root ruling 2026-11-14 (b6(b))).
        ici.env("LC_CTYPE", "C.UTF-8");
    }
    if let Some(pin) = pin {
        // Explicit pin of a replay input that depends on frozen's `sys.executable`: frozen
        // `verify` probes mypy, pytest and coverage as modules of its own interpreter, the
        // capture runtime (`assert_capture_runtime`); native has no interpreter of its own and is
        // given that one through ICI_PYTHON (decisions.md "legacy capability inventory의 Python
        // 모듈 도구", replay pin (A)/(A')). The case's own `[engines.test] python` is untouched.
        ici.env("ICI_PYTHON", pin);
    }
    let output = ici.output().unwrap();
    let name = format!("{command} {label}");
    let saved = fs::read_to_string(root.join(format!("{command}_report.json"))).ok();
    if frozen_saved_report(frozen, command) {
        let text = saved
            .as_deref()
            .unwrap_or_else(|| panic!("{name}: frozen saved a report, native none"));
        assert_pinned_interpreter_note(
            &name,
            text,
            &serde_json::from_str(text).unwrap(),
            pin.is_some(),
        );
    }
    // Otherwise (a next project, refused by both) frozen saved no report: no native run is
    // expected and the note is not counted; `compare` asserts there is no test engine.
    let report = saved.map(|text| {
        let text = text.replace(&root.display().to_string(), "<ROOT>");
        let text = match &no_coverage {
            Some(path) => text.replace(path.as_str(), "<PYTHON_NO_COVERAGE>"),
            None => text,
        };
        serde_json::from_str::<Value>(&text.replace(&capture_python, "<PYTHON>")).unwrap()
    });
    let (engines, engine) = match report {
        Some(report) if command == "verify" => match report["results"].as_array() {
            Some(results) => {
                let mut names: Vec<Value> = results
                    .iter()
                    .map(|item| {
                        json!([
                            item["engine_name"],
                            item["status"],
                            item["evidence"],
                            item["required"]
                        ])
                    })
                    .collect();
                names.sort_by_key(|item| item.to_string());
                (
                    Some(names),
                    results
                        .iter()
                        .find(|item| item["engine_name"] == "test")
                        .cloned(),
                )
            }
            None => (None, None),
        },
        other => (None, other),
    };
    Run {
        exit: output.status.code().unwrap(),
        engines,
        engine,
    }
}

/// Frozen's estimated coverage and TEM; native records none (decisions.md R9).
const ESTIMATES: [&str; 5] = [
    "branch_coverage",
    "function_coverage",
    "tem_score",
    "coverage_source",
    "metrics_summary",
];

/// Remove `expected` (`None` matches any one token), which must sit right after the first `anchor`.
fn take_after(argv: &mut Vec<String>, anchor: &[&str], expected: &[Option<&str>]) {
    let at = argv
        .windows(anchor.len())
        .position(|window| window == anchor)
        .unwrap_or_else(|| panic!("no {anchor:?} in {argv:?}"))
        + anchor.len();
    let found = argv.get(at..at + expected.len()).unwrap_or_default();
    assert!(
        found.len() == expected.len()
            && found
                .iter()
                .zip(expected)
                .all(|(token, wanted)| wanted.is_none_or(|wanted| token == wanted)),
        "expected {expected:?} right after {anchor:?} in {argv:?}"
    );
    argv.drain(at..at + expected.len());
}

/// A coverage/pytest record's argv in meaning form, after each side's approved difference
/// (decisions.md "legacy Python 도구 argv"; `legacy_python_tool_argv.rs` `compare_record`): native
/// adds its `--data-file`, cache and output options at fixed places and names the target from the
/// project root; frozen's `-v` follows `-o addopts=`; the json report location is either side's own.
fn invocation(name: &str, argv: &Value, native: bool) -> Vec<String> {
    let mut argv: Vec<String> = argv
        .as_array()
        .unwrap()
        .iter()
        .map(|token| token.as_str().unwrap().replace("<ROOT>/", ""))
        .collect();
    match (name, native) {
        ("coverage pytest", true) => {
            take_after(&mut argv, &["--branch"], &[Some("--data-file"), None]);
            take_after(
                &mut argv,
                &["-m", "pytest"],
                &[
                    Some("-p"),
                    Some("no:cacheprovider"),
                    Some("-v"),
                    Some("--tb=short"),
                    Some("--color=no"),
                ],
            );
        }
        ("coverage pytest", false) | ("pytest", false) => {
            take_after(&mut argv, &["addopts="], &[Some("-v")]);
        }
        // The plain suite of an interpreter without coverage: the same output contract.
        ("pytest", true) => take_after(
            &mut argv,
            &["-m", "pytest"],
            &[
                Some("-p"),
                Some("no:cacheprovider"),
                Some("-v"),
                Some("--tb=short"),
                Some("--color=no"),
            ],
        ),
        ("coverage json", _) => {
            if native {
                take_after(&mut argv, &["json"], &[Some("--data-file"), None]);
            }
            take_after(&mut argv, &["-o"], &[None]);
        }
        _ => {}
    }
    argv
}

fn cxx_step(name: &str) -> bool {
    name.starts_with("g++") || name.starts_with("gcov") || name == "C++ test"
}

/// The Python tool steps (name, return code, invocation) and the C++ tests' return codes; the
/// C++ build steps are native's actual executions (decisions.md "C++ 도구 증거는 실제 실행 기록").
fn tools(evidence: &Value, native: bool) -> (Vec<Value>, Vec<Value>) {
    let items = evidence.as_array().unwrap();
    let python = items
        .iter()
        .filter(|item| !cxx_step(item["name"].as_str().unwrap()))
        .map(|item| {
            let name = item["name"].as_str().unwrap();
            json!([
                name,
                item["returncode"],
                invocation(name, &item["argv"], native)
            ])
        })
        .collect();
    let cxx = items
        .iter()
        .filter(|item| item["name"] == "C++ test")
        .map(|item| item["returncode"].clone())
        .collect();
    (python, cxx)
}

fn targets(targets: &Value) -> Vec<Value> {
    targets
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            let case = target["target_name"]
                .as_str()
                .unwrap()
                .starts_with("tests/");
            json!([
                target["file_path"],
                if case {
                    Value::Null
                } else {
                    target["start_line"].clone()
                },
                target["target_name"],
                target["status"],
                target["message"]
            ])
        })
        .collect()
}

fn compare(name: &str, frozen: &Value, actual: &Run) {
    if frozen["report"] == false {
        assert!(actual.engine.is_none(), "{name}: no test engine");
        return;
    }
    let native = actual
        .engine
        .as_ref()
        .unwrap_or_else(|| panic!("{name}: no test engine"));
    for key in ["status", "evidence", "required"] {
        assert_eq!(native[key], frozen["engine"][key], "{name}: {key}");
    }
    let mut findings: Vec<Value> = native["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            json!({"rule_id": finding["rule_id"], "path": finding["primary_location"]["path"],
                "message": finding["message"], "fingerprint": finding["fingerprint"],
                "severity": finding["severity"]})
        })
        .collect();
    findings.sort_by(|a, b| a["fingerprint"].as_str().cmp(&b["fingerprint"].as_str()));
    assert_eq!(
        Value::Array(findings),
        frozen["findings"],
        "{name}: findings"
    );
    assert_eq!(
        targets(&native["targets"]),
        targets(&frozen["targets"]),
        "{name}: targets"
    );
    assert_eq!(
        tools(&native["tool_evidence"], true),
        tools(&frozen["tool_evidence"], false),
        "{name}: tool evidence"
    );
    if frozen["interpreter"] == "no-coverage" {
        // The test engine really runs the case's interpreter without coverage, whatever
        // interpreter the `verify` inventory was pinned to.
        let (python, _) = tools(&native["tool_evidence"], true);
        assert!(!python.is_empty(), "{name}: no Python tool step");
        for step in &python {
            assert_eq!(step[2][0], "<PYTHON_NO_COVERAGE>", "{name}: {step}");
        }
    }
    let extra = &frozen["extra"];
    for (key, value) in extra.as_object().unwrap() {
        if key == "coverage_provenance" && value["cpp"]["format"] == "gcov-json" {
            assert_eq!(
                without_gcov_tally(&native["extra"][key], name),
                without_gcov_tally(value, name),
                "{name}: extra.{key}"
            );
        } else if !ESTIMATES.contains(&key.as_str()) || extra["coverage_source"] != "estimated" {
            assert_eq!(&native["extra"][key], value, "{name}: extra.{key}");
        }
    }
    if extra["coverage_source"] != "estimated" {
        assert_eq!(native["summary"], frozen["engine"]["summary"], "{name}");
        assert_eq!(native["score"], frozen["engine"]["score"], "{name}: score");
        return;
    }
    // Frozen's estimate (R9): native carries none of it, and says what it did not measure.
    for key in ESTIMATES {
        assert!(native["extra"][key].is_null(), "{name}: extra.{key}");
    }
    assert!(native["score"].is_null(), "{name}: score");
    let summary = native["summary"].as_str().unwrap();
    // No case ran at all (a "[<language>] Tests" FAIL beside measured cases is not this).
    let zero = extra["total_tests"] == 0
        && frozen["targets"]
            .as_array()
            .unwrap()
            .iter()
            .any(|target| target["message"] == "No tests collected");
    if frozen["engine"]["status"] == "ERROR" {
        assert_eq!(summary, frozen["engine"]["summary"], "{name}");
    } else if zero {
        assert_eq!(summary, "0/0 Tests Passed; No tests collected", "{name}");
    } else {
        let counts = format!(
            "{}/{} Tests Passed; coverage not measured (optional): ",
            extra["passed_tests"], extra["total_tests"]
        );
        assert!(summary.starts_with(&counts), "{name}: {summary}");
    }
}

/// decisions.md "legacy C++ coverage 집계 counter" (approved): `coverage_provenance.cpp`'s report
/// and record counters follow the gcov reports each side read; every other provenance field
/// (format, versions, expected/covered/matched sources, mapping) is compared. Each counter must
/// be present.
const GCOV_TALLY: [&str; 9] = [
    "call_records",
    "compressed_bytes",
    "decompressed_bytes",
    "file_records",
    "function_records",
    "ignored_file_records",
    "line_records",
    "lines_without_block_ids",
    "report_count",
];

fn without_gcov_tally(provenance: &Value, name: &str) -> Value {
    let mut provenance = provenance.clone();
    let cpp = provenance["cpp"]
        .as_object_mut()
        .unwrap_or_else(|| panic!("{name}: no gcov provenance"));
    for key in GCOV_TALLY {
        assert!(
            cpp.remove(key).is_some_and(|value| value.is_u64()),
            "{name}: coverage_provenance.cpp.{key}"
        );
    }
    provenance
}

/// Each scenario's frozen outcome as literals, so a recapture that drifts fails here too:
/// (scenario, exit, status, target names in order).
type Expected = (&'static str, i64, &'static str, &'static [&'static str]);

/// `verify` trees whose every engine (name, status, evidence, required) is compared with frozen's:
/// the hybrid trees without a selected source and the hybrid src+tests tree, whose exits came
/// from other engines than the test engine (a dup panic, lint and mypy).
const ALL_ENGINES: [&str; 3] = [
    "hybrid-tests-python",
    "hybrid-tests-empty",
    "hybrid-src-python-tests-python",
];

/// (section, scenario, native exit, reason) where native's exit is a recorded class other than
/// frozen's.
const NATIVE_EXIT: [(&str, &str, i32, &str); 2] = [
    // decisions.md "legacy 종료 코드 3의 범위" (`covjson-exit7-required`): under
    // `coverage_required` the failed `coverage json` is the required check's missing evidence,
    // so the native gate is INCOMPLETE (3) where frozen exits 1. Status, evidence, summary and
    // targets are frozen's.
    (
        "test",
        "cpp-src-cpp-tests-python-required",
        3,
        "coverage_required tool error",
    ),
    (
        "verify",
        "cpp-src-cpp-tests-python-required",
        3,
        "coverage_required tool error",
    ),
    // Root ruling 2026-11-14 (cppval19): the `open` pooled trees under `coverage_required`
    // (`verify:<type>-...-required`, the cpp.coverage check blocked for no production C/C++
    // source) exit 1 under `verify` as under `ici test`, frozen's exit; they are not listed here.
];

fn replay(section: &str, expected: &[Expected]) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    // `ici test` runs no capability inventory and is replayed unpinned; the `open` section holds
    // both commands (`<command>:<tree>`).
    let command = |name: &str| -> String {
        match name.split_once(':') {
            Some((command, _))
                if matches!(section, "open" | "open_adapter" | "adapter_gcov_failure") =>
            {
                command.to_owned()
            }
            _ => section.to_owned(),
        }
    };
    let pin = expected
        .iter()
        .any(|row| command(row.0) == "verify")
        .then(|| assert_capture_runtime(&oracle));
    let mut names: Vec<&str> = expected.iter().map(|row| row.0).collect();
    names.sort_unstable();
    assert_eq!(
        oracle[section]
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        names,
        "{section}"
    );
    for (name, exit, status, target_names) in expected {
        let frozen = &oracle[section][name];
        assert_eq!(frozen["exit_code"], *exit, "{name}: frozen exit");
        assert_eq!(
            frozen["engine"]["status"].as_str().unwrap_or("-"),
            *status,
            "{name}: frozen"
        );
        let frozen_names: Vec<&str> = frozen["targets"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|target| target["target_name"].as_str().unwrap())
            .collect();
        assert_eq!(frozen_names, *target_names, "{name}: frozen targets");
        let command = command(name);
        let actual = run(
            name,
            frozen,
            &command,
            pin.as_deref().filter(|_| command == "verify"),
        );
        let mut expected_exit = *exit;
        if let Some(engines) = frozen["engines"].as_array() {
            let native = actual.engines.as_deref().unwrap();
            let names = |items: &[Value]| -> Vec<Value> {
                items.iter().map(|item| item[0].clone()).collect()
            };
            assert_eq!(names(native), names(engines), "{name}: engines");
            // The verdicts this unit decides: test and sanitize (other engines' evidence labels
            // are their own facets; complexity's is a recorded open difference).
            let owned = |items: &[Value]| -> Vec<Value> {
                items
                    .iter()
                    .filter(|item| item[0] == "test" || item[0] == "sanitize")
                    .cloned()
                    .collect()
            };
            assert_eq!(owned(native), owned(engines), "{name}: test and sanitize");
            if ALL_ENGINES.contains(name) {
                assert_eq!(native, engines.as_slice(), "{name}: every engine");
            }
            // decisions.md "legacy 종료 코드 3의 범위": the required sanitize engine that produced
            // no evidence (ERROR/NOT_RUN on both sides, compared above) leaves the native gate
            // INCOMPLETE (3) where frozen exits 1, and it must be the only such engine natively.
            // A test engine's `coverage json` tool error is not that case (native gate PASS,
            // exit 1, like frozen).
            let unmeasured =
                |item: &&Value| item[1] == "ERROR" && item[2] == "NOT_RUN" && item[3] == true;
            let sanitize_unmeasured = engines
                .iter()
                .any(|item| item == &json!(["sanitize", "ERROR", "NOT_RUN", true]));
            if *exit == 1 && sanitize_unmeasured {
                assert!(
                    native
                        .iter()
                        .filter(unmeasured)
                        .all(|item| item[0] == "sanitize" || item[0] == "test"),
                    "{name}: {native:?}"
                );
                expected_exit = 3;
            }
        }
        if let Some((_, _, native, reason)) = NATIVE_EXIT
            .iter()
            .find(|(owner, scenario, _, _)| *owner == section && scenario == name)
        {
            assert_eq!(
                *exit, 1,
                "{name}: frozen exit of a recorded class ({reason})"
            );
            expected_exit = i64::from(*native);
        }
        assert_eq!(i64::from(actual.exit), expected_exit, "{name}: exit");
        compare(name, frozen, &actual);
    }
}

const CASE: &[&str] = &["tests/test_calc.py::test_add"];
const PROJECT: &[&str] = &["[Project] Tests"];
/// The Python case, then frozen `_mark_zero_tests("C++")`.
const ZERO_CPP: &[&str] = &["tests/test_calc.py::test_add", "[C++] Tests"];
const MEASURED: &[&str] = &[
    "tests/test_calc.py::test_add",
    "Coverage:Overall line",
    "Coverage:Functions",
    "Coverage:Function:add",
];

/// `ici test` on every tree. Positive: tests/test_*.py alone (flat, nested, beside Python outside
/// `tests/`, python and hybrid types, the disabled engine and `fast` a typed command re-enables)
/// runs pytest (WARN, no coverage estimate), selected Python measured (PASS), a measured C++ test.
/// Failure: zero C++ tests beside the Python suite (the coverage json of a `src` without Python
/// fails: ERROR with "[C++] Tests"), the C++ test beside it (ERROR, gcov partial), "[Project]
/// Tests" when neither suite is attempted. Negative: a next project (exit 2, no report).
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON) and g++ on /usr/bin"]
fn legacy_test_follows_the_frozen_suite_scope() {
    replay(
        "test",
        &[
            ("cpp-tests-python", 0, "WARN", CASE),
            (
                "cpp-nested-tests-python",
                0,
                "WARN",
                &["tests/a/b/test_x.py::test_add"],
            ),
            ("cpp-outside-python-tests-python", 0, "WARN", CASE),
            ("python-tests-python", 0, "WARN", CASE),
            ("hybrid-tests-python", 0, "WARN", CASE),
            ("cpp-src-python-tests-python", 0, "PASS", MEASURED),
            ("hybrid-src-python-tests-python", 0, "PASS", MEASURED),
            ("cpp-tests-helper-only", 1, "FAIL", PROJECT),
            ("cpp-tests-empty", 1, "FAIL", PROJECT),
            ("cpp-outside-python", 1, "FAIL", PROJECT),
            ("cpp-src-python", 1, "FAIL", PROJECT),
            ("cpp-src-cpp", 1, "FAIL", &["[C++] Tests"]),
            (
                "cpp-src-cpp-tests-python",
                1,
                "ERROR",
                &["tests/test_calc.py::test_add", "[C++] Tests"],
            ),
            (
                "cpp-src-cpp-tests-cpp-python",
                1,
                "ERROR",
                &["tests/test_calc.py::test_add", "[C++] test_twice.cpp"],
            ),
            (
                "cpp-src-python-tests-cpp",
                0,
                "WARN",
                &["[C++] test_main.cpp"],
            ),
            ("cpp-tests-python-disabled", 0, "WARN", CASE),
            ("cpp-tests-python-fast", 0, "WARN", CASE),
            ("next-cpp-tests-python", 2, "-", &[]),
            ("hybrid-tests-empty", 1, "FAIL", PROJECT),
            ("cpp-src-cpp-tests-python-required", 1, "ERROR", ZERO_CPP),
            (
                "hybrid-src-cpp-python-tests-python-required",
                1,
                "ERROR",
                ZERO_CPP,
            ),
            ("no-coverage-cpp-src-cpp-tests-python", 1, "FAIL", ZERO_CPP),
            (
                "no-coverage-hybrid-src-cpp-python-tests-python",
                1,
                "FAIL",
                ZERO_CPP,
            ),
            (
                "no-coverage-hybrid-src-python-tests-python",
                0,
                "WARN",
                CASE,
            ),
            ("no-coverage-hybrid-tests-python", 0, "WARN", CASE),
            ("no-coverage-cpp-tests-python", 0, "WARN", CASE),
            (
                "no-coverage-cpp-src-cpp-tests-cpp-python",
                0,
                "WARN",
                &["tests/test_calc.py::test_add", "[C++] test_twice.cpp"],
            ),
        ],
    );
}

/// Deep/standard `ici verify` composes the same test engine; the disabled engine and `fast` select
/// none (frozen and native exit 0 without a test engine); a next project is refused alike (3).
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON) and g++ on /usr/bin"]
fn legacy_verify_composes_the_same_test_engine() {
    replay(
        "verify",
        &[
            ("cpp-tests-python", 0, "WARN", CASE),
            ("cpp-tests-python-standard", 0, "WARN", CASE),
            ("cpp-tests-helper-only", 1, "FAIL", PROJECT),
            ("cpp-src-python", 1, "FAIL", PROJECT),
            (
                "cpp-src-cpp-tests-python",
                1,
                "ERROR",
                &["tests/test_calc.py::test_add", "[C++] Tests"],
            ),
            (
                "cpp-src-cpp-tests-cpp-python",
                1,
                "ERROR",
                &["tests/test_calc.py::test_add", "[C++] test_twice.cpp"],
            ),
            ("cpp-tests-python-disabled", 0, "-", &[]),
            ("cpp-tests-python-fast", 0, "-", &[]),
            ("next-cpp-tests-python", 3, "-", &[]),
            ("hybrid-tests-python", 0, "WARN", CASE),
            ("hybrid-tests-empty", 1, "FAIL", PROJECT),
            ("hybrid-src-python-tests-python", 0, "PASS", MEASURED),
            ("cpp-src-cpp-tests-python-required", 1, "ERROR", ZERO_CPP),
            ("no-coverage-cpp-src-cpp-tests-python", 1, "FAIL", ZERO_CPP),
            (
                "no-coverage-hybrid-src-cpp-python-tests-python",
                1,
                "FAIL",
                ZERO_CPP,
            ),
            (
                "no-coverage-hybrid-src-python-tests-python",
                0,
                "WARN",
                CASE,
            ),
        ],
    );
}

/// A `type = "python"` project's tests/*.cpp (frozen test.py:268 attempts the C++ suite whatever
/// the type) beside its Python suite plans both, also when no C/C++ source is selected (its
/// component's languages are not widened); with a selected C/C++ source the widened component
/// plans them as before.
#[test]
fn a_python_project_plans_its_cpp_test_suite_beside_the_python_one() {
    for files in [
        &[
            ("src/calc.py", "X = 1\n"),
            ("tests/test_calc.py", TEST),
            ("tests/test_main.cpp", "int main() { return 0; }\n"),
        ][..],
        &[
            ("src/calc.py", "X = 1\n"),
            ("src/twice.cpp", CPP),
            ("tests/test_calc.py", TEST),
            ("tests/test_twice.cpp", "int main() { return 0; }\n"),
        ][..],
    ] {
        for argv in [&["ici", "test"][..], &["ici", "verify"][..]] {
            let planned = plan(files, &legacy_config("python", "", "deep"), argv);
            let ids: Vec<&str> = planned.checks.iter().map(|(id, _)| id.as_str()).collect();
            assert_eq!(
                ids,
                ["cpp.coverage", "cpp.test", "python.coverage", "python.test"],
                "{files:?} {argv:?}"
            );
        }
    }
    // Without tests/*.cpp a python project attempts no C++ suite for its Python sources.
    let planned = plan(
        &[("src/calc.py", "X = 1\n"), ("tests/test_calc.py", TEST)],
        &legacy_config("python", "", "deep"),
        &["ici", "test"],
    );
    assert_eq!(planned.checks, owned(PYTHON_ONLY));
}

/// A `type = "python"` project's tests/*.cpp (the oracle's `open` section, replayed since
/// cppval17). Positive: beside a C/C++ source frozen pools gcov rows with coverage.py's (PASS,
/// MEASURED, "coverage.py/gcov"); failure-to-measure boundary: without one the C++ case passes
/// and gcov reads nothing, so frozen pools the coverage.py rows alone (WARN ESTIMATED,
/// "coverage.py (partial)", no Coverage target, no "cpp" provenance), under the python, cpp and
/// hybrid types alike (cppval18). Failure: the same pooled state under `coverage_required`
/// (ERROR NOT_RUN, the direct path's two tool errors, the same pooled extra; exit 1 under both
/// commands, root ruling 2026-11-14). The pooled views compare `coverage_provenance`,
/// `coverage_files`, `coverage_totals`, `coverage_policy` and `function_rows` too. `ici test` and
/// `verify`.
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON) and g++ on /usr/bin"]
fn python_type_cpp_tests_pool_both_suites_like_frozen() {
    const POOLED: &[&str] = &["tests/test_calc.py::test_add", "[C++] test_main.cpp"];
    const MEASURED_BOTH: &[&str] = &[
        "tests/test_calc.py::test_add",
        "[C++] test_twice.cpp",
        "Coverage:Overall line",
        "Coverage:Functions",
        "Coverage:Function:add",
        "Coverage:Function:twice(int)",
    ];
    replay(
        "open",
        &[
            ("test:python-src-python-tests-cpp", 0, "WARN", POOLED),
            (
                "test:python-src-cpp-python-tests-cpp",
                0,
                "PASS",
                MEASURED_BOTH,
            ),
            ("verify:python-src-python-tests-cpp", 0, "WARN", POOLED),
            (
                "verify:python-src-cpp-python-tests-cpp",
                0,
                "PASS",
                MEASURED_BOTH,
            ),
            ("test:cpp-src-python-tests-python-cpp", 0, "WARN", POOLED),
            ("verify:cpp-src-python-tests-python-cpp", 0, "WARN", POOLED),
            ("test:hybrid-src-python-tests-python-cpp", 0, "WARN", POOLED),
            (
                "verify:hybrid-src-python-tests-python-cpp",
                0,
                "WARN",
                POOLED,
            ),
            (
                "test:python-src-python-tests-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "verify:python-src-python-tests-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "test:cpp-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "verify:cpp-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "test:hybrid-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "verify:hybrid-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
        ],
    );
}

/// The frozen literals of the `open` section, so a recapture that drifts fails without a host.
#[test]
fn python_type_cpp_tests_frozen_pool_is_recorded() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let open = oracle["open"].as_object().unwrap();
    let mut names: Vec<&str> = open.keys().map(String::as_str).collect();
    names.sort_unstable();
    let mut expected = vec![];
    for tree in [
        "python-src-cpp-python-tests-cpp",
        "python-src-python-tests-cpp",
        "python-src-python-tests-cpp-required",
        "cpp-src-python-tests-python-cpp",
        "cpp-src-python-tests-python-cpp-required",
        "hybrid-src-python-tests-python-cpp",
        "hybrid-src-python-tests-python-cpp-required",
    ] {
        for command in ["test", "verify"] {
            expected.push(format!("{command}:{tree}"));
        }
    }
    expected.sort_unstable();
    assert_eq!(names, expected);
    for command in ["test", "verify"] {
        let measured = &open[&format!("{command}:python-src-cpp-python-tests-cpp")];
        assert_eq!(measured["exit_code"], 0);
        assert_eq!(measured["engine"]["status"], "PASS");
        assert_eq!(measured["engine"]["evidence"], "MEASURED");
        assert_eq!(measured["extra"]["coverage_source"], "coverage.py/gcov");
        assert_eq!(measured["extra"]["total_tests"], 2);
        for (tree, kind) in [
            ("python-src-python-tests-cpp", "python"),
            ("cpp-src-python-tests-python-cpp", "cpp"),
            ("hybrid-src-python-tests-python-cpp", "hybrid"),
        ] {
            for required in [false, true] {
                let name = format!(
                    "{command}:{tree}{}",
                    if required { "-required" } else { "" }
                );
                let pooled = &open[&name];
                assert_eq!(pooled["type"], kind, "{name}");
                assert_eq!(
                    pooled["extra_config"],
                    if required {
                        "coverage_required = true\n"
                    } else {
                        ""
                    },
                    "{name}"
                );
                let names: Vec<&str> = pooled["targets"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|target| target["target_name"].as_str().unwrap())
                    .collect();
                assert_eq!(
                    names,
                    ["tests/test_calc.py::test_add", "[C++] test_main.cpp"],
                    "{name}"
                );
                assert_eq!(pooled["extra"]["coverage_source"], "coverage.py (partial)");
                assert_eq!(pooled["extra"]["total_tests"], 2, "{name}");
                assert_eq!(pooled["extra"]["passed_tests"], 2, "{name}");
                assert_eq!(pooled["extra"]["tem_score"], 5.0, "{name}");
                assert_eq!(pooled["extra"]["coverage_policy"], Value::Null, "{name}");
                // Frozen records no "cpp" provenance: gcov read nothing.
                let provenance = pooled["extra"]["coverage_provenance"].as_object().unwrap();
                let keys: Vec<&str> = provenance.keys().map(String::as_str).collect();
                assert_eq!(keys, ["python", "scope"], "{name}");
                assert_eq!(
                    provenance["scope"]["included_sources"],
                    json!(["src/calc.py"]),
                    "{name}"
                );
                assert_eq!(provenance["scope"]["discovered_cpp_sources"], 0, "{name}");
                let files: Vec<&str> = pooled["extra"]["coverage_files"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|row| row["file"].as_str().unwrap())
                    .collect();
                assert_eq!(files, ["src/calc.py"], "{name}");
                if required {
                    assert_eq!(pooled["exit_code"], 1, "{name}");
                    assert_eq!(pooled["engine"]["status"], "ERROR", "{name}");
                    assert_eq!(pooled["engine"]["evidence"], "NOT_RUN", "{name}");
                    assert_eq!(
                        pooled["engine"]["summary"],
                        "C++ source files for gcov were unavailable; C++ gcov coverage \
                         evidence was unavailable or malformed",
                        "{name}"
                    );
                } else {
                    assert_eq!(pooled["exit_code"], 0, "{name}");
                    assert_eq!(pooled["engine"]["status"], "WARN", "{name}");
                    assert_eq!(pooled["engine"]["evidence"], "ESTIMATED", "{name}");
                    assert!(
                        pooled["engine"]["summary"].as_str().unwrap().ends_with(
                            "; Coverage evidence ESTIMATED (optional; not threshold evidence)"
                        ),
                        "{name}"
                    );
                }
                assert_eq!(pooled["engine"]["score"], 5.0, "{name}");
            }
        }
    }
}

/// The frozen literals of the `open_adapter` section (root ruling 2026-11-14 (3)): the same pooled
/// state with a CMakeLists.txt that builds only the tests/*.cpp case. Frozen's CMake adapter runs
/// gcov over the test-only notes, so its provenance carries a "cpp" entry with no expected source;
/// optional it is WARN ESTIMATED and exit 0, required the gcov output error ERROR NOT_RUN and exit
/// 1, under `test` and `verify` and every project type. Replayed by
/// `cmake_adapter_pool_without_production_sources_like_frozen`.
#[test]
fn frozen_cmake_adapter_pool_is_recorded() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let adapter = oracle["open_adapter"].as_object().unwrap();
    assert_eq!(adapter.len(), 12);
    for command in ["test", "verify"] {
        for (tree, kind) in [
            ("python-cmake-src-python-tests-cpp", "python"),
            ("cpp-cmake-src-python-tests-python-cpp", "cpp"),
            ("hybrid-cmake-src-python-tests-python-cpp", "hybrid"),
        ] {
            for required in [false, true] {
                let name = format!(
                    "{command}:{tree}{}",
                    if required { "-required" } else { "" }
                );
                let view = &adapter[&name];
                assert_eq!(view["type"], kind, "{name}");
                assert!(view["files"]["CMakeLists.txt"].is_string(), "{name}");
                assert_eq!(
                    view["extra"]["coverage_source"], "coverage.py (partial)",
                    "{name}"
                );
                let cpp = &view["extra"]["coverage_provenance"]["cpp"];
                assert_eq!(cpp["format"], "gcov-json", "{name}");
                assert_eq!(cpp["expected_sources"], 0, "{name}");
                assert_eq!(cpp["matched_file_records"], 0, "{name}");
                assert_eq!(cpp["report_count"], 2, "{name}");
                let (exit, status, evidence) = if required {
                    (1, "ERROR", "NOT_RUN")
                } else {
                    (0, "WARN", "ESTIMATED")
                };
                assert_eq!(view["exit_code"], exit, "{name}");
                assert_eq!(view["engine"]["status"], status, "{name}");
                assert_eq!(view["engine"]["evidence"], evidence, "{name}");
                if required {
                    assert_eq!(
                        view["engine"]["summary"],
                        "C++ gcov coverage output was missing or malformed; C++ gcov coverage \
                         evidence was unavailable or malformed",
                        "{name}"
                    );
                }
            }
        }
    }
}

/// The CMake adapter port (root ruling 2026-11-14, cppval21) over the `open_adapter` trees:
/// tests/test_calc.py beside a CMakeLists.txt that builds only the tests/*.cpp case, no production
/// C/C++ source. Native now runs gcov over the test-only notes like frozen
/// `_run_cpp_tests_via_adapter` (every file record ignored, no row) and pools the coverage.py rows
/// alone. Positive/optional: WARN ESTIMATED "coverage.py (partial)" with the gcov-json provenance
/// (`expected_sources` 0), exit 0. Failure: under `coverage_required` ERROR NOT_RUN "C++ gcov
/// coverage output was missing or malformed; C++ gcov coverage evidence was unavailable or
/// malformed", exit 1 under `ici test` and `verify` (the pooled no-production exit, root ruling
/// 2026-11-14). The python, cpp and hybrid types alike; `coverage_provenance.cpp` is compared
/// except the approved gcov counters (`GCOV_TALLY`).
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON), g++ and cmake on /usr/bin"]
fn cmake_adapter_pool_without_production_sources_like_frozen() {
    const POOLED: &[&str] = &["tests/test_calc.py::test_add", "[C++] test_main"];
    replay(
        "open_adapter",
        &[
            ("test:python-cmake-src-python-tests-cpp", 0, "WARN", POOLED),
            (
                "test:python-cmake-src-python-tests-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "test:cpp-cmake-src-python-tests-python-cpp",
                0,
                "WARN",
                POOLED,
            ),
            (
                "test:cpp-cmake-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "test:hybrid-cmake-src-python-tests-python-cpp",
                0,
                "WARN",
                POOLED,
            ),
            (
                "test:hybrid-cmake-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "verify:python-cmake-src-python-tests-cpp",
                0,
                "WARN",
                POOLED,
            ),
            (
                "verify:python-cmake-src-python-tests-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "verify:cpp-cmake-src-python-tests-python-cpp",
                0,
                "WARN",
                POOLED,
            ),
            (
                "verify:cpp-cmake-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
            (
                "verify:hybrid-cmake-src-python-tests-python-cpp",
                0,
                "WARN",
                POOLED,
            ),
            (
                "verify:hybrid-cmake-src-python-tests-python-cpp-required",
                1,
                "ERROR",
                POOLED,
            ),
        ],
    );
}

/// The CMake adapter pool's failure leg (cpp36, the `open_adapter` cpp tree with a `gcov` stub
/// first on PATH, `adapter_gcov_failure`): frozen `collect_coverage` returns no directory when
/// gcov exits non-zero ("gcov failed with exit code 3") or `gcov --help` fails ("gcov JSON
/// capability probe was incomplete"), so `_run_cpp_tests_via_adapter` records that tool error
/// and the empty reading's coverage error and pools the coverage.py rows alone, with no "cpp"
/// provenance. Positive: both C++ and Python cases PASS with the pooled extra. Negative/failure:
/// ERROR NOT_RUN, exit 1, optional (the summary is the tool error) and under
/// `coverage_required` (the tool error, the coverage error and the missing evidence), under
/// `ici test` and `verify` alike.
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON), g++ and cmake on /usr/bin"]
fn cmake_adapter_pool_gcov_failure_like_frozen() {
    const POOLED: &[&str] = &["tests/test_calc.py::test_add", "[C++] test_main"];
    let mut expected: Vec<Expected> = vec![];
    for name in [
        "test:cpp-cmake-gcov-exit",
        "test:cpp-cmake-gcov-exit-required",
        "test:cpp-cmake-gcov-probe",
        "test:cpp-cmake-gcov-probe-required",
        "verify:cpp-cmake-gcov-exit",
        "verify:cpp-cmake-gcov-exit-required",
        "verify:cpp-cmake-gcov-probe",
        "verify:cpp-cmake-gcov-probe-required",
    ] {
        expected.push((name, 1, "ERROR", POOLED));
    }
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for (name, reason) in [
        ("cpp-cmake-gcov-exit", "gcov failed with exit code 3"),
        (
            "cpp-cmake-gcov-probe",
            "gcov JSON capability probe was incomplete",
        ),
    ] {
        for command in ["test", "verify"] {
            let section = &oracle["adapter_gcov_failure"];
            let optional = &section[format!("{command}:{name}")];
            let required = &section[format!("{command}:{name}-required")];
            assert_eq!(optional["engine"]["summary"], reason, "{command}:{name}");
            assert_eq!(
                required["engine"]["summary"],
                format!(
                    "{reason}; C++ gcov coverage output was missing or malformed; C++ gcov \
                     coverage evidence was unavailable or malformed"
                ),
                "{command}:{name}"
            );
            for view in [optional, required] {
                assert_eq!(view["extra"]["coverage_source"], "coverage.py (partial)");
                assert!(view["extra"]["coverage_provenance"]["cpp"].is_null());
            }
        }
    }
    replay("adapter_gcov_failure", &expected);
}
