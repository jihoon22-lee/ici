use crate::{ApplicationError, Result};
use ici_config::Suppression;
use ici_domain::{Finding, FindingSuppression};
use regex::{Regex, RegexBuilder};

pub struct SuppressionPolicy {
    rules: Vec<(Suppression, Option<Regex>)>,
}
impl SuppressionPolicy {
    pub fn new(suppressions: &[Suppression]) -> Result<Self> {
        let mut rules = vec![];
        for suppression in suppressions {
            if suppression.reason.trim().is_empty()
                || [
                    &suppression.fingerprint,
                    &suppression.rule,
                    &suppression.path,
                    &suppression.component,
                ]
                .iter()
                .all(|s| s.is_empty())
            {
                return Err(ApplicationError(
                    "suppression requires a selector and reason".into(),
                ));
            }
            let pattern = if suppression.path.is_empty() {
                None
            } else {
                Some(fnmatch_regex(&suppression.path)?)
            };
            rules.push((suppression.clone(), pattern));
        }
        Ok(Self { rules })
    }
    /// All declared selectors are conjunctive. The earliest matching declaration supplies provenance.
    pub fn apply(&self, finding: &mut Finding) {
        for (rule, pattern) in &self.rules {
            if !rule.fingerprint.is_empty() && rule.fingerprint != finding.fingerprint {
                continue;
            }
            if !rule.rule.is_empty()
                && !(finding.rule_id == rule.rule
                    || finding
                        .rule_id
                        .strip_prefix(&rule.rule)
                        .is_some_and(|suffix| suffix.starts_with('.')))
            {
                continue;
            }
            if !rule.component.is_empty()
                && finding.component_id.as_deref() != Some(rule.component.as_str())
            {
                continue;
            }
            if let Some(pattern) = pattern {
                let location = if rule.path.contains('/') {
                    finding.primary_location.path.as_str()
                } else {
                    finding
                        .primary_location
                        .path
                        .rsplit('/')
                        .next()
                        .unwrap_or("")
                };
                if !pattern.is_match(location) {
                    continue;
                }
            }
            finding.suppression = FindingSuppression {
                suppressed: true,
                kind: "config".into(),
                reason: rule.reason.clone(),
                origin: rule.declared_in.clone(),
            };
            return;
        }
    }
}

/// Python fnmatchcase semantics, including '*' spanning '/' and literal backslashes. A Rust regex
/// performs linear matching; Python's backtracking-specific STAR optimization is unnecessary.
pub fn fnmatch_regex(pattern: &str) -> Result<Regex> {
    let chars: Vec<_> = pattern.chars().collect();
    if chars.len() > 4096 {
        return Err(ApplicationError(
            "suppression pattern exceeds 4096 characters".into(),
        ));
    }
    let mut expression = String::from("\\A(?s:");
    let mut index = 0;
    while index < chars.len() {
        let character = chars[index];
        index += 1;
        match character {
            '*' => {
                while chars.get(index) == Some(&'*') {
                    index += 1;
                }
                expression.push_str(".*");
            }
            '?' => expression.push('.'),
            '[' => {
                let mut end = index;
                if chars.get(end) == Some(&'!') {
                    end += 1;
                }
                if chars.get(end) == Some(&']') {
                    end += 1;
                }
                while end < chars.len() && chars[end] != ']' {
                    end += 1;
                }
                if end == chars.len() {
                    expression.push_str("\\[");
                    continue;
                }
                let mut cursor = index;
                let mut search = if chars[index] == '!' {
                    index + 2
                } else {
                    index + 1
                };
                let mut chunks: Vec<Vec<char>> = vec![];
                while search < end {
                    let Some(offset) = chars[search..end].iter().position(|c| *c == '-') else {
                        break;
                    };
                    let hyphen = search + offset;
                    chunks.push(chars[cursor..hyphen].to_vec());
                    cursor = hyphen + 1;
                    search = hyphen + 3;
                }
                if cursor < end {
                    chunks.push(chars[cursor..end].to_vec());
                } else if let Some(last) = chunks.last_mut() {
                    last.push('-');
                }
                for part in (1..chunks.len()).rev() {
                    if chunks[part - 1]
                        .last()
                        .zip(chunks[part].first())
                        .is_some_and(|(start, end)| start > end)
                    {
                        let tail = chunks.remove(part);
                        chunks[part - 1].pop();
                        chunks[part - 1].extend(tail.into_iter().skip(1));
                    }
                }
                let stuff = chunks
                    .iter()
                    .map(|chunk| {
                        let mut escaped = String::new();
                        for character in chunk {
                            if matches!(character, '\\' | '-' | '&' | '~' | '|' | '[' | ']' | '^') {
                                escaped.push('\\');
                            }
                            escaped.push(*character);
                        }
                        escaped
                    })
                    .collect::<Vec<_>>()
                    .join("-");
                if stuff.is_empty() {
                    expression.push_str("\\z.");
                } else if stuff == "!" {
                    expression.push('.');
                } else {
                    expression.push('[');
                    if let Some(rest) = stuff.strip_prefix('!') {
                        expression.push('^');
                        expression.push_str(rest);
                    } else {
                        expression.push_str(&stuff);
                    }
                    expression.push(']');
                }
                index = end + 1;
            }
            other => expression.push_str(&regex::escape(&other.to_string())),
        }
    }
    expression.push_str(")\\z");
    RegexBuilder::new(&expression)
        .size_limit(1024 * 1024)
        .dfa_size_limit(1024 * 1024)
        .build()
        .map_err(|e| ApplicationError(format!("invalid suppression pattern: {e}")))
}
