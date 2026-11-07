//! Historical producer metadata travels alongside immutable native finding identities.
use crate::{ValidationKind, ValidationPlan, sanitizer::SanitizerDiagnostic};
use ici_domain::{AnalysisOutput, Finding, InspectionTarget};
use serde_json::json;

pub(crate) fn quality(item: &Finding, target: &InspectionTarget, output: &mut AnalysisOutput) {
    let slow = item.rule_id == "ici.test.slow-test";
    let explanation = if slow {
        "Pytest reported a test setup, call, or teardown duration at or above the configured deep-profile threshold."
    } else {
        "The same pytest node produced different terminal verdicts across the bounded repeat runs requested by the deep profile."
    };
    let remediation = if slow {
        "Profile the test and reduce fixture or assertion work, or raise the threshold deliberately when the duration is expected."
    } else {
        "Remove order, time, or external-state dependence so repeated executions produce one deterministic verdict."
    };
    let metrics = target
        .metrics
        .iter()
        .filter(|(_, value)| value.is_number())
        .map(|(name, value)| {
            let unit = match name.as_str() {
                "duration" | "threshold" => "seconds",
                "runs" => "runs",
                "passes" | "failures" => "tests",
                _ => "",
            };
            (name.clone(), json!({"value":value,"unit":unit}))
        })
        .collect::<serde_json::Map<_, _>>();
    output.extra["finding_details"][&item.fingerprint] = json!({
        "legacy_rule_id":item.rule_id,"legacy_category":"test","legacy_confidence":"high",
        "legacy_severity":item.severity,"legacy_label":target.target_name,
        "tool_name":"pytest","tool_rule_id":if slow{"pytest --durations=0"}else{"pytest repeat"},
        "explanation":explanation,"remediation":remediation,"metrics":metrics
    });
}

pub(crate) fn binary(item: &Finding, output: &mut AnalysisOutput) {
    output.extra["finding_details"][&item.fingerprint] = json!({
        "legacy_rule_id":item.rule_id,"legacy_category":"compatibility","legacy_confidence":"exact",
        "legacy_severity":"high","legacy_label":"","tool_name":"readelf","tool_rule_id":item.native_rule_id,
        "explanation":"The linked binary contract is incompatible with the configured deployment policy.",
        "remediation":"Relink with compatible dependencies, ABI floors, and loader paths."
    });
}

pub(crate) fn sanitizer(
    plan: &ValidationPlan,
    diagnostic: &SanitizerDiagnostic,
    label: &str,
    output: &mut AnalysisOutput,
) {
    let remediation = match diagnostic.kind.as_str() {
        "tsan" => {
            "Synchronize every access to the shared state and add a deterministic multithreaded regression test that runs under TSan."
        }
        "lsan" => {
            "Release the allocation on every ownership path or transfer ownership explicitly."
        }
        "ubsan" => {
            "Remove the reported undefined operation and add a regression test for the input."
        }
        _ => "Fix the invalid memory access and add a regression test that runs under ASan.",
    };
    let family = if plan.kind == ValidationKind::ThreadSanitizer {
        "thread-sanitize"
    } else {
        "sanitize"
    };
    output.extra["finding_details"][&diagnostic.finding.fingerprint] = json!({
        "legacy_rule_id":format!("ici.legacy.{family}.target"),
        "legacy_category":if diagnostic.kind=="lsan"{"resource"}else{"correctness"},
        "legacy_confidence":"exact","legacy_severity":"high","legacy_label":label,
        "tool_name":diagnostic.tool_name,"tool_rule_id":format!("{}.{}",diagnostic.kind,diagnostic.defect),
        "explanation":"Normalized from a bounded runtime-sanitizer report and linked to its recorded process invocation.",
        "remediation":remediation,
        "related_locations":diagnostic.related_locations
    });
}

pub(crate) fn sanitizer_label(kind: &str) -> &'static str {
    match kind {
        "lsan" => "LSan Error",
        "tsan" => "TSan Error",
        _ => "ASan/UBSan Error",
    }
}
