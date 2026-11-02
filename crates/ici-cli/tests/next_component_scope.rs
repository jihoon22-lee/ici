//! next-v1 component scope keys against the frozen `ici next verify --json` oracle
//! (`tests/rust-parity/cpp/capture_next_component_scope.py`): `external` members, `vendor`,
//! `builds.*.prepare` without a prepared database, conventional and malformed databases,
//! `cpp.qt` (inline and child file) and the child-file `[component]` form.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-component-scope-oracle.json"
    ))
    .unwrap()
}

fn materialize(project: &Path, case: &Value) {
    for (name, text) in case["outside"].as_object().unwrap() {
        fs::write(project.parent().unwrap().join(name), text.as_str().unwrap()).unwrap();
    }
    for (name, text) in case["files"].as_object().unwrap() {
        let path = project.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let text = text
            .as_str()
            .unwrap()
            .replace("{root}", &project.display().to_string());
        fs::write(path, text).unwrap();
    }
    for (name, target) in case["links"].as_object().unwrap() {
        std::os::unix::fs::symlink(target.as_str().unwrap(), project.join(name)).unwrap();
    }
    fs::write(project.join("ici.toml"), case["config"].as_str().unwrap()).unwrap();
}

fn pick(item: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| {
                (
                    (*key).to_owned(),
                    item.get(*key).cloned().unwrap_or(Value::Null),
                )
            })
            .collect(),
    )
}

fn projected(document: &Value) -> Value {
    let list = |key: &str, keys: &[&str]| -> Value {
        document[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| pick(item, keys))
            .collect()
    };
    json!({
        "gate": pick(&document["gate"], &["selected", "reasons", "has_violations"]),
        "checks": list("checks", &["task_id", "check_id", "state", "evidence_complete", "variant"]),
        "blocked": document["execution"]["blocked_task_ids"],
        "failed": document["execution"]["failed_task_ids"],
        "metrics": list("metrics", &["name", "value", "numerator", "denominator", "unit", "evidence"]),
        "findings": list("findings", &["task_id", "rule_id", "message", "severity", "primary_location"]),
        "source_files": document["identity"]["source"].get("files").cloned().unwrap_or(json!([])),
        "external_inputs": document["identity"]["source"].get("external_inputs").cloned().unwrap_or(json!([])),
        "limitations": document["limitations"],
    })
}

/// Native `ici next verify --json` with PATH pinned to an empty directory, HOME and the cache
/// isolated, exactly like the capture.
fn run(case: &Value) -> Value {
    let work = tempfile::tempdir().unwrap();
    let project = work.path().join("project");
    fs::create_dir_all(&project).unwrap();
    fs::create_dir_all(work.path().join("bin")).unwrap();
    materialize(&project, case);
    let environment = BTreeMap::from([
        (
            "PATH".to_string(),
            work.path().join("bin").display().to_string(),
        ),
        ("HOME".to_string(), work.path().display().to_string()),
        (
            "XDG_CACHE_HOME".to_string(),
            work.path().join("cache").display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "next", "verify", "--json"]).unwrap();
    let root = project.display().to_string();
    match commands::dispatch(&invocation, &project, &environment) {
        Ok(output) => {
            let document: Value = serde_json::from_str(&output.stdout)
                .unwrap_or_else(|error| panic!("{error}: {}", output.stdout));
            let text = projected(&document).to_string().replace(&root, "{root}");
            let mut captured: Value = serde_json::from_str(&text).unwrap();
            captured["exit_code"] = json!(output.exit_code);
            captured
        }
        Err(error) => json!({"exit_code": 2, "output": error.to_string().replace(&root, "{root}")}),
    }
}

fn sorted(items: &Value) -> Vec<String> {
    let mut items: Vec<String> = items
        .as_array()
        .unwrap()
        .iter()
        .map(Value::to_string)
        .collect();
    items.sort();
    items
}

fn language(path: &str, languages: &[&str]) -> bool {
    let extension = Path::new(path)
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default();
    languages.iter().any(|language| match *language {
        "cpp" => matches!(
            extension,
            "c" | "cc" | "cpp" | "cxx" | "h" | "hh" | "hpp" | "hxx" | "ixx"
        ),
        _ => matches!(extension, "py" | "pyi"),
    })
}

#[test]
fn next_component_scope_keys_match_the_frozen_members_metrics_checks_and_gate() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 29);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let actual = run(case);
        if let Some(output) = frozen["output"].as_str() {
            // Configuration refusal before any run: exit 2 naming the same key. Native wording
            // is the config rows' recorded located diagnostic.
            // `config: <file>: <key>: <message>`
            let key = output.split(": ").nth(2).unwrap();
            // A dangling `build` is refused by native at the component (frozen names the
            // `build` key inline and the composed `<model>` unit for a child file).
            let needle = if name.ends_with("build-dangling") {
                "component references an undeclared build".to_owned()
            } else {
                format!(": {key}: ")
            };
            let refused = actual["exit_code"] == 2
                && actual["output"]
                    .as_str()
                    .is_some_and(|text| text.contains(&needle));
            if frozen["exit_code"] != 2 || !refused {
                eprintln!("== {name}: frozen {output}\nactual {actual}");
                mismatches.push(name.clone());
            }
            continue;
        }
        if name == "cmake-tests-declared" {
            // Open on check:cpp.test: a CMake tree whose literal add_test sits behind a literal
            // add_subdirectory declares a suite, so neither side blocks with the no-suite
            // reason; frozen then expands one `<task>.<build>-ctest` suite task and blocks it
            // ('ctest is not available'), native keeps one cpp.test task blocked on its own
            // build-directory precondition.
            let test = |document: &Value| {
                document["checks"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|check| check["check_id"] == "cpp.test")
                    .cloned()
                    .unwrap()
            };
            let suite_reason = |gate: &Value| {
                gate["reasons"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|reason| reason.as_str().unwrap().contains("no declared test suite"))
            };
            let blocked = test(&actual)["state"] == "BLOCKED"
                && test(frozen)["state"] == "BLOCKED"
                && frozen["blocked"] == json!(["app.cpp.test.native-ctest"])
                && actual["blocked"] == json!(["app.cpp.test"])
                && !suite_reason(&actual["gate"])
                && !suite_reason(&frozen["gate"])
                && actual["exit_code"] == frozen["exit_code"];
            if !blocked {
                eprintln!("== {name} (suite expansion)\nactual {actual}");
                mismatches.push(name.clone());
            }
            continue;
        }
        let mut differences = vec![];
        let expected_checks = frozen["checks"].clone();
        // Every metric, `cpp.compile`'s frozen `units.covered` (numerator/denominator) and
        // `units.generated` included.
        let expected_metrics = frozen["metrics"].clone();
        let expected_gate = frozen["gate"].clone();
        let expected_exit = frozen["exit_code"].clone();
        let expected_failed = frozen["failed"].clone();
        let actual_metrics = actual["metrics"].clone();
        if sorted(&actual_metrics) != sorted(&expected_metrics) {
            differences.push(format!("metrics {actual_metrics} vs {expected_metrics}"));
        }
        if actual["checks"] != expected_checks {
            differences.push(format!("checks {} vs {expected_checks}", actual["checks"]));
        }
        for key in ["blocked", "findings", "external_inputs"] {
            if actual[key] != frozen[key] {
                differences.push(format!("{key} {} vs {}", actual[key], frozen[key]));
            }
        }
        if actual["failed"] != expected_failed {
            differences.push(format!("failed {} vs {expected_failed}", actual["failed"]));
        }
        if actual["exit_code"] != expected_exit {
            differences.push(format!("exit {} vs {expected_exit}", actual["exit_code"]));
        }
        // Native identity lists the component's analysable-language members only; frozen lists
        // every member file (a `.txt`/`.md` external or a `.py` one in a C++ component).
        let languages: Vec<&str> = if name == "python-external" {
            vec!["python"]
        } else {
            vec!["cpp"]
        };
        let members: Vec<Value> = frozen["source_files"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|path| language(path.as_str().unwrap(), &languages))
            .cloned()
            .collect();
        if actual["source_files"] != json!(members) {
            differences.push(format!(
                "source_files {} vs {members:?}",
                actual["source_files"]
            ));
        }
        // Gate: selection, violations and every frozen reason equal, except the malformed-
        // database compile wording (open on check:cpp.compile).
        let open_reason =
            |reason: &str| name == "malformed-database" && reason.contains(".cpp.compile: ");
        let reasons = |gate: &Value| -> Vec<String> {
            gate["reasons"]
                .as_array()
                .unwrap()
                .iter()
                .map(|reason| reason.as_str().unwrap().to_owned())
                .filter(|reason| !open_reason(reason))
                .collect()
        };
        if actual["gate"]["selected"] != expected_gate["selected"]
            || actual["gate"]["has_violations"] != expected_gate["has_violations"]
            || reasons(&actual["gate"]) != reasons(&expected_gate)
        {
            differences.push(format!("gate {} vs {expected_gate}", actual["gate"]));
        }
        // Root ruling (decisions.md "component 밖 `external` 항목"): a declared external
        // member outside the component root is refused by dup and exception exactly as frozen
        // (`external-second-component`, `child-file`). The run's limitation list is
        // deduplicated natively (`ici-application` assemble), frozen repeats it per check.
        let outside = |document: &Value| {
            document["limitations"]
                .as_array()
                .unwrap()
                .iter()
                .any(|item| item.as_str().unwrap().starts_with("outside-project: "))
        };
        if outside(&actual) != outside(frozen) {
            differences.push(format!(
                "outside-project limitations {} vs {}",
                outside(&actual),
                outside(frozen)
            ));
        }
        // Blocked compile-input wording is part of the run's limitations too.
        for limitation in frozen["limitations"].as_array().unwrap() {
            let limitation = limitation.as_str().unwrap();
            if (limitation.starts_with("no compilation database for the component")
                || limitation.contains(": no compilation database for its C++ scope (")
                || limitation.starts_with("no artifact contract")
                || limitation.starts_with("build is not instrumented"))
                && !actual["limitations"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|native| native == limitation)
            {
                differences.push(format!("limitation {limitation:?} missing"));
            }
        }
        if !differences.is_empty() {
            eprintln!("== {name}\n{}", differences.join("\n"));
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// `cpp.qt` has no consumer in frozen next composition (`config/composition.py` never reads
/// `CppSettings.qt`): true, false and omitted give the same run, natively too.
#[test]
fn cpp_qt_is_accepted_without_changing_the_run() {
    let oracle = oracle();
    let runs: Vec<Value> = ["qt-true", "qt-false", "qt-omitted"]
        .iter()
        .map(|name| {
            let mut actual = run(&oracle["cases"][*name]);
            actual.as_object_mut().unwrap().remove("limitations");
            actual
        })
        .collect();
    assert_eq!(runs[0], runs[1]);
    assert_eq!(runs[0], runs[2]);
    assert_eq!(runs[0]["exit_code"], 0, "{}", runs[0]);
}
