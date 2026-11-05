//! Legacy `ici lint` / verify lint engine C++ heuristic-fallback projection against the frozen
//! CLI oracle (`tests/rust-parity/cpp/capture_lint_cli.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

#[path = "support/host_tools.rs"]
mod host_tools;

#[path = "support/lint_extra.rs"]
mod lint_extra;

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
];

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn run(case: &Value, base: &str, environment: &BTreeMap<String, String>) -> Value {
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        root.path(),
    );
    fs::write(
        root.path().join("ici.toml"),
        format!("{base}{}", case["config"].as_str().unwrap()),
    )
    .unwrap();
    let files = case["files"].as_object().unwrap();
    for (name, text) in files {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(
            path,
            text.as_str()
                .unwrap()
                .replace("{root}", &root.path().display().to_string()),
        )
        .unwrap();
    }
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let output = commands::dispatch(&invocation, root.path(), environment).unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
    )
    .unwrap();
    let engine = if command == "verify" {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "lint")
            .unwrap()
            .clone()
    } else {
        report
    };
    let targets: Vec<Value> = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            Value::Object(
                FIELDS
                    .iter()
                    .map(|key| ((*key).to_owned(), target[*key].clone()))
                    .collect(),
            )
        })
        .collect();
    let mut captured = json!({
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": targets,
        "findings": engine["findings"],
    });
    let (extra, tools) =
        lint_extra::captured(&engine, &[(root.path().display().to_string(), "{root}")]);
    captured["extra"] = extra;
    captured["tool_evidence"] = tools;
    if command != "verify" {
        captured["exit_code"] = json!(output.exit_code);
    }
    if !files.is_empty() {
        captured["built"] = json!(root.path().join("build/ici-make").exists());
    }
    captured
}

#[test]
#[ignore = "explicit actual g++ legacy lint fallback: ICI_TEST_CXX must be g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 (the oracle compiler) and /usr/bin must lack clang-tidy/clazy-standalone"]
fn legacy_cpp_lint_fallback_matches_frozen_targets_summaries_and_exit_codes() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let version = std::process::Command::new(&compiler)
        .arg("--version")
        .output()
        .unwrap();
    let version = String::from_utf8(version.stdout).unwrap();
    // Compiler wording is version specific: a different g++ must fail, not silently pass.
    assert_eq!(
        version.lines().next().unwrap(),
        oracle["gxx"].as_str().unwrap(),
        "the lint oracle was captured with another g++"
    );
    let directory = Path::new(&compiler).parent().unwrap().display().to_string();
    for tool in ["clang-tidy", "clazy-standalone"] {
        for path in [directory.as_str(), "/usr/bin", "/bin"] {
            assert!(
                !Path::new(path).join(tool).exists(),
                "{tool} must be absent from {path}"
            );
        }
    }
    replay_fallback(&oracle, &format!("{directory}:/usr/bin:/bin"), false);
}

/// The CI twin of the fallback cases with the host's own g++ (any release, as on the GitHub
/// runner) behind a mirror of `/usr/bin` and `/bin` without clang*/clazy* (the capture host had
/// none; `support/host_tools.rs`). Incidental masks (`host_view`), applied to both sides: what
/// the GCC release itself writes — target messages and columns, finding messages, fingerprints,
/// columns and tool versions, and the compiler's resolved path, `argv[0]` and version banner in
/// `tool_evidence`. Every exit code, engine status, evidence and summary, every target name,
/// status, file and line, every finding rule, category, severity, path and line, the remaining
/// argv and every `extra` counter stay compared.
#[test]
fn legacy_cpp_lint_fallback_matches_frozen_targets_summaries_and_exit_codes_with_the_host_gxx() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-oracle.json"
    ))
    .unwrap();
    let host = host_tools::mirror(&host_tools::NO_LLVM);
    replay_fallback(&oracle, &host.path, true);
}

fn replay_fallback(oracle: &Value, system_path: &str, host: bool) {
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    // The temporary directory bounds VCS discovery (each fixture root is directly inside it).
    let environment = BTreeMap::from([
        ("PATH".to_string(), system_path.to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_string(),
            std::env::temp_dir().display().to_string(),
        ),
    ]);
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 9);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let mut actual = run(case, base, &environment);
        let mut frozen = case["frozen"].clone();
        if host {
            host_view(&mut actual);
            host_view(&mut frozen);
        }
        // Frozen tool errors: ERROR with NOT_RUN evidence and exit 1, compared exactly.
        mismatch(name, &actual, &frozen, &mut mismatches);
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// Mask only what the GCC release writes (see the host twin's doc).
fn host_view(view: &mut Value) {
    for target in view["targets"].as_array_mut().into_iter().flatten() {
        for key in ["message", "start_column"] {
            target[key] = json!("<host>");
        }
    }
    for finding in view["findings"].as_array_mut().into_iter().flatten() {
        if finding["tool_name"] != "g++" {
            continue;
        }
        for key in ["message", "fingerprint", "tool_version"] {
            finding[key] = json!("<host>");
        }
        for key in ["start_column", "end_column"] {
            finding["primary_location"][key] = json!("<host>");
        }
    }
    for record in view["tool_evidence"].as_array_mut().into_iter().flatten() {
        if record["name"] != "g++" {
            continue;
        }
        record["path"] = json!("<host>");
        record["argv"][0] = json!("<host>");
        if record["version"] != "" {
            record["version"] = json!("<host>");
        }
    }
}

/// The capture script's temporary bin directory: `clang-tidy` symlinked to ICI_TEST_CLANG_TIDY,
/// `clazy-standalone` a wrapper that sets ICI_TEST_CLANG_LIBRARY_PATH (frozen replaces the
/// clang tools' environment), or the raw binary for `clazy-raw`.
fn tool_bin(directory: &Path, tools: &[Value]) {
    use std::os::unix::fs::symlink;
    fs::create_dir_all(directory).unwrap();
    let variable = |name: &str| std::env::var(name).unwrap_or_else(|_| panic!("explicit {name}"));
    for tool in tools {
        match tool.as_str().unwrap() {
            "clang-tidy" => symlink(
                variable("ICI_TEST_CLANG_TIDY"),
                directory.join("clang-tidy"),
            )
            .unwrap(),
            "clazy-raw" => symlink(
                variable("ICI_TEST_CLAZY"),
                directory.join("clazy-standalone"),
            )
            .unwrap(),
            // Passes the `--version` capability probe, exits 3 on every run.
            "clazy-probe-only" => script(
                &directory.join("clazy-standalone"),
                &format!(
                    "#!/bin/sh\nif [ \"$1\" = --version ]; then LD_LIBRARY_PATH={} exec {} \"$@\"; fi\nexit 3\n",
                    variable("ICI_TEST_CLANG_LIBRARY_PATH"),
                    variable("ICI_TEST_CLAZY")
                ),
            ),
            // Exits 0 without a parseable version: an incomplete capability probe.
            "tidy-no-version" => script(
                &directory.join("clang-tidy"),
                "#!/bin/sh\necho clang-tidy without a version banner\nexit 0\n",
            ),
            _ => script(
                &directory.join("clazy-standalone"),
                &format!(
                    "#!/bin/sh\nLD_LIBRARY_PATH={} exec {} \"$@\"\n",
                    variable("ICI_TEST_CLANG_LIBRARY_PATH"),
                    variable("ICI_TEST_CLAZY")
                ),
            ),
        }
    }
}

fn script(path: &Path, text: &str) {
    use std::os::unix::fs::PermissionsExt;
    fs::write(path, text).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}

#[test]
#[ignore = "explicit actual g++, clang-tidy and Clazy legacy verify lint with a compilation database (ICI_TEST_CXX, ICI_TEST_CLANG_TIDY, ICI_TEST_CLAZY, ICI_TEST_CLANG_LIBRARY_PATH)"]
fn legacy_cpp_lint_with_resolvable_clang_tools_matches_frozen_targets_and_summaries() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-oracle.json"
    ))
    .unwrap();
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let directory = Path::new(&compiler).parent().unwrap().display().to_string();
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["tool_cases"].as_object().unwrap();
    assert_eq!(cases.len(), 12);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let tools = tempfile::tempdir().unwrap();
        tool_bin(&tools.path().join("bin"), case["tools"].as_array().unwrap());
        let mut environment = BTreeMap::from([(
            "PATH".to_string(),
            format!(
                "{}:{directory}:/usr/bin:/bin",
                tools.path().join("bin").display()
            ),
        )]);
        for (key, value) in case["env"].as_object().into_iter().flatten() {
            environment.insert(
                key.clone(),
                value.as_str().unwrap().replace(
                    "{clang-library-path}",
                    &std::env::var("ICI_TEST_CLANG_LIBRARY_PATH").unwrap(),
                ),
            );
        }
        let mut actual = run(case, base, &environment);
        lint_extra::relabel(
            &mut actual,
            &tools.path().join("bin").display().to_string(),
            "{bin}",
        );
        let mut frozen = case["frozen"].clone();
        if name == "clazy-env-library-path" {
            // Known frozen defect, corrected (D-B): frozen probes Clazy with the caller's
            // LD_LIBRARY_PATH, then runs it in a replaced minimal environment, so every
            // run exits 127 (ClazyExecutionError). Rust probes and runs with the same
            // environment, so the raw binary loads in both and the result is exactly the
            // frozen result of a Clazy that can load its libraries (`clazy-only`).
            assert_eq!(frozen["status"], "ERROR", "{name}: frozen defect captured");
            assert!(
                frozen["summary"]
                    .as_str()
                    .unwrap()
                    .starts_with("clazy failed with exit code 127"),
                "{name}"
            );
            frozen = cases["clazy-only"]["frozen"].clone();
            // clazy-only's Clazy is a wrapper script; here the tool is a symlink to the raw
            // binary, which frozen `regular_executable` runs by its resolved path.
            let raw = fs::canonicalize(std::env::var("ICI_TEST_CLAZY").unwrap()).unwrap();
            lint_extra::relabel(
                &mut frozen,
                "{bin}/clazy-standalone",
                &raw.display().to_string(),
            );
        }
        mismatch(name, &actual, &frozen, &mut mismatches);
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// Records a case that differs from frozen, printing both sides as single JSON lines.
fn mismatch(name: &str, actual: &Value, frozen: &Value, mismatches: &mut Vec<String>) {
    let (mut actual, mut frozen) = (actual.clone(), frozen.clone());
    let differences = lint_extra::split(&mut actual, &mut frozen);
    if !differences.is_empty() {
        eprintln!(
            "== {name} (extra/tool_evidence)\n{}",
            differences.join("\n")
        );
        mismatches.push(format!("{name}/extra"));
    }
    if actual != frozen {
        eprintln!(
            "== {name}\nactual: {}\nfrozen: {}",
            serde_json::to_string(&actual).unwrap(),
            serde_json::to_string(&frozen).unwrap()
        );
        mismatches.push(name.to_owned());
    }
}
