//! Historical suite facts every frozen renderer printed beside the engine table: per-engine
//! score/cache/duration/issue counts, the status tally, the gate reason, the capability
//! snapshot and the issues-first baseline delta (frozen `baseline_view` selection).
use crate::{projection::engine_required, safe::plain, view::View};
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn float(value: &Value) -> Option<f64> {
    value.as_f64().filter(|value| value.is_finite())
}
/// Frozen `format_score_display`.
pub(crate) fn score_display(engine: &Value) -> String {
    match (float(&engine["score"]), float(&engine["max_score"])) {
        (Some(score), Some(maximum)) => format!("{score:.2} / {maximum:.2}"),
        (Some(score), None) => format!("{score:.2}"),
        _ => engine["extra"]
            .get("metrics_summary")
            .map_or_else(|| "-".into(), plain),
    }
}
fn duration_display(value: &Value) -> String {
    match float(value) {
        Some(seconds) if seconds > 0.0 => format!("{seconds:.2}s"),
        _ => "-".into(),
    }
}
fn delta_count(baseline: &Value, state: &str) -> usize {
    baseline["entries"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter(|entry| {
            entry["state"]
                .as_str()
                .is_some_and(|value| value.eq_ignore_ascii_case(state))
        })
        .count()
}
/// Frozen `gate_reason`: why the historical suite landed on its status.
pub(crate) fn gate_reason(document: &Value, engines: &[Value]) -> String {
    for engine in engines {
        if !engine_required(engine) || engine["evidence"] == "NOT_APPLICABLE" {
            continue;
        }
        let name = plain(&engine["engine_name"]);
        if engine["evidence"] == "NOT_RUN" {
            return format!("required engine '{name}' did not run (evidence NOT_RUN)");
        }
        if let Some(status @ ("ERROR" | "SKIP")) = engine["status"].as_str() {
            return format!("required engine '{name}' reported {status}");
        }
    }
    if let Some(engine) = engines
        .iter()
        .find(|engine| engine_required(engine) && engine["status"] == "FAIL")
    {
        return format!("required engine '{}' failed", plain(&engine["engine_name"]));
    }
    let baseline = &document["baseline_comparison"];
    if baseline["gate_failed"] == true {
        return format!(
            "baseline gate found {} new or regressed actionable finding(s)",
            plain(&baseline["gated_count"])
        );
    }
    let warned: Vec<_> = engines
        .iter()
        .filter(|engine| engine["status"] == "WARN")
        .collect();
    if !warned.is_empty() {
        let names: Vec<_> = warned
            .iter()
            .take(3)
            .map(|engine| plain(&engine["engine_name"]))
            .collect();
        let more = if warned.len() > 3 {
            format!(" (+{} more)", warned.len() - 3)
        } else {
            String::new()
        };
        return format!(
            "{} engine(s) warned: {}{more}",
            warned.len(),
            names.join(", ")
        );
    }
    if document["suite_status"] == "PASS" {
        "all applicable engines passed".into()
    } else {
        "see engine results".into()
    }
}
/// Per-engine rows plus the suite tally; `None` for a modern run without legacy engines.
pub(crate) fn overview(view: &View, issue_counts: &BTreeMap<String, usize>) -> Option<Value> {
    if view.engines.is_empty() {
        return None;
    }
    let engines: Vec<Value> = view
        .engines
        .iter()
        .map(|engine| {
            let name = plain(&engine["engine_name"]);
            json!({"engine":name,"status":engine["status"],"summary":engine["summary"],
                "issues":issue_counts.get(&name).copied().unwrap_or(0),
                "score_or_metrics":score_display(engine),
                "cache":if engine["cache_hit"] == true {"HIT"} else {"—"},
                "duration":duration_display(&engine["duration"])})
        })
        .collect();
    let document = &view.document;
    if !document["results"].is_array() {
        return Some(json!({"engines":engines}));
    }
    let number = |key: &str| document[key].as_u64().unwrap_or(0);
    let tem = float(&document["tem_score"]).map(|tem| {
        format!(
            "{tem:.2} / {:.1}",
            float(&document["max_tem_score"]).unwrap_or(5.0)
        )
    });
    Some(json!({"engines":engines,"tally":{
        "total":number("total_count"),"pass":number("passed_count"),"warn":number("warned_count"),
        "fail":number("failed_count").saturating_sub(number("error_count")),"error":number("error_count"),
        "skip":number("skipped_count"),"tem":tem,
        "cache_hits":view.engines.iter().filter(|engine| engine["cache_hit"] == true).count(),
        "total_time":format!("{:.2}s",float(&document["duration"]).unwrap_or(0.0))},
        "suite_status":document["suite_status"],
        "gate_reason":gate_reason(document, &view.engines),
        "capability_snapshot":capabilities(&document["capability_inventory"])}))
}
fn capabilities(inventory: &Value) -> Value {
    if !inventory.is_object() {
        return Value::Null;
    }
    let required: Vec<String> = inventory["tools"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter(|tool| tool["required"] == true)
        .map(|tool| format!("{}={}", plain(&tool["name"]), plain(&tool["state"])))
        .collect();
    json!({"health":if inventory["healthy"] == true {"READY"} else {"ATTENTION"},
        "counts":inventory["counts"],
        "required_policy":if required.is_empty() {"none".into()} else {required.join(", ")},
        "missing_required":inventory["missing_required"],
        "incomplete_required":inventory["incomplete_required"]})
}
pub(crate) fn location_text(location: &Value) -> String {
    if !location.is_object() {
        return "—".into();
    }
    let start = location["start_line"].as_u64().unwrap_or(1);
    let mut line = format!("L{start}");
    if let Some(end) = location["end_line"].as_u64().filter(|end| *end > start) {
        line.push_str(&format!("-L{end}"));
    }
    format!("{}:{line}", plain(&location["path"]))
}
/// Frozen `select_baseline_details` order: gated, state, engine, fingerprint, location.
pub(crate) fn delta_order(entry: &Value) -> impl Ord + use<> {
    let state = entry["state"].as_str().unwrap_or("").to_ascii_lowercase();
    let location = if entry["current_location"].is_object() {
        &entry["current_location"]
    } else {
        &entry["baseline_location"]
    };
    (
        entry["gated"] != true,
        match state.as_str() {
            "new" => 0,
            "moved" => 1,
            "resolved" => 2,
            "carried" => 3,
            "unchanged" => 4,
            _ => 99,
        },
        entry["engine_name"].as_str().unwrap_or("").to_owned(),
        entry["fingerprint"].as_str().unwrap_or("").to_owned(),
        location["path"].as_str().unwrap_or("").to_owned(),
        location["start_line"].as_u64().unwrap_or(0),
        location["end_line"].as_u64().unwrap_or(0),
        location["label"].as_str().unwrap_or("").to_owned(),
    )
}
/// Counts and fail-on-new label of a saved baseline (historical delta or modern lists).
pub(crate) fn baseline_summary(baseline: &Value) -> Value {
    if baseline["entries"].is_array() {
        let label = if baseline["gate_failed"] == true {
            "FAILED"
        } else if baseline["fail_on_new"] == true {
            "PASSED"
        } else {
            "NOT ENFORCED"
        };
        let mut summary = json!({"new":delta_count(baseline,"new"),"unchanged":delta_count(baseline,"unchanged"),
            "moved":delta_count(baseline,"moved"),"resolved":delta_count(baseline,"resolved"),
            "regressed":baseline["regressed_count"],"gated":baseline["gated_count"],
            "fail_on_new_gate":label,"total_entries":baseline["entries"].as_array().map_or(0, Vec::len)});
        // A native-derived delta also keeps the fingerprints it could not prove resolved.
        if baseline.get("carried_count").is_some() {
            summary["carried"] = json!(delta_count(baseline, "carried"));
        }
        summary
    } else {
        let size = |key: &str| baseline[key].as_array().map_or(0, Vec::len);
        json!({"new":size("new"),"unchanged":size("unchanged"),"resolved":size("resolved"),
            "carried":size("carried"),"compared_against":baseline["origin"]})
    }
}
