//! Legacy `ici verify` on a C++ project: engine selection by profile, `enabled`, `mode` and
//! `required` keys, every frozen single-engine command, `ici analyze` and `ici next verify`
//! against the frozen capture tests/rust-parity/cpp/verify-matrix-oracle.json.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
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

const FINDING_FIELDS: [&str; 15] = [
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "tool_name",
    "tool_rule_id",
    "explanation",
    "remediation",
    "related_locations",
    "fixes",
    "metrics",
    "snippet",
    "primary_location",
    "fingerprint",
];

/// Run `arguments` in a fresh `project` directory (the frozen capture's directory name, which
/// the configless `analyze` workspace takes its name and next fingerprints from).
fn execute(
    arguments: &[&str],
    config: Option<&str>,
    output: &str,
    environment: &BTreeMap<String, String>,
) -> (i32, Value, tempfile::TempDir) {
    let work = tempfile::tempdir().unwrap();
    let root = work.path().join("project");
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
        &root,
    );
    if let Some(config) = config {
        fs::write(root.join("ici.toml"), config).unwrap();
    }
    let invocation =
        registry::parse(std::iter::once("ici").chain(arguments.iter().copied())).unwrap();
    let result = commands::dispatch(&invocation, &root, environment).unwrap();
    // `--json` results are machine output on stdout (native saves under its own namespace).
    let report = if output.is_empty() {
        serde_json::from_str(&result.stdout).unwrap_or(Value::Null)
    } else {
        fs::read(root.join(output))
            .map(|bytes| serde_json::from_slice(&bytes).unwrap())
            .unwrap_or(Value::Null)
    };
    (result.exit_code, report, work)
}

fn run(config: &str, environment: &BTreeMap<String, String>) -> (i32, Value) {
    let (exit_code, report, _work) = execute(
        &["verify", "--report"],
        Some(config),
        "verify_report.json",
        environment,
    );
    (exit_code, report)
}

fn project(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

fn findings(engine: &Value) -> Value {
    engine["findings"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .map(|item| project(item, &FINDING_FIELDS))
                .collect()
        })
        .unwrap_or_default()
}

fn targets(engine: &Value) -> Value {
    engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| project(target, &FIELDS))
        .collect()
}

/// Compare one legacy engine report with its frozen capture, collecting every difference.
fn compare_engine(case: &str, actual: &Value, expected: &Value, mismatches: &mut Vec<String>) {
    let engine = expected["engine_name"].as_str().unwrap();
    let mut check = |key: &str, left: Value, right: &Value| {
        if &left != right {
            mismatches.push(format!(
                "{case}: {engine}.{key}: native {left} frozen {right}"
            ));
        }
    };
    // Recorded validation difference: the no-test verdict is MEASURED with no fabricated
    // coverage estimate or TEM where frozen kept the ESTIMATED label, summary and score of it.
    let no_test = engine == "test" && expected["status"] == "FAIL";
    let mut keys = vec!["status", "required"];
    if !no_test {
        keys.extend(["evidence", "summary", "score", "max_score"]);
    }
    for key in keys {
        check(key, actual[key].clone(), &expected[key]);
    }
    if no_test {
        check(
            "evidence",
            actual["evidence"].clone(),
            &Value::from("MEASURED"),
        );
    }
    check("targets", targets(actual), &expected["targets"]);
    let mut frozen = expected["findings"].clone();
    if no_test {
        // The same recorded difference: the adapted finding's confidence follows MEASURED.
        for finding in frozen.as_array_mut().unwrap() {
            finding["confidence"] = "high".into();
        }
    }
    check("findings", findings(actual), &frozen);
}

fn compiler_environment(oracle: &Value) -> BTreeMap<String, String> {
    let compiler = std::env::var("ICI_TEST_CXX").expect("explicit C++ compiler");
    let version = std::process::Command::new(&compiler)
        .arg("--version")
        .output()
        .unwrap();
    // The lint fallback wording is compiler-version specific: another g++ fails, never passes.
    assert_eq!(
        String::from_utf8(version.stdout).unwrap().lines().next(),
        oracle["gxx"].as_str(),
        "the verify matrix was captured with another g++"
    );
    let directory = Path::new(&compiler).parent().unwrap().display().to_string();
    for tool in ["clang-tidy", "clazy-standalone"] {
        for path in [directory.as_str(), "/usr/bin", "/bin"] {
            assert!(!Path::new(path).join(tool).exists(), "{tool} in {path}");
        }
    }
    BTreeMap::from([("PATH".to_string(), format!("{directory}:/usr/bin:/bin"))])
}

#[test]
#[ignore = "explicit actual g++ legacy verify: ICI_TEST_CXX must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone"]
fn legacy_cpp_verify_engine_keys_and_profiles_match_the_frozen_suite() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/verify-matrix-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let environment = compiler_environment(&oracle);
    let base = oracle["base"].as_str().unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 7);
    let mut mismatches = Vec::new();
    for (name, frozen) in cases {
        let config = format!("{base}{}", oracle["configs"][name].as_str().unwrap());
        let (exit_code, report) = run(&config, &environment);
        let engines = report["results"].as_array().unwrap();
        let expected = frozen["engines"].as_array().unwrap();
        let names = |list: &[Value]| -> Vec<Value> {
            list.iter()
                .map(|engine| engine["engine_name"].clone())
                .collect()
        };
        // Profile and `enabled` select exactly the frozen engines in frozen order.
        assert_eq!(names(engines), names(expected), "{name}: engine set");
        for (actual, expected) in engines.iter().zip(expected) {
            compare_engine(name, actual, expected, &mut mismatches);
        }
        let incomplete = report["native_run"]["execution"]["required_complete"] == false;
        if report["suite_status"] == frozen["suite_status"] && exit_code == frozen["exit_code"] {
            continue;
        }
        // Accepted policy: a run whose required evidence stays incomplete keeps an ERROR suite
        // and exits 3 where frozen exited 1 with the same ERROR suite.
        assert!(incomplete, "{name}: complete run differs from frozen");
        assert_eq!(report["suite_status"], "ERROR", "{name}: suite");
        assert_eq!(frozen["suite_status"], "ERROR", "{name}: frozen suite");
        assert_eq!(
            (exit_code, &frozen["exit_code"]),
            (3, &Value::from(1)),
            "{name}: exit"
        );
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/verify-matrix-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

#[test]
#[ignore = "explicit actual g++ legacy commands: ICI_TEST_CXX must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone"]
fn every_legacy_single_command_on_a_cpp_project_matches_the_frozen_engine_report() {
    let oracle = oracle();
    let environment = compiler_environment(&oracle);
    let commands = oracle["commands"].as_object().unwrap();
    assert_eq!(commands.len(), 15);
    let mut mismatches = Vec::new();
    for (command, frozen) in commands {
        let output = format!("{}_report.json", command.replace('-', "_"));
        let (exit_code, report, _work) = execute(
            &[command.as_str(), "--report"],
            oracle["base"].as_str(),
            &output,
            &environment,
        );
        // A single-engine report is the engine result itself, never the suite document.
        assert_eq!(report["engine_name"], frozen["engine_name"], "{command}");
        if frozen["exit_code"] != exit_code {
            mismatches.push(format!(
                "{command}: exit native {exit_code} frozen {}",
                frozen["exit_code"]
            ));
        }
        compare_engine(command, &report, frozen, &mut mismatches);
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}

#[test]
#[ignore = "explicit actual g++ C++ analyze/next verify: ICI_TEST_CXX must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0 and /usr/bin must lack clang-tidy/clazy-standalone"]
fn cpp_analyze_and_next_verify_match_the_frozen_checks_findings_and_gate() {
    let oracle = oracle();
    let environment = compiler_environment(&oracle);
    let mut mismatches = Vec::new();
    for (name, config) in [
        ("analyze", None),
        ("next_verify", oracle["next_config"].as_str()),
    ] {
        let frozen = &oracle["next"][name];
        let arguments: Vec<&str> = frozen["arguments"]
            .as_array()
            .unwrap()
            .iter()
            .map(|argument| argument.as_str().unwrap())
            .chain(["--json"])
            .collect();
        let (exit_code, result, _work) = execute(&arguments, config, "", &environment);
        let mut check = |key: &str, left: Value, right: &Value| {
            if &left != right {
                mismatches.push(format!("{name}: {key}: native {left} frozen {right}"));
            }
        };
        check("exit_code", exit_code.into(), &frozen["exit_code"]);
        let checks: Value = result["checks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| project(item, &["task_id", "check_id", "evidence_complete"]))
            .collect();
        let frozen_checks: Value = frozen["checks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| project(item, &["task_id", "check_id", "evidence_complete"]))
            .collect();
        check("checks", checks, &frozen_checks);
        check(
            "findings.len",
            result["findings"].as_array().unwrap().len().into(),
            &frozen["findings"].as_array().unwrap().len().into(),
        );
    }
    // Intended difference (ADR-0008, one config model): frozen `analyze` refused a legacy
    // component ici.toml (exit 2, nothing written); native projects it through the explicit
    // legacy reader and analyzes the same C++ scope (incomplete without a database: exit 3).
    assert_eq!(oracle["next"]["analyze_legacy_config"]["exit_code"], 2);
    let (exit_code, result, _work) = execute(
        &["analyze", "--json"],
        oracle["base"].as_str(),
        "",
        &environment,
    );
    assert_eq!(exit_code, 3);
    assert_eq!(result["gate"]["selected"], "INCOMPLETE");
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
