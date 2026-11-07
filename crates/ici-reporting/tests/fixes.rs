use ici_reporting::{RenderOptions, StoredResult, sarif_document};
use serde_json::{Value, json};
fn stored(fixes: Value) -> StoredResult {
    let mut document: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/legacy-v3-suite.json"
    ))
    .unwrap();
    document["results"][0]["findings"]
        .as_array_mut()
        .unwrap()
        .truncate(1);
    document["results"][0]["findings"][0]["fixes"] = fixes;
    StoredResult::from_value(document).unwrap()
}
fn replacement(path: &str, inserted: &str) -> Value {
    json!({"path":path,"start_line":12,"start_column":5,"end_line":12,"end_column":9,"replacement":inserted,"column_kind":"unicodeCodePoints","end_exclusive":true})
}
#[test]
fn confirmed_unicode_exclusive_edits_export_as_one_atomic_multifile_fix() {
    let stored = stored(
        json!([{"description":"replace <NULL>","replacements":[replacement("src/main.cpp","nullptr"),replacement("src/other.cpp","")]}]),
    );
    let document = sarif_document(&stored, &RenderOptions::default()).unwrap();
    let fixes = &document["runs"][0]["results"][0]["fixes"];
    assert_eq!(fixes.as_array().unwrap().len(), 1);
    let changes = &fixes[0]["artifactChanges"];
    assert_eq!(changes.as_array().unwrap().len(), 2);
    assert_eq!(
        changes[0]["replacements"][0]["deletedRegion"],
        json!({"startLine":12,"startColumn":5,"endLine":12,"endColumn":9})
    );
    assert_eq!(changes[1]["replacements"][0]["insertedContent"]["text"], "");
}
#[test]
fn unconfirmed_native_edits_remain_literal_and_mixed_fixes_are_not_partially_exported() {
    let mut unknown = replacement("src/unknown.cpp", "x");
    unknown.as_object_mut().unwrap().remove("column_kind");
    let stored = stored(
        json!([{"description":"all replacements belong together","replacements":[replacement("src/main.cpp","0"),unknown]}]),
    );
    let document = sarif_document(&stored, &RenderOptions::default()).unwrap();
    let result = &document["runs"][0]["results"][0];
    assert!(result.get("fixes").is_none());
    assert_eq!(
        result["properties"]["fixes"][0]["replacements"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert!(
        result["properties"]["fixLimitations"][0]
            .as_str()
            .unwrap()
            .contains("not explicitly confirmed")
    );
    let mut bad = replacement("../outside.cpp", "x");
    assert!(
        sarif_document(
            &stored_with_replacement(bad.clone()),
            &RenderOptions::default()
        )
        .is_err()
    );
    bad["path"] = json!("src/x.cpp");
    bad["end_column"] = json!(3);
    assert!(sarif_document(&stored_with_replacement(bad), &RenderOptions::default()).is_err());
}
fn stored_with_replacement(value: Value) -> StoredResult {
    stored(json!([{"description":"bad edit","replacements":[value]}]))
}
