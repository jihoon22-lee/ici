//! Field-for-field parity of the frozen brace-heuristic C++ inventory, `_slice_body` and
//! `cpp_cognitive_metric` (captured by `tests/rust-parity/cpp/capture_cpp_heuristic_inventory.py`).
use ici_cpp::legacy_heuristic::{
    CognitiveMetric, cognitive_metric, function_inventory, slice_body,
};
use ici_cpp::py_splitlines;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

const BLOB_LIMIT: usize = 4096;

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/cpp-heuristic-inventory-oracle.json"
    ))
    .expect("heuristic inventory oracle")
}

fn expand(recipe: &Value) -> String {
    recipe
        .as_array()
        .expect("recipe")
        .iter()
        .map(|part| {
            part[0]
                .as_str()
                .expect("recipe text")
                .repeat(part[1].as_u64().expect("recipe count") as usize)
        })
        .collect()
}

fn blob(text: &str) -> Value {
    let length = text.chars().count();
    if length <= BLOB_LIMIT {
        return json!(text);
    }
    let digest = Sha256::digest(text.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    json!({"len": length, "sha256": hex})
}

fn metric_value(metric: &CognitiveMetric) -> Value {
    json!({
        "cognitive": metric.cognitive,
        "max_nesting": metric.max_nesting,
        "unbraced_controls": metric.unbraced_controls,
        "logical_sequences": metric.logical_sequences,
        "excluded_lambdas": metric.excluded_lambdas,
        "preprocessor_conditional": metric.preprocessor_conditional,
    })
}

fn cognitive_value(body: &str) -> Value {
    match cognitive_metric(body) {
        Ok(metric) => json!({"value": metric_value(&metric)}),
        Err(message) => json!({"error": format!("ValueError: {message}")}),
    }
}

fn check(failures: &mut Vec<String>, label: &str, frozen: &Value, rust: &Value) {
    if frozen != rust {
        failures.push(format!("{label}: frozen {frozen} vs rust {rust}"));
    }
}

fn case_lines(case: &Value, failures: &mut Vec<String>, name: &str) -> Vec<String> {
    if let Some(recipe) = case.get("recipe") {
        return py_splitlines(&expand(recipe));
    }
    let lines: Vec<String> = case["lines"]
        .as_array()
        .expect("lines")
        .iter()
        .map(|line| line.as_str().expect("line").to_string())
        .collect();
    if let Some(text) = case.get("text").and_then(Value::as_str) {
        check(
            failures,
            &format!("{name}/splitlines"),
            &json!(lines),
            &json!(py_splitlines(text)),
        );
    }
    lines
}

fn compare_case(case: &Value, failures: &mut Vec<String>) {
    let name = case["name"].as_str().expect("case name");
    let lines = case_lines(case, failures, name);
    let outcome = &case["outcome"];
    let (spans, metric_lines) = match function_inventory(&lines) {
        Err(message) => {
            check(
                failures,
                &format!("{name}/outcome"),
                outcome,
                &json!({"error": format!("ValueError: {message}")}),
            );
            return;
        }
        Ok(found) => found,
    };
    if outcome.get("error").is_some() {
        failures.push(format!("{name}/outcome: frozen {outcome} vs rust success"));
        return;
    }
    let metric_blobs: Vec<Value> = metric_lines.iter().map(|line| blob(line)).collect();
    check(
        failures,
        &format!("{name}/metric_lines"),
        &outcome["metric_lines"],
        &json!(metric_blobs),
    );
    let frozen_spans = outcome["spans"].as_array().expect("spans");
    if frozen_spans.len() != spans.len() {
        failures.push(format!(
            "{name}/span-count: frozen {} vs rust {} ({:?})",
            frozen_spans.len(),
            spans.len(),
            spans.iter().map(|span| &span.name).collect::<Vec<_>>()
        ));
    }
    for (index, (frozen, span)) in frozen_spans.iter().zip(&spans).enumerate() {
        let label = format!("{name}/span[{index}]");
        let mut rust = json!({
            "name": span.name,
            "start_line": span.start_line,
            "start_column": span.start_column,
            "body_start_line": span.body_start_line,
            "body_start_column": span.body_start_column,
            "end_line": span.end_line,
            "end_column": span.end_column,
            "complexity": span.complexity,
            "max_nesting": span.max_nesting,
            "excluded_lambdas": span.excluded_lambdas,
            "function_kind": span.function_kind,
            "is_template": span.is_template,
            "preprocessor_conditional": span.preprocessor_conditional,
        });
        if let Some(end_column) = span.end_column {
            match slice_body(
                &metric_lines,
                span.body_start_line,
                span.body_start_column,
                span.end_line,
                end_column,
            ) {
                Some(body) => {
                    rust["body"] = blob(&body);
                    rust["cognitive"] = cognitive_value(&body);
                }
                None => rust["body"] = json!("IndexError"),
            }
        }
        let fields: BTreeSet<&String> = frozen
            .as_object()
            .expect("span object")
            .keys()
            .chain(rust.as_object().expect("span object").keys())
            .collect();
        for field in fields {
            check(
                failures,
                &format!("{label}/{field}"),
                &frozen[field],
                &rust[field],
            );
        }
    }
}

#[test]
fn brace_heuristic_inventory_slices_and_cognitive_metrics_match_frozen() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_array().expect("cases");
    let bodies = oracle["bodies"].as_array().expect("bodies");
    assert_eq!(oracle["summary"]["cases"], json!(cases.len()));
    assert!(cases.len() > 400, "corpus shrank to {}", cases.len());
    let mut failures = vec![];
    let mut compared_spans = 0;
    for case in cases {
        compare_case(case, &mut failures);
        compared_spans += case["outcome"]
            .get("spans")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
    }
    assert_eq!(oracle["summary"]["spans"], json!(compared_spans));
    for body in bodies {
        let name = body["name"].as_str().expect("body name");
        let text = match body.get("recipe") {
            Some(recipe) => expand(recipe),
            None => body["body"].as_str().expect("body").to_string(),
        };
        check(
            &mut failures,
            &format!("body:{name}"),
            &body["result"],
            &cognitive_value(&text),
        );
    }
    assert!(
        failures.is_empty(),
        "{} mismatches:\n{}",
        failures.len(),
        failures.join("\n")
    );
}
