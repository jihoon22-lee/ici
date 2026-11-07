//! Human output of `ici analyze` / `ici next verify` — frozen `cli/next_common._announce` and
//! `cli/next_render.verify_text`. The frozen block is reproduced byte for byte and ends at
//! `saved <path>`; native-only lines (the offline page and a bounded finding preview, execution
//! plan §2.1) follow it so the frozen prefix stays exact.
use ici_application::request::RunRequest;
use ici_config::ResolvedProject;
use ici_domain::{BaselineState, Finding, RunResult};
use std::{fmt::Write, path::Path};

/// Findings previewed after the frozen block; the HTML page and `--json` keep the complete list.
pub const PREVIEW_FINDINGS: usize = 5;

fn label<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_default()
}

/// `root:` / `scope:` lines, or `None` when the request names no valid scope (frozen prints no
/// announcement for an unknown component or language: `_scope` refuses before `_announce`).
pub fn announce(project: &ResolvedProject, request: &RunRequest) -> Option<String> {
    let scope = request.scope(project).ok()?;
    let components: Vec<&str> = scope.iter().map(|item| item.id.as_str()).collect();
    let languages = request.requested_languages(&scope);
    let languages = if languages.is_empty() {
        "all".to_owned()
    } else {
        languages.join(",")
    };
    let profile = label(&request.profile.unwrap_or(project.profile));
    Some(format!(
        "root: {}\nscope: components={} languages={languages} profile={profile}{}\n",
        project.root.display(),
        components.join(","),
        if request.require_full {
            " require-full"
        } else {
            ""
        }
    ))
}

/// Frozen `verify_text`: verdict, gate reasons, baseline, cache reuse, per-unit execution notes,
/// run notes and the saved result path.
pub fn verdict_block(
    result: &RunResult,
    unit_notes: &[(String, String)],
    notes: &[String],
    saved: &Path,
) -> String {
    let mut out = format!(
        "{}: {} finding(s)\n",
        label(&result.gate.selected),
        result.findings.len()
    );
    for reason in &result.gate.reasons {
        // Frozen next tool lookup wording for a tool found nowhere (`<task>: ruff is not
        // available`) for reasons that did not come from planning's tool resolution.
        let shown = match reason.split_once(": ") {
            Some((task, rest)) if !task.contains(char::is_whitespace) => {
                format!("{task}: {}", crate::plan_text::tool_blocked_reason(rest))
            }
            _ => reason.clone(),
        };
        let _ = writeln!(out, "  {shown}");
    }
    if let Some(delta) = &result.baseline {
        if delta.state == BaselineState::Comparable {
            let _ = writeln!(
                out,
                "  baseline: {} new, {} unchanged, {} resolved, {} carried",
                delta.new.len(),
                delta.unchanged.len(),
                delta.resolved.len(),
                delta.carried.len()
            );
        } else {
            let _ = writeln!(out, "  baseline: incompatible — {}", delta.reason);
        }
    }
    if !result.execution.reused_task_ids.is_empty() {
        let _ = writeln!(
            out,
            "  cache: reused {} stored result(s)",
            result.execution.reused_task_ids.len()
        );
    }
    for (unit, detail) in unit_notes {
        let _ = writeln!(out, "  note: {unit}: {detail}");
    }
    for note in notes {
        let _ = writeln!(out, "  note: {note}");
    }
    let _ = writeln!(out, "saved {}", saved.display());
    out
}

fn rank(severity: &str) -> u8 {
    match severity {
        "critical" => 0,
        "high" => 1,
        "medium" => 2,
        "low" => 3,
        _ => 4,
    }
}

/// Native addition after the frozen block: where the page is and the most severe unsuppressed
/// findings with file, line and message (plan §2.1), bounded to [`PREVIEW_FINDINGS`].
pub fn native_tail(result: &RunResult, page: &Path) -> String {
    let mut out = format!("HTML: {}\n", page.display());
    let mut active: Vec<&Finding> = result
        .findings
        .iter()
        .filter(|finding| !finding.suppression.suppressed)
        .collect();
    if active.is_empty() {
        return out;
    }
    active.sort_by(|left, right| {
        (
            rank(&left.severity),
            &left.primary_location.path,
            left.primary_location.start_line,
            &left.rule_id,
        )
            .cmp(&(
                rank(&right.severity),
                &right.primary_location.path,
                right.primary_location.start_line,
                &right.rule_id,
            ))
    });
    let shown = active.len().min(PREVIEW_FINDINGS);
    let _ = writeln!(
        out,
        "findings ({shown} of {} unsuppressed, most severe first):",
        active.len()
    );
    for finding in active.iter().take(shown) {
        let message = finding.message.lines().next().unwrap_or_default();
        let _ = writeln!(
            out,
            "  {} {}:{} {}: {message}",
            finding.severity,
            finding.primary_location.path,
            finding.primary_location.start_line,
            finding.rule_id
        );
    }
    out
}
