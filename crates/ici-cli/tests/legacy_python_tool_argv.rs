//! Frozen legacy Python tool runs replayed from
//! `tests/rust-parity/python/legacy-python-argv-oracle.json` (`capture_legacy_python_argv.py`).
//!
//! Each case keeps frozen's exit code, engine status, every target (path, status, name) and every
//! finding (rule, path, message), and what the tools were given where it changes the result:
//! - `ici test` clears the project's pytest `addopts`, so `-k`, `-x` or an unknown option set
//!   there neither deselects tests, stops early, nor turns a test failure into a tool error;
//! - `ici python-compat` hands compileall the selected sources' configured source directories
//!   (frozen `_compile_targets`), so a broken file there that discovery did not select fails it.
//!
//! The native argv adds its own isolation options (documented in decisions.md "legacy Python 도구
//! argv"); only the parts that carry these meanings are compared, never the whole argv string.
use serde_json::Value;
use std::{fs, path::Path, process::Command, process::Stdio};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/python/legacy-python-argv-oracle.json");

struct Run {
    exit: i64,
    status: String,
    targets: Vec<Value>,
    findings: Vec<Value>,
    argv: Vec<(String, Vec<String>)>,
}

/// The interpreter's own version (`<python> (3.10.21)`) is a fact of the capturing machine.
fn version_free(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(start) = rest.find(" (3.") {
        out.push_str(&rest[..start]);
        let tail = &rest[start + 2..];
        match tail.find(')') {
            Some(end) if tail[..end].chars().all(|c| c.is_ascii_digit() || c == '.') => {
                out.push_str(" (<version>)");
                rest = &tail[end + 1..];
            }
            _ => {
                out.push_str(" (");
                rest = tail;
            }
        }
    }
    out.push_str(rest);
    out
}

/// Machine paths in a value, longest first: the fixture's venv, the project, the base interpreter
/// the venv or the test Python resolves to, and the test Python itself.
type Names = Vec<(String, &'static str)>;

fn normalize(value: &Value, names: &Names) -> Value {
    match value {
        Value::String(text) => {
            let mut text = text.clone();
            for (path, name) in names {
                text = text.replace(path.as_str(), name);
            }
            Value::String(version_free(&text))
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|item| normalize(item, names)).collect())
        }
        other => other.clone(),
    }
}

/// A pip-less virtualenv over `python` holding one module only it can import (as captured).
fn venv(python: &str, base: &Path) -> String {
    let directory = base.join("venv");
    let status = Command::new(python)
        .args(["-m", "venv", "--without-pip"])
        .arg(&directory)
        .status()
        .unwrap();
    assert!(status.success());
    let site = fs::read_dir(directory.join("lib"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path()
        .join("site-packages");
    fs::write(site.join("venvonly.py"), "VALUE = 1\n").unwrap();
    directory.join("bin/python").display().to_string()
}

fn run(case: &Value, python: &str) -> Run {
    let base = tempfile::tempdir().unwrap();
    let base_path = base.path().canonicalize().unwrap();
    let root = base_path.join("project");
    let launched = if case["venv"] == true {
        venv(python, &base_path)
    } else {
        python.to_owned()
    };
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut text = text.as_str().unwrap().to_owned();
        if name == "ici.toml" {
            text.push_str(
                &case["engine"]
                    .as_str()
                    .unwrap_or("")
                    .replace("<venv-python>", &launched),
            );
        }
        fs::write(path, text).unwrap();
    }
    let bin = Path::new(python).parent().unwrap().display().to_string();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args([case["command"].as_str().unwrap(), "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", base.path())
        .env("PATH", format!("{bin}:/usr/bin:/bin"))
        .env("ICI_PYTHON", &launched)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let reports: Vec<_> = fs::read_dir(&root)
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.to_string_lossy().ends_with("_report.json"))
        .collect();
    assert_eq!(
        reports.len(),
        1,
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_str(&fs::read_to_string(&reports[0]).unwrap()).unwrap();
    let names: Names = vec![
        (
            base_path.join("venv/bin/python").display().to_string(),
            "<venv-python>",
        ),
        (root.display().to_string(), "<root>"),
        (
            Path::new(python)
                .canonicalize()
                .unwrap()
                .display()
                .to_string(),
            "<base-python>",
        ),
        (python.to_owned(), "<python>"),
        (
            format!("{}/", Path::new(python).parent().unwrap().display()),
            "<bin>/",
        ),
    ];
    let row = |value: &Value, keys: &[&str]| {
        Value::Array(
            keys.iter()
                .map(|key| {
                    normalize(
                        &Value::String(value[*key].as_str().unwrap_or("").into()),
                        &names,
                    )
                })
                .collect(),
        )
    };
    let mut targets: Vec<_> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| row(target, &["file_path", "status", "target_name"]))
        .collect();
    targets.sort_by_key(ToString::to_string);
    let mut findings: Vec<_> = report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| row(finding, &["rule_id", "file_path", "message"]))
        .collect();
    findings.sort_by_key(ToString::to_string);
    let argv = report["tool_evidence"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|record| {
            (
                record["name"].as_str().unwrap_or("").to_owned(),
                record["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|part| normalize(part, &names).as_str().unwrap().to_owned())
                    .collect(),
            )
        })
        .collect();
    Run {
        exit: i64::from(output.status.code().unwrap()),
        status: report["status"].as_str().unwrap().into(),
        targets,
        findings,
        argv,
    }
}

fn sorted(value: &Value) -> Vec<Value> {
    let mut rows: Vec<_> = value
        .as_array()
        .unwrap()
        .iter()
        .map(|row| normalize(row, &Vec::new()))
        .collect();
    rows.sort_by_key(ToString::to_string);
    rows
}

fn argv<'a>(argv: &'a [(String, Vec<String>)], name: &str) -> Option<&'a [String]> {
    argv.iter()
        .find(|(tool, _)| tool == name)
        .map(|(_, argv)| argv.as_slice())
}

#[test]
#[ignore = "needs a Python with pytest and coverage (ICI_TEST_PYTHON)"]
fn legacy_python_tools_are_given_what_frozen_gave_them() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for (name, case) in oracle.as_object().unwrap() {
        if case["command"] == "verify" {
            continue;
        }
        let frozen = &case["frozen"];
        let native = run(case, &python);
        assert_eq!(native.exit, frozen["exit"].as_i64().unwrap(), "{name}");
        assert_eq!(native.status, frozen["status"].as_str().unwrap(), "{name}");
        assert_eq!(native.targets, sorted(&frozen["targets"]), "{name}");
        assert_eq!(native.findings, sorted(&frozen["findings"]), "{name}");
        let frozen_argv: Vec<(String, Vec<String>)> = frozen["tool_evidence"]
            .as_array()
            .unwrap()
            .iter()
            .map(|record| {
                (
                    record["name"].as_str().unwrap().to_owned(),
                    record["argv"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|part| part.as_str().unwrap().to_owned())
                        .collect(),
                )
            })
            .collect();
        if let Some(expected) = argv(&frozen_argv, "python -m compileall") {
            let given = argv(&native.argv, "python -m compileall")
                .unwrap_or_else(|| panic!("{name}: no compileall run: {:?}", native.argv));
            let targets = |argv: &[String]| {
                argv.iter()
                    .skip_while(|part| *part != "-f")
                    .skip(1)
                    .cloned()
                    .collect::<Vec<_>>()
            };
            assert_eq!(targets(given), targets(expected), "{name}");
        }
        // The interpreter that ran: frozen resolves it (a venv's python runs as its base).
        for tool in ["python -m compileall", "python import smoke"] {
            if let Some(expected) = argv(&frozen_argv, tool) {
                let given = argv(&native.argv, tool)
                    .unwrap_or_else(|| panic!("{name}: no {tool} run: {:?}", native.argv));
                assert_eq!(given.first(), expected.first(), "{name}: {tool}");
            }
        }
        if let Some(expected) = argv(&frozen_argv, "coverage pytest") {
            let given = argv(&native.argv, "coverage pytest")
                .unwrap_or_else(|| panic!("{name}: no coverage pytest run: {:?}", native.argv));
            assert!(
                given.windows(2).any(|pair| pair == ["-o", "addopts="]),
                "{name}: {given:?}"
            );
            // Native names the test directory absolutely; frozen relative to the same cwd.
            let resolved = |argv: &[String]| {
                argv.last()
                    .map(|target| target.strip_prefix("<root>/").unwrap_or(target).to_owned())
            };
            assert_eq!(resolved(given), resolved(expected), "{name}");
        }
    }
}

fn compat(root: &Path, python: &str) -> (i32, Value, Value) {
    let bin = Path::new(python).parent().unwrap().display().to_string();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["python-compat", "--report"])
        .current_dir(root)
        .env_clear()
        .env("HOME", root.parent().unwrap())
        .env("PATH", format!("{bin}:/usr/bin:/bin"))
        .env("ICI_PYTHON", python)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let read = |name: &str| {
        fs::read_to_string(root.join(name))
            .map(|text| serde_json::from_str(&text).unwrap())
            .unwrap_or(Value::Null)
    };
    (
        output.status.code().unwrap(),
        read(".ici/rust-v1/result.json"),
        read("python_compat_report.json"),
    )
}

/// What compileall reads under a legacy target directory is a run input: it is in the run's
/// source identity, so a change to it is seen, although ici's own discovery did not select it. A
/// file there that resolves outside the project cannot be recorded, so the runtime check is
/// refused instead of compiling it. A schema 2 project keeps its resolved selection.
#[test]
#[ignore = "needs a Python with pytest and coverage (ICI_TEST_PYTHON)"]
fn compileall_reads_are_recorded_run_inputs() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let base = tempfile::tempdir().unwrap();
    let base = base.path().canonicalize().unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src/build")).unwrap();
    fs::write(root.join("src/a.py"), "def one():\n    return 1\n").unwrap();
    fs::write(root.join("src/build/n.py"), "X = 1\n").unwrap();
    fs::write(
        root.join("pyproject.toml"),
        "[project]\nname = 'q'\nversion = '1'\nrequires-python = '>=3.10'\n",
    )
    .unwrap();
    fs::write(
        root.join("ici.toml"),
        "[project]\ntype = 'python'\nsource_dirs = ['src']\n",
    )
    .unwrap();
    let (_, first, _) = compat(&root, &python);
    assert_eq!(
        first["identity"]["source"]["files"],
        serde_json::json!(["src/a.py", "src/build/n.py"])
    );
    fs::write(root.join("src/build/n.py"), "X = 2\n").unwrap();
    let (_, second, _) = compat(&root, &python);
    assert_ne!(
        first["identity"]["source"]["digest"],
        second["identity"]["source"]["digest"]
    );
    // Outside the project: refused, not compiled.
    fs::write(base.join("outside.py"), "Y = 1\n").unwrap();
    std::os::unix::fs::symlink("../../outside.py", root.join("src/link.py")).unwrap();
    let (exit, _, report) = compat(&root, &python);
    assert_eq!(exit, 1);
    assert_eq!(report["status"], "ERROR");
    assert!(
        report["targets"]
            .as_array()
            .unwrap()
            .iter()
            .any(|target| target["message"]
                .as_str()
                .unwrap()
                .contains("compileall would read src/link.py, which resolves outside the project")),
        "{report}"
    );
    assert!(
        !report["tool_evidence"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|record| record["name"] == "python -m compileall"),
        "{report}"
    );
    // Schema 2: the resolved selection, not the directory, is compiled and recorded.
    fs::remove_file(root.join("src/link.py")).unwrap();
    fs::remove_dir_all(root.join(".ici")).unwrap();
    fs::write(root.join("src/build/n.py"), "def broken(:\n").unwrap();
    fs::write(root.join(".hg"), "").unwrap();
    fs::write(
        root.join("ici.toml"),
        format!(
            "schema_version = 2\n[python]\nexecutable = {python:?}\n[[components]]\nid = \"app\"\nroot = \".\"\nlanguages = [\"python\"]\nsources = [\"src/**\"]\n"
        ),
    )
    .unwrap();
    let (exit, v2, _) = compat(&root, &python);
    assert_eq!(exit, 0, "{v2}");
    assert_eq!(
        v2["identity"]["source"]["files"],
        serde_json::json!(["src/a.py"])
    );
}

/// ADR-0008's fixed runtime metadata script, the native replacement of frozen `python -VV`.
const METADATA_PROBE: &str = "import json,sys,sysconfig,platform;print(json.dumps({'version':list(sys.version_info[:3]),'implementation':sys.implementation.name,'platform':sys.platform,'machine':platform.machine(),'soabi':sysconfig.get_config_var('SOABI'),'ext_suffix':sysconfig.get_config_var('EXT_SUFFIX')}))";

/// A token as a meaning: paths relative to the project (same cwd), ici's own run directory as
/// `<run>`.
fn meaning(token: &str) -> String {
    let token = match token.find("<root>/.ici/rust-v1/runs/") {
        Some(start) => {
            let rest = &token[start + "<root>/.ici/rust-v1/runs/".len()..];
            let file = rest.rsplit('/').next().unwrap_or(rest);
            format!("{}<run>/{file}", &token[..start])
        }
        None => token.to_owned(),
    };
    token.replace("<root>/", "")
}

/// Remove `expected`, which must sit exactly right after the first occurrence of `anchor`.
fn take_after(argv: &mut Vec<String>, anchor: &[&str], expected: &[&str]) -> Result<(), String> {
    let at = argv
        .windows(anchor.len())
        .position(|window| window == anchor)
        .ok_or_else(|| format!("no {anchor:?} in {argv:?}"))?
        + anchor.len();
    if argv.get(at..at + expected.len())
        != Some(
            expected
                .iter()
                .map(|s| s.to_string())
                .collect::<Vec<_>>()
                .as_slice(),
        )
    {
        return Err(format!(
            "expected {expected:?} right after {anchor:?} in {argv:?}"
        ));
    }
    argv.drain(at..at + expected.len());
    Ok(())
}

/// `--` is only an option terminator: it must be followed by operands alone, and is removed.
fn terminator(argv: &mut Vec<String>) -> Result<(), String> {
    let at = argv
        .iter()
        .position(|token| token == "--")
        .ok_or_else(|| format!("no -- in {argv:?}"))?;
    if at + 1 == argv.len() || argv[at + 1..].iter().any(|token| token.starts_with('-')) {
        return Err(format!("-- is not followed by operands only in {argv:?}"));
    }
    argv.remove(at);
    Ok(())
}

/// Frozen Ruff names its path operand before `--output-format`; clap reads options anywhere, so
/// the operand is compared at the end, where native puts it after `--`.
fn operand_last(argv: &mut Vec<String>, operand: &str) -> Result<(), String> {
    let at = argv
        .iter()
        .position(|token| token == operand)
        .ok_or_else(|| format!("no {operand} in {argv:?}"))?;
    let token = argv.remove(at);
    argv.push(token);
    Ok(())
}

/// Replace the operand of `option` with `name` (an output location, not an input).
fn operand_as(argv: &mut [String], option: &str, name: &str) -> Result<(), String> {
    let at = argv
        .iter()
        .position(|token| token == option)
        .ok_or_else(|| format!("no {option} in {argv:?}"))?;
    let slot = argv
        .get_mut(at + 1)
        .ok_or_else(|| format!("{option} without operand"))?;
    *slot = name.into();
    Ok(())
}

/// One record's native argv against frozen's, both already in `meaning` form. Native may add
/// only the record's own approved sequence at its fixed place; frozen's own position quirks are
/// named per record. What remains is compared in order: interpreter, tool, subcommand, options,
/// option operands and targets.
fn compare_record(key: &str, native: &[String], frozen: &[String]) -> Result<(), String> {
    let mut native = native.to_vec();
    let mut frozen = frozen.to_vec();
    match key {
        "lint ruff check" | "lint ruff format" => {
            // Cache isolation: no .ruff_cache in the project.
            take_after(
                &mut native,
                &["--output-format=json"],
                &["--no-cache", "--"],
            )
            .map(|_| native.insert(native.len() - 1, "--".into()))?;
            terminator(&mut native)?;
            operand_last(&mut frozen, "src/a.py")?;
        }
        "test coverage pytest" => {
            // Write isolation: coverage data in ici's run directory, not build/coverage.
            take_after(
                &mut native,
                &["--branch"],
                &["--data-file", "<run>/coverage.data"],
            )?;
            // Cache isolation, then this record's output contract for ici's pytest reader.
            take_after(
                &mut native,
                &["-m", "pytest"],
                &["-p", "no:cacheprovider", "-v", "--tb=short", "--color=no"],
            )?;
            // Frozen's `-v` is the same flag after `-o addopts=`; compared at native's place.
            take_after(&mut frozen, &["addopts="], &["-v"])?;
        }
        "test coverage json" => {
            take_after(
                &mut native,
                &["json"],
                &["--data-file", "<run>/coverage.data"],
            )?;
            // Output location: ici's run directory instead of the user's build/coverage.
            operand_as(&mut native, "-o", "<report>")?;
            operand_as(&mut frozen, "-o", "<report>")?;
        }
        "type mypy" => {
            take_after(
                &mut native,
                &["-m", "mypy"],
                &["--no-incremental", "--cache-dir", "/dev/null"],
            )?;
            terminator(&mut native)?;
        }
        // Frozen's own command, unchanged: its output is the public snippet and the verdict's
        // counts, so the cache isolation goes through `PYTEST_ADDOPTS` like frozen's
        // `-p no:cacheprovider` (decisions.md "legacy Python 도구의 격리 옵션과 프로젝트에 남는
        // 파일").
        "sanitize pytest resource warnings" => {}
        "python_compat python -VV" => {
            // ADR-0008: the allowlisted fixed metadata script replaces `-VV`; only the
            // interpreter is the same, and the script is compared exactly.
            if native.get(1..) != Some(&["-I".to_string(), "-c".into(), METADATA_PROBE.into()][..])
            {
                return Err(format!("{key}: not the fixed metadata probe: {native:?}"));
            }
            native.truncate(1);
            frozen.truncate(1);
        }
        _ => {}
    }
    if native == frozen {
        Ok(())
    } else {
        Err(format!("{key}: native {native:?} != frozen {frozen:?}"))
    }
}

/// Every Python tool `ici verify --profile deep` runs on a legacy project, record by record: the
/// same engines, records and interpreters, and argv equal to frozen's in order once each record's
/// own approved insertion is removed from its fixed place (`compare_record`). Frozen leaves tool caches and its coverage report in the user's project;
/// native writes only under `.ici/`. The runtime metadata probe replaces `python -VV`
/// (ADR-0008) and is compared by interpreter only.
#[test]
#[ignore = "needs a Python with pytest, coverage, mypy and Ruff (ICI_TEST_PYTHON)"]
fn legacy_verify_tool_records_differ_from_frozen_only_by_isolation() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let case = &oracle["verify-deep"];
    let frozen = &case["frozen"];
    let base = tempfile::tempdir().unwrap();
    let base_path = base.path().canonicalize().unwrap();
    let root = base_path.join("project");
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let bin = Path::new(&python).parent().unwrap().display().to_string();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["verify", "--profile", "deep", "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &base_path)
        .env("PATH", format!("{bin}:/usr/bin:/bin"))
        .env("ICI_PYTHON", &python)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert_eq!(
        i64::from(output.status.code().unwrap()),
        frozen["exit"].as_i64().unwrap()
    );
    let report: Value =
        serde_json::from_str(&fs::read_to_string(root.join("verify_report.json")).unwrap())
            .unwrap();
    let engines: serde_json::Map<String, Value> = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|result| {
            (
                result["engine_name"].as_str().unwrap().into(),
                result["status"].clone(),
            )
        })
        .collect();
    assert_eq!(Value::Object(engines), frozen["engines"]);
    let names: Names = vec![
        (root.display().to_string(), "<root>"),
        (
            Path::new(&python)
                .canonicalize()
                .unwrap()
                .display()
                .to_string(),
            "<base-python>",
        ),
        (python.clone(), "<python>"),
        (format!("{bin}/"), "<bin>/"),
    ];
    let mut native = vec![];
    for result in report["results"].as_array().unwrap() {
        for record in result["tool_evidence"].as_array().into_iter().flatten() {
            let name = match record["name"].as_str().unwrap() {
                "python runtime metadata" => "python -VV",
                other => other,
            };
            native.push((
                format!("{} {name}", result["engine_name"].as_str().unwrap()),
                record["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|part| meaning(normalize(part, &names).as_str().unwrap()))
                    .collect::<Vec<_>>(),
            ));
        }
    }
    let expected: Vec<_> = frozen["records"]
        .as_array()
        .unwrap()
        .iter()
        .map(|record| {
            (
                format!(
                    "{} {}",
                    record["engine"].as_str().unwrap(),
                    record["name"].as_str().unwrap()
                ),
                record["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|part| meaning(part.as_str().unwrap()))
                    .collect::<Vec<_>>(),
            )
        })
        .collect();
    assert_eq!(
        native.iter().map(|(key, _)| key).collect::<Vec<_>>(),
        expected.iter().map(|(key, _)| key).collect::<Vec<_>>()
    );
    for ((key, given), (_, frozen_argv)) in native.iter().zip(&expected) {
        compare_record(key, given, frozen_argv).unwrap();
    }
    // Negative controls: the comparison rejects reordered arguments and moved option operands.
    let record = |name: &str| {
        native
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, argv)| argv.clone())
            .unwrap()
    };
    let frozen_record = |name: &str| {
        expected
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, argv)| argv.clone())
            .unwrap()
    };
    let mut swapped = record("test coverage pytest");
    let run = swapped.iter().position(|token| token == "run").unwrap();
    swapped.swap(run, run + 1);
    let mut moved_operand = record("test coverage pytest");
    let data = moved_operand
        .iter()
        .position(|token| token == "--data-file")
        .unwrap();
    let operand = moved_operand.remove(data + 1);
    moved_operand.push(operand);
    let mut moved_addopts = record("test coverage pytest");
    let addopts = moved_addopts
        .iter()
        .position(|token| token == "addopts=")
        .unwrap();
    moved_addopts.swap(addopts - 1, addopts);
    let mut wrong_output = record("sanitize pytest resource warnings");
    let pytest = wrong_output
        .iter()
        .position(|token| token == "pytest")
        .unwrap();
    wrong_output.insert(pytest + 1, "--tb=short".into());
    let mut option_after_terminator = record("type mypy");
    option_after_terminator.push("--strict".into());
    let mut target_before_subcommand = record("lint ruff check");
    let target = target_before_subcommand.pop().unwrap();
    target_before_subcommand.insert(1, target);
    for (key, mutated) in [
        ("test coverage pytest", swapped),
        ("test coverage pytest", moved_operand),
        ("test coverage pytest", moved_addopts),
        ("sanitize pytest resource warnings", wrong_output),
        ("type mypy", option_after_terminator),
        ("lint ruff check", target_before_subcommand),
    ] {
        assert!(
            compare_record(key, &mutated, &frozen_record(key)).is_err(),
            "{key}: accepted {mutated:?}"
        );
    }
    let mut written: Vec<String> = vec![];
    for entry in fs::read_dir(&root).unwrap().filter_map(Result::ok) {
        let name = entry.file_name().to_string_lossy().into_owned();
        let known = case["files"]
            .as_object()
            .unwrap()
            .keys()
            .any(|file| file == &name || file.starts_with(&format!("{name}/")));
        if !known && !name.ends_with("_report.json") && !name.ends_with("_report.html") {
            written.push(if entry.path().is_dir() {
                format!("{name}/")
            } else {
                name
            });
        }
    }
    written.sort();
    assert_eq!(written, [".ici/"]);
    assert_eq!(
        frozen["written"],
        serde_json::json!([".mypy_cache/", ".pytest_cache/", ".ruff_cache/", "build/"])
    );
}
