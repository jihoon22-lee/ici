//! Legacy `ici dead` C/C++ source policy (`engines.dead.include_generated` / `include_vendor`)
//! against frozen v0.13.0 (`tests/rust-parity/cpp/dead-source-policy-oracle.json`,
//! `capture_dead_source_policy.py`): a production, a generated, a vendor, a both-excluded and a
//! Qt moc source under `cpp_unused = 'auto'`, without a database (the unavailable scope still
//! counts the exclusions) and with one (the real g++ reports the unused functions of exactly the
//! sources each opt-in selects).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "end_line",
    "file_path",
];
const SOURCE_EXTRA: [&str; 5] = [
    "source_exclusion_counts",
    "source_files_excluded",
    "source_files_analyzed",
    "source_files_snapshotted",
    "source_bytes_analyzed",
];

fn database(root: &std::path::Path, sources: &[&String]) -> String {
    Value::Array(
        sources
            .iter()
            .enumerate()
            .map(|(index, source)| {
                json!({"directory": root, "file": source,
                    "arguments": ["g++", "-std=c++17", "-c", source, "-o", format!("{index}.o")]})
            })
            .collect(),
    )
    .to_string()
}

/// Positive: each opt-in adds exactly its sources to the compiler scope (neither 1, generated 3
/// with the moc source, vendor 2, both 5 findings). Negative: the excluded sources are counted
/// per reason and never reported. Failure: without a database the scope is frozen's
/// unavailable SKIP with the same exclusion counts for every option.
#[test]
#[ignore = "explicit actual /usr/bin/g++ unused-function probes (the captured g++ version)"]
fn legacy_cpp_dead_include_generated_and_vendor_select_sources_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/dead-source-policy-oracle.json"
    ))
    .unwrap();
    assert_eq!(
        oracle["oracle"],
        "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    let tree = oracle["tree"].as_object().unwrap();
    let sources: Vec<&String> = tree.keys().collect();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 8);
    let mut failures = vec![];
    for (name, frozen) in cases {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        let home = work.path().join("home");
        fs::create_dir_all(root.join(".hg")).unwrap();
        fs::create_dir(&home).unwrap();
        let with_database = frozen["database"] == true;
        let mut config = "[project]\ntype='cpp'\nsource_dirs=['src']\n".to_owned();
        if with_database {
            config.push_str("compile_database='compile_commands.json'\n");
        }
        config.push_str("[engines.dead]\ncpp_unused='auto'\n");
        config.push_str(
            oracle["options"][frozen["option"].as_str().unwrap()]
                .as_str()
                .unwrap(),
        );
        fs::write(root.join("ici.toml"), config).unwrap();
        for (path, content) in tree {
            let path = root.join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, content.as_str().unwrap()).unwrap();
        }
        if with_database {
            fs::write(
                root.join("compile_commands.json"),
                database(&root, &sources),
            )
            .unwrap();
        }
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let invocation = registry::parse(["ici", "dead", "--report"]).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let text = fs::read_to_string(root.join("dead_report.json"))
            .unwrap()
            .replace(&root.display().to_string(), "<root>");
        let report: Value = serde_json::from_str(&text).unwrap();
        let actual = json!({
            "database": frozen["database"],
            "option": frozen["option"],
            "exit_code": output.exit_code,
            "status": report["status"],
            "evidence": report["evidence"],
            "required": report["required"],
            "summary": report["summary"],
            "source_extra": Value::Object(SOURCE_EXTRA.iter().map(|key| ((*key).to_owned(), report["extra"][*key].clone())).collect()),
            "findings": report["findings"].as_array().unwrap().iter().map(|finding| json!({
                "rule_id": finding["rule_id"], "message": finding["message"],
                "confidence": finding["confidence"], "severity": finding["severity"],
                "path": finding["primary_location"]["path"],
                "start_line": finding["primary_location"]["start_line"],
            })).collect::<Vec<_>>(),
            "targets": report["targets"].as_array().unwrap().iter().map(|target| Value::Object(
                FIELDS.iter().map(|key| ((*key).to_owned(), target[*key].clone())).collect()
            )).collect::<Vec<_>>(),
        });
        if actual != *frozen {
            failures.push(format!("{name}\nnative: {actual:#}\nfrozen: {frozen:#}"));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
