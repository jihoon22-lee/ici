use super::{Parsed, finding, span};
use crate::{
    ProviderError,
    plan::{ProviderKind, ProviderPlan, relative_location},
};
use regex::Regex;

pub(super) fn parse(
    stdout: &str,
    stderr: &str,
    plan: &ProviderPlan,
) -> Result<Parsed, ProviderError> {
    let diagnostic = Regex::new(r"^(.+?):([1-9]\d*):([1-9]\d*):\s*(fatal error|error|warning|note):\s*(\S.*?)(?:\s+\[([^\]]+)\])?$").expect("constant regex");
    let context = Regex::new(r"^(?:In file included from |\s+from |.*: In (?:function|member function|instantiation)|\d+ warnings? generated\.|\d+ errors? generated\.|Suppressed \d+ warnings?|Use -header-filter=|Error while processing )").expect("constant regex");
    let mut parsed = Parsed::default();
    let mut saw_diagnostic = false;
    for line in stdout
        .lines()
        .chain(stderr.lines())
        .filter(|line| !line.trim().is_empty())
    {
        if let Some(captures) = diagnostic.captures(line) {
            saw_diagnostic = true;
            let row = captures[2]
                .parse()
                .map_err(|_| ProviderError("invalid C++ line".into()))?;
            let column = Some(
                captures[3]
                    .parse()
                    .map_err(|_| ProviderError("invalid C++ column".into()))?,
            );
            if relative_location(&captures[1], &plan.context).is_err() {
                if captures[4].contains("error") {
                    return Err(ProviderError(format!(
                        "unattributed external compiler error: {line}"
                    )));
                }
                parsed
                    .limitations
                    .push(format!("external compiler context: {line}"));
                continue;
            }
            if &captures[4] == "note" && !parsed.findings.is_empty() {
                parsed
                    .findings
                    .last_mut()
                    .expect("nonempty")
                    .related_locations
                    .push(span(
                        &plan.context,
                        &captures[1],
                        row,
                        column,
                        &captures[5],
                    )?);
                continue;
            }
            let rule = captures.get(6).map_or("diagnostic", |value| value.as_str());
            let severity = if captures[4].contains("error") {
                "high"
            } else {
                "medium"
            };
            parsed.findings.push(finding(
                plan,
                &captures[1],
                row,
                column,
                rule,
                &captures[5],
                severity,
            )?);
        } else if context.is_match(line)
            || (saw_diagnostic && line.starts_with(char::is_whitespace))
            || line.trim_start().starts_with(['^', '~', '|'])
        {
            continue;
        } else {
            return Err(ProviderError(format!(
                "unrecognized C++ diagnostic output: {line}"
            )));
        }
    }
    if plan.kind == ProviderKind::Compiler {
        parsed
            .inspected
            .extend(plan.context.targets.iter().cloned());
    }
    Ok(parsed)
}
