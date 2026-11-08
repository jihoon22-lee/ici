//! Coordinator ruling e (decisions.md "Make 준비 거부 문구"): native refuses a Make build whose
//! working directory or executable is unusable at plan time, with a more specific sentence than
//! frozen. That is approved only where the failure class, the exit code and the files a run
//! leaves equal frozen (`tests/rust-parity/cpp/build-refusals-oracle.json`,
//! `capture_build_refusals.py`). Failure class and exit code are equal. The side effects are not
//! yet: frozen leaves `build/ici-make/` and `<version>/x86_64/{bin,lib}/` and runs Make in a
//! symlinked working directory inside the project, so that comparison stays an open, ignored
//! test until the coordinator rules.
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
    let home = root.join("home");
    fs::create_dir(&home).unwrap();
    let log = root.join("make.log");
    for (path, text) in oracle["tree"].as_object().unwrap() {
        let file = base.join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(file, text.as_str().unwrap()).unwrap();
    }
    std::os::unix::fs::symlink("real", base.join("linked")).unwrap();
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
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let mut files = vec![];
    listing(&base, &base, &mut files);
    files.sort();
    serde_json::json!({
        "exit_code": output.exit_code,
        "stdout": output.stdout,
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

#[test]
#[ignore = "open: ruling e's side-effect condition is unmet (frozen leaves build/ici-make and <version>/x86_64/{bin,lib} and runs Make in a contained symlinked workdir); decisions.md \"Make 준비 거부 문구\""]
fn a_refused_make_build_leaves_the_frozen_files_and_process_trace() {
    let oracle = oracle();
    let mut differences = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let actual = run(&oracle, case);
        if actual != case["frozen"] {
            differences.push(format!(
                "{name}:\n native {actual}\n frozen {}",
                case["frozen"]
            ));
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}
