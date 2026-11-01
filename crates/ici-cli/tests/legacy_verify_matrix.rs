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
        let states: Value = result["checks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["state"].clone())
            .collect();
        let frozen_states: Value = frozen["checks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["state"].clone())
            .collect();
        check("states", states, &frozen_states);
        let keys = [
            "task_id",
            "rule_id",
            "message",
            "severity",
            "confidence",
            "evidence",
            "primary_location",
            "related_locations",
            "fingerprint",
        ];
        let found: Vec<Value> = result["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| project(item, &keys))
            .collect();
        let expected = frozen["findings"].as_array().unwrap();
        check("findings.len", found.len().into(), &expected.len().into());
        for (actual, frozen) in found.iter().zip(expected) {
            let mut actual = actual.clone();
            let mut frozen = frozen.clone();
            if frozen["rule_id"] == "cycle.include" {
                // Recorded difference: frozen hashed absolute checkout paths in Python set
                // order; native names the cycle from its first sorted member at the include.
                for key in [
                    "fingerprint",
                    "message",
                    "primary_location",
                    "related_locations",
                ] {
                    actual[key] = Value::Null;
                    frozen[key] = Value::Null;
                }
            }
            if name == "next_verify" {
                // Recorded difference: frozen next paths are relative to the component root
                // (`src`); native keeps workspace-relative paths, so the identity differs too.
                if let Some(path) = frozen["primary_location"]["path"].as_str() {
                    frozen["primary_location"]["path"] = format!("src/{path}").into();
                }
                actual["fingerprint"] = Value::Null;
                frozen["fingerprint"] = Value::Null;
            }
            check("finding", actual, &frozen);
        }
        check(
            "gate.selected",
            result["gate"]["selected"].clone(),
            &frozen["gate"]["selected"],
        );
        check(
            "execution",
            result["execution"].clone(),
            &frozen["execution"],
        );
        check("metrics", result["metrics"].clone(), &frozen["metrics"]);
        // The compilation blocking reasons are frozen's own words.
        for reason in frozen["gate"]["reasons"].as_array().unwrap() {
            let reason = reason.as_str().unwrap();
            if reason.contains(".cpp.compile:") || reason.contains(".cpp.diagnostics:") {
                let present = result["gate"]["reasons"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|item| item == reason);
                check("gate.reason", present.into(), &Value::from(true));
            }
        }
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

#[test]
fn cpp_compilation_context_export_matches_the_frozen_cli_payload_and_refusals() {
    let oracle = oracle();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let exports = oracle["exports"].as_object().unwrap();
    assert_eq!(exports.len(), 4);
    for (name, frozen) in exports {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().join("project");
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
            &root,
        );
        fs::write(root.join("ici.toml"), oracle["base"].as_str().unwrap()).unwrap();
        for (file, text) in frozen["files"].as_object().unwrap() {
            fs::write(root.join(file), text.as_str().unwrap()).unwrap();
        }
        let options: Vec<&str> = frozen["options"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap())
            .collect();
        let arguments = ["ici", "export-compilation-context"]
            .into_iter()
            .chain(options)
            .chain(["--output", "-"]);
        let invocation = registry::parse(arguments).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        assert_eq!(output.exit_code, frozen["exit_code"], "{name}");
        assert_eq!(output.stderr.trim(), frozen["stderr"], "{name}");
        if frozen["payload"].is_null() {
            assert!(output.stdout.is_empty(), "{name}");
            continue;
        }
        let mut payload: Value = serde_json::from_str(&output.stdout).unwrap();
        // The producer names its own release; every compilation fact and digest is frozen's.
        assert_eq!(payload["producer"]["name"], "ici");
        payload["producer"]["version"] = frozen["payload"]["producer"]["version"].clone();
        assert_eq!(payload, frozen["payload"], "{name}");
    }
}

/// The legacy descriptor cost profile is applied exactly once: every check whose frozen
/// descriptor lacks the profile is omitted once with the honest "not in profile" reason (never
/// "disabled"), every other enabled check is planned, and a single-engine command still runs its
/// own deep-only engine under a configured fast profile.
#[test]
fn legacy_descriptor_profiles_are_applied_once_for_verify_and_single_commands() {
    let catalog = ici_domain::builtin_catalog();
    let base = "[project]\ntype='cpp'\nsource_dirs=['src']\n";
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    for (profile, configured, flag) in [
        ("fast", true, false),
        ("standard", false, false),
        ("deep", true, false),
        ("deep", false, true),
        ("fast", false, true),
    ] {
        let work = tempfile::tempdir().unwrap();
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
            work.path(),
        );
        let config = if configured {
            format!("{base}[ici]\nprofile='{profile}'\n")
        } else {
            base.to_string()
        };
        fs::write(work.path().join("ici.toml"), config).unwrap();
        let mut arguments = vec!["ici", "verify"];
        if flag {
            arguments.extend(["--profile", profile]);
        }
        let invocation = registry::parse(arguments).unwrap();
        let prepared =
            ici_cli::planning::prepare(&invocation, work.path(), &environment, false).unwrap();
        let wanted: ici_domain::Profile = serde_json::from_value(Value::from(profile)).unwrap();
        let label = format!("{profile} configured={configured} flag={flag}");
        let mut seen = std::collections::BTreeSet::new();
        for omitted in &prepared.plan.omitted {
            assert!(
                seen.insert((omitted.component_id.clone(), omitted.check_id.clone())),
                "{label}: {} omitted twice",
                omitted.check_id
            );
            let Some(engine) = ici_application::plan::engine_name(&omitted.check_id) else {
                continue;
            };
            let Some(descriptor) = catalog.legacy.iter().find(|item| item.name == engine) else {
                continue;
            };
            let in_profile = descriptor.profiles.contains(&wanted);
            assert_eq!(
                omitted.reason == format!("not in profile '{profile}'"),
                !in_profile,
                "{label}: {} omitted as {}",
                omitted.check_id,
                omitted.reason
            );
        }
        for planned in &prepared.plan.checks {
            if let Some(engine) = ici_application::plan::engine_name(&planned.check.id)
                && let Some(descriptor) = catalog.legacy.iter().find(|item| item.name == engine)
            {
                assert!(
                    descriptor.profiles.contains(&wanted),
                    "{label}: {} planned outside its profile",
                    planned.check.id
                );
            }
        }
        let planned = |id: &str| {
            prepared
                .plan
                .checks
                .iter()
                .any(|check| check.check.id == id)
        };
        assert_eq!(planned("cpp.cognitive"), profile == "deep", "{label}");
        assert!(planned("cpp.complexity"), "{label}");
    }
    // `ici cognitive` under a configured fast profile still runs its own deep-only engine.
    let work = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
        work.path(),
    );
    fs::write(
        work.path().join("ici.toml"),
        format!("{base}[ici]\nprofile='fast'\n"),
    )
    .unwrap();
    let invocation = registry::parse(["ici", "cognitive", "--report"]).unwrap();
    let prepared =
        ici_cli::planning::prepare(&invocation, work.path(), &environment, false).unwrap();
    assert!(
        prepared
            .plan
            .checks
            .iter()
            .any(|check| check.check.id == "cpp.cognitive")
    );
}

/// A legacy suite without any C/C++ component still lists the frozen compile_db (and, in deep,
/// thread_sanitize) engines as SKIP NOT_APPLICABLE rows, in the frozen engine order.
#[test]
fn legacy_python_only_suite_keeps_the_frozen_cpp_not_applicable_engines() {
    let oracle = oracle();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let suites = oracle["python_suites"].as_object().unwrap();
    assert_eq!(suites.len(), 3);
    let mut mismatches = Vec::new();
    for (profile, frozen) in suites {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("src/pkg")).unwrap();
        fs::write(
            root.path().join("src/pkg/__init__.py"),
            oracle["python_source"].as_str().unwrap(),
        )
        .unwrap();
        fs::write(
            root.path().join("ici.toml"),
            oracle["python_base"].as_str().unwrap(),
        )
        .unwrap();
        let invocation =
            registry::parse(["ici", "verify", "--profile", profile.as_str(), "--report"]).unwrap();
        commands::dispatch(&invocation, root.path(), &environment).unwrap();
        let report: Value =
            serde_json::from_slice(&fs::read(root.path().join("verify_report.json")).unwrap())
                .unwrap();
        let names: Value = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .map(|engine| engine["engine_name"].clone())
            .collect();
        assert_eq!(names, frozen["engine_names"], "{profile}: engine order");
        for expected in frozen["engines"].as_array().unwrap() {
            let actual = report["results"]
                .as_array()
                .unwrap()
                .iter()
                .find(|engine| engine["engine_name"] == expected["engine_name"])
                .unwrap();
            compare_engine(profile, actual, expected, &mut mismatches);
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}

/// `ici verify` with a discovered root compile_commands.json naming every production unit.
/// Cases: the discovered database alone and with compile_db required/forbidden flags. The source
/// engines, the compiler-traced cycle verdict, compile_db (coverage, configuration and flag-policy
/// targets, summary, coverage score) and the not-applicable engines equal frozen. Open (recorded
/// on their rows): the exact-context legacy projections of lint (C++Syntax:<configuration>
/// targets) and dead (C++UnusedFunctionError) still report native modern shapes.
#[test]
#[ignore = "explicit actual g++ legacy verify with a compilation database: ICI_TEST_CXX must be the oracle g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0"]
fn legacy_cpp_verify_with_a_compilation_database_matches_frozen_source_engines() {
    let oracle = oracle();
    let environment = compiler_environment(&oracle);
    let base = oracle["base"].as_str().unwrap();
    const OPEN: [&str; 0] = [];
    let mut mismatches = Vec::new();
    for (name, frozen) in oracle["database_cases"].as_object().unwrap() {
        let config = format!(
            "{base}{}",
            oracle["database_configs"][name].as_str().unwrap()
        );
        let work = tempfile::tempdir().unwrap();
        let root = work.path().join("project");
        copy_tree(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../parity-fixtures/cpp/legacy-scope-fixture"),
            &root,
        );
        fs::write(root.join("ici.toml"), config).unwrap();
        fs::write(
            root.join("compile_commands.json"),
            oracle["database"].as_str().unwrap(),
        )
        .unwrap();
        if let Some(files) = oracle["database_files"][name].as_object() {
            for (path, text) in files {
                fs::write(root.join(path), text.as_str().unwrap()).unwrap();
            }
        }
        let invocation = registry::parse(["ici", "verify", "--report"]).unwrap();
        commands::dispatch(&invocation, &root, &environment).unwrap();
        let report: Value =
            serde_json::from_slice(&fs::read(root.join("verify_report.json")).unwrap()).unwrap();
        let engines = report["results"].as_array().unwrap();
        let expected = frozen["engines"].as_array().unwrap();
        let names = |list: &[Value]| -> Vec<Value> {
            list.iter()
                .map(|engine| engine["engine_name"].clone())
                .collect()
        };
        assert_eq!(names(engines), names(expected), "{name}: engine set");
        for (actual, expected) in engines.iter().zip(expected) {
            if OPEN.contains(&expected["engine_name"].as_str().unwrap()) {
                continue;
            }
            compare_engine(name, actual, expected, &mut mismatches);
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
