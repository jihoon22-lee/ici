//! Frozen next refusals planned by `application/testing.py`, and the schema 1 rule that naming a
//! check without `required` leaves it not required (`config/composition.py:74`,
//! `application/selection.py:252`). `tests/rust-parity/validation/next-refusals-oracle.json`.
//! Only `git` and the project interpreter are shimmed; every other check needing a tool is disabled.
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/next-refusals-oracle.json");

/// decisions.md "next plan 거부 문구": frozen's text and the more specific native text.
const RULED: [(&str, &str); 2] = [
    (
        "declared test_paths matched no files",
        "test target tests/none matched no existing input",
    ),
    (
        "python.coverage reads the test run's data — enable python.test",
        "needs app.python-test-evidence, which no selected check produces — python.test produces \
         it and is disabled (enabled = false)",
    ),
];
const COMPAT: &str = "app.python.compat: python metadata unreadable: ";
/// The scenarios whose full record is compared: every probe, write, metric and source fact.
const STRICT: [&str; 5] = [
    "test-paths-none",
    "coverage-without-test",
    "metadata-required",
    "metadata-advisory",
    "metadata-floor-invalid",
];
/// Frozen `python.dup`'s note, which native does not carry: native applies the bounded AST-shape
/// clustering under `python_semantic = auto` (closure/python.json `check:python.dup`,
/// decisions.md "next `python.dup`의 AST-shape 미지원 안내").
const FROZEN_CLONE_NOTE: &str =
    "AST-shape semantic clustering is not carried yet — matches are lexical Type-2 clones only";
/// Native `python.dead`'s added explanatory limitation (decisions.md "next `python.dead`의 heuristic
/// 안내").
const NATIVE_DEAD_NOTE: &str = "Private-symbol references and local unreachable statements are \
                                bounded static heuristics; reflective exports and runtime \
                                reachability are not proven";
/// Native's source-identity note for a repository without a HEAD (`next_output_options.rs`).
const NATIVE_HEAD_NOTE: &str = "source.git.head unavailable; source cleanliness is unknown";
/// The one VCS probe of the verify runs (native adds its read-only flags).
const FROZEN_GIT: &str = "git rev-parse HEAD";
const NATIVE_GIT: &str = "git --no-optional-locks -c core.fsmonitor=false -c \
                          core.untrackedCache=false rev-parse --verify HEAD";

fn code(message: &str) -> &'static str {
    if message.starts_with("project.requires-python") {
        "invalid-requires-python"
    } else {
        "invalid-metadata"
    }
}

/// The file a metadata failure names: the component's own, or the workspace's it fell back to.
fn metadata_path(scenario: &str) -> &'static str {
    if scenario == "metadata-workspace-floor" {
        "pyproject.toml"
    } else {
        "app/pyproject.toml"
    }
}

/// What native must print where frozen printed `text`.
fn ruled(scenario: &str, text: &str) -> String {
    if let Some(message) = text.strip_prefix(COMPAT) {
        return format!(
            "app.python.compat: {}:1:1: {}: {message}",
            metadata_path(scenario),
            code(message)
        );
    }
    RULED
        .iter()
        .fold(text.to_owned(), |text, (frozen, native)| {
            text.replace(frozen, native)
        })
}

struct Project {
    temporary: tempfile::TempDir,
    root: std::path::PathBuf,
    log: std::path::PathBuf,
    shims: std::path::PathBuf,
}

fn project(config: &str, files: &serde_json::Map<String, Value>) -> Project {
    let python = std::env::var("ICI_TEST_PYTHON").expect("set ICI_TEST_PYTHON");
    let temporary = tempfile::tempdir().unwrap();
    let base = temporary.path().canonicalize().unwrap();
    let log = base.join("tools.log");
    let shims = base.join("shims");
    fs::create_dir(&shims).unwrap();
    let shim = |name: &str, real: &str| {
        let script = shims.join(name);
        fs::write(
            &script,
            format!(
                "#!/bin/sh\nprintf '{name} %s\\n' \"$*\" >> {log:?}\nexec {real:?} \"$@\"\n",
                log = log.display().to_string()
            ),
        )
        .unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
        script
    };
    shim("git", "/usr/bin/git");
    let script = shim("project-python", &python);
    let root = base.join("project");
    for (path, content) in files {
        let target = root.join(path);
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        match content {
            Value::String(text) => fs::write(&target, text).unwrap(),
            link => std::os::unix::fs::symlink(link["link"].as_str().unwrap(), &target).unwrap(),
        }
    }
    let quoted = serde_json::to_string(&script.display().to_string()).unwrap();
    fs::write(
        root.join("ici.toml"),
        config.replace("<PYTHON_JSON>", &quoted),
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
    Project {
        temporary,
        root,
        log,
        shims,
    }
}

fn ici(project: &Project, arguments: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(arguments)
        .current_dir(&project.root)
        .env_clear()
        .env("PATH", format!("{}:/usr/bin:/bin", project.shims.display()))
        .env("HOME", project.temporary.path())
        .env("LANG", "C.UTF-8")
        .output()
        .unwrap()
}

/// Every path's content (a symlink's target) outside `.git`.
fn snapshot(root: &Path) -> BTreeMap<String, String> {
    fn digest(bytes: &[u8]) -> String {
        bytes
            .iter()
            .fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
                (hash ^ u64::from(*byte)).wrapping_mul(0x0100_0000_01b3)
            })
            .to_string()
    }
    let mut state = BTreeMap::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory).unwrap().map(Result::unwrap) {
            let path = entry.path();
            let relative = path.strip_prefix(root).unwrap().display().to_string();
            if relative == ".git" {
                continue;
            }
            let kind = entry.file_type().unwrap();
            if kind.is_symlink() {
                let target = fs::read_link(&path).unwrap();
                state.insert(relative, format!("link:{}", target.display()));
            } else if kind.is_dir() {
                pending.push(path);
            } else {
                state.insert(relative, digest(&fs::read(path).unwrap()));
            }
        }
    }
    state
}

fn changes(before: &BTreeMap<String, String>, after: &BTreeMap<String, String>) -> Value {
    let created: Vec<_> = after
        .keys()
        .filter(|path| !before.contains_key(*path))
        .collect();
    let modified: Vec<_> = after
        .iter()
        .filter(|(path, content)| before.get(*path).is_some_and(|old| old != *content))
        .map(|(path, _)| path)
        .collect();
    let deleted: Vec<_> = before
        .keys()
        .filter(|path| !after.contains_key(*path))
        .collect();
    json!({"created": created, "modified": modified, "deleted": deleted})
}

fn probes(project: &Project) -> Vec<String> {
    let text = fs::read_to_string(&project.log).unwrap_or_default();
    let _ = fs::remove_file(&project.log);
    text.lines().map(str::to_owned).collect()
}

fn observed(project: &Project, check: &str, strict: bool) -> Value {
    let before = snapshot(&project.root);
    let plan = ici(project, &["next", "plan"]);
    let plan_probes = probes(project);
    let after_plan = snapshot(&project.root);
    let stdout = String::from_utf8_lossy(&plan.stdout).into_owned();
    let plan_line = stdout
        .lines()
        .find(|line| line.contains(&format!(".{check}:")))
        .filter(|line| line.contains("blocked"))
        .map(str::trim);
    let verify = ici(
        project,
        &[
            "next",
            "verify",
            "--no-cache",
            "--result",
            "out/result.json",
        ],
    );
    let verify_probes = probes(project);
    let mut after_verify = snapshot(&project.root);
    // Root decision E2: the native archive of an explicit result lives only under the project's
    // `.ici/rust-v1/details/`; nothing is written beside the requested file.
    let archived: Vec<_> = after_verify
        .keys()
        .filter(|path| {
            path.starts_with(".ici/rust-v1/details/result.json/") && path.ends_with("/details.json")
        })
        .cloned()
        .collect();
    assert_eq!(archived.len(), 1, "{after_verify:?}");
    for path in archived {
        after_verify.remove(&path);
    }
    let result: Value =
        serde_json::from_slice(&fs::read(project.root.join("out/result.json")).unwrap()).unwrap();
    let mut states = BTreeMap::<String, Vec<String>>::new();
    for item in result["checks"].as_array().unwrap() {
        let entry = states
            .entry(item["check_id"].as_str().unwrap().to_owned())
            .or_default();
        let state = item["state"].as_str().unwrap().to_owned();
        if !entry.contains(&state) {
            entry.push(state);
            entry.sort();
        }
    }
    let mut source = result["identity"]["source"].clone();
    source.as_object_mut().unwrap().remove("digest");
    let mut checks = result["checks"].clone();
    for item in checks.as_array_mut().unwrap() {
        item.as_object_mut().unwrap().remove("scope_digest");
    }
    let mut limitations = result["limitations"].as_array().unwrap().clone();
    if strict {
        remove_once(&mut limitations, NATIVE_DEAD_NOTE, "native");
    }
    limitations.retain(|text| text != NATIVE_HEAD_NOTE);
    json!({
        "exit_code": verify.status.code(),
        "plan_exit_code": plan.status.code(),
        "plan_line": plan_line,
        "gate": result["gate"],
        "states": states,
        "blocked": result["execution"]["blocked_task_ids"],
        "failed": result["execution"]["failed_task_ids"],
        "required_complete": result["execution"]["required_complete"],
        "compileall_ran": verify_probes.iter().any(|line| line.contains("compileall")),
        "strict": {
            "plan_probes": plan_probes,
            "verify_probes": verify_probes,
            "plan_changes": changes(&before, &after_plan),
            "verify_changes": changes(&after_plan, &after_verify),
            "findings": result["findings"],
            "metrics": result["metrics"],
            "limitations": limitations,
            "scope": result["scope"],
            "source": source,
            "checks": checks,
        },
    })
}

/// Asserts `note` appears exactly once on that side, then removes it: an approved difference
/// only, never a filter that passes when the note is gone.
fn remove_once(limitations: &mut Vec<Value>, note: &str, side: &str) {
    let count = limitations.iter().filter(|text| *text == note).count();
    assert_eq!(
        count, 1,
        "{side} limitations carry {note:?} {count} times: {limitations:?}"
    );
    limitations.retain(|text| text != note);
}

fn native_probe(line: &str) -> String {
    if line == FROZEN_GIT {
        NATIVE_GIT.to_owned()
    } else {
        line.to_owned()
    }
}

/// The frozen record as native must reproduce it, under the named allowances only.
fn expected(scenario: &str, frozen: &Value, strict: bool) -> Value {
    let mut expected = frozen.clone();
    if let Some(line) = frozen["plan_line"].as_str() {
        expected["plan_line"] = json!(ruled(scenario, line));
    }
    expected["gate"]["reasons"] = frozen["gate"]["reasons"]
        .as_array()
        .unwrap()
        .iter()
        .map(|text| json!(ruled(scenario, text.as_str().unwrap())))
        .collect();
    if !strict {
        expected.as_object_mut().unwrap().remove("strict");
        return expected;
    }
    let record = &mut expected["strict"];
    record["verify_probes"] = record["verify_probes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|line| json!(native_probe(line.as_str().unwrap())))
        .collect();
    // Frozen reports the unreadable metadata twice in the run limitations; native's static check
    // reports it at the failing file (ruling e) and the runtime check keeps frozen's text.
    let mut first_metadata = true;
    let mut frozen_limitations = record["limitations"].as_array().unwrap().clone();
    remove_once(&mut frozen_limitations, FROZEN_CLONE_NOTE, "frozen");
    record["limitations"] = frozen_limitations
        .iter()
        .map(|text| {
            let text = text.as_str().unwrap();
            if let Some(message) = text.strip_prefix("python metadata unreadable: ")
                && std::mem::take(&mut first_metadata)
            {
                return json!(format!(
                    "{}:1:1: {}: {message}",
                    metadata_path(scenario),
                    code(message)
                ));
            }
            json!(
                RULED
                    .iter()
                    .fold(text.to_owned(), |text, (frozen, native)| {
                        text.replace(frozen, native)
                    })
            )
        })
        .collect();
    expected
}

fn compare(prefix: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let runs = oracle["runs"].as_object().unwrap();
    let selected: Vec<_> = runs.iter().filter(|(n, _)| n.starts_with(prefix)).collect();
    assert!(!selected.is_empty(), "{prefix}");
    for (name, run) in selected {
        let project = project(
            run["config"].as_str().unwrap(),
            run["files"].as_object().unwrap(),
        );
        let strict = STRICT.contains(&name.as_str());
        let mut native = observed(&project, run["check"].as_str().unwrap(), strict);
        if !strict {
            native.as_object_mut().unwrap().remove("strict");
        }
        assert_eq!(native, expected(name, &run["frozen"], strict), "{name}");
    }
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn a_check_named_without_required_is_not_required_in_schema_one() {
    compare("finding-");
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn declared_test_paths_that_match_nothing_block_the_test_check() {
    compare("test-paths-");
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn coverage_without_the_test_check_is_blocked() {
    compare("coverage-without-test");
}

#[test]
#[ignore = "explicit project interpreter (ICI_TEST_PYTHON)"]
fn unreadable_metadata_blocks_compat_runtime_before_any_interpreter_runs() {
    compare("metadata-");
}
