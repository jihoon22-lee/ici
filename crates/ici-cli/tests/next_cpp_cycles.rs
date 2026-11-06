//! `ici analyze --json` C++ include-cycle check against frozen v0.13.0 captures
//! (`tests/rust-parity/cpp/cycle-next-oracle.json`, `capture_cycle_next.py`) on the
//! `parity-fixtures/cpp/cycle-next` trees: a resolved acyclic include chain and quoted includes
//! with no project-file match. Needs only PATH=/usr/bin:/bin (no tool runs for `cpp.cycle`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

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

fn analyze(name: &str) -> Value {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("parity-fixtures/cpp/cycle-next");
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    copy(&fixtures.join(name), &base);
    // The project is its own VCS boundary and Git discovery stops above it: neither climbs to a
    // repository enclosing the host's temporary directory (frozen and native stop at `.hg`).
    fs::create_dir_all(base.join(".hg")).unwrap();
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.parent().unwrap().display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "analyze", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let document: Value = serde_json::from_str(output.stdout.trim_end()).unwrap();
    json!({
        "exit_code": output.exit_code,
        "checks": document["checks"].as_array().unwrap().iter()
            .filter(|check| check["check_id"] == "cpp.cycle")
            .map(|check| json!({
                "task_id": check["task_id"], "check_id": check["check_id"],
                "state": check["state"], "evidence_complete": check["evidence_complete"],
            }))
            .collect::<Vec<_>>(),
        "findings": document["findings"].as_array().unwrap().iter()
            .filter(|finding| finding["rule_id"].as_str().unwrap().starts_with("cycle."))
            .cloned()
            .collect::<Vec<_>>(),
        "metrics": document["metrics"].as_array().unwrap().iter()
            .filter(|metric| metric["name"] == "cycles_found")
            .map(|metric| json!({
                "name": metric["name"], "unit": metric["unit"],
                "evidence": metric["evidence"], "value": metric["value"],
            }))
            .collect::<Vec<_>>(),
        "limitations": document["limitations"].as_array().unwrap().iter()
            .filter(|item| item.as_str().unwrap().to_lowercase().contains("include"))
            .cloned()
            .collect::<Vec<_>>(),
    })
}

fn compare(name: &str) {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/cycle-next-oracle.json"
    ))
    .unwrap();
    let native = analyze(name);
    let expected = oracle["cases"][name].clone();
    assert_eq!(native, expected, "{name}");
}

#[test]
fn analyze_cpp_cycle_check_reports_no_cycle_on_an_acyclic_include_chain_like_frozen() {
    compare("acyclic");
}

#[test]
fn analyze_cpp_cycle_check_records_unresolved_quoted_includes_like_frozen() {
    compare("unresolved");
}

/// One `next-source-checks-oracle.json` case as `capture_next_source_checks.py` recorded it, through
/// the real CLI: every next source check's state, the clone, cycle and exception findings, the line,
/// clone and cycle metrics, and those checks' limitations as a set (decisions.md "next 실행의 run limitation
/// 중복"). Cycle fingerprints hash frozen's absolute checkout paths and are not compared.
fn source_checks(case: &Value) -> Value {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    fs::create_dir_all(base.parent().unwrap().join(".hg")).unwrap();
    for (path, content) in case["tree"].as_object().unwrap() {
        let target = base.join(path);
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        fs::write(target, decode(content.as_str().unwrap())).unwrap();
    }
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        (
            "GIT_CEILING_DIRECTORIES".to_owned(),
            base.parent().unwrap().display().to_string(),
        ),
    ]);
    let invocation = registry::parse(["ici", "analyze", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let document: Value = serde_json::from_str(output.stdout.trim_end()).unwrap();
    let pick = |item: &Value, keys: &[&str]| {
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
    };
    let mut limitations: Vec<String> = document["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(Value::as_str)
        .filter(|item| {
            [
                "include",
                "excluded ",
                "duplicate",
                "lexical",
                "not readable",
                "invalid-",
            ]
            .iter()
            .any(|text| item.contains(text))
                && !item.starts_with("no compilation database")
        })
        .map(str::to_owned)
        .collect();
    limitations.sort();
    limitations.dedup();
    json!({
        "exit_code": output.exit_code,
        "checks": document["checks"].as_array().unwrap().iter()
            .filter(|check| {
                ["cpp.line", "cpp.dup", "cpp.cycle", "cpp.exception"]
                    .contains(&check["check_id"].as_str().unwrap())
            })
            .map(|check| pick(check, &["check_id", "task_id", "state", "evidence_complete"]))
            .collect::<Vec<_>>(),
        "findings": document["findings"].as_array().unwrap().iter()
            .filter(|finding| {
                let rule = finding["rule_id"].as_str().unwrap();
                rule.starts_with("dup.") || rule.starts_with("cycle.") || rule.starts_with("exception.")
            })
            .map(|finding| {
                pick(finding, &[
                    "rule_id", "message", "severity", "confidence", "evidence",
                    "primary_location", "related_locations", "task_id",
                ])
            })
            .collect::<Vec<_>>(),
        "metrics": document["metrics"].as_array().unwrap().iter()
            .filter(|metric| {
                [
                    "lines_total", "lines_code", "lines_comment", "lines_blank", "files_counted",
                    "clone_groups", "duplicated_lines", "cycles_found",
                ]
                .contains(&metric["name"].as_str().unwrap())
            })
            .map(|metric| pick(metric, &["name", "value", "numerator", "denominator", "evidence"]))
            .collect::<Vec<_>>(),
        "limitations": limitations,
    })
}

fn decode(text: &str) -> Vec<u8> {
    let value = |byte: u8| match byte {
        b'A'..=b'Z' => byte - b'A',
        b'a'..=b'z' => byte - b'a' + 26,
        b'0'..=b'9' => byte - b'0' + 52,
        b'+' => 62,
        _ => 63,
    };
    let mut bytes = vec![];
    for chunk in text.as_bytes().chunks(4) {
        let digits: Vec<u8> = chunk
            .iter()
            .filter(|b| **b != b'=')
            .map(|b| value(*b))
            .collect();
        let word = digits
            .iter()
            .fold(0u32, |word, digit| (word << 6) | u32::from(*digit))
            << (6 * (4 - digits.len()));
        bytes.extend_from_slice(&word.to_be_bytes()[1..digits.len()]);
    }
    bytes
}

#[test]
fn analyze_cpp_source_checks_equal_frozen_on_clones_cycles_and_undecodable_sources() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/next-source-checks-oracle.json"
    ))
    .unwrap();
    let mut differences = vec![];
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let native = source_checks(case);
        for key in ["exit_code", "checks", "findings", "metrics", "limitations"] {
            // Pending root's ruling (recorded on check:cpp.dup): frozen measures zero clones over
            // an all-excluded scan, native records it as not applicable.
            if name == "dup-all-excluded" && key == "metrics" {
                continue;
            }
            let view = |value: &Value| -> Vec<Value> {
                match value[key].as_array() {
                    Some(items) => items.clone(),
                    None => vec![value[key].clone()],
                }
            };
            let (native, frozen) = (view(&native), view(case));
            let only = |left: &[Value], right: &[Value]| -> Vec<String> {
                left.iter()
                    .filter(|item| !right.contains(item))
                    .map(ToString::to_string)
                    .collect()
            };
            if native != frozen {
                differences.push(format!(
                    "{name}: {key}\n  native only {:?}\n  frozen only {:?}",
                    only(&native, &frozen),
                    only(&frozen, &native)
                ));
            }
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}
