//! Replay frozen v0.13.0 legacy `load_config` verdicts against the native resolver.
//! Golden: tests/rust-parity/config/legacy-schema-oracle.json (capture_legacy_schema.py).
//! Reviewed native differences: tests/rust-parity/config/legacy-schema-differences.json.
use ici_config::*;
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};
use tempfile::TempDir;

fn fixture(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/rust-parity/config");
    serde_json::from_str(&fs::read_to_string(path.join(name)).unwrap()).unwrap()
}

struct Outcome {
    accepted: Option<ConfigurationInspection>,
    keys: Vec<String>,
    text: String,
}

fn run(text: &str) -> Outcome {
    let project = TempDir::new().unwrap();
    let xdg = TempDir::new().unwrap();
    fs::create_dir(project.path().join(".git")).unwrap();
    fs::write(project.path().join("ici.toml"), text).unwrap();
    let environment = BTreeMap::from([(
        "XDG_CONFIG_HOME".to_string(),
        xdg.path().display().to_string(),
    )]);
    match inspect_configuration_with_environment(project.path(), None, None, &environment) {
        Ok(inspection) => Outcome {
            accepted: Some(inspection),
            keys: vec![],
            text: String::new(),
        },
        Err(error) => Outcome {
            accepted: None,
            keys: std::iter::once(error.origin.key.clone())
                .chain(error.related.iter().map(|r| r.origin.key.clone()))
                .collect(),
            text: error.to_string(),
        },
    }
}

/// Frozen dotted keys and native origin keys spell list members the same way.
fn key_matches(native: &str, frozen: &str) -> bool {
    let frozen = frozen.trim_end_matches(':');
    native == frozen
        || frozen.starts_with(&format!("{native}["))
        || frozen.starts_with(&format!("{native}."))
}

fn leaves(value: &Value, pointer: String, output: &mut Vec<(String, Value)>) {
    match value {
        Value::Object(map) if !map.is_empty() => {
            for (key, child) in map {
                leaves(child, format!("{pointer}/{key}"), output);
            }
        }
        _ => output.push((pointer, value.clone())),
    }
}

#[test]
fn legacy_defaults_match_frozen_default_policy_leaf_for_leaf() {
    let oracle = fixture("legacy-schema-oracle.json");
    let outcome = run("");
    let inspection = outcome.accepted.expect("empty legacy file is accepted");
    assert_eq!(inspection.schema_family, SchemaFamily::Legacy);
    let mut expected = vec![];
    leaves(&oracle["defaults"], String::new(), &mut expected);
    assert!(expected.len() > 130, "frozen defaults are enumerated");
    let mismatches: Vec<_> = expected
        .iter()
        .filter(|(pointer, value)| inspection.effective.pointer(pointer) != Some(value))
        .map(|(pointer, value)| {
            format!(
                "{pointer}: frozen {value} native {:?}",
                inspection.effective.pointer(pointer)
            )
        })
        .collect();
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
    // Absent keys without a frozen default stay absent: no invented native policy value.
    // `ici.version` is the running product version in both implementations (capture drops it).
    for key in LEGACY_KEYS
        .iter()
        .filter(|key| !key.contains("[]") && **key != "ici.version")
    {
        let pointer = format!("/{}", key.replace('.', "/"));
        if oracle["defaults"].pointer(&pointer).is_none() {
            assert!(
                inspection.effective.pointer(&pointer).is_none(),
                "{key}: native invents a default the frozen policy lacks"
            );
        }
    }
    for (pointer, _) in expected.iter().filter(|(_, v)| !v.is_object()) {
        let key = pointer[1..].replace('/', ".");
        let origin = inspection.origins.get(&key).expect("default origin");
        assert_eq!(origin.layer, "defaults", "{key}");
    }
}

#[test]
fn legacy_key_cases_replay_frozen_accept_reject_values_and_keys() {
    let oracle = fixture("legacy-schema-oracle.json");
    let differences = fixture("legacy-schema-differences.json");
    let cases = oracle["cases"].as_array().unwrap();
    assert!(cases.len() > 700);
    let mut failures = vec![];
    let mut used = 0;
    for case in cases {
        let id = case["id"].as_str().unwrap();
        let text = case["toml"].as_str().unwrap();
        let outcome = run(text);
        let difference = differences.get(id);
        let frozen = case["outcome"].as_str().unwrap();
        let expected = match difference {
            Some(entry) => {
                used += 1;
                assert!(
                    entry["reason"].as_str().is_some_and(|r| r.len() > 20),
                    "{id}: difference needs a reason"
                );
                entry["native"].as_str().unwrap()
            }
            None => frozen,
        };
        match (expected, &outcome.accepted) {
            ("accept", Some(inspection)) => {
                if let Some(pointer) = case["pointer"].as_str()
                    && difference.is_none()
                {
                    if inspection.effective.pointer(pointer) != Some(&case["value"]) {
                        failures.push(format!(
                            "{id}: value frozen {} native {:?}",
                            case["value"],
                            inspection.effective.pointer(pointer)
                        ));
                    }
                    let dotted = pointer[1..].replace('/', ".");
                    if !dotted.contains("cases")
                        && !dotted.contains("python_targets")
                        && inspection
                            .origins
                            .get(&dotted)
                            .is_none_or(|o| o.layer != "root" || !o.file.ends_with("ici.toml"))
                    {
                        failures.push(format!(
                            "{id}: origin {:?}",
                            inspection.origins.get(&dotted)
                        ));
                    }
                }
            }
            ("reject", None) => {
                let frozen_key = difference
                    .and_then(|d| d.get("native_key"))
                    .or(case.get("error_key"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                if !outcome.keys.iter().any(|k| key_matches(k, frozen_key)) {
                    failures.push(format!(
                        "{id}: key frozen {frozen_key:?} native {:?} ({})",
                        outcome.keys, case["message"]
                    ));
                }
            }
            (expected, _) => failures.push(format!(
                "{id}: expected {expected} (frozen {frozen}: {}) native {}",
                case["message"],
                if outcome.accepted.is_some() {
                    "accept".to_string()
                } else {
                    outcome.text.clone()
                }
            )),
        }
    }
    assert!(
        failures.is_empty(),
        "{}\n{} failures",
        failures.join("\n"),
        failures.len()
    );
    assert_eq!(
        used,
        differences.as_object().unwrap().len(),
        "every reviewed difference names an existing case"
    );
}

/// Sparse v2 accepts the legacy engine/build/project tables directly; the same declaration must
/// receive the same native verdict and effective value in both families.
#[test]
fn sparse_v2_gives_legacy_tables_the_same_native_verdicts() {
    let oracle = fixture("legacy-schema-oracle.json");
    let mut failures = vec![];
    for case in oracle["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let text = case["toml"].as_str().unwrap();
        let legacy = run(text);
        let sparse = run(&format!("schema_version = 2\n{text}"));
        match (&legacy.accepted, &sparse.accepted) {
            (Some(left), Some(right)) => {
                assert_eq!(right.schema_family, SchemaFamily::SparseV2);
                if let Some(pointer) = case["pointer"].as_str()
                    && left.effective.pointer(pointer) != right.effective.pointer(pointer)
                {
                    failures.push(format!("{id}: value differs between families"));
                }
            }
            (None, None) => {}
            _ => failures.push(format!(
                "{id}: legacy {} sparse {}",
                legacy.text, sparse.text
            )),
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// Frozen `load_config` merged XDG global, project ici.toml, dev.toml and ICI_CONFIG and wrote a
/// global default policy on a first config-less run (tests/rust-parity/config/
/// legacy-layers-oracle.json). Native reads only the project file (or ICI_CONFIG as an isolated
/// explicit file), never writes a global file, and reports the unmerged historical layers;
/// `legacy_migration_report` explains their winners and losers.
#[test]
fn historical_layers_are_reported_not_merged_and_no_global_default_is_written() {
    let oracle = fixture("legacy-layers-oracle.json")["observations"].clone();
    assert_eq!(
        oracle["first_run_global_default"]["global_file_written"],
        true
    );
    let temporary = TempDir::new().unwrap();
    let base = temporary.path();
    fs::create_dir_all(base.join("xdg")).unwrap();
    fs::create_dir_all(base.join("project/.git")).unwrap();
    fs::write(base.join("project/main.py"), "x = 1\n").unwrap();
    let mut environment = BTreeMap::from([(
        "XDG_CONFIG_HOME".to_string(),
        base.join("xdg").display().to_string(),
    )]);
    let project = base.join("project");
    // First config-less run: frozen wrote xdg/ici/ici.toml; native writes nothing.
    let first = resolve_with_environment(&project, None, None, &environment).unwrap();
    assert_eq!(first.schema_family, SchemaFamily::Automatic);
    assert!(fs::read_dir(base.join("xdg")).unwrap().next().is_none());
    // get_engine_config: the effective engine table, or nothing for an unknown engine.
    let legacy = run("");
    let inspection = legacy.accepted.unwrap();
    for (key, value) in oracle["get_engine_config"]["line"].as_object().unwrap() {
        assert_eq!(
            &inspection.effective["engines"]["line"][key], value,
            "{key}"
        );
    }
    assert_eq!(
        oracle["get_engine_config"]["missing"],
        serde_json::json!({})
    );
    assert!(
        inspection.effective["engines"]
            .get("no-such-engine")
            .is_none()
    );
    for (name, text) in oracle["layers"]["files"].as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    // Without ICI_CONFIG frozen merged all layers (dev.toml won); native uses the project file.
    assert_eq!(oracle["layers"]["without_explicit"]["warn_limit"], 800);
    let native = resolve_with_environment(&project, None, None, &environment).unwrap();
    assert_eq!(native.effective["engines"]["line"]["warn_limit"], 700);
    assert_eq!(native.effective["engines"]["line"]["fail_limit"], 1000);
    assert_ne!(native.effective["ici"]["policy_name"], "Global policy");
    for expected in [
        "legacy global policy is not implicitly merged",
        "legacy dev.toml is not implicitly merged",
    ] {
        assert!(
            native.limitations.iter().any(|l| l.contains(expected)),
            "{expected}: {:?}",
            native.limitations
        );
    }
    // With ICI_CONFIG frozen still merged every layer under it; native reads it in isolation.
    assert_eq!(oracle["layers"]["with_explicit"]["fail_limit"], 2000);
    environment.insert(
        "ICI_CONFIG".into(),
        base.join("explicit.toml").display().to_string(),
    );
    let explicit = resolve_with_environment(&project, None, None, &environment).unwrap();
    assert_eq!(explicit.effective["engines"]["line"]["warn_limit"], 900);
    assert_eq!(explicit.effective["engines"]["line"]["fail_limit"], 1000);
    assert!(explicit.config_source.unwrap().ends_with("explicit.toml"));
    let report = legacy_migration_report(&project, &environment);
    let contested = report
        .contested
        .iter()
        .find(|item| item.key == "engines.line.warn_limit")
        .expect("historical precedence is explained");
    assert_eq!(contested.winner.label, "ICI_CONFIG");
    assert_eq!(contested.winner.value, 900);
    // A missing explicit file is refused in both implementations.
    assert_eq!(oracle["missing_explicit"]["outcome"], "reject");
    environment.insert(
        "ICI_CONFIG".into(),
        base.join("missing.toml").display().to_string(),
    );
    let error = resolve_with_environment(&project, None, None, &environment).unwrap_err();
    assert!(error.message.contains("does not exist"), "{error}");
    assert_eq!(
        fs::read_to_string(base.join("project/ici.toml")).unwrap(),
        oracle["layers"]["files"]["project/ici.toml"]
    );
}

/// Frozen `config/automatic.py` (no ici.toml): one `project` component at the checkout root with
/// the discovered languages and default exclusions; an existing file is never bypassed and an
/// unsupported tree is refused. Native matches for single-package trees (monorepo partitioning
/// is a separate approved rule covered in resolution.rs).
#[test]
fn configless_runs_match_frozen_automatic_scope_and_refusals() {
    let oracle = fixture("legacy-layers-oracle.json")["observations"]["automatic"].clone();
    for (name, observed) in oracle.as_object().unwrap() {
        let temporary = TempDir::new().unwrap();
        let base = temporary.path().join("checkout");
        for (file, text) in observed["files"].as_object().unwrap() {
            let path = base.join(file);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text.as_str().unwrap()).unwrap();
        }
        fs::create_dir_all(base.join(".git")).unwrap();
        let start = if name == "subdirectory" {
            base.join("sub")
        } else {
            base.clone()
        };
        let result = resolve_with_environment(&start, None, None, &BTreeMap::new());
        match observed["outcome"].as_str().unwrap() {
            "accept" => {
                let project = result.unwrap_or_else(|e| panic!("{name}: {e}"));
                assert_eq!(project.schema_family, SchemaFamily::Automatic, "{name}");
                assert_eq!(project.root, base.canonicalize().unwrap(), "{name}");
                let ids: Vec<_> = project.components.iter().map(|c| c.id.clone()).collect();
                assert_eq!(serde_json::json!(ids), observed["components"], "{name}");
                let component = &project.components[0];
                assert_eq!(component.root, observed["component_root"], "{name}");
                let mut languages: Vec<_> = observed["languages"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_string())
                    .collect();
                languages.sort();
                assert_eq!(component.languages, languages, "{name}");
                for pattern in observed["exclude"].as_array().unwrap() {
                    assert!(
                        component.exclude.iter().any(|p| p == pattern),
                        "{name}: {pattern}"
                    );
                }
                assert_eq!(
                    serde_json::json!(project.profile),
                    observed["profile"],
                    "{name}"
                );
            }
            "reject" => {
                let error = result.unwrap_err();
                assert!(
                    error
                        .message
                        .contains("no supported source files discovered"),
                    "{name}: {error}"
                );
            }
            "existing_config" => {
                assert_eq!(
                    result.unwrap().schema_family,
                    SchemaFamily::Legacy,
                    "{name}"
                );
            }
            other => panic!("{other}"),
        }
    }
}
