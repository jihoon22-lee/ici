//! A selected file symlink that resolves inside the workspace but outside its component's root
//! (decisions.md "component root 밖을 가리키는 선택 symlink"). Frozen makes the target a member and its
//! per-file reads then fail; native keeps it out of the selection, names the link, the target
//! and the component, and leaves the component's scope incomplete without reading the file. Every
//! other symlink shape is compared with frozen (`next-outside-links-oracle.json`).
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/next-outside-links-oracle.json");
const REAL: &str = "x = 1\n";

struct Run {
    exit: i32,
    plan_exit: i32,
    plan: String,
    analyze_exit: i32,
    result: Value,
}

/// `config` `None` is the configless Automatic selection, run through `ici analyze`.
fn run(config: Option<&str>, files: &serde_json::Map<String, Value>) -> Run {
    let temporary = tempfile::tempdir().unwrap();
    let base = temporary.path().canonicalize().unwrap();
    let external = base.join("external.py");
    fs::write(&external, REAL).unwrap();
    let root = base.join("project");
    for (path, content) in files {
        let target = root.join(path);
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        match content {
            Value::String(text) => fs::write(&target, text).unwrap(),
            link => std::os::unix::fs::symlink(
                link["link"]
                    .as_str()
                    .unwrap()
                    .replace("<EXTERNAL>", &external.display().to_string()),
                &target,
            )
            .unwrap(),
        }
    }
    if let Some(config) = config {
        fs::write(root.join("ici.toml"), config).unwrap();
    }
    assert!(
        Command::new("/usr/bin/git")
            .args(["init", "-q", "."])
            .current_dir(&root)
            .status()
            .unwrap()
            .success()
    );
    let ici = |arguments: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(arguments)
            .current_dir(&root)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", &base)
            .env("LANG", "C.UTF-8")
            .output()
            .unwrap()
    };
    let contents = || -> Vec<_> {
        files
            .keys()
            .map(|path| fs::read(root.join(path)).ok())
            .collect()
    };
    let before = contents();
    let (plan, verify) = if config.is_some() {
        (
            ici(&["next", "plan"]),
            ici(&[
                "next",
                "verify",
                "--no-cache",
                "--result",
                "out/result.json",
            ]),
        )
    } else {
        (
            ici(&["analyze", "--plan"]),
            ici(&["analyze", "--no-cache", "--result", "out/result.json"]),
        )
    };
    let analyze = ici(&["analyze", "--no-cache", "--result", "out/analyze.json"]);
    // No source or link target is written.
    assert_eq!(contents(), before);
    Run {
        exit: verify.status.code().unwrap(),
        plan_exit: plan.status.code().unwrap(),
        plan: String::from_utf8(plan.stdout).unwrap(),
        analyze_exit: analyze.status.code().unwrap(),
        result: serde_json::from_slice(&fs::read(root.join("out/result.json")).unwrap()).unwrap(),
    }
}

fn states(result: &Value) -> BTreeMap<String, String> {
    result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            (
                check["task_id"].as_str().unwrap().to_owned(),
                check["state"].as_str().unwrap().to_owned(),
            )
        })
        .collect()
}

fn oracle() -> Value {
    serde_json::from_str(ORACLE).unwrap()
}

fn scenario(name: &str) -> (Value, Run) {
    let oracle = oracle();
    let run_record = &oracle["runs"][name];
    let native = run(
        run_record["config"].as_str(),
        run_record["files"].as_object().unwrap(),
    );
    (run_record["frozen"].clone(), native)
}

/// Shapes this rule does not cover keep frozen's exit, gate, states and task sets. Frozen's source
/// identity also lists a non-Python link target; native lists analysis-language members only
/// (decisions.md "next source identity의 member 파일").
#[test]
fn other_symlink_shapes_keep_the_frozen_outcome() {
    for name in [
        "same-component",
        "outside-workspace",
        "broken",
        "directory",
        "name-py-target-not-source",
        "name-txt-target-py",
        "excluded",
        "unselected-glob",
    ] {
        let (frozen, native) = scenario(name);
        assert_eq!(native.exit, frozen["exit_code"], "{name}");
        assert_eq!(native.analyze_exit, frozen["exit_code"], "{name}");
        assert_eq!(native.plan_exit, frozen["plan_exit_code"], "{name}");
        assert_eq!(native.result["gate"], frozen["gate"], "{name}");
        assert_eq!(json!(states(&native.result)), frozen["states"], "{name}");
        assert_eq!(
            native.result["execution"]["blocked_task_ids"], frozen["blocked"],
            "{name}"
        );
        assert_eq!(
            native.result["execution"]["failed_task_ids"], frozen["failed"],
            "{name}"
        );
        assert!(!native.plan.contains("selection incomplete"), "{name}");
        let native_files = &native.result["identity"]["source"]["files"];
        if name == "name-py-target-not-source" {
            assert_eq!(
                frozen["source_files"],
                json!(["app/real.py", "other/notes.txt"])
            );
            assert_eq!(*native_files, json!(["app/real.py"]));
        } else {
            assert_eq!(*native_files, frozen["source_files"], "{name}");
        }
    }
}

const APP: [&str; 10] = [
    "line",
    "complexity",
    "cognitive",
    "cycle",
    "dup",
    "security",
    "resource",
    "exception",
    "dead",
    "compat",
];

fn reason(link: &str) -> String {
    format!("{link} -> other/m.py is outside the root of component `app` and was not analyzed")
}

fn assert_frozen_reads_the_target(frozen: &Value, name: &str) {
    // What native does not reproduce: frozen selects the target and its per-file reads fail.
    assert_eq!(frozen["exit_code"], 3, "{name}");
    for check in ["dup", "security", "resource", "exception", "dead", "compat"] {
        assert_eq!(
            frozen["states"][format!("app.python.{check}")],
            "FAILED",
            "{name}"
        );
    }
    for check in ["line", "complexity", "cognitive", "cycle"] {
        assert_eq!(
            frozen["states"][format!("app.python.{check}")],
            "SUCCEEDED",
            "{name}"
        );
    }
    assert!(
        frozen["source_files"]
            .as_array()
            .unwrap()
            .contains(&json!("other/m.py")),
        "{name}"
    );
}

/// Beside a real file: per-file checks keep that file's results but stay incomplete; checks
/// relating files are blocked; nothing reads or counts the target.
#[test]
fn a_link_outside_the_component_leaves_its_scope_incomplete() {
    for name in [
        "outside-beside-real",
        "outside-beside-independent-component",
    ] {
        let (frozen, native) = scenario(name);
        assert_frozen_reads_the_target(&frozen, name);
        assert_eq!(native.exit, 3, "{name}");
        assert_eq!(native.analyze_exit, 3, "{name}");
        assert_eq!(native.plan_exit, 0, "{name}");
        assert!(
            native.plan.contains(&format!(
                "  note: selection incomplete: {}\n",
                reason("app/link.py")
            )),
            "{name}: {}",
            native.plan
        );
        assert!(
            !native
                .plan
                .contains("symlink not traversed during source discovery: app/link.py")
        );
        let gate = &native.result["gate"];
        assert_eq!(gate["selected"], "INCOMPLETE");
        assert_eq!(gate["has_violations"], false);
        // Each check of the component, then the run-wide intake reason (the partial intake contract).
        let mut expected: Vec<_> = APP
            .iter()
            .map(|check| json!(format!("app.python.{check}: {}", reason("app/link.py"))))
            .collect();
        expected.push(json!(reason("app/link.py")));
        assert_eq!(gate["reasons"], json!(expected), "{name}");
        let states = states(&native.result);
        for check in [
            "line",
            "complexity",
            "cognitive",
            "security",
            "resource",
            "exception",
        ] {
            assert_eq!(states[&format!("app.python.{check}")], "FAILED", "{name}");
        }
        for check in ["cycle", "dup", "dead", "compat"] {
            assert_eq!(states[&format!("app.python.{check}")], "BLOCKED", "{name}");
        }
        assert_eq!(
            native.result["identity"]["source"]["files"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|path| path.as_str().unwrap().starts_with("other/"))
                .count(),
            0
        );
        // No whole-scope measurement for the incomplete component: only the independent
        // component's single file is counted.
        let counted: Vec<_> = native.result["metrics"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|metric| metric["name"] == "files_counted")
            .map(|metric| metric["value"].clone())
            .collect();
        let expected = if name == "outside-beside-independent-component" {
            json!([1.0])
        } else {
            json!([])
        };
        assert_eq!(json!(counted), expected, "{name}");
        if name == "outside-beside-independent-component" {
            for check in APP {
                assert_eq!(states[&format!("lib.python.{check}")], "SUCCEEDED");
            }
        }
    }
}

/// Only the link: every check of the component is blocked with the link's reason, never as an
/// empty scope ("no sources in scope") and never with zero findings read as complete.
#[test]
fn a_component_whose_only_source_is_an_outside_link_is_blocked_with_the_link() {
    let (frozen, native) = scenario("outside-only");
    assert_frozen_reads_the_target(&frozen, "outside-only");
    assert_eq!(
        (native.exit, native.analyze_exit, native.plan_exit),
        (3, 3, 0)
    );
    let mut expected: Vec<_> = APP
        .iter()
        .map(|check| json!(format!("app.python.{check}: {}", reason("app/m.py"))))
        .collect();
    expected.push(json!(reason("app/m.py")));
    assert_eq!(native.result["gate"]["reasons"], json!(expected));
    assert!(
        states(&native.result)
            .values()
            .all(|state| state == "BLOCKED")
    );
    for check in APP {
        assert!(
            native.plan.contains(&format!(
                "  app.python.{check}: blocked — {}\n",
                reason("app/m.py")
            )),
            "{}",
            native.plan
        );
    }
}

/// C++ under schema 1: frozen already ends INCOMPLETE for the same link and component, through
/// its dup and exception reads of the target ("outside-project"), while line, complexity,
/// cognitive and cycle read the target and succeed. Native names the link for every check.
#[test]
fn a_cpp_link_outside_the_component_has_the_frozen_cause_and_scope() {
    const CPP: [&str; 6] = [
        "line",
        "complexity",
        "cognitive",
        "cycle",
        "dup",
        "exception",
    ];
    for (name, link) in [
        ("cpp-outside-beside-real", "app/link.cpp"),
        ("cpp-outside-only", "app/m.cpp"),
    ] {
        let (frozen, native) = scenario(name);
        assert_eq!(frozen["exit_code"], 3, "{name}");
        assert_eq!(frozen["gate"]["selected"], "INCOMPLETE", "{name}");
        assert_eq!(
            frozen["gate"]["reasons"],
            json!([
                "app.cpp.dup: outside-project: Selected analysis source is outside the project root",
                "app.cpp.exception: outside-project: Selected analysis source is outside the project root",
            ]),
            "{name}"
        );
        assert!(
            frozen["source_files"]
                .as_array()
                .unwrap()
                .contains(&json!("other/m.cpp"))
        );
        assert_eq!(
            (native.exit, native.analyze_exit, native.plan_exit),
            (3, 3, frozen["plan_exit_code"].as_i64().unwrap() as i32),
            "{name}"
        );
        let reason = format!(
            "{link} -> other/m.cpp is outside the root of component `app` and was not analyzed"
        );
        assert!(
            native
                .plan
                .contains(&format!("  note: selection incomplete: {reason}\n")),
            "{name}: {}",
            native.plan
        );
        let gate = &native.result["gate"];
        assert_eq!(gate["selected"], "INCOMPLETE", "{name}");
        assert_eq!(gate["has_violations"], false, "{name}");
        let mut expected: Vec<_> = CPP
            .iter()
            .map(|check| json!(format!("app.cpp.{check}: {reason}")))
            .collect();
        expected.push(json!(reason));
        assert_eq!(gate["reasons"], json!(expected), "{name}");
        let states = states(&native.result);
        let files = native.result["identity"]["source"]["files"].clone();
        if name == "cpp-outside-only" {
            assert!(
                states.values().all(|state| state == "BLOCKED"),
                "{states:?}"
            );
        } else {
            for check in ["line", "complexity", "cognitive", "exception"] {
                assert_eq!(states[&format!("app.cpp.{check}")], "FAILED", "{name}");
            }
            for check in ["cycle", "dup"] {
                assert_eq!(states[&format!("app.cpp.{check}")], "BLOCKED", "{name}");
            }
            assert_eq!(files, json!(["app/real.cpp"]));
        }
    }
}

/// Configless Automatic selection: the link's target is a source of another discovered component
/// (frozen makes one `project` component holding it), so it is analyzed once on both sides and
/// the link itself is not. The rule does not apply; the link keeps the traversal note. The
/// per-package split and the type checker are separate Automatic rows.
#[test]
fn an_automatic_link_to_another_components_source_keeps_the_frozen_outcome() {
    for (name, components) in [
        ("automatic-link-to-another-package", ["a", "b"]),
        ("automatic-link-out-of-a-package", ["a", "project"]),
    ] {
        let (frozen, native) = scenario(name);
        assert_eq!(native.exit, frozen["exit_code"], "{name}");
        assert_eq!(native.analyze_exit, frozen["exit_code"], "{name}");
        assert_eq!(native.plan_exit, frozen["plan_exit_code"], "{name}");
        assert_eq!(
            native.result["gate"]["selected"],
            frozen["gate"]["selected"]
        );
        assert_eq!(
            native.result["identity"]["source"]["files"], frozen["source_files"],
            "{name}"
        );
        assert!(
            native
                .plan
                .contains("  note: symlink not traversed during source discovery: a/link.py\n"),
            "{name}: {}",
            native.plan
        );
        assert!(!native.plan.contains("selection incomplete"), "{name}");
        let states = states(&native.result);
        for component in components {
            for (task, state) in frozen["states"].as_object().unwrap() {
                let check = task.strip_prefix("project.").unwrap();
                let native_state = states.get(&format!("{component}.{check}"));
                // Frozen's type step is mypy, native's ty; both are absent here.
                assert_eq!(
                    native_state.map(String::as_str),
                    state.as_str(),
                    "{name}: {task}"
                );
            }
        }
        for reason in native.result["gate"]["reasons"].as_array().unwrap() {
            assert!(
                reason.as_str().unwrap().ends_with(" is not available"),
                "{name}: {reason}"
            );
        }
    }
}

/// Schema 2 has no frozen counterpart and keeps its own native policy: the rule is not widened to
/// it. The link is reported as not traversed and its target is not a member.
#[test]
fn a_schema_2_link_outside_the_component_keeps_the_native_policy() {
    let files = json!({
        "app/link.py": {"link": "../other/m.py"},
        "app/real.py": REAL,
        "other/m.py": REAL,
    });
    let native = run(
        Some(
            "schema_version = 2\n[[components]]\nid = \"app\"\nroot = \"app\"\nlanguages = [\"python\"]\n",
        ),
        files.as_object().unwrap(),
    );
    assert!(
        native
            .plan
            .contains("  note: symlink not traversed during source discovery: app/link.py\n"),
        "{}",
        native.plan
    );
    assert!(
        !native.plan.contains("selection incomplete"),
        "{}",
        native.plan
    );
    assert_eq!(
        native.result["identity"]["source"]["files"],
        json!(["app/real.py"])
    );
    for reason in native.result["gate"]["reasons"].as_array().unwrap() {
        assert!(!reason.as_str().unwrap().contains("link.py"), "{reason}");
    }
}
