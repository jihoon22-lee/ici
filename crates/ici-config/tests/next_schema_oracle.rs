//! Replay frozen v0.13.0 ici-next (`schema_version = 1`) verdicts against the native resolver.
//! Golden: tests/rust-parity/config/next-schema-oracle.json (capture_next_schema.py).
//! Reviewed native differences: tests/rust-parity/config/next-schema-differences.json.
use ici_config::*;
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};
use tempfile::TempDir;

fn fixture(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/rust-parity/config");
    serde_json::from_str(&fs::read_to_string(path.join(name)).unwrap()).unwrap()
}

enum Outcome {
    Accept(Box<ResolvedProject>, String),
    Reject(Vec<String>, String),
}

fn run(case: &Value) -> Outcome {
    let temporary = TempDir::new().unwrap();
    let root = temporary.path().join("project");
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(root.join(".git")).unwrap();
    let local = case["local"].as_str().map(|text| {
        let path = temporary.path().join("local.toml");
        fs::write(&path, text).unwrap();
        path
    });
    let explicit = case["explicit"].as_str().map(|name| root.join(name));
    let xdg = temporary.path().join("xdg");
    fs::create_dir(&xdg).unwrap();
    let environment = BTreeMap::from([("XDG_CONFIG_HOME".to_string(), xdg.display().to_string())]);
    match resolve_with_environment(&root, explicit.as_deref(), local.as_deref(), &environment) {
        Ok(project) => {
            let base = temporary
                .path()
                .canonicalize()
                .unwrap()
                .display()
                .to_string();
            Outcome::Accept(Box::new(project), base)
        }
        Err(error) => Outcome::Reject(
            std::iter::once(error.origin.key.clone())
                .chain(error.related.iter().map(|r| r.origin.key.clone()))
                .collect(),
            error.to_string(),
        ),
    }
}

fn anchored(root: &str, patterns: &[String]) -> Value {
    json!(
        patterns
            .iter()
            .map(|pattern| if root == "." || pattern.starts_with('/') {
                pattern.clone()
            } else {
                format!("{root}/{pattern}")
            })
            .collect::<Vec<_>>()
    )
}

/// The same flat projection the capture computes from the frozen `EffectiveConfig`.
fn project(project: &ResolvedProject) -> BTreeMap<String, Value> {
    let mut flat = BTreeMap::new();
    flat.insert("workspace.name".into(), json!(project.workspace_name));
    flat.insert("workspace.profile".into(), json!(project.profile));
    flat.insert("scope_kind".into(), json!(project.scope_kind));
    for (id, check) in &project.checks {
        flat.insert(format!("checks.{id}.enabled"), json!(check.enabled));
        flat.insert(format!("checks.{id}.required"), json!(check.required));
    }
    for component in &project.components {
        let name = if project.scope_kind == ici_domain::ScopeKind::Standalone {
            "<standalone>"
        } else {
            component.id.as_str()
        };
        let prefix = format!("components.{name}");
        let root = component.root.as_str();
        flat.insert(format!("{prefix}.root"), json!(root));
        flat.insert(format!("{prefix}.languages"), json!(component.languages));
        flat.insert(
            format!("{prefix}.sources"),
            anchored(root, &component.sources),
        );
        flat.insert(
            format!("{prefix}.include"),
            anchored(root, &component.include),
        );
        flat.insert(
            format!("{prefix}.exclude"),
            anchored(root, &component.exclude),
        );
        flat.insert(
            format!("{prefix}.vendor"),
            anchored(root, &component.vendor),
        );
        flat.insert(
            format!("{prefix}.test_paths"),
            anchored(root, &component.test_paths),
        );
        flat.insert(format!("{prefix}.build"), json!(component.build));
        flat.insert(format!("{prefix}.needs"), json!(component.needs));
        flat.insert(format!("{prefix}.external"), json!(component.external));
        flat.insert(
            format!("{prefix}.python.executable"),
            json!(component.python_executable),
        );
        flat.insert(
            format!("{prefix}.python.type_provider"),
            json!(component.python_type_provider),
        );
        for (id, check) in &component.checks {
            flat.insert(
                format!("{prefix}.checks.{id}.enabled"),
                json!(check.enabled),
            );
            flat.insert(
                format!("{prefix}.checks.{id}.required"),
                json!(check.required),
            );
        }
        // Declared integration fields stay raw in configuration; consumers apply defaults.
        for (index, case) in component.integrations.iter().enumerate() {
            for (field, value) in case.as_object().unwrap() {
                if field == "output_artifacts" {
                    for (position, output) in value.as_array().unwrap().iter().enumerate() {
                        for (key, item) in output.as_object().unwrap() {
                            flat.insert(
                                format!(
                                    "{prefix}.integrations[{index}].output_artifacts[{position}].{key}"
                                ),
                                item.clone(),
                            );
                        }
                    }
                } else {
                    flat.insert(
                        format!("{prefix}.integrations[{index}].{field}"),
                        value.clone(),
                    );
                }
            }
        }
    }
    for build in &project.builds {
        let prefix = format!("builds.{}", build.id);
        flat.insert(format!("{prefix}.system"), json!(build.system));
        flat.insert(format!("{prefix}.directory"), json!(build.directory));
        flat.insert(format!("{prefix}.project"), json!(build.project));
        flat.insert(format!("{prefix}.variant"), json!(build.variant));
        flat.insert(format!("{prefix}.prepare"), json!(build.prepare));
        flat.insert(format!("{prefix}.artifacts"), json!(build.artifacts));
    }
    for (index, item) in project.suppressions.iter().enumerate() {
        let prefix = format!("suppressions[{index}]");
        flat.insert(format!("{prefix}.fingerprint"), json!(item.fingerprint));
        flat.insert(format!("{prefix}.rule"), json!(item.rule));
        flat.insert(format!("{prefix}.path"), json!(item.path));
        flat.insert(format!("{prefix}.component"), json!(item.component));
        flat.insert(format!("{prefix}.reason"), json!(item.reason));
    }
    if let Some(publish) = &project.publish {
        flat.insert("publish.repo".into(), json!(publish.repo));
        flat.insert("publish.api_url".into(), json!(publish.api_url));
        flat.insert("publish.server_url".into(), json!(publish.server_url));
        flat.insert("publish.branch".into(), json!(publish.branch));
        flat.insert("publish.token_env".into(), json!(publish.token_env));
    }
    flat
}

/// Frozen problem keys and native origin keys are dotted with `[index]`; either may be the
/// more specific spelling of the same declaration. A document-level frozen problem has no key.
fn key_matches(native: &str, frozen: &str) -> bool {
    let related = |long: &str, short: &str| {
        long == short
            || long.starts_with(&format!("{short}."))
            || long.starts_with(&format!("{short}["))
    };
    frozen.is_empty() || related(native, frozen) || related(frozen, native)
}

#[test]
fn next_v1_cases_replay_frozen_values_and_problem_keys() {
    let oracle = fixture("next-schema-oracle.json");
    let differences = fixture("next-schema-differences.json");
    let cases = oracle["cases"].as_array().unwrap();
    assert!(cases.len() > 300);
    let mut failures = vec![];
    let mut used = 0;
    for case in cases {
        let id = case["id"].as_str().unwrap();
        let difference = differences.get(id);
        if let Some(entry) = difference {
            used += 1;
            assert!(
                entry["reason"].as_str().is_some_and(|r| r.len() > 20),
                "{id}"
            );
        }
        let expected = difference
            .and_then(|d| d["native"].as_str())
            .unwrap_or(case["outcome"].as_str().unwrap());
        match (expected, run(case)) {
            ("accept", Outcome::Accept(resolved, base)) => {
                let flat: BTreeMap<String, Value> = project(&resolved)
                    .into_iter()
                    .map(|(key, value)| {
                        let text = value.to_string().replace(&base, "<tmp>");
                        (key, serde_json::from_str(&text).unwrap())
                    })
                    .collect();
                let values = difference
                    .and_then(|d| d.get("native_values"))
                    .unwrap_or(&case["values"]);
                for (key, value) in values.as_object().into_iter().flatten() {
                    let native = flat.get(key).cloned().unwrap_or(Value::Null);
                    if &native != value {
                        failures.push(format!("{id}: {key} frozen {value} native {native}"));
                    }
                }
            }
            ("reject", Outcome::Reject(keys, text)) => {
                let frozen: Vec<String> = match difference.and_then(|d| d.get("native_key")) {
                    Some(key) => vec![key.as_str().unwrap().into()],
                    None => case["problems"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|p| p["key"].as_str().unwrap().to_string())
                        .collect(),
                };
                if !keys
                    .iter()
                    .any(|native| frozen.iter().any(|f| key_matches(native, f)))
                {
                    failures.push(format!(
                        "{id}: keys frozen {frozen:?} native {keys:?} ({text})"
                    ));
                }
            }
            (expected, Outcome::Accept(..)) => failures.push(format!(
                "{id}: expected {expected} ({}) native accept",
                case["problems"]
            )),
            (expected, Outcome::Reject(_, text)) => {
                failures.push(format!("{id}: expected {expected} native reject {text}"))
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{}\n{} failures",
        failures.join("\n"),
        failures.len()
    );
    assert_eq!(used, differences.as_object().unwrap().len());
}

/// Frozen `--local-config` accepted `output`/`cache` without applying them, and native sparse
/// files/preferences also accept `display.*`. None is honoured yet, so each accepted value is
/// reported with its file and line instead of silently looking applied.
#[test]
fn unapplied_output_cache_and_display_preferences_are_reported_not_silent() {
    let temporary = TempDir::new().unwrap();
    let root = temporary.path().join("project");
    fs::create_dir_all(root.join(".git")).unwrap();
    fs::write(root.join("app.py"), "x = 1\n").unwrap();
    let xdg = temporary.path().join("xdg");
    fs::create_dir_all(xdg.join("ici")).unwrap();
    let environment = BTreeMap::from([("XDG_CONFIG_HOME".to_string(), xdg.display().to_string())]);
    fs::write(root.join("ici.toml"), "schema_version = 2\n").unwrap();
    let quiet = resolve_with_environment(&root, None, None, &environment).unwrap();
    assert!(
        !quiet.limitations.iter().any(|l| l.contains("preference")),
        "{:?}",
        quiet.limitations
    );
    fs::write(
        root.join("ici.toml"),
        "schema_version = 2\noutput = \"results\"\n[display]\ncolor = \"never\"\n",
    )
    .unwrap();
    fs::write(
        xdg.join("ici/preferences.toml"),
        "cache = \"/var/tmp/ici\"\n[display]\nverbosity = \"high\"\n",
    )
    .unwrap();
    let local = temporary.path().join("local.toml");
    fs::write(&local, "output = \"mine\"\n").unwrap();
    let project = resolve_with_environment(&root, None, Some(&local), &environment).unwrap();
    for key in ["output", "cache", "display.color", "display.verbosity"] {
        assert!(
            project
                .limitations
                .iter()
                .any(|l| l.starts_with(&format!("{key} preference from "))
                    && l.contains("not applied")),
            "{key}: {:?}",
            project.limitations
        );
    }
    // The local overlay wins over the root spelling and is the reported origin.
    assert!(
        project
            .limitations
            .iter()
            .any(|l| l.starts_with("output preference from") && l.contains("local.toml:1"))
    );
    assert_eq!(
        project.checks, quiet.checks,
        "preferences never change inspection policy"
    );
}
