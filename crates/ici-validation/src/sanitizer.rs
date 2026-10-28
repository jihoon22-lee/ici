//! Bounded native sanitizer normalization over immutable captured source coordinates.
use crate::{ValidationError, ValidationKind, ValidationPlan, planning, python::finding};
use ici_domain::{AnalysisOutput, Finding, InspectionTarget, Measurement, SourceSpan};
use ici_execution::TaskOutcome;
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, serde::Serialize)]
pub struct SanitizerDiagnostic {
    pub kind: String,
    pub tool_name: String,
    pub defect: String,
    pub rule_id: String,
    pub message: String,
    pub primary_location: Option<SourceSpan>,
    pub related_locations: Vec<SourceSpan>,
    pub frames_observed: usize,
    pub project_frames: usize,
    pub external_locations: Vec<ExternalLocation>,
    #[serde(skip)]
    pub finding: Finding,
}
#[derive(Clone, Debug, serde::Serialize)]
pub struct ExternalLocation {
    pub path: String,
    pub start_line: u32,
    pub native_column: Option<u32>,
    pub column_unit: String,
    pub label: String,
}

pub fn parse(text: &str, plan: &ValidationPlan) -> Result<Vec<Finding>, ValidationError> {
    parse_diagnostics(text, plan).map(|items| items.into_iter().map(|item| item.finding).collect())
}
pub fn parse_diagnostics(
    text: &str,
    plan: &ValidationPlan,
) -> Result<Vec<SanitizerDiagnostic>, ValidationError> {
    if text.len() > 1024 * 1024 || text.contains('\0') {
        return Err(ValidationError(
            "sanitizer transcript exceeds bounded UTF-8/NUL contract".into(),
        ));
    }
    let error=Regex::new(r"^\s*(?:==\d+==)?\s*ERROR:\s*(AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer):\s*(\S.*)$").expect("constant regex");
    let warning = Regex::new(r"^\s*(?:==\d+==)?\s*WARNING:\s*(ThreadSanitizer):\s*(\S.*)$")
        .expect("constant regex");
    let runtime = Regex::new(r"^(.+:[1-9]\d*(?::[1-9]\d*)?):\s*runtime error:\s*(\S.*)$")
        .expect("constant regex");
    let summary=Regex::new(r"^\s*(?:==\d+==)?\s*SUMMARY:\s*(AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|ThreadSanitizer):\s*(\S.*)$").expect("constant regex");
    let frame = Regex::new(r"^\s*#(\d+)\s+").expect("constant regex");
    let location = Regex::new(
        r"((?:[A-Za-z]:[\\/][^:\r\n]*|/[^:\r\n]*|(?:\.{1,2}[\\/])?[^:\s]+)):([1-9]\d*)(?::([1-9]\d*))?",
    )
    .expect("constant regex");
    let lines = text.lines().collect::<Vec<_>>();
    let mut starters = vec![];
    for (index, line) in lines.iter().enumerate() {
        if let Some(capture) = error.captures(line).or_else(|| warning.captures(line)) {
            starters.push((index, capture[1].to_string(), capture[2].to_string()));
        } else if let Some(capture) = runtime.captures(line) {
            starters.push((
                index,
                "UndefinedBehaviorSanitizer".into(),
                capture[2].to_string(),
            ));
        }
    }
    if starters.is_empty() {
        for (index, line) in lines.iter().enumerate() {
            if let Some(capture) = summary.captures(line) {
                starters.push((index, capture[1].to_string(), capture[2].to_string()));
            }
        }
    }
    if starters.len() > 64 {
        return Err(ValidationError(
            "sanitizer diagnostic count exceeds 64".into(),
        ));
    }
    let mut findings = vec![];
    for (index, (start, tool, description)) in starters.iter().enumerate() {
        let end = starters.get(index + 1).map_or(lines.len(), |item| item.0);
        let mut runtime_locations = vec![];
        let mut frames = vec![];
        let mut markers = vec![];
        let mut frames_seen = 0;
        let mut limitations = vec![];
        let mut external_locations = vec![];
        for line in &lines[*start..end] {
            let is_runtime = runtime.is_match(line);
            let is_frame = frame.is_match(line);
            let is_marker =
                error.is_match(line) || warning.is_match(line) || summary.is_match(line);
            if is_frame {
                frames_seen += 1;
                if frames_seen > 32 {
                    return Err(ValidationError(
                        "sanitizer stack exceeds 32 frame limit".into(),
                    ));
                }
            }
            if !is_runtime && !is_frame && !is_marker {
                continue;
            }
            let Some(capture) = location.captures_iter(line).last() else {
                continue;
            };
            let row = capture[2]
                .parse::<u32>()
                .ok()
                .filter(|row| *row <= i32::MAX as u32)
                .ok_or_else(|| ValidationError("sanitizer source line overflow".into()))?;
            let column = capture
                .get(3)
                .map(|column| {
                    column
                        .as_str()
                        .parse::<u32>()
                        .ok()
                        .filter(|column| *column <= i32::MAX as u32)
                        .ok_or_else(|| ValidationError("sanitizer source column overflow".into()))
                })
                .transpose()?;
            let label = if is_runtime {
                "runtime error".to_string()
            } else if is_frame {
                frame_label(line, &capture, &frame)
            } else {
                "sanitizer diagnostic".into()
            };
            let path = planning::relative(&capture[1], &plan.context.root, &plan.context.cwd).ok();
            let mut span = if let Some(path) = path {
                if plan.source_coordinates.contains_line(&path, row) != Some(true) {
                    limitations.push(
                    "diagnostic source line could not be confirmed against immutable UTF-8 input"
                        .into(),
                );
                    continue;
                }
                let mut span = SourceSpan::line(path, row);
                if let Some(column) = column {
                    span.start_column = plan
                        .source_coordinates
                        .byte_column(&span.path, row, column)
                        .map_err(|e| ValidationError(e.to_string()))?;
                }
                span
            } else {
                external_locations.push(ExternalLocation {
                    path: "[external]".into(),
                    start_line: row,
                    native_column: column,
                    column_unit: "producer-native".into(),
                    label: label.clone(),
                });
                SourceSpan::line("[external]", row)
            };
            span.label = label;
            if is_runtime {
                runtime_locations.push(span);
            } else if is_frame {
                frames.push(span);
            } else {
                markers.push(span);
            }
        }
        let primary = runtime_locations
            .iter()
            .find(|span| span.path != "[external]")
            .or_else(|| frames.iter().find(|span| span.path != "[external]"))
            .or_else(|| markers.iter().find(|span| span.path != "[external]"))
            .cloned();
        let (kind, defect) = taxonomy(tool, description);
        let message = format!("{tool} detected {}", defect.replace('-', " "));
        let anchor = primary
            .clone()
            .unwrap_or_else(|| SourceSpan::line(crate::cpp::source(plan, "sanitizer"), 1));
        let mut item = finding(
            plan,
            &format!("ici.sanitize.{kind}.{defect}"),
            &message,
            &anchor.path,
            anchor.start_line,
        );
        item.primary_location = anchor;
        item.native_rule_id = defect.clone();
        item.category = "runtime".into();
        if primary.is_none() {
            limitations.push("the diagnostic named no confirmed project source location".into());
        }
        let include_markers = !runtime_locations
            .iter()
            .chain(&frames)
            .any(|span| span.path != "[external]");
        let project_frames = frames
            .iter()
            .filter(|span| span.path != "[external]")
            .count();
        let primary_key = primary
            .as_ref()
            .map(|span| (span.path.clone(), span.start_line, span.start_column));
        let mut unique = BTreeSet::new();
        let mut related_locations = vec![];
        for span in runtime_locations
            .into_iter()
            .chain(frames)
            .chain(if include_markers { markers } else { vec![] })
        {
            let key = (span.path.clone(), span.start_line, span.start_column);
            if Some(&key) != primary_key.as_ref() && unique.insert(key) {
                if span.path != "[external]" {
                    item.related_locations.push(span.clone());
                }
                related_locations.push(span);
            }
        }
        limitations.sort();
        limitations.dedup();
        item.limitations = limitations;
        findings.push(SanitizerDiagnostic {
            kind,
            tool_name: tool.clone(),
            defect,
            rule_id: item.rule_id.clone(),
            message,
            primary_location: primary,
            related_locations,
            frames_observed: frames_seen,
            project_frames,
            external_locations,
            finding: item,
        });
    }
    Ok(findings)
}
fn frame_label(line: &str, location: &regex::Captures<'_>, frame: &Regex) -> String {
    let index = frame
        .captures(line)
        .and_then(|capture| capture.get(1))
        .map_or("?", |index| index.as_str());
    let prefix = line[..location.get(1).expect("matched source path").start()].trim();
    let function = if let Some((_, function)) = prefix.rsplit_once(" in ") {
        function.trim().to_string()
    } else {
        let rest = frame.replace(prefix, "");
        let rest = rest.trim();
        if rest.starts_with("0x") {
            rest.split_once(' ')
                .map_or("", |(_, rest)| rest.trim())
                .to_string()
        } else {
            rest.to_string()
        }
    };
    let addresses = Regex::new(r"(?i)\b0x[0-9a-f]+\b").expect("constant regex");
    let function = addresses
        .replace_all(&function, "address")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(480)
        .collect::<String>();
    if function.is_empty() {
        format!("frame #{index}")
    } else {
        format!("frame #{index}: {function}")
    }
}
fn taxonomy(tool: &str, description: &str) -> (String, String) {
    let lowered = description.to_ascii_lowercase();
    let kind = if tool == "ThreadSanitizer" {
        "tsan"
    } else if tool == "LeakSanitizer" || lowered.contains("leak") {
        "lsan"
    } else if tool == "UndefinedBehaviorSanitizer" {
        "ubsan"
    } else {
        "asan"
    };
    let definitions = if kind == "tsan" {
        vec![
            ("data race", "data-race"),
            ("lock-order-inversion", "lock-order-inversion"),
            ("thread leak", "thread-leak"),
            ("mutex destroyed while busy", "mutex-destroyed-while-busy"),
            ("unlock of an unlocked mutex", "invalid-mutex-unlock"),
            ("double lock of a mutex", "double-mutex-lock"),
        ]
    } else {
        vec![
            ("heap-use-after-free", "heap-use-after-free"),
            ("stack-use-after-return", "stack-use-after-return"),
            ("stack-use-after-scope", "stack-use-after-scope"),
            ("heap-buffer-overflow", "heap-buffer-overflow"),
            ("stack-buffer-overflow", "stack-buffer-overflow"),
            ("global-buffer-overflow", "global-buffer-overflow"),
            ("container-overflow", "container-overflow"),
            ("initialization-order-fiasco", "initialization-order-fiasco"),
            ("alloc-dealloc-mismatch", "alloc-dealloc-mismatch"),
            ("new-delete-type-mismatch", "new-delete-type-mismatch"),
            ("attempting double-free", "double-free"),
            ("attempting free on address", "invalid-free"),
            ("negative-size-param", "negative-size-param"),
            ("detected memory leaks", "memory-leak"),
            ("direct leak", "memory-leak"),
            ("indirect leak", "memory-leak"),
            ("signed integer overflow", "signed-integer-overflow"),
            ("unsigned integer overflow", "unsigned-integer-overflow"),
            ("division by zero", "division-by-zero"),
            ("shift exponent", "invalid-shift"),
            ("shift base", "invalid-shift"),
            ("load of misaligned address", "misaligned-load"),
            ("store to misaligned address", "misaligned-store"),
            (
                "reference binding to misaligned address",
                "misaligned-reference",
            ),
            ("member access within", "invalid-member-access"),
            ("index ", "out-of-bounds-index"),
            ("null pointer", "null-pointer"),
            ("applying non-zero offset", "invalid-pointer-offset"),
            ("implicit conversion", "implicit-conversion"),
            ("execution reached an unreachable", "unreachable"),
        ]
    };
    let defect = definitions
        .iter()
        .find(|(prefix, _)| lowered.starts_with(prefix) || lowered.contains(&format!(": {prefix}")))
        .map(|(_, defect)| (*defect).to_string())
        .unwrap_or_else(|| {
            if kind == "tsan" {
                "thread-safety-defect".into()
            } else {
                let addresses = Regex::new(r"(?i)\b0x[0-9a-f]+\b").expect("constant regex");
                let numbers = Regex::new(r"\b\d+\b").expect("constant regex");
                let slug = Regex::new(r"[^a-z0-9]+").expect("constant regex");
                let stable = addresses.replace_all(&lowered, "address");
                let stable = numbers.replace_all(&stable, "number");
                let stable = stable
                    .split(" on address")
                    .next()
                    .unwrap_or(&stable)
                    .split(" at address")
                    .next()
                    .unwrap_or(&stable)
                    .split(':')
                    .next()
                    .unwrap_or(&stable);
                let slug = slug
                    .replace_all(stable, "-")
                    .trim_matches('-')
                    .chars()
                    .take(80)
                    .collect::<String>();
                if slug.is_empty() {
                    "runtime-defect".into()
                } else {
                    slug.trim_end_matches('-').into()
                }
            }
        });
    (kind.into(), defect)
}
pub(crate) fn observe(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    if plan.context.options.sanitizer_suite_kind.is_some() {
        output.extra["sanitizer_scope"] = crate::sanitizer_suite::proof(plan, outcomes)?;
    } else {
        let probe_id = format!("{}.instrumentation", plan.context.task_id);
        let probe = outcomes
            .get(&probe_id)
            .ok_or_else(|| ValidationError("instrumentation proof is absent".into()))?;
        crate::cpp::instrumentation(plan, probe)?;
    }
    let id = format!("{}.suite", plan.context.task_id);
    let outcome = outcomes
        .get(&id)
        .ok_or_else(|| ValidationError("sanitizer suite was not executed".into()))?;
    let stderr = std::str::from_utf8(&outcome.stderr)
        .map_err(|_| ValidationError("sanitizer stderr is not UTF-8".into()))?;
    let stdout = std::str::from_utf8(&outcome.stdout)
        .map_err(|_| ValidationError("sanitizer stdout is not UTF-8".into()))?;
    let transcript = format!("{stderr}\n{stdout}");
    let diagnostics = parse_diagnostics(&transcript, plan)?;
    output.extra["sanitizer_diagnostics"] = serde_json::json!(diagnostics);
    output
        .observation
        .findings
        .extend(diagnostics.into_iter().map(|diagnostic| diagnostic.finding));
    if !planning::completed(outcome) {
        return Err(ValidationError(
            "sanitizer process did not complete; retained diagnostics cover only observed scope"
                .into(),
        ));
    }
    if plan.context.options.sanitizer_suite_kind.is_some() {
        crate::sanitizer_suite::observe_framework(plan, outcome, output)?;
    }
    if output.observation.findings.is_empty() {
        if transcript.contains("AddressSanitizer:DEADLYSIGNAL")
            || transcript.contains("FATAL: ThreadSanitizer")
            || transcript.contains("Sanitizer CHECK failed")
            || transcript.contains("ERROR: AddressSanitizer")
            || transcript.contains("WARNING: ThreadSanitizer")
        {
            return Err(ValidationError(
                "sanitizer runtime failed or produced an incomplete diagnostic".into(),
            ));
        }
        if outcome.exit_code != Some(0) {
            output.observation.findings.push(finding(
                plan,
                "ici.sanitize.suite-failure",
                &format!(
                    "suite exited {:?} without sanitizer defect evidence",
                    outcome.exit_code
                ),
                &crate::cpp::source(plan, "sanitizer"),
                1,
            ));
        }
    }
    output.observation.measurements.push(Measurement::measured(
        format!("{}.diagnostics", plan.kind.provider()),
        output.observation.findings.len() as f64,
    ));
    let mut target = InspectionTarget::at(crate::cpp::source(plan, "sanitizer"), 1);
    target.target_name = "Instrumented suite".into();
    target.status = if output.observation.findings.is_empty() {
        "PASS"
    } else {
        "FAIL"
    }
    .into();
    output.targets.push(target);
    output.summary = format!(
        "{} sanitizer diagnostics",
        output.observation.findings.len()
    );
    if plan.kind == ValidationKind::ThreadSanitizer
        && output
            .observation
            .findings
            .iter()
            .any(|finding| finding.native_rule_id == "thread-safety-defect")
    {
        output
            .observation
            .limitations
            .push("ThreadSanitizer reported a defect outside the explicit taxonomy".into());
    }
    Ok(())
}
