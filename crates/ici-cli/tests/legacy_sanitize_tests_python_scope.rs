//! Frozen `SanitizeEngine._analysis_scopes` (engines/sanitize.py:100-125) puts the Python scope in
//! play for a selected Python source or `tests/**/*.py` (`_has_python_tests`), whatever the declared
//! project type. A legacy `type = "cpp"` project whose only Python is under `tests/` therefore runs
//! the ResourceWarning suite under `ici sanitize` and deep/standard `ici verify` (frozen captures:
//! PASS, MEASURED, exit 0). Native used to drop `python.sanitize` at planning because the root
//! component declares only `cpp`.
//!
//! Positive: `python.sanitize` is planned (not blocked) beside `cpp.sanitize` for `tests/*.py` and
//! a nested `tests/a/b/test_x.py`, under `ici sanitize` and `ici verify`, without widening the
//! component's languages or planning any other Python check. Negative: no `python.sanitize` for a
//! tests path without Python, for Python outside `tests/` that `source_dirs` does not select (frozen:
//! the no-scope SKIP), under `ici thread-sanitize` (`CHECK_PYTHON_RESOURCES = False`), under the
//! next command, and for a schema 2 project. Failure/omission: the disabled engine and the `fast`
//! profile omit `python.sanitize` with the reason `cpp.sanitize` gets.
use ici_cli::{planning, registry};
use std::{collections::BTreeMap, fs};

const TEST: &str = "def test_add():\n    assert 1 + 2 == 3\n";

fn legacy_config(extra: &str, profile: &str) -> String {
    format!(
        "[ici]\nprofile = \"{profile}\"\n[project]\ntype = 'cpp'\nsource_dirs = [\"src\"]\n{extra}"
    )
}

struct Planned {
    /// (check id, blocked) of every planned check.
    checks: Vec<(String, bool)>,
    /// (check id, reason) of every omitted check.
    omitted: Vec<(String, String)>,
    languages: Vec<Vec<String>>,
}

fn plan(files: &[(&str, &str)], config: &str, argv: &[&str]) -> Planned {
    let dir = tempfile::tempdir().unwrap();
    // A VCS boundary at the fixture: root discovery never climbs past it.
    fs::create_dir(dir.path().join(".hg")).unwrap();
    for (path, text) in files {
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

fn python_checks(planned: &Planned) -> Vec<(&str, bool)> {
    planned
        .checks
        .iter()
        .filter(|(id, _)| id.starts_with("python."))
        .map(|(id, blocked)| (id.as_str(), *blocked))
        .collect()
}

fn mentions_python_sanitize(planned: &Planned) -> bool {
    planned.checks.iter().any(|(id, _)| id == "python.sanitize")
        || planned
            .omitted
            .iter()
            .any(|(id, _)| id == "python.sanitize")
}

#[test]
fn a_cpp_project_with_only_tests_python_plans_the_python_sanitize_scope() {
    for tests in [
        "tests/test_calc.py",
        "tests/a/b/test_x.py",
        "tests/helper.py",
    ] {
        let planned = plan(
            &[(tests, TEST)],
            &legacy_config("", "deep"),
            &["ici", "sanitize"],
        );
        assert_eq!(
            planned.checks,
            [
                ("cpp.sanitize".to_owned(), false),
                ("python.sanitize".to_owned(), false)
            ],
            "{tests}"
        );
        assert_eq!(planned.languages, [["cpp"]], "{tests}");
        // Deep and standard `ici verify` run the same engine (frozen PASS, MEASURED, exit 0).
        // Beside it, frozen TestEngine runs the `tests/**/test_*.py` suite (not a helper):
        // `legacy_test_scope.rs`.
        let expected: &[(&str, bool)] = if tests.ends_with("helper.py") {
            &[("python.sanitize", false)]
        } else {
            &[
                ("python.coverage", false),
                ("python.sanitize", false),
                ("python.test", false),
            ]
        };
        for profile in ["deep", "standard"] {
            let planned = plan(
                &[(tests, TEST)],
                &legacy_config("", profile),
                &["ici", "verify"],
            );
            assert_eq!(python_checks(&planned), expected, "{tests} {profile}");
            assert_eq!(planned.languages, [["cpp"]], "{tests} {profile}");
        }
    }
    // Beside a selected C++ source (frozen: the partial verdict, Python measured).
    let planned = plan(
        &[
            ("src/twice.cpp", "int twice(int v) { return v * 2; }\n"),
            ("tests/test_calc.py", TEST),
        ],
        &legacy_config("", "deep"),
        &["ici", "sanitize"],
    );
    assert_eq!(
        planned.checks,
        [
            ("cpp.sanitize".to_owned(), false),
            ("python.sanitize".to_owned(), false)
        ]
    );
    // A typed `ici sanitize` selects its engine even when the project disables it (frozen runs
    // the Python scope: PASS, MEASURED).
    let planned = plan(
        &[("tests/test_calc.py", TEST)],
        &legacy_config("[engines.sanitize]\nenabled = false\n", "deep"),
        &["ici", "sanitize"],
    );
    assert_eq!(python_checks(&planned), [("python.sanitize", false)]);
}

#[test]
fn no_python_scope_without_tests_python_or_outside_legacy_sanitize() {
    let data = [("tests/data.txt", "x\n")];
    let outside = [("tools/helper.py", "X = 1\n"), ("tests/data.txt", "x\n")];
    let outside_with_cpp = [
        ("src/twice.cpp", "int twice(int v) { return v * 2; }\n"),
        ("tools/helper.py", "X = 1\n"),
    ];
    for (files, argv) in [
        (&data[..], &["ici", "sanitize"][..]),
        (&data[..], &["ici", "verify"][..]),
        (&outside[..], &["ici", "sanitize"][..]),
        (&outside[..], &["ici", "verify"][..]),
        (&outside_with_cpp[..], &["ici", "sanitize"][..]),
    ] {
        let planned = plan(files, &legacy_config("", "deep"), argv);
        assert!(!mentions_python_sanitize(&planned), "{files:?} {argv:?}");
        assert_eq!(python_checks(&planned), [], "{files:?} {argv:?}");
    }
    let only_tests = [("tests/test_calc.py", TEST)];
    // ThreadSanitizeEngine has no Python scope.
    let planned = plan(
        &only_tests,
        &legacy_config("", "deep"),
        &["ici", "thread-sanitize"],
    );
    assert_eq!(planned.checks, [("cpp.tsan".to_owned(), false)]);
    assert!(!mentions_python_sanitize(&planned));
    // The next command keeps the declared scope.
    let planned = plan(
        &only_tests,
        &legacy_config("", "deep"),
        &["ici", "next", "verify"],
    );
    assert!(!mentions_python_sanitize(&planned));
    assert_eq!(python_checks(&planned), []);
    // A schema 2 project declares its languages; the historical command keeps them.
    let planned = plan(
        &[("src/a.cpp", "int a;\n"), ("tests/test_calc.py", TEST)],
        "schema_version=2\n[[components]]\nid='project'\nroot='.'\nlanguages=['cpp']\nsources=['src/a.cpp']\n",
        &["ici", "sanitize"],
    );
    assert!(!mentions_python_sanitize(&planned));
}

#[test]
fn verify_omits_the_python_scope_with_its_engine() {
    let only_tests = [("tests/test_calc.py", TEST)];
    // Frozen deep `ici verify` with `[engines.sanitize] enabled = false` and `fast` `ici verify`
    // run no sanitize engine at all.
    let disabled = plan(
        &only_tests,
        &legacy_config("[engines.sanitize]\nenabled = false\n", "deep"),
        &["ici", "verify"],
    );
    let sanitize_omitted = |planned: &Planned| -> Vec<(String, String)> {
        planned
            .omitted
            .iter()
            .filter(|(id, _)| id.ends_with(".sanitize"))
            .cloned()
            .collect()
    };
    assert_eq!(
        sanitize_omitted(&disabled),
        [
            ("cpp.sanitize".to_owned(), "disabled".to_owned()),
            ("python.sanitize".to_owned(), "disabled".to_owned())
        ]
    );
    assert!(
        !disabled
            .checks
            .iter()
            .any(|(id, _)| id == "python.sanitize")
    );
    let fast = plan(&only_tests, &legacy_config("", "fast"), &["ici", "verify"]);
    assert_eq!(
        sanitize_omitted(&fast),
        [
            (
                "cpp.sanitize".to_owned(),
                "not in profile 'fast'".to_owned()
            ),
            (
                "python.sanitize".to_owned(),
                "not in profile 'fast'".to_owned()
            )
        ]
    );
    assert_eq!(python_checks(&fast), []);
}
