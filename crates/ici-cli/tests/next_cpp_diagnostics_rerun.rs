//! Next `cpp.diagnostics` is complete per replayed unit while its header closure stays unproven,
//! so nothing of it is reused: a header-only edit re-runs the replay, the stale warning is gone
//! and the baseline sees it fixed. A unit the database leaves out stays an incomplete step and the
//! check is never complete evidence.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

const HEADER_WARNS: &str = "inline int api() {\n    int unused_local = 0;\n    return 1;\n}\n";
const HEADER_CLEAN: &str = "inline int api() {\n    return 1;\n}\n";

fn config() -> String {
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

fn project(base: &Path, covered: &[&str]) -> std::path::PathBuf {
    let project = base.join("project");
    fs::create_dir_all(project.join("app")).unwrap();
    fs::create_dir_all(base.join("home")).unwrap();
    fs::write(project.join(".hg"), "").unwrap();
    fs::write(project.join("ici.toml"), config()).unwrap();
    fs::write(project.join("app/a.hpp"), HEADER_WARNS).unwrap();
    fs::write(
        project.join("app/a.cpp"),
        "#include \"a.hpp\"\nint main() { return api(); }\n",
    )
    .unwrap();
    fs::write(project.join("app/b.cpp"), "int b() { return 2; }\n").unwrap();
    let rows: Vec<Value> = covered
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
    project
}

fn verify(project: &Path, base: &Path, extra: &[&str]) -> (i32, Value) {
    let environment = BTreeMap::from([
        ("HOME".to_owned(), base.join("home").display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.display().to_string(),
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

fn diagnostics(document: &Value) -> Vec<&Value> {
    document["checks"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|check| check["check_id"] == "cpp.diagnostics")
        .collect()
}

fn warnings(document: &Value) -> Vec<Value> {
    document["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| {
            finding["task_id"]
                .as_str()
                .is_some_and(|task| task.starts_with("app.cpp.diagnostics"))
        })
        .cloned()
        .collect()
}

/// The replay of `a.cpp`: its own step row, or the check row when every step succeeded.
fn replays_a(check: &Value) -> bool {
    matches!(
        check["task_id"].as_str(),
        Some("app.cpp.diagnostics.app-a-cpp-0" | "app.cpp.diagnostics")
    )
}

#[test]
fn a_header_edit_reruns_the_replay_and_resolves_its_warning() {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = project(&base, &["a.cpp", "b.cpp"]);
    let (_, first) = verify(&project, &base, &["--result", "first.json"]);
    let found = warnings(&first);
    assert_eq!(found.len(), 1, "{first}");
    assert_eq!(found[0]["primary_location"]["path"], "app/a.hpp", "{first}");
    assert!(
        found[0]["message"]
            .as_str()
            .unwrap()
            .contains("unused_local"),
        "{first}"
    );
    let replay = diagnostics(&first)
        .into_iter()
        .find(|check| replays_a(check))
        .unwrap_or_else(|| panic!("{first}"))
        .clone();
    assert_eq!(replay["state"], "SUCCEEDED", "{replay}");
    assert_eq!(replay["evidence_complete"], true, "{replay}");
    // Only the header changes.
    fs::write(project.join("app/a.hpp"), HEADER_CLEAN).unwrap();
    let (_, now) = verify(
        &project,
        &base,
        &["--result", "now.json", "--baseline", "first.json"],
    );
    let rerun = diagnostics(&now)
        .into_iter()
        .find(|check| replays_a(check))
        .unwrap_or_else(|| panic!("{now}"))
        .clone();
    // Re-ran: nothing reused, a fresh SUCCEEDED replay without the stale warning.
    let reused = now["execution"]["reused_task_ids"].as_array().unwrap();
    assert!(
        reused
            .iter()
            .all(|task| !task.as_str().unwrap().starts_with("app.cpp.")),
        "{now}"
    );
    assert_eq!(rerun["state"], "SUCCEEDED", "{rerun}");
    assert_eq!(rerun["evidence_complete"], true, "{rerun}");
    assert!(warnings(&now).is_empty(), "{now}");
    // The replay scope names the unit, not its unproven header closure, so it does not move;
    // the source identity does, and the baseline resolves the warning on the fresh replay.
    assert_eq!(rerun["scope_digest"], replay["scope_digest"]);
    assert_ne!(
        now["identity"]["source"]["digest"],
        first["identity"]["source"]["digest"]
    );
    let fingerprint = &found[0]["fingerprint"];
    let resolved = now["baseline"]["resolved"].as_array().unwrap();
    assert!(resolved.contains(fingerprint), "{}", now["baseline"]);
    for key in ["carried", "new", "unchanged"] {
        assert!(
            !now["baseline"][key]
                .as_array()
                .is_some_and(|list| list.contains(fingerprint)),
            "{key}: {}",
            now["baseline"]
        );
    }
}

#[test]
fn a_unit_outside_the_database_is_an_incomplete_step() {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    let project = project(&base, &["a.cpp"]);
    let (exit, document) = verify(&project, &base, &[]);
    assert_eq!(exit, 3, "{document}");
    assert_eq!(document["gate"]["selected"], "INCOMPLETE", "{document}");
    let checks = diagnostics(&document);
    assert!(!checks.is_empty(), "{document}");
    assert!(
        checks
            .iter()
            .all(|check| check["evidence_complete"] == false && check["state"] == "BLOCKED"),
        "{document}"
    );
    let reasons = document["gate"]["reasons"].as_array().unwrap();
    assert!(
        reasons.iter().any(|reason| reason
            .as_str()
            .unwrap()
            .contains("no compile invocation for 1/2 translation units: app/b.cpp")),
        "{document}"
    );
}
