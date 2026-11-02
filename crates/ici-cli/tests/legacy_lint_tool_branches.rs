//! Legacy verify lint clang-tidy / Clazy branches (frozen analysis/_clang_tidy.py and
//! analysis/_clazy.py: lint configuration keys, discovered `.clang-tidy`, the Clazy compiler
//! wrapper, config/context/replay/toolchain errors, process and output failures, time budgets)
//! against the frozen CLI oracle (`tests/rust-parity/cpp/capture_lint_tool_branches.py`).
use ici_cli::{commands, cpp::set_legacy_tooling_budget_for_tests, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, time::Duration};

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

fn variable(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("explicit {name}"))
}

fn executable(path: &Path, text: &str) {
    use std::os::unix::fs::PermissionsExt;
    fs::write(path, text).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}

/// The capture script's argv log: every non-`--version` invocation, project as {root} and the
/// tool directory as {bin}.
fn invocations(log: &Path, root: &Path, bin: &Path) -> Vec<Value> {
    let Ok(text) = fs::read_to_string(log) else {
        return vec![];
    };
    let (mut calls, mut current) = (vec![], vec![]);
    for line in text.lines() {
        if line == "--END--" {
            // The clang-tidy function-boundary probe of the complexity engines shares the
            // tool; only the lint invocations are compared here.
            if current != ["--version"]
                && !current
                    .iter()
                    .any(|argument| argument == "--checks=-*,readability-function-size")
            {
                calls.push(json!(current));
            }
            current = vec![];
        } else {
            current.push(
                line.replace(&root.display().to_string(), "{root}")
                    .replace(&bin.display().to_string(), "{bin}"),
            );
        }
    }
    calls
}

fn run(oracle: &Value, case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    let bin = work.path().join("bin");
    let log = work.path().join("argv.log");
    fs::create_dir_all(&bin).unwrap();
    let clazy = variable("ICI_TEST_CLAZY");
    let values = [
        ("{clang-tidy}", variable("ICI_TEST_CLANG_TIDY")),
        (
            "{clazy-wrapper}",
            Path::new(&clazy)
                .parent()
                .unwrap()
                .join("clazy")
                .display()
                .to_string(),
        ),
        ("{clazy}", clazy.clone()),
        ("{clang}", variable("ICI_TEST_CLANG")),
        ("{lib}", variable("ICI_TEST_CLANG_LIBRARY_PATH")),
        ("{log}", log.display().to_string()),
        ("{bin}", bin.display().to_string()),
    ];
    let substitute = |text: &str| {
        values.iter().fold(text.to_owned(), |text, (key, value)| {
            text.replace(key, value)
        })
    };
    for (name, text) in case["tools"].as_object().unwrap() {
        executable(&bin.join(name), &substitute(text.as_str().unwrap()));
    }
    if let Some(gxx) = case["gxx"].as_str().filter(|text| !text.is_empty()) {
        executable(&bin.join("g++"), gxx);
    }
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp")
            .join(case["fixture"].as_str().unwrap()),
        &root,
    );
    fs::write(
        root.join("ici.toml"),
        format!(
            "{}{}",
            oracle["base"].as_str().unwrap(),
            case["config"].as_str().unwrap()
        ),
    )
    .unwrap();
    for (name, text) in case["outside"].as_object().unwrap() {
        fs::write(work.path().join(name), text.as_str().unwrap()).unwrap();
    }
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    for (name, size) in case["generated"].as_object().unwrap() {
        fs::write(
            root.join(name),
            "#".repeat(usize::try_from(size.as_u64().unwrap()).unwrap()),
        )
        .unwrap();
    }
    let generated = case["generated_sources"].as_u64().unwrap_or(0);
    if generated > 0 {
        fs::create_dir_all(root.join("src/gen")).unwrap();
    }
    for index in 0..generated {
        fs::write(
            root.join(format!("src/gen/s{index}.cpp")),
            format!("int s{index}();\n"),
        )
        .unwrap();
    }
    for (name, mode) in case["modes"].as_object().into_iter().flatten() {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(
            root.join(name),
            fs::Permissions::from_mode(u32::try_from(mode.as_u64().unwrap()).unwrap()),
        )
        .unwrap();
    }
    for (name, target) in case["links"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(target.as_str().unwrap(), path).unwrap();
    }
    fs::create_dir_all(root.join("build")).unwrap();
    fs::write(
        root.join("build/compile_commands.json"),
        serde_json::to_string(&case["rows"])
            .unwrap()
            .replace("{root}", &root.display().to_string()),
    )
    .unwrap();
    let patches = &case["patches"];
    let seconds = |key: &str| {
        patches
            .get(key)
            .and_then(Value::as_f64)
            .map(Duration::from_secs_f64)
    };
    // The frozen capture patches both tools' constants with the same values.
    set_legacy_tooling_budget_for_tests(
        seconds("tidy._TIMEOUT_SECONDS"),
        seconds("tidy._GLOBAL_TIMEOUT_SECONDS"),
    );
    let environment = BTreeMap::from([
        (
            "PATH".to_string(),
            format!("{}:/usr/bin:/bin", bin.display()),
        ),
        ("HOME".to_string(), work.path().display().to_string()),
    ]);
    let invocation = registry::parse(["ici", "verify", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, &root, &environment);
    set_legacy_tooling_budget_for_tests(None, None);
    let output = match output {
        Ok(output) => output,
        Err(error) => {
            return json!({"error": error.to_string().replace(&root.display().to_string(), "{root}")});
        }
    };
    let report_path = root.join("verify_report.json");
    if !report_path.exists() {
        return json!({
            "exit_code": output.exit_code,
            "output": format!("{}{}", output.stdout, output.stderr)
                .replace(&root.display().to_string(), "{root}"),
        });
    }
    let report: Value = serde_json::from_slice(&fs::read(report_path).unwrap()).unwrap();
    let engine = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "lint")
        .unwrap()
        .clone();
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
        "extra": engine["extra"],
        "tool_evidence": engine["tool_evidence"],
    });
    captured["verify_exit_code"] = json!(output.exit_code);
    if case["tools"]
        .as_object()
        .unwrap()
        .values()
        .any(|text| text.as_str().unwrap().contains(">> {log}"))
    {
        captured["invocations"] = json!(invocations(&log, &root, &bin));
    }
    let text = serde_json::to_string(&captured)
        .unwrap()
        .replace(&root.display().to_string(), "{root}")
        .replace(&bin.display().to_string(), "{bin}");
    serde_json::from_str(&text).unwrap()
}

/// Security correction (docs/design/rust-replacement/decisions.md "clang-tidy 설정의 escape된
/// 인자 키"): frozen ran clang-tidy with these configs (its raw-text key regex missed LLVM YAML
/// double-quoted escapes); native refuses them. The word-bounded literal match itself is kept, so
/// `tidy-config-word-boundary` (in-word `notExtraArgs`) runs clang-tidy exactly like frozen.
const INTENDED_REFUSALS: [&str; 3] = [
    "tidy-config-escaped-key",
    "tidy-config-quote-desync",
    "tidy-discovered-escaped-key",
];
const REFUSAL: &str = "clang-tidy config may not add compiler arguments or inherit parent config";

fn names(report: &Value, prefix: &str) -> Vec<Value> {
    report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| {
            target["target_name"]
                .as_str()
                .is_some_and(|name| name.starts_with(prefix))
        })
        .cloned()
        .collect()
}

/// Frozen ran clang-tidy for the case; native keeps the compiler syntax targets, runs no
/// clang-tidy unit and reports frozen's config refusal (text and target) as the engine ERROR.
fn refused_like_unsafe_config(actual: &Value, frozen: &Value) -> bool {
    let refusals = names(actual, "ClangTidyConfigError");
    let located = |target: &Value| target["message"] == REFUSAL && target["status"] == "ERROR";
    // An explicit config is refused once at "."; a discovered one at every unit it governs.
    let sources: Vec<Value> = names(frozen, "C++Syntax")
        .iter()
        .map(|target| target["file_path"].clone())
        .collect();
    let paths: Vec<Value> = refusals
        .iter()
        .map(|target| target["file_path"].clone())
        .collect();
    !names(frozen, "ClangTidy:").is_empty()
        && names(actual, "ClangTidy:").is_empty()
        && !refusals.is_empty()
        && refusals.iter().all(located)
        && (paths == [json!(".")] || paths == sources)
        && actual["status"] == "ERROR"
        && actual["summary"]
            .as_str()
            .unwrap_or_default()
            .contains(REFUSAL)
        && names(actual, "C++Syntax") == names(frozen, "C++Syntax")
        && actual["invocations"].as_array().is_some_and(Vec::is_empty)
}

#[test]
#[ignore = "explicit actual g++, clang-tidy, Clazy and clang++ legacy verify lint branches: /usr/bin/g++ must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0; ICI_TEST_CLANG_TIDY, ICI_TEST_CLAZY, ICI_TEST_CLANG and ICI_TEST_CLANG_LIBRARY_PATH must be the captured LLVM 21.1.8 / clazy 1.17 tools"]
fn legacy_clang_tool_branches_match_frozen_targets_summaries_findings_and_argv() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/lint-tool-branches-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let version = |program: &str| {
        let output = std::process::Command::new(program)
            .arg("--version")
            .env("LD_LIBRARY_PATH", variable("ICI_TEST_CLANG_LIBRARY_PATH"))
            .output()
            .unwrap();
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    };
    // Tool wording is version specific: other tools must fail, not silently pass.
    assert_eq!(
        version("/usr/bin/g++").lines().next().unwrap(),
        oracle["gxx"].as_str().unwrap()
    );
    for (name, variable_name) in [
        ("clang-tidy", "ICI_TEST_CLANG_TIDY"),
        ("clazy", "ICI_TEST_CLAZY"),
        ("clang", "ICI_TEST_CLANG"),
    ] {
        assert_eq!(
            version(&variable(variable_name)),
            oracle["tool_versions"][name].as_str().unwrap(),
            "{name}"
        );
    }
    let only = std::env::var("ICI_CASE_FILTER").ok();
    let cases = oracle["cases"].as_object().unwrap();
    let mut mismatches = vec![];
    for (name, case) in cases {
        if only.as_deref().is_some_and(|only| only != name) {
            continue;
        }
        let mut actual = run(&oracle, case);
        let exit = actual
            .as_object_mut()
            .and_then(|object| object.remove("verify_exit_code"));
        if name == "tools-unreadable-source" {
            // Intended difference: frozen reports per-step errors for an unreadable production
            // source (and, with the line engine enabled, its JSON reporter crashes on the
            // 0-line target); native refuses the whole run at its input snapshot, so lint is
            // blocked with no evidence and the gate fails.
            let blocked = actual["status"] == "SKIP"
                && actual["summary"] == "input snapshot failed"
                && actual["evidence"] == "NOT_RUN"
                && actual["targets"].as_array().is_some_and(Vec::is_empty)
                && exit
                    .as_ref()
                    .and_then(Value::as_i64)
                    .is_some_and(|code| code != 0)
                && case["frozen"]["status"] == "ERROR";
            if !blocked {
                eprintln!("== {name} (intended block)\nactual: {actual} exit {exit:?}");
                mismatches.push(name.clone());
            }
            continue;
        }
        let mut frozen = case["frozen"].clone();
        if let Some(calls) = frozen.get_mut("invocations").and_then(Value::as_array_mut) {
            calls.retain(|call| {
                !call
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|argument| argument == "--checks=-*,readability-function-size")
            });
        }
        // The frozen LintEngine counters and tool evidence (not of an intended refusal, which
        // runs no clang-tidy unit).
        let differences = lint_extra::split(&mut actual, &mut frozen);
        if !INTENDED_REFUSALS.contains(&name.as_str()) && !differences.is_empty() {
            eprintln!(
                "== {name} (extra/tool_evidence)\n{}",
                differences.join("\n")
            );
            mismatches.push(format!("{name}/extra"));
        }
        let frozen = &frozen;
        if frozen.get("exit_code").is_some() {
            // Refused by configuration validation before any engine ran: frozen exits 2 with
            // "Configuration error: <key>: ..."; native refuses the same key (located,
            // reworded: the config row's recorded wording difference) and exits 2.
            let output = frozen["output"].as_str().unwrap();
            let key = output
                .strip_prefix("Configuration error: ")
                .and_then(|rest| rest.split(':').next())
                .unwrap();
            assert_eq!(frozen["exit_code"], 2, "{name}");
            let error = actual["error"].as_str().unwrap_or_default();
            if !(error.starts_with("Configuration error: ") && error.contains(key)) {
                eprintln!("== {name}\nactual: {actual}\nfrozen: {frozen}");
                mismatches.push(name.clone());
            }
            continue;
        }
        if INTENDED_REFUSALS.contains(&name.as_str()) {
            if !refused_like_unsafe_config(&actual, frozen) {
                eprintln!("== {name} (intended refusal)\nactual: {actual}\nfrozen: {frozen}");
                mismatches.push(name.clone());
            }
            continue;
        }
        if &actual != frozen {
            eprintln!(
                "== {name}\nactual: {}\nfrozen: {}",
                serde_json::to_string(&actual).unwrap(),
                serde_json::to_string(frozen).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
