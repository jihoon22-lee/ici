//! Frozen `core/baseline.py::compare_suite_to_baseline` over the legacy findings of two
//! historical suites: the current legacy view and the legacy view a native `--write-baseline`
//! saved beside its `native_run`. Every legacy finding (PASS inventory included) is a record of
//! its engine; records pair by `(engine, fingerprint)`, then by location, then MOVED by sorted
//! order, and the rest are NEW or RESOLVED. The native comparison still decides whether the
//! baseline is comparable at all (ADR-0008 "readable is not comparable").
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, VecDeque};

/// Frozen coverage-regression rules (`_coverage_regression_records`): recomputed per run from the
/// coverage snapshots, never compared as saved engine findings.
pub const COVERAGE_REGRESSION_RULE: &str = "ici.test.coverage-regression.";

/// Frozen `_SEVERITY_RANK`.
fn rank(severity: &str) -> u8 {
    match severity {
        "low" => 1,
        "medium" => 2,
        "high" => 3,
        "critical" => 4,
        _ => 0,
    }
}

/// One frozen `_FindingRecord`.
#[derive(Clone, Debug)]
pub struct LegacyRecord {
    pub engine_name: String,
    pub fingerprint: String,
    pub rule_id: String,
    pub severity: String,
    /// `{path, start_line, end_line, start_column, end_column, label}`.
    pub location: Value,
    pub message: String,
    pub suppressed: bool,
}

type LocationKey = (String, u64, u64, u64, u64, String);

fn location_key(location: &Value) -> LocationKey {
    let number = |key: &str| location[key].as_u64().unwrap_or(0);
    (
        location["path"].as_str().unwrap_or_default().to_owned(),
        number("start_line"),
        number("start_column"),
        number("end_line"),
        number("end_column"),
        location["label"].as_str().unwrap_or_default().to_owned(),
    )
}

fn record_key(record: &LegacyRecord) -> (LocationKey, u8, String) {
    (
        location_key(&record.location),
        rank(&record.severity),
        record.message.clone(),
    )
}

/// Frozen `_serialize_optional_location`.
fn location(value: &Value) -> Value {
    json!({"path":value["path"],"start_line":value["start_line"],"end_line":value["end_line"],
        "start_column":value["start_column"],"end_column":value["end_column"],"label":value["label"]})
}

impl LegacyRecord {
    /// The record of one saved legacy finding of `engine_name`.
    pub fn from_finding(engine_name: &str, finding: &Value) -> Self {
        Self {
            engine_name: engine_name.to_owned(),
            fingerprint: finding["fingerprint"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            rule_id: finding["rule_id"].as_str().unwrap_or_default().to_owned(),
            severity: finding["severity"].as_str().unwrap_or("info").to_owned(),
            location: location(&finding["primary_location"]),
            message: finding["message"].as_str().unwrap_or_default().to_owned(),
            suppressed: finding["suppression"]["suppressed"] == true,
        }
    }
}

/// Every saved legacy finding of a suite's `results`, except the per-run coverage regressions.
pub fn legacy_records(results: &Value) -> Vec<LegacyRecord> {
    results
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|engine| {
            let name = engine["engine_name"].as_str().unwrap_or_default();
            engine["findings"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|finding| {
                    !finding["rule_id"]
                        .as_str()
                        .is_some_and(|rule| rule.starts_with(COVERAGE_REGRESSION_RULE))
                })
                .map(move |finding| LegacyRecord::from_finding(name, finding))
        })
        .collect()
}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum State {
    New,
    Moved,
    Unchanged,
    Resolved,
}

impl State {
    fn name(self) -> &'static str {
        match self {
            Self::New => "new",
            Self::Moved => "moved",
            Self::Unchanged => "unchanged",
            Self::Resolved => "resolved",
        }
    }
}

struct Delta {
    state: State,
    regressed: bool,
    gated: bool,
    value: Value,
    sort_location: LocationKey,
    engine_name: String,
    fingerprint: String,
}

/// Frozen `_is_actionable`.
fn actionable(record: &LegacyRecord) -> bool {
    !record.suppressed && record.severity != "info"
}

fn paired(current: &LegacyRecord, baseline: &LegacyRecord, state: State) -> Delta {
    // Frozen `_is_regressed`.
    let regressed = rank(&current.severity) > rank(&baseline.severity)
        || (baseline.suppressed && !current.suppressed);
    let gated = regressed && actionable(current);
    Delta {
        state,
        regressed,
        gated,
        value: json!({"state":state.name(),"engine_name":current.engine_name,
            "fingerprint":current.fingerprint,"rule_id":current.rule_id,"message":current.message,
            "current_location":current.location,"baseline_location":baseline.location,
            "current_severity":current.severity,"baseline_severity":baseline.severity,
            "regressed":regressed,"suppressed":current.suppressed,"gated":gated}),
        sort_location: location_key(&current.location),
        engine_name: current.engine_name.clone(),
        fingerprint: current.fingerprint.clone(),
    }
}

fn new_delta(current: &LegacyRecord) -> Delta {
    let gated = actionable(current);
    Delta {
        state: State::New,
        regressed: false,
        gated,
        value: json!({"state":"new","engine_name":current.engine_name,
            "fingerprint":current.fingerprint,"rule_id":current.rule_id,"message":current.message,
            "current_location":current.location,"baseline_location":null,
            "current_severity":current.severity,"baseline_severity":null,
            "regressed":false,"suppressed":current.suppressed,"gated":gated}),
        sort_location: location_key(&current.location),
        engine_name: current.engine_name.clone(),
        fingerprint: current.fingerprint.clone(),
    }
}

fn resolved_delta(baseline: &LegacyRecord) -> Delta {
    Delta {
        state: State::Resolved,
        regressed: false,
        gated: false,
        value: json!({"state":"resolved","engine_name":baseline.engine_name,
            "fingerprint":baseline.fingerprint,"rule_id":baseline.rule_id,"message":baseline.message,
            "current_location":null,"baseline_location":baseline.location,
            "current_severity":null,"baseline_severity":baseline.severity,
            "regressed":false,"suppressed":baseline.suppressed,"gated":false}),
        sort_location: location_key(&baseline.location),
        engine_name: baseline.engine_name.clone(),
        fingerprint: baseline.fingerprint.clone(),
    }
}

/// Frozen `_compare_group`.
fn compare_group(current: &[LegacyRecord], baseline: &[LegacyRecord]) -> Vec<Delta> {
    let mut current: Vec<&LegacyRecord> = current.iter().collect();
    current.sort_by_key(|record| record_key(record));
    let mut sorted_baseline: Vec<&LegacyRecord> = baseline.iter().collect();
    sorted_baseline.sort_by_key(|record| record_key(record));
    let mut by_location: BTreeMap<LocationKey, VecDeque<&LegacyRecord>> = BTreeMap::new();
    for record in sorted_baseline {
        by_location
            .entry(location_key(&record.location))
            .or_default()
            .push_back(record);
    }
    let mut deltas = vec![];
    let mut unmatched_current = vec![];
    for record in current {
        match by_location
            .get_mut(&location_key(&record.location))
            .and_then(VecDeque::pop_front)
        {
            Some(old) => deltas.push(paired(record, old, State::Unchanged)),
            None => unmatched_current.push(record),
        }
    }
    let mut unmatched_baseline: Vec<&LegacyRecord> = by_location.into_values().flatten().collect();
    unmatched_baseline.sort_by_key(|record| record_key(record));
    let pairs = unmatched_current.len().min(unmatched_baseline.len());
    for index in 0..pairs {
        deltas.push(paired(
            unmatched_current[index],
            unmatched_baseline[index],
            State::Moved,
        ));
    }
    deltas.extend(
        unmatched_current[pairs..]
            .iter()
            .map(|record| new_delta(record)),
    );
    deltas.extend(
        unmatched_baseline[pairs..]
            .iter()
            .map(|record| resolved_delta(record)),
    );
    deltas
}

/// The frozen `baseline_comparison` of `current` (the current legacy suite's records plus its
/// per-run coverage regressions) against the `baseline` records. `statuses` is the current
/// legacy status of every engine of the run: a baseline engine that is absent, ERROR or SKIP did
/// not look, so its baseline-only findings are left out with the frozen warning instead of
/// being reported resolved. `warnings` precede that warning (frozen order: compatibility,
/// coverage, then engines that did not look).
pub fn frozen_baseline_comparison(
    current: &[LegacyRecord],
    baseline: &[LegacyRecord],
    statuses: &BTreeMap<String, String>,
    source_path: &str,
    warnings: Vec<String>,
    baseline_metadata: Value,
    fail_on_new: bool,
) -> Value {
    let mut current_groups: BTreeMap<(String, String), Vec<LegacyRecord>> = BTreeMap::new();
    let mut baseline_groups: BTreeMap<(String, String), Vec<LegacyRecord>> = BTreeMap::new();
    for record in current {
        current_groups
            .entry((record.engine_name.clone(), record.fingerprint.clone()))
            .or_default()
            .push(record.clone());
    }
    for record in baseline {
        baseline_groups
            .entry((record.engine_name.clone(), record.fingerprint.clone()))
            .or_default()
            .push(record.clone());
    }
    // Frozen `_engines_that_did_not_look`.
    let mut unlooked: BTreeMap<String, String> = BTreeMap::new();
    for record in baseline {
        if unlooked.contains_key(&record.engine_name) {
            continue;
        }
        match statuses.get(&record.engine_name).map(String::as_str) {
            None => {
                unlooked.insert(
                    record.engine_name.clone(),
                    "was not part of this run".into(),
                );
            }
            Some(status @ ("ERROR" | "SKIP")) => {
                unlooked.insert(record.engine_name.clone(), format!("reported {status}"));
            }
            Some(_) => {}
        }
    }
    let mut skipped: BTreeMap<String, usize> = BTreeMap::new();
    let keys: BTreeSet<_> = current_groups
        .keys()
        .chain(baseline_groups.keys())
        .cloned()
        .collect();
    let empty = vec![];
    let mut deltas = vec![];
    for key in keys {
        let now = current_groups.get(&key).unwrap_or(&empty);
        let before = baseline_groups.get(&key).unwrap_or(&empty);
        if unlooked.contains_key(&key.0) && now.is_empty() {
            *skipped.entry(key.0.clone()).or_default() += before.len();
            continue;
        }
        deltas.extend(compare_group(now, before));
    }
    deltas.sort_by(|left, right| {
        (
            !left.gated,
            left.state,
            &left.engine_name,
            &left.fingerprint,
            &left.sort_location,
        )
            .cmp(&(
                !right.gated,
                right.state,
                &right.engine_name,
                &right.fingerprint,
                &right.sort_location,
            ))
    });
    let count = |state: State| deltas.iter().filter(|delta| delta.state == state).count();
    let gated_count = deltas.iter().filter(|delta| delta.gated).count();
    let regressed_count = deltas.iter().filter(|delta| delta.regressed).count();
    let mut warnings = warnings;
    warnings.extend(skipped.iter().map(|(engine, count)| {
        format!(
            "{engine} {}; {count} baseline finding(s) were not compared and are not resolved",
            unlooked[engine]
        )
    }));
    json!({
        "source_path":source_path,"warnings":warnings,"baseline_metadata":baseline_metadata,
        "fail_on_new":fail_on_new,"gate_failed":fail_on_new && gated_count > 0,
        "new_count":count(State::New),"unchanged_count":count(State::Unchanged),
        "moved_count":count(State::Moved),"resolved_count":count(State::Resolved),
        "regressed_count":regressed_count,"gated_count":gated_count,
        "entries":deltas.into_iter().map(|delta| delta.value).collect::<Vec<_>>()
    })
}

/// Frozen `_coverage_regression_records`: a `test` record per regressed coverage metric. An
/// aggregate metric is located at the project (`"."`) whatever source the native finding names;
/// a `file-line` regression at its file.
pub fn coverage_regression_record(
    kind: &str,
    path: &str,
    current: f64,
    baseline: f64,
    tolerance: f64,
) -> crate::Result<LegacyRecord> {
    let path = if kind.starts_with("aggregate-") {
        "."
    } else {
        path
    };
    let rule_id = format!("{COVERAGE_REGRESSION_RULE}{kind}");
    let label = format!("coverage:{kind}");
    let location = json!({"path":path,"start_line":1,"end_line":null,"start_column":null,
        "end_column":null,"label":label});
    Ok(LegacyRecord {
        engine_name: "test".into(),
        fingerprint: crate::migration::fingerprint(&rule_id, &location, &label)?,
        rule_id,
        severity: "medium".into(),
        location,
        message: format!(
            "Coverage regressed from {baseline:.1}% to {current:.1}% (allowed drop {tolerance:.1}%)"
        ),
        suppressed: false,
    })
}

#[cfg(test)]
mod tests {
    use super::{LegacyRecord, coverage_regression_record, frozen_baseline_comparison};
    use serde_json::{Value, json};
    use std::collections::BTreeMap;

    fn record(engine: &str, fingerprint: &str, line: u64, severity: &str) -> LegacyRecord {
        LegacyRecord::from_finding(
            engine,
            &json!({"fingerprint":fingerprint,"rule_id":format!("ici.legacy.{engine}.target"),
                "severity":severity,"message":"m","suppression":{"suppressed":false},
                "primary_location":{"path":"a.py","start_line":line,"end_line":null,
                    "start_column":null,"end_column":null,"label":"f()"}}),
        )
    }

    fn compare(current: &[LegacyRecord], baseline: &[LegacyRecord], statuses: &[&str]) -> Value {
        let statuses: BTreeMap<_, _> = statuses
            .iter()
            .map(|name| ((*name).to_owned(), "PASS".to_owned()))
            .collect();
        frozen_baseline_comparison(
            current,
            baseline,
            &statuses,
            "base.json",
            vec![],
            Value::Null,
            true,
        )
    }

    /// Frozen `_coverage_regression_records` (captured cppval case
    /// verify-baseline:coverage-breach): aggregate metrics at `"."`, a file at its path, frozen's
    /// fingerprints and one-decimal message.
    #[test]
    fn coverage_regression_records_take_frozen_locations_and_fingerprints() {
        for (kind, path, fingerprint) in [
            (
                "aggregate-line",
                ".",
                "sha256:4b175e47868a695c220b48c38f66467464e951776363660b0ae34aae591ad5d3",
            ),
            (
                "aggregate-function",
                ".",
                "sha256:6c38263a02a7070015887665927ae39cc237f63e747b8e1b252eccfabc56104e",
            ),
            (
                "file-line",
                "src/calc.cpp",
                "sha256:f5056d905a442dcd22b19095e9ae7f6c785ca7371ee212e055daacf9a9c1f5aa",
            ),
        ] {
            let record =
                coverage_regression_record(kind, "src/calc.cpp", 200.0 / 3.0, 100.0, 1.5).unwrap();
            assert_eq!(record.location["path"], path, "{kind}");
            assert_eq!(record.location["label"], format!("coverage:{kind}"));
            assert_eq!(record.fingerprint, fingerprint, "{kind}");
            assert_eq!(record.severity, "medium");
            assert_eq!(
                record.message,
                "Coverage regressed from 100.0% to 66.7% (allowed drop 1.5%)"
            );
        }
    }

    #[test]
    fn frozen_groups_pair_by_location_then_moved_then_new_and_resolved() {
        let baseline = [
            record("complexity", "sha256:a", 1, "info"),
            record("complexity", "sha256:b", 5, "low"),
            record("lint", "sha256:c", 9, "medium"),
        ];
        let current = [
            record("complexity", "sha256:a", 1, "info"),
            record("complexity", "sha256:b", 7, "high"),
            record("complexity", "sha256:b", 8, "medium"),
            record("lint", "sha256:d", 2, "info"),
        ];
        let delta = compare(&current, &baseline, &["complexity", "lint"]);
        let states: Vec<_> = delta["entries"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| {
                (
                    entry["state"].as_str().unwrap(),
                    entry["fingerprint"].as_str().unwrap(),
                    entry["gated"] == true,
                )
            })
            .collect();
        // Gated first: the MOVED severity regression (low -> high, sorted by location key so the
        // line-7 record pairs) and the extra medium occurrence of `b` (NEW, actionable).
        assert_eq!(
            states,
            [
                ("new", "sha256:b", true),
                ("moved", "sha256:b", true),
                ("new", "sha256:d", false),
                ("unchanged", "sha256:a", false),
                ("resolved", "sha256:c", false),
            ]
        );
        assert_eq!(delta["moved_count"], 1);
        assert_eq!(delta["regressed_count"], 1);
        assert_eq!(delta["gated_count"], 2);
        assert_eq!(delta["gate_failed"], true);
        assert!(delta.get("carried_count").is_none());
    }

    #[test]
    fn baseline_findings_of_engines_that_did_not_look_are_not_resolved() {
        let baseline = [
            record("lint", "sha256:c", 9, "medium"),
            record("type", "sha256:e", 3, "medium"),
        ];
        let statuses = BTreeMap::from([("lint".to_owned(), "ERROR".to_owned())]);
        let delta = frozen_baseline_comparison(
            &[],
            &baseline,
            &statuses,
            "base.json",
            vec![],
            Value::Null,
            false,
        );
        assert_eq!(delta["entries"], json!([]));
        assert_eq!(
            delta["warnings"],
            json!([
                "lint reported ERROR; 1 baseline finding(s) were not compared and are not resolved",
                "type was not part of this run; 1 baseline finding(s) were not compared and are not resolved"
            ])
        );
        assert_eq!(delta["gate_failed"], false);
    }
}
