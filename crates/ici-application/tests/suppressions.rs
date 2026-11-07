use ici_application::suppressions::{SuppressionPolicy, fnmatch_regex};
use ici_config::Suppression;
use ici_domain::{Finding, SourceSpan};
use serde_json::Value;
#[test]
fn frozen_python_fnmatchcase_path_semantics_are_preserved() {
    let data: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/suppressions/fnmatch.json"
    ))
    .unwrap();
    for case in data["cases"].as_array().unwrap() {
        let pattern = case["pattern"].as_str().unwrap();
        let path = case["path"].as_str().unwrap();
        assert_eq!(
            fnmatch_regex(pattern).unwrap().is_match(path),
            case["matches"].as_bool().unwrap(),
            "{pattern:?}, {path:?}"
        );
    }
}
#[test]
fn selectors_are_conjunctive_and_first_matching_reason_wins_without_removing_finding() {
    let mut finding = Finding::new(
        "python.dead.unused",
        "unused",
        SourceSpan::line("src/nested/é.py", 3),
        "ici",
    );
    finding.fingerprint = "fingerprint".into();
    finding.component_id = Some("app".into());
    let base = Suppression {
        fingerprint: String::new(),
        rule: "python.dead".into(),
        path: "*.py".into(),
        component: "app".into(),
        reason: "first accepted exception".into(),
        declared_in: "ici.toml:7".into(),
    };
    let mut wrong = base.clone();
    wrong.fingerprint = "other".into();
    let mut later = base.clone();
    later.reason = "later".into();
    SuppressionPolicy::new(&[wrong, base, later])
        .unwrap()
        .apply(&mut finding);
    assert!(finding.suppression.suppressed);
    assert_eq!(finding.suppression.reason, "first accepted exception");
    assert_eq!(finding.suppression.origin, "ici.toml:7");
    assert_eq!(finding.primary_location.start_line, 3);
    let mut unrelated = finding.clone();
    unrelated.rule_id = "python.deadly".into();
    unrelated.suppression = Default::default();
    SuppressionPolicy::new(&[Suppression {
        fingerprint: String::new(),
        rule: "python.dead".into(),
        path: String::new(),
        component: String::new(),
        reason: "reason".into(),
        declared_in: "ici.toml".into(),
    }])
    .unwrap()
    .apply(&mut unrelated);
    assert!(!unrelated.suppression.suppressed);
}
