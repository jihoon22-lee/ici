//! A selected file symlink that resolves inside the workspace but outside its component's root
//! (decisions.md "component root 밖을 가리키는 선택 symlink"). Frozen makes the target a member and its
//! per-file reads then fail; native keeps it out of the selection, names the link, the target
//! and the component, and leaves the component's scope incomplete without reading the file. Every
//! other symlink shape is compared with frozen (`next-outside-links-oracle.json`).
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/next-outside-links-oracle.json");
const REAL: &str = "x = 1\n";

struct Run {
    exit: i32,
    plan_exit: i32,
    plan: String,
    analyze_exit: i32,
    result: Value,
    /// The project interpreter's argv per command (plan, verify, analyze), from a logging shim.
    interpreter: Vec<Vec<String>>,
}

/// `config` `None` is the configless Automatic selection, run through `ici analyze`.
fn run(config: Option<&str>, files: &serde_json::Map<String, Value>, args: &[&str]) -> Run {
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
    let log = base.join("interpreter.log");
    if let Some(config) = config {
        let config = if config.contains("<PYTHON_JSON>") {
            let python = std::env::var("ICI_TEST_PYTHON").expect("set ICI_TEST_PYTHON");
            let shim = base.join("project-python");
            fs::write(
                &shim,
                format!(
                    "#!/bin/sh\nprintf '%s\\n' \"$*\" >> {:?}\nexec {python:?} \"$@\"\n",
                    log.display().to_string()
                ),
            )
            .unwrap();
            fs::set_permissions(&shim, fs::Permissions::from_mode(0o755)).unwrap();
            config.replace(
                "<PYTHON_JSON>",
                &serde_json::to_string(&shim.display().to_string()).unwrap(),
            )
        } else {
            config.to_owned()
        };
        fs::write(root.join("ici.toml"), config).unwrap();
    }
    let starts = || -> Vec<String> {
        let text = fs::read_to_string(&log).unwrap_or_default();
        let _ = fs::remove_file(&log);
        text.lines().map(str::to_owned).collect()
    };
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
    let mut interpreter = vec![];
    // `analyze` first in `args` runs `ici analyze` with the rest (its `--language` option).
    let (analyze_args, args) = match args.split_first() {
        Some((&"analyze", rest)) => (true, rest),
        _ => (false, args),
    };
    let (plan, verify) = if config.is_some() && !analyze_args {
        let plan = ici(&[&["next", "plan"], args].concat());
        interpreter.push(starts());
        let verify = ici(&[
            &["next", "verify"],
            args,
            &["--no-cache", "--result", "out/result.json"],
        ]
        .concat());
        (plan, verify)
    } else {
        let plan = ici(&[&["analyze", "--plan"], args].concat());
        interpreter.push(starts());
        (
            plan,
            ici(&[
                &["analyze"],
                args,
                &["--no-cache", "--result", "out/result.json"],
            ]
            .concat()),
        )
    };
    interpreter.push(starts());
    let analyze = ici(&["analyze", "--no-cache", "--result", "out/analyze.json"]);
    interpreter.push(starts());
    // No source or link target is written.
    assert_eq!(contents(), before);
    Run {
        exit: verify.status.code().unwrap(),
        plan_exit: plan.status.code().unwrap(),
        plan: String::from_utf8(plan.stdout).unwrap(),
        analyze_exit: analyze.status.code().unwrap(),
        result: serde_json::from_slice(&fs::read(root.join("out/result.json")).unwrap()).unwrap(),
        interpreter,
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
    let args: Vec<&str> = run_record["args"]
        .as_array()
        .map(|args| args.iter().map(|arg| arg.as_str().unwrap()).collect())
        .unwrap_or_default();
    let native = run(
        run_record["config"].as_str(),
        run_record["files"].as_object().unwrap(),
        &args,
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
        &[],
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

/// A required `python.compat-runtime` beside the link. Frozen's `component_targets`
/// (application/testing.py:46-60) drops the resolved target from the runtime step: with the link
/// alone the step is blocked "no Python sources in the component's scope" (:296), and beside a
/// real file it runs `-VV` and compileall on that file only. Native does not start the project
/// interpreter where the component's scope is incomplete: the step is blocked with the link, in
/// `next plan` as in `next verify`.
#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn the_runtime_check_does_not_start_the_interpreter_beside_an_outside_link() {
    for (name, link) in [
        ("runtime-outside-only", "app/m.py"),
        ("runtime-outside-beside-real", "app/link.py"),
    ] {
        let (frozen, native) = scenario(name);
        assert_eq!(frozen["exit_code"], 3, "{name}");
        let frozen_runtime: Vec<_> = frozen["states"]
            .as_object()
            .unwrap()
            .iter()
            .filter(|(task, _)| task.starts_with("app.python.compat-runtime"))
            .map(|(task, state)| (task.clone(), state.clone()))
            .collect();
        if name == "runtime-outside-only" {
            assert_eq!(
                frozen_runtime,
                vec![("app.python.compat-runtime".to_owned(), json!("BLOCKED"))]
            );
            assert!(
                frozen["gate"]["reasons"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(
                        "app.python.compat-runtime: no Python sources in the component's scope"
                    ))
            );
            assert_eq!(frozen["interpreter_starts"]["verify"], json!([]));
        } else {
            assert_eq!(
                frozen_runtime,
                vec![
                    (
                        "app.python.compat-runtime.compileall".to_owned(),
                        json!("SUCCEEDED")
                    ),
                    (
                        "app.python.compat-runtime.version".to_owned(),
                        json!("SUCCEEDED")
                    ),
                ]
            );
            assert!(
                frozen["interpreter_starts"]["verify"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|line| line.as_str().unwrap().contains("compileall"))
            );
        }
        assert_eq!(
            (native.exit, native.analyze_exit, native.plan_exit),
            (3, 3, 0),
            "{name}"
        );
        assert_eq!(native.interpreter, vec![Vec::<String>::new(); 3], "{name}");
        assert!(
            !native.plan.contains("compileall"),
            "{name}: {}",
            native.plan
        );
        let reason = reason(link);
        let states = states(&native.result);
        let runtime: Vec<_> = states
            .iter()
            .filter(|(task, _)| task.starts_with("app.python.compat-runtime"))
            .collect();
        assert!(!runtime.is_empty(), "{states:?}");
        for (task, state) in runtime {
            assert_eq!(state, "BLOCKED", "{name}: {task}");
            // The plan previews what verify does: the same step, blocked with the same reason,
            // never the interpreter argv.
            assert!(
                native
                    .plan
                    .contains(&format!("  {task}: blocked — {reason}\n")),
                "{name}: {}",
                native.plan
            );
            assert!(
                native.result["gate"]["reasons"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(format!("{task}: {reason}"))),
                "{name}: {}",
                native.result["gate"]
            );
        }
        assert!(
            !native.result["gate"]["reasons"]
                .to_string()
                .contains("no Python sources"),
            "{name}"
        );
    }
}

/// Native policy for schema 1 `python.artifact` beside an outside link (frozen's next catalog has
/// no `python.artifact`, so nothing here is compared with frozen). `app` holds the link; `pkg` is
/// an independent package that reads only its own sources and metadata.
struct Packaged {
    _temporary: tempfile::TempDir,
    root: std::path::PathBuf,
    plan: String,
    exit: i32,
    result: Value,
    /// Every `git` start of `next plan` and `next verify`.
    git: Vec<String>,
}

const PACKAGE_INIT: &str = "VALUE = 1\n";

/// `links` replace a path with a symlink to the given target after `files` are written.
fn packaged(
    extra: &str,
    link: bool,
    oversized: Option<&str>,
    files: &[(&str, &str)],
    links: &[(&str, &str)],
) -> Packaged {
    let temporary = tempfile::tempdir().unwrap();
    let base = temporary.path().canonicalize().unwrap();
    let root = base.join("project");
    let shims = base.join("shims");
    fs::create_dir_all(&shims).unwrap();
    let log = base.join("git.log");
    let shim = shims.join("git");
    fs::write(
        &shim,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> {:?}\nexec /usr/bin/git \"$@\"\n",
            log.display().to_string()
        ),
    )
    .unwrap();
    fs::set_permissions(&shim, fs::Permissions::from_mode(0o755)).unwrap();
    let mut tree: Vec<(&str, &str)> = vec![
        ("app/real.py", REAL),
        ("other/m.py", REAL),
        (
            "pkg/pyproject.toml",
            "[project]\nname = \"pkg\"\nversion = \"1.0\"\n",
        ),
        ("pkg/pkg/__init__.py", PACKAGE_INIT),
    ];
    tree.extend_from_slice(files);
    for (path, text) in tree {
        fs::create_dir_all(root.join(path).parent().unwrap()).unwrap();
        fs::write(root.join(path), text).unwrap();
    }
    if link {
        std::os::unix::fs::symlink("../other/m.py", root.join("app/link.py")).unwrap();
    }
    for (path, target) in links {
        let _ = fs::remove_file(root.join(path));
        std::os::unix::fs::symlink(target, root.join(path)).unwrap();
    }
    if let Some(path) = oversized {
        let file = fs::File::create(root.join(path)).unwrap();
        file.set_len(16 * 1024 * 1024 + 1).unwrap();
    }
    let optional = [
        "lint",
        "format",
        "type",
        "test",
        "coverage",
        "compat-runtime",
    ]
    .iter()
    .map(|name| format!("[checks.\"python.{name}\"]\nenabled = false\n"))
    .collect::<String>();
    fs::write(
        root.join("ici.toml"),
        format!(
            "schema_version = 1\n[workspace]\nname = \"demo\"\nprofile = \"deep\"\n\
             [[components]]\nid = \"app\"\nroot = \"app\"\nlanguages = [\"python\"]\n\
             [[components]]\nid = \"pkg\"\nroot = \"pkg\"\nlanguages = [\"python\"]\n\
             {optional}[checks.\"python.artifact\"]\nenabled = true\n{extra}"
        ),
    )
    .unwrap();
    assert!(
        Command::new("/usr/bin/git")
            .args(["init", "-q", "."])
            .current_dir(&root)
            .status()
            .unwrap()
            .success()
    );
    let _ = fs::remove_file(&log);
    let ici = |arguments: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(arguments)
            .current_dir(&root)
            .env_clear()
            .env("PATH", format!("{}:/usr/bin:/bin", shims.display()))
            .env("HOME", &base)
            .env("LANG", "C.UTF-8")
            .output()
            .unwrap()
    };
    let plan = ici(&["next", "plan"]);
    assert_eq!(plan.status.code(), Some(0));
    assert!(!root.join(".ici").exists(), "next plan writes nothing");
    let verify = ici(&[
        "next",
        "verify",
        "--no-cache",
        "--result",
        "out/result.json",
    ]);
    let git = fs::read_to_string(&log)
        .unwrap_or_default()
        .lines()
        .map(str::to_owned)
        .collect();
    Packaged {
        plan: String::from_utf8(plan.stdout).unwrap(),
        exit: verify.status.code().unwrap(),
        result: serde_json::from_slice(&fs::read(root.join("out/result.json")).unwrap()).unwrap(),
        git,
        root,
        _temporary: temporary,
    }
}

fn check<'a>(result: &'a Value, task: &str) -> &'a Value {
    result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["task_id"] == task)
        .unwrap_or_else(|| panic!("{task}: {result}"))
}

/// Whether `pkg` published its real source and its manifest.
fn published(run: &Packaged) -> bool {
    fs::read(run.root.join("pkg/v1.0/x86_64/lib/pkg/__init__.py")).ok()
        == Some(PACKAGE_INIT.as_bytes().to_vec())
        && fs::read_dir(run.root.join(".ici/rust-v1/python-artifacts"))
            .is_ok_and(|entries| entries.count() > 0)
}

/// No `v*/` release tree under `app`.
fn app_untouched(run: &Packaged) -> bool {
    fs::read_dir(run.root.join("app")).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with('v')
    })
}

#[test]
fn an_independent_package_still_publishes_beside_an_outside_link() {
    let link = reason("app/link.py");
    // Control: no link, both packages publish.
    let control = packaged("", false, None, &[], &[]);
    assert_eq!(control.exit, 0, "{}", control.result["gate"]);
    assert_eq!(
        check(&control.result, "pkg.python.artifact")["state"],
        "SUCCEEDED"
    );
    assert_eq!(
        check(&control.result, "app.python.artifact")["state"],
        "SUCCEEDED"
    );
    assert!(published(&control));

    for (extra, required) in [("", false), ("required = true\n", true)] {
        let run = packaged(extra, true, None, &[], &[]);
        // The link's own component: blocked in the plan and in verify, with no version probe
        // and no release tree.
        assert!(
            run.plan
                .contains(&format!("  app.python.artifact: blocked — {link}\n")),
            "{}",
            run.plan
        );
        assert!(
            !run.plan.contains("app.python.artifact writes"),
            "{}",
            run.plan
        );
        let app = check(&run.result, "app.python.artifact");
        assert_eq!(app["state"], "BLOCKED", "{app}");
        assert!(
            !run.git.iter().any(|line| line.starts_with("describe")),
            "{:?}",
            run.git
        );
        assert!(app_untouched(&run));
        // The independent package publishes its own source and manifest.
        assert_eq!(
            check(&run.result, "pkg.python.artifact")["state"],
            "SUCCEEDED"
        );
        assert!(published(&run));
        // The run stays incomplete for app; pkg's success is not the run's.
        assert_eq!(run.exit, 3, "{}", run.result["gate"]);
        assert_eq!(run.result["gate"]["selected"], "INCOMPLETE");
        let reasons = run.result["gate"]["reasons"].as_array().unwrap();
        assert_eq!(
            reasons.contains(&json!(format!("app.python.artifact: {link}"))),
            required,
            "{reasons:?}"
        );
        // Publishing is not an input change, and pkg contributes no reason.
        assert!(
            !reasons.iter().any(|reason| {
                let reason = reason.as_str().unwrap();
                reason.starts_with("pkg.") || reason.contains("inputs changed")
            }),
            "{reasons:?}"
        );
    }
}

/// Another cause still refuses every preparation, even when the link's reason sorts first.
#[test]
fn a_size_limit_beside_an_outside_link_still_refuses_every_preparation() {
    let run = packaged("required = true\n", true, Some("app/zbig.py"), &[], &[]);
    let size = "app/zbig.py was not analyzed: it exceeds the 16 MiB per-file input limit";
    assert_eq!(run.exit, 3);
    let pkg = check(&run.result, "pkg.python.artifact");
    assert_eq!(pkg["state"], "BLOCKED", "{pkg}");
    assert!(
        run.result["gate"]["reasons"]
            .as_array()
            .unwrap()
            .contains(&json!(format!(
                "pkg.python.artifact: artifact preparation refused: {size}"
            ))),
        "{}",
        run.result["gate"]
    );
    assert!(!published(&run));
}

/// Independence not proven keeps the refusal, in the plan as in verify. Through the schema 1
/// CLI a package cannot read beyond its own sources and metadata: a metadata (or source) link is
/// refused by the no-follow snapshot before the policy, `[build.artifacts]` is a configuration
/// error and compiled inputs come only from C++ preparation. The policy's own predicate is
/// exercised on a declared input below.
#[test]
fn a_package_reading_through_a_link_keeps_the_refusal_beside_an_outside_link() {
    let run = packaged(
        "required = true\n",
        true,
        None,
        &[(
            "meta/pyproject.toml",
            "[project]\nname = \"pkg\"\nversion = \"1.0\"\n",
        )],
        &[("pkg/pyproject.toml", "../meta/pyproject.toml")],
    );
    let refused = "snapshot pkg/pyproject.toml: Too many levels of symbolic links (os error 40)";
    assert!(
        run.plan
            .contains(&format!("  pkg.python.artifact: blocked — {refused}\n")),
        "{}",
        run.plan
    );
    assert_eq!(
        check(&run.result, "pkg.python.artifact")["state"],
        "BLOCKED"
    );
    assert!(
        run.result["gate"]["reasons"]
            .as_array()
            .unwrap()
            .contains(&json!(format!("pkg.python.artifact: {refused}"))),
        "{}",
        run.result["gate"]
    );
    assert!(!run.root.join("pkg/v1.0").exists());
    assert_eq!(run.exit, 3);
    // A declared artifact table is a schema 1 configuration error.
    fs::write(
        run.root.join("ici.toml"),
        format!(
            "{}[build.artifacts]\npython_wheel = [\"dist/*.whl\"]\n",
            fs::read_to_string(run.root.join("ici.toml")).unwrap()
        ),
    )
    .unwrap();
    let plan = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["next", "plan"])
        .current_dir(&run.root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", run._temporary.path())
        .output()
        .unwrap();
    assert_eq!(plan.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&plan.stderr).contains("build: unknown configuration key"),
        "{}",
        String::from_utf8_lossy(&plan.stderr)
    );
}

/// The independence predicate itself: a declared `[build.artifacts]` input (a schema 2 project,
/// where the table exists) is not the package's own source, so the policy would refuse it.
#[test]
fn a_declared_artifact_input_is_not_an_own_source() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path();
    fs::create_dir_all(root.join("pkg/pkg")).unwrap();
    fs::create_dir_all(root.join("dist")).unwrap();
    fs::write(root.join("pkg/pkg/__init__.py"), PACKAGE_INIT).unwrap();
    fs::write(
        root.join("pkg/pyproject.toml"),
        "[project]\nname = \"pkg\"\nversion = \"1.0\"\n",
    )
    .unwrap();
    fs::write(root.join("dist/pkg-1.0-py3-none-any.whl"), "wheel\n").unwrap();
    for (declared, own) in [
        ("", true),
        (
            "[build.artifacts]\npython_wheel = [\"dist/*.whl\"]\n",
            false,
        ),
    ] {
        fs::write(
            root.join("ici.toml"),
            format!(
                "schema_version = 2\n[[components]]\nid = \"pkg\"\nroot = \"pkg\"\nlanguages = [\"python\"]\n{declared}"
            ),
        )
        .unwrap();
        let project =
            ici_config::resolve_with_environment(root, None, None, &BTreeMap::new()).unwrap();
        let component = &project.components[0];
        let plan =
            ici_cli::artifact_build::describe(&project, component, "pkg.python.artifact").unwrap();
        assert_eq!(
            plan.reads_only_own_sources(&project, component),
            own,
            "{declared}"
        );
    }
}

/// A link of a component or language the selection leaves out is outside the run: frozen's
/// outcome, with no scope note and no link reason. `--require-full` keeps its own omitted-component
/// incompleteness, never the link's.
#[test]
fn a_link_outside_the_selection_keeps_the_frozen_outcome() {
    for name in [
        "unselected-component",
        "unselected-component-require-full",
        "unselected-language",
    ] {
        let (frozen, native) = scenario(name);
        assert_eq!(
            native.exit, frozen["exit_code"],
            "{name}: {}",
            native.result["gate"]
        );
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
        assert!(
            !native.plan.contains("selection incomplete"),
            "{name}: {}",
            native.plan
        );
        assert!(
            !native
                .result
                .to_string()
                .contains("outside the root of component"),
            "{name}"
        );
    }
    assert_eq!(
        oracle()["runs"]["unselected-component"]["frozen"]["exit_code"],
        0
    );
}
