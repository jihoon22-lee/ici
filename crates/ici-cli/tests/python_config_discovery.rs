//! The configuration files ici takes a Python run's pytest and coverage to read
//! (`inputs::python_configuration`), against the file the real tools chose in the same layout
//! and environment. ici's walk is a superset of pytest 9 `determine_setup`/`locate_config` and
//! coverage 7 `config_files_to_try`, not their resolution: the file a real tool read must be among
//! ici's inputs, or ici must say the run may read one it cannot hold (decisions.md "next check
//! scope의 설정 파일 입력", "도구 실행 환경").
use ici_cli::inputs::{PythonRuns, python_configuration};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

struct Layout {
    name: &'static str,
    /// Files under the project; a path starting with `../` lies beside it, outside.
    files: &'static [(&'static str, &'static str)],
    targets: &'static [&'static str],
    environment: &'static [(&'static str, &'static str)],
}

const TEST: &str = "def test_one():\n    pass\n";
const SECTION: &str = "[tool.pytest.ini_options]\nmarkers = ['slow']\n";

const PYTEST_LAYOUTS: &[Layout] = &[
    Layout {
        name: "sibling targets: the common ancestor's walk, not each target's own file",
        files: &[
            ("tests/a/test_a.py", TEST),
            ("tests/a/pytest.ini", "[pytest]\n"),
            ("tests/b/test_b.py", TEST),
            ("tests/b/pytest.ini", "[pytest]\n"),
            ("pyproject.toml", SECTION),
        ],
        targets: &["tests/a", "tests/b"],
        environment: &[],
    },
    Layout {
        name: "nested target below a configured directory",
        files: &[
            ("tests/unit/test_a.py", TEST),
            ("tests/pytest.ini", "[pytest]\n"),
            ("pyproject.toml", SECTION),
        ],
        targets: &["tests/unit"],
        environment: &[],
    },
    Layout {
        name: "node id target",
        files: &[
            ("tests/test_a.py", TEST),
            ("tox.ini", "[pytest]\nmarkers = slow\n"),
        ],
        targets: &["tests/test_a.py::test_one"],
        environment: &[],
    },
    Layout {
        name: "shared files without a pytest section: pyproject.toml is the fallback",
        files: &[
            ("tests/test_a.py", TEST),
            ("tox.ini", "[testenv]\ncommands = pytest\n"),
            ("setup.cfg", "[metadata]\nname = fixture\n"),
            ("pyproject.toml", "[project]\nname = 'fixture'\n"),
        ],
        targets: &["tests"],
        environment: &[],
    },
    Layout {
        name: "toml mode [tool.pytest]",
        files: &[
            ("tests/test_a.py", TEST),
            (
                "tests/pyproject.toml",
                "[tool.pytest]\nminversion = '9.0'\n",
            ),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[],
    },
    Layout {
        name: "an empty [tool.pytest] table does not stop the search",
        files: &[
            ("tests/test_a.py", TEST),
            ("tests/pyproject.toml", "[tool.pytest]\n"),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[],
    },
    Layout {
        name: "a section header inside a TOML string does not stop the search",
        files: &[
            ("tests/test_a.py", TEST),
            (
                "tests/pyproject.toml",
                "[project]\ndescription = \"\"\"\n[tool.pytest.ini_options]\n\"\"\"\n",
            ),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[],
    },
    Layout {
        name: "setup.cfg [tool:pytest]",
        files: &[
            ("tests/test_a.py", TEST),
            ("setup.cfg", "[tool:pytest]\nmarkers = slow\n"),
            ("pyproject.toml", SECTION),
        ],
        targets: &["tests"],
        environment: &[],
    },
    Layout {
        name: "tox.ini [pytest] with a comment on its header line",
        files: &[
            ("tests/test_a.py", TEST),
            ("tests/tox.ini", "[pytest] # tests\nmarkers = slow\n"),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[],
    },
    Layout {
        name: "explicit -c in PYTEST_ADDOPTS",
        files: &[
            ("tests/test_a.py", TEST),
            ("conf/other.ini", "[pytest]\nmarkers = slow\n"),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[("PYTEST_ADDOPTS", "-c conf/other.ini")],
    },
    Layout {
        name: "explicit --config-file= in PYTEST_ADDOPTS, the last one wins",
        files: &[
            ("tests/test_a.py", TEST),
            ("conf/first.ini", "[pytest]\n"),
            ("conf/other.ini", "[pytest]\nmarkers = slow\n"),
        ],
        targets: &["tests"],
        environment: &[(
            "PYTEST_ADDOPTS",
            "-p no:cacheprovider -c conf/first.ini --config-file=conf/other.ini",
        )],
    },
    Layout {
        name: "explicit -c with a quoted path holding spaces",
        files: &[
            ("tests/test_a.py", TEST),
            ("conf dir/my config.ini", "[pytest]\nmarkers = slow\n"),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[("PYTEST_ADDOPTS", "-c 'conf dir/my config.ini'")],
    },
    Layout {
        name: "explicit -c with an escaped space",
        files: &[
            ("tests/test_a.py", TEST),
            ("conf dir/other.ini", "[pytest]\nmarkers = slow\n"),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[("PYTEST_ADDOPTS", "-c conf\\ dir/other.ini")],
    },
    Layout {
        name: "a positional path in PYTEST_ADDOPTS moves pytest's common ancestor",
        files: &[
            ("tests/unit/test_a.py", TEST),
            ("tests/unit/pytest.ini", "[pytest]\n"),
            ("tests/other/test_b.py", TEST),
            ("pytest.ini", "[pytest]\nmarkers = slow\n"),
        ],
        targets: &["tests/unit"],
        environment: &[("PYTEST_ADDOPTS", "tests/other")],
    },
    Layout {
        name: "-k and -m operands that name directories are not paths",
        files: &[
            ("tests/unit/test_a.py", TEST),
            ("tests/unit/pytest.ini", "[pytest]\nmarkers = slow\n"),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests/unit"],
        environment: &[("PYTEST_ADDOPTS", "-k tests -m tests")],
    },
    Layout {
        name: "explicit -c outside the project",
        files: &[
            ("tests/test_a.py", TEST),
            ("../outside.ini", "[pytest]\nmarkers = slow\n"),
            ("pytest.ini", "[pytest]\n"),
        ],
        targets: &["tests"],
        environment: &[("PYTEST_ADDOPTS", "-c ../outside.ini")],
    },
    Layout {
        name: "nothing inside configures pytest, a file above the project does",
        files: &[("tests/test_a.py", TEST), ("../pytest.ini", "[pytest]\n")],
        targets: &["tests"],
        environment: &[],
    },
];

const COVERAGE_LAYOUTS: &[Layout] = &[
    Layout {
        name: "coverage .coveragerc",
        files: &[
            (".coveragerc", "[run]\nbranch = True\n"),
            ("pyproject.toml", ""),
        ],
        targets: &[],
        environment: &[],
    },
    Layout {
        name: "coverage [tool.coverage] in pyproject.toml",
        files: &[("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")],
        targets: &[],
        environment: &[],
    },
    Layout {
        name: "COVERAGE_RCFILE relative to the run",
        files: &[
            ("conf/coverage.rc", "[run]\nbranch = True\n"),
            (".coveragerc", "[run]\n"),
        ],
        targets: &[],
        environment: &[("COVERAGE_RCFILE", "conf/coverage.rc")],
    },
    Layout {
        name: "COVERAGE_RCFILE outside the project",
        files: &[("../coverage.rc", "[run]\nbranch = True\n")],
        targets: &[],
        environment: &[("COVERAGE_RCFILE", "../coverage.rc")],
    },
];

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON with pytest and coverage")
}

fn project(layout: &Layout) -> (tempfile::TempDir, PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("base");
    let root = base.join("project");
    fs::create_dir_all(&root).unwrap();
    for (path, text) in layout.files {
        let path = match path.strip_prefix("../") {
            Some(outside) => base.join(outside),
            None => root.join(path),
        };
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    (directory, root)
}

fn environment(layout: &Layout, root: &Path) -> BTreeMap<String, String> {
    let mut environment: BTreeMap<String, String> = layout
        .environment
        .iter()
        .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
        .collect();
    environment.insert("HOME".into(), root.parent().unwrap().display().to_string());
    environment.insert("PATH".into(), "/usr/bin:/bin".into());
    environment
}

/// The one line of a tool's report that names the file it took, made absolute.
fn chosen(output: &str, key: &str, base: Option<&str>) -> Option<PathBuf> {
    // pytest may follow the name with a note, e.g. "(WARNING: ignoring pytest config in ...)".
    let value = output
        .lines()
        .find_map(|line| line.trim().strip_prefix(key))?;
    let value = value.split(" (").next()?.trim();
    Some(match base {
        Some(base) => Path::new(base).join(value),
        None => PathBuf::from(value),
    })
}

fn assert_covered(name: &str, root: &Path, chosen: &Path, read: &[String], unprovable: &[String]) {
    match chosen.strip_prefix(root) {
        Ok(relative) => assert!(
            read.contains(&relative.to_string_lossy().into_owned()),
            "{name}: the tool read {} and ici's inputs are {read:?}",
            chosen.display()
        ),
        Err(_) => assert!(
            !unprovable.is_empty(),
            "{name}: the tool read {} outside the project and ici claims none",
            chosen.display()
        ),
    }
}

#[test]
#[ignore = "needs a Python with pytest and coverage (ICI_TEST_PYTHON)"]
fn the_configuration_real_pytest_takes_is_among_the_runs_inputs() {
    let python = python();
    for layout in PYTEST_LAYOUTS {
        let (_directory, root) = project(layout);
        let environment = environment(layout, &root);
        let targets: Vec<String> = layout.targets.iter().map(|t| (*t).to_owned()).collect();
        let configuration = python_configuration(&PythonRuns {
            root: &root,
            cwd: &root,
            targets: &targets,
            pytest: vec![environment.clone()],
            coverage: vec![],
        });
        let output = Command::new(&python)
            .args([
                "-I",
                "-m",
                "pytest",
                "--collect-only",
                "-p",
                "no:cacheprovider",
            ])
            .args(&targets)
            .current_dir(&root)
            .env_clear()
            .envs(&environment)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        let text = String::from_utf8_lossy(&output.stdout).into_owned()
            + &String::from_utf8_lossy(&output.stderr);
        let rootdir = text
            .lines()
            .find_map(|line| line.strip_prefix("rootdir: "))
            .unwrap_or_else(|| panic!("{}: no rootdir line: {text}", layout.name))
            .trim()
            .to_owned();
        let read: Vec<String> = configuration.read.iter().cloned().collect();
        let file = chosen(&text, "configfile: ", Some(&rootdir))
            .unwrap_or_else(|| panic!("{}: pytest named no configuration: {text}", layout.name));
        assert_covered(layout.name, &root, &file, &read, &configuration.unprovable);
        // A file the explicit selection replaced is not read by pytest; ici need not hold it,
        // but an explicit choice inside the project leaves nothing unprovable.
        // Only a choice outside the project or above it leaves something unprovable here.
        if !layout.name.contains("outside") && !layout.name.contains("above") {
            assert!(
                configuration.unprovable.is_empty(),
                "{}: {configuration:?}",
                layout.name
            );
        }
    }
}

#[test]
#[ignore = "needs a Python with pytest and coverage (ICI_TEST_PYTHON)"]
fn the_configuration_real_coverage_reads_is_among_the_runs_inputs() {
    let python = python();
    for layout in COVERAGE_LAYOUTS {
        let (_directory, root) = project(layout);
        let environment = environment(layout, &root);
        let configuration = python_configuration(&PythonRuns {
            root: &root,
            cwd: &root,
            targets: &[],
            pytest: vec![],
            coverage: vec![environment.clone()],
        });
        let output = Command::new(&python)
            .args(["-I", "-m", "coverage", "debug", "config"])
            .current_dir(&root)
            .env_clear()
            .envs(&environment)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        let text = String::from_utf8_lossy(&output.stdout).into_owned();
        let read: Vec<String> = configuration.read.iter().cloned().collect();
        let file = chosen(&text, "config_files_read:", None)
            .unwrap_or_else(|| panic!("{}: coverage read no configuration: {text}", layout.name));
        // coverage prints the path as it opened it: resolve `..` against the run directory.
        let file = file
            .canonicalize()
            .unwrap_or_else(|_| root.join(&file).canonicalize().unwrap());
        assert_covered(layout.name, &root, &file, &read, &configuration.unprovable);
    }
}

/// A setup.cfg `[pytest]` section makes pytest stop with an error there; ici's walk stops on it
/// too, keeping the file among the inputs. `PYTEST_ADDOPTS` that shlex cannot split (pytest
/// fails on it too) or that leaves a path's role open is reported instead of guessed.
#[test]
fn undecidable_and_failing_configurations_stay_among_the_inputs() {
    let layout = Layout {
        name: "setup.cfg [pytest]",
        files: &[("tests/test_a.py", TEST), ("setup.cfg", "[pytest]\n")],
        targets: &["tests"],
        environment: &[],
    };
    let (_directory, root) = project(&layout);
    let targets = vec!["tests".to_owned()];
    let configuration = python_configuration(&PythonRuns {
        root: &root,
        cwd: &root,
        targets: &targets,
        pytest: vec![BTreeMap::new()],
        coverage: vec![],
    });
    assert!(configuration.read.contains("setup.cfg"));
    assert!(configuration.unprovable.is_empty(), "{configuration:?}");
    let addopts = |value: &str| {
        python_configuration(&PythonRuns {
            root: &root,
            cwd: &root,
            targets: &targets,
            pytest: vec![BTreeMap::from([(
                "PYTEST_ADDOPTS".to_owned(),
                value.to_owned(),
            )])],
            coverage: vec![],
        })
        .unprovable
    };
    // Split as pytest splits it: quoting is understood; only what shlex refuses is not.
    assert!(addopts("-k 'one or two'").is_empty());
    assert_eq!(
        addopts("-k 'one or two"),
        vec![ici_cli::inputs::UNREAD_PYTEST_ADDOPTS.to_owned()]
    );
    // A plugin option ici does not know, followed by an existing path: its value or a target.
    assert_eq!(
        addopts("--cov tests"),
        vec![ici_cli::inputs::AMBIGUOUS_PYTEST_ADDOPTS.to_owned()]
    );
    assert!(addopts("--cov=tests").is_empty());
    // An oversized shared file cannot be decided: the walk goes on past it to the root.
    fs::write(
        root.join("tests/tox.ini"),
        format!("[pytest]\n{}", "# padding\n".repeat(120_000)),
    )
    .unwrap();
    let oversized = python_configuration(&PythonRuns {
        root: &root,
        cwd: &root,
        targets: &targets,
        pytest: vec![BTreeMap::new()],
        coverage: vec![],
    });
    assert!(oversized.read.contains("tests/tox.ini"));
    assert!(oversized.read.contains("setup.cfg"), "{oversized:?}");
}
