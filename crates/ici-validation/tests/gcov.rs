use flate2::{Compression, write::GzEncoder};
use ici_domain::sha256;
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::{gcov::*, *};
use serde_json::{Value, json};
use std::{io::Write, path::Path};
fn document() -> Value {
    json!({"format_version":"2","gcc_version":"15.2.0 20250914","current_working_directory":"/workspace","data_file":"first.gcda","files":[{"file":"main.cpp","functions":[{"blocks":2,"blocks_executed":1,"demangled_name":"f()","name":"_Z1fv","start_line":1,"end_line":3,"start_column":1,"end_column":2,"execution_count":1}],"lines":[{"line_number":2,"count":1,"unexecuted_block":false,"block_ids":[0,1],"function_name":"_Z1fv","branches":[{"count":1,"fallthrough":true,"throw":false,"source_block_id":0,"destination_block_id":1},{"count":0,"fallthrough":false,"throw":true,"source_block_id":0,"destination_block_id":2}],"calls":[{"source_block_id":0,"destination_block_id":1,"returned":1}],"conditions":[{"count":2,"covered":1,"not_covered_false":[1],"not_covered_true":[]}]}]}]})
}
fn gzip(bytes: &[u8]) -> Vec<u8> {
    let mut encoder = GzEncoder::new(vec![], Compression::fast());
    encoder.write_all(bytes).unwrap();
    encoder.finish().unwrap()
}
fn plan_fixture() -> ValidationPlan {
    plan(&request_fixture()).unwrap()
}
fn request_fixture() -> ValidationRequest {
    let options = ValidationOptions {
        shared_test_task_id: Some("component.ctest.suite".into()),
        shared_test_kind: Some(ValidationKind::Ctest),
        ..Default::default()
    };
    ValidationRequest {
        kind: ValidationKind::Gcov,
        task_id: "component.gcov".into(),
        component_id: "component".into(),
        root: "/workspace".into(),
        cwd: "/workspace".into(),
        owned_run_dir: "/workspace/run".into(),
        tool: ResolvedTool {
            id: "gcov".into(),
            path: "/tools/gcov".into(),
            source: ToolSource::Explicit,
            digest: sha256("fixture"),
            version: None,
            configuration: vec![],
        },
        targets: vec!["build/main.gcno".into()],
        source_files: vec!["main.cpp".into()],
        functions: vec![],
        options,
    }
}
#[test]
fn gzip_crc_truncation_trailing_concatenation_and_decompression_limits_are_strict() {
    let bytes = serde_json::to_vec(&document()).unwrap();
    let compressed = gzip(&bytes);
    assert_eq!(
        parse_gzip(&compressed, 1024 * 1024).unwrap().gcc_version,
        "15.2.0 20250914"
    );
    for bad in [
        compressed[..compressed.len() - 1].to_vec(),
        [compressed.clone(), vec![0]].concat(),
        [compressed.clone(), compressed.clone()].concat(),
    ] {
        assert!(parse_gzip(&bad, 1024 * 1024).is_err());
    }
    let mut bad = compressed.clone();
    let last = bad.len() - 1;
    bad[last] ^= 1;
    assert!(parse_gzip(&bad, 1024 * 1024).is_err());
    assert!(parse_gzip(&compressed, 10).is_err());
}
#[test]
fn schema_refuses_unknown_duplicate_boolean_null_and_version_specific_fields() {
    let value = document();
    for (pointer, bad) in [
        ("/format_version", json!(3)),
        ("/files/0/functions/0/execution_count", json!(true)),
        ("/files/0/functions/0/end_line", json!(0)),
        ("/files/0/lines/0/branches/0/source_block_id", Value::Null),
        ("/files/0/functions/0/total_prime_paths", Value::Null),
    ] {
        let mut bad_value = value.clone();
        if pointer.ends_with("total_prime_paths") {
            bad_value["files"][0]["functions"][0]["total_prime_paths"] = bad;
        } else {
            *bad_value.pointer_mut(pointer).unwrap() = bad;
        }
        assert!(
            parse_json(&serde_json::to_vec(&bad_value).unwrap()).is_err(),
            "{pointer}"
        );
    }
    let mut bad = value.clone();
    bad["unexpected"] = json!(1);
    assert!(parse_json(&serde_json::to_vec(&bad).unwrap()).is_err());
    let json = serde_json::to_string(&value).unwrap().replacen(
        "\"format_version\":\"2\"",
        "\"format_version\":\"2\",\"format_version\":\"2\"",
        1,
    );
    assert!(parse_json(json.as_bytes()).is_err());
    let mut v1 = value;
    v1["format_version"] = json!("1");
    assert!(parse_json(&serde_json::to_vec(&v1).unwrap()).is_err());
    v1["files"][0]["lines"][0]
        .as_object_mut()
        .unwrap()
        .remove("calls");
    v1["files"][0]["lines"][0]
        .as_object_mut()
        .unwrap()
        .remove("block_ids");
    for branch in v1["files"][0]["lines"][0]["branches"]
        .as_array_mut()
        .unwrap()
    {
        branch.as_object_mut().unwrap().remove("source_block_id");
        branch
            .as_object_mut()
            .unwrap()
            .remove("destination_block_id");
    }
    assert!(parse_json(&serde_json::to_vec(&v1).unwrap()).is_ok());
}
#[test]
fn prime_paths_and_conditions_are_validated_and_retained() {
    let mut value = document();
    let function = &mut value["files"][0]["functions"][0];
    function["total_prime_paths"] = json!(2);
    function["covered_prime_paths"] = json!(1);
    function["prime_path_coverage"] = json!([{"id":1,"sequence":[{"block_id":1,"edge_kind":"true","locations":[{"file":"main.cpp","line_numbers":[1,2]}]}]}]);
    let parsed = parse_json(&serde_json::to_vec(&value).unwrap()).unwrap();
    assert_eq!(
        parsed.files[0].functions[0]
            .prime_path_coverage
            .as_ref()
            .unwrap()[0]
            .sequence[0]
            .edge_kind,
        "true"
    );
    value["files"][0]["lines"][0]["conditions"][0]["covered"] = json!(3);
    assert!(parse_json(&serde_json::to_vec(&value).unwrap()).is_err());
}
#[test]
fn multi_unit_coverage_merges_identities_and_excludes_exception_edges_without_averaging() {
    let planned = plan_fixture();
    let first = parse_json(&serde_json::to_vec(&document()).unwrap()).unwrap();
    let mut second = first.clone();
    second.data_file = "second.gcda".into();
    second.files[0].functions[0].execution_count = 0;
    second.files[0].lines[0].count = 0;
    let result = merge(&[first.clone(), second], &planned).unwrap();
    assert_eq!(result.counts.total_lines, 1);
    assert_eq!(result.counts.covered_lines, 1);
    assert_eq!(result.counts.total_branches, 1);
    assert_eq!(result.counts.covered_branches, 1);
    assert_eq!(result.counts.total_functions, Some(1));
    assert_eq!(result.counts.covered_functions, Some(1));
    assert!(merge(&[first.clone(), first.clone()], &planned).is_err());
    let mut missing = planned.clone();
    missing.context.source_files.push("unmeasured.cpp".into());
    assert!(merge(&[first], &missing).is_err());
}
#[test]
fn ambiguous_report_path_is_refused_instead_of_assigning_the_wrong_source() {
    let mut planned = plan_fixture();
    planned.context.source_files.push("build/main.cpp".into());
    let mut document = document();
    document["current_working_directory"] = json!("/workspace/build");
    let report = parse_json(&serde_json::to_vec(&document).unwrap()).unwrap();
    assert!(merge(&[report], &planned).is_err());
    assert!(planned.tasks[0].cwd.ends_with("/run"));
    assert!(Path::new(&planned.tasks[0].argv[0]).is_absolute());
}
#[test]
fn frozen_actual_gcc_report_keeps_counts_and_unicode_function_columns() {
    let data = include_str!("../../../tests/rust-parity/validation/gcov-native.json")
        .replace("<ROOT>", "/workspace");
    let fixture: Value = serde_json::from_str(&data).unwrap();
    let parsed = parse_json(&serde_json::to_vec(&fixture["report"]).unwrap()).unwrap();
    let mut planned = plan_fixture();
    attach_sources(
        &mut planned,
        ici_providers::source_coordinates(&[ici_domain::SourceInput {
            path: "main.cpp".into(),
            bytes: fixture["source"].as_str().unwrap().as_bytes().to_vec(),
        }]),
    );
    let merged = merge(&[parsed], &planned).unwrap();
    assert_eq!(merged.counts.covered_functions, Some(2));
    assert_eq!(merged.counts.total_functions, Some(3));
    let main = merged
        .function_rows
        .iter()
        .find(|function| function["symbol"] == "main")
        .unwrap();
    assert_eq!(main["start_column"], 10);
    assert_eq!(main["native_columns"]["start"], 13);
}

#[test]
fn preexisting_counter_guard_preserves_bindings_and_user_data() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir(root.path().join("build")).unwrap();
    let mut request = plan_fixture().context;
    request.root = root.path().into();
    request.cwd = root.path().into();
    request.owned_run_dir = root.path().join("run");
    let clean = plan(&request).unwrap();
    assert!(clean.context.options.gcov_preexisting_data.is_empty());
    std::fs::write(root.path().join("build/main.gcda"), b"previous-user-run").unwrap();
    let accumulated = plan(&request).unwrap();
    assert_eq!(
        accumulated.context.options.gcov_preexisting_data,
        vec!["build/main.gcda"]
    );
    assert_eq!(clean.tasks, accumulated.tasks);
    let reason = ready_steps(&accumulated, &std::collections::BTreeMap::new())
        .unwrap_err()
        .to_string();
    assert!(reason.contains("accumulated coverage"));
    assert_eq!(
        std::fs::read(root.path().join("build/main.gcda")).unwrap(),
        b"previous-user-run"
    );
}

/// Frozen `adapters/providers/gcov.py` plans gcov as TaskKind.ANALYZE and the frozen fast
/// profile blocks only PREPARE (mutating) tasks: the report step is an uncached analysis step,
/// never a mutating preparation (negative: no step of the gcov plan is mutating).
#[test]
fn gcov_report_is_an_uncached_analysis_step_like_frozen_not_a_prepare_task() {
    let plan = plan_fixture();
    let report = plan
        .tasks
        .iter()
        .find(|task| task.id.ends_with(".report"))
        .unwrap();
    assert_eq!(report.kind, ici_domain::TaskKind::Analyze);
    assert!(!report.cacheable);
    assert!(plan.tasks.iter().all(|task| !task.mutating()));
}
/// Frozen `_GcovJsonTally.absorb` counts `not line.block_ids`: an empty `[]` (GCC 15 writes one
/// for a line without basic blocks) and a missing key (format 1) are both lines without block
/// ids; a line with ids is not (positive, empty and missing cases).
#[test]
fn lines_without_block_ids_count_empty_and_missing_lists_like_frozen() {
    let parsed = parse_json(&serde_json::to_vec(&document()).unwrap()).unwrap();
    assert!(!line_without_block_ids(&parsed.files[0].lines[0]));
    let mut empty = document();
    empty["files"][0]["lines"][0]["block_ids"] = json!([]);
    let parsed = parse_json(&serde_json::to_vec(&empty).unwrap()).unwrap();
    assert!(line_without_block_ids(&parsed.files[0].lines[0]));
    let mut v1 = document();
    v1["format_version"] = json!("1");
    let line = &mut v1["files"][0]["lines"][0];
    line.as_object_mut().unwrap().remove("block_ids");
    line.as_object_mut().unwrap().remove("calls");
    line.as_object_mut().unwrap().remove("conditions");
    for branch in line["branches"].as_array_mut().unwrap() {
        let branch = branch.as_object_mut().unwrap();
        branch.remove("source_block_id");
        branch.remove("destination_block_id");
    }
    let parsed = parse_json(&serde_json::to_vec(&v1).unwrap()).unwrap();
    assert!(line_without_block_ids(&parsed.files[0].lines[0]));
}
/// Frozen legacy `TestEngine._consume_cpp_coverage` words a rejected gcov JSON report set as
/// `message = f"gcov JSON evidence rejected ({exc.code}): {exc}"` (engines/test_coverage.py:207),
/// with `super().__init__(f"{code}: {message}")` (analysis/gcov_json.py:52). Each string below is
/// the frozen raise in analysis/coverage_support.py, quoted beside its assertion; the legacy
/// option (`legacy_gcov_source_wording`) selects it, native runs keep native wording.
#[test]
fn legacy_gcov_json_rejections_use_frozen_codes_and_messages() {
    let mut planned = plan_fixture();
    planned.context.options.legacy_gcov_source_wording = true;
    let rejected =
        |reports: &[GcovReport], plan: &ValidationPlan| merge(reports, plan).err().unwrap().0;
    let first = parse_json(&serde_json::to_vec(&document()).unwrap()).unwrap();
    // coverage_support.py:615 `raise GcovJsonError("no .gcov.json.gz reports were found",
    // code="missing_data")`
    assert_eq!(
        rejected(&[], &planned),
        "gcov JSON evidence rejected (missing_data): missing_data: no .gcov.json.gz reports were found"
    );
    // coverage_support.py:603 `f"gcov JSON report count exceeds {_MAX_GCOV_JSON_REPORTS}"`,
    // code="aggregate_limit" (4,096 reports).
    let many: Vec<GcovReport> = (0..4097)
        .map(|index| {
            let mut report = first.clone();
            report.data_file = format!("{index}.gcda");
            report
        })
        .collect();
    assert_eq!(
        rejected(&many, &planned),
        "gcov JSON evidence rejected (aggregate_limit): aggregate_limit: gcov JSON report count exceeds 4096"
    );
    // coverage_support.py:665 `f"cumulative gcov JSON {label} records exceed {maximum}"`,
    // code="aggregate_limit", labels file/function/line/branch/call in that order.
    let wide: Vec<GcovReport> = (0..5)
        .map(|index| {
            let mut report = first.clone();
            report.data_file = format!("{index}.gcda");
            report.files = vec![first.files[0].clone(); 4096];
            report
        })
        .collect();
    assert_eq!(
        rejected(&wide, &planned),
        "gcov JSON evidence rejected (aggregate_limit): aggregate_limit: cumulative gcov JSON file records exceed 16384"
    );
    // coverage_support.py:708 `"gcov JSON reports contain mixed format versions"` and :713
    // `"gcov JSON reports contain mixed GCC versions"`, code="inconsistent_report_set"; the
    // format version is checked first.
    let mut other_gcc = first.clone();
    other_gcc.data_file = "second.gcda".into();
    other_gcc.gcc_version = "14.1.0".into();
    assert_eq!(
        rejected(&[first.clone(), other_gcc.clone()], &planned),
        "gcov JSON evidence rejected (inconsistent_report_set): inconsistent_report_set: gcov JSON reports contain mixed GCC versions"
    );
    let mut other_format = other_gcc;
    other_format.format_version = "1".into();
    assert_eq!(
        rejected(&[first.clone(), other_format], &planned),
        "gcov JSON evidence rejected (inconsistent_report_set): inconsistent_report_set: gcov JSON reports contain mixed format versions"
    );
    // coverage_support.py:519 `f"source path {declared!r} maps to multiple project files"`,
    // code="ambiguous_source_path".
    let mut ambiguous = planned.clone();
    ambiguous.context.source_files.push("build/main.cpp".into());
    let mut moved = document();
    moved["current_working_directory"] = json!("/workspace/build");
    let report = parse_json(&serde_json::to_vec(&moved).unwrap()).unwrap();
    assert_eq!(
        rejected(std::slice::from_ref(&report), &ambiguous),
        "gcov JSON evidence rejected (ambiguous_source_path): ambiguous_source_path: source path 'main.cpp' maps to multiple project files"
    );
    // Native wording without the legacy option.
    let native = plan_fixture();
    assert_eq!(
        rejected(&[], &native),
        "gcov report set is empty or too large"
    );
    let mut native_ambiguous = native;
    native_ambiguous
        .context
        .source_files
        .push("build/main.cpp".into());
    assert_eq!(
        rejected(&[report], &native_ambiguous),
        "gcov source path maps to multiple declared files"
    );
}
/// Frozen `_collect_cpp_coverage` (engines/test_coverage.py) and build-session
/// `collect_coverage` (core/cmake.py) run `gcov --help` and the report under `run_process`
/// defaults (300 s, 1,000,000 characters per stream) and record these tool errors:
/// test_coverage.py:153 `"gcov JSON capability probe was incomplete"` (via cmake.py:260
/// `gcov_json_capability` returning None for a failed, timed-out or truncated probe),
/// :164 `"gcov output was incomplete"` (timed out or truncated) and :167
/// `f"gcov failed with exit code {gcov_result.returncode}"`; cmake.py:811
/// `f"gcov produced no {'JSON' if json_capability else 'text'} reports"` for a session run.
#[test]
fn legacy_gcov_process_failures_use_frozen_tool_errors() {
    use ici_domain::TaskState;
    use ici_execution::TaskOutcome;
    use std::collections::BTreeMap;
    let mut request = request_fixture();
    request.options.gcov_capability_probe = true;
    request.options.legacy_gcov_source_wording = true;
    let planned = plan(&request).unwrap();
    let bounds = |id: &str| {
        let task = planned.tasks.iter().find(|task| task.id == id).unwrap();
        (task.timeout_seconds, task.output_limit_bytes)
    };
    assert_eq!(
        bounds("component.gcov.capability"),
        (Some(300.0), Some(1_000_000))
    );
    // A build session's merged report keeps the native bound (frozen bounds each per-directory
    // gcov); the direct path's single report takes frozen's.
    assert_eq!(
        bounds("component.gcov.report"),
        (Some(1800.0), Some(8 * 1024 * 1024))
    );
    let mut direct = request.clone();
    direct.options.shared_test_kind = Some(ValidationKind::CppExecutable);
    direct
        .options
        .shared_cpp_test_sources
        .insert("component.ctest.suite".into(), "tests/test_main.cpp".into());
    let direct = plan(&direct).unwrap();
    for id in ["component.gcov.report", "component.gcov.text-report"] {
        let task = direct.tasks.iter().find(|task| task.id == id).unwrap();
        assert_eq!(
            (task.timeout_seconds, task.output_limit_bytes),
            (Some(300.0), Some(1_000_000)),
            "{id}"
        );
    }
    // Frozen runs no `gcov --version`; native's version probe keeps its own bound.
    assert_eq!(bounds("component.gcov.version"), (Some(30.0), Some(8192)));
    let completed = |id: &str, stdout: &str, exit: Option<i32>| {
        let mut outcome = TaskOutcome::empty(id);
        outcome.state = TaskState::Succeeded;
        outcome.exit_code = exit;
        outcome.stdout = stdout.as_bytes().to_vec();
        outcome
    };
    let summary = |outcomes: &BTreeMap<String, TaskOutcome>| {
        observe(&planned, outcomes, &CapturedArtifacts::default()).summary
    };
    let help = "Usage: gcov [OPTION...] SOURCE|OBJ...\n  -b  --json-format\n";
    let mut outcomes = BTreeMap::new();
    for (id, stdout) in [
        ("component.ctest.suite", ""),
        ("component.gcov.version", "gcov (GCC) 15.2.0\n"),
    ] {
        outcomes.insert(id.to_owned(), completed(id, stdout, Some(0)));
    }
    let probe = completed("component.gcov.capability", help, Some(1));
    outcomes.insert(probe.task_id.clone(), probe);
    // The incomplete probe selects no report: neither report step becomes ready.
    let ready = ready_steps(&planned, &outcomes).unwrap();
    assert!(!ready.iter().any(|id| id.contains("report")), "{ready:?}");
    assert_eq!(
        summary(&outcomes),
        "gcov JSON capability probe was incomplete"
    );
    outcomes.insert(
        "component.gcov.capability".into(),
        completed("component.gcov.capability", help, Some(0)),
    );
    let report = |configure: &dyn Fn(&mut TaskOutcome)| {
        let mut outcomes = outcomes.clone();
        let mut outcome = completed("component.gcov.report", "", Some(0));
        configure(&mut outcome);
        outcomes.insert(outcome.task_id.clone(), outcome);
        summary(&outcomes)
    };
    assert_eq!(
        report(&|outcome| outcome.exit_code = Some(3)),
        "gcov failed with exit code 3"
    );
    assert_eq!(
        report(&|outcome| {
            outcome.exit_code = None;
            outcome.signal = Some(9);
        }),
        "gcov failed with exit code -9"
    );
    assert_eq!(
        report(&|outcome| outcome.truncated = true),
        "gcov output was incomplete"
    );
    assert_eq!(
        report(&|outcome| {
            outcome.exit_code = None;
            outcome.timed_out = true;
        }),
        "gcov output was incomplete"
    );
    // A build session (no direct g++ sources) whose gcov wrote no report.
    let mut empty = outcomes.clone();
    let report_outcome = completed("component.gcov.report", "", Some(0));
    empty.insert(report_outcome.task_id.clone(), report_outcome);
    let mut artifacts = CapturedArtifacts::default();
    artifacts.errors.insert(
        "gcov-json".into(),
        "producer wrote no matching fresh report".into(),
    );
    assert_eq!(
        observe(&planned, &empty, &artifacts).summary,
        "gcov produced no JSON reports"
    );
    // Native wording without the legacy option.
    let mut native = request_fixture();
    native.options.gcov_capability_probe = true;
    let native = plan(&native).unwrap();
    let mut failed = outcomes.clone();
    let exit = completed("component.gcov.report", "", Some(3));
    failed.insert(exit.task_id.clone(), exit);
    assert_eq!(
        observe(&native, &failed, &CapturedArtifacts::default()).summary,
        "gcov report process did not complete successfully"
    );
}
/// Frozen `_bounded_gcov_json_report_paths` (coverage_support.py:601-606) enumerates the reports
/// and refuses an over-count set before reading any, as `aggregate_limit` evidence. The real
/// capture path must reach that wording: at 4097 reports it records the refusal instead of
/// aborting the run, and at exactly 4096 the count passes.
#[test]
fn legacy_gcov_json_report_count_reaches_the_frozen_refusal_through_the_real_capture() {
    use ici_domain::TaskState;
    use ici_execution::TaskOutcome;
    use std::collections::BTreeMap;
    let root = tempfile::tempdir().unwrap();
    let root = root.path().canonicalize().unwrap();
    let mut request = request_fixture();
    request.root = root.clone();
    request.cwd = root.clone();
    request.owned_run_dir = root.join("run");
    request.options.gcov_capability_probe = true;
    request.options.legacy_gcov_source_wording = true;
    request.options.gcov_without_suite = true;
    request.options.shared_test_task_id = None;
    request.options.shared_test_kind = None;
    let planned = plan(&request).unwrap();
    let pattern = planned
        .artifact_patterns
        .iter()
        .find(|pattern| pattern.id_prefix == "gcov-json")
        .unwrap()
        .clone();
    assert_eq!(pattern.max_files, 4096);
    let completed = |id: &str, stdout: &str| {
        let mut outcome = TaskOutcome::empty(id);
        outcome.state = TaskState::Succeeded;
        outcome.exit_code = Some(0);
        outcome.stdout = stdout.as_bytes().to_vec();
        outcome
    };
    let help = "Usage: gcov [OPTION...] SOURCE|OBJ...\n  -b  --json-format\n";
    let mut outcomes = BTreeMap::new();
    for (id, stdout) in [
        ("component.gcov.version", "gcov (GCC) 15.2.0\n"),
        ("component.gcov.capability", help),
        (pattern.producer_task_id.as_str(), ""),
    ] {
        outcomes.insert(id.to_owned(), completed(id, stdout));
    }
    let report = |index: usize| {
        let mut one = document();
        one["data_file"] = json!(format!("{index}.gcda"));
        gzip(&serde_json::to_vec(&one).unwrap())
    };
    let run = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
    run.mark_started(&planned, &pattern.producer_task_id);
    for index in 0..4097 {
        std::fs::write(
            run.path().join(format!("r{index:04}.gcov.json.gz")),
            report(index),
        )
        .unwrap();
    }
    let captured = capture_artifacts(&planned, &run, &outcomes).unwrap();
    assert!(captured.files.is_empty(), "nothing is read over the count");
    let output = observe(&planned, &outcomes, &captured);
    assert_eq!(
        output.summary,
        "gcov JSON evidence rejected (aggregate_limit): aggregate_limit: gcov JSON report count exceeds 4096"
    );
    assert!(!output.observation.evidence_is_complete());
    // Exactly 4096 reports: every one is captured and the count is not the refusal. One report
    // is removed so the same run directory holds the bound exactly.
    std::fs::remove_file(run.path().join("r4096.gcov.json.gz")).unwrap();
    let captured = capture_artifacts(&planned, &run, &outcomes).unwrap();
    assert_eq!(captured.files.len(), 4096);
    assert!(captured.errors.is_empty(), "{:?}", captured.errors);
    let output = observe(&planned, &outcomes, &captured);
    assert!(
        !output.summary.contains("report count exceeds"),
        "{}",
        output.summary
    );
    // The cumulative byte bound (shown with a scaled pattern so the test reads kilobytes): the
    // report that crosses it is the reading's own refusal instead of a capture abort, and the
    // capture stops there, as frozen's `_read_report_within_budget` stops at that report.
    for index in 3..4096 {
        std::fs::remove_file(run.path().join(format!("r{index:04}.gcov.json.gz"))).unwrap();
    }
    let lengths: Vec<u64> = (0..2)
        .map(|index| {
            std::fs::metadata(run.path().join(format!("r{index:04}.gcov.json.gz")))
                .unwrap()
                .len()
        })
        .collect();
    let mut scaled = planned.clone();
    for pattern in &mut scaled.artifact_patterns {
        if pattern.id_prefix == "gcov-json" {
            pattern.max_bytes_total = lengths[0] + lengths[1] - 1;
        }
    }
    let captured = capture_artifacts(&scaled, &run, &outcomes).unwrap();
    assert_eq!(
        captured.files.keys().collect::<Vec<_>>(),
        ["gcov-json:r0000.gcov.json.gz"]
    );
    assert_eq!(
        captured.errors["gcov-json:r0001.gcov.json.gz"],
        "artifact is larger than its bound"
    );
    assert!(!captured.errors.contains_key("gcov-json:r0002.gcov.json.gz"));
    assert_eq!(
        observe(&scaled, &outcomes, &captured).summary,
        "gcov JSON evidence rejected (compressed_limit): compressed_limit: compressed gcov JSON is larger than 8388608 bytes"
    );
}
/// decisions.md "legacy gcov text 수집 한도": frozen `parse_gcov_dir` (coverage_support.py:393)
/// reads every `cov_dir.glob("*.gcov")` report whole and skips only one it cannot read (`except
/// OSError: continue`); native bounds the text reports (4096 reports, 64 MiB each, 256 MiB in
/// total) and exceeding a bound never measures the rest: the count and aggregate bounds abort
/// the capture (the check FAILED, legacy ERROR/NOT_RUN) and an oversized report fails the
/// reading instead of being skipped like an unreadable one.
#[test]
fn legacy_gcov_text_bounds_fail_closed() {
    use ici_domain::TaskState;
    use ici_execution::TaskOutcome;
    use std::collections::BTreeMap;
    let root = tempfile::tempdir().unwrap();
    let root = root.path().canonicalize().unwrap();
    let mut request = request_fixture();
    request.root = root.clone();
    request.cwd = root.clone();
    request.owned_run_dir = root.join("run");
    request.options.gcov_capability_probe = true;
    request.options.legacy_gcov_source_wording = true;
    let planned = plan(&request).unwrap();
    let pattern = planned
        .artifact_patterns
        .iter()
        .find(|pattern| pattern.id_prefix == "gcov-text")
        .unwrap()
        .clone();
    assert_eq!(
        (
            pattern.max_files,
            pattern.max_bytes_each,
            pattern.max_bytes_total
        ),
        (4096, 64 * 1024 * 1024, 256 * 1024 * 1024)
    );
    let completed = |id: &str, stdout: &str| {
        let mut outcome = TaskOutcome::empty(id);
        outcome.state = TaskState::Succeeded;
        outcome.exit_code = Some(0);
        outcome.stdout = stdout.as_bytes().to_vec();
        outcome
    };
    let help = "Usage: gcov [OPTION...] SOURCE|OBJ...\n  -b  --branch-probabilities\n";
    let mut outcomes = BTreeMap::new();
    let suite = "1/1 Test #1: main ....   Passed    0.01 sec\n\
                 100% tests passed, 0 tests failed out of 1\n";
    for (id, stdout) in [
        ("component.ctest.suite", suite),
        ("component.gcov.version", "gcov (GCC) 15.2.0\n"),
        ("component.gcov.capability", help),
        (pattern.producer_task_id.as_str(), ""),
    ] {
        outcomes.insert(id.to_owned(), completed(id, stdout));
    }
    let text = b"        -:    0:Source:main.cpp\n        1:    2:int f() { return 1; }\n";
    // 4097 fresh reports: the capture aborts.
    let run = OwnedRun::create(&planned.context.owned_run_dir).unwrap();
    run.mark_started(&planned, &pattern.producer_task_id);
    for index in 0..4097 {
        std::fs::write(run.path().join(format!("r{index}.gcov")), text).unwrap();
    }
    assert_eq!(
        capture_artifacts(&planned, &run, &outcomes)
            .err()
            .unwrap()
            .0,
        "artifact report count exceeds bound"
    );
    for index in 0..4097 {
        std::fs::remove_file(run.path().join(format!("r{index}.gcov"))).unwrap();
    }
    // A report over 64 MiB (sparse) is a capture error that fails the legacy text reading.
    run.mark_started(&planned, &pattern.producer_task_id);
    std::fs::write(run.path().join("main.cpp.gcov"), text).unwrap();
    std::fs::File::create(run.path().join("zz-big.gcov"))
        .unwrap()
        .set_len(64 * 1024 * 1024 + 1)
        .unwrap();
    let captured = capture_artifacts(&planned, &run, &outcomes).unwrap();
    assert_eq!(
        captured.errors["gcov-text:zz-big.gcov"],
        "artifact is larger than its bound"
    );
    let output = observe(&planned, &outcomes, &captured);
    assert_eq!(output.summary, LEGACY_TEXT_TOO_LARGE);
    assert!(!output.observation.evidence_is_complete());
    assert!(output.observation.measurements.is_empty());
    // Without the oversized report the same reading measures main.cpp.
    std::fs::remove_file(run.path().join("zz-big.gcov")).unwrap();
    let captured = capture_artifacts(&planned, &run, &outcomes).unwrap();
    let output = observe(&planned, &outcomes, &captured);
    assert!(
        output.observation.evidence_is_complete(),
        "{}",
        output.summary
    );
    // The aggregate bound (256 MiB in the plan) aborts the capture the same way; shown with the
    // plan's pattern scaled down so the test reads kilobytes, not 256 MiB.
    let mut scaled = planned.clone();
    for pattern in &mut scaled.artifact_patterns {
        if pattern.id_prefix == "gcov-text" {
            pattern.max_bytes_total = text.len() as u64 * 2 - 1;
        }
    }
    std::fs::write(run.path().join("second.gcov"), text).unwrap();
    assert_eq!(
        capture_artifacts(&scaled, &run, &outcomes).err().unwrap().0,
        "aggregate artifact report byte limit exceeded"
    );
}
