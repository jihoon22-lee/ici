//! `ici analyze --json` C++ complexity/cognitive checks against frozen v0.13.0
//! (`tests/rust-parity/cpp/heuristic-metrics-oracle.json`, `capture_heuristic_metrics.py`): frozen
//! next scans every planned C++ file with the brace heuristic (`languages/metrics.py::_scan_cpp`
//! and `measure`). Each case runs the real CLI without any `ici.toml` (one automatic C++
//! component) and PATH=/usr/bin:/bin, and compares the exit code, both checks, every `metrics.*`
//! finding (message, severity, confidence, evidence, location, fingerprint), the
//! `functions_measured` / `max_*` metrics in run order and the scanner limitations (each distinct
//! one once, decisions.md "next 실행의 run limitation 중복"). No tool runs for these checks.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const ORACLE: &str = include_str!("../../../tests/rust-parity/cpp/heuristic-metrics-oracle.json");
const OK: &str = "int ok(int x) {\n  if (x) { return 1; }\n  return 0;\n}\n";

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

/// The capture's tree: a committed fixture or the generated one, without `ici.toml`.
fn materialize(name: &str, project: &Path) {
    let generated: Option<Vec<(&str, Vec<u8>)>> = match name {
        "lambda-limit" => Some(vec![(
            "src/lambda.cpp",
            format!("void f() {{\n{}\n}}\n", "[]{};".repeat(100_001)).into_bytes(),
        )]),
        "too-large" => Some(vec![(
            "src/big.cpp",
            format!("// {}\n", "x".repeat(8 * 1024 * 1024)).into_bytes(),
        )]),
        "nul" => Some(vec![(
            "src/nul.cpp",
            b"int nul(int a) {\n  char c = '\0';\n  if (a) { return c; }\n  return 0;\n}\n"
                .to_vec(),
        )]),
        _ => None,
    };
    if let Some(files) = generated {
        for (path, bytes) in files
            .into_iter()
            .chain([("src/ok.cpp", OK.as_bytes().to_vec())])
        {
            fs::create_dir_all(project.join(path).parent().unwrap()).unwrap();
            fs::write(project.join(path), bytes).unwrap();
        }
        return;
    }
    let fixture = match name {
        "zoo" | "errors" => format!("heuristic-metrics/{name}"),
        _ => format!("metric-sources/{name}"),
    };
    copy(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .unwrap()
            .join("parity-fixtures/cpp")
            .join(fixture),
        project,
    );
    let _ = fs::remove_file(project.join("ici.toml"));
}

fn analyze(name: &str) -> Value {
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().canonicalize().unwrap().join("project");
    materialize(name, &base);
    // `src/plain.c` stays: automatic next scope selects no `.c` file, like frozen (decisions.md
    // "next 경로의 source 확장자").
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.path().display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
        ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
    ]);
    let invocation = registry::parse(["ici", "analyze", "--json"]).unwrap();
    let output = commands::dispatch(&invocation, &base, &environment).unwrap();
    let document: Value = serde_json::from_str(output.stdout.trim_end()).unwrap();
    let keep = |check: &Value| {
        let mut check = check.clone();
        // The scope digest is the native digest namespace (decisions.md identity row).
        check.as_object_mut().unwrap().remove("scope_digest");
        check
    };
    json!({
        "exit_code": output.exit_code,
        "checks": document["checks"].as_array().unwrap().iter()
            .filter(|check| matches!(check["check_id"].as_str(), Some("cpp.complexity" | "cpp.cognitive")))
            .map(keep)
            .collect::<Vec<_>>(),
        "findings": document["findings"].as_array().unwrap().iter()
            .filter(|finding| finding["rule_id"].as_str().unwrap().starts_with("metrics."))
            .cloned()
            .collect::<Vec<_>>(),
        "metrics": document["metrics"].as_array().unwrap().iter()
            .filter(|metric| matches!(metric["name"].as_str(),
                Some("functions_measured" | "max_complexity" | "max_cognitive")))
            .cloned()
            .collect::<Vec<_>>(),
        "limitations": document["limitations"].as_array().unwrap().iter()
            .filter(|item| {
                let item = item.as_str().unwrap();
                item.contains("function boundaries") || item.ends_with("Error")
            })
            .cloned()
            .collect::<Vec<_>>(),
    })
}

fn compare(name: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let mut expected = oracle["cases"][format!("analyze:{name}")].clone();
    for check in expected["checks"].as_array_mut().unwrap() {
        check.as_object_mut().unwrap().remove("scope_digest");
    }
    // Approved (decisions.md "next 실행의 run limitation 중복"): the native run lists each
    // distinct limitation once, in first-seen order; frozen repeats it per check.
    let mut seen = vec![];
    for item in expected["limitations"].as_array().unwrap() {
        if !seen.contains(item) {
            seen.push(item.clone());
        }
    }
    expected["limitations"] = Value::Array(seen);
    assert_eq!(analyze(name), expected, "{name}");
}

/// Operators, constructors, lambdas, templates, preprocessor conditionals, a macro-generated
/// function and both thresholds: the frozen rows, findings and maxima.
#[test]
fn analyze_cpp_metrics_scan_functions_with_the_frozen_brace_heuristic() {
    compare("zoo");
}

/// Bodies the cognitive parser rejects keep their rows with cognitive 0; an unterminated body
/// is not a row; a NUL inside a body is ordinary text.
#[test]
fn analyze_cpp_metrics_keep_rejected_and_skip_unterminated_bodies_like_frozen() {
    for name in ["errors", "unterminated", "nul"] {
        compare(name);
    }
}

/// A non-UTF-8 source is read with replacement characters and an 8 MiB source is scanned.
#[test]
fn analyze_cpp_metrics_read_every_planned_file_lossily_like_frozen() {
    for name in ["unreadable", "too-large"] {
        compare(name);
    }
}

/// The bounded lambda-body limit is the file's `ValueError` limitation and a FAILED check.
#[test]
fn analyze_cpp_metrics_fail_a_file_over_the_lambda_bound_like_frozen() {
    compare("lambda-limit");
}
