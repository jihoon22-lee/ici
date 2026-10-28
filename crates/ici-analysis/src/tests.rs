use super::*;
use ici_domain::SourceInput;
use serde_json::{Value, json};

fn request(check: &str, text: &str) -> AnalysisRequest {
    AnalysisRequest {
        task_id: format!("task:{check}"),
        check_id: format!("python.{check}"),
        component_id: "project".into(),
        language: "python".into(),
        inputs: vec![SourceInput {
            path: "main.py".into(),
            bytes: text.as_bytes().to_vec(),
        }],
        options: json!({}),
        target_version: "3.14".into(),
    }
}
#[test]
fn metrics_match_fixed_python_formulas() {
    let fixtures: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/metrics.json"
    ))
    .unwrap();
    for case in fixtures["cases"].as_array().unwrap() {
        let mut session = AnalysisSession::new();
        for kind in ["complexity", "cognitive"] {
            let output = session.analyze(&request(kind, case["source"].as_str().unwrap()));
            assert_eq!(
                output.observation.state,
                TaskState::Succeeded,
                "{}: {:?}",
                case["id"],
                output.observation.limitations
            );
            assert_eq!(
                output
                    .targets
                    .iter()
                    .filter(|target| target.metrics.contains_key(kind))
                    .count(),
                case["functions"].as_array().unwrap().len(),
                "{} function inventory",
                case["id"]
            );
            for expected in case["functions"].as_array().unwrap() {
                let name = expected["name"].as_str().unwrap();
                let label = if kind == "complexity" {
                    format!("{name}()")
                } else {
                    name.to_owned()
                };
                let target = output
                    .targets
                    .iter()
                    .find(|target| target.target_name == label)
                    .unwrap();
                assert_eq!(
                    target.metrics[kind], expected[kind],
                    "{}:{label}:{kind}",
                    case["id"]
                );
                assert_eq!(
                    target.metrics["nesting"],
                    expected[if kind == "complexity" {
                        "nesting"
                    } else {
                        "cognitive_nesting"
                    }],
                    "{}:{label}:nesting",
                    case["id"]
                );
                assert_eq!(
                    target.start_line,
                    expected["line"].as_u64().unwrap() as u32,
                    "{}:{label}:start",
                    case["id"]
                );
                assert_eq!(
                    target.end_line,
                    Some(expected["end_line"].as_u64().unwrap() as u32)
                );
            }
        }
        assert_eq!(
            session.parser_invocations(),
            1,
            "parse shared across two metrics"
        );
    }
}
#[test]
fn unicode_and_legacy_threshold_boundaries_are_preserved() {
    let mut input = request(
        "cognitive",
        "def 이름(값):\n    if 값:\n        return 값\n",
    );
    input.options = json!({"warn":1,"fail":2,"warn_nesting":9});
    let output = analyze(&input);
    assert_eq!(output.targets[0].status, "WARN");
    assert!(
        output.observation.findings.is_empty(),
        "modern metric uses strict > threshold"
    );
    assert_eq!(output.targets[0].start_column, Some(1));
    input.options["warn"] = json!(0);
    let output = analyze(&input);
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(
        output.observation.findings[0].primary_location.end_column,
        Some(17)
    );
}
#[test]
fn syntax_semantics_and_bounds_cannot_be_clean() {
    for source in [
        "return 1\n",
        "nonlocal missing\n",
        "break\n",
        "await work()\n",
        "def bad(:\n pass\n",
        "def f(a,a):\n pass\n",
    ] {
        let output = analyze(&request("complexity", source));
        assert_eq!(output.observation.state, TaskState::Failed, "{source}");
        assert_eq!(output.status, "ERROR");
        assert!(!output.targets.is_empty());
    }
    let mut input = request("complexity", "type Alias[T] = list[T]\n");
    input.target_version = "3.10".into();
    assert_eq!(analyze(&input).observation.state, TaskState::Failed);
    input = request(
        "complexity",
        &format!("x = {}1{}\n", "(".repeat(201), ")".repeat(201)),
    );
    assert_eq!(analyze(&input).observation.state, TaskState::Failed);
}
#[test]
fn scope_aliases_respect_shadowing_and_comprehension_boundaries() {
    let input = request(
        "complexity",
        "import os\ndef outer(os):\n    os.system('x')\n    return [os.system('x') for os in values]\ndef plain():\n    return os.system('x')\n",
    );
    let file =
        ParsedFile::parse(SourceDocument::decode(&input.inputs[0]).unwrap(), "3.14").unwrap();
    struct Calls<'a> {
        graph: &'a scope::ScopeGraph,
        names: Vec<Option<String>>,
    }
    impl<'a> ruff_python_ast::visitor::Visitor<'a> for Calls<'_> {
        fn visit_expr(&mut self, e: &'a ruff_python_ast::Expr) {
            if let ruff_python_ast::Expr::Call(call) = e {
                if matches!(call.func.as_ref(), ruff_python_ast::Expr::Attribute(_)) {
                    self.names.push(self.graph.resolve(&call.func));
                }
            }
            ruff_python_ast::visitor::walk_expr(self, e);
        }
    }
    use ruff_python_ast::visitor::Visitor;
    let mut calls = Calls {
        graph: &file.scopes,
        names: vec![],
    };
    calls.visit_body(file.body());
    assert_eq!(calls.names, vec![None, None, Some("os.system".into())]);
}
#[test]
fn decoder_maps_original_bytes_and_universal_newlines() {
    let input = SourceInput {
        path: "main.py".into(),
        bytes: b"\xef\xbb\xbf# coding: utf-8\r\nx=1\rprint(x)\r".to_vec(),
    };
    let source = SourceDocument::decode(&input).unwrap();
    assert_eq!(source.location(source.text.find("print").unwrap()), (3, 1));
    assert_eq!(source.raw_offset(0), Some(3));
    let input = SourceInput {
        path: "main.py".into(),
        bytes: b"#!/usr/bin/python\r\n# coding: latin-1\r\nname='caf\xe9'\r\n".to_vec(),
    };
    let source = SourceDocument::decode(&input).unwrap();
    let start = source.text.find('é').unwrap();
    assert_eq!(
        source.raw_offset(start + 2),
        source.raw_offset(start).map(|value| value + 1)
    );
    assert_eq!(source.lines().len(), 3);
}
#[test]
fn line_counts_and_explicit_legacy_scopes_keep_all_inspected_targets() {
    let mut input = request("line", "# comment\r\nx=1\r\n\r\n");
    input.inputs.push(SourceInput {
        path: "other.py".into(),
        bytes: b"x=2\n".to_vec(),
    });
    input.options = json!({"legacy_projection":true,"source_paths":["main.py"],"gated_paths":[],"warn_limit":0,"fail_limit":null});
    let output = analyze(&input);
    assert_eq!(output.targets.len(), 2);
    assert_eq!(output.extra["code"], json!(1));
    assert_eq!(output.extra["all"]["code"], json!(2));
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.extra["comment"], json!(1));
    assert_eq!(output.extra["blank"], json!(1));
}
