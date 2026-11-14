//! Real clang-tidy transcripts parsed by the frozen `ici next` ClangTidyProvider
//! (`tests/rust-parity/cpp/capture_tidy_provider.py`) replayed through the native observer.
use ici_domain::{EvidenceLevel, TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::{
    ProviderKind, ProviderRequest, observe_full, plan,
    resolver::{ResolvedTool, ToolSource},
};
use serde_json::{Value, json};
use std::{fs, path::Path};

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// The native observation of one frozen transcript: `failed_to_parse` is the frozen parse
/// failure (the native task does not succeed), findings carry the frozen fields.
fn observed(oracle: &Value, case: &Value) -> Value {
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/lint-tidy"),
        root.path(),
    );
    let text = |key: &str| {
        case[key]
            .as_str()
            .unwrap()
            .replace("<ROOT>", &root.path().display().to_string())
    };
    let sources: Vec<String> = oracle["argv_tail"].as_array().unwrap()[2..]
        .iter()
        .map(|item| item.as_str().unwrap().to_owned())
        .collect();
    let tool = ResolvedTool {
        id: "clang-tidy".into(),
        path: "/usr/bin/clang-tidy".into(),
        source: ToolSource::Explicit,
        digest: sha256("clang-tidy"),
        version: None,
        configuration: vec![],
    };
    let mut request = ProviderRequest::new(
        ProviderKind::ClangTidy,
        tool,
        root.path(),
        root.path(),
        sources,
        "app.cpp.tidy",
    );
    request.options.database_dir = Some("build".into());
    request.options.next_tidy = true;
    let mut planned = plan(&request).unwrap();
    // The CLI attaches the selected source snapshots, which establish byte columns.
    let inputs: Vec<_> = request
        .targets
        .iter()
        .map(|path| ici_domain::SourceInput {
            path: path.clone(),
            bytes: fs::read(root.path().join(path)).unwrap(),
        })
        .collect();
    ici_providers::attach_sources(&mut planned, ici_providers::source_coordinates(&inputs));
    // Native targets are absolute spellings of the same project sources.
    let prefix = format!("{}/", root.path().display());
    let tail: Vec<String> = planned.task.argv[1..]
        .iter()
        .map(|item| item.strip_prefix(&prefix).unwrap_or(item).to_owned())
        .collect();
    assert_eq!(
        tail[..],
        oracle["argv_tail"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap().to_owned())
            .collect::<Vec<_>>()[..],
        "the native plan is the frozen `clang-tidy -p <build> <sources>`"
    );
    let mut outcome = TaskOutcome::empty(&planned.task.id);
    outcome.state = TaskState::Succeeded;
    outcome.exit_code = Some(0);
    outcome.stdout = text("stdout").into_bytes();
    outcome.stderr = text("stderr").into_bytes();
    if case["outcome"] == "timed-out" {
        outcome.state = TaskState::Failed;
        outcome.timed_out = true;
        outcome.exit_code = None;
    }
    let output = observe_full(&planned, &outcome);
    let failed = output.observation.state != TaskState::Succeeded;
    let findings: Vec<Value> = output
        .observation
        .findings
        .iter()
        .map(|finding| {
            json!({
                "fingerprint": finding.fingerprint,
                "rule_id": finding.rule_id,
                "native_rule_id": finding.native_rule_id,
                "message": finding.message,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "path": finding.primary_location.path,
                "start_line": finding.primary_location.start_line,
                "start_column": finding.primary_location.start_column,
                "provider": finding.provider,
                "task_id": finding.task_id,
                "evidence": match finding.evidence {
                    EvidenceLevel::Measured => "MEASURED",
                    _ => "other",
                },
            })
        })
        .collect();
    json!({
        "failed": failed,
        "summary": output.summary,
        "findings": findings,
        "limitations": output.observation.limitations,
    })
}

/// Native findings equal the frozen ones field by field, including the frozen
/// `<kind>: <message>` text and its fingerprint (root ruling R1). One recorded correction: a
/// diagnostic outside the project is omitted with a limitation instead of the frozen
/// `[external]` pseudo-path finding the frozen `_relative` filter meant to drop.
#[test]
fn frozen_next_clang_tidy_provider_parse_matches_native_findings_and_parse_failures() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/tidy-provider-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 6);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let actual = observed(&oracle, case);
        let frozen = &case["frozen"];
        let frozen_failed = frozen["failed_to_parse"]
            .as_str()
            .is_some_and(|text| !text.is_empty());
        let expected: Vec<Value> = frozen["findings"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|finding| finding["path"] != "[external]")
            .cloned()
            .collect();
        let external = frozen["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| finding["path"] == "[external]");
        let omitted = actual["limitations"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item == "external compiler diagnostic omitted from project locations");
        // Correction (root ruling R2): the real multi-source transcript carries clang-tidy 21
        // `[n/N] Processing file` progress lines on stderr. Frozen rejects it; native ignores
        // exactly those lines and yields the same finding as the stdout alone.
        let expected = if name == "real" {
            assert!(frozen_failed);
            cases["real-stdout"]["frozen"]["findings"]
                .as_array()
                .unwrap()
                .to_vec()
        } else {
            expected
        };
        let frozen_failed = frozen_failed && name != "real";
        if actual["failed"] != json!(frozen_failed)
            || (!frozen_failed && actual["findings"] != json!(expected))
            || external != omitted
        {
            eprintln!("== {name}\nactual: {actual}\nfrozen: {frozen}");
            mismatches.push(name.clone());
        }
    }
    assert_eq!(cases["real"]["frozen"]["findings"], json!([]));
    assert!(
        cases["real"]["stderr"]
            .as_str()
            .unwrap()
            .contains("[1/3] Processing file <ROOT>/src/clean.cpp.")
    );
    let real = observed(&oracle, &cases["real"]);
    assert_eq!(real["failed"], json!(false), "{real}");
    assert_eq!(real["findings"][0]["message"], "warning: Division by zero");
    assert_eq!(
        real["findings"][0]["fingerprint"],
        cases["real-stdout"]["frozen"]["findings"][0]["fingerprint"]
    );
    assert!(mismatches.is_empty(), "{mismatches:?}");
}

/// One real `clang-tidy -p build <sources>` run planned by the native provider over a copy of
/// `parity-fixtures/cpp/lint-tidy` (optionally with the sources replaced), with the observation.
fn real_run(
    sources: &[(&str, &str)],
) -> (tempfile::TempDir, TaskOutcome, ici_providers::ProviderPlan) {
    let tidy = std::env::var("ICI_TEST_CLANG_TIDY").expect("explicit clang-tidy 21 is required");
    let root = tempfile::tempdir().unwrap();
    copy_tree(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity-fixtures/cpp/lint-tidy"),
        root.path(),
    );
    let mut targets = vec![];
    if sources.is_empty() {
        for name in ["clean.cpp", "qt.cpp", "tidy.cpp"] {
            targets.push(format!("src/{name}"));
        }
    } else {
        fs::remove_dir_all(root.path().join("src")).unwrap();
        fs::create_dir(root.path().join("src")).unwrap();
        for (name, text) in sources {
            fs::write(root.path().join("src").join(name), text).unwrap();
            targets.push(format!("src/{name}"));
        }
    }
    fs::create_dir(root.path().join("build")).unwrap();
    let database: Vec<Value> = targets
        .iter()
        .map(|source| json!({"directory":root.path(),"file":source,"arguments":["g++","-std=c++17","-Iinclude","-c",source]}))
        .collect();
    fs::write(
        root.path().join("build/compile_commands.json"),
        Value::Array(database).to_string(),
    )
    .unwrap();
    let tool = ici_providers::resolver::ToolRequest {
        id: "clang-tidy".into(),
        explicit: Some(tidy.into()),
        project_root: root.path().into(),
        pack_root: None,
        path_env: None,
    }
    .resolve()
    .unwrap();
    let mut request = ProviderRequest::new(
        ProviderKind::ClangTidy,
        tool,
        root.path(),
        root.path(),
        targets.clone(),
        "app.cpp.tidy",
    );
    request.options.database_dir = Some("build".into());
    request.options.next_tidy = true;
    let mut planned = plan(&request).unwrap();
    let inputs: Vec<_> = targets
        .iter()
        .map(|path| ici_domain::SourceInput {
            path: path.clone(),
            bytes: fs::read(root.path().join(path)).unwrap(),
        })
        .collect();
    ici_providers::attach_sources(&mut planned, ici_providers::source_coordinates(&inputs));
    let mut environment = std::collections::BTreeMap::new();
    environment.insert("PATH".to_owned(), "/usr/bin:/bin".to_owned());
    environment.insert("LC_ALL".to_owned(), "C".to_owned());
    if let Ok(libraries) = std::env::var("ICI_TEST_CPP_LIBRARY_PATH") {
        environment.insert("LD_LIBRARY_PATH".into(), libraries);
    }
    let outcome = ici_execution::Runner::with_environment(environment)
        .run(&planned.task, &ici_execution::Cancellation::default());
    (root, outcome, planned)
}

/// Root ruling R2 (correction of a defect shared with frozen): a real clang-tidy 21 run over
/// several sources prints `[n/N] Processing file <source>.` on stderr. Native accepts and
/// ignores exactly those lines: findings are parsed (positive), a clean multi-source run is a
/// clean observation (negative), and output that is not a genuine progress line, or a progress
/// line that does not belong to this run, still fails the parse (failure).
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn real_multi_source_clang_tidy_progress_lines_are_ignored_and_everything_else_stays_strict() {
    let (_project, outcome, planned) = real_run(&[]);
    let stderr = String::from_utf8(outcome.stderr.clone()).unwrap();
    let root = planned.context.workspace_root.display().to_string();
    for (index, name) in ["clean.cpp", "qt.cpp", "tidy.cpp"].iter().enumerate() {
        assert!(
            stderr.contains(&format!(
                "[{}/3] Processing file {root}/src/{name}.",
                index + 1
            )),
            "{stderr}\n{outcome:?}"
        );
    }
    let output = observe_full(&planned, &outcome);
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    let findings: Vec<_> = output
        .observation
        .findings
        .iter()
        .map(|finding| {
            (
                finding.rule_id.as_str(),
                finding.message.as_str(),
                finding.primary_location.path.as_str(),
                finding.primary_location.start_line,
                finding.primary_location.start_column,
            )
        })
        .collect();
    assert_eq!(
        findings,
        [(
            "clang_tidy.clang-analyzer-core.DivideZero",
            "warning: Division by zero",
            "src/tidy.cpp",
            7,
            Some(18)
        )]
    );
    // Failure: strictness is kept for anything that is not this run's progress output.
    let mutated = |extra: &str| {
        let mut changed = outcome.clone();
        changed.stderr = format!("{stderr}{extra}\n").into_bytes();
        let observation = observe_full(&planned, &changed).observation;
        (observation.state, observation.limitations.first().cloned())
    };
    for extra in [
        "Processing file src/tidy.cpp".to_owned(),
        format!("[4/4] Processing file {root}/src/tidy.cpp."),
        format!("[5/3] Processing file {root}/src/tidy.cpp."),
        format!("[4/3] Processing file {root}/src/other.cpp."),
        "[4/3] Processing file /usr/include/stdio.h.".to_owned(),
        "clang-tidy: error: unexpected output".to_owned(),
    ] {
        let (state, limitation) = mutated(&extra);
        assert_eq!(state, TaskState::Failed, "{extra}");
        assert!(
            limitation
                .as_deref()
                .is_some_and(|text| text.starts_with("cannot parse clang-tidy diagnostics: ")),
            "{extra}: {limitation:?}"
        );
    }
    // A further command for an already processed source continues the counter past N and, once
    // a warning was generated, repeats the cumulative summary (cpp28 R3); without it the
    // transcript is not a real batch.
    let (state, _) = mutated(&format!(
        "[4/3] Processing file {root}/src/tidy.cpp.\n1 warning generated."
    ));
    assert_eq!(state, TaskState::Succeeded);
    let (state, limitation) = mutated(&format!("[4/3] Processing file {root}/src/tidy.cpp."));
    assert_eq!(state, TaskState::Failed);
    assert_eq!(
        limitation.as_deref(),
        Some(
            "cannot parse clang-tidy diagnostics: clang-tidy command after a warning has no \
             generated-warning summary"
        )
    );

    // Negative: a clean multi-source run prints only progress lines and is a clean pass.
    let (_clean, outcome, planned) = real_run(&[
        ("one.cpp", "int one() { return 1; }\n"),
        ("two.cpp", "int two() { return 2; }\n"),
    ]);
    let stderr = String::from_utf8(outcome.stderr.clone()).unwrap();
    assert!(stderr.starts_with("[1/2] Processing file "), "{stderr}");
    assert!(outcome.stdout.is_empty());
    let output = observe_full(&planned, &outcome);
    assert_eq!(output.observation.state, TaskState::Succeeded, "{output:?}");
    assert!(output.observation.findings.is_empty());
    assert_eq!(output.status, "PASS");
}
