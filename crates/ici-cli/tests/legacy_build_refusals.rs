//! Coordinator ruling e (decisions.md "Make 준비 거부 문구"): native may refuse a Make build whose
//! working directory or executable is unusable at plan time with a more specific sentence, only
//! where the failure class, the exit code and the write side effects equal frozen
//! (`tests/rust-parity/cpp/build-refusals-oracle.json`, `capture_build_refusals.py`). A workdir
//! symlink that stays in the project is a real directory and builds like frozen; one that leaves
//! is refused. The only file allowance is the approved shadow location (decisions.md "native C++
//! shadow build 위치"): native does not create the Make shadow for a build it refuses before
//! preparation, and it records its owner marker there when it does.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt, path::Path};

/// Directories a run owns that are not the project's outcome.
const PRIVATE: [&str; 4] = [".ici", ".ici-native", "__pycache__", ".git"];

fn listing(base: &Path, directory: &Path, names: &mut Vec<String>) {
    let mut entries: Vec<_> = fs::read_dir(directory)
        .unwrap()
        .map(|entry| entry.unwrap())
        .collect();
    entries.sort_by_key(fs::DirEntry::file_name);
    for entry in entries {
        let name = entry.file_name().to_string_lossy().into_owned();
        let relative = entry
            .path()
            .strip_prefix(base)
            .unwrap()
            .display()
            .to_string();
        // Frozen's os.walk lists a symlink to a directory as a directory and does not enter it.
        if entry.path().is_dir() {
            if !PRIVATE.contains(&name.as_str()) {
                names.push(format!("{relative}/"));
                if !entry.file_type().unwrap().is_symlink() {
                    listing(base, &entry.path(), names);
                }
            }
        } else {
            names.push(relative);
        }
    }
}

/// One refused build through the real CLI.
fn run(oracle: &Value, case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().canonicalize().unwrap();
    let base = root.join("project");
    let tools = root.join("tools");
    fs::create_dir(&tools).unwrap();
    fs::write(tools.join("make"), oracle["fake"].as_str().unwrap()).unwrap();
    fs::set_permissions(tools.join("make"), fs::Permissions::from_mode(0o755)).unwrap();
    fs::write(tools.join("builder"), oracle["builder"].as_str().unwrap()).unwrap();
    fs::set_permissions(tools.join("builder"), fs::Permissions::from_mode(0o755)).unwrap();
    let home = root.join("home");
    fs::create_dir(&home).unwrap();
    let log = root.join("make.log");
    for (path, text) in oracle["tree"].as_object().unwrap() {
        let file = base.join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(file, text.as_str().unwrap()).unwrap();
    }
    std::os::unix::fs::symlink("real", base.join("linked")).unwrap();
    fs::create_dir(root.join("outside")).unwrap();
    std::os::unix::fs::symlink("../outside", base.join("escaped")).unwrap();
    fs::write(
        base.join("ici.toml"),
        format!(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n[build.make]\nenabled=true\n{}\n",
            case["make"].as_str().unwrap()
        ),
    )
    .unwrap();
    fs::create_dir(base.join(".git")).unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        (
            "PATH".to_owned(),
            format!("{}:/usr/bin:/bin", tools.display()),
        ),
        ("ICI_MAKE_LOG".to_owned(), log.display().to_string()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            root.display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "build"]).unwrap();
    // A configuration refusal never reaches a run: the CLI prints it to stderr and exits 2.
    let (exit_code, stdout) = match commands::dispatch(&invocation, &base, &environment) {
        Ok(output) => (output.exit_code, output.stdout),
        Err(_) => (2, String::new()),
    };
    let mut files = vec![];
    listing(&base, &base, &mut files);
    files.sort();
    serde_json::json!({
        "exit_code": exit_code,
        "stdout": stdout,
        "files": files,
        "make_ran": log.exists(),
    })
}

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/build-refusals-oracle.json"
    ))
    .unwrap()
}

#[test]
fn a_refused_make_build_has_the_frozen_failure_class_and_exit_code() {
    let oracle = oracle();
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let actual = run(&oracle, case);
        for key in ["exit_code", "stdout"] {
            assert_eq!(actual[key], case["frozen"][key], "{name}: {key}");
        }
    }
}

/// The approved shadow location difference, and nothing else (see the module comment).
fn apply_shadow_allowance(name: &str, native: &mut Value, frozen: &mut Value) {
    let drop = |value: &mut Value, names: &[&str]| {
        value["files"]
            .as_array_mut()
            .unwrap()
            .retain(|file| !names.contains(&file.as_str().unwrap()));
    };
    drop(native, &["build/ici-make/.ici-owner.json"]);
    if ["missing-workdir", "unavailable-executable"].contains(&name) {
        drop(frozen, &["build/", "build/ici-make/"]);
    }
}

#[test]
fn a_refused_make_build_leaves_the_frozen_files_and_process_trace() {
    let oracle = oracle();
    let mut differences = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let mut actual = run(&oracle, case);
        let mut frozen = case["frozen"].clone();
        apply_shadow_allowance(name, &mut actual, &mut frozen);
        if actual != frozen {
            differences.push(format!("{name}:\n native {actual}\n frozen {frozen}"));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}
