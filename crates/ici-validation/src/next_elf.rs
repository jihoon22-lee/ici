//! Frozen next binary-compat step contract (`adapters/providers/binarycompat.py` and the
//! `adapters/providers/base.observe` order) for the per-ELF steps of
//! `application/testing.expand_cpp_binary_compat`: an unfinished run is never parsed, a
//! non-zero exit and an incomplete transcript fail with frozen's wording, and a parsed
//! artifact reports frozen's ELF facts limitation, one `binary.checked` metric and the
//! fixed default loader policy's `ici.binary.*` findings.
use crate::{ValidationError, ValidationPlan, planning};
use ici_domain::{AnalysisOutput, EvidenceLevel, Finding, Measurement, SourceSpan};
use ici_execution::TaskOutcome;
use std::collections::BTreeMap;

pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let step = crate::next_suite::step_id(&plan.context);
    let relative = plan
        .context
        .targets
        .first()
        .ok_or_else(|| ValidationError("frozen next ELF step names no artifact".into()))?;
    let outcome = outcomes
        .get(&step)
        .ok_or_else(|| ValidationError(format!("ELF step {step} absent")))?;
    if let Some(reason) = unfinished(&step, outcome) {
        return Err(ValidationError(reason));
    }
    let code = outcome.exit_code.unwrap_or_default();
    if code != 0 {
        let stderr = String::from_utf8_lossy(&outcome.stderr);
        let detail = first_line(&stderr);
        return Err(ValidationError(format!(
            "readelf exited {code} for {relative}{}",
            detail
                .map(|line| format!(": {}", line.chars().take(200).collect::<String>()))
                .unwrap_or_default()
        )));
    }
    let text = String::from_utf8_lossy(&outcome.stdout);
    let facts = ici_cpp::parse_readelf(&text).map_err(|error| {
        ValidationError(format!(
            "readelf output for {relative} was incomplete: {}",
            parse_error(&error)
        ))
    })?;
    // Frozen `_POLICY`: only the parts that need no declaration; the build roots are the
    // declared build directories (`cli/next_path.providers`), not the workspace root.
    let policy = ici_cpp::ElfPolicy::default();
    let violations = ici_cpp::abi_violations(&facts, &policy, &plan.context.options.build_roots)
        .map_err(|error| {
            ValidationError(format!(
                "readelf output for {relative} was incomplete: {}",
                parse_error(&error)
            ))
        })?;
    for violation in violations {
        let mut item = Finding::new(
            &violation.rule_id,
            &violation.message,
            SourceSpan::line(relative, 1),
            "binary-compat",
        );
        item.task_id = Some(step.clone());
        item.component_id = Some(plan.context.component_id.clone());
        item.native_rule_id = violation.native_rule_id;
        item.severity = "high".into();
        item.confidence = "high".into();
        item.evidence = EvidenceLevel::Measured;
        item.fingerprint = legacy_fingerprint(&violation.rule_id, relative, 1);
        output.observation.findings.push(item);
    }
    let floors: Vec<String> = [
        ("glibc", &facts.glibc),
        ("glibcxx", &facts.glibcxx),
        ("cxxabi", &facts.cxxabi),
    ]
    .into_iter()
    .filter_map(|(namespace, versions)| {
        let maximum = ici_cpp::maximum_version(versions).ok()?;
        (!maximum.is_empty()).then(|| format!("{namespace}<={maximum}"))
    })
    .collect();
    output.observation.measurements.push(Measurement {
        name: "binary.checked".into(),
        value: 1.0,
        evidence: EvidenceLevel::Measured,
        unit: "artifacts".into(),
        numerator: None,
        denominator: None,
    });
    output.observation.limitations.push(format!(
        "{relative}: ELF {} {}{} — no ABI floors are declared, so the measured ranges were not judged against a deployment contract",
        facts.elf_class,
        facts.machine,
        if floors.is_empty() {
            String::new()
        } else {
            format!("; measured {}", floors.join(", "))
        }
    ));
    output.summary = format!("{relative}: ELF {} {}", facts.elf_class, facts.machine);
    Ok(())
}

/// Frozen `base._limitation` for a run that did not finish, in frozen `_reason` cause order
/// (cancelled, timed out, signalled, truncated); `None` for a finished run.
fn unfinished(step: &str, outcome: &TaskOutcome) -> Option<String> {
    let (outcome_name, detail) = if outcome.cancelled {
        ("cancelled", outcome.error.clone().unwrap_or_default())
    } else if outcome.timed_out {
        ("timed-out", "no answer within 60s".to_string())
    } else if let Some(signal) = outcome.signal {
        ("signalled", format!("killed by signal {signal}"))
    } else if outcome.truncated {
        (
            "output-truncated",
            "more than 1000000 characters of output".to_string(),
        )
    } else if !planning::completed(outcome) {
        ("start-failed", outcome.error.clone().unwrap_or_default())
    } else {
        return None;
    };
    Some(if detail.is_empty() {
        format!("{step} {outcome_name}")
    } else {
        format!("{step} {outcome_name}: {detail}")
    })
}

/// Python `text.strip().splitlines()[0]`, `None` for blank text.
fn first_line(text: &str) -> Option<&str> {
    let trimmed = text.trim();
    (!trimmed.is_empty()).then(|| {
        trimmed
            .split([
                '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
                '\u{2029}',
            ])
            .next()
            .unwrap_or(trimmed)
    })
}

/// Frozen `analysis/_elf.ElfParseError` wording for the native parse errors frozen shares.
fn parse_error(error: &ici_cpp::CppError) -> String {
    match error.code.as_str() {
        "readelf-bounds" => "readelf output exceeds the bounded text contract".into(),
        "readelf-header-missing" => "readelf output is missing ELF header facts".into(),
        _ => error.message.clone(),
    }
}

/// Frozen `core/findings.finding_fingerprint(rule_id, SourceLocation(path, line))`: sha256 of
/// the sorted compact JSON `{path, region, rule_id, symbol, version}` (no ASCII escaping).
fn legacy_fingerprint(rule: &str, path: &str, line: u32) -> String {
    let payload = serde_json::json!({
        "path": path,
        "region": {"end_column": null, "end_line": null, "start_column": null, "start_line": line},
        "rule_id": rule,
        "symbol": "",
        "version": "ici-fingerprint/v1",
    });
    ici_domain::sha256(ici_domain::canonical_json(&payload).expect("JSON value"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Frozen `finding_fingerprint("ici.binary.forbidden-rpath",
    /// SourceLocation(path="out/lib/libplug.so", start_line=1))` (oracle 6fe3e40, venv
    /// Python 3.10) and Python `strip().splitlines()`.
    #[test]
    fn frozen_raw_fingerprint_and_first_stderr_line() {
        assert_eq!(
            legacy_fingerprint("ici.binary.forbidden-rpath", "out/lib/libplug.so", 1),
            "sha256:f3ba0bca2a4d883d46e73ecdecbbde94a1a8e578ccacc91f0831382cd0cf9058"
        );
        assert_eq!(
            first_line("\n  readelf: Error: x\r\nmore\n"),
            Some("readelf: Error: x")
        );
        assert_eq!(first_line(" \n\t"), None);
    }
}
