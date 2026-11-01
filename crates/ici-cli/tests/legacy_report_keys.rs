//! Per-engine keys of the historical JSON reports (`<engine>_report.json`, `verify_report.json`)
//! against the frozen CLI (`tests/rust-parity/python/capture_legacy_report_keys.py`, CPython
//! 3.10.21, the pinned ruff/mypy linked into one PATH directory).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-report-keys-oracle.json"
    ))
    .unwrap()
}

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("required native fixture tool {name} is missing"))
}

/// One PATH directory holding the captured ruff and mypy, checked against the captured versions.
fn tool_dir(oracle: &Value) -> tempfile::TempDir {
    let tools = tempfile::tempdir().unwrap();
    for (name, variable) in [
        ("ruff", "ICI_TEST_RUFF"),
        ("mypy", "ICI_TEST_MYPY"),
        ("python3", "ICI_TEST_PYTHON"),
    ] {
        let target = required(variable);
        let version = std::process::Command::new(&target)
            .arg("--version")
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8_lossy(&version.stdout).trim(),
            oracle["tool_versions"][name],
            "{variable} is not the captured {name}"
        );
        std::os::unix::fs::symlink(target, tools.path().join(name)).unwrap();
    }
    tools
}

fn project(oracle: &Value, case: &Value) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    let config = format!(
        "{}{}",
        oracle["base"].as_str().unwrap(),
        case["config"].as_str().unwrap()
    );
    fs::write(root.path().join("ici.toml"), config).unwrap();
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    root
}

fn run(root: &Path, path: &str, args: &[&str]) -> (i32, Value) {
    let invocation = registry::parse(std::iter::once("ici").chain(args.iter().copied())).unwrap();
    let mut environment = BTreeMap::from([("PATH".to_string(), path.to_string())]);
    if args[0] == "verify" {
        // Frozen verify ran `-m mypy` with its own interpreter (`sys.executable`); under the pyz
        // that is the launcher's choice, which ICI_PYTHON pins to the same captured CPython.
        environment.insert("ICI_PYTHON".into(), required("ICI_TEST_PYTHON"));
    }
    let output = commands::dispatch(&invocation, root, &environment).unwrap();
    let name = if args[0] == "verify" {
        "verify_report.json".to_owned()
    } else {
        format!("{}_report.json", args[0].replace('-', "_"))
    };
    let report = root.join(name);
    let value = serde_json::from_slice(&fs::read(&report).unwrap()).unwrap();
    fs::remove_file(report).unwrap();
    (output.exit_code, value)
}

/// Entry-level differences of two serialized support matrices (empty when equal).
fn support_differences(name: &str, actual: &Value, frozen: &Value) -> Vec<String> {
    let mut found = vec![];
    for key in ["project_languages", "project_frameworks"] {
        if actual[key] != frozen[key] {
            found.push(format!("{name}: {key} {} != {}", actual[key], frozen[key]));
        }
    }
    let empty = vec![];
    let actual_entries = actual["entries"].as_array().unwrap_or(&empty);
    let frozen_entries = frozen["entries"].as_array().unwrap_or(&empty);
    if actual_entries.len() != frozen_entries.len() {
        found.push(format!(
            "{name}: {} entries != {}",
            actual_entries.len(),
            frozen_entries.len()
        ));
    }
    for (left, right) in actual_entries.iter().zip(frozen_entries) {
        if left != right {
            let fields: Vec<_> = right
                .as_object()
                .unwrap()
                .iter()
                .filter(|(key, value)| left[key.as_str()] != **value)
                .map(|(key, value)| format!("{key}: {} != {value}", left[key.as_str()]))
                .collect();
            found.push(format!(
                "{name}: {}/{}: {}",
                right["engine_name"],
                right["language"],
                fields.join("; ")
            ));
        }
    }
    found
}

/// Engine evidence that differs from frozen for a reason outside the report projection.
const KNOWN_ENGINE_EVIDENCE: [(&str, &str, &str); 1] = [(
    "configured-hybrid/line",
    "NOT_RUN",
    "cross-area planning: `type='hybrid'` without C++ sources plans a cpp.line check that is \
     BLOCKED (no sources in scope), so the line engine reads NOT_RUN and the run exits 3; \
     frozen counted the Python sources (MEASURED, exit 0)",
)];

#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn single_engine_reports_carry_the_frozen_support_matrix() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let tools = tool_dir(&oracle);
    let with_tools = format!("{}:/usr/bin:/bin", tools.path().display());
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 21);
    let mut failures: Vec<String> = vec![];
    for (name, case) in cases {
        let root = project(&oracle, case);
        let path = if case["tools"] == true {
            with_tools.as_str()
        } else {
            "/usr/bin:/bin"
        };
        let (_, report) = run(
            root.path(),
            path,
            &[case["command"].as_str().unwrap(), "--report"],
        );
        let frozen = &case["frozen"];
        if let Some((_, native, _)) = KNOWN_ENGINE_EVIDENCE
            .iter()
            .find(|(known, _, _)| known == name)
        {
            // The engine's own evidence differs for a recorded reason; the project facts the
            // matrix is built from must still be frozen's.
            assert_eq!(report["evidence"], *native, "{name}: known native evidence");
            for key in ["project_languages", "project_frameworks"] {
                assert_eq!(
                    report["support_matrix"][key], frozen["support_matrix"][key],
                    "{name}: {key}"
                );
            }
            continue;
        }
        // Frozen single-engine commands never consulted the cache (false/null); the native
        // command reuses its own observation cache, so only the shape and a fresh-run miss are
        // frozen's.
        if report["cache_hit"] != false || !digest_or_null(&report["cache_key"]) {
            failures.push(format!(
                "{name}: cache {} {}",
                report["cache_hit"], report["cache_key"]
            ));
        }
        failures.extend(evidence_differences(
            name,
            &report["tool_evidence"],
            &frozen["tool_evidence"],
        ));
        if report["artifact_manifests"] != frozen["artifact_manifests"] {
            failures.push(format!(
                "{name}: artifact_manifests {}",
                report["artifact_manifests"]
            ));
        }
        if report["evidence"] != frozen["evidence"] {
            failures.push(format!(
                "{name}: engine evidence {} != {}",
                report["evidence"], frozen["evidence"]
            ));
        }
        failures.extend(support_differences(
            name,
            &report["support_matrix"],
            &frozen["support_matrix"],
        ));
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// Frozen tool evidence records: the same processes in order with frozen's names, exits, errors
/// and (empty) versions. Native argv is its own, so only the subcommand is compared.
fn evidence_differences(name: &str, actual: &Value, frozen: &Value) -> Vec<String> {
    let keys = [
        "name",
        "returncode",
        "timed_out",
        "truncated",
        "error",
        "version",
    ];
    let pick = |items: &Value| -> Vec<Value> {
        items
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .map(|item| {
                        let mut picked: serde_json::Map<String, Value> = keys
                            .iter()
                            .map(|key| ((*key).to_owned(), item[*key].clone()))
                            .collect();
                        // Ruff records name their subcommand; mypy argv is native's own.
                        if item["name"]
                            .as_str()
                            .is_some_and(|name| name.starts_with("ruff "))
                        {
                            picked.insert("subcommand".into(), item["argv"][1].clone());
                        }
                        // ADR-0008: the runtime metadata probe is the allowlisted fixed script
                        // (`-I -c ...`), where frozen ran `python -VV`; same position, version
                        // and exit, a different record name.
                        if item["name"] == "python runtime metadata" || item["name"] == "python -VV"
                        {
                            picked.insert("name".into(), Value::from("<runtime metadata>"));
                        }
                        Value::Object(picked)
                    })
                    .collect()
            })
            .unwrap_or_default()
    };
    if pick(actual) == pick(frozen) {
        vec![]
    } else {
        vec![format!(
            "{name}: tool_evidence {:?} != {:?}",
            pick(actual),
            pick(frozen)
        )]
    }
}

/// Frozen `_require_digest`: a cache key is null or `sha256:` and 64 lowercase hex digits.
fn digest_or_null(value: &Value) -> bool {
    value.is_null()
        || value.as_str().is_some_and(|key| {
            key.strip_prefix("sha256:").is_some_and(|hex| {
                hex.len() == 64
                    && hex
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
        })
}

/// Second-run cache reuse that differs from frozen by design (native namespace, native rules).
const CACHE_REUSE_DIFFERENCES: [(&str, bool, &str); 4] = [
    (
        "lint",
        false,
        "legacy Ruff runs keep frozen's explicit-file argv; their configuration closure is not \
         proven complete, so native never reuses them (frozen cached the engine result)",
    ),
    (
        "type",
        false,
        "legacy mypy runs (project configuration and plugins) are never reused natively",
    ),
    (
        "dead",
        true,
        "frozen DeadCodeEngine was CACHE_REUSE_SAFE=False; the native Python dead-code analysis \
         is a pure function of the hashed inputs and is reused",
    ),
    (
        "compile_db",
        true,
        "frozen did not store its Python-only compile_db SKIP; the native check output is reused",
    ),
];

#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn verify_reports_carry_the_frozen_suite_support_matrix_and_null_engine_matrices() {
    let oracle = oracle();
    let tools = tool_dir(&oracle);
    let with_tools = format!("{}:/usr/bin:/bin", tools.path().display());
    let mut failures: Vec<String> = vec![];
    for (name, case) in oracle["verify"].as_object().unwrap() {
        let root = project(&oracle, case);
        let profile = case["profile"].as_str().unwrap();
        for (label, extra) in [
            ("first", None),
            ("second", None),
            ("no-cache", Some("--no-cache")),
        ] {
            let frozen = &case["frozen"][label];
            let mut args = vec!["verify", "--report", "--profile", profile];
            args.extend(extra);
            let (_, report) = run(root.path(), &with_tools, &args);
            failures.extend(support_differences(
                &format!("{name}/{label}"),
                &report["support_matrix"],
                &frozen["support_matrix"],
            ));
            let results = report["results"].as_array().unwrap();
            let engines: Vec<_> = results.iter().map(|r| r["engine_name"].clone()).collect();
            let frozen_engines: Vec<_> = frozen["results"]
                .as_object()
                .unwrap()
                .keys()
                .map(|engine| Value::from(engine.as_str()))
                .collect();
            let mut sorted = engines.clone();
            sorted.sort_by_key(|engine| engine.as_str().unwrap().to_owned());
            if sorted != frozen_engines {
                failures.push(format!(
                    "{name}/{label}: engines {sorted:?} != {frozen_engines:?}"
                ));
            }
            for result in results {
                let engine = result["engine_name"].as_str().unwrap();
                let at = format!("{name}/{label}/{engine}");
                let expected = &frozen["results"][engine];
                if result["support_matrix"] != Value::Null {
                    failures.push(format!("{at}: result support_matrix is not null"));
                }
                failures.extend(evidence_differences(
                    &at,
                    &result["tool_evidence"],
                    &expected["tool_evidence"],
                ));
                if result["artifact_manifests"] != serde_json::json!([]) {
                    failures.push(format!(
                        "{at}: artifact_manifests {}",
                        result["artifact_manifests"]
                    ));
                }
                if !result["cache_hit"].is_boolean() || !digest_or_null(&result["cache_key"]) {
                    failures.push(format!(
                        "{at}: cache shape {} {}",
                        result["cache_hit"], result["cache_key"]
                    ));
                }
                // Without the cache both products report false/null for every engine; a first
                // run is never a hit; a second run hits exactly where frozen hit, except the
                // recorded native-namespace differences.
                let hit = match (
                    label,
                    CACHE_REUSE_DIFFERENCES
                        .iter()
                        .find(|(e, _, _)| *e == engine),
                ) {
                    ("second", Some((_, native, _))) => Value::Bool(*native),
                    _ => expected["cache_hit"].clone(),
                };
                if result["cache_hit"] != hit {
                    failures.push(format!("{at}: cache_hit {} != {hit}", result["cache_hit"]));
                }
                if label == "no-cache" && !result["cache_key"].is_null() {
                    failures.push(format!(
                        "{at}: --no-cache keeps key {}",
                        result["cache_key"]
                    ));
                }
                if result["cache_hit"] == true && result["cache_key"].is_null() {
                    failures.push(format!("{at}: a hit without a key"));
                }
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// Frozen BuildEngine publishes artifact manifests only for compiled C++ build sessions; a
/// Python release build under verify carries `artifact_manifests: []` on every engine.
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn python_build_under_verify_publishes_no_legacy_artifact_manifest_like_frozen() {
    let oracle = oracle();
    let tools = tool_dir(&oracle);
    let with_tools = format!("{}:/usr/bin:/bin", tools.path().display());
    let case = &oracle["build"];
    let root = project(&oracle, case);
    let (_, report) = run(
        root.path(),
        &with_tools,
        &["verify", "--report", "--no-cache"],
    );
    let frozen = &case["frozen"];
    let mut failures = vec![];
    let results = report["results"].as_array().unwrap();
    assert!(
        results
            .iter()
            .any(|result| result["engine_name"] == "build")
    );
    for result in results {
        let engine = result["engine_name"].as_str().unwrap();
        if result["artifact_manifests"] != serde_json::json!([]) {
            failures.push(format!("{engine}: {}", result["artifact_manifests"]));
        }
        if result["cache_key"] != Value::Null || result["cache_hit"] != false {
            failures.push(format!("{engine}: --no-cache keeps a cache key"));
        }
    }
    let build = results
        .iter()
        .find(|result| result["engine_name"] == "build")
        .unwrap();
    for key in ["status", "evidence"] {
        if build[key] != frozen["results"]["build"][key] {
            failures.push(format!(
                "build {key}: {} != {}",
                build[key], frozen["results"]["build"][key]
            ));
        }
    }
    assert_eq!(frozen["context_manifests"], serde_json::json!([]));
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
