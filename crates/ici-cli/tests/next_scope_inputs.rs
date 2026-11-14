//! Which check scopes a `next verify` result moves when one kind of input changes, against frozen
//! (`tests/rust-parity/python/capture_scope_inputs.py` → `scope-inputs-oracle.json`).
//!
//! Frozen's check scope (application/check_scope.py) hashes selected files, units, externals,
//! builds and the task invocation with the bytes of the tool configuration files among its input
//! references; not source contents and not the inherited environment. Native moves the same
//! scopes for source edits, an added source, an unrelated file and a new `ruff.toml`; a changed
//! environment moves every check that starts a tool. Where a tool reads a configuration file frozen did not put among its task
//! inputs, native's scope also moves (decisions.md "next check scope의 설정 파일 입력"): a changed
//! setting there must not make an old finding look resolved.
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    process::{Command, Stdio},
};

const ORACLE: &str = include_str!("../../../tests/rust-parity/python/scope-inputs-oracle.json");

/// Native moves these scopes in addition to frozen's, each because its tool reads the file:
/// mypy reads mypy.ini, setup.cfg and pyproject.toml; pytest reads its locate_config candidates
/// from the test directory upward and coverage its config files in the working directory; the
/// runtime check reads pyproject.toml's requires-python. A task gets the caller's whole
/// environment and its scope hashes it (decisions.md "도구 실행 환경"), so any variable moves every
/// check that starts a tool; frozen hashed only the task's overlay.
const TOOL_CHECKS: &[&str] = &[
    "python.compat-runtime",
    "python.coverage",
    "python.format",
    "python.lint",
    "python.test",
    "python.type",
];
const STRICTER: &[(&str, &[&str])] = &[
    ("inherited-environment", TOOL_CHECKS),
    // coverage also reads the file `COVERAGE_RCFILE` names instead of `.coveragerc`.
    ("coverage-rcfile-environment", TOOL_CHECKS),
    ("mypy-ini-added", &["python.type"]),
    (
        "setup-cfg-added",
        &["python.coverage", "python.test", "python.type"],
    ),
    ("pytest-ini-added", &["python.coverage", "python.test"]),
    (
        "pyproject-tool-edit",
        &[
            "python.compat-runtime",
            "python.coverage",
            "python.test",
            "python.type",
        ],
    ),
    // coverage reads .coveragerc from its working directory; `omit` there changes what it
    // measures (frozen: 85.7% of 7 lines became 100% of 3 with its scope unmoved).
    ("coveragerc-added", &["python.coverage"]),
    ("coveragerc-omit", &["python.coverage"]),
    // pytest 9 takes these as its configuration, searching up from the test directory.
    ("pytest-toml-added", &["python.coverage", "python.test"]),
    (
        "hidden-pytest-ini-added",
        &["python.coverage", "python.test"],
    ),
    (
        "tests-pytest-ini-added",
        &["python.coverage", "python.test"],
    ),
];

fn config(python: &str) -> String {
    format!(
        "schema_version=1\n[workspace]\nname='fixture'\n[[components]]\nid='app'\nroot='.'\nlanguages=['python']\nsources=['src/**/*.py']\n[components.python]\nexecutable={python:?}\ntest_tools='project'\ntest_paths=['tests']\ntype_provider='mypy'\n"
    )
}

fn scopes(
    root: &Path,
    base: &Path,
    python: &str,
    environment: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let bin = Path::new(python).parent().unwrap().display().to_string();
    let status = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["next", "verify", "--result", "now.json"])
        .current_dir(root)
        .env_clear()
        .env("HOME", base)
        .env("PATH", format!("{bin}:/usr/bin:/bin"))
        .env("GIT_CEILING_DIRECTORIES", base)
        .envs(environment)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .unwrap();
    assert!(status.code().is_some());
    let result: Value =
        serde_json::from_str(&fs::read_to_string(root.join("now.json")).unwrap()).unwrap();
    result["checks"]
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

#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn one_kind_of_input_moves_the_scopes_frozen_moved() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let stricter: BTreeMap<_, _> = STRICTER.iter().copied().collect();
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        fs::write(base.join(".hg"), "").unwrap();
        let root = base.join("project");
        fs::create_dir_all(root.join("src")).unwrap();
        fs::create_dir_all(root.join("tests")).unwrap();
        fs::write(root.join("src/app.py"), oracle["source"].as_str().unwrap()).unwrap();
        fs::write(
            root.join("tests/test_app.py"),
            oracle["test"].as_str().unwrap(),
        )
        .unwrap();
        fs::write(
            root.join("pyproject.toml"),
            oracle["pyproject"].as_str().unwrap(),
        )
        .unwrap();
        fs::write(root.join("ici.toml"), config(&python)).unwrap();
        let before = scopes(&root, &base, &python, &BTreeMap::new());
        for (path, text) in case["files"].as_object().unwrap() {
            fs::write(root.join(path), text.as_str().unwrap()).unwrap();
        }
        let environment: BTreeMap<String, String> = case["environment"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), value.as_str().unwrap().to_owned()))
            .collect();
        let after = scopes(&root, &base, &python, &environment);
        let checks: Vec<_> = after.keys().cloned().collect();
        assert_eq!(
            serde_json::json!(checks),
            case["checks"],
            "{name}: the same checks"
        );
        let moved: BTreeSet<String> = after
            .iter()
            .filter(|(check, digest)| before.get(*check) != Some(*digest))
            .map(|(check, _)| check.clone())
            .collect();
        let mut expected: BTreeSet<String> = case["moved_scopes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|check| check.as_str().unwrap().to_owned())
            .collect();
        expected.extend(
            stricter
                .get(name.as_str())
                .into_iter()
                .flat_map(|checks| checks.iter().map(|check| (*check).to_owned())),
        );
        assert_eq!(moved, expected, "{name}");
    }
}

/// pytest searches above the project root when nothing inside configures it. A configuration
/// there cannot be in the snapshot, so the check says so and claims no complete evidence.
#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn a_pytest_configuration_above_the_project_leaves_the_suite_incomplete() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::create_dir_all(root.join("tests")).unwrap();
    fs::write(root.join("src/app.py"), oracle["source"].as_str().unwrap()).unwrap();
    fs::write(
        root.join("tests/test_app.py"),
        format!(
            "import sys\nsys.path.insert(0, 'src')\n{}",
            oracle["test"].as_str().unwrap()
        ),
    )
    .unwrap();
    fs::write(
        root.join("pyproject.toml"),
        "[project]\nname='fixture'\nversion='1'\nrequires-python='>=3.10'\n",
    )
    .unwrap();
    fs::write(root.join("ici.toml"), config(&python)).unwrap();
    let complete = |root: &Path| {
        scopes(root, &base, &python, &BTreeMap::new());
        let result: Value =
            serde_json::from_str(&fs::read_to_string(root.join("now.json")).unwrap()).unwrap();
        let checks: BTreeMap<String, bool> = result["checks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|check| {
                (
                    check["check_id"].as_str().unwrap().to_owned(),
                    check["evidence_complete"] == true,
                )
            })
            .collect();
        (checks, result.to_string())
    };
    let (inside, _) = complete(&root);
    assert!(
        inside["python.test"] && inside["python.coverage"],
        "{inside:?}"
    );
    fs::write(base.join("pytest.ini"), "[pytest]\nmarkers = slow\n").unwrap();
    let (outside, text) = complete(&root);
    assert!(
        !outside["python.test"] && !outside["python.coverage"],
        "{outside:?}"
    );
    assert!(outside["python.line"], "{outside:?}");
    assert!(
        text.contains("pytest may read a configuration file outside the project"),
        "{text}"
    );
}
