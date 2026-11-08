//! Bounded partial intake through real `ici next verify --json` (coordinator ruling 2026-11-07,
//! decisions.md "`cpp.line` 입력 상한"): a source over the 16 MiB per-file limit is recorded with its
//! path, cause and limit; the readable sources of its component and an independent component still
//! run; per-file checks keep the captured files and claim no whole-scope metric; checks relating
//! files stay blocked; a partial run reuses and stores nothing and never reads as complete.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, fs::File, path::Path};

const MIB: u64 = 1024 * 1024;
const ON: [&str; 6] = [
    "cpp.line",
    "cpp.complexity",
    "cpp.dup",
    "cpp.cycle",
    "python.line",
    "python.complexity",
];

fn config() -> String {
    let mut text = String::from(
        "schema_version = 1\n[workspace]\nname = \"probe\"\nprofile = \"standard\"\n\
         [[components]]\nid = \"app\"\nroot = \"app\"\nlanguages = [\"cpp\"]\n\
         [[components]]\nid = \"lib\"\nroot = \"lib\"\nlanguages = [\"python\"]\n",
    );
    for check in &ici_domain::builtin_catalog().checks {
        text.push_str(&format!(
            "[checks.\"{}\"]\nenabled = {}\n",
            check.id,
            ON.contains(&check.id.as_str())
        ));
    }
    text
}

const PYTHON: &str = "def other(a):\n    if a > 1:\n        return a\n    return 0\n";

const SMALL: &str = "int small(int a) {\n  if (a > 1) { return a; }\n  return 0;\n}\n";

fn sparse(path: &Path, size: u64) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    let file = File::create(path).unwrap();
    file.set_len(size).unwrap();
}

fn verify(project: &Path, home: &Path, ceiling: &Path) -> (i32, Value) {
    verify_with(project, home, ceiling, &[])
}

fn verify_with(project: &Path, home: &Path, ceiling: &Path, extra: &[&str]) -> (i32, Value) {
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            ceiling.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(
        ["ici", "next", "verify", "--json"]
            .iter()
            .chain(extra)
            .copied(),
    )
    .unwrap();
    let output = commands::dispatch(&invocation, project, &environment).unwrap();
    (
        output.exit_code,
        serde_json::from_str(&output.stdout).unwrap(),
    )
}

fn check<'a>(document: &'a Value, task: &str) -> &'a Value {
    document["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["task_id"] == task)
        .unwrap_or_else(|| panic!("{task}: {document}"))
}

fn has(list: &Value, text: &str) -> bool {
    list.as_array()
        .unwrap()
        .iter()
        .any(|item| item.as_str().unwrap().contains(text))
}

#[test]
fn a_source_over_the_per_file_limit_leaves_the_rest_of_the_run_measured_and_incomplete() {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = base.join("project");
    let home = base.join("home");
    fs::create_dir_all(&home).unwrap();
    fs::create_dir(base.join(".hg")).unwrap();
    fs::create_dir_all(project.join("app")).unwrap();
    fs::create_dir_all(project.join("lib")).unwrap();
    fs::write(project.join("ici.toml"), config()).unwrap();
    fs::write(project.join("app/small.cpp"), SMALL).unwrap();
    fs::write(project.join("lib/other.py"), PYTHON).unwrap();
    sparse(&project.join("app/big.cpp"), 16 * MIB + 1);
    let reason = "app/big.cpp was not analyzed: it exceeds the 16 MiB per-file input limit";

    for run in 0..2 {
        let (exit, document) = verify(&project, &home, &base);
        assert_eq!(exit, 3, "run {run}: {document}");
        assert_eq!(document["gate"]["selected"], "INCOMPLETE");
        assert!(
            has(&document["gate"]["reasons"], reason),
            "{}",
            document["gate"]
        );
        assert!(
            !has(&document["gate"]["reasons"], "inputs changed"),
            "{}",
            document["gate"]
        );
        // The per-file checks run over app/small.cpp and stay incomplete.
        for task in ["app.cpp.line", "app.cpp.complexity"] {
            let check = check(&document, task);
            assert_eq!(check["state"], "FAILED", "{check}");
            assert_eq!(check["evidence_complete"], false, "{check}");
        }
        // Clone and include relations over the scope cannot be partial.
        for task in ["app.cpp.dup", "app.cpp.cycle"] {
            assert_eq!(check(&document, task)["state"], "BLOCKED", "{task}");
        }
        // The independent component is analyzed in full.
        for task in ["lib.python.line", "lib.python.complexity"] {
            assert_eq!(check(&document, task)["state"], "SUCCEEDED", "{task}");
        }
        // Only lib's line count is measured: nothing claims app's whole scope.
        let totals: Vec<&Value> = document["metrics"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|metric| metric["name"] == "lines_total")
            .collect();
        assert_eq!(totals.len(), 1, "{}", document["metrics"]);
        assert_eq!(totals[0]["value"], 4.0);
        assert!(has(&document["limitations"], reason));
        // Nothing is reused or stored from a partial run.
        assert_eq!(
            document["execution"]["reused_task_ids"],
            serde_json::json!([])
        );
    }

    // Selecting lib alone: app's unread file is not an input of that run, which is complete and
    // may reuse what it stored.
    for run in 0..2 {
        let (_, document) = verify_with(&project, &home, &base, &["--component", "lib"]);
        assert_ne!(
            document["gate"]["selected"], "INCOMPLETE",
            "{}",
            document["gate"]
        );
        assert!(!has(&document["limitations"], "was not analyzed"));
        for task in ["lib.python.line", "lib.python.complexity"] {
            let check = check(&document, task);
            assert_eq!(check["state"], "SUCCEEDED", "{check}");
            assert_eq!(check["evidence_complete"], true, "{check}");
        }
        if run == 1 {
            assert!(
                has(&document["execution"]["reused_task_ids"], "lib.python.line"),
                "{}",
                document["execution"]
            );
        }
    }
    fs::remove_dir_all(project.join(".ici")).unwrap();

    // Once the file fits, the run is complete and its analyses become reusable.
    fs::write(project.join("app/big.cpp"), SMALL.replace("small", "big")).unwrap();
    let (_, complete) = verify(&project, &home, &base);
    assert!(!has(&complete["gate"]["reasons"], "was not analyzed"));
    let line = check(&complete, "app.cpp.line");
    assert_eq!(line["state"], "SUCCEEDED");
    assert_eq!(line["evidence_complete"], true);
    let (_, again) = verify(&project, &home, &base);
    assert!(
        has(&again["execution"]["reused_task_ids"], "lib.python.line"),
        "{}",
        again["execution"]
    );
}

fn diagnostics_config() -> String {
    "schema_version = 1\n[workspace]\nname = \"probe\"\nprofile = \"standard\"\n\
     [[components]]\nid = \"app\"\nroot = \"app\"\nlanguages = [\"cpp\"]\n"
        .to_owned()
        + &ici_domain::builtin_catalog()
            .checks
            .iter()
            .map(|check| {
                format!(
                    "[checks.\"{}\"]\nenabled = {}\n",
                    check.id,
                    matches!(check.id.as_str(), "cpp.compile" | "cpp.diagnostics")
                )
            })
            .collect::<String>()
}

fn database(project: &Path, sources: &[&str]) {
    let rows: Vec<Value> = sources
        .iter()
        .map(|source| {
            serde_json::json!({"directory": project, "file": format!("app/{source}"),
                "arguments": ["/usr/bin/g++", "-Wall", "-c", format!("app/{source}")]})
        })
        .collect();
    fs::write(
        project.join("compile_commands.json"),
        serde_json::to_string(&rows).unwrap(),
    )
    .unwrap();
}

/// Next `cpp.diagnostics` replays one unit at a time: only the unread source's step is refused.
#[test]
fn only_the_replay_of_an_unread_source_is_refused() {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = base.join("project");
    let home = base.join("home");
    fs::create_dir_all(&home).unwrap();
    fs::create_dir(base.join(".hg")).unwrap();
    fs::create_dir_all(project.join("app")).unwrap();
    fs::write(project.join("ici.toml"), diagnostics_config()).unwrap();
    fs::write(project.join("app/small.cpp"), SMALL).unwrap();
    sparse(&project.join("app/big.cpp"), 16 * MIB + 1);
    database(&project, &["big.cpp", "small.cpp"]);
    let (exit, document) = verify(&project, &home, &base);
    assert_eq!(exit, 3, "{document}");
    assert_eq!(check(&document, "app.cpp.compile")["state"], "SUCCEEDED");
    let reasons = &document["gate"]["reasons"];
    assert!(
        has(
            reasons,
            "app.cpp.diagnostics.app-big-cpp-0: app/big.cpp was not analyzed: it exceeds the 16 MiB \
             per-file input limit"
        ),
        "{reasons}"
    );
    assert!(
        !has(reasons, "app.cpp.diagnostics.app-small-cpp-1"),
        "{reasons}"
    );
    assert_eq!(check(&document, "app.cpp.diagnostics")["state"], "BLOCKED");
}

/// No unit's header closure is proven: an unread header refuses every replay that may include it.
#[test]
fn an_unread_header_refuses_every_replay_that_may_include_it() {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = base.join("project");
    let home = base.join("home");
    fs::create_dir_all(&home).unwrap();
    fs::create_dir(base.join(".hg")).unwrap();
    fs::create_dir_all(project.join("app")).unwrap();
    fs::write(project.join("ici.toml"), diagnostics_config()).unwrap();
    fs::write(project.join("app/small.cpp"), SMALL).unwrap();
    fs::write(
        project.join("app/other.cpp"),
        SMALL.replace("small", "other"),
    )
    .unwrap();
    sparse(&project.join("app/big.hpp"), 16 * MIB + 1);
    database(&project, &["other.cpp", "small.cpp"]);
    let (exit, document) = verify(&project, &home, &base);
    assert_eq!(exit, 3, "{document}");
    let reasons = &document["gate"]["reasons"];
    for step in ["app-other-cpp-0", "app-small-cpp-1"] {
        assert!(
            has(
                reasons,
                &format!(
                    "app.cpp.diagnostics.{step}: app/big.hpp was not analyzed: it exceeds the 16 \
                     MiB per-file input limit; this unit may include it"
                )
            ),
            "{reasons}"
        );
    }
    assert_eq!(check(&document, "app.cpp.diagnostics")["state"], "BLOCKED");
}

/// What compileall reads under a legacy target directory is an input of the runtime check
/// although discovery did not select it. Over the per-file limit it is not captured, so the
/// check is refused by name: compileall does not run, and neither empty bytes nor a digest stand
/// in for the unread file.
#[test]
#[ignore = "needs a Python with pytest and coverage (ICI_TEST_PYTHON)"]
fn a_runtime_only_compile_input_over_the_limit_refuses_the_runtime_check() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("ICI_TEST_PYTHON");
    let base = tempfile::tempdir().unwrap();
    let base = base.path().canonicalize().unwrap();
    let project = base.join("project");
    fs::create_dir_all(project.join("src")).unwrap();
    fs::write(project.join(".hg"), "").unwrap();
    fs::write(project.join("src/a.py"), "def one():\n    return 1\n").unwrap();
    sparse(&project.join("src/build/n.py"), 16 * MIB + 1);
    fs::write(
        project.join("pyproject.toml"),
        "[project]\nname = 'q'\nversion = '1'\nrequires-python = '>=3.10'\n",
    )
    .unwrap();
    fs::write(
        project.join("ici.toml"),
        "[project]\ntype = 'python'\nsource_dirs = ['src']\n",
    )
    .unwrap();
    let bin = Path::new(&python).parent().unwrap().display().to_string();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), base.display().to_string()),
        ("PATH".to_owned(), format!("{bin}:/usr/bin:/bin")),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ("ICI_PYTHON".to_owned(), python.clone()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.display().to_string(),
        ),
    ]);
    let run = || {
        let invocation = registry::parse(["ici", "python-compat", "--report"]).unwrap();
        let output = commands::dispatch(&invocation, &project, &environment).unwrap();
        let read = |name: &str| -> Value {
            serde_json::from_str(&fs::read_to_string(project.join(name)).unwrap()).unwrap()
        };
        let documents = (
            output.exit_code,
            read(".ici/rust-v1/result.json"),
            read("python_compat_report.json"),
        );
        fs::remove_dir_all(project.join(".ici")).unwrap();
        documents
    };
    let compiled = |report: &Value| {
        report["tool_evidence"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|record| record["name"] == "python -m compileall")
    };
    let reason = "src/build/n.py was not analyzed: it exceeds the 16 MiB per-file input limit";
    let (exit, result, report) = run();
    assert_ne!(exit, 0, "{result}");
    assert_eq!(result["gate"]["selected"], "INCOMPLETE", "{result}");
    assert!(has(&result["gate"]["reasons"], reason), "{result}");
    assert!(has(&result["limitations"], reason), "{result}");
    let runtime = check(&result, "project.python.compat-runtime");
    assert_eq!(runtime["state"], "BLOCKED", "{runtime}");
    assert_eq!(runtime["evidence_complete"], false, "{runtime}");
    assert_eq!(
        result["execution"]["blocked_task_ids"],
        serde_json::json!(["project.python.compat-runtime"]),
        "{result}"
    );
    // The static check of the selected source still runs on what was read.
    assert_eq!(
        check(&result, "project.python.compat")["state"],
        "SUCCEEDED"
    );
    // Not compiled, and the unread file is named in the source identity, not hashed as empty.
    assert!(!compiled(&report), "{report}");
    assert!(!result.to_string().contains("compileall"), "{result}");
    let unread = result["identity"]["source"]["digest"].clone();
    // The same run with the file empty and within the limit: compiled, and a different identity.
    fs::write(project.join("src/build/n.py"), "").unwrap();
    let (_, result, report) = run();
    assert_eq!(
        check(&result, "project.python.compat-runtime")["state"],
        "SUCCEEDED",
        "{result}"
    );
    assert!(compiled(&report), "{report}");
    assert_ne!(result["identity"]["source"]["digest"], unread, "{result}");
}
