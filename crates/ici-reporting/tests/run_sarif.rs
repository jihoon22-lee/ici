//! Native SARIF of stored run results against frozen v0.13.0 `ici.reporting.sarif.document`
//! (`tests/rust-parity/reporting/run-sarif-oracle.json`, `capture_run_sarif.py`): every run
//! fixture plus comparable/incompatible baselines, a suppressed finding, a bare span and an
//! unknown severity. Every frozen field is carried; the recorded native differences are the
//! code-point column kind with exclusive end columns, no `uriBaseId`, the native exit code of a
//! cancelled run and the complete saved baseline instead of its count summary.
use ici_reporting::{RenderOptions, StoredResult, sarif_document};
use serde_json::{Value, json};

fn frozen_baseline(native: &Value) -> Value {
    if native.is_null() {
        return json!({"state":"none"});
    }
    let mut summary = json!({"state":native["state"],"origin":native["origin"]});
    if native["state"] == "comparable" {
        let size = |key: &str| native[key].as_array().map_or(0, Vec::len);
        summary["counts"] = json!({"new":size("new"),"unchanged":size("unchanged"),
            "resolved":size("resolved"),"carried":size("carried")});
    } else {
        summary["reason"] = native["reason"].clone();
    }
    summary
}

#[test]
fn run_sarif_carries_every_frozen_field() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/run-sarif-oracle.json"
    ))
    .unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 11);
    for (name, case) in cases {
        let stored = StoredResult::from_bytes(&serde_json::to_vec(&case["input"]).unwrap())
            .unwrap_or_else(|error| panic!("{name}: {error}"));
        let native = sarif_document(&stored, &RenderOptions::default()).unwrap();
        let frozen = &case["sarif"];
        assert_eq!(native["$schema"], frozen["$schema"], "{name}");
        assert_eq!(native["version"], frozen["version"], "{name}");
        let (run, expected) = (&native["runs"][0], &frozen["runs"][0]);
        for key in ["name", "version", "informationUri"] {
            assert_eq!(
                run["tool"]["driver"][key], expected["tool"]["driver"][key],
                "{name}"
            );
        }
        let rules = run["tool"]["driver"]["rules"].as_array().unwrap();
        let frozen_rules = expected["tool"]["driver"]["rules"].as_array().unwrap();
        assert_eq!(rules.len(), frozen_rules.len(), "{name}");
        for (rule, frozen_rule) in rules.iter().zip(frozen_rules) {
            assert_eq!(rule["id"], frozen_rule["id"], "{name}");
            assert_eq!(
                rule["defaultConfiguration"],
                frozen_rule["defaultConfiguration"]
            );
            for key in ["provider", "nativeRuleId", "tags"] {
                assert_eq!(
                    rule["properties"][key], frozen_rule["properties"][key],
                    "{name}"
                );
            }
        }
        let invocation = &run["invocations"][0];
        let frozen_invocation = &expected["invocations"][0];
        assert_eq!(
            invocation["executionSuccessful"],
            frozen_invocation["executionSuccessful"]
        );
        assert_eq!(
            invocation["properties"], frozen_invocation["properties"],
            "{name}"
        );
        if case["input"]["execution"]["cancelled"] == true {
            // Native exits 130 for a cancelled run and reports that exit; frozen the gate's 3.
            assert_eq!(
                (
                    invocation["exitCode"].clone(),
                    frozen_invocation["exitCode"].clone()
                ),
                (json!(130), json!(3))
            );
        } else {
            assert_eq!(
                invocation["exitCode"], frozen_invocation["exitCode"],
                "{name}"
            );
        }
        assert_eq!(run["columnKind"], "unicodeCodePoints");
        assert_eq!(expected["columnKind"], "utf16CodeUnits");
        for key in [
            "ici.schemaId",
            "policyDigest",
            "toolchainDigest",
            "sourceDigest",
            "scopeKind",
            "selectedComponents",
            "gate",
        ] {
            assert_eq!(
                run["properties"][key], expected["properties"][key],
                "{name}: {key}"
            );
        }
        assert_eq!(
            frozen_baseline(&run["properties"]["baseline"]),
            expected["properties"]["baseline"],
            "{name}"
        );
        let results = run["results"].as_array().unwrap();
        let frozen_results = expected["results"].as_array().unwrap();
        assert_eq!(results.len(), frozen_results.len(), "{name}");
        for (result, frozen_result) in results.iter().zip(frozen_results) {
            for key in [
                "ruleId",
                "level",
                "message",
                "partialFingerprints",
                "baselineState",
            ] {
                assert_eq!(result[key], frozen_result[key], "{name}: {key}");
            }
            match (
                result.get("suppressions"),
                frozen_result.get("suppressions"),
            ) {
                (None, None) => {}
                (Some(native), Some(frozen)) => {
                    for key in ["kind", "justification", "properties"] {
                        assert_eq!(native[0][key], frozen[0][key], "{name}: suppression {key}");
                    }
                }
                other => panic!("{name}: suppressions {other:?}"),
            }
            let location = &result["locations"][0]["physicalLocation"];
            let frozen_location = &frozen_result["locations"][0]["physicalLocation"];
            assert_eq!(
                location["artifactLocation"]["uri"],
                frozen_location["artifactLocation"]["uri"]
            );
            let (region, frozen_region) = (&location["region"], &frozen_location["region"]);
            for key in ["startLine", "startColumn", "endLine"] {
                assert_eq!(region[key], frozen_region[key], "{name}: {key}");
            }
            // Native SARIF end columns are exclusive (inclusive saved column + 1).
            assert_eq!(
                region["endColumn"].as_u64(),
                frozen_region["endColumn"].as_u64().map(|column| column + 1),
                "{name}"
            );
            for (key, frozen_value) in frozen_result["properties"].as_object().unwrap() {
                assert_eq!(
                    &result["properties"][key], frozen_value,
                    "{name}: properties.{key}"
                );
            }
        }
    }
}
