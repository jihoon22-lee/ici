//! TEM formula boundaries against the frozen `application/tem.py` `calculate` and the legacy
//! engine wrapper `coverage_support.calculate_tem`
//! (tests/rust-parity/validation/capture_tem_boundaries.py). Tool-free.
use ici_domain::tem::{TemInputs, calculate};
use serde_json::{Value, json};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/tem-boundaries-oracle.json");

fn inputs(value: &Value) -> TemInputs {
    TemInputs {
        passed: value["passed"].as_u64().unwrap(),
        total: value["total"].as_u64().unwrap(),
        line_coverage: value["line_coverage"].as_f64(),
        branch_coverage: value["branch_coverage"].as_f64(),
        function_coverage: value["function_coverage"].as_f64(),
        estimated_coverage: value["estimated_coverage"].as_f64(),
    }
}

/// Positive, negative and failure boundaries of the versioned formula: every grid case (zero
/// and partial counts, the 80 % cap, branch x 1.25, missing line/branch/function evidence,
/// the estimated fallback, score/pass-rate rounding, passed > total) equals frozen in every
/// result field; the guarded validation wrapper agrees on these in-range inputs.
#[test]
fn tem_formula_matches_the_frozen_grid_in_every_field() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let cases = oracle["formula"].as_array().unwrap();
    assert!(cases.len() > 1000);
    let mut refused = 0;
    for case in cases {
        let input = inputs(&case["inputs"]);
        let native = calculate(&input);
        let frozen = &case["result"];
        let actual = json!({
            "score": native.score, "formula": native.formula, "pass_rate": native.pass_rate,
            "coverage_label": native.coverage_label, "coverage_shown": native.coverage_shown,
            "estimated": native.estimated, "reason": native.reason,
        });
        assert_eq!(&actual, frozen, "{}", case["inputs"]);
        assert_eq!(ici_validation::tem::calculate(&input).score, native.score);
        refused += usize::from(native.score.is_none());
    }
    assert!(refused > 0, "the grid covers refused inputs");
}

/// The legacy engine's TEM: with measured line or branch totals, native's legacy inputs
/// (coverage rows rounded to one decimal, function coverage unrounded, no estimate) give
/// frozen's score, label, shown coverage and pass rate. Without measured totals frozen scored
/// its invented estimate (` (est)`); native computes no score (validation.json
/// `finding_site:src/ici/engines/test.py:352:12`, no fabricated TEM).
#[test]
fn legacy_tem_matches_measured_totals_and_refuses_frozen_estimates() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let (mut measured, mut estimated) = (0, 0);
    for case in oracle["legacy"].as_array().unwrap() {
        let given = &case["inputs"];
        let totals = &given["coverage_totals"];
        let input = ici_validation::tem::legacy_inputs(&TemInputs {
            passed: given["passed"].as_u64().unwrap(),
            total: given["total"].as_u64().unwrap(),
            line_coverage: totals["cover"].as_f64(),
            branch_coverage: totals["branch_cover"].as_f64(),
            function_coverage: given["func_cov"].as_f64(),
            estimated_coverage: None,
        });
        let native = ici_validation::tem::calculate(&input);
        let frozen = &case["result"];
        if frozen["cov_suffix"] == " (est)" {
            estimated += 1;
            assert_eq!(native.score, None, "{given}");
            assert!(native.reason.starts_with("no coverage evidence"), "{given}");
            continue;
        }
        measured += 1;
        assert_eq!(native.score, frozen["tem_score"].as_f64(), "{given}");
        assert_eq!(native.coverage_label, frozen["cov_label"], "{given}");
        assert_eq!(
            native.coverage_shown,
            frozen["cov_shown"].as_f64(),
            "{given}"
        );
        assert_eq!(json!(native.pass_rate), frozen["pass_rate"], "{given}");
    }
    assert!(measured > 0 && estimated > 0);
}
