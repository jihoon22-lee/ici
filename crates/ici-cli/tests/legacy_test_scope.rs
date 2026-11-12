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
//! steps; `verify`'s exit belongs to every engine (sanitize's required ERROR exits 3, decisions.md
//! "legacy 종료 코드 3의 범위") and is compared only for the next project.
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
    engines: Option<Vec<String>>,
    engine: Option<Value>,
}

fn run(frozen: &Value, command: &str) -> Run {
    let python = std::env::var("ICI_TEST_PYTHON")
        .expect("explicit ICI_TEST_PYTHON (project CPython with pytest and coverage)");
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
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args([command, "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &home)
        .env("PATH", "/usr/bin:/bin")
        .env("LC_CTYPE", "C.UTF-8")
        .output()
        .unwrap();
    let report = fs::read_to_string(root.join(format!("{command}_report.json")))
        .ok()
        .map(|text| {
            serde_json::from_str::<Value>(
                &text
                    .replace(&root.display().to_string(), "<ROOT>")
                    .replace(&python, "<PYTHON>"),
            )
            .unwrap()
        });
    let (engines, engine) = match report {
        Some(report) if command == "verify" => match report["results"].as_array() {
            Some(results) => {
                let mut names: Vec<String> = results
                    .iter()
                    .map(|item| item["engine_name"].as_str().unwrap().to_owned())
                    .collect();
                names.sort();
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

/// A pytest/unittest invocation without native's own bookkeeping (data file, cache plugin,
/// traceback and colour options), its tokens sorted (native orders `-v` before `-o addopts=`).
fn invocation(argv: &Value) -> Vec<String> {
    let mut tokens = vec![];
    let mut skip = false;
    for token in argv.as_array().unwrap() {
        let token = token.as_str().unwrap();
        if std::mem::take(&mut skip) {
            continue;
        }
        match token {
            "--data-file" | "-p" => skip = true,
            "--tb=short" | "--color=no" => {}
            _ => tokens.push(token.replace("<ROOT>/", "")),
        }
    }
    tokens.sort();
    tokens
}

fn cxx_step(name: &str) -> bool {
    name.starts_with("g++") || name.starts_with("gcov") || name == "C++ test"
}

/// The Python tool steps (name, return code, invocation) and whether a C++ test ran.
fn tools(evidence: &Value) -> (Vec<Value>, Vec<Value>) {
    let items = evidence.as_array().unwrap();
    let python = items
        .iter()
        .filter(|item| !cxx_step(item["name"].as_str().unwrap()))
        .map(|item| {
            let name = item["name"].as_str().unwrap();
            if name.contains("pytest") || name.contains("unittest") {
                json!([name, item["returncode"], invocation(&item["argv"])])
            } else {
                json!([name, item["returncode"]])
            }
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
        tools(&native["tool_evidence"]),
        tools(&frozen["tool_evidence"]),
        "{name}: tool evidence"
    );
    let extra = &frozen["extra"];
    for (key, value) in extra.as_object().unwrap() {
        if !ESTIMATES.contains(&key.as_str()) || extra["coverage_source"] != "estimated" {
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
    let zero = frozen["targets"]
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

/// Each scenario's frozen outcome as literals, so a recapture that drifts fails here too:
/// (scenario, exit, status, target names in order).
type Expected = (&'static str, i64, &'static str, &'static [&'static str]);

fn replay(section: &str, expected: &[Expected]) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
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
        let actual = run(frozen, section);
        if section == "test" || frozen["type"] == "next" {
            assert_eq!(i64::from(actual.exit), *exit, "{name}: exit");
        } else {
            let engines: Vec<&str> = frozen["engines"]
                .as_array()
                .unwrap()
                .iter()
                .map(|item| item.as_str().unwrap())
                .collect();
            assert_eq!(
                actual.engines.as_deref().unwrap(),
                engines,
                "{name}: engines"
            );
        }
        compare(name, frozen, &actual);
    }
}

const CASE: &[&str] = &["tests/test_calc.py::test_add"];
const PROJECT: &[&str] = &["[Project] Tests"];
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
        ],
    );
}
