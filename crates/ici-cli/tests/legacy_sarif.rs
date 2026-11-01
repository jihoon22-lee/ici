//! Root decision E1, end to end: native `ici verify --sarif` on the Python, C++ and hybrid
//! projects frozen ran (`tests/rust-parity/reporting/legacy-sarif-oracle.json`, `end_to_end`)
//! writes frozen `reporters/sarif.py`'s historical shape, and the code-scanning identity of
//! every result (rule, `ici/v3` fingerprint, `%SRCROOT%` location, baseline state) matches.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-sarif-oracle.json"
    ))
    .unwrap()
}

fn write(base: &Path, tree: &Value) {
    for (name, text) in tree.as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
}

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// Run the captured steps natively; returns the exit codes and the written SARIF. (Exit codes
/// are the verify exit policy's, compared by the verify rows, not this writer's.)
fn run(case: &Value) -> (Vec<i32>, Value, String) {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().join("project");
    if let Some(copied) = case["copied"].as_str() {
        copy(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../..")
                .join(copied),
            &base,
        );
    } else {
        write(&base, &case["tree"]);
    }
    fs::create_dir_all(base.join(".git")).unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        (
            "XDG_CONFIG_HOME".to_owned(),
            home.path().display().to_string(),
        ),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
    ]);
    let steps = case["steps"].as_array().unwrap();
    let mut codes = vec![];
    let mut stdout = String::new();
    for (index, step) in steps.iter().enumerate() {
        if index + 1 == steps.len() && case["edit"].is_object() {
            write(&base, &case["edit"]);
        }
        let mut argv = vec!["ici"];
        argv.extend(
            step.as_array()
                .unwrap()
                .iter()
                .map(|part| part.as_str().unwrap()),
        );
        let output = commands::dispatch(&registry::parse(argv).unwrap(), &base, &environment)
            .unwrap_or_else(|error| panic!("{step}: {error}"));
        codes.push(output.exit_code);
        stdout = output.stdout;
    }
    let text = fs::read_to_string(base.join("out/report.sarif")).unwrap();
    let root = base.display().to_string();
    (
        codes,
        serde_json::from_str(&text.replace(&root, "<root>")).unwrap(),
        stdout,
    )
}

/// The code-scanning identity of every result, in document order.
fn identity(sarif: &Value) -> Vec<Value> {
    sarif["runs"][0]["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|result| {
            json!([
                result["ruleId"],
                result["fingerprints"],
                result["locations"],
                result["baselineState"],
                result["level"]
            ])
        })
        .collect()
}

/// Each result's alert identity apart from the region columns: rule, `ici/v3` fingerprint,
/// URI, start line and level, keyed by fingerprint.
fn alerts(sarif: &Value) -> BTreeMap<String, Value> {
    sarif["runs"][0]["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|result| {
            let location = &result["locations"][0]["physicalLocation"];
            (
                result["fingerprints"]["ici/v3"]
                    .as_str()
                    .unwrap()
                    .to_owned(),
                json!([
                    result["ruleId"],
                    location["artifactLocation"],
                    location["region"]["startLine"],
                    result["level"]
                ]),
            )
        })
        .collect()
}

fn native_and_frozen(name: &str) -> (Value, Value) {
    let oracle = oracle();
    let case = &oracle["end_to_end"][name];
    let (_, native, _) = run(case);
    let frozen = case["sarif"].clone();
    let document = &native["runs"][0];
    // The historical shape: no stored-run fields; frozen invocation and suite/engine counts.
    assert!(document.get("columnKind").is_none(), "{name}");
    assert!(
        document["results"][0].get("partialFingerprints").is_none(),
        "{name}"
    );
    assert_eq!(
        document["invocations"], frozen["runs"][0]["invocations"],
        "{name}"
    );
    for key in [
        "suite_status",
        "engine_count",
        "baseline_source",
        "baseline_gate_failed",
    ] {
        assert_eq!(
            document["properties"][key], frozen["runs"][0]["properties"][key],
            "{name} {key}"
        );
    }
    (native, frozen)
}

#[test]
fn cpp_verify_sarif_keeps_the_frozen_alert_identity() {
    let (native, frozen) = native_and_frozen("cpp");
    // Every result, its order, regions and the run properties equal frozen.
    assert_eq!(identity(&native), identity(&frozen));
    assert_eq!(
        native["runs"][0]["properties"],
        frozen["runs"][0]["properties"]
    );
}

/// Frozen alerts the native legacy projection also reports keep their identity; native-only
/// results are PASS inventory (`note`), never a new actionable alert.
fn assert_frozen_alerts_kept(name: &str, scope: impl Fn(&str) -> bool) {
    let (native, frozen) = native_and_frozen(name);
    let native = alerts(&native);
    for (fingerprint, alert) in alerts(&frozen) {
        if !scope(alert[1]["uri"].as_str().unwrap()) {
            continue;
        }
        assert_eq!(
            native.get(&fingerprint),
            Some(&alert),
            "{name} {fingerprint}"
        );
    }
    let frozen = alerts(&frozen);
    for (fingerprint, alert) in &native {
        if !frozen.contains_key(fingerprint) {
            assert_eq!(alert[3], "note", "{name}: {alert}");
        }
    }
}

#[test]
fn python_verify_sarif_keeps_the_frozen_alert_identity() {
    assert_frozen_alerts_kept("python", |_| true);
}

#[test]
fn hybrid_verify_sarif_keeps_the_frozen_source_alert_identity() {
    // Project-level Python inventory targets (`.`, `tests`) are compared by the legacy
    // projection rows, not here (see the closure record); every source alert is.
    assert_frozen_alerts_kept("hybrid", |uri| uri.starts_with("src/"));
}

#[test]
fn baseline_verify_sarif_is_written_with_the_frozen_baseline_properties() {
    // `--baseline` and `--fail-on-new` runs (a resolved and a new finding) write the
    // historical document instead of refusing it; the gate and source match frozen.
    for name in ["baseline", "baseline-fail-on-new"] {
        let (native, _) = native_and_frozen(name);
        assert!(
            native["runs"][0]["results"]
                .as_array()
                .unwrap()
                .iter()
                .any(|result| result["baselineState"] == "absent"),
            "{name}"
        );
    }
}

#[test]
fn engine_commands_have_no_sarif_option_as_in_frozen() {
    // Frozen `_ENGINE_COMMANDS` handlers take only `--report`; `--sarif` is a usage error.
    for command in ["line", "lint", "test", "complexity"] {
        assert!(
            registry::parse(["ici", command, "--sarif", "x.sarif"]).is_err(),
            "{command}"
        );
    }
    assert!(registry::parse(["ici", "verify", "--sarif", "x.sarif"]).is_ok());
}
