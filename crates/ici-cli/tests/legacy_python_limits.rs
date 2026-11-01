//! Legacy Python bounded-analysis stops against the frozen CLI
//! (`tests/rust-parity/python/capture_legacy_python_limits.py`): `ici resource` past
//! MAX_RESOURCE_AST_NODES (engines/resource.py:185) and `ici dup` past a bounded comparison
//! limit (engines/dup.py:207). Fixtures are generated from the recorded parameters.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
];

/// The capture's generators (`constants`: `count + 6` CPython AST nodes in one statement;
/// `statements`: `1 + 4 * count + 5 + tuple` nodes over many statements; `functions`: identical
/// 14-line bodies).
fn generate(content: &Value) -> String {
    if let Some(text) = content.as_str() {
        return text.to_owned();
    }
    let count = content[1].as_u64().unwrap() as usize;
    match content[0].as_str().unwrap() {
        "constants" => format!("x = [{}]\n", vec!["0"; count].join(", ")),
        "statements" => format!(
            "{}x = ({})\n",
            "x = 0\n".repeat(count),
            vec!["0"; content[2].as_u64().unwrap() as usize].join(", ")
        ),
        "functions" => {
            let body: String = (0..12)
                .map(|j| format!("    v{j} = a * {j} + b - {j}\n"))
                .collect();
            (0..count)
                .map(|i| format!("def f{i}(a, b):\n{body}    return v0\n\n\n"))
                .collect()
        }
        other => panic!("unknown generator {other}"),
    }
}

#[test]
fn legacy_resource_and_dup_bounded_stops_match_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-python-limits-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 6);
    for (name, case) in cases {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("ici.toml"),
            oracle["base"].as_str().unwrap(),
        )
        .unwrap();
        for (file, content) in case["files"].as_object().unwrap() {
            let path = root.path().join(file);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, generate(content)).unwrap();
        }
        let command = case["command"].as_str().unwrap();
        let invocation = registry::parse(["ici", command, "--report"]).unwrap();
        let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
        let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
        let report: Value = serde_json::from_slice(
            &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
        )
        .unwrap();
        let frozen = &case["frozen"];
        if name == "resource/long-logical-statement" {
            // Reviewed difference: every native Python analysis refuses one logical statement of
            // more than 8192 tokens (shared parser safety bound) before counting AST nodes;
            // frozen parsed it with CPython and analyzed it.
            assert_eq!(frozen["status"], "PASS");
            assert_eq!(report["status"], "ERROR", "{name}");
            assert_eq!(
                report["targets"][0]["target_name"],
                "PythonAnalysis:syntax-resource-limit"
            );
            continue;
        }
        // Accepted exit policy: `dup` is required by default, so its ERROR leaves the native run
        // incomplete (exit 3) where frozen exited 1; `resource` is optional and keeps exit 1.
        let exit = if frozen["status"] == "ERROR" && command == "dup" {
            json!(3)
        } else {
            frozen["exit_code"].clone()
        };
        assert_eq!(json!(output.exit_code), exit, "{name}: exit");
        for key in ["status", "summary", "evidence"] {
            assert_eq!(report[key], frozen[key], "{name}: {key}");
        }
        let pick = |targets: &Value| -> Vec<Value> {
            targets
                .as_array()
                .unwrap()
                .iter()
                .map(|target| {
                    Value::Object(
                        FIELDS
                            .iter()
                            .map(|key| ((*key).to_owned(), target[*key].clone()))
                            .collect(),
                    )
                })
                .collect()
        };
        assert_eq!(
            pick(&report["targets"]),
            pick(&frozen["targets"]),
            "{name}: targets"
        );
    }
}
