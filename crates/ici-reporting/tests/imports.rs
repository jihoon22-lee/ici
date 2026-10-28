use ici_reporting::{ImportOptions, ImportProvenance, import_report, load_auxiliary};
use serde_json::json;
use std::collections::BTreeMap;
fn provenance() -> ImportProvenance {
    ImportProvenance {
        source_path: "external-output".into(),
        scope: "external CI job".into(),
        imported_at: "2026-10-29T00:00:00Z".into(),
        observed_at: None,
    }
}
fn sarif(artifact: serde_json::Value, kind: Option<&str>, region: serde_json::Value) -> Vec<u8> {
    let mut run = json!({"tool":{"driver":{"name":"external"}},"results":[{"ruleId":"X1","message":{"text":"external finding"},"locations":[{"physicalLocation":{"artifactLocation":artifact,"region":region}}]}]});
    if let Some(kind) = kind {
        run["columnKind"] = json!(kind);
    }
    serde_json::to_vec(&json!({"version":"2.1.0","runs":[run]})).unwrap()
}
#[test]
fn supporting_imports_never_claim_an_ici_gate_and_roundtrip_provenance() {
    let payload = b"SF:src/main.cpp\nDA:1,3\nDA:2,0\nend_of_record\n";
    let document =
        import_report("lcov", payload, &provenance(), &ImportOptions::default()).unwrap();
    assert_eq!(document.gate, "NOT_EVALUATED");
    assert_eq!(document.evidence, "imported");
    assert_eq!(document.source.sha256, &ici_domain::sha256(payload)[7..]);
    assert_eq!(
        document.records[0]["lines"],
        json!([{"line":1,"hits":3},{"line":2,"hits":0}])
    );
    assert_eq!(
        load_auxiliary(document.to_json().unwrap().as_bytes()).unwrap(),
        document
    );
    let mut invalid = document.to_value().unwrap();
    invalid["gate"] = json!("PASS");
    assert!(load_auxiliary(serde_json::to_string(&invalid).unwrap().as_bytes()).is_err());
    let mut bad = provenance();
    bad.imported_at = "2026-10-29T00:00:00".into();
    assert!(import_report("lcov", payload, &bad, &ImportOptions::default()).is_err());
}
#[test]
fn sarif_uri_base_requires_explicit_mapping_and_unicode_columns_are_converted_once() {
    let bytes = sarif(
        json!({"uri":"main%20file.py","uriBaseId":"SRCROOT"}),
        Some("utf16CodeUnits"),
        json!({"startLine":1,"startColumn":4,"endColumn":5}),
    );
    assert!(import_report("sarif", &bytes, &provenance(), &ImportOptions::default()).is_err());
    let options = ImportOptions {
        uri_base_mappings: BTreeMap::from([("SRCROOT".into(), "src".into())]),
        source_texts: BTreeMap::from([("src/main file.py".into(), "@😀x".into())]),
    };
    let document = import_report("sarif", &bytes, &provenance(), &options).unwrap();
    let location = &document.records[0]["locations"][0];
    assert_eq!(location["path"], "src/main file.py");
    assert_eq!(location["start_column"], 3);
    assert_eq!(location["end_column"], 3);
    assert_eq!(location["native_region"]["startColumn"], 4);
    assert_eq!(location["column_kind"], "unicodeCodePoints");
    let split = sarif(
        json!({"uri":"main%20file.py","uriBaseId":"SRCROOT"}),
        Some("utf16CodeUnits"),
        json!({"startLine":1,"startColumn":3}),
    );
    assert!(import_report("sarif", &split, &provenance(), &options).is_err());
}
#[test]
fn sarif_unspecified_column_units_and_zero_width_regions_are_retained_honestly() {
    let bytes = sarif(
        json!({"uri":"src.py"}),
        None,
        json!({"startLine":1,"startColumn":4,"endColumn":5}),
    );
    let document =
        import_report("sarif", &bytes, &provenance(), &ImportOptions::default()).unwrap();
    let location = &document.records[0]["locations"][0];
    assert_eq!(location["native_column_kind"], "unspecified");
    assert!(location.get("start_column").is_none());
    assert!(
        document
            .limitations
            .iter()
            .any(|value| value.contains("unspecified"))
    );
    let bytes = sarif(
        json!({"uri":"src.py"}),
        Some("unicodeCodePoints"),
        json!({"startLine":1,"startColumn":2,"endColumn":2}),
    );
    let document =
        import_report("sarif", &bytes, &provenance(), &ImportOptions::default()).unwrap();
    assert_eq!(document.records[0]["locations"][0]["zero_width"], true);
    assert!(
        document.records[0]["locations"][0]
            .get("end_column")
            .is_none()
    );
}
#[test]
fn external_paths_and_invalid_sarif_shapes_do_not_escape_project_scope() {
    for uri in [
        "../x.py",
        "%2e%2e/x.py",
        "/absolute/x.py",
        "https://example.com/x.py",
        "C:/x.py",
        "x%5cy.py",
        "bad%ZZ.py",
    ] {
        let bytes = sarif(json!({"uri":uri}), None, json!({"startLine":1}));
        assert!(
            import_report("sarif", &bytes, &provenance(), &ImportOptions::default()).is_err(),
            "{uri}"
        );
    }
    let bytes = sarif(json!({"index":9}), None, json!({"startLine":1}));
    assert!(import_report("sarif", &bytes, &provenance(), &ImportOptions::default()).is_err());
}
#[test]
fn junit_preserves_test_outcomes_failure_text_native_zero_lines_and_empty_suite_scope() {
    let xml = r#"<testsuites><testsuite name="suite"><testcase name="pass" file="tests/a.py" line="0" time="0.1"/><testcase name="fail"><failure>bad &lt;value&gt; &#128512;</failure></testcase><testcase name="error"><failure message="failure"/><error><![CDATA[exception <trace>]]></error></testcase><testcase name="skip"><skipped/></testcase></testsuite></testsuites>"#;
    let document = import_report(
        "junit",
        xml.as_bytes(),
        &provenance(),
        &ImportOptions::default(),
    )
    .unwrap();
    let records = &document.records;
    assert_eq!(records.len(), 4);
    assert_eq!(records[0]["native_line"], 0);
    assert_eq!(records[0]["locations"][0]["line"], serde_json::Value::Null);
    assert_eq!(records[1]["message"], "bad <value> 😀");
    assert_eq!(records[2]["status"], "error");
    assert_eq!(records[2]["message"], "exception <trace>");
    assert_eq!(records[3]["status"], "skipped");
    assert!(
        import_report(
            "junit",
            b"<testsuite/>",
            &provenance(),
            &ImportOptions::default()
        )
        .unwrap()
        .records
        .is_empty()
    );
}
#[test]
fn junit_dtd_entities_malformed_xml_and_lcov_incomplete_records_are_refused() {
    for xml in [
        "<!DOCTYPE x [<!ENTITY leak SYSTEM 'file:///private'>]><testsuite/>",
        "<testsuite><testcase><failure>&unknown;</failure></testcase></testsuite>",
        "<testsuite><testcase></testsuite>",
        "<testsuite/><testsuite/>",
        "<bad:testsuite/>",
        "<testsuite><testcase name='x' name='y'/></testsuite>",
        "<testsuite><testcase line='-1'/></testsuite>",
    ] {
        assert!(
            import_report(
                "junit",
                xml.as_bytes(),
                &provenance(),
                &ImportOptions::default()
            )
            .is_err(),
            "{xml}"
        );
    }
    for text in [
        "DA:1,3\n",
        "SF:x.py\nDA:0,1\nend_of_record\n",
        "SF:x.py\nDA:1,-1\nend_of_record\n",
        "SF:x.py\nDA:1,1\n",
        "end_of_record\n",
    ] {
        assert!(
            import_report(
                "lcov",
                text.as_bytes(),
                &provenance(),
                &ImportOptions::default()
            )
            .is_err()
        );
    }
}
