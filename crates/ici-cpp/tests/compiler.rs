use ici_cpp::{
    CompilerConfigurationObservation, ContextRequest, ReplayPurpose, compiler_probe_task,
    load_compilation_context, parse_clang_function_boundaries, parse_include_trace,
    unused_function_consensus,
};
use ici_domain::{AnalysisRequest, Finding, SourceInput, SourceSpan, TaskState};
use ici_execution::{Cancellation, Runner};
use regex::Regex;
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
};
fn request(input: SourceInput) -> AnalysisRequest {
    AnalysisRequest {
        task_id: "dead".into(),
        check_id: "cpp.dead".into(),
        component_id: "fixture".into(),
        language: "cpp".into(),
        inputs: vec![input],
        options: json!({}),
        target_version: "c++20".into(),
    }
}
#[test]
fn actual_clang21_ast_golden_excludes_macro_and_lambda_bodies_and_deduplicates_templates() {
    let source = SourceInput {
        path: "main.cpp".into(),
        bytes: include_bytes!("../../../parity-fixtures/cpp/clang-boundaries.cpp").to_vec(),
    };
    let transcript = include_str!("../../../tests/rust-parity/cpp/clang-boundaries.json");
    let evidence = parse_clang_function_boundaries(
        transcript,
        &source,
        Path::new("/fixture/main.cpp"),
        "debug",
    )
    .unwrap();
    let names: Vec<_> = evidence
        .boundaries
        .iter()
        .map(|boundary| boundary.name.as_str())
        .collect();
    assert_eq!(names, ["operator()", "~X", "ordinary", "add", "use"]);
    assert_eq!(evidence.macro_definitions_excluded, 1);
    assert_eq!(evidence.lambda_definitions_excluded, 1);
    assert!(
        evidence
            .boundaries
            .iter()
            .find(|boundary| boundary.name == "add")
            .unwrap()
            .is_template
    );
    assert!(
        parse_clang_function_boundaries("{}", &source, Path::new("/fixture/main.cpp"), "debug")
            .is_err()
    );
    let mut changed = source.clone();
    changed.bytes = b"int other(){}".to_vec();
    let changed = parse_clang_function_boundaries(
        transcript,
        &changed,
        Path::new("/fixture/main.cpp"),
        "debug",
    )
    .unwrap();
    assert!(changed.boundaries.is_empty());
}
fn context(root: &Path) -> ici_cpp::CompilationContext {
    fs::write(root.join("compile_commands.json"),serde_json::to_vec(&json!([{"directory":root,"file":"main.cpp","arguments":["g++","-std=c++20","-DKEEP=0","-c","main.cpp","-o","must-not-create-0.o"]},{"directory":root,"file":"main.cpp","arguments":["g++","-std=c++20","-DKEEP=1","-c","main.cpp","-o","must-not-create-1.o"]}])).unwrap()).unwrap();
    load_compilation_context(
        root,
        &ContextRequest {
            selected_sources: vec!["main.cpp".into()],
            ..Default::default()
        },
    )
}
#[test]
fn actual_gcc_unused_consensus_requires_every_variant_and_never_reports_a_function_used_elsewhere()
{
    let root = tempfile::tempdir().unwrap();
    let source=b"static int common_unused(){return 1;}\nstatic int variant_used(){return 2;}\nint main(){\n#if KEEP\nreturn variant_used();\n#else\nreturn 0;\n#endif\n}\n";
    fs::write(root.path().join("main.cpp"), source).unwrap();
    let context = context(root.path());
    assert!(context.complete());
    let request = request(SourceInput {
        path: "main.cpp".into(),
        bytes: source.to_vec(),
    });
    let pattern =
        Regex::new(r"^.+:([0-9]+):([0-9]+): warning: (.+) \[-Wunused-function\]$").unwrap();
    let mut observations = vec![];
    for unit in &context.units {
        let mut task = compiler_probe_task(
            root.path(),
            unit,
            Path::new("/usr/bin/g++"),
            ReplayPurpose::UnusedFunctions,
            &unit.configuration,
        )
        .unwrap();
        task.argv.push("-Wunused-function".into());
        task.argv.push("-fdiagnostics-show-option".into());
        let outcome = Runner::default().run(&task, &Cancellation::default());
        assert!(
            outcome.prerequisite_succeeded(),
            "{}",
            String::from_utf8_lossy(&outcome.stderr)
        );
        let findings = String::from_utf8_lossy(&outcome.stderr)
            .lines()
            .filter_map(|line| pattern.captures(line))
            .map(|capture| {
                let mut span = SourceSpan::line("main.cpp", capture[1].parse().unwrap());
                span.start_column = Some(capture[2].parse().unwrap());
                let mut finding =
                    Finding::new("compiler.unused-function", &capture[3], span, "compiler");
                finding.native_rule_id = "-Wunused-function".into();
                finding
            })
            .collect();
        observations.push(CompilerConfigurationObservation {
            source: "main.cpp".into(),
            configuration: unit.configuration.clone(),
            complete: true,
            findings,
        });
    }
    assert_eq!(
        observations[0].findings.len() + observations[1].findings.len(),
        3
    );
    let output = unused_function_consensus(&request, &context, &observations);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(
        output.observation.findings[0].primary_location.start_line,
        1
    );
    observations.pop();
    assert_eq!(
        unused_function_consensus(&request, &context, &observations)
            .observation
            .state,
        TaskState::Failed
    );
    assert!(!root.path().join("must-not-create-0.o").exists());
    assert!(!root.path().join("must-not-create-1.o").exists());
}

#[test]
fn actual_gcc_unused_policy_requires_both_generated_and_vendor_opt_ins() {
    let root = tempfile::tempdir().unwrap();
    let path = "Vendor/Generated/item.cpp";
    fs::create_dir_all(root.path().join("Vendor/Generated")).unwrap();
    let bytes = b"static int unused(){return 1;}\nint main(){return 0;}\n";
    fs::write(root.path().join(path), bytes).unwrap();
    fs::write(root.path().join("compile_commands.json"),serde_json::to_vec(&json!([{"directory":root.path(),"file":path,"arguments":["g++","-c",path,"-o","never.o"]}])).unwrap()).unwrap();
    let context = load_compilation_context(
        root.path(),
        &ContextRequest {
            selected_sources: vec![path.into()],
            ..Default::default()
        },
    );
    assert!(context.complete(), "{:?}", context.diagnostics);
    let unit = &context.units[0];
    let mut task = compiler_probe_task(
        root.path(),
        unit,
        Path::new("/usr/bin/g++"),
        ReplayPurpose::UnusedFunctions,
        "unused-policy",
    )
    .unwrap();
    task.argv.extend([
        "-Wunused-function".into(),
        "-fdiagnostics-show-option".into(),
    ]);
    let outcome = Runner::default().run(&task, &Cancellation::default());
    assert!(outcome.prerequisite_succeeded());
    let stderr = String::from_utf8(outcome.stderr).unwrap();
    assert!(
        stderr.contains(":1:12: warning:") && stderr.contains("[-Wunused-function]"),
        "{stderr}"
    );
    let mut finding = Finding::new(
        "compiler.unused-function",
        "unused function",
        SourceSpan::line(path, 1),
        "compiler",
    );
    finding.native_rule_id = "-Wunused-function".into();
    let observations = [CompilerConfigurationObservation {
        source: path.into(),
        configuration: unit.configuration.clone(),
        complete: true,
        findings: vec![finding],
    }];
    let mut input = request(SourceInput {
        path: path.into(),
        bytes: bytes.to_vec(),
    });
    for (generated, vendor, expected) in [
        (false, false, 0),
        (true, false, 0),
        (false, true, 0),
        (true, true, 1),
    ] {
        input.options = json!({"include_generated":generated,"include_vendor":vendor});
        let output = unused_function_consensus(&input, &context, &observations);
        assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
        assert_eq!(output.observation.findings.len(), expected, "{output:?}");
    }
    assert!(!root.path().join("never.o").exists());
}
#[test]
fn actual_gcc_include_trace_preserves_generated_external_and_direct_edges_and_rejects_depth_jumps()
{
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("generated")).unwrap();
    fs::write(
        root.path().join("generated/item.h"),
        "#pragma once\nint generated();\n",
    )
    .unwrap();
    fs::write(
        outside.path().join("external.h"),
        "#pragma once\nint external();\n",
    )
    .unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "#include \"generated/item.h\"\n#include <external.h>\nint main(){return 0;}\n",
    )
    .unwrap();
    let mut context = context(root.path());
    let unit = &mut context.units[0];
    unit.argv
        .insert(1, format!("-I{}", outside.path().display()));
    let task = compiler_probe_task(
        root.path(),
        unit,
        Path::new("/usr/bin/g++"),
        ReplayPurpose::IncludeTrace,
        "includes",
    )
    .unwrap();
    let result = Runner::default().run(&task, &Cancellation::default());
    assert!(result.prerequisite_succeeded());
    let trace = parse_include_trace(
        root.path(),
        root.path(),
        &root.path().join("main.cpp"),
        &String::from_utf8_lossy(&result.stderr),
    )
    .unwrap();
    assert!(trace.complete(), "{:?}", trace.unexpected);
    assert_eq!(trace.edges.len(), 2);
    assert!(
        trace
            .edges
            .iter()
            .any(|edge| edge.to == "generated/item.h" && edge.to_scope == "project")
    );
    assert!(
        trace
            .edges
            .iter()
            .any(|edge| edge.to.ends_with("external.h") && edge.to_scope == "external")
    );
    assert!(
        parse_include_trace(
            root.path(),
            root.path(),
            &root.path().join("main.cpp"),
            "... skipped-parent.h\n"
        )
        .is_err()
    );
}
#[test]
fn optional_installed_clang_probe_has_no_artifact_side_effects() {
    let clang = std::env::var_os("ICI_TEST_CLANG")
        .map(PathBuf::from)
        .or_else(|| {
            ["/usr/bin/clang++-21", "/usr/bin/clang++"]
                .iter()
                .map(PathBuf::from)
                .find(|path| path.is_file())
        });
    let Some(clang) = clang else {
        assert!(
            std::env::var_os("ICI_REQUIRE_BUILD_ADAPTERS").is_none(),
            "ICI_REQUIRE_BUILD_ADAPTERS requires ICI_TEST_CLANG or installed clang++"
        );
        eprintln!(
            "SKIP actual Clang AST replay: ICI_TEST_CLANG/clang++ unavailable; frozen actual Clang21 AST still tested"
        );
        return;
    };
    let root = tempfile::tempdir().unwrap();
    let bytes = b"int real(int n){if(n)return 1;return 0;}\n";
    fs::write(root.path().join("main.cpp"), bytes).unwrap();
    let context = context(root.path());
    let mut task = compiler_probe_task(
        root.path(),
        &context.units[0],
        &clang,
        ReplayPurpose::AstJson,
        "ast",
    )
    .unwrap();
    if let Ok(paths) = std::env::var("ICI_TEST_CLANG_LIBRARY_PATH") {
        task.env_overlay.insert("LD_LIBRARY_PATH".into(), paths);
    }
    let outcome = Runner::default().run(&task, &Cancellation::default());
    assert!(
        outcome.prerequisite_succeeded(),
        "{}",
        String::from_utf8_lossy(&outcome.stderr)
    );
    let facts = parse_clang_function_boundaries(
        &String::from_utf8(outcome.stdout).unwrap(),
        &SourceInput {
            path: "main.cpp".into(),
            bytes: bytes.to_vec(),
        },
        &root.path().join("main.cpp"),
        &context.units[0].configuration,
    )
    .unwrap();
    assert_eq!(facts.boundaries.len(), 1);
    assert_eq!(facts.boundaries[0].name, "real");
    assert!(!root.path().join("must-not-create-0.o").exists());
}

#[test]
fn required_compiler_boundary_policy_preserves_estimated_metrics_and_rejects_missing_variants() {
    let root = tempfile::tempdir().unwrap();
    let bytes = include_bytes!("../../../parity-fixtures/cpp/clang-boundaries.cpp").to_vec();
    fs::write(root.path().join("main.cpp"), &bytes).unwrap();
    let mut context = context(root.path());
    context.units.truncate(1);
    context.units[0].configuration = "debug".into();
    let source = SourceInput {
        path: "main.cpp".into(),
        bytes,
    };
    let fact = parse_clang_function_boundaries(
        include_str!("../../../tests/rust-parity/cpp/clang-boundaries.json"),
        &source,
        Path::new("/fixture/main.cpp"),
        "debug",
    )
    .unwrap();
    let mut request = request(source);
    request.check_id = "cpp.complexity".into();
    request.options = json!({"cpp_boundaries":"required","warn_limit":0});
    let mut session = ici_cpp::CppAnalysisSession::new();
    assert_eq!(
        session.analyze(&request).observation.state,
        TaskState::Failed
    );
    let output =
        session.analyze_with_compiler_context(&request, &context, std::slice::from_ref(&fact));
    assert_eq!(
        output.observation.state,
        TaskState::Succeeded,
        "{:?}",
        output.observation.limitations
    );
    assert_eq!(output.extra["boundary_mode"], "exact");
    assert!(
        output
            .targets
            .iter()
            .filter(|target| target.metrics.contains_key("boundary_source"))
            .all(|target| target.metrics["boundary_source"] == "compiler-ast")
    );
    assert!(
        output
            .observation
            .findings
            .iter()
            .all(|finding| finding.evidence == ici_domain::EvidenceLevel::Estimated)
    );
    let mut other = context.units[0].clone();
    other.configuration = "release".into();
    context.units.push(other);
    assert_eq!(
        session
            .analyze_with_compiler_context(&request, &context, &[fact])
            .observation
            .state,
        TaskState::Failed
    );
}
#[test]
fn compiler_include_graphs_never_merge_incompatible_variant_edges_into_a_cycle() {
    use ici_cpp::{CompilerIncludeObservation, IncludeEdge, IncludeTrace};
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.cpp"), "int main(){return 0;}\n").unwrap();
    let context = context(root.path());
    let mut request = request(SourceInput {
        path: "main.cpp".into(),
        bytes: b"int main(){return 0;}\n".to_vec(),
    });
    request.inputs.extend([
        SourceInput {
            path: "a.h".into(),
            bytes: b"// fixture".to_vec(),
        },
        SourceInput {
            path: "b.h".into(),
            bytes: b"// fixture".to_vec(),
        },
    ]);
    let edge = |from: &str, to: &str| IncludeEdge {
        from: from.into(),
        to: to.into(),
        from_scope: "project".into(),
        to_scope: "project".into(),
    };
    let mut observations = vec![
        CompilerIncludeObservation {
            source: "main.cpp".into(),
            configuration: context.units[0].configuration.clone(),
            complete: true,
            trace: IncludeTrace {
                edges: vec![edge("a.h", "b.h")],
                unexpected: vec![],
            },
        },
        CompilerIncludeObservation {
            source: "main.cpp".into(),
            configuration: context.units[1].configuration.clone(),
            complete: true,
            trace: IncludeTrace {
                edges: vec![edge("b.h", "a.h")],
                unexpected: vec![],
            },
        },
    ];
    let output = ici_cpp::analyze_compiler_include_cycles(&request, &context, &observations);
    assert_eq!(output.observation.state, TaskState::Succeeded);
    assert!(output.observation.findings.is_empty());
    observations[0].trace.edges.push(edge("b.h", "a.h"));
    let output = ici_cpp::analyze_compiler_include_cycles(&request, &context, &observations);
    assert_eq!(output.observation.findings.len(), 1);
    assert_eq!(
        output.observation.findings[0].evidence,
        ici_domain::EvidenceLevel::Measured
    );
    request
        .inputs
        .extend(["c.h", "d.h"].map(|path| SourceInput {
            path: path.into(),
            bytes: b"// fixture".to_vec(),
        }));
    observations[0]
        .trace
        .edges
        .extend([edge("c.h", "d.h"), edge("d.h", "c.h")]);
    request.options = json!({"max_reported":1});
    let capped = ici_cpp::analyze_compiler_include_cycles(&request, &context, &observations);
    assert_eq!(capped.observation.state, TaskState::Succeeded);
    assert_eq!(capped.observation.findings.len(), 1);
    assert_eq!(capped.extra["cycles_reported"], 1);
    assert_eq!(capped.extra["cycles_truncated"], 1);
    assert_eq!(
        capped
            .observation
            .measurements
            .iter()
            .find(|metric| metric.name == "cycles_found")
            .unwrap()
            .value,
        2.0
    );
    request.options = json!({"max_reported":2});
    assert_eq!(
        ici_cpp::analyze_compiler_include_cycles(&request, &context, &observations)
            .observation
            .findings
            .len(),
        2
    );
    request.options = json!({"max_reported":0});
    assert_eq!(
        ici_cpp::analyze_compiler_include_cycles(&request, &context, &observations)
            .observation
            .state,
        TaskState::Failed
    );
    request.options = json!({});
    observations.pop();
    assert_eq!(
        ici_cpp::analyze_compiler_include_cycles(&request, &context, &observations)
            .observation
            .state,
        TaskState::Failed
    );
}

#[test]
fn actual_missing_header_diagnostics_preserve_unresolved_location_without_inventing_an_edge() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("main.cpp"),
        "#include \"never-generated.h\"\nint main(){return 0;}\n",
    )
    .unwrap();
    let context = context(root.path());
    let task = compiler_probe_task(
        root.path(),
        &context.units[0],
        Path::new("/usr/bin/g++"),
        ReplayPurpose::IncludeTrace,
        "missing",
    )
    .unwrap();
    let result = Runner::default().run(&task, &Cancellation::default());
    assert_eq!(result.exit_code, Some(1));
    let stderr = String::from_utf8(result.stderr).unwrap();
    let targets = ici_cpp::parse_missing_include_targets(
        root.path(),
        root.path(),
        &root.path().join("main.cpp"),
        &stderr,
    )
    .unwrap();
    assert_eq!(targets.len(), 1);
    assert_eq!(targets[0].file_path, "main.cpp");
    assert_eq!(targets[0].start_line, 1);
    assert_eq!(targets[0].target_name, "CppIncludeUnresolved");
    assert_eq!(targets[0].metrics["include"], "never-generated.h");
    assert!(
        ici_cpp::parse_missing_include_targets(
            root.path(),
            root.path(),
            &root.path().join("main.cpp"),
            &format!("{stderr}\nunknown compiler message")
        )
        .is_err()
    );
}
