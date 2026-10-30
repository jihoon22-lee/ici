//! Native wire documents against the frozen v0.13.0 JSON Schemas (`src/ici/schemas/`):
//! next run v1/v2, the JSONL event stream v1, the compilation export v1 and the auxiliary import
//! envelope v1.
//!
//! `check` implements exactly the draft 2020-12 keywords these four schemas use and panics on
//! any other, so a schema change can never be skipped silently. Every document checked here is
//! also written under `CARGO_TARGET_TMPDIR/wire-schemas/` so
//! `tests/rust-parity/reporting/validate_wire_schemas.py` can confirm with the reference
//! `jsonschema` validator that it agrees on each valid and invalid sample.
use ici_cli::{commands, registry};
use ici_domain::{RunResult, read_events};
use ici_reporting::{ImportOptions, ImportProvenance, import_report, load_auxiliary};
use regex::Regex;
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn schema(name: &str) -> Value {
    let text = match name {
        "ici-next-run-v1" => include_str!("../../../src/ici/schemas/ici-next-run-v1.schema.json"),
        "ici-next-run-v2" => include_str!("../../../src/ici/schemas/ici-next-run-v2.schema.json"),
        "ici-next-event-v1" => {
            include_str!("../../../src/ici/schemas/ici-next-event-v1.schema.json")
        }
        "ici-compilation-export-v1" => {
            include_str!("../../../src/ici/schemas/ici-compilation-export-v1.schema.json")
        }
        "ici-auxiliary-v1" => include_str!("../../../src/ici/schemas/ici-auxiliary-v1.schema.json"),
        _ => unreachable!("{name}"),
    };
    serde_json::from_str(text).unwrap()
}

fn type_matches(kind: &str, value: &Value) -> bool {
    match kind {
        "object" => value.is_object(),
        "array" => value.is_array(),
        "string" => value.is_string(),
        "boolean" => value.is_boolean(),
        "null" => value.is_null(),
        "number" => value.is_number(),
        "integer" => value.as_f64().is_some_and(|number| number.fract() == 0.0),
        _ => panic!("unknown schema type {kind}"),
    }
}

/// Every violation of `node` by `value`, as `path: keyword` strings.
fn check(root: &Value, node: &Value, value: &Value, path: &str, errors: &mut Vec<String>) {
    let Some(node) = node.as_object() else {
        if node == &json!(false) {
            errors.push(format!("{path}: false"));
        }
        return;
    };
    let mut local = vec![];
    for (keyword, rule) in node {
        let mut violated = false;
        match keyword.as_str() {
            // `format` is an annotation in draft 2020-12 unless a format checker is opted in,
            // which the reference validator (and so the cross-check) does not do.
            "$schema" | "$id" | "$defs" | "title" | "description" | "format" => {}
            "$ref" => {
                let pointer = rule.as_str().unwrap().strip_prefix('#').unwrap();
                let target = root.pointer(pointer).expect("local $ref");
                check(root, target, value, path, &mut local);
            }
            "type" => {
                violated = match rule {
                    Value::String(kind) => !type_matches(kind, value),
                    Value::Array(kinds) => !kinds
                        .iter()
                        .any(|kind| type_matches(kind.as_str().unwrap(), value)),
                    _ => panic!("type {rule}"),
                }
            }
            "const" => violated = value != rule,
            "enum" => violated = !rule.as_array().unwrap().contains(value),
            "pattern" => {
                if let Some(text) = value.as_str() {
                    violated = !Regex::new(rule.as_str().unwrap()).unwrap().is_match(text);
                }
            }
            "maxLength" | "minLength" => {
                if let Some(text) = value.as_str() {
                    let length = text.chars().count() as u64;
                    let bound = rule.as_u64().unwrap();
                    violated = if keyword == "maxLength" {
                        length > bound
                    } else {
                        length < bound
                    };
                }
            }
            "minimum" => {
                if let Some(number) = value.as_f64() {
                    violated = number < rule.as_f64().unwrap();
                }
            }
            "maxItems" => {
                if let Some(items) = value.as_array() {
                    violated = items.len() as u64 > rule.as_u64().unwrap();
                }
            }
            "items" => {
                if let Some(items) = value.as_array() {
                    for (index, item) in items.iter().enumerate() {
                        check(root, rule, item, &format!("{path}/{index}"), &mut local);
                    }
                }
            }
            "required" => {
                if let Some(object) = value.as_object() {
                    for key in rule.as_array().unwrap() {
                        if !object.contains_key(key.as_str().unwrap()) {
                            local.push(format!("{path}: required {key}"));
                        }
                    }
                }
            }
            "properties" => {
                if let Some(object) = value.as_object() {
                    for (key, property) in rule.as_object().unwrap() {
                        if let Some(item) = object.get(key) {
                            check(root, property, item, &format!("{path}/{key}"), &mut local);
                        }
                    }
                }
            }
            "additionalProperties" => {
                if let Some(object) = value.as_object() {
                    let known = node.get("properties").and_then(Value::as_object);
                    for (key, item) in object {
                        if known.is_some_and(|known| known.contains_key(key)) {
                            continue;
                        }
                        check(root, rule, item, &format!("{path}/{key}"), &mut local);
                    }
                }
            }
            "not" => {
                let mut inner = vec![];
                check(root, rule, value, path, &mut inner);
                violated = inner.is_empty();
            }
            "allOf" => {
                for branch in rule.as_array().unwrap() {
                    check(root, branch, value, path, &mut local);
                }
            }
            "oneOf" => {
                let matched = rule
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|branch| {
                        let mut inner = vec![];
                        check(root, branch, value, path, &mut inner);
                        inner.is_empty()
                    })
                    .count();
                violated = matched != 1;
            }
            "if" => {
                let mut condition = vec![];
                check(root, rule, value, path, &mut condition);
                let branch = if condition.is_empty() {
                    node.get("then")
                } else {
                    node.get("else")
                };
                if let Some(branch) = branch {
                    check(root, branch, value, path, &mut local);
                }
            }
            "then" | "else" => {}
            other => panic!("schema keyword {other} is not implemented by this checker"),
        }
        if violated {
            local.push(format!("{path}: {keyword}"));
        }
    }
    errors.extend(local);
}

fn violations(name: &str, value: &Value) -> Vec<String> {
    let schema = schema(name);
    let mut errors = vec![];
    check(&schema, &schema, value, "", &mut errors);
    errors
}

/// Records one sample for the reference-validator cross-check and returns its violations.
fn sample(name: &str, label: &str, value: &Value, valid: bool) -> Vec<String> {
    let directory = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("wire-schemas")
        .join(name)
        .join(if valid { "valid" } else { "invalid" });
    fs::create_dir_all(&directory).unwrap();
    fs::write(
        directory.join(format!("{label}.json")),
        serde_json::to_vec_pretty(value).unwrap(),
    )
    .unwrap();
    let errors = violations(name, value);
    assert_eq!(errors.is_empty(), valid, "{name} {label}: {errors:?}");
    errors
}

/// `base` with the member at `pointer` replaced, added, or (with `None`) removed.
fn mutate(base: &Value, pointer: &str, replacement: Option<Value>) -> Value {
    let mut value = base.clone();
    let (parent, key) = pointer.rsplit_once('/').unwrap();
    let parent = value.pointer_mut(parent).unwrap();
    match replacement {
        Some(replacement) if parent.is_array() => {
            parent[key.parse::<usize>().unwrap()] = replacement;
        }
        Some(replacement) => parent[key] = replacement,
        None => {
            parent.as_object_mut().unwrap().remove(key);
        }
    }
    value
}

fn environment() -> BTreeMap<String, String> {
    BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())])
}

/// A python project whose run carries findings, checks, blocked tasks and TEM limitations.
fn analyzed(root: &Path, extra: &[&str]) -> (Value, String) {
    let invocation = registry::parse(
        [
            "ici",
            "analyze",
            "--json",
            "--events",
            "events.jsonl",
            "--result",
            "run.json",
        ]
        .into_iter()
        .chain(extra.iter().copied()),
    )
    .unwrap();
    commands::dispatch(&invocation, root, &environment()).unwrap();
    let run = serde_json::from_slice(&fs::read(root.join("run.json")).unwrap()).unwrap();
    let events = fs::read_to_string(root.join("events.jsonl")).unwrap();
    fs::remove_file(root.join("events.jsonl")).unwrap();
    (run, events)
}

fn python_project() -> tempfile::TempDir {
    let work = tempfile::tempdir().unwrap();
    fs::write(
        work.path().join("ici.toml"),
        "schema_version=1\n[workspace]\nname=\"wire\"\nprofile=\"fast\"\n[[components]]\nid=\"project\"\nroot=\".\"\nlanguages=[\"python\"]\n",
    )
    .unwrap();
    let branches: String = (0..30)
        .map(|index| format!("    if value == {index}:\n        return {index}\n"))
        .collect();
    fs::write(
        work.path().join("main.py"),
        format!("def answer(value):\n{branches}    return eval(str(value))\n"),
    )
    .unwrap();
    work
}

#[test]
fn native_runs_and_events_validate_against_the_frozen_next_schemas() {
    let work = python_project();
    let (first, events) = analyzed(work.path(), &[]);
    assert_eq!(first["schema_version"], 2);
    assert!(!first["findings"].as_array().unwrap().is_empty());
    assert!(
        !first["execution"]["blocked_task_ids"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    sample("ici-next-run-v2", "native-analyze", &first, true);
    fs::rename(
        work.path().join("run.json"),
        work.path().join("baseline.json"),
    )
    .unwrap();
    let (compared, _) = analyzed(work.path(), &["--baseline", "baseline.json"]);
    assert_eq!(compared["baseline"]["state"], "comparable");
    sample(
        "ici-next-run-v2",
        "native-analyze-baseline",
        &compared,
        true,
    );
    let lines: Vec<Value> = events
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert!(lines.len() >= 4);
    for (index, line) in lines.iter().enumerate() {
        sample("ici-next-event-v1", &format!("native-{index}"), line, true);
    }
    // Frozen-written runs calibrate the checker on the same schema.
    for (label, text) in [
        (
            "frozen-analyze-cold",
            include_str!("../../../tests/rust-parity/oracle/analyze-cold.json"),
        ),
        (
            "frozen-run-v2",
            include_str!("../../../tests/rust-parity/domain/run-v2.json"),
        ),
    ] {
        sample(
            "ici-next-run-v2",
            label,
            &serde_json::from_str(text).unwrap(),
            true,
        );
    }
}

#[test]
fn published_v1_runs_validate_and_roundtrip_through_the_native_reader_as_v1() {
    for (label, text) in [
        (
            "run-success",
            include_str!("../../../tests/fixtures/ici-next/run-success.json"),
        ),
        (
            "run-code-fail",
            include_str!("../../../tests/fixtures/ici-next/run-code-fail.json"),
        ),
        (
            "run-required-incomplete",
            include_str!("../../../tests/fixtures/ici-next/run-required-incomplete.json"),
        ),
        (
            "run-partial-selection",
            include_str!("../../../tests/fixtures/ici-next/run-partial-selection.json"),
        ),
        (
            "run-cancelled",
            include_str!("../../../tests/fixtures/ici-next/run-cancelled.json"),
        ),
    ] {
        let published: Value = serde_json::from_str(text).unwrap();
        sample("ici-next-run-v1", label, &published, true);
        let written = RunResult::from_json(text).unwrap().to_value().unwrap();
        assert_eq!(written["schema_version"], 1, "{label}");
        sample(
            "ici-next-run-v1",
            &format!("{label}-native"),
            &written,
            true,
        );
    }
}

#[test]
fn schema_invalid_runs_and_events_are_refused_by_the_schema_and_the_native_reader() {
    let work = python_project();
    let (run, events) = analyzed(work.path(), &[]);
    let event: Value = serde_json::from_str(events.lines().next().unwrap()).unwrap();
    let finding = "/findings/0";
    let cases = [
        (
            "unknown-top-level",
            mutate(&run, "/unexpected", Some(json!(true))),
        ),
        (
            "future-version",
            mutate(&run, "/schema_version", Some(json!(3))),
        ),
        ("v2-without-checks", mutate(&run, "/checks", None)),
        ("missing-gate", mutate(&run, "/gate", None)),
        (
            "unknown-evidence",
            mutate(&run, &format!("{finding}/evidence"), Some(json!("GUESSED"))),
        ),
        (
            "absolute-location",
            mutate(
                &run,
                &format!("{finding}/primary_location/path"),
                Some(json!("/etc/passwd")),
            ),
        ),
        (
            "parent-location",
            mutate(
                &run,
                &format!("{finding}/primary_location/path"),
                Some(json!("../outside.py")),
            ),
        ),
        (
            "uppercase-task-id",
            mutate(
                &run,
                "/execution/blocked_task_ids/0",
                Some(json!("Project.Python.Lint")),
            ),
        ),
        (
            "bad-policy-digest",
            mutate(&run, "/identity/policy_digest", Some(json!("md5:1"))),
        ),
        (
            "uppercase-component",
            mutate(&run, "/scope/selected_components/0", Some(json!("Project"))),
        ),
    ];
    for (label, value) in cases {
        sample("ici-next-run-v2", label, &value, false);
        assert!(
            RunResult::from_value(value).is_err(),
            "native reader accepted schema-invalid {label}"
        );
    }
    let mut v1_with_checks = mutate(&run, "/schema_version", Some(json!(1)));
    sample("ici-next-run-v1", "v1-with-checks", &v1_with_checks, false);
    assert!(RunResult::from_value(v1_with_checks.clone()).is_err());
    v1_with_checks.as_object_mut().unwrap().remove("checks");
    v1_with_checks["unexpected"] = json!(1);
    sample("ici-next-run-v1", "v1-unknown-key", &v1_with_checks, false);
    assert!(RunResult::from_value(v1_with_checks).is_err());
    for (label, value) in [
        (
            "future-version",
            mutate(&event, "/schema_version", Some(json!(2))),
        ),
        (
            "foreign-schema",
            mutate(&event, "/schema_id", Some(json!("ici.next.run"))),
        ),
    ] {
        sample("ici-next-event-v1", label, &value, false);
        assert!(read_events(&value.to_string()).is_err(), "{label}");
    }
    // The reader skips (and notes) event types it does not know, like the frozen reader;
    // the schema still rejects them on the wire.
    let unknown = mutate(&event, "/event_type", Some(json!("future")));
    sample("ici-next-event-v1", "unknown-type", &unknown, false);
    let (read, notes) = read_events(&unknown.to_string()).unwrap();
    assert!(read.is_empty() && notes.len() == 1);
}

fn export_fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int value;\n").unwrap();
    let argv = [
        "/private/compiler/bin/clang++".to_string(),
        "-std=c++17".into(),
        "-c".into(),
        root.path().join("main.cpp").to_string_lossy().into_owned(),
        "-o".into(),
        root.path().join("main.o").to_string_lossy().into_owned(),
    ];
    fs::write(
        root.path().join("compile_commands.json"),
        serde_json::to_vec(&json!([{"directory":root.path(),"file":root.path().join("main.cpp"),"arguments":argv}])).unwrap(),
    )
    .unwrap();
    root
}

#[test]
fn native_compilation_export_validates_and_schema_violations_are_detected() {
    let root = export_fixture();
    let invocation = registry::parse(["ici", "export-compilation-context"]).unwrap();
    let output = commands::dispatch(
        &invocation,
        root.path(),
        &BTreeMap::from([("PATH".into(), String::new())]),
    )
    .unwrap();
    assert_eq!(output.exit_code, 0, "{}", output.stderr);
    let payload: Value = serde_json::from_str(&output.stdout).unwrap();
    sample(
        "ici-compilation-export-v1",
        "native-standalone",
        &payload,
        true,
    );
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/compilation-export-oracle.json"
    ))
    .unwrap();
    let mut frozen = 0;
    let mut walk = vec![&corpus];
    while let Some(node) = walk.pop() {
        match node {
            Value::Object(object) => {
                if object.get("schema_version") == Some(&json!("ici.compilation-export/v1")) {
                    sample(
                        "ici-compilation-export-v1",
                        &format!("frozen-{frozen}"),
                        node,
                        true,
                    );
                    frozen += 1;
                } else {
                    walk.extend(object.values());
                }
            }
            Value::Array(items) => walk.extend(items),
            _ => {}
        }
    }
    assert!(frozen > 0, "frozen export corpus carries payloads");
    for (label, pointer, replacement) in [
        (
            "future-version",
            "/schema_version",
            Some(json!("ici.compilation-export/v2")),
        ),
        ("unknown-evidence", "/evidence", Some(json!("GUESSED"))),
        ("missing-project", "/project", None),
        ("unknown-key", "/unexpected", Some(json!(1))),
        (
            "bad-digest",
            "/compilation/semantic_digest",
            Some(json!("sha256:xyz")),
        ),
    ] {
        let value = mutate(&payload, pointer, replacement);
        sample("ici-compilation-export-v1", label, &value, false);
    }
}

#[test]
fn native_auxiliary_imports_validate_and_invalid_envelopes_are_refused_by_both() {
    let provenance = ImportProvenance {
        source_path: "external-output".into(),
        scope: "external CI job".into(),
        imported_at: "2026-10-29T00:00:00Z".into(),
        observed_at: Some("2026-10-28T23:59:00+09:00".into()),
    };
    let sarif = json!({"version":"2.1.0","runs":[{"tool":{"driver":{"name":"external"}},"results":[{"ruleId":"X1","level":"warning","message":{"text":"external finding"},"locations":[{"physicalLocation":{"artifactLocation":{"uri":"src/main.py"},"region":{"startLine":2,"startColumn":1}}}]}]}]});
    let inputs: [(&str, Vec<u8>); 3] = [
        ("lcov", b"SF:src/main.cpp\nDA:1,3\nDA:2,0\nend_of_record\n".to_vec()),
        (
            "junit",
            br#"<testsuite name="suite"><testcase name="pass" file="tests/a.py" line="3"/><testcase name="fail"><failure>bad</failure></testcase><testcase name="skip"><skipped/></testcase></testsuite>"#.to_vec(),
        ),
        ("sarif", serde_json::to_vec(&sarif).unwrap()),
    ];
    let mut documents = vec![];
    for (format, bytes) in inputs {
        let document = import_report(format, &bytes, &provenance, &ImportOptions::default())
            .unwrap()
            .to_value()
            .unwrap();
        sample(
            "ici-auxiliary-v1",
            &format!("native-{format}"),
            &document,
            true,
        );
        documents.push(document);
    }
    for (label, pointer, replacement) in [
        ("foreign-schema", "/schema_id", Some(json!("ici.next.run"))),
        ("future-version", "/schema_version", Some(json!(2))),
        ("claims-a-gate", "/gate", Some(json!("PASS"))),
        ("measured-evidence", "/evidence", Some(json!("MEASURED"))),
        ("unknown-format", "/format", Some(json!("xml"))),
        ("missing-scope", "/scope", None),
        ("unknown-key", "/unexpected", Some(json!(1))),
    ] {
        let value = mutate(&documents[0], pointer, replacement);
        sample("ici-auxiliary-v1", label, &value, false);
        assert!(
            load_auxiliary(&serde_json::to_vec(&value).unwrap()).is_err(),
            "native reader accepted schema-invalid {label}"
        );
    }
}
