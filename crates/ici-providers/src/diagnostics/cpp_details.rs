//! Stable historical rule metadata, without changing modern diagnostic identity.
use super::Parsed;
use crate::{ProviderKind, ProviderPlan};
use serde_json::{Value, json};
use std::{collections::BTreeMap, sync::OnceLock};
fn category(family: &str, rule: &str) -> &'static str {
    static POLICY: OnceLock<Value> = OnceLock::new();
    let data = POLICY.get_or_init(|| {
        serde_json::from_str(include_str!("cpp-category-policy.json"))
            .expect("frozen C++ category policy")
    });
    let rule = rule.to_ascii_lowercase();
    let exact = |key: &str| {
        data[key]
            .as_array()
            .expect("policy list")
            .iter()
            .any(|item| item.as_str() == Some(&rule))
    };
    let prefix = |key: &str| {
        data[key]
            .as_array()
            .expect("policy list")
            .iter()
            .any(|item| rule.starts_with(item.as_str().expect("prefix")))
    };
    let stem = |key: &str| {
        data[key]
            .as_array()
            .expect("policy list")
            .iter()
            .any(|item| {
                let s = item.as_str().expect("stem");
                rule == s
                    || rule
                        .strip_prefix(s)
                        .is_some_and(|tail| tail.starts_with(['-', '.']))
            })
    };
    match family {
        "compiler" => "correctness",
        "clang-analyzer" => {
            if prefix("_CLANG_SECURITY_PREFIXES") {
                "security"
            } else if exact("_CLANG_ANALYZER_RESOURCE_RULES")
                || prefix("_CLANG_ANALYZER_RESOURCE_PREFIXES")
            {
                "resource"
            } else {
                "correctness"
            }
        }
        "clang-tidy" => {
            if prefix("_CLANG_TIDY_SECURITY_PREFIXES") || exact("_CLANG_TIDY_SECURITY_RULES") {
                "security"
            } else if exact("_CLANG_TIDY_RESOURCE_RULES") {
                "resource"
            } else if rule.starts_with("portability-") || rule == "modernize-deprecated-headers" {
                "compatibility"
            } else if rule.starts_with("bugprone-") || rule.starts_with("concurrency-") {
                "correctness"
            } else {
                "maintainability"
            }
        }
        "clazy" => {
            if exact("_CLAZY_RESOURCE_RULES") || stem("_CLAZY_RESOURCE_STEMS") {
                "resource"
            } else if exact("_CLAZY_COMPATIBILITY_RULES") || stem("_CLAZY_COMPATIBILITY_STEMS") {
                "compatibility"
            } else if exact("_CLAZY_CORRECTNESS_RULES") || stem("_CLAZY_CORRECTNESS_STEMS") {
                "correctness"
            } else {
                "maintainability"
            }
        }
        _ => "correctness",
    }
}
pub(super) fn finish(parsed: &mut Parsed, plan: &ProviderPlan) {
    let descriptor = |rule: &str| match plan.kind {
        ProviderKind::ClangTidy if rule.starts_with("clang-analyzer-") => (
            "clang-analyzer",
            "ClangAnalyzer",
            "Clang Static Analyzer diagnostic.",
        ),
        ProviderKind::ClangTidy => ("clang-tidy", "ClangTidy", "clang-tidy diagnostic."),
        ProviderKind::Clazy => ("clazy", "Clazy", "Qt-aware clazy diagnostic."),
        _ => (
            "compiler",
            "Compiler",
            "Compiler diagnostic from a sanitized translation-unit replay.",
        ),
    };
    let label = |rule: &str| {
        let normalized = if plan.kind == ProviderKind::Clazy {
            rule.strip_prefix("-Wclazy-")
                .map(|rule| format!("clazy-{rule}"))
        } else {
            None
        };
        let rule = normalized.as_deref().unwrap_or(rule);
        let (_, prefix, _) = descriptor(rule);
        if rule.is_empty() || rule == "diagnostic" {
            "C++Syntax".into()
        } else {
            format!("{prefix}:{rule}")
        }
    };
    let mut counts = BTreeMap::new();
    for finding in &parsed.findings {
        *counts
            .entry((
                finding.primary_location.path.clone(),
                label(&finding.native_rule_id),
            ))
            .or_insert(0usize) += 1;
    }
    for finding in &parsed.findings {
        let rule = if plan.kind == ProviderKind::Clazy {
            finding.native_rule_id.strip_prefix("-Wclazy-").map_or_else(
                || finding.native_rule_id.clone(),
                |rule| format!("clazy-{rule}"),
            )
        } else {
            finding.native_rule_id.clone()
        };
        let (family, _, explanation) = descriptor(&rule);
        let name = label(&finding.native_rule_id);
        let detail = parsed
            .details
            .entry(finding.fingerprint.clone())
            .or_insert_with(|| json!({}));
        detail["legacy_rule_id"] = json!("ici.legacy.lint.target");
        detail["legacy_category"] = json!(category(family, &rule));
        detail["legacy_confidence"] = json!(if family != "compiler"
            || plan.context.compiler_columns.is_some()
        {
            "exact"
        } else {
            "medium"
        });
        detail["legacy_severity"] = json!(if finding.severity == "high" {
            "high"
        } else {
            "medium"
        });
        detail["legacy_label"] = json!(if counts
            [&(finding.primary_location.path.clone(), name.clone())]
            == 1
        {
            name
        } else {
            String::new()
        });
        detail["tool_name"] = json!(plan.context.tool_name);
        detail["tool_version"] = json!(plan.context.tool_version);
        detail["tool_rule_id"] = json!(if rule == "diagnostic" { "" } else { &rule });
        detail["explanation"] = json!(explanation);
        detail["snippet"] = json!("");
        detail["metrics"] = json!({});
        let replacements = detail
            .get("replacements")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut suggestions = replacements
            .iter()
            .take(8)
            .map(|edit| {
                format!(
                    "{}:{}:{}-{}:{} replace with {}",
                    edit["path"].as_str().unwrap_or(""),
                    edit["start_line"],
                    edit["start_column"],
                    edit["end_line"],
                    edit["end_column"],
                    python_repr(edit["replacement"].as_str().unwrap_or(""))
                )
            })
            .collect::<Vec<_>>();
        if replacements.len() > suggestions.len() {
            suggestions.push(format!(
                "{} additional suggestion(s) omitted",
                replacements.len() - suggestions.len()
            ));
        }
        detail["remediation"] = json!(
            suggestions
                .join("; ")
                .chars()
                .take(8192)
                .collect::<String>()
        );
        detail["fixes"] = if replacements.is_empty() {
            json!([])
        } else {
            json!([{"description":format!("{} suggested fix",if rule=="diagnostic"{family}else{&rule}),"replacements":replacements}])
        };
        detail
            .as_object_mut()
            .expect("detail object")
            .remove("replacements");
    }
}

// Prose uses the historical repr spelling; replacement text remains literal in the structured fix.
fn python_repr(value: &str) -> String {
    use std::fmt::Write;
    static NON_PRINTABLE: OnceLock<regex::Regex> = OnceLock::new();
    let non_printable = NON_PRINTABLE
        .get_or_init(|| regex::Regex::new(r"[\p{C}\p{Z}]").expect("Unicode categories"));
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut output = String::with_capacity(value.len() + 2);
    output.push(quote);
    for character in value.chars() {
        match character {
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value == quote => {
                output.push('\\');
                output.push(value);
            }
            ' ' => output.push(' '),
            value => {
                let mut bytes = [0; 4];
                if non_printable.is_match(value.encode_utf8(&mut bytes)) {
                    let code = value as u32;
                    if code <= 0xff {
                        write!(output, "\\x{code:02x}").expect("String write");
                    } else if code <= 0xffff {
                        write!(output, "\\u{code:04x}").expect("String write");
                    } else {
                        write!(output, "\\U{code:08x}").expect("String write");
                    }
                } else {
                    output.push(value);
                }
            }
        }
    }
    output.push(quote);
    output
}
