use ici_domain::{TaskState, sha256};
use ici_execution::TaskOutcome;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use std::collections::BTreeMap;

fn request() -> ValidationRequest {
    ValidationRequest {
        kind: ValidationKind::Gcov,
        task_id: "project.gcov".into(),
        component_id: "project".into(),
        root: "/workspace".into(),
        cwd: "/workspace".into(),
        owned_run_dir: "/workspace/run".into(),
        tool: ResolvedTool {
            id: "gcov".into(),
            path: "/tools/gcov".into(),
            source: ToolSource::Explicit,
            digest: sha256("tool"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["build/main.gcno".into()],
        source_files: vec!["main.c".into()],
        functions: vec![],
        options: ValidationOptions {
            gcov_capability_probe: true,
            shared_test_task_id: Some("project.test.suite".into()),
            shared_test_kind: Some(ValidationKind::CppExecutable),
            executable_test_source: Some("main.c".into()),
            ..Default::default()
        },
    }
}
fn outcome(id: &str, text: &str, exit: i32) -> TaskOutcome {
    let mut result = TaskOutcome::empty(id);
    result.state = TaskState::Succeeded;
    result.exit_code = Some(exit);
    result.stdout = text.as_bytes().to_vec();
    result
}
fn text() -> &'static str {
    "        -:    0:Source:main.c\n        -:    0:Graph:/workspace/build/main.gcno\n        -:    0:Data:/workspace/build/main.gcda\n        -:    0:Runs:1\nfunction main called 1 returned 100% blocks executed 80%\n        1:    1:int main(){\nbranch 0 taken 1 (fallthrough)\nbranch 1 taken 0\nbranch 2 taken 1 (throw)\ncall 3 returned 100%\n    #####:    2:  return 1;\nfunction never called 0 returned 0% blocks executed 0%\n    =====:    3:int never(){return 4;}\n"
}
#[test]
fn legacy_text_merges_raw_identities_without_inventing_function_geometry() {
    let mut request = request();
    request.targets.push("build/second.gcno".into());
    let planned = plan(&request).unwrap();
    let first = gcov_text::parse(text().as_bytes()).unwrap();
    let mut second = first.clone();
    second.graph = "/workspace/build/second.gcno".into();
    second.data = "/workspace/build/second.gcda".into();
    second.lines.insert(2, true);
    second.branches.insert((1, "main".into(), 1), true);
    let (report, info) = gcov_text::merge(&[first, second], &planned).unwrap();
    assert_eq!(
        (report.counts.covered_lines, report.counts.total_lines),
        (2, 3)
    );
    assert_eq!(
        (report.counts.covered_branches, report.counts.total_branches),
        (2, 2)
    );
    assert_eq!(
        (
            report.counts.covered_functions,
            report.counts.total_functions
        ),
        (Some(1), Some(2))
    );
    assert!(report.function_rows.iter().all(|row| row["start_line"] == 1
        && row["end_line"].is_null()
        && row["start_column"].is_null()
        && row["geometry"] == "source-unit-anchor"));
    assert_eq!(info["format"], "gcov-text");
    assert_eq!(info["excluded_throw_branches"], 2);
    let mut raw = text().as_bytes().to_vec();
    raw.extend_from_slice(b"        -:    4: // non-UTF8 source byte \xff\n");
    assert!(gcov_text::parse(&raw).is_ok());
}
#[test]
fn legacy_text_rejects_bad_counts_missing_headers_ambiguous_scope_and_unknown_records() {
    let mut planned = plan(&request()).unwrap();
    for bad in [
        text().replace("called 1", "called -1"),
        text().replace("taken 1 (fallthrough)", "taken NaN"),
        text().replace("Source:", "Unknown:"),
        text().replace("returned 100%", "returned 101%"),
        format!("{}unexpected protocol\n", text()),
        text().replace("        1:    1:int main(){\n", ""),
    ] {
        assert!(gcov_text::parse(bad.as_bytes()).is_err());
    }
    let parsed = gcov_text::parse(text().as_bytes()).unwrap();
    assert!(gcov_text::merge(&[parsed.clone(), parsed.clone()], &planned).is_err());
    planned.context.source_files = vec!["main.c".into(), "src/main.c".into()];
    let mut ambiguous = parsed.clone();
    ambiguous.source = "../../../src/main.c".into();
    assert!(gcov_text::merge(&[ambiguous], &planned).is_err());
    planned.context.source_files = vec!["missing.c".into()];
    assert!(gcov_text::merge(&[parsed], &planned).is_err());
}
#[test]
fn text_without_function_execution_records_keeps_function_coverage_unavailable() {
    let mut parsed = gcov_text::parse(text().as_bytes()).unwrap();
    parsed.functions.clear();
    let (report, _) = gcov_text::merge(&[parsed], &plan(&request()).unwrap()).unwrap();
    assert_eq!(report.counts.total_functions, None);
    assert_eq!(report.counts.covered_functions, None);
}
#[test]
fn only_complete_recognized_capability_probes_select_one_visible_report_format() {
    let planned = plan(&request()).unwrap();
    assert!(
        planned
            .tasks
            .iter()
            .any(|task| task.argv.contains(&"--help".into()))
    );
    assert!(
        planned
            .tasks
            .iter()
            .any(|task| task.argv.contains(&"--version".into()))
    );
    let mut outcomes = BTreeMap::from([
        (
            "project.gcov.capability".into(),
            outcome(
                "project.gcov.capability",
                "Usage: gcov [OPTION] SOURCE\n -b branch probabilities\n",
                0,
            ),
        ),
        (
            "project.gcov.version".into(),
            outcome("project.gcov.version", "gcov (Debian 7.4.0-6) 7.4.0\n", 0),
        ),
        (
            "project.test.suite".into(),
            outcome("project.test.suite", "", 0),
        ),
    ]);
    assert_eq!(
        ready_steps(&planned, &outcomes).unwrap(),
        vec!["project.gcov.text-report"]
    );
    let help = outcomes.get_mut("project.gcov.capability").unwrap();
    help.stdout.extend_from_slice(b" --json-format\n");
    assert_eq!(
        ready_steps(&planned, &outcomes).unwrap(),
        vec!["project.gcov.report"]
    );
    outcomes.insert(
        "project.gcov.text-report".into(),
        outcome("project.gcov.text-report", "", 0),
    );
    assert!(ready_steps(&planned, &outcomes).is_err());
    outcomes.remove("project.gcov.text-report");
    for failure in ["timeout", "truncated", "exit", "malformed"] {
        let mut broken = outcomes.clone();
        let probe = broken.get_mut("project.gcov.capability").unwrap();
        match failure {
            "timeout" => probe.timed_out = true,
            "truncated" => probe.truncated = true,
            "exit" => probe.exit_code = Some(1),
            "malformed" => probe.stdout = b"anything\n".to_vec(),
            _ => unreachable!(),
        }
        assert!(ready_steps(&planned, &broken).is_err(), "{failure}");
    }
}
