//! Historical `ici verify --sarif` against frozen `ici/reporters/sarif.py`
//! (`tests/rust-parity/reporting/legacy-sarif-oracle.json`, `capture_legacy_sarif.py`): the
//! frozen document of every captured suite is reproduced field for field from the same saved
//! v3 suite, apart from `tool.driver.version` (the producing ici's own version).
use ici_reporting::{
    MAX_LEGACY_SARIF_RESULTS, MAX_LEGACY_SARIF_RULES, legacy_sarif_document, sarif_document,
};
use serde_json::{Value, json};

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-sarif-oracle.json"
    ))
    .unwrap()
}

fn assert_frozen(name: &str, case: &Value) {
    let native = legacy_sarif_document(&case["suite"], "0.13.0").unwrap();
    assert_eq!(
        case["sarif"]["runs"][0]["tool"]["driver"]["version"],
        "0.13.0"
    );
    if native != case["sarif"] {
        panic!(
            "{name}: native\n{}\nfrozen\n{}",
            serde_json::to_string_pretty(&native).unwrap(),
            serde_json::to_string_pretty(&case["sarif"]).unwrap()
        );
    }
}

#[test]
fn synthetic_suites_reproduce_the_frozen_document_field_for_field() {
    let oracle = oracle();
    let cases = oracle["synthetic"].as_object().unwrap();
    let mut compared = 0;
    for (name, case) in cases {
        if case.get("suite").is_some() {
            assert_frozen(name, case);
            compared += 1;
        }
    }
    assert_eq!(compared, 6);
    let edges = &cases["edges"]["sarif"]["runs"][0];
    // The fields GitHub code scanning keys alerts by stay in the historical spelling.
    let first = &edges["results"][0];
    assert!(first["fingerprints"]["ici/v3"].is_string());
    assert!(first.get("partialFingerprints").is_none());
    assert_eq!(
        first["locations"][0]["physicalLocation"]["artifactLocation"]["uriBaseId"],
        "%SRCROOT%"
    );
    assert!(edges.get("columnKind").is_none());
    let baseline = &cases["baseline"]["sarif"]["runs"][0];
    let states: Vec<_> = baseline["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|result| result["baselineState"].as_str().unwrap_or("-"))
        .collect();
    for state in ["new", "unchanged", "updated", "absent"] {
        assert!(states.contains(&state), "{states:?}");
    }
}

#[test]
fn real_frozen_verify_suites_reproduce_their_sarif() {
    let oracle = oracle();
    let cases = oracle["end_to_end"].as_object().unwrap();
    assert_eq!(cases.len(), 5);
    for (name, case) in cases {
        assert_frozen(name, case);
    }
}

#[test]
fn the_historical_shape_differs_from_the_stored_run_shape() {
    // Negative: the run-SARIF writer (analyze/next report) keys results differently, so the
    // historical command must not be routed through it.
    let oracle = oracle();
    let case = &oracle["synthetic"]["edges"];
    let stored = ici_reporting::StoredResult::from_value(case["suite"].clone()).unwrap();
    let run = sarif_document(&stored, &Default::default()).unwrap();
    let result = &run["runs"][0]["results"][0];
    assert!(result.get("fingerprints").is_none());
    assert!(result["partialFingerprints"]["ici.fingerprint"].is_string());
    assert_ne!(run, case["sarif"]);
    // A real frozen suite locates `BuildArtifacts` at the project root (`.`). The run-SARIF
    // writer keeps that result without a physical location (the saved span in
    // `properties.projectLocation`; decisions.md "project root 위치의 SARIF 결과"), while the
    // historical writer emits frozen's `uri` "." under `%SRCROOT%`: the shapes still differ.
    let suite = &oracle["end_to_end"]["python"];
    let stored = ici_reporting::StoredResult::from_value(suite["suite"].clone()).unwrap();
    let run = sarif_document(&stored, &Default::default()).unwrap();
    let root: Vec<&Value> = run["runs"][0]["results"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|result| result["properties"]["projectLocation"]["path"] == ".")
        .collect();
    assert!(!root.is_empty());
    assert!(root.iter().all(|result| result["locations"] == json!([])));
    let historical = legacy_sarif_document(&suite["suite"], "0.13.0").unwrap();
    assert!(
        historical["runs"][0]["results"]
            .as_array()
            .unwrap()
            .iter()
            .any(|result| {
                result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "."
            })
    );
}

fn bounded(findings: usize, rules: usize) -> Value {
    let findings: Vec<Value> = (0..findings)
        .map(|index| {
            json!({"rule_id":format!("ici.bound.r{}", index % rules),"category":"correctness",
                "severity":"low","confidence":"high","fingerprint":format!("sha256:{index:064}"),
                "primary_location":{"path":"src/a.py","start_line":index + 1,"end_line":null,
                    "start_column":null,"end_column":null,"label":""},
                "related_locations":[],"message":"bounded","explanation":"","remediation":"",
                "tool_rule_id":"","tool_name":"","tool_version":"",
                "suppression":{"suppressed":false,"kind":"none","reason":""},"metrics":{},
                "snippet":"","fixes":[]})
        })
        .collect();
    json!({"schema_version":"ici.result/v3","suite_status":"FAIL","baseline_comparison":null,
        "results":[{"engine_name":"lint","status":"FAIL","findings":findings}]})
}

#[test]
fn rule_and_result_bounds_refuse_with_the_frozen_messages() {
    let oracle = oracle();
    let cases = &oracle["synthetic"];
    assert_eq!(MAX_LEGACY_SARIF_RULES, 10_000);
    assert_eq!(MAX_LEGACY_SARIF_RESULTS, 100_000);
    // Positive edge: exactly 10 000 rules are written.
    let edge = &cases["rule-bound-edge"];
    let written = legacy_sarif_document(
        &bounded(
            edge["findings"].as_u64().unwrap() as usize,
            edge["rules"].as_u64().unwrap() as usize,
        ),
        "0.13.0",
    )
    .unwrap();
    assert_eq!(
        written["runs"][0]["tool"]["driver"]["rules"]
            .as_array()
            .unwrap()
            .len() as u64,
        edge["rules"].as_u64().unwrap()
    );
    assert_eq!(
        written["runs"][0]["results"].as_array().unwrap().len() as u64,
        edge["results"].as_u64().unwrap()
    );
    for name in ["rule-bound", "result-bound"] {
        let case = &cases[name];
        let refused = legacy_sarif_document(
            &bounded(
                case["findings"].as_u64().unwrap() as usize,
                case["rules"].as_u64().unwrap() as usize,
            ),
            "0.13.0",
        )
        .unwrap_err();
        assert_eq!(
            refused.to_string(),
            case["refused"].as_str().unwrap(),
            "{name}"
        );
    }
}

#[test]
fn a_native_identity_resolved_delta_is_reported_absent_instead_of_refusing_the_document() {
    // Failure mode: native legacy views carry native-identity deltas (e.g. `security.shelltrue`);
    // frozen `canonicalize_finding` would have raised on such a rule, which would refuse every
    // historical baseline SARIF. The delta stays an `absent` result under its own identity.
    let mut suite = oracle()["synthetic"]["baseline"]["suite"].clone();
    let entries = suite["baseline_comparison"]["entries"]
        .as_array_mut()
        .unwrap();
    let resolved = entries
        .iter_mut()
        .find(|entry| entry["state"] == "resolved")
        .unwrap();
    resolved["rule_id"] = json!("security.shelltrue");
    let fingerprint = resolved["fingerprint"].clone();
    let document = legacy_sarif_document(&suite, "0.13.0").unwrap();
    let absent = document["runs"][0]["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|result| result["ruleId"] == "security.shelltrue")
        .unwrap();
    assert_eq!(absent["baselineState"], "absent");
    assert_eq!(absent["fingerprints"]["ici/v3"], fingerprint);
}

fn root_finding(suite: &mut Value) -> &mut Value {
    suite["results"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .flat_map(|engine| engine["findings"].as_array_mut().unwrap().iter_mut())
        .find(|finding| finding["primary_location"]["path"] == ".")
        .unwrap()
}
fn resolved(suite: &mut Value) -> &mut Value {
    suite["baseline_comparison"]["entries"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|entry| entry["state"] == "resolved")
        .unwrap()
}

/// Run SARIF of a saved legacy report (the real frozen `python` suite, whose `ASTSyntaxFallback`
/// and `PythonRuntime:Verified` findings sit at the project root `.`): only the relative root
/// spellings are location-less, each with its saved span as `projectLocation` (primary, related
/// and a resolved baseline delta). The absolute-root family `/`, `//` and `/./` is not a project
/// root: it keeps the absolute-path refusal at every location kind instead of silently dropping
/// the location (decisions.md "project root 위치의 SARIF 결과").
#[test]
fn absolute_root_spans_are_refused_and_relative_roots_keep_their_saved_span() {
    let oracle = oracle();
    let saved = oracle["end_to_end"]["python"]["suite"].clone();
    let export = |suite: Value| {
        ici_reporting::StoredResult::from_value(suite)
            .and_then(|stored| sarif_document(&stored, &Default::default()))
    };
    let span = |path: &str| {
        json!({"path": path, "start_line": 1, "end_line": null,
        "start_column": null, "end_column": null, "label": "Root"})
    };
    let mut baseline = oracle["synthetic"]["baseline"]["suite"].clone();
    let fingerprint = resolved(&mut baseline)["fingerprint"].clone();
    for absolute in ["/", "//", "/./"] {
        let mut primary = saved.clone();
        root_finding(&mut primary)["primary_location"]["path"] = json!(absolute);
        assert!(export(primary).is_err(), "primary {absolute}");
        let mut related = saved.clone();
        root_finding(&mut related)["related_locations"] = json!([span(absolute)]);
        assert!(export(related).is_err(), "related {absolute}");
        let mut delta = baseline.clone();
        resolved(&mut delta)["baseline_location"] = span(absolute);
        assert!(export(delta).is_err(), "baseline {absolute}");
    }
    for relative in [".", "./", "./."] {
        let mut related = saved.clone();
        let finding = root_finding(&mut related);
        finding["primary_location"]["path"] = json!(relative);
        finding["related_locations"] = json!([span(relative)]);
        let label = finding["primary_location"]["label"].clone();
        let document = export(related).unwrap();
        let result = document["runs"][0]["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|result| result["properties"]["projectLocation"]["label"] == label)
            .unwrap();
        assert_eq!(result["locations"], json!([]), "{relative}");
        assert_eq!(
            result["properties"]["projectLocation"]["path"], relative,
            "{relative}"
        );
        let location = &result["relatedLocations"][0];
        assert!(location.get("physicalLocation").is_none(), "{relative}");
        assert_eq!(location["properties"]["projectLocation"], span(relative));
        let mut delta = baseline.clone();
        resolved(&mut delta)["baseline_location"] = span(relative);
        let document = export(delta).unwrap();
        let absent = document["runs"][0]["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|result| result["partialFingerprints"]["ici.fingerprint"] == fingerprint)
            .unwrap();
        assert_eq!(absent["baselineState"], "absent");
        assert_eq!(absent["locations"], json!([]), "{relative}");
        assert_eq!(absent["properties"]["projectLocation"], span(relative));
    }
}
