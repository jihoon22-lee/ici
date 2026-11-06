//! `ici verify` on a real terminal, a pipe and a saved report. A terminal gets the run axes in
//! words; a pipe keeps its whole JSON line for consumers we cannot see, and the saved report does
//! not depend on where stdout goes.
use nix::pty::openpty;
use serde_json::Value;
use std::{
    fs,
    io::Read,
    path::Path,
    process::{Command, Stdio},
};

fn project(source: &str) -> (tempfile::TempDir, std::path::PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap();
    fs::write(base.join(".hg"), "").unwrap();
    let root = base.join("project");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("src/a.py"), source).unwrap();
    fs::write(
        root.join("ici.toml"),
        "type = \"python\"\n[project]\nname = \"p\"\n",
    )
    .unwrap();
    (directory, root)
}

fn command(root: &Path, args: &[&str]) -> Command {
    let base = root.parent().unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_ici"));
    command
        .args(args)
        .current_dir(root)
        .env_clear()
        .env("HOME", base)
        .env("PATH", "/usr/bin:/bin")
        .env("GIT_CEILING_DIRECTORIES", base)
        .stdin(Stdio::null())
        .stderr(Stdio::piped());
    command
}

/// Runs with stdout on a new terminal; returns the exit code and what the terminal showed.
fn on_terminal(root: &Path, args: &[&str]) -> (i32, String) {
    run_on_terminal(command(root, args))
}

fn run_on_terminal(mut command: Command) -> (i32, String) {
    let terminal = openpty(None, None).unwrap();
    command.stdout(Stdio::from(terminal.slave));
    let mut child = command.spawn().unwrap();
    // The command holds a copy of the terminal side; the read below only ends once none is left.
    drop(command);
    let mut master = fs::File::from(terminal.master);
    let mut bytes = Vec::new();
    // Ends with EIO once the child closed the last terminal side.
    let _ = master.read_to_end(&mut bytes);
    let code = child.wait().unwrap().code().unwrap();
    (code, String::from_utf8_lossy(&bytes).replace("\r\n", "\n"))
}

fn on_pipe(root: &Path, args: &[&str]) -> (i32, String) {
    let output = command(root, args).stdout(Stdio::piped()).output().unwrap();
    (
        output.status.code().unwrap(),
        String::from_utf8_lossy(&output.stdout).into_owned(),
    )
}

/// The report without what each run measures anew.
fn report(root: &Path) -> Value {
    let mut report: Value =
        serde_json::from_slice(&fs::read(root.join("verify_report.json")).unwrap()).unwrap();
    fn strip(value: &mut Value) {
        match value {
            Value::Object(items) => {
                // `cleanup` holds the probe's process group id.
                for key in ["duration", "duration_seconds", "run_id", "cleanup"] {
                    items.remove(key);
                }
                items.values_mut().for_each(strip);
            }
            Value::Array(items) => items.iter_mut().for_each(strip),
            _ => {}
        }
    }
    strip(&mut report);
    report
}

const CLEAN: &str = "def a():\n    return 1\n";

#[test]
fn a_terminal_gets_words_and_a_pipe_keeps_the_axes_line() {
    let (_directory, root) = project(CLEAN);
    let args = ["verify", "--no-cache", "--report"];
    let (terminal_code, terminal) = on_terminal(&root, &args);
    let terminal_report = report(&root);
    let (pipe_code, pipe) = on_pipe(&root, &args);
    let pipe_report = report(&root);
    assert_eq!(terminal_code, pipe_code);

    // Terminal: every axis in words, the saved result named, nothing past the width.
    assert!(
        !terminal.contains("Scope, execution, gate and publication"),
        "{terminal}"
    );
    assert!(
        terminal.contains("Scope: partial; components project"),
        "{terminal}"
    );
    assert!(
        terminal.contains("Execution: required work incomplete"),
        "{terminal}"
    );
    assert!(
        terminal.contains("Gate: selected scope INCOMPLETE"),
        "{terminal}"
    );
    // Critical findings of an incomplete run do not read as "no gate violations".
    let flat = terminal.split_whitespace().collect::<Vec<_>>().join(" ");
    assert!(flat.contains("gate violations not ruled out"), "{terminal}");
    assert!(!terminal.contains("no gate violations"), "{terminal}");
    assert!(
        terminal.contains("Publication: not configured"),
        "{terminal}"
    );
    assert!(terminal.contains("Result: "), "{terminal}");
    assert!(
        terminal
            .lines()
            .all(|line| !line.starts_with("{\"checks\"") && line.len() < 400)
    );

    // Pipe: the whole machine-readable line, as before.
    let axes = pipe
        .lines()
        .find_map(|line| line.strip_prefix("Scope, execution, gate and publication: "))
        .unwrap_or_else(|| panic!("{pipe}"));
    let axes: Value = serde_json::from_str(axes).unwrap();
    assert_eq!(axes["gate"]["selected"], "INCOMPLETE");
    assert!(
        axes["checks"]
            .as_array()
            .is_some_and(|checks| !checks.is_empty())
    );
    assert!(!pipe.contains("Gate: selected scope"));

    // Saved report: the same whichever way stdout went.
    assert_eq!(terminal_report, pipe_report);
}

/// An explicit `--json` wins over the terminal: the same document as on a pipe.
#[test]
fn an_explicit_json_option_prints_json_on_a_terminal() {
    let config = "schema_version=1\n[workspace]\nname='p'\n[[components]]\nid='app'\nroot='.'\n\
                  languages=['python']\nsources=['src/**/*.py']\n";
    for args in [
        &["analyze", "--json"][..],
        &["next", "plan", "--json"],
        &["next", "verify", "--json"],
    ] {
        let (_terminal_directory, terminal_root) = project(CLEAN);
        let (_pipe_directory, pipe_root) = project(CLEAN);
        fs::write(terminal_root.join("ici.toml"), config).unwrap();
        fs::write(pipe_root.join("ici.toml"), config).unwrap();
        let (terminal_code, terminal) = on_terminal(&terminal_root, args);
        let (pipe_code, pipe) = on_pipe(&pipe_root, args);
        assert_eq!(terminal_code, pipe_code, "{args:?}");
        let shown: Value = serde_json::from_str(&terminal)
            .unwrap_or_else(|error| panic!("{args:?}: {error}: {terminal}"));
        let piped: Value = serde_json::from_str(&pipe).unwrap();
        let keys = |value: &Value| {
            value
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect::<Vec<_>>()
        };
        assert_eq!(keys(&shown), keys(&piped), "{args:?}");
    }
}

#[test]
fn a_refused_run_reports_the_same_on_a_terminal() {
    let (_directory, root) = project(CLEAN);
    let args = ["verify", "--no-cache", "--baseline", "none.json"];
    let (terminal_code, _) = on_terminal(&root, &args);
    let (pipe_code, _) = on_pipe(&root, &args);
    assert_eq!((terminal_code, pipe_code), (2, 2));
}

#[test]
#[ignore = "explicit project Python runtime+pytest+coverage acceptance"]
fn a_passing_suite_on_a_terminal_still_separates_its_axes() {
    let python = std::env::var("ICI_TEST_PYTHON").expect("set explicit ICI_TEST_PYTHON");
    let (_directory, root) = project("def a(value):\n    return value\n");
    fs::create_dir(root.join("tests")).unwrap();
    fs::write(
        root.join("tests/test_a.py"),
        "import a\n\n\ndef test_a():\n    assert a.a(1) == 1\n",
    )
    .unwrap();
    let mut verify = command(&root, &["verify", "--no-cache"]);
    verify
        .env("ICI_PYTHON", &python)
        .env("PYTHONPATH", root.join("src"));
    let (_, shown) = run_on_terminal(verify);
    assert!(shown.contains("Gate: selected scope "), "{shown}");
    assert!(
        shown.contains("Publication: not configured (separate"),
        "{shown}"
    );
    assert!(
        !shown.contains("Scope, execution, gate and publication"),
        "{shown}"
    );
}
