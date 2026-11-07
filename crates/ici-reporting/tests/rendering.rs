use ici_domain::{
    AnalysisOutput, CheckRecord, EvidenceLevel, Finding, InspectionTarget, RunResult, SourceSpan,
    TaskState,
};
use ici_reporting::{
    ProjectionContext, RenderOptions, StoredResult, github_annotations, legacy_engine,
    legacy_projection, migrate_report_payload, render_console, render_html, render_markdown,
    sarif_document,
};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
fn run() -> RunResult {
    RunResult::from_json(include_str!(
        "../../../tests/fixtures/ici-next/run-success.json"
    ))
    .unwrap()
}
fn finding() -> Finding {
    let mut finding = Finding::new(
        "ici.example",
        "message </script><img src=x onerror=alert(1)> & API_KEY=do-not-publish",
        SourceSpan {
            path: "src/😀 %name.py".into(),
            start_line: 2,
            end_line: Some(2),
            start_column: Some(3),
            end_column: Some(4),
            label: "".into(),
        },
        "external-tool",
    );
    finding.fingerprint = "attributed-fingerprint".into();
    finding.task_id = Some("task-lint".into());
    finding.component_id = Some("tool-a".into());
    finding
}
#[test]
fn explicit_v2_migration_matches_frozen_original_without_guessing_schema() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/migration-v2.json"
    ))
    .unwrap();
    for case in corpus["cases"].as_array().unwrap() {
        let actual = migrate_report_payload(&case["input"], case["project_root"].as_str())
            .unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
        assert_eq!(actual.to_value(), case["expected"], "{}", case["name"]);
        assert!(StoredResult::from_value(case["input"].clone()).is_err());
    }
    assert!(migrate_report_payload(&json!({"schema_version":"ici.result/v9"}), None).is_err());
    assert!(migrate_report_payload(&json!({"schema_version":"ici.result/v2","engine_name":"x","targets":[{"file_path":"/other/x"}]}),None).is_err());
}
#[test]
fn saved_run_renderers_preserve_axes_baseline_suppression_unicode_and_escape_boundaries() {
    let mut run = run();
    run.scope.kind = ici_domain::ScopeKind::Partial;
    run.scope.full_required_satisfied = false;
    run.scope.omitted_components = vec!["omitted".into()];
    run.gate.workspace = ici_domain::GateVerdict::NotEvaluated;
    let mut first = finding();
    first.evidence = EvidenceLevel::Estimated;
    let mut second = first.clone();
    second.fingerprint = "suppressed-id".into();
    second.suppression = ici_domain::FindingSuppression {
        suppressed: true,
        kind: "inline".into(),
        reason: "accepted <reason>".into(),
        origin: "policy.toml".into(),
    };
    run.findings = vec![first, second];
    run.publication.state = ici_domain::PublicationState::Failed;
    run.publication.detail = "publish denied".into();
    run.baseline = Some(ici_domain::BaselineComparison {
        state: ici_domain::BaselineState::Comparable,
        origin: "prior.json".into(),
        new: vec!["attributed-fingerprint".into()],
        unchanged: vec![],
        resolved: vec!["old".into()],
        carried: vec!["unselected".into()],
        reason: "".into(),
    });
    let stored = StoredResult::from_run(run).unwrap();
    let options = RenderOptions::default();
    let html = render_html(&stored, &options).unwrap();
    assert!(!html.contains("<img src=x"));
    assert!(!html.contains("do-not-publish"));
    assert!(html.contains("ESTIMATED"));
    assert!(html.contains("NOT_EVALUATED") || html.contains("NOT_CONFIGURED"));
    for text in [
        "omitted",
        "publish denied",
        "accepted &lt;reason&gt;",
        "unselected",
        "old",
        "unicode",
    ] {
        if text != "unicode" {
            assert!(html.contains(text), "{text}");
        }
    }
    assert!(!html.contains("src=\"http"));
    assert!(!html.contains("href=\"http"));
    assert!(!html.contains("fetch("));
    assert!(html.contains("connect-src 'none'"));
    let markdown = render_markdown(&stored, &options).unwrap();
    assert!(!markdown.contains("<img src=x"));
    assert!(!markdown.contains("do-not-publish"));
    let console = render_console(&stored, &options).unwrap();
    assert!(console.contains("2 findings; 1 suppressed"));
    assert!(console.contains("partial"));
    let sarif = sarif_document(&stored, &options).unwrap();
    assert_eq!(sarif["runs"][0]["columnKind"], "unicodeCodePoints");
    let result = &sarif["runs"][0]["results"][0];
    assert_eq!(
        result["locations"][0]["physicalLocation"]["region"]["endColumn"],
        5
    );
    assert!(
        result["locations"][0]["physicalLocation"]["artifactLocation"]
            .get("uriBaseId")
            .is_none()
    );
    assert_eq!(
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
        "src/%F0%9F%98%80%20%25name.py"
    );
    assert_eq!(result["baselineState"], "new");
    assert_eq!(
        sarif["runs"][0]["results"][1]["suppressions"][0]["kind"],
        "inSource"
    );
    assert_eq!(
        sarif["runs"][0]["properties"]["baseline"]["carried"],
        json!(["unselected"])
    );
}
#[test]
fn native_projection_covers_catalog_and_retains_every_target_and_raw_finding_detail() {
    let mut run = run();
    run.schema_version = 2;
    let mut outputs = BTreeMap::new();
    let mut checks = vec![];
    for check in ici_domain::builtin_catalog().checks {
        let task = if check.id == "python.lint" {
            "task-lint".into()
        } else {
            format!("task-{}", check.id)
        };
        checks.push(CheckRecord {
            task_id: task.clone(),
            check_id: check.id.clone(),
            component_id: "tool-a".into(),
            language: check.language,
            scope_digest: format!("sha256:{}", "a".repeat(64)),
            state: TaskState::Succeeded,
            evidence_complete: true,
            analysis_unit_id: None,
            variant: None,
        });
        let mut output = AnalysisOutput::new(&task, "native");
        output.targets.push(InspectionTarget::at("src/pass.py", 1));
        output.summary = format!("{} saved", check.id);
        output.extra = json!({"unique_producer_data":check.id,"api_key":"bare-secret","finding_details":{"raw-id":{"explanation":"full explanation","remediation":"use safe API","snippet":"x < y","metrics":{"weight":{"value":2,"unit":"count"}},"fixes":[{"description":"literal <fix>","replacements":[{"path":"src/pass.py","start_line":1,"start_column":1,"end_line":1,"end_column":2,"replacement":"<replacement>"}]}]}}});
        outputs.insert(task, output);
    }
    run.checks = Some(checks);
    run.findings = vec![finding()];
    run.gate.selected = ici_domain::GateVerdict::Fail;
    run.gate.has_violations = true;
    run.gate.reasons = vec!["observed issue".into()];
    let context = ProjectionContext {
        raw_fingerprints: BTreeMap::from([("attributed-fingerprint".into(), "raw-id".into())]),
        ..Default::default()
    };
    let document = legacy_projection(&run, &outputs, &context).unwrap();
    let value = document.as_value();
    let names: BTreeSet<_> = value["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|engine| engine["engine_name"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(names.len(), 19);
    assert_eq!(
        names,
        ici_domain::builtin_catalog()
            .legacy
            .iter()
            .map(|descriptor| descriptor.name.clone())
            .collect()
    );
    assert_eq!(
        value["results"]
            .as_array()
            .unwrap()
            .iter()
            .map(|engine| engine["targets"].as_array().unwrap().len())
            .sum::<usize>(),
        36
    );
    let lint = value["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "lint")
        .unwrap();
    assert_eq!(lint["findings"][0]["explanation"], "full explanation");
    assert_eq!(
        lint["findings"][0]["fixes"][0]["description"],
        "literal <fix>"
    );
    assert_eq!(lint["extra"]["native_checks"].as_array().unwrap().len(), 7);
    let stored = StoredResult::Legacy(document);
    let html = render_html(&stored, &RenderOptions::default()).unwrap();
    for name in names {
        assert!(html.contains(&format!("data-family=\"{name}\"")), "{name}");
    }
    assert!(html.contains("literal &lt;fix&gt;"));
    assert!(html.contains("x &lt; y"));
    assert!(!html.contains("bare-secret"));
    assert!(html.contains("PASS · src"));
    assert_eq!(legacy_engine("cpp.dead"), Some("dead"));
    assert_eq!(legacy_engine("unknown"), None);
}
#[test]
fn incomplete_cancelled_and_publication_states_are_not_flattened_by_reports() {
    for bytes in [
        include_bytes!("../../../tests/fixtures/ici-next/run-required-incomplete.json").as_slice(),
        include_bytes!("../../../tests/fixtures/ici-next/run-cancelled.json").as_slice(),
        include_bytes!("../../../tests/fixtures/ici-next/run-partial-selection.json").as_slice(),
    ] {
        let stored = StoredResult::from_bytes(bytes).unwrap();
        let options = RenderOptions::default();
        let html = render_html(&stored, &options).unwrap();
        let console = render_console(&stored, &options).unwrap();
        let sarif = sarif_document(&stored, &options).unwrap();
        for key in ["scope", "execution", "publication"] {
            assert!(html.contains(key));
            assert!(console.contains(key));
            assert!(sarif["runs"][0]["properties"]["axes"].get(key).is_some());
        }
    }
}
#[test]
fn annotation_command_injection_and_output_budgets_are_bounded() {
    let mut run = run();
    let mut finding = finding();
    finding.primary_location.path = "src/a,b.py".into();
    finding.message = "hello\n::error::forged".into();
    run.findings = vec![finding];
    let stored = StoredResult::from_run(run).unwrap();
    let options = RenderOptions::default();
    let annotations = github_annotations(&stored, &options).unwrap();
    assert!(annotations[0].contains("a%2Cb.py"));
    assert!(annotations[0].contains("%0A::error::forged"));
    assert!(!annotations[0].contains('\n'));
    assert!(
        render_markdown(
            &stored,
            &RenderOptions {
                max_markdown_bytes: 100,
                ..Default::default()
            }
        )
        .is_err()
    );
    let annotations = github_annotations(
        &stored,
        &RenderOptions {
            max_annotations: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(annotations.is_empty());
}
/// R8: the console's saved-JSON lines stay single, parseable lines at the default 80-column
/// width (the run axes once broke into fragments that no consumer could parse), while ordinary
/// prose is still wrapped at the width.
#[test]
fn console_json_payload_lines_are_never_wrapped() {
    let stored = StoredResult::from_bytes(include_bytes!(
        "../../../tests/fixtures/ici-next/run-required-incomplete.json"
    ))
    .unwrap();
    let options = RenderOptions::default();
    assert_eq!(options.console_width, 80);
    let console = render_console(&stored, &options).unwrap();
    let axes = console
        .lines()
        .find_map(|line| line.strip_prefix("Scope, execution, gate and publication: "))
        .unwrap_or_else(|| panic!("{console}"));
    // Positive: longer than the width and still one complete JSON object.
    assert!(axes.len() > 80, "{axes}");
    let value: Value = serde_json::from_str(axes).unwrap();
    assert!(value.get("scope").is_some() && value.get("execution").is_some());
    // Negative: no other line holds a fragment of that object.
    assert!(
        !console
            .lines()
            .any(|line| line.starts_with("\"") || line.starts_with("},"))
    );
    // Failure boundary: a long prose line is still wrapped at the width.
    let mut long = serde_json::from_slice::<Value>(include_bytes!(
        "../../../tests/fixtures/ici-next/run-required-incomplete.json"
    ))
    .unwrap();
    long["gate"]["reasons"] = json!([format!("reason {}", "word ".repeat(40))]);
    let stored = StoredResult::from_value(long).unwrap();
    let console = render_console(&stored, &options).unwrap();
    assert!(
        console
            .lines()
            .filter(|line| !line.starts_with("Scope, execution, gate and publication: "))
            .all(|line| unicode_width::UnicodeWidthStr::width(line) <= 80),
        "{console}"
    );
}

/// Without the console's wrapping, for phrases a line break may fall inside.
fn flat(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn terminal_console(fixture: &str) -> String {
    let path = format!(
        "{}/../../tests/fixtures/ici-next/{fixture}.json",
        env!("CARGO_MANIFEST_DIR")
    );
    let stored = StoredResult::from_bytes(&std::fs::read(path).unwrap()).unwrap();
    let options = RenderOptions {
        terminal: true,
        ..Default::default()
    };
    render_console(&stored, &options).unwrap()
}

#[test]
fn a_terminal_gets_each_run_axis_in_words_and_never_a_success_it_did_not_earn() {
    let success = terminal_console("run-success");
    assert!(
        success.contains("Execution: required work complete"),
        "{success}"
    );
    assert!(success.contains("Gate: selected scope PASS; whole workspace PASS"));
    assert!(success.contains("Publication: not configured"));
    assert!(!success.contains("Not the full required scope"));
    assert!(!success.contains("Scope, execution, gate and publication"));
    // A passing selection does not read as a passing workspace.
    let partial = terminal_console("run-partial-selection");
    assert!(partial.contains("Scope: partial"), "{partial}");
    assert!(partial.contains("Not the full required scope; not selected:"));
    assert!(partial.contains("Gate: selected scope PASS; whole workspace NOT_EVALUATED"));
    assert!(
        flat(&partial).contains("no gate violations in the selected scope"),
        "{partial}"
    );
    // Incomplete, failing and cancelled runs say so, with what stopped them.
    let incomplete = terminal_console("run-required-incomplete");
    assert!(
        incomplete.contains("Execution: required work incomplete"),
        "{incomplete}"
    );
    assert!(
        flat(&incomplete).contains("gate violations found"),
        "{incomplete}"
    );
    assert!(incomplete.contains("Blocked: prepare-native"));
    assert!(incomplete.contains("Failed: analyze-gui"));
    assert!(incomplete.contains("Gate reasons: 2"));
    assert!(incomplete.contains("Limitations: 1"));
    let failed = terminal_console("run-code-fail");
    assert!(
        failed.contains("Gate: selected scope FAIL; whole workspace FAIL"),
        "{failed}"
    );
    assert!(flat(&failed).contains("gate violations found"));
    let cancelled = terminal_console("run-cancelled");
    assert!(cancelled.contains("Execution: cancelled"), "{cancelled}");
    // No line of the summary is a JSON payload or past the width.
    for console in [&success, &partial, &incomplete, &failed, &cancelled] {
        assert!(
            console
                .lines()
                .all(|line| unicode_width::UnicodeWidthStr::width(line) <= 80),
            "{console}"
        );
    }
}

#[test]
fn an_incomplete_gate_on_a_terminal_does_not_rule_violations_out() {
    let text = std::fs::read(format!(
        "{}/../../tests/fixtures/ici-next/run-required-incomplete.json",
        env!("CARGO_MANIFEST_DIR")
    ))
    .unwrap();
    let mut value: Value = serde_json::from_slice(&text).unwrap();
    value["gate"]["has_violations"] = json!(false);
    let stored = StoredResult::from_value(value).unwrap();
    let options = RenderOptions {
        terminal: true,
        ..Default::default()
    };
    let console = render_console(&stored, &options).unwrap();
    assert!(
        flat(&console).contains("gate violations not ruled out"),
        "{console}"
    );
    assert!(!console.contains("no gate violations"), "{console}");
}

#[test]
fn publication_on_a_terminal_is_separate_from_the_code_verdict() {
    let text = std::fs::read(format!(
        "{}/../../tests/fixtures/ici-next/run-code-fail.json",
        env!("CARGO_MANIFEST_DIR")
    ))
    .unwrap();
    let mut value: Value = serde_json::from_slice(&text).unwrap();
    value["publication"] = json!({"state": "SUCCESS", "detail": "report published"});
    let stored = StoredResult::from_value(value).unwrap();
    let options = RenderOptions {
        terminal: true,
        ..Default::default()
    };
    let console = render_console(&stored, &options).unwrap();
    assert!(console.contains("selected scope FAIL"), "{console}");
    assert!(console.contains("Publication: success (separate from the code verdict)"));
}

#[test]
fn piped_output_keeps_the_whole_axes_line() {
    let stored = StoredResult::from_bytes(
        &std::fs::read(format!(
            "{}/../../tests/fixtures/ici-next/run-partial-selection.json",
            env!("CARGO_MANIFEST_DIR")
        ))
        .unwrap(),
    )
    .unwrap();
    let console = render_console(&stored, &RenderOptions::default()).unwrap();
    let axes = console
        .lines()
        .find_map(|line| line.strip_prefix("Scope, execution, gate and publication: "))
        .unwrap();
    let axes: Value = serde_json::from_str(axes).unwrap();
    assert_eq!(axes["gate"]["workspace"], "NOT_EVALUATED");
    assert!(!console.contains("Gate: selected scope"));
}
