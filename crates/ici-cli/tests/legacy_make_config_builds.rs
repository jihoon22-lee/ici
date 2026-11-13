//! Legacy `ici build` for every `[build.make]` key value against the frozen CLI's own rendered
//! BuildEngine result (`tests/rust-parity/cpp/capture_make_builds.py`): accepted values, values the
//! frozen configuration loader refuses (exit 2, nothing runs) and failing steps, with the Make calls
//! (working directory and argv) a `make` wrapper logged.
use serde_json::{Value, json};
use std::{collections::BTreeSet, fs, path::Path};

/// Directories a run owns that are not the project's outcome.
const PRIVATE: [&str; 3] = [".ici", "__pycache__", ".git"];

fn listing(root: &Path) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).unwrap() {
            let entry = entry.unwrap();
            let name = entry.file_name().to_string_lossy().into_owned();
            let relative = entry
                .path()
                .strip_prefix(root)
                .unwrap()
                .display()
                .to_string();
            if entry.file_type().unwrap().is_dir() {
                if PRIVATE.contains(&name.as_str()) {
                    continue;
                }
                names.insert(format!("{relative}/"));
                pending.push(entry.path());
            } else {
                names.insert(relative);
            }
        }
    }
    names
}

/// The created project paths that are the build's outcome. Frozen also leaves its empty owned
/// shadow directories under `build/` (the approved native shadow location difference); the files a
/// recipe writes there are compared.
fn outcome(paths: &Value) -> Vec<String> {
    paths
        .as_array()
        .unwrap()
        .iter()
        .filter_map(Value::as_str)
        .filter(|path| !(path.ends_with('/') && path.starts_with("build/")))
        .map(str::to_owned)
        .collect()
}

fn console(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn run(oracle: &Value, case: &Value, make: &str, compiler_dir: &str) -> Value {
    let work = tempfile::tempdir().unwrap();
    let work = work.path().canonicalize().map(|path| (work, path)).unwrap();
    let project = work.1.join("project");
    let tools = work.1.join("tools");
    fs::create_dir_all(&tools).unwrap();
    let wrapper = tools.join("make");
    fs::write(
        &wrapper,
        oracle["wrapper"].as_str().unwrap().replace("{make}", make),
    )
    .unwrap();
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&wrapper, fs::Permissions::from_mode(0o755)).unwrap();
    }
    let ici_toml = format!(
        "{}{}",
        oracle["head"].as_str().unwrap(),
        case["make"].as_str().unwrap()
    );
    for (name, text) in oracle["tree"]
        .as_object()
        .unwrap()
        .iter()
        .chain(case["files"].as_object().unwrap())
        .map(|(name, text)| (name.clone(), text.as_str().unwrap().to_owned()))
        .chain([("ici.toml".to_owned(), ici_toml)])
    {
        let path = project.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    let before = listing(&project);
    let log = work.1.join("make.log");
    let process = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
        .arg("build")
        .current_dir(&project)
        .env_clear()
        .env(
            "PATH",
            format!("{}:{compiler_dir}:/usr/bin:/bin", tools.display()),
        )
        .env("HOME", &work.1)
        .env("LC_ALL", "C.UTF-8")
        .env("ICI_MAKE_LOG", &log)
        .env("GIT_CEILING_DIRECTORIES", &work.1)
        .output()
        .unwrap();
    let created = listing(&project)
        .difference(&before)
        .cloned()
        .collect::<Vec<_>>();
    let calls = fs::read_to_string(&log)
        .unwrap_or_default()
        .lines()
        .map(|line| line.replacen(&project.display().to_string(), "<project>", 1))
        .collect::<Vec<_>>();
    let engine = fs::read_dir(project.join(".ici/rust-v1/details/result.json"))
        .ok()
        .and_then(|mut runs| runs.next())
        .and_then(|run| fs::read(run.unwrap().path().join("details.json")).ok())
        .map(|bytes| serde_json::from_slice::<Value>(&bytes).unwrap())
        .and_then(|details| {
            details["results"]
                .as_array()
                .unwrap()
                .iter()
                .find(|engine| engine["engine_name"] == "build")
                .cloned()
        })
        .map(|engine| {
            json!({
                "status": engine["status"],
                "summary": engine["summary"],
                "evidence": engine["evidence"],
                "targets": engine["targets"].as_array().unwrap().iter().map(|target| json!({
                    "target_name": target["target_name"],
                    "status": target["status"],
                    "message": target["message"],
                    "start_line": target["start_line"],
                    "file_path": target["file_path"],
                })).collect::<Vec<_>>(),
            })
        })
        .unwrap_or(Value::Null);
    json!({
        "exit_code": process.status.code().unwrap(),
        "stdout": String::from_utf8_lossy(&process.stdout).trim(),
        "stderr": String::from_utf8_lossy(&process.stderr).trim(),
        "engine": engine,
        "calls": calls,
        "created": created,
    })
}

#[test]
#[ignore = "explicit actual GNU Make legacy builds for every build.make key (ICI_TEST_MAKE, ICI_TEST_CXX)"]
fn every_build_make_key_builds_like_the_frozen_recorded_capture() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/make-builds-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let make = std::env::var("ICI_TEST_MAKE").expect("explicit ICI_TEST_MAKE");
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit ICI_TEST_CXX");
    let compiler_dir = Path::new(&compiler).parent().unwrap().display().to_string();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 32);
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let actual = run(&oracle, case, &make, &compiler_dir);
        assert_eq!(actual["exit_code"], frozen["exit_code"], "{name}: {actual}");
        // Every Make call: its working directory and argv, in order.
        assert_eq!(actual["calls"], frozen["calls"], "{name}");
        assert_eq!(
            outcome(&actual["created"]),
            outcome(&frozen["created"]),
            "{name} created"
        );
        if frozen["engine"].is_null() {
            // Refused by the configuration loader before any engine ran: frozen prints
            // "Configuration error: <key> ..." and exits 2; native refuses the same key with its
            // located wording (the config rows' recorded wording difference) and exits 2.
            let key = frozen["stderr"]
                .as_str()
                .unwrap()
                .strip_prefix("Configuration error: ")
                .and_then(|rest| rest.split([' ', '[']).next())
                .unwrap();
            let stderr = actual["stderr"].as_str().unwrap();
            assert!(
                stderr.contains("Configuration error") && stderr.contains(key),
                "{name}: {stderr}"
            );
            assert!(actual["engine"].is_null(), "{name}");
            continue;
        }
        assert_eq!(
            console(actual["stdout"].as_str().unwrap()),
            console(frozen["stdout"].as_str().unwrap()),
            "{name}"
        );
        assert_eq!(actual["engine"], frozen["engine"], "{name}");
    }
}
