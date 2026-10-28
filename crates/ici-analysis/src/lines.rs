use crate::finding::sha1_short;
use crate::source::SourceDocument;
use crate::{failure, finish_status, number_option};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, Finding, InspectionTarget, Measurement, SourceSpan,
};
use serde_json::json;

pub fn analyze(
    request: &AnalysisRequest,
    output: &mut AnalysisOutput,
    cancellation: Option<&std::sync::atomic::AtomicBool>,
) {
    let warn = (!request
        .options
        .get("warn_limit")
        .is_some_and(serde_json::Value::is_null))
    .then(|| number_option(request, "warn_limit", 500));
    let fail = (!request
        .options
        .get("fail_limit")
        .is_some_and(serde_json::Value::is_null))
    .then(|| number_option(request, "fail_limit", 1000));
    let legacy = request
        .options
        .get("legacy_projection")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let source_paths = request
        .options
        .get("source_paths")
        .and_then(serde_json::Value::as_array);
    let gated_paths = request
        .options
        .get("gated_paths")
        .and_then(serde_json::Value::as_array);
    let selected = |list: Option<&Vec<serde_json::Value>>, path: &str| {
        list.is_none_or(|items| items.iter().any(|item| item.as_str() == Some(path)))
    };
    let (mut all_code, mut all_comment, mut all_blank) = (0u64, 0u64, 0u64);
    let (mut code, mut comment, mut blank, mut counted) = (0u64, 0u64, 0u64, 0u64);
    let mut rows = Vec::new();
    for input in &request.inputs {
        if crate::cancelled(output, cancellation) {
            break;
        }
        let source = match SourceDocument::decode(input) {
            Ok(source) => source,
            Err(error) => {
                failure(output, &input.path, &error);
                continue;
            }
        };
        let in_scope = !legacy || selected(source_paths, &input.path);
        let gated = in_scope && (!legacy || selected(gated_paths, &input.path));
        let hash_comments = [".py", ".pyi", ".sh", ".csh", ".toml", ".yml", ".yaml"]
            .iter()
            .any(|suffix| input.path.ends_with(suffix));
        let (mut file_code, mut file_comment, mut file_blank) = (0u64, 0u64, 0u64);
        for (index, line) in source.lines().enumerate() {
            if index % 256 == 0 && crate::cancelled(output, cancellation) {
                break;
            }
            let text = line.trim();
            if text.is_empty() {
                file_blank += 1;
            } else if hash_comments && text.starts_with('#') {
                file_comment += 1;
            } else {
                file_code += 1;
            }
        }
        if crate::cancelled(output, cancellation) {
            break;
        }
        all_code += file_code;
        all_comment += file_comment;
        all_blank += file_blank;
        if in_scope {
            code += file_code;
            comment += file_comment;
            blank += file_blank;
            counted += 1;
        }
        let mut target = InspectionTarget::at(&source.path, 1);
        let total = file_code + file_comment + file_blank;
        target.end_line = Some(total.max(1) as u32);
        target.target_name = "Python".into();
        target.metrics.extend([
            ("code".into(), json!(file_code)),
            ("comment".into(), json!(file_comment)),
            ("blank".into(), json!(file_blank)),
            ("total".into(), json!(total)),
        ]);
        let oversized = gated && fail.is_some_and(|fail| file_code > u64::from(fail));
        let warned = gated && warn.is_some_and(|warn| file_code > u64::from(warn));
        let fail = fail.unwrap_or(u32::MAX);
        let warn = warn.unwrap_or(u32::MAX);
        target.status = if oversized {
            "FAIL"
        } else if warned {
            "WARN"
        } else {
            "PASS"
        }
        .into();
        target.message = if oversized {
            format!(
                "Pure code lines ({file_code}) exceed {fail} lines limit (Refactoring required)"
            )
        } else if warned {
            format!(
                "Pure code lines ({file_code}) exceed {warn} lines threshold (Split recommended)"
            )
        } else {
            format!("{file_code} code lines")
        };
        if oversized || warned {
            let mut finding = Finding::new(
                "line.file-size",
                &target.message,
                SourceSpan::line(&source.path, 1),
                "ici.line",
            );
            finding.fingerprint = sha1_short("line-", &source.path);
            finding.severity = if oversized { "high" } else { "medium" }.into();
            finding.task_id = Some(request.task_id.clone());
            finding.component_id = Some(request.component_id.clone());
            output.observation.findings.push(finding);
        }
        rows.push(json!({"path":source.path,"lang":"Python","code":file_code,"comment":file_comment,"blank":file_blank,"total":total,"status":target.status,"scope":if in_scope { "source" } else { "extra" }}));
        output.targets.push(target);
    }
    let total = code + comment + blank;
    for (name, value, denominator, unit) in [
        ("lines_total", total, 0, "lines"),
        ("lines_code", code, total, "lines"),
        ("lines_comment", comment, total, "lines"),
        ("lines_blank", blank, total, "lines"),
        (
            "files_counted",
            counted,
            request.inputs.len() as u64,
            "files",
        ),
    ] {
        let mut measurement = Measurement::measured(name, value as f64);
        measurement.unit = unit.into();
        if denominator > 0 {
            measurement.numerator = Some(value);
            measurement.denominator = Some(denominator);
        }
        output.observation.measurements.push(measurement);
    }
    rows.sort_by_key(|row| std::cmp::Reverse(row["code"].as_u64().unwrap_or(0)));
    let top: Vec<_> = rows.iter().take(5).cloned().collect();
    output.extra = json!({"code":code,"comment":comment,"blank":blank,"total":total,"files_data":rows,"top_files":top,"top_files_all":top,"all":{"code":all_code,"comment":all_comment,"blank":all_blank,"total":all_code+all_comment+all_blank,"files":rows.len()},"metrics_summary":format!("{code} code / {counted} files")});
    output.summary = format!(
        "Total {total} lines ({code} code, {comment} comment, {blank} blank) across {counted} files"
    );
    crate::cancelled(output, cancellation);
    finish_status(output, request);
}
