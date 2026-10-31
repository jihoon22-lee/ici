use ici_domain::{SourceInput, sha256};
use ici_providers::resolver::{ResolvedTool, ToolSource};
use ici_validation::*;
use serde_json::Value;
fn fixture() -> (Value, ValidationPlan) {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/sanitizer-oracle.json"
    ))
    .unwrap();
    assert_eq!(
        fixture["oracle_source"],
        "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    let sources = fixture["sources"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(path, text)| SourceInput {
            path: path.clone(),
            bytes: text.as_str().unwrap().as_bytes().to_vec(),
        })
        .collect::<Vec<_>>();
    let tool = ResolvedTool {
        id: "fixture".into(),
        path: "/tools/suite".into(),
        source: ToolSource::Explicit,
        digest: sha256("fixture"),
        version: None,
        configuration: vec![],
    };
    let mut options = ValidationOptions::default();
    options
        .auxiliary_tools
        .insert("readelf".into(), tool.clone());
    let request = ValidationRequest {
        kind: ValidationKind::Sanitizer,
        task_id: "component.sanitize".into(),
        component_id: "component".into(),
        root: "/workspace".into(),
        cwd: "/workspace".into(),
        owned_run_dir: "/workspace/run".into(),
        tool,
        targets: vec!["suite".into()],
        source_files: sources.iter().map(|source| source.path.clone()).collect(),
        functions: vec![],
        options,
    };
    let mut planned = plan(&request).unwrap();
    attach_sources(&mut planned, ici_providers::source_coordinates(&sources));
    (fixture, planned)
}
#[test]
fn fixed_product_oracle_preserves_all_defect_taxonomy_frames_and_redacted_locations() {
    let (fixture, planned) = fixture();
    for case in fixture["cases"].as_array().unwrap() {
        let text = if let Some(text) = case["output"].as_str() {
            text.to_string()
        } else {
            case["output_recipe"]["repeat_text"]
                .as_str()
                .unwrap()
                .repeat(case["output_recipe"]["repeat_count"].as_u64().unwrap() as usize)
        }
        .replace("<ROOT>", "/workspace");
        let actual = sanitizer::parse_diagnostics(&text, &planned);
        if case.get("expected_error").is_some() {
            assert!(actual.is_err(), "{}", case["name"]);
            continue;
        }
        let actual = actual.unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
        let expected = case["expected"].as_array().unwrap();
        assert_eq!(actual.len(), expected.len(), "{}", case["name"]);
        for (actual, expected) in actual.iter().zip(expected) {
            let serialized = serde_json::to_value(actual).unwrap();
            for field in [
                "kind",
                "tool_name",
                "defect",
                "rule_id",
                "message",
                "frames_observed",
                "project_frames",
            ] {
                assert_eq!(
                    serialized[field], expected[field],
                    "{} {field}",
                    case["name"]
                );
            }
            match (
                &actual.primary_location,
                expected
                    .get("primary_location")
                    .filter(|location| !location.is_null()),
            ) {
                (None, None) => {}
                (Some(actual), Some(expected)) => {
                    assert_eq!(
                        actual.path,
                        expected["path"].as_str().unwrap(),
                        "{}",
                        case["name"]
                    );
                    assert_eq!(
                        u64::from(actual.start_line),
                        expected["start_line"].as_u64().unwrap()
                    );
                    assert_eq!(
                        actual.start_column.map(u64::from),
                        expected["start_column"].as_u64()
                    );
                    assert_eq!(actual.label, expected["label"].as_str().unwrap());
                }
                _ => panic!("{} primary mismatch {actual:?}", case["name"]),
            }
            let related = expected["related_locations"].as_array().unwrap();
            assert_eq!(
                actual.related_locations.len(),
                related.len(),
                "{}",
                case["name"]
            );
            for (actual_location, expected) in actual.related_locations.iter().zip(related) {
                assert_eq!(actual_location.path, expected["path"].as_str().unwrap());
                assert_eq!(
                    u64::from(actual_location.start_line),
                    expected["start_line"].as_u64().unwrap()
                );
                if actual_location.path == "[external]" {
                    assert!(actual_location.start_column.is_none());
                    assert!(actual.external_locations.iter().any(|location| u64::from(
                        location.start_line
                    )
                        == expected["start_line"].as_u64().unwrap()
                        && location.native_column.map(u64::from)
                            == expected["start_column"].as_u64()));
                } else {
                    assert_eq!(
                        actual_location.start_column.map(u64::from),
                        expected["start_column"].as_u64()
                    );
                }
            }
            assert!(
                actual
                    .finding
                    .related_locations
                    .iter()
                    .all(|location| location.path != "[external]")
            );
            assert!(!format!("{actual:?}").contains("C:\\sdk"));
        }
    }
}
#[test]
fn native_utf8_byte_columns_become_unicode_and_unconfirmed_columns_are_never_guessed() {
    let (_, mut planned) = fixture();
    attach_sources(
        &mut planned,
        ici_providers::source_coordinates(&[SourceInput {
            path: "src/unicode.cpp".into(),
            bytes: "/*😀*/ int value = 0;\n".as_bytes().to_vec(),
        }]),
    );
    let diagnostics = sanitizer::parse_diagnostics(
        "/workspace/src/unicode.cpp:1:13: runtime error: signed integer overflow\n",
        &planned,
    )
    .unwrap();
    assert_eq!(
        diagnostics[0]
            .primary_location
            .as_ref()
            .unwrap()
            .start_column,
        Some(10)
    );
    assert_eq!(diagnostics[0].kind, "ubsan");
}
