use crate::{CppError, input::scoped};
use ici_domain::InspectionTarget;
use regex::Regex;
use serde_json::json;
use std::{collections::BTreeMap, path::Path, sync::LazyLock};
static MISSING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?P<file>.+?):(?P<line>[1-9][0-9]*)(?::[1-9][0-9]*)?: fatal error: (?:(?P<gcc>.+?): No such file or directory|['<](?P<clang>.+?)[>'] file not found)$").unwrap()
});
static CONTEXT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:In file included from .+|\s*from .+|\s*[0-9]+\s*\|.*|\s*\|.*|\s*[\^~].*|compilation terminated\.|[0-9]+ errors? generated\.)$").unwrap()
});
/// Accept only located missing-header diagnostics accompanied by an otherwise valid -H trace.
pub fn parse_missing_include_targets(
    root: &Path,
    cwd: &Path,
    source: &Path,
    stderr: &str,
) -> Result<Vec<InspectionTarget>, CppError> {
    if stderr.len() > 8 * 1024 * 1024 || stderr.contains('\0') {
        return Err(CppError::new(
            "include-diagnostic-bounds",
            "Compiler missing-include output exceeds bounded text contract",
        ));
    }
    let mut traces = vec![];
    let mut targets = vec![];
    let mut trailer = false;
    for line in stderr.lines().map(|line| line.trim_end_matches('\r')) {
        if line == "Multiple include guards may be useful for:" {
            trailer = true;
            traces.push(line);
            continue;
        }
        if trailer {
            traces.push(line);
            continue;
        }
        if line.is_empty() {
            continue;
        }
        let depth = line.bytes().take_while(|byte| *byte == b'.').count();
        if depth > 0 && line.as_bytes().get(depth) == Some(&b' ') {
            traces.push(line);
            continue;
        }
        if let Some(capture) = MISSING.captures(line) {
            let number = capture["line"]
                .parse::<u32>()
                .ok()
                .filter(|line| *line <= 2_147_483_647)
                .ok_or_else(|| {
                    CppError::new(
                        "include-diagnostic-line",
                        "Missing-include line is outside bounded shape",
                    )
                })?;
            let (path, scope, _) = scoped(root, cwd, &capture["file"])?;
            if scope != "project" {
                return Err(CppError::new(
                    "include-diagnostic-external",
                    "Missing include originates outside selected project scope",
                ));
            }
            let include = capture
                .name("gcc")
                .or_else(|| capture.name("clang"))
                .unwrap()
                .as_str();
            let mut target = InspectionTarget::at(path, number);
            target.target_name = "CppIncludeUnresolved".into();
            target.status = "WARN".into();
            target.message = format!(
                "Compiler could not resolve active include {include:?}; no dependency edge was recorded"
            );
            target.snippet = line.into();
            target.metrics = BTreeMap::from([
                ("include".into(), json!(include)),
                ("candidates".into(), json!([])),
                ("resolution".into(), json!("compiler_trace")),
            ]);
            targets.push(target);
        } else if !CONTEXT.is_match(line) {
            return Err(CppError::new(
                "include-diagnostic-unrecognized",
                "Compiler missing-include output contains an unrecognized diagnostic",
            ));
        }
    }
    let trace = crate::parse_include_trace(root, cwd, source, &traces.join("\n"))?;
    if !trace.complete() {
        return Err(CppError::new(
            "include-diagnostic-unrecognized",
            "Missing-header output contains an incomplete include trace",
        ));
    }
    Ok(targets)
}
