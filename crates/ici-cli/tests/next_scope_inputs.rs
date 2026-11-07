//! Which check scopes a `next verify` result moves when one kind of input changes, against frozen
//! (`tests/rust-parity/python/capture_scope_inputs.py` → `scope-inputs-oracle.json`).
//!
//! Frozen's check scope (application/check_scope.py) hashes selected files, units, externals,
//! builds and the task invocation with the bytes of the tool configuration files among its input
//! references; not source contents and not the inherited environment. Native moves the same
//! scopes for source edits, an added source, an unrelated file, the inherited environment and a
//! new `ruff.toml`. Where a tool reads a configuration file frozen did not put among its task
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
/// mypy reads mypy.ini, setup.cfg and pyproject.toml; pytest and coverage read pytest.ini,
/// setup.cfg and pyproject.toml; the runtime check reads pyproject.toml's requires-python.
const STRICTER: &[(&str, &[&str])] = &[
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
