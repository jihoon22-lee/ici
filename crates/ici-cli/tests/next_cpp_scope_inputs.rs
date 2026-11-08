//! Which C++ check scopes a `next verify` result moves when one kind of input changes, against
//! frozen (`tests/rust-parity/cpp/capture_cpp_scope_inputs.py` → `cpp-scope-inputs-oracle.json`).
//!
//! Frozen moves every C++ scope for an added source and the scope of one diagnostics unit for
//! a changed compile command; source or header contents, an unrelated file, `.clang-tidy`, a
//! comment in `ici.toml` and the environment move nothing. Native moves the same scopes and,
//! where its check reads more, more (`STRICTER`). Frozen reports one `cpp.diagnostics` record per
//! translation unit; native one record whose steps carry those unit ids (decisions.md "next
//! `cpp.diagnostics`·`cpp.test` 실행 단위"), so a moved unit is a moved native record.
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    process::{Command, Stdio},
};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/cpp-scope-inputs-oracle.json");
const DIAGNOSTICS: &str = "app.cpp.diagnostics";

/// Native moves these scopes in addition to frozen's. `cpp.compile` reads the compilation
/// database itself, so a changed command there moves its scope. A task gets the caller's whole
/// environment and its scope hashes it (decisions.md "도구 실행 환경"), so any variable moves the
/// check that starts the compiler; frozen hashed only the task's overlay.
const STRICTER: &[(&str, &[&str])] = &[
    ("database-flag", &["app.cpp.compile"]),
    ("inherited-environment", &[DIAGNOSTICS]),
    ("compiler-environment", &[DIAGNOSTICS]),
];

fn native(task: &str) -> &str {
    if task.starts_with(&format!("{DIAGNOSTICS}.")) {
        DIAGNOSTICS
    } else {
        task
    }
}

fn database(root: &Path, flags: &BTreeMap<&str, &str>) -> String {
    serde_json::json!(
        ["a.cpp", "b.cpp"]
            .iter()
            .map(|name| {
                let mut arguments = vec!["/usr/bin/g++".to_owned(), "-Wall".to_owned()];
                arguments.extend(flags.get(name).map(|flag| (*flag).to_owned()));
                arguments.extend(["-c".to_owned(), format!("app/{name}")]);
                serde_json::json!({
                    "directory": root.display().to_string(),
                    "file": format!("app/{name}"),
                    "arguments": arguments,
                })
            })
            .collect::<Vec<_>>()
    )
    .to_string()
}

fn write(root: &Path, path: &str, text: &str) {
    let text = match text
        .strip_prefix("<database:")
        .and_then(|rest| rest.strip_suffix('>'))
    {
        Some(flag) => {
            let (name, flag) = flag.split_once('=').unwrap();
            database(root, &BTreeMap::from([(name, flag)]))
        }
        None => text.to_owned(),
    };
    let path = root.join(path);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, text).unwrap();
}

/// (exit code, task → (scope digest, state)).
fn verify(
    root: &Path,
    base: &Path,
    environment: &BTreeMap<String, String>,
) -> (i32, BTreeMap<String, (String, String)>) {
    let status = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["next", "verify", "--result", "now.json"])
        .current_dir(root)
        .env_clear()
        .env("HOME", base)
        .env("PATH", "/usr/bin:/bin")
        .env("LC_ALL", "C.UTF-8")
        .env("GIT_CEILING_DIRECTORIES", base)
        .envs(environment)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .unwrap();
    let result: Value =
        serde_json::from_str(&fs::read_to_string(root.join("now.json")).unwrap()).unwrap();
    let checks = result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            (
                check["task_id"].as_str().unwrap().to_owned(),
                (
                    check["scope_digest"].as_str().unwrap().to_owned(),
                    check["state"].as_str().unwrap().to_owned(),
                ),
            )
        })
        .collect();
    (status.code().unwrap(), checks)
}

#[test]
fn one_kind_of_input_moves_the_cpp_scopes_frozen_moved() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let stricter: BTreeMap<_, _> = STRICTER.iter().copied().collect();
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap();
        fs::write(base.join(".hg"), "").unwrap();
        let root = base.join("project");
        fs::create_dir_all(root.join("app")).unwrap();
        fs::write(root.join("app/a.hpp"), oracle["header"].as_str().unwrap()).unwrap();
        fs::write(root.join("app/a.cpp"), oracle["source"].as_str().unwrap()).unwrap();
        fs::write(root.join("app/b.cpp"), oracle["other"].as_str().unwrap()).unwrap();
        fs::write(
            root.join("compile_commands.json"),
            database(&root, &BTreeMap::new()),
        )
        .unwrap();
        fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
        let (_, before) = verify(&root, &base, &BTreeMap::new());
        for (path, text) in case["files"].as_object().unwrap() {
            write(&root, path, text.as_str().unwrap());
        }
        let environment: BTreeMap<String, String> = case["environment"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), value.as_str().unwrap().to_owned()))
            .collect();
        let (code, after) = verify(&root, &base, &environment);
        assert_eq!(serde_json::json!(code), case["exit"], "{name}: exit");
        // Every frozen unit record has the state of the one native record.
        let states: BTreeMap<String, String> = after
            .iter()
            .map(|(task, (_, state))| (task.clone(), state.clone()))
            .collect();
        let frozen_states: BTreeMap<String, String> = case["states"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(task, state)| (native(task).to_owned(), state.as_str().unwrap().to_owned()))
            .collect();
        assert_eq!(states, frozen_states, "{name}: the same checks and states");
        let moved: BTreeSet<String> = after
            .iter()
            .filter(|(task, (digest, _))| before.get(*task).map(|(old, _)| old) != Some(digest))
            .map(|(task, _)| task.clone())
            .collect();
        let mut expected: BTreeSet<String> = case["moved_tasks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|task| native(task.as_str().unwrap()).to_owned())
            .collect();
        expected.extend(
            stricter
                .get(name.as_str())
                .into_iter()
                .flat_map(|tasks| tasks.iter().map(|task| (*task).to_owned())),
        );
        assert_eq!(moved, expected, "{name}");
    }
}
