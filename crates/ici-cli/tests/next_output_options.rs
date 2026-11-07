//! `ici analyze --output/--result/--no-cache`, `ici next diff --output/--result` and its body,
//! and `ici next publish --output/--result/--out` through the built binary against frozen
//! v0.13.0 (`tests/rust-parity/runtime/next-output-options-oracle.json`,
//! `capture_next_output_options.py`). Every frozen exit code, output line (in order), refusal
//! and written file is reproduced; the only native additions are the reviewed ones below.
use serde_json::Value;
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/runtime/next-output-options-oracle.json");

/// Native lines frozen did not print, each with its reason:
/// `ici analyze` adds the offline page and the finding preview after `saved` (execution plan
/// §2.1 analyze 동선, accepted reporting difference; the page of an explicit result lives under
/// `.ici/rust-v1/details/`, root decision E2),
/// the native source-identity note for a `.git` without a HEAD, and the native observation cache
/// reusing in-process checks (frozen never cached them: "internal checks run in-process").
fn reviewed_native_line(line: &str) -> bool {
    line.starts_with("HTML: <root>/")
        || line.starts_with("findings (")
        || line == "  note: source.git.head unavailable; source cleanliness is unknown"
        || (line.starts_with("  cache: reused ") && line.ends_with(" stored result(s)"))
        || [
            "  critical ",
            "  high ",
            "  medium ",
            "  low ",
            "  warning ",
            "  info ",
        ]
        .iter()
        .any(|prefix| line.starts_with(prefix) && line.contains(".py:"))
}

/// An OS error is worded by the platform library (Python `[Errno 2] …: '<path>'`, Rust
/// `… (os error 2)`); the refusal class, the file and everything before the OS text are frozen's.
fn without_os_error(line: &str) -> &str {
    [
        "cannot be read: ",
        "cannot read result <root>/out/missing.json: ",
    ]
    .iter()
    .find_map(|marker| line.find(marker).map(|index| &line[..index + marker.len()]))
    .unwrap_or(line)
}

fn files(root: &Path) -> Vec<String> {
    let mut found = vec![];
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory).unwrap().flatten() {
            let path = entry.path();
            let relative = path
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            if relative == ".ici" || relative == ".git" {
                continue;
            }
            if path.is_dir() {
                pending.push(path);
            } else {
                found.push(relative);
            }
        }
    }
    found.sort();
    found
}

/// Output lines with the project root as `<root>`; the native default artifacts live in the
/// accepted `.ici/rust-v1` namespace where frozen used `.ici/next` (publish CORRECTIONS).
fn lines(text: &[u8], root: &Path) -> Vec<String> {
    String::from_utf8_lossy(text)
        .replace(&root.display().to_string(), "<root>")
        .replace("<root>/.ici/rust-v1/", "<root>/.ici/next/")
        .lines()
        .map(str::to_owned)
        .collect()
}

fn replay(case: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("project");
    fs::create_dir_all(root.join("app")).unwrap();
    fs::create_dir_all(root.join("lib")).unwrap();
    fs::create_dir_all(root.join(".git")).unwrap();
    fs::write(root.join("ici.toml"), oracle["config"].as_str().unwrap()).unwrap();
    fs::write(
        root.join("app/core.py"),
        oracle["sources"]["clean"].as_str().unwrap(),
    )
    .unwrap();
    let texts = |name: &str| match name {
        "ici.toml" => oracle["two_components"].as_str().unwrap().to_owned(),
        _ => oracle["sources"]["pickle"].as_str().unwrap().to_owned(),
    };
    for step in oracle["cases"][case].as_array().unwrap() {
        for edit in step["edits"].as_array().unwrap() {
            let name = edit.as_str().unwrap();
            fs::write(root.join(name), texts(name)).unwrap();
        }
        let argv: Vec<&str> = step["argv"]
            .as_array()
            .unwrap()
            .iter()
            .map(|part| part.as_str().unwrap())
            .collect();
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(&argv)
            .current_dir(&root)
            .env_clear()
            .env("HOME", home.path())
            .env("XDG_CONFIG_HOME", home.path())
            .env("PATH", "/usr/bin:/bin")
            .output()
            .unwrap();
        let label = format!("{case} {argv:?}");
        assert_eq!(
            Some(step["exit_code"].as_i64().unwrap() as i32),
            output.status.code(),
            "{label}"
        );
        // Every frozen stdout line, in order; any other native line is a reviewed addition.
        let native = lines(&output.stdout, &root);
        let mut cursor = native.iter();
        for line in step["stdout"].as_array().unwrap() {
            let line = line.as_str().unwrap();
            loop {
                let next = cursor
                    .next()
                    .unwrap_or_else(|| panic!("{label}: missing {line:?} in {native:#?}"));
                if next == line {
                    break;
                }
                assert!(reviewed_native_line(next), "{label}: unreviewed {next:?}");
            }
        }
        for rest in cursor {
            assert!(reviewed_native_line(rest), "{label}: unreviewed {rest:?}");
        }
        let frozen_errors: Vec<&str> = step["stderr"]
            .as_array()
            .unwrap()
            .iter()
            .map(|line| without_os_error(line.as_str().unwrap()))
            .collect();
        let native_errors = lines(&output.stderr, &root);
        let native_errors: Vec<&str> = native_errors
            .iter()
            .map(|line| without_os_error(line))
            .collect();
        assert_eq!(native_errors, frozen_errors, "{label} stderr");
        // Exactly frozen's files outside .ici/.git (root decision E2): the analyze page of an
        // explicit result is kept under .ici/rust-v1/details/, never beside the user's result.
        let written = files(&root);
        let expected: Vec<String> = step["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|file| file.as_str().unwrap().to_owned())
            .collect();
        assert_eq!(written, expected, "{label}");
        if let Some(frozen) = step["result"].as_object() {
            let position = argv
                .iter()
                .position(|part| *part == "--output" || *part == "--result")
                .unwrap();
            let document: Value =
                serde_json::from_slice(&fs::read(root.join(argv[position + 1])).unwrap()).unwrap();
            assert_eq!(document["gate"]["selected"], frozen["gate"], "{label} gate");
            assert_eq!(
                document["findings"].as_array().unwrap().len() as u64,
                frozen["findings"].as_u64().unwrap(),
                "{label} findings"
            );
            assert_eq!(frozen["reused_task_ids"], serde_json::json!([]), "{label}");
            if argv.contains(&"--no-cache") {
                assert_eq!(
                    document["execution"]["reused_task_ids"],
                    serde_json::json!([]),
                    "{label}: --no-cache reuses nothing"
                );
            }
        }
    }
}

#[test]
fn analyze_output_and_no_cache_and_diff_output_match_frozen() {
    replay("analyze-output-diff");
}

#[test]
fn diff_reports_carried_findings_of_an_unselected_component_like_frozen() {
    replay("diff-carried");
}

#[test]
fn publish_reads_the_result_named_by_output_or_result_and_the_page_named_by_out() {
    replay("publish-output");
}

#[test]
fn top_level_diff_reads_output_and_result_and_refuses_like_frozen() {
    replay("top-diff");
}
