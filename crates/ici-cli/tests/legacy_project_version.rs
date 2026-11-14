//! The project name and version of a real `ici` run against frozen's capture, on fixtures described
//! by the capture itself (`tests/rust-parity/python/capture_project_metadata.py`). Refusals keep
//! frozen's message and exit without its traceback.
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

fn oracle() -> Value {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../tests/rust-parity/oracle/project-metadata.json"
    );
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

fn entries(value: &Value) -> impl Iterator<Item = (&String, &Value)> {
    value.as_object().into_iter().flatten()
}

fn items(value: &Value) -> impl Iterator<Item = &Value> {
    value.as_array().into_iter().flatten()
}

fn build(base: &Path, case: &Value, git_env: &Value) -> PathBuf {
    let root = base.join(case["directory"].as_str().unwrap_or("project"));
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("src/a.py"), "x = 1\n").unwrap();
    fs::write(
        root.join("ici.toml"),
        "type = \"python\"\n[project]\nname = \"p\"\n",
    )
    .unwrap();
    for (name, text) in entries(&case["files"]) {
        fs::write(root.join(name), text.as_str().unwrap()).unwrap();
    }
    for (name, size) in entries(&case["padding"]) {
        let mut text = fs::read_to_string(root.join(name)).unwrap();
        text.push('#');
        text.push_str(&"x".repeat(usize::try_from(size.as_u64().unwrap()).unwrap()));
        text.push('\n');
        fs::write(root.join(name), text).unwrap();
    }
    for (name, hex) in entries(&case["bytes"]) {
        let hex = hex.as_str().unwrap();
        let bytes: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|at| u8::from_str_radix(&hex[at..at + 2], 16).unwrap())
            .collect();
        fs::write(root.join(name), bytes).unwrap();
    }
    for (name, text) in entries(&case["outside"]) {
        fs::write(base.join(name), text.as_str().unwrap()).unwrap();
    }
    for name in items(&case["directories"]) {
        fs::create_dir(root.join(name.as_str().unwrap())).unwrap();
    }
    for name in items(&case["fifos"]) {
        let path = root.join(name.as_str().unwrap());
        assert!(Command::new("mkfifo").arg(path).status().unwrap().success());
    }
    for (name, target) in entries(&case["symlinks"]) {
        std::os::unix::fs::symlink(target.as_str().unwrap(), root.join(name)).unwrap();
    }
    for arguments in items(&case["git"]) {
        let mut git = Command::new("git");
        git.current_dir(&root)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", base);
        for (key, value) in entries(git_env) {
            git.env(key, value.as_str().unwrap());
        }
        for argument in items(arguments) {
            git.arg(argument.as_str().unwrap());
        }
        assert!(git.status().unwrap().success(), "{arguments}");
    }
    root
}

struct Native {
    exit: i32,
    stderr: String,
    project: Option<(String, String)>,
    report_name: Option<String>,
    title: Option<String>,
    ici_state: bool,
}

fn run(command: &str, case: &Value, git_env: &Value) -> Native {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = build(&base, case, git_env);
    let path = if case["no_git_on_path"] == true {
        "/nonexistent"
    } else {
        "/usr/bin:/bin"
    };
    let mut process = Command::new(env!("CARGO_BIN_EXE_ici"));
    process.arg(command);
    if command == "verify" {
        process.args([
            "--no-cache",
            "--report",
            "--html",
            "h.html",
            "--profile",
            "fast",
        ]);
    }
    let output = process
        .current_dir(&root)
        .env_clear()
        .env("HOME", &base)
        .env("PATH", path)
        .env("GIT_CEILING_DIRECTORIES", &base)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let report: Option<Value> = fs::read(root.join("verify_report.json"))
        .ok()
        .map(|bytes| serde_json::from_slice(&bytes).unwrap());
    let project = report.as_ref().map(|report| {
        let project = &report["analysis_context"]["project"];
        (
            project["name"].as_str().unwrap().to_owned(),
            project["version"].as_str().unwrap().to_owned(),
        )
    });
    let report_name = report
        .as_ref()
        .and_then(|report| report["project_name"].as_str().map(str::to_owned));
    Native {
        exit: output.status.code().unwrap(),
        stderr: String::from_utf8_lossy(&output.stderr)
            .replace(&root.display().to_string(), "<root>"),
        project,
        report_name,
        title: fs::read_to_string(root.join("h.html")).ok().map(|page| {
            let (_, tail) = page.split_once("<title>").unwrap();
            tail.split_once("</title>").unwrap().0.to_owned()
        }),
        ici_state: root.join(".ici/rust-v1/result.json").exists(),
    }
}

/// Frozen wraps a long traceback line at the console width.
fn unwrapped(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// The approved location of a native configuration error: `<file>:<line>:<column>: <key>: `.
fn without_location(text: &str) -> String {
    regex::Regex::new(r"<root>/ici\.toml:\d+:\d+: (\w+): ")
        .unwrap()
        .replace(text, "$1 ")
        .into_owned()
}

#[test]
fn every_captured_run_records_or_refuses_what_frozen_did() {
    let oracle = oracle();
    let mut failures = vec![];
    for captured in items(&oracle["runs"]) {
        let (command, name) = (
            captured["command"].as_str().unwrap(),
            captured["name"].as_str().unwrap(),
        );
        let frozen = &captured["frozen"];
        let native = run(command, &captured["case"], &oracle["git_env"]);
        let mut wrong = |what: String| failures.push(format!("{command} {name}: {what}"));

        // `dev.toml` is no native configuration (the approved config contract of
        // capability:legacy-command-config-result-projection), so its other keys are not checked.
        if name == "dev-unknown-key" {
            if native.exit == 2 || native.project != Some(("d".into(), "v1.0.0".into())) {
                wrong(format!("{}: {:?}", native.exit, native.project));
            }
            continue;
        }
        let expected = frozen["project"].as_object().map(|project| {
            (
                project["name"].as_str().unwrap().to_owned(),
                project["version"].as_str().unwrap().to_owned(),
            )
        });
        if native.project != expected {
            wrong(format!("project {:?}, frozen {expected:?}", native.project));
        }
        if native.title.as_deref() != frozen["title"].as_str() {
            wrong(format!(
                "title {:?}, frozen {}",
                native.title, frozen["title"]
            ));
        }
        // One run names one project, in every saved place.
        if let Some((name, _)) = &native.project
            && native.report_name.as_ref() != Some(name)
        {
            wrong(format!(
                "report name {:?}, project {name}",
                native.report_name
            ));
        }
        // Frozen `line` and the rest never read these files for a name.
        if command == "line" {
            if native.stderr.contains("Project metadata error") {
                wrong(format!("refused: {}", native.stderr));
            }
            continue;
        }
        let Some(refusal) = frozen["refusal"].as_str().map(unwrapped) else {
            if native.stderr.contains("Project metadata error") {
                wrong(format!("refused: {}", native.stderr));
            }
            continue;
        };
        if native.exit != frozen["exit"].as_i64().unwrap() as i32 {
            wrong(format!("exit {}, frozen {}", native.exit, frozen["exit"]));
        }
        if native.stderr.contains("Traceback") {
            wrong(format!("traceback: {}", native.stderr));
        }
        // Only a run that was refused before any tool leaves no saved result.
        if native.ici_state {
            wrong("left a saved result".into());
        }
        let prefix = match (command, frozen["exit"].as_i64()) {
            ("export-compilation-context", _) if !refusal.contains("configuration") => {
                "Compilation export error: "
            }
            (_, Some(2)) => "Configuration error: ",
            _ => "Project metadata error: ",
        };
        // The TOML parser's own words are its own; which file and which class stay frozen's.
        let matches = match refusal.split_once(".toml: ") {
            Some((head, _)) if refusal.starts_with("could not parse") => {
                native.stderr.starts_with(&format!("{prefix}{head}.toml: "))
            }
            _ => without_location(&unwrapped(&native.stderr)) == format!("{prefix}{refusal}"),
        };
        if !matches {
            wrong(format!("stderr {:?}, frozen {refusal:?}", native.stderr));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// Frozen reads any size; the bound of every configuration read refuses past it, in words.
#[test]
fn metadata_past_the_configuration_bound_is_refused_by_name() {
    let case = serde_json::json!({
        "files": {"pyproject.toml": "[project]\nname = \"big\"\nversion = \"9\"\n"},
        "padding": {"pyproject.toml": 4 * 1024 * 1024},
    });
    let native = run("verify", &case, &Value::Null);
    assert_eq!(native.exit, 1, "{}", native.stderr);
    assert_eq!(
        native.stderr,
        "Project metadata error: could not read project metadata pyproject.toml: larger than 4 MiB\n"
    );
    assert_eq!(native.project, None);
}
