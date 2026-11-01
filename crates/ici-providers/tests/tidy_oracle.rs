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

/// The frozen finding as the native provider reports it. Two recorded differences:
/// the native message omits the frozen `<kind>: ` prefix (the kind is the finding severity),
/// so the frozen fingerprint formula (provider, rule, path, line, column, message) is applied
/// to that message; and a diagnostic outside the project is omitted with a limitation
/// instead of the frozen `[external]` pseudo-path finding the provider meant to drop.
fn native_view(frozen: &Value) -> Value {
    let mut finding = frozen.clone();
    let message = frozen["message"].as_str().unwrap();
    let message = message.split_once(": ").map_or(message, |(_, rest)| rest);
    finding["message"] = json!(message);
    finding["fingerprint"] = json!(sha256(
        [
            "clang-tidy",
            frozen["native_rule_id"].as_str().unwrap(),
            frozen["path"].as_str().unwrap(),
            &frozen["start_line"].to_string(),
            &frozen["start_column"].to_string(),
            message,
        ]
        .join("\0")
    ));
    finding
}

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
            .map(native_view)
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
        if actual["failed"] != json!(frozen_failed)
            || (!frozen_failed && actual["findings"] != json!(expected))
            || external != omitted
        {
            eprintln!("== {name}\nactual: {actual}\nfrozen: {frozen}");
            mismatches.push(name.clone());
        }
    }
    // Both parsers reject a real multi-source clang-tidy 21 transcript whose stderr carries
    // `[n/N] Processing file` progress lines; the stdout alone yields the finding.
    assert_eq!(cases["real"]["frozen"]["findings"], json!([]));
    assert!(observed(&oracle, &cases["real"])["failed"] == json!(true));
    assert_eq!(
        observed(&oracle, &cases["real-stdout"])["findings"][0]["start_column"],
        18
    );
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
