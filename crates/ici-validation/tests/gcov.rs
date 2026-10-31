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
    let options = ValidationOptions {
        shared_test_task_id: Some("component.ctest.suite".into()),
        shared_test_kind: Some(ValidationKind::Ctest),
        ..Default::default()
    };
    let request = ValidationRequest {
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
    };
    plan(&request).unwrap()
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
