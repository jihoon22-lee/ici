//! A tool a `next` run starts gets the caller's whole environment, as frozen's subprocesses did
//! (decisions.md "도구 실행 환경"): the effective environment of each task is hashed into its
//! check's scope and cache identity, the explicit configuration selections it carries
//! (`COVERAGE_RCFILE`, `-c` in `PYTEST_ADDOPTS`) are read before the default candidates, and no
//! value is written into a result, plan, report or ici state.
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

const SOURCE: &str = "def called(value):\n    if value:\n        return 1\n    return 0\n";
const EXTRA: &str = "def extra(value):\n    if value:\n        return 2\n    return 0\n";
const TEST: &str = "import app\n\n\ndef test_called():\n    assert app.called(True) == 1\n";
const EXTRA_TEST: &str = "import extra\n\n\ndef test_extra():\n    assert extra.extra(True) == 2\n";
const PYPROJECT: &str = "[project]\nname='fixture'\nversion='1'\nrequires-python='>=3.10'\n[tool.pytest.ini_options]\npythonpath=['src']\n";

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON with pytest, coverage and mypy")
}

fn config(python: &str) -> String {
    format!(
        "schema_version=1\n[workspace]\nname='fixture'\n[[components]]\nid='app'\nroot='.'\nlanguages=['python']\nsources=['src/**/*.py']\n[components.python]\nexecutable={python:?}\ntest_tools='project'\ntest_paths=['tests']\ntype_provider='mypy'\n"
    )
}

struct Fixture {
    _directory: tempfile::TempDir,
    base: PathBuf,
    root: PathBuf,
    python: String,
}

impl Fixture {
    fn new(files: &[(&str, &str)]) -> Self {
        let python = python();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        fs::write(base.join(".hg"), "").unwrap();
        let root = base.join("project");
        let mut all = vec![
            ("src/app.py", SOURCE),
            ("tests/test_app.py", TEST),
            ("pyproject.toml", PYPROJECT),
        ];
        all.extend_from_slice(files);
        for (path, text) in all {
            let path = root.join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text).unwrap();
        }
        fs::write(root.join("ici.toml"), config(&python)).unwrap();
        Self {
            _directory: directory,
            base,
            root,
            python,
        }
    }

    fn write(&self, path: &str, text: &str) {
        let path = self.root.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }

    /// `ici <args>` with exactly this environment beside the fixed HOME/PATH; its output text.
    fn ici(&self, environment: &[(&str, &str)], args: &[&str]) -> (i32, String) {
        let bin = Path::new(&self.python)
            .parent()
            .unwrap()
            .display()
            .to_string();
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(args)
            .current_dir(&self.root)
            .env_clear()
            .env("HOME", &self.base)
            .env("PATH", format!("{bin}:/usr/bin:/bin"))
            .env("GIT_CEILING_DIRECTORIES", &self.base)
            .envs(environment.iter().copied())
            .stdin(Stdio::null())
            .output()
            .unwrap();
        (
            output.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&output.stdout).into_owned()
                + &String::from_utf8_lossy(&output.stderr),
        )
    }

    fn verify(&self, environment: &[(&str, &str)], result: &str) -> Value {
        let (code, text) = self.ici(environment, &["next", "verify", "--result", result]);
        let path = self.root.join(result);
        assert!(path.is_file(), "{code}: {text}");
        serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
    }
}

fn checks(result: &Value) -> BTreeMap<String, &Value> {
    result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| (check["check_id"].as_str().unwrap().to_owned(), check))
        .collect()
}

fn moved(before: &Value, after: &Value) -> BTreeSet<String> {
    let before = checks(before);
    checks(after)
        .into_iter()
        .filter(|(id, check)| {
            before.get(id).map(|old| &old["scope_digest"]) != Some(&check["scope_digest"])
        })
        .map(|(id, _)| id)
        .collect()
}

fn rules(result: &Value, rule: &str) -> usize {
    fingerprints(result, rule).len()
}

fn fingerprints(result: &Value, rule: &str) -> Vec<String> {
    result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["rule_id"] == rule)
        .map(|finding| finding["fingerprint"].as_str().unwrap().to_owned())
        .collect()
}

fn complete(result: &Value, check: &str) -> bool {
    checks(result)[check]["evidence_complete"] == true
}

/// coverage's line measurement: (percent, statements).
fn lines(result: &Value) -> (f64, u64) {
    let metric = result["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .find(|metric| metric["name"] == "coverage.lines")
        .unwrap_or_else(|| panic!("no line coverage: {result}"));
    (
        metric["value"].as_f64().unwrap(),
        metric["denominator"].as_u64().unwrap(),
    )
}

/// The checks that start a tool: their invocation, and so their environment, is in their scope.
const TOOL_CHECKS: [&str; 6] = [
    "python.compat-runtime",
    "python.coverage",
    "python.format",
    "python.lint",
    "python.test",
    "python.type",
];

#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn the_caller_environment_reaches_tools_and_moves_only_their_scopes() {
    let fixture = Fixture::new(&[]);
    let first = fixture.verify(&[], "first.json");
    let same = fixture.verify(&[], "same.json");
    assert!(moved(&first, &same).is_empty());
    let other = fixture.verify(&[("ICI_FIXTURE_UNRELATED", "1")], "other.json");
    assert_eq!(
        moved(&first, &other),
        TOOL_CHECKS
            .iter()
            .map(|check| (*check).to_owned())
            .collect()
    );
    // A test that reads a variable only the caller sets sees it.
    fixture.write(
        "tests/test_environment.py",
        "import os\n\n\ndef test_variable():\n    assert os.environ['ICI_FIXTURE_REQUIRED'] == 'on'\n",
    );
    let without = fixture.verify(&[], "without.json");
    assert_eq!(rules(&without, "pytest.failed"), 1, "{without}");
    let with = fixture.verify(&[("ICI_FIXTURE_REQUIRED", "on")], "with.json");
    assert_eq!(rules(&with, "pytest.failed"), 0, "{with}");
}

/// `COVERAGE_RCFILE` reaches coverage, which then reads that file instead of `.coveragerc`: an
/// omit there leaves a declared source unmeasured (native never reports it as measured), an edit
/// there moves the coverage scope, and a file outside the project leaves the check incomplete
/// without naming the value. Coverage counts the declared sources only (8 statements, 6 run), not
/// the test files frozen also counted (decisions.md "next Python coverage의 측정 범위").
#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn coverage_rcfile_in_the_environment_is_read_and_tracked() {
    let fixture = Fixture::new(&[
        ("src/extra.py", EXTRA),
        ("tests/test_extra.py", EXTRA_TEST),
        ("other.coveragerc", "[run]\nomit = src/extra.py\n"),
        (".coveragerc", "[run]\nbranch = True\n"),
    ]);
    let plain = fixture.verify(&[], "plain.json");
    assert_eq!(lines(&plain), (75.0, 8), "{plain}");
    assert!(complete(&plain, "python.coverage"), "{plain}");
    let selected = [("COVERAGE_RCFILE", "other.coveragerc")];
    let first = fixture.verify(&selected, "first.json");
    assert!(!complete(&first, "python.coverage"), "{first}");
    assert!(
        first["gate"]["reasons"]
            .to_string()
            .contains("coverage report omits declared source files"),
        "{first}"
    );
    // .coveragerc is not read under COVERAGE_RCFILE; other.coveragerc is.
    fixture.write(".coveragerc", "[run]\nbranch = False\n");
    let unread = fixture.verify(&selected, "unread.json");
    assert!(
        moved(&first, &unread).is_empty(),
        "{:?}",
        moved(&first, &unread)
    );
    fixture.write("other.coveragerc", "[run]\nbranch = True\n");
    let edited = fixture.verify(&selected, "edited.json");
    assert_eq!(
        moved(&unread, &edited),
        BTreeSet::from(["python.coverage".to_owned()])
    );
    assert_eq!(lines(&edited), (75.0, 8), "{edited}");
    assert!(complete(&edited, "python.coverage"), "{edited}");
    fs::write(
        fixture.base.join("outside-marker-91c.coveragerc"),
        "[run]\n",
    )
    .unwrap();
    let outside = fixture.verify(
        &[("COVERAGE_RCFILE", "../outside-marker-91c.coveragerc")],
        "outside.json",
    );
    assert!(!complete(&outside, "python.coverage"), "{outside}");
    assert!(complete(&outside, "python.test"), "{outside}");
    let text = outside.to_string();
    assert!(
        text.contains(ici_cli::inputs::OUTSIDE_COVERAGE_RCFILE),
        "{text}"
    );
    assert!(!text.contains("outside-marker-91c"), "{text}");
}

/// `-c` in `PYTEST_ADDOPTS` makes pytest read that file instead of searching: an edit there moves
/// the test and coverage scopes; a file outside the project leaves them incomplete.
#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn an_explicit_pytest_configuration_is_read_and_tracked() {
    let fixture = Fixture::new(&[(
        "conf dir/other.ini",
        "[pytest]\npythonpath = ../src\nmarkers = slow\n",
    )]);
    let selected = [("PYTEST_ADDOPTS", "-c 'conf dir/other.ini'")];
    let first = fixture.verify(&selected, "first.json");
    assert_eq!(
        checks(&first)["python.test"]["state"],
        "SUCCEEDED",
        "{first}"
    );
    assert!(complete(&first, "python.test"), "{first}");
    fixture.write(
        "conf dir/other.ini",
        "[pytest]\npythonpath = ../src\nmarkers = slow fast\n",
    );
    let edited = fixture.verify(&selected, "edited.json");
    assert_eq!(
        moved(&first, &edited),
        BTreeSet::from(["python.coverage".to_owned(), "python.test".to_owned()])
    );
    fs::write(
        fixture.base.join("outside-marker-5d2.ini"),
        "[pytest]\npythonpath = project/src\n",
    )
    .unwrap();
    let outside = fixture.verify(
        &[("PYTEST_ADDOPTS", "-c ../outside-marker-5d2.ini")],
        "outside.json",
    );
    assert!(!complete(&outside, "python.test"), "{outside}");
    assert!(!complete(&outside, "python.coverage"), "{outside}");
    let text = outside.to_string();
    assert!(
        text.contains(ici_cli::inputs::OUTSIDE_PYTEST_ADDOPTS_CONFIG),
        "{text}"
    );
    assert!(!text.contains("outside-marker-5d2"), "{text}");
}

/// `MYPYPATH` reaches an explicitly selected mypy: a stub there resolves an import mypy reported
/// missing. mypy's configuration evidence is never complete, so this is not closure evidence.
#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn mypypath_in_the_environment_reaches_mypy() {
    let fixture = Fixture::new(&[
        (
            "src/typed.py",
            "import stubbed\n\nVALUE: int = stubbed.VALUE\n",
        ),
        ("stubs/stubbed.pyi", "VALUE: int\n"),
    ]);
    let missing = |result: &Value| {
        result["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| {
                finding["task_id"] == "app.python.type" && finding.to_string().contains("stubbed")
            })
    };
    let without = fixture.verify(&[], "without.json");
    assert!(missing(&without), "{without}");
    let with = fixture.verify(&[("MYPYPATH", "stubs")], "with.json");
    assert!(!missing(&with), "{with}");
    assert!(moved(&without, &with).contains("python.type"));
    assert!(!complete(&with, "python.type"));
}

/// A synthetic secret in the caller's environment, also inside a variable ici copies into a
/// task's overlay, is never written by ici: not in the result, plan, report, ici state or console,
/// whether the tools succeed, fail or cannot start.
#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn a_synthetic_secret_in_the_environment_is_never_written() {
    const MARKER: &str = "ici-synthetic-secret-3b9e";
    let environment = [
        ("ICI_FIXTURE_TOKEN", MARKER),
        ("PYTEST_ADDOPTS", "-o markers=ici-synthetic-secret-3b9e"),
    ];
    // The failing test prints the value without its variable's name: only the remembered value
    // can be masked.
    const ECHO: &str =
        "import os\n\n\ndef test_failing():\n    assert False, os.environ['ICI_FIXTURE_TOKEN']\n";
    let fixture = Fixture::new(&[("tests/test_failing.py", ECHO)]);
    let mut console = String::new();
    for args in [
        &["next", "plan", "--json"][..],
        &["next", "verify", "--result", "run.json"][..],
        &[
            "next",
            "report",
            "--result",
            "run.json",
            "--out",
            "page.html",
        ][..],
    ] {
        console += &fixture.ici(&environment, args).1;
    }
    // A tool that cannot start: an executable the system refuses to run.
    let refused = fixture.root.join("refused-python");
    fs::write(&refused, "not a program\n").unwrap();
    fs::set_permissions(&refused, fs::Permissions::from_mode(0o755)).unwrap();
    fs::write(
        fixture.root.join("ici.toml"),
        config(&refused.display().to_string()),
    )
    .unwrap();
    console += &fixture
        .ici(
            &environment,
            &["next", "verify", "--result", "refused.json"],
        )
        .1;
    // The legacy sanitize run copies the caller's PYTEST_ADDOPTS into its task's overlay.
    let legacy = Fixture::new(&[
        ("tests/test_failing.py", ECHO),
        (
            "ici.toml",
            "[project]\ntype = 'python'\nsource_dirs = ['src']\n[engines.sanitize]\nenabled = true\n",
        ),
    ]);
    fs::write(
        legacy.root.join("ici.toml"),
        "[project]\ntype = 'python'\nsource_dirs = ['src']\n[engines.sanitize]\nenabled = true\n",
    )
    .unwrap();
    let legacy_environment = [
        ("ICI_FIXTURE_TOKEN", MARKER),
        ("ICI_PYTHON", legacy.python.as_str()),
        ("PYTEST_ADDOPTS", "-o markers=ici-synthetic-secret-3b9e"),
    ];
    console += &legacy
        .ici(
            &legacy_environment,
            &["verify", "--profile", "deep", "--report"],
        )
        .1;
    assert!(
        fs::read_dir(&legacy.root).unwrap().any(|entry| entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with("_report.json")),
        "{console}"
    );
    assert!(fixture.root.join("run.json").is_file(), "{console}");
    // The tool did print the value: the legacy report carries pytest's output, masked.
    let report = fs::read_dir(&legacy.root)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .find(|path| path.to_string_lossy().ends_with("_report.json"))
        .unwrap();
    assert!(
        fs::read_to_string(&report).unwrap().contains(&format!(
            "AssertionError: {}",
            ici_execution::redaction::REDACTED
        )),
        "the failing test's message did not reach the report"
    );
    assert!(!console.contains(MARKER), "{console}");
    let mut written = vec![];
    let mut pending = vec![fixture.base.clone(), legacy.base.clone()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                pending.push(path);
            } else if fs::read(&path)
                .unwrap()
                .windows(MARKER.len())
                .any(|window| window == MARKER.as_bytes())
            {
                written.push(path);
            }
        }
    }
    assert!(written.is_empty(), "{written:?}");
}

/// A declared CMake build that needs a variable only the caller sets: before the whole
/// environment reached a task, the build could not see it and failed.
#[test]
#[ignore = "needs real CMake and g++ (ICI_TEST_CMAKE, ICI_TEST_CXX)"]
fn a_build_reads_a_variable_only_the_caller_sets() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap().join("project");
    let files = [
        (
            "CMakeLists.txt",
            "cmake_minimum_required(VERSION 3.16)\nproject(demo CXX)\nif(NOT \"$ENV{ICI_FIXTURE_BUILD_FLAG}\" STREQUAL \"on\")\n  message(FATAL_ERROR \"ICI_FIXTURE_BUILD_FLAG is not set\")\nendif()\nenable_testing()\nset(CMAKE_EXPORT_COMPILE_COMMANDS ON)\nadd_library(lib STATIC src/lib.cpp)\nadd_executable(unit tests/unit.cpp)\ntarget_link_libraries(unit lib)\nadd_test(NAME unit COMMAND unit)\n",
        ),
        ("src/lib.cpp", "int answer() { return 42; }\n"),
        (
            "tests/unit.cpp",
            "int answer();\nint main() { return answer() == 42 ? 0 : 1; }\n",
        ),
        (
            "ici.toml",
            "schema_version = 2\nname = \"demo\"\nversion = \"v1.0.0\"\n[builds.native]\nsystem = \"cmake\"\nproject = \"CMakeLists.txt\"\ndirectory = \"out\"\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"cpp\"]\nbuild = \"native\"\nsources = [\"src/**\"]\n",
        ),
        (".hg", ""),
    ];
    for (path, text) in files {
        let path = root.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    let directory_of = |variable: &str| {
        PathBuf::from(std::env::var(variable).unwrap())
            .parent()
            .unwrap()
            .display()
            .to_string()
    };
    let path = format!(
        "{}:{}:/usr/bin:/bin",
        directory_of("ICI_TEST_CMAKE"),
        directory_of("ICI_TEST_CXX")
    );
    let test = |environment: &[(&str, &str)]| {
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .arg("test")
            .current_dir(&root)
            .env_clear()
            .env("HOME", &root)
            .env("PATH", &path)
            .envs(environment.iter().copied())
            .stdin(Stdio::null())
            .output()
            .unwrap();
        let result: Value = serde_json::from_str(
            &fs::read_to_string(root.join(".ici/rust-v1/result.json")).unwrap(),
        )
        .unwrap_or_else(|_| panic!("{output:?}"));
        let states: BTreeMap<String, String> = checks(&result)
            .into_iter()
            .map(|(id, check)| (id, check["state"].as_str().unwrap().to_owned()))
            .collect();
        (output.status.code(), states)
    };
    let (code, states) = test(&[]);
    assert_ne!(code, Some(0), "{states:?}");
    assert_ne!(
        states.get("cpp.test").map(String::as_str),
        Some("SUCCEEDED")
    );
    fs::remove_dir_all(root.join("out")).ok();
    let (code, states) = test(&[("ICI_FIXTURE_BUILD_FLAG", "on")]);
    assert_eq!(code, Some(0), "{states:?}");
    assert_eq!(states["cpp.test"], "SUCCEEDED", "{states:?}");
}

/// What a variable that changes every run (a CI run id) does to a `next` baseline, measured: the
/// checks that start a tool get another scope and no tool result is reused, but a finding found
/// again stays `unchanged` and a new one is `new`. A tool finding that disappears is `carried`,
/// not `resolved`: the newer run holds no complete check with the old scope, so it cannot confirm
/// the fix (the same fix under the same run id is `resolved`). A finding of ici's own analysis is
/// resolved either way. A carried finding is history, not a finding of the newer run: the newer
/// run neither lists it nor counts it in its gate (decisions.md "도구 실행 환경").
#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff beside it (ICI_TEST_PYTHON)"]
fn a_per_run_variable_cannot_resolve_tool_findings_but_keeps_them_comparable() {
    const FAILING: &str = "import os\nimport app\n\n\ndef test_called():\n    assert app.called(True) == 1\n\n\ndef test_known():\n    assert app.called(False) == 1\n\n\ndef test_mode():\n    assert os.environ.get('FIXTURE_MODE') != 'fail'\n";
    const UNSAFE: &str = "def called(value):\n    if value:\n        return 1\n    return 0\n\n\ndef unsafe(text):\n    return eval(text)\n";
    let fixture = Fixture::new(&[
        ("tests/test_app.py", FAILING),
        ("src/app.py", UNSAFE),
        ("ruff.toml", "line-length = 100\n"),
    ]);
    let compare = |environment: &[(&str, &str)]| {
        let (_, text) = fixture.ici(
            environment,
            &[
                "next",
                "verify",
                "--result",
                "now.json",
                "--baseline",
                "first.json",
            ],
        );
        let now: Value =
            serde_json::from_str(&fs::read_to_string(fixture.root.join("now.json")).unwrap())
                .unwrap_or_else(|_| panic!("{text}"));
        now
    };
    let bucket = |now: &Value, name: &str| -> BTreeSet<String> {
        now["baseline"][name]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap().to_owned())
            .collect()
    };
    let reused = |now: &Value| -> BTreeSet<String> {
        now["execution"]["reused_task_ids"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap().to_owned())
            .collect()
    };
    let first = fixture.verify(&[("CI_RUN_ID", "1")], "first.json");
    let failed = fingerprints(&first, "pytest.failed");
    let unsafe_call = fingerprints(&first, "security.evalexec");
    assert_eq!((failed.len(), unsafe_call.len()), (1, 1), "{first}");
    let (failed, unsafe_call) = (failed[0].clone(), unsafe_call[0].clone());
    let both = BTreeSet::from([failed.clone(), unsafe_call.clone()]);

    let same = compare(&[("CI_RUN_ID", "1")]);
    assert!(moved(&first, &same).is_empty());
    assert!(reused(&same).contains("app.python.format"), "{same}");
    assert!(reused(&same).contains("app.python.security"), "{same}");
    assert_eq!(bucket(&same, "unchanged"), both);
    assert!(bucket(&same, "new").is_empty());

    let other_run = compare(&[("CI_RUN_ID", "2")]);
    assert_eq!(
        moved(&first, &other_run),
        TOOL_CHECKS
            .iter()
            .map(|check| (*check).to_owned())
            .collect()
    );
    assert!(
        !reused(&other_run).contains("app.python.format"),
        "{other_run}"
    );
    assert!(
        reused(&other_run).contains("app.python.security"),
        "{other_run}"
    );
    assert_eq!(bucket(&other_run, "unchanged"), both);
    assert!(bucket(&other_run, "new").is_empty());

    let changed = compare(&[("CI_RUN_ID", "1"), ("FIXTURE_MODE", "fail")]);
    assert_eq!(bucket(&changed, "new").len(), 1, "{changed}");
    assert!(bucket(&changed, "unchanged").contains(&failed));

    fixture.write(
        "tests/test_app.py",
        &FAILING.replace("called(False) == 1", "called(False) == 0"),
    );
    fixture.write("src/app.py", &UNSAFE.replace("eval(text)", "int(text)"));
    let fixed = compare(&[("CI_RUN_ID", "1")]);
    assert_eq!(bucket(&fixed, "resolved"), both);
    assert!(bucket(&fixed, "carried").is_empty());
    let fixed_other_run = compare(&[("CI_RUN_ID", "2")]);
    assert_eq!(
        bucket(&fixed_other_run, "resolved"),
        BTreeSet::from([unsafe_call])
    );
    assert_eq!(
        bucket(&fixed_other_run, "carried"),
        BTreeSet::from([failed.clone()])
    );
    // History, not a finding of this run: not listed, not in its gate.
    assert!(!fixed_other_run["findings"].to_string().contains(&failed));
    assert_eq!(
        fixed_other_run["gate"]["has_violations"], false,
        "{fixed_other_run}"
    );
    let (diff_code, diff) =
        fixture.ici(&[], &["next", "diff", "first.json", "--result", "now.json"]);
    assert!(
        diff.starts_with("0 new, 0 unchanged, 1 resolved, 1 carried"),
        "{diff}"
    );
    assert!(diff.contains("not fixed code"), "{diff}");
    // `next diff` reports; it does not gate on new findings (frozen `cmd_diff` the same).
    assert_eq!(diff_code, 0);
}
