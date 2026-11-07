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
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            ceiling.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "next", "verify", "--json"]).unwrap();
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
    fs::write(
        project.join("ici.toml"),
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
                .collect::<String>(),
    )
    .unwrap();
    fs::write(project.join("app/small.cpp"), SMALL).unwrap();
    sparse(&project.join("app/big.cpp"), 16 * MIB + 1);
    let row = |source: &str| {
        serde_json::json!({"directory": project, "file": format!("app/{source}"),
            "arguments": ["/usr/bin/g++", "-Wall", "-c", format!("app/{source}")]})
    };
    fs::write(
        project.join("compile_commands.json"),
        serde_json::to_string(&[row("big.cpp"), row("small.cpp")]).unwrap(),
    )
    .unwrap();
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
