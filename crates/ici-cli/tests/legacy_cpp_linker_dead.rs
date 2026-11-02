//! Legacy `ici dead` GNU ELF section-GC projection (frozen `_cpp_linker_dead_symbols` and
//! `DeadCodeEngine._linker_findings`/`_scoped_findings`) against frozen v0.13.0 runs
//! (`tests/rust-parity/cpp/linker-dead-oracle.json`, `capture_linker_dead.py`) over
//! parity-fixtures/cpp/linker-dead: the discarded hidden function's WARN target, its exact
//! `ici.dead.gnu-elf-discarded-function` finding and `cpp_linker_details`, the exact `GNU ld
//! reachability scan` PASS rows, the disabled compiler scope and the configure failure.
//! Compared: exit code, status, evidence, required flag, summary, targets, findings, every frozen
//! engine extra key and the set of tool evidence names. The tool evidence records are the
//! processes each implementation actually ran (decisions.md "C++ 도구 증거는 실제 실행 기록"):
//! native probes the driver identity per link target and reads each direct object's readelf
//! tables once, before any discarded section is known (frozen reads them per discarded object).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    process::Command,
};

const OBJECT_READS: [&str; 3] = ["readelf -WS", "readelf -Ws", "readelf --section-groups"];

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

fn banner(executable: &str) -> Option<String> {
    let output = Command::new(executable).arg("--version").output().unwrap();
    String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .next()
        .map(str::to_owned)
}

fn names(items: &Value) -> BTreeSet<String> {
    items
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["name"].as_str().unwrap().to_owned())
        .collect()
}

#[test]
#[ignore = "explicit actual /usr/bin/cmake, /usr/bin/g++ and GNU binutils relinks (the captured versions)"]
fn legacy_cpp_linker_dead_projects_frozen_section_gc_results() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/linker-dead-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    assert_eq!(banner("/usr/bin/g++").as_deref(), oracle["gxx"].as_str());
    assert_eq!(
        banner("/usr/bin/cmake").as_deref(),
        oracle["cmake"].as_str()
    );
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/linker-dead")
        .canonicalize()
        .unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 3);
    let mut mismatches = vec![];
    for (name, expected) in cases {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy(&fixtures.join(name), &root);
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            (
                "XDG_CONFIG_HOME".to_owned(),
                home.join(".config").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_owned(),
                home.join(".cache").display().to_string(),
            ),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let invocation = registry::parse(["ici", "dead", "--report"]).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let text = fs::read_to_string(root.join("dead_report.json"))
            .unwrap()
            .replace(&root.display().to_string(), "<root>");
        let report: Value = serde_json::from_str(&text).unwrap();
        let pick = |value: &Value, keys: &[&str]| -> Value {
            Value::Object(
                keys.iter()
                    .map(|key| ((*key).to_owned(), value[*key].clone()))
                    .collect(),
            )
        };
        let mut findings: Vec<Value> = report["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                pick(
                    finding,
                    &[
                        "rule_id",
                        "severity",
                        "confidence",
                        "message",
                        "explanation",
                        "remediation",
                        "tool_rule_id",
                        "tool_name",
                        "tool_version",
                        "primary_location",
                        "metrics",
                    ],
                )
            })
            .collect();
        // Native reads every direct object's readelf tables up front; frozen reads them only
        // for an object with a discarded section. The scope findings name the tools that ran.
        let discarded = report["extra"]["cpp_linker_discarded_sections_observed"] != 0;
        for finding in &mut findings {
            if let Some(tool) = finding["tool_name"].as_str()
                && !discarded
            {
                let kept: Vec<&str> = tool
                    .split('+')
                    .filter(|name| !OBJECT_READS.contains(name))
                    .collect();
                finding["tool_name"] = json!(kept.join("+"));
            }
        }
        findings.sort_by_key(|finding| serde_json::to_string(finding).unwrap());
        // Every frozen engine extra key; native adds its own evidence beside them (R11).
        let extra: serde_json::Map<String, Value> = expected["extra"]
            .as_object()
            .unwrap()
            .keys()
            .map(|key| (key.clone(), report["extra"][key].clone()))
            .collect();
        let actual = json!({
            "exit_code": output.exit_code,
            "status": report["status"],
            "evidence": report["evidence"],
            "required": report["required"],
            "summary": report["summary"],
            "targets": report["targets"].as_array().unwrap().iter()
                .map(|target| pick(target, &["target_name", "status", "message", "file_path", "start_line", "metrics"]))
                .collect::<Vec<_>>(),
            "findings": findings,
            "extra": extra,
        });
        let mut wanted = expected.clone();
        wanted.as_object_mut().unwrap().remove("tool_evidence");
        if actual != wanted {
            eprintln!("== {name}\nnative: {actual:#}\nfrozen: {wanted:#}");
            mismatches.push(name.clone());
        }
        let mut ran = names(&report["tool_evidence"]);
        if !discarded {
            ran.retain(|name| !OBJECT_READS.contains(&name.as_str()));
        }
        if ran != names(&expected["tool_evidence"]) {
            eprintln!("== {name} tool evidence names: {ran:?}");
            mismatches.push(format!("{name}:tool_evidence"));
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
