//! `ici verify` on a `[workspace]` root against frozen v0.13.0 `cli/cutover.py` captures
//! (`tests/rust-parity/reporting/verify-cutover-oracle.json`, `capture_verify_cutover.py`).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

const FROZEN_RESULT: &str = ".ici/next/result.json";

fn materialize(base: &Path, tree: &Value) {
    for (name, text) in tree.as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(base.join(".git")).unwrap();
}

fn schema(path: &Path) -> Value {
    let payload: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    ["schema_id", "schema_version", "version"]
        .iter()
        .find_map(|key| payload.get(*key).cloned())
        .unwrap_or(Value::Null)
}

#[test]
fn workspace_verify_runs_the_next_path_and_composes_outputs_like_frozen_cutover() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/verify-cutover-oracle.json"
    ))
    .unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 8);
    for (name, case) in cases {
        let home = tempfile::tempdir().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let base = directory.path().canonicalize().unwrap().join("project");
        materialize(&base, &case["tree"]);
        // A repository and token are present, so only the missing Actions marker skips publish.
        let environment = BTreeMap::from([
            ("HOME".to_owned(), home.path().display().to_string()),
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("GITHUB_REPOSITORY".to_owned(), "team/repo".to_owned()),
            ("GITHUB_TOKEN".to_owned(), "unused-test-token".to_owned()),
        ]);
        let step = &case["steps"][0];
        let argv: Vec<&str> = std::iter::once("ici")
            .chain(
                step["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|arg| arg.as_str().unwrap()),
            )
            .collect();
        let invocation = registry::parse(argv.iter().copied()).unwrap();
        let output = commands::dispatch(&invocation, &base, &environment).unwrap();
        if name == "legacy_publish_outside_actions" {
            // Stable path: frozen skips publishing outside GitHub Actions with this notice, still
            // writes the page, and the verify exit is the run's own (native INCOMPLETE rule).
            let notice = "[publish] --publish requires a GitHub Actions environment; skipping.";
            assert!(step["stdout"].as_str().unwrap().contains(notice));
            assert!(output.stdout.contains(notice), "{}", output.stdout);
            assert!(base.join("verify_report.html").is_file());
            assert!(!base.join(".ici/rust-v1/publish.json").exists());
            let plain = commands::dispatch(
                &registry::parse(["ici", "verify"]).unwrap(),
                &base,
                &environment,
            )
            .unwrap();
            assert_eq!(output.exit_code, plain.exit_code);
            continue;
        }
        let root = base.display().to_string();
        let stdout = output
            .stdout
            .replace(&root, "<root>")
            .replace(ici_cli::DEFAULT_RESULT, FROZEN_RESULT);
        let stderr = output.stderr.replace(&root, "<root>");
        assert_eq!(
            output.exit_code,
            step["exit_code"].as_i64().unwrap() as i32,
            "{name}: {stdout}{stderr}"
        );
        // Stable-only refusals and the dispatch notice are exact.
        // The one wording difference: frozen told users to gate on `ici next diff`, which exits 0
        // with new findings in both products (decisions.md "--fail-on-new 안내").
        let stderr = stderr.replace(
            ici_cli::cutover::FAIL_ON_NEW_HINT,
            ici_cli::cutover::FROZEN_FAIL_ON_NEW_HINT,
        );
        assert_eq!(stderr, step["stderr"].as_str().unwrap(), "{name}");
        let frozen: Vec<&str> = step["stdout"].as_str().unwrap().lines().collect();
        let native: Vec<&str> = stdout.lines().collect();
        let kept = |lines: &[&str], prefixes: &[&str]| -> Vec<String> {
            lines
                .iter()
                .filter(|line| prefixes.iter().any(|prefix| line.starts_with(prefix)))
                .map(|line| line.to_string())
                .collect()
        };
        let frame = [
            "root: ",
            "scope: ",
            "FAIL: ",
            "PASS: ",
            "INCOMPLETE: ",
            "  2 violation",
            "saved ",
            "wrote ",
        ];
        assert_eq!(kept(&native, &frame), kept(&frozen, &frame), "{name}");
        // Every frozen artifact exists natively with the same schema (frozen result → native
        // result namespace); the stable path's v3 suite is never written for a workspace.
        for (file, frozen_schema) in step["files"].as_object().unwrap() {
            let native_file = file.replace(FROZEN_RESULT, ici_cli::DEFAULT_RESULT);
            let path = base.join(&native_file);
            assert!(path.is_file(), "{name}: {native_file}");
            if !frozen_schema.is_null() {
                assert_eq!(&schema(&path), frozen_schema, "{name}: {native_file}");
            }
        }
        if output.exit_code == 2 {
            assert!(output.stdout.is_empty(), "{name}");
            assert!(!base.join(ici_cli::DEFAULT_RESULT).exists(), "{name}");
        }
    }
}

/// A legacy-schema root keeps the stable verify path (frozen: no `[workspace]`, no cutover).
#[test]
fn legacy_schema_verify_keeps_the_historical_suite() {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path();
    fs::write(base.join("main.py"), "def hello():\n    return 1\n").unwrap();
    fs::create_dir(base.join(".git")).unwrap();
    let environment = BTreeMap::from([("PATH".to_owned(), "/usr/bin:/bin".to_owned())]);
    let invocation = registry::parse(["ici", "verify", "--report"]).unwrap();
    let output = commands::dispatch(&invocation, base, &environment).unwrap();
    assert!(!output.stderr.contains("ici-next:"), "{}", output.stderr);
    assert_eq!(
        schema(&base.join("verify_report.json")),
        Value::from("ici.result/v3")
    );
}
