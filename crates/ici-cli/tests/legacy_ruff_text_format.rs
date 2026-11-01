//! Legacy `ici lint` with a Ruff whose formatter has no JSON output (frozen engines/lint.py:651,
//! the text dialect after the `ruff format --help` capability probe) against the frozen CLI
//! (`tests/rust-parity/python/capture_legacy_ruff_text_format.py`, Ruff 0.5.7).
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

fn pick(items: &Value, keys: &[&str]) -> Vec<Value> {
    items
        .as_array()
        .unwrap()
        .iter()
        .map(|item| {
            Value::Object(
                keys.iter()
                    .map(|key| ((*key).to_owned(), item[*key].clone()))
                    .collect(),
            )
        })
        .collect()
}

#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn legacy_lint_reads_the_text_dialect_of_a_formatter_without_json_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-ruff-text-format-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let ruff = std::env::var("ICI_TEST_RUFF_TEXT_FORMAT")
        .expect("required native fixture tool ICI_TEST_RUFF_TEXT_FORMAT is missing");
    let version = std::process::Command::new(&ruff)
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&version.stdout).trim(),
        oracle["ruff_version"],
        "ICI_TEST_RUFF_TEXT_FORMAT is not the captured Ruff"
    );
    let tools = tempfile::tempdir().unwrap();
    std::os::unix::fs::symlink(&ruff, tools.path().join("ruff")).unwrap();
    let path = format!("{}:/usr/bin:/bin", tools.path().display());
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 6);
    for (name, case) in cases {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("ici.toml"),
            oracle["base"].as_str().unwrap(),
        )
        .unwrap();
        for (file, text) in case["files"].as_object().unwrap() {
            let target = root.path().join(file);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(target, text.as_str().unwrap()).unwrap();
        }
        let invocation = registry::parse(["ici", "lint", "--report"]).unwrap();
        let environment = BTreeMap::from([("PATH".to_string(), path.clone())]);
        let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
        let report: Value =
            serde_json::from_slice(&fs::read(root.path().join("lint_report.json")).unwrap())
                .unwrap();
        // Reviewed difference (legacy_python_engines.rs SYNTAX_WORDING): frozen embedded the
        // running CPython's `SyntaxError.msg`; native names the pinned parser's wording.
        let frozen: Value =
            serde_json::from_str(&serde_json::to_string(&case["frozen"]).unwrap().replace(
                "SyntaxError: invalid syntax",
                "SyntaxError: Expected a parameter or the end of the parameter list",
            ))
            .unwrap();
        let frozen = &frozen;
        // Accepted exit policy: a required engine's ERROR leaves the native run incomplete
        // (exit 3) where frozen exited 1.
        let exit = if frozen["status"] == "ERROR" {
            json!(3)
        } else {
            frozen["exit_code"].clone()
        };
        assert_eq!(json!(output.exit_code), exit, "{name}: exit");
        for key in ["status", "summary", "evidence"] {
            assert_eq!(report[key], frozen[key], "{name}: {key}");
        }
        assert_eq!(
            pick(&report["targets"], &FIELDS),
            pick(&frozen["targets"], &FIELDS),
            "{name}: targets"
        );
        // The same processes in the same order with frozen's names, exits and errors. Native
        // argv is its own (absolute targets, `--no-cache`, `--`), so only the subcommand and
        // whether the formatter asked for JSON are compared.
        let evidence = &report["tool_evidence"];
        let keys = [
            "name",
            "returncode",
            "timed_out",
            "truncated",
            "error",
            "version",
        ];
        assert_eq!(
            pick(evidence, &keys),
            pick(&frozen["tool_evidence"], &keys),
            "{name}: tool evidence"
        );
        for (native, frozen) in evidence
            .as_array()
            .unwrap()
            .iter()
            .zip(frozen["tool_evidence"].as_array().unwrap())
        {
            assert_eq!(native["argv"][1], frozen["argv"][1], "{name}: subcommand");
            let json = |item: &Value| {
                item["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|arg| arg == "--output-format=json")
            };
            assert_eq!(json(native), json(frozen), "{name}: {}", native["name"]);
            assert_eq!(native["path"], native["argv"][0], "{name}: path");
        }
    }
}
