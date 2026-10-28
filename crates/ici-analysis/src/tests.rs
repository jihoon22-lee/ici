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
fn dead_code_matches_frozen_targets_and_reviewed_corrections() {
    let fixture: Value =
        serde_json::from_str(include_str!("../../../tests/rust-parity/python/dead.json")).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let mut input = request("dead", "");
        input.inputs = case["sources"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(path, text)| SourceInput {
                path: path.clone(),
                bytes: text.as_str().unwrap().as_bytes().to_vec(),
            })
            .collect();
        input.options = json!({"source_roots":case["source_roots"]});
        let output = analyze(&input);
        if !case["original_errors"].as_array().unwrap().is_empty() {
            assert_eq!(
                output.observation.state,
                TaskState::Failed,
                "{}",
                case["id"]
            );
            assert_eq!(output.status, "ERROR");
            assert!(output.targets.iter().any(|target| target.status == "ERROR"));
            assert!(output.observation.findings.is_empty());
            continue;
        }
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}: {:?}",
            case["id"],
            output.observation.limitations
        );
        let mut actual: Vec<Value> = output
            .targets
            .iter()
            .map(|target| {
                json!({
                    "file_path":target.file_path,"start_line":target.start_line,
                    "end_line":target.end_line,"start_column":target.start_column,
                    "end_column":target.end_column,"target_name":target.target_name,
                    "status":target.status,"message":target.message,"snippet":target.snippet,
                })
            })
            .collect();
        let mut expected = case
            .get("expected_targets")
            .unwrap_or(&case["original_targets"])
            .as_array()
            .unwrap()
            .clone();
        let key = |value: &Value| {
            (
                value["file_path"].as_str().unwrap().to_owned(),
                value["start_line"].as_u64().unwrap(),
                value["target_name"].as_str().unwrap().to_owned(),
            )
        };
        actual.sort_by_key(key);
        expected.sort_by_key(key);
        assert_eq!(actual, expected, "{}", case["id"]);
        let warnings = output
            .targets
            .iter()
            .filter(|target| target.status == "WARN")
            .count();
        assert_eq!(
            output.observation.findings.len(),
            warnings,
            "{}",
            case["id"]
        );
        for finding in &output.observation.findings {
            assert_eq!(finding.evidence, ici_domain::EvidenceLevel::Estimated);
            assert_eq!(finding.severity, "medium");
            assert_eq!(finding.category, "maintainability");
        }
    }
}

#[test]
fn explicit_annotation_projection_preserves_optin_and_nested_definitions() {
    let fixtures: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/annotations.json"
    ))
    .unwrap();
    for case in fixtures["cases"].as_array().unwrap() {
        let mut input = request("type", case["source"].as_str().unwrap());
        input.options = json!({"legacy_projection":true,"warn_on_missing_annotation":case["warn_on_missing_annotation"]});
        let output = analyze(&input);
        if case["incomplete"] == true {
            assert_eq!(output.observation.state, TaskState::Failed);
            assert_eq!(output.status, "ERROR");
            continue;
        }
        let mut actual: Vec<_> = output.targets.iter().filter(|target| target.status=="WARN").map(|target| json!({"line":target.start_line,"name":target.target_name,"status":target.status,"message":target.message})).collect();
        let mut expected = case["original_targets"].as_array().unwrap().clone();
        actual.sort_by_key(|row| row["line"].as_u64());
        expected.sort_by_key(|row| row["line"].as_u64());
        assert_eq!(actual, expected, "{}", case["id"]);
        assert!(
            output
                .targets
                .iter()
                .any(|target| target.target_name == "AnnotationCoverage")
        );
        assert_eq!(output.observation.findings.len(), actual.len());
    }
    let output = analyze(&request("type", "def f():\n    pass\n"));
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output.targets[0]
            .message
            .contains("explicit legacy_projection")
    );
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
        Some(16)
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
            if let ruff_python_ast::Expr::Call(call) = e
                && matches!(call.func.as_ref(), ruff_python_ast::Expr::Attribute(_))
            {
                self.names.push(self.graph.resolve(&call.func));
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
    assert_eq!(source.lines().count(), 3);
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

#[test]
fn inclusive_endpoints_cover_unicode_multiline_and_zero_width() {
    use ruff_text_size::{TextRange, TextSize};
    let source = SourceDocument::decode(&SourceInput {
        path: "main.py".into(),
        bytes: "가나\néx".as_bytes().to_vec(),
    })
    .unwrap();
    let span = source.span(TextRange::new(TextSize::new(0), TextSize::new(10)));
    assert_eq!(
        (
            span.start_line,
            span.start_column,
            span.end_line,
            span.end_column
        ),
        (1, Some(1), Some(2), Some(2))
    );
    let letter = source.span(TextRange::new(TextSize::new(7), TextSize::new(9)));
    assert_eq!(
        (
            letter.start_line,
            letter.start_column,
            letter.end_line,
            letter.end_column
        ),
        (2, Some(1), Some(2), Some(1))
    );
    let point = source.span(TextRange::new(TextSize::new(7), TextSize::new(7)));
    assert_eq!(point, letter);
}

#[test]
fn hygiene_matches_fixed_oracle_and_explicit_corrected_expectations() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/hygiene.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let output = analyze(&request(
            case["kind"].as_str().unwrap(),
            case["source"].as_str().unwrap(),
        ));
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}:{} {:?}",
            case["kind"],
            case["id"],
            output.observation.limitations
        );
        let targets:Vec<Value>=output.targets.iter().filter(|target|matches!(target.status.as_str(),"WARN"|"FAIL")).map(|target|json!({"name":target.target_name,"line":target.start_line,"end_line":target.end_line,"message":target.message,"snippet":target.snippet,"metrics":target.metrics,"status":target.status})).collect();
        let expected = case
            .get("expected_targets")
            .unwrap_or(&case["oracle_targets"]);
        assert_eq!(
            json!(targets),
            *expected,
            "{}:{} targets",
            case["kind"],
            case["id"]
        );
        let counters = case
            .get("expected_counters")
            .unwrap_or(&case["oracle_counters"]);
        for (key, value) in counters.as_object().unwrap() {
            assert_eq!(
                output.extra[key], *value,
                "{}:{}:{key}",
                case["kind"], case["id"]
            );
        }
        assert!(
            output.targets.iter().any(|target| target.status == "PASS"),
            "all checked files retain a target"
        );
    }
}
#[test]
fn secrets_are_redacted_and_allowlists_and_nosec_are_structural() {
    let source = "password=\"sk-fixture-sensitive-value-98765\"; eval(payload)\nsettings={\"auth_token\":\"ghp_fixture_sensitive_123456\"}\n";
    let mut input = request("security", source);
    let output = analyze(&input);
    assert_eq!(output.observation.findings.len(), 3);
    let serialized = serde_json::to_string(&output).unwrap();
    assert!(!serialized.contains("sk-fixture-sensitive-value-98765"));
    assert!(!serialized.contains("ghp_fixture_sensitive_123456"));
    input.options = json!({"secret_name_allowlist":["password","AUTH_TOKEN"]});
    assert_eq!(analyze(&input).observation.findings.len(), 1);
    let input = request("security", "암호 = \"x\"; password=\"abcdefghijk\"\n");
    let output = analyze(&input);
    let target = output
        .targets
        .iter()
        .find(|target| target.target_name == "Security:HardcodedSecret")
        .unwrap();
    assert_eq!(target.start_column, Some(11));
    assert_eq!(target.end_column, Some(32));
}

#[test]
fn excluded_vendor_and_generated_inputs_remain_visible_and_not_measured() {
    let mut input = request("security", "x=1\n");
    input.inputs.extend([
        SourceInput {
            path: "vendor/a.py".into(),
            bytes: b"eval(data)\n".to_vec(),
        },
        SourceInput {
            path: "generated/b.py".into(),
            bytes: b"eval(data)\n".to_vec(),
        },
        SourceInput {
            path: "vendor/generated/c.py".into(),
            bytes: b"eval(data)\n".to_vec(),
        },
    ]);
    let output = analyze(&input);
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.extra["files_checked"], json!(1));
    assert_eq!(
        output.extra["excluded_source_counts"],
        json!({"generated":2,"vendor":2})
    );
    assert_eq!(
        output
            .targets
            .iter()
            .filter(|target| target.status == "SKIP")
            .count(),
        3
    );
    input.options = json!({"include_vendor":true,"include_generated":true});
    assert_eq!(analyze(&input).observation.findings.len(), 3);
}

#[test]
fn exception_aliases_and_handler_targets_match_fixed_source() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/exception.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let output = analyze(&request("exception", case["source"].as_str().unwrap()));
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{} {:?}",
            case["id"],
            output.observation.limitations
        );
        let targets:Vec<Value>=output.targets.iter().map(|target|json!({"name":target.target_name,"line":target.start_line,"end_line":target.end_line,"message":target.message,"status":target.status})).collect();
        assert_eq!(json!(targets), case["targets"], "{}", case["id"]);
    }
}

#[test]
fn cancellation_is_explicit_before_intake_and_does_not_populate_cache() {
    use std::sync::atomic::AtomicBool;
    let cancellation = AtomicBool::new(true);
    let mut session = AnalysisSession::new();
    let output = session.analyze_cancellable(
        &request("complexity", "def f():\n return 1\n"),
        &cancellation,
    );
    assert_eq!(output.observation.state, TaskState::Cancelled);
    assert_eq!(session.parser_invocations(), 0);
    assert!(output.targets.is_empty());
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|message| message == "analysis cancelled")
    );
}
#[test]
fn frozen_source_limit_accepts_sixteen_mib_and_rejects_larger_input() {
    let mut input = SourceInput {
        path: "large.py".into(),
        bytes: vec![b' '; 16 * 1024 * 1024],
    };
    input.bytes[0] = b'#';
    let source = SourceDocument::decode(&input).unwrap();
    assert_eq!(source.text.len(), 16 * 1024 * 1024);
    assert_eq!(source.lines().count(), 1);
    input.bytes.push(b' ');
    assert_eq!(
        SourceDocument::decode(&input).unwrap_err().code,
        "source-limit"
    );
}

fn project_request(check: &str, files: &[(&str, &str)], options: Value) -> AnalysisRequest {
    let mut request = request(check, "");
    request.inputs = files
        .iter()
        .map(|(path, text)| SourceInput {
            path: (*path).into(),
            bytes: text.as_bytes().to_vec(),
        })
        .collect();
    request.options = options;
    request
}
#[test]
fn cycle_preserves_exact_graph_and_native_fingerprint() {
    let input = project_request(
        "cycle",
        &[
            ("src/a.py", "from b import y\n"),
            ("src/b.py", "from a import x\ny=2\n"),
        ],
        json!({"source_roots":["src"]}),
    );
    let output = analyze(&input);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.extra["py_cycles"], json!(1));
    assert_eq!(
        output.observation.findings[0].fingerprint,
        finding::sha1_short("cycle-", "python:b:a")
    );
    assert_eq!(
        output.observation.findings[0].message,
        "import cycle: a -> b -> a"
    );
    assert_eq!(
        output.observation.findings[0].evidence,
        ici_domain::EvidenceLevel::Measured
    );
    let target = output
        .targets
        .iter()
        .find(|target| target.target_name == "Cycle:2")
        .unwrap();
    assert_eq!(target.file_path, "src/a.py");
    assert_eq!(target.metrics["modules"], json!(["a", "b"]));
}
#[test]
fn package_relative_imports_and_source_root_boundaries_are_preserved() {
    let input = project_request(
        "cycle",
        &[
            ("src/pkg/__init__.py", "from . import a\n"),
            ("src/pkg/a.py", "from .b import value\n"),
            ("src/pkg/b.py", "from .a import value\n"),
        ],
        json!({"source_roots":["src"]}),
    );
    let output = analyze(&input);
    assert_eq!(output.extra["py_cycles"], json!(1));
    let edges = output.extra["import_edges"].as_array().unwrap();
    assert!(
        edges
            .iter()
            .any(|edge| edge["from"] == "pkg" && edge["to"] == "pkg.a")
    );
    assert!(
        edges
            .iter()
            .any(|edge| edge["from"] == "pkg.a" && edge["to"] == "pkg.b")
    );
    assert!(
        edges
            .iter()
            .any(|edge| edge["from"] == "pkg.b" && edge["to"] == "pkg.a")
    );
    let input = project_request(
        "cycle",
        &[
            ("pkg/__init__.py", "from . import a\n"),
            ("pkg/a.py", "from . import b\n"),
            ("pkg/b.py", "from . import a\n"),
        ],
        json!({"component_root":"pkg"}),
    );
    let output = analyze(&input);
    assert_eq!(output.extra["py_cycles"], json!(1));
    assert_eq!(
        output.extra["module_index"]["__init__"],
        json!("pkg/__init__.py")
    );
}
#[test]
fn stdlib_collision_and_package_attributes_do_not_invent_edges() {
    let input = project_request(
        "cycle",
        &[
            ("src/pkg/html.py", "import pkg.other\n"),
            ("src/pkg/other.py", "import html\n"),
            ("src/pkg/__init__.py", "child=1\n"),
            ("src/pkg/child.py", "import pkg.user\n"),
            ("src/pkg/user.py", "from pkg import child\n"),
        ],
        json!({"source_roots":["src"]}),
    );
    let output = analyze(&input);
    assert_eq!(output.extra["py_cycles"], json!(0));
    assert!(
        !output.extra["import_edges"]
            .as_array()
            .unwrap()
            .iter()
            .any(|edge| edge["from"] == "pkg.user" && edge["to"] == "pkg.child")
    );
}
#[test]
fn ambiguous_suffix_and_invalid_sources_are_incomplete_graphs() {
    let input = project_request(
        "cycle",
        &[
            ("one/b.py", "import a\n"),
            ("two/b.py", "import a\n"),
            ("a.py", "import b\n"),
        ],
        json!({}),
    );
    let output = analyze(&input);
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|text| text.contains("ambiguous-import"))
    );
    let output = analyze(&request("cycle", "def broken(:\n"));
    assert_eq!(output.observation.state, TaskState::Failed);
}
#[test]
fn iterative_scc_handles_deep_graph_and_real_cycle_edges() {
    use std::collections::BTreeSet;
    let mut graph: Vec<_> = (0..5000).map(|index| BTreeSet::from([index + 1])).collect();
    graph.push(BTreeSet::from([0]));
    let components = cycle::components(&graph);
    assert_eq!(components.len(), 1);
    assert_eq!(components[0].len(), 5001);
}
#[test]
fn fixed_python_import_graphs_and_explicit_corrected_expectations_match() {
    use std::collections::BTreeSet;
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../../tests/rust-parity/python/cycle.json")).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let sources: Vec<_> = case["sources"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(path, source)| (path.as_str(), source.as_str().unwrap()))
            .collect();
        let input = project_request(
            "cycle",
            &sources,
            json!({"source_roots":case["source_roots"]}),
        );
        let output = analyze(&input);
        assert_eq!(
            output.observation.state,
            TaskState::Succeeded,
            "{}: {:?}",
            case["id"],
            output.observation.limitations
        );
        let edges: BTreeSet<_> = output.extra["import_edges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|edge| {
                vec![
                    edge["from"].as_str().unwrap().to_owned(),
                    edge["to"].as_str().unwrap().to_owned(),
                ]
            })
            .collect();
        let expected = case
            .get("corrected_edges")
            .unwrap_or(&case["original_edges"]);
        assert_eq!(
            serde_json::to_value(edges).unwrap(),
            *expected,
            "{}",
            case["id"]
        );
        let cycles = case
            .get("corrected_cycles")
            .unwrap_or(&case["original_cycles"])
            .as_array()
            .unwrap()
            .len();
        assert_eq!(output.extra["py_cycles"], json!(cycles), "{}", case["id"]);
        if case["id"] == "source-precedence" {
            assert_eq!(output.extra["module_index"]["a"], json!("first/a.py"));
        }
    }
}
#[test]
fn relative_import_leaving_declared_package_is_incomplete_instead_of_guessed() {
    let input = project_request(
        "cycle",
        &[
            ("pkg/a.py", "from ..b import x\n"),
            ("b.py", "import pkg.a\n"),
        ],
        json!({"source_roots":["."]}),
    );
    let output = analyze(&input);
    assert_eq!(output.observation.state, TaskState::Failed);
    assert!(
        output
            .observation
            .limitations
            .iter()
            .any(|message| message.contains("relative-import-context"))
    );
    assert_eq!(output.extra["py_cycles"], json!(0));
}
