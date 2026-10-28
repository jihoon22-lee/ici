use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, SourceSpan,
};
use sha1::{Digest, Sha1};

pub fn sha1_short(prefix: &str, value: &str) -> String {
    let digest = format!("{:x}", Sha1::digest(value.as_bytes()));
    format!("{prefix}{}", &digest[..16])
}
pub fn span(target: &InspectionTarget) -> SourceSpan {
    SourceSpan {
        path: target.file_path.clone(),
        start_line: target.start_line,
        end_line: target.end_line,
        start_column: target.start_column,
        end_column: target.end_column,
        label: String::new(),
    }
}
pub fn hygiene(
    output: &mut AnalysisOutput,
    request: &AnalysisRequest,
    target: &InspectionTarget,
    kind: &str,
    severity: &str,
    category: &str,
    confidence: &str,
) {
    let rule = target
        .target_name
        .split_once(':')
        .map_or(target.target_name.as_str(), |(_, name)| name);
    let mut finding = Finding::new(
        format!("{kind}.{}", rule.to_lowercase().replace('_', "-")),
        &target.message,
        span(target),
        "ici.hygiene",
    );
    finding.fingerprint = sha1_short(
        "hygiene-",
        &format!(
            "{}:{}:{rule}:{}",
            target.file_path, target.start_line, target.message
        ),
    );
    finding.native_rule_id = target.target_name.clone();
    finding.severity = severity.into();
    finding.confidence = confidence.into();
    finding.category = category.into();
    finding.component_id = Some(request.component_id.clone());
    finding.task_id = Some(request.task_id.clone());
    finding.evidence = EvidenceLevel::Measured;
    output.observation.findings.push(finding);
}
