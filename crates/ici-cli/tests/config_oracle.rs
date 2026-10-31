//! Frozen configuration refusals through the actual native `ici` binary.
//! Golden: tests/rust-parity/config/legacy-schema-oracle.json (capture_legacy_schema.py).
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

fn fixture(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/rust-parity/config");
    serde_json::from_str(&fs::read_to_string(path.join(name)).unwrap()).unwrap()
}

fn run_line(text: &str) -> (Option<i32>, String) {
    let project = tempfile::tempdir().unwrap();
    let xdg = tempfile::tempdir().unwrap();
    fs::create_dir(project.path().join(".git")).unwrap();
    fs::write(project.path().join("ici.toml"), text).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .arg("line")
        .current_dir(project.path())
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", xdg.path())
        .env("XDG_CONFIG_HOME", xdg.path())
        .output()
        .unwrap();
    assert!(
        fs::read_dir(xdg.path()).unwrap().next().is_none(),
        "a refused configuration writes no global default"
    );
    (
        output.status.code(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

#[test]
fn every_frozen_legacy_refusal_exits_two_from_a_historical_command_and_names_the_key() {
    let oracle = fixture("legacy-schema-oracle.json");
    let differences = fixture("legacy-schema-differences.json");
    let mut failures = vec![];
    let mut checked = 0;
    for case in oracle["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let native = differences
            .get(id)
            .and_then(|d| d["native"].as_str())
            .unwrap_or(case["outcome"].as_str().unwrap());
        if native != "reject" {
            continue;
        }
        let key = differences
            .get(id)
            .and_then(|d| d.get("native_key"))
            .or(case.get("error_key"))
            .and_then(Value::as_str)
            .unwrap();
        let (code, stderr) = run_line(case["toml"].as_str().unwrap());
        checked += 1;
        let named = key.is_empty() || {
            let base = key.split('[').next().unwrap();
            stderr.contains(base)
        };
        if code != Some(2) || !named || !stderr.contains("ici.toml") {
            failures.push(format!("{id}: exit {code:?} stderr {stderr}"));
        }
    }
    assert!(checked > 450, "{checked}");
    assert!(failures.is_empty(), "{}", failures.join("\n"));
    for sample in oracle["cli"].as_array().unwrap() {
        assert_eq!(sample["exit_code"], 2, "frozen CLI sample {sample}");
        assert!(
            sample["stderr"]
                .as_str()
                .unwrap()
                .starts_with("Configuration error: ")
        );
    }
}

fn run_next_plan(case: &Value) -> (Option<i32>, String) {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("project");
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(root.join(".git")).unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_ici"));
    command.args(["next", "plan", "--json"]);
    if let Some(name) = case["explicit"].as_str() {
        command.arg("--config").arg(root.join(name));
    }
    if let Some(text) = case["local"].as_str() {
        let path = temporary.path().join("local.toml");
        fs::write(&path, text).unwrap();
        command.arg("--local-config").arg(path);
    }
    let output = command
        .current_dir(&root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", temporary.path())
        .env("XDG_CONFIG_HOME", temporary.path())
        .output()
        .unwrap();
    // Frozen stable commands wrote a global default policy on a first run; native never does,
    // whether the configuration is accepted or refused.
    assert!(!temporary.path().join("ici").exists());
    (
        output.status.code(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

#[test]
fn every_frozen_next_v1_case_has_the_frozen_plan_exit_code_through_the_binary() {
    let oracle = fixture("next-schema-oracle.json");
    let differences = fixture("next-schema-differences.json");
    let mut failures = vec![];
    let mut samples = 0;
    for case in oracle["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let native = differences
            .get(id)
            .and_then(|d| d["native"].as_str())
            .unwrap_or(case["outcome"].as_str().unwrap());
        let (code, stderr) = run_next_plan(case);
        let expected = if native == "reject" { 2 } else { 0 };
        if code != Some(expected) {
            failures.push(format!("{id}: exit {code:?} expected {expected}: {stderr}"));
        }
        if let Some(cli) = case.get("cli") {
            samples += 1;
            if differences.get(id).is_none() {
                assert_eq!(cli["exit_code"], json!(expected), "{id}: frozen CLI sample");
            }
        }
    }
    assert_eq!(samples, 4);
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// An accepted but not yet honoured preference is visible to the user in the plan, not only in
/// the library's `ResolvedProject.limitations`.
#[test]
fn unapplied_preferences_reach_the_plan_output_of_the_binary() {
    let project = tempfile::tempdir().unwrap();
    fs::create_dir(project.path().join(".git")).unwrap();
    fs::write(project.path().join("app.py"), "x = 1\n").unwrap();
    fs::write(
        project.path().join("ici.toml"),
        "schema_version = 2\noutput = \"results\"\ncache = \"cache\"\n",
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["next", "plan", "--json"])
        .current_dir(project.path())
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", project.path())
        .env("XDG_CONFIG_HOME", project.path().join("xdg"))
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(0));
    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    let text = plan.to_string();
    for key in ["output", "cache"] {
        assert!(
            text.contains(&format!("{key} preference from ")) && text.contains("not applied"),
            "{key}: {text}"
        );
    }
}

/// Exit code plus, for an accepted plan, the projection the frozen capture recorded: sorted
/// task ids, scope.require_full, scope.profile and the tasks whose blocked reason names the
/// local-overlay interpreter (host-independent proof that --local-config reached the plan).
fn planned(tree: &Value, argv: &[String], executable: &str) -> Value {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("project");
    for (name, text) in tree.as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(root.join(".git")).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(argv)
        .current_dir(&root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", temporary.path())
        .env("XDG_CONFIG_HOME", temporary.path())
        .output()
        .unwrap();
    assert!(
        !temporary.path().join("ici").exists(),
        "a plan writes no user configuration"
    );
    if !output.status.success() {
        return json!({"exit_code": output.status.code(), "tasks": null});
    }
    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    let checks: Vec<&Value> = plan["plans"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|item| item["checks"].as_array().unwrap())
        .collect();
    let mut tasks: Vec<&str> = checks
        .iter()
        .map(|check| check["task_id"].as_str().unwrap())
        .collect();
    tasks.sort();
    let mut named: Vec<&str> = checks
        .iter()
        .filter(|check| {
            check["blocked"]
                .as_str()
                .is_some_and(|reason| reason.contains(executable))
        })
        .map(|check| check["task_id"].as_str().unwrap())
        .collect();
    named.sort();
    json!({
        "exit_code": 0,
        "tasks": tasks,
        "require_full": plan["scope"]["require_full"],
        "profile": plan["scope"]["profile"],
        "names_local_executable": named,
    })
}

#[test]
fn plan_selection_options_select_the_frozen_task_sets_and_refuse_the_frozen_inputs() {
    // Golden: cli-selection-oracle.json (capture_cli_selection.py) — --component (single,
    // repeated, unknown), --python/--cpp/--language, --profile (fast, deep, invalid),
    // --require-full, --config (same, alternate, missing) and --local-config (interpreter
    // override, policy refused). Every accepted option changes a compared field.
    let golden = fixture("cli-selection-oracle.json");
    let executable = golden["local_executable"].as_str().unwrap();
    let mut seen = 0;
    for (group, tree) in [
        ("next_plan", "workspace"),
        ("analyze_plan", "configless"),
        ("analyze_configured_plan", "workspace"),
    ] {
        for (name, case) in golden["results"][group].as_object().unwrap() {
            let argv: Vec<String> = serde_json::from_value(case["argv"].clone()).unwrap();
            let mut expected = case.clone();
            expected.as_object_mut().unwrap().remove("argv");
            let observed = planned(&golden[tree], &argv, executable);
            assert_eq!(observed, expected, "{group}:{name}");
            seen += 1;
        }
    }
    assert_eq!(seen, 35);
}

#[test]
fn next_init_keeps_the_frozen_preview_refusal_and_force_contract() {
    // Golden: cli-selection-oracle.json results.next_init; differences:
    // cli-selection-differences.json. Intended differences, asserted:
    // native writes the sparse schema_version=2 scaffold (an unrestricted preview names no
    // languages; discovery supplies them), `--force` keeps the existing scope with a
    // `.before-init` backup, and an unparseable existing file is refused with its location
    // instead of being overwritten (frozen exit 0).
    let golden = fixture("cli-selection-oracle.json");
    let cases = golden["results"]["next_init"].as_object().unwrap();
    let differences = fixture("cli-selection-differences.json");
    let differences = differences.as_object().unwrap();
    assert_eq!(cases.len(), 8);
    for (name, case) in cases {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("project");
        for (file, text) in golden["configless"].as_object().unwrap() {
            fs::create_dir_all(&root).unwrap();
            fs::write(root.join(file), text.as_str().unwrap()).unwrap();
        }
        fs::create_dir(root.join(".git")).unwrap();
        let target = root.join("ici.toml");
        let existing = case["existing"].as_str();
        if let Some(text) = existing {
            fs::write(&target, text).unwrap();
        }
        let argv: Vec<String> = serde_json::from_value(case["argv"].clone()).unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_ici"))
            .args(&argv)
            .current_dir(&root)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", temporary.path())
            .env("XDG_CONFIG_HOME", temporary.path())
            .output()
            .unwrap();
        assert!(
            !temporary.path().join("ici").exists(),
            "{name}: no user configuration"
        );
        let after = fs::read_to_string(&target).ok();
        let unchanged = after.as_deref() == existing;
        let stdout = String::from_utf8_lossy(&output.stdout);
        let languages = stdout.lines().find(|line| line.starts_with("languages"));
        if let Some(difference) = differences.get(&format!("next_init:{name}")) {
            let native = difference["native_exit_code"]
                .as_i64()
                .map(|code| code as i32);
            assert_eq!(output.status.code(), native, "{name}");
            assert_eq!(
                unchanged,
                difference["native_file_unchanged"].as_bool().unwrap(),
                "{name}: an unparseable file is preserved, not overwritten"
            );
            assert!(
                String::from_utf8_lossy(&output.stderr).contains("ici.toml:1:"),
                "{name}"
            );
            continue;
        }
        assert_eq!(
            output.status.code(),
            case["exit_code"].as_i64().map(|code| code as i32),
            "{name}"
        );
        assert_eq!(
            unchanged,
            case["file_unchanged"].as_bool().unwrap(),
            "{name}"
        );
        if argv.iter().any(|arg| arg == "--python" || arg == "--cpp") {
            assert_eq!(languages, case["languages_line"].as_str(), "{name}");
        } else if argv.iter().any(|arg| arg == "--preview") {
            assert!(
                stdout.contains("schema_version = 2") && languages.is_none(),
                "{name}"
            );
        }
        if name == "existing_force" {
            let backup = fs::read_to_string(root.join("ici.toml.before-init")).unwrap();
            assert_eq!(
                Some(backup.as_str()),
                existing,
                "{name}: original kept as a backup"
            );
        }
        if name == "fresh" {
            assert!(after.unwrap().contains("schema_version = 2"), "{name}");
        }
    }
}
