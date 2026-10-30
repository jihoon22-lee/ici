use crate::{
    ConfigError, SchemaFamily,
    parse::Parsed,
    schema_keys::{LEGACY_KEYS, NEXT_KEYS},
};
use serde_json::Value;
use std::collections::BTreeSet;

const V2_EXTRA: &[&str] = &[
    "schema_version",
    "profile",
    "exclude",
    "extend_exclude",
    "source_dirs",
    "languages",
    "python",
    "python.executable",
    "python.type_provider",
    "python.test_tools",
    "python.test_paths",
    "cpp",
    "cpp.qt",
    "output",
    "cache",
    "display",
    "display.color",
    "display.verbosity",
    "display.max_findings",
    "display.group_by",
    "display.format",
    "components[].extend_exclude",
    "components[].source_dirs",
];
const STRING_LISTS: &[&str] = &[
    "source_dirs",
    "cpp_pkg_config",
    "cpp_external_build_dirs",
    "required_tools",
    "gate_dirs",
    "include_dirs",
    "exclude_dirs",
    "required_flags",
    "forbidden_flags",
    "changed_lines",
    "clang_tidy_checks",
    "clazy_checks",
    "secret_name_allowlist",
    "interpreters",
    "required_interpreters",
    "imports",
    "wheel_globs",
    "forbidden_needed",
    "allowed_needed",
    "artifacts",
    "argv",
    "command",
    "configure_argv",
    "build_argv",
    "test_argv",
    "clean_argv",
    "coverage_build_argv",
    "coverage_test_argv",
    "sanitize_build_argv",
    "sanitize_test_argv",
    "thread_sanitize_build_argv",
    "thread_sanitize_test_argv",
    "stdout_contains",
    "stderr_contains",
    "stdout_not_contains",
    "stderr_not_contains",
    "inherit_env",
    "requires",
    "languages",
    "sources",
    "include",
    "exclude",
    "extend_exclude",
    "vendor",
    "external",
    "test_paths",
    "needs",
    "python_wheel",
    "report",
];
const BOOLS: &[&str] = &[
    "enabled",
    "required",
    "ruff_required",
    "database_required",
    "coverage_required",
    "fail_on_error",
    "warn_on_missing_annotation",
    "mypy_required",
    "wheel_required",
    "check_entrypoints",
    "check_package_files",
    "include_generated",
    "include_vendor",
    "scan_tests",
    "forbid_absolute_rpath",
    "require_static",
    "forbid_build_paths",
    "allow_non_elf",
    "qt",
];
const INTEGERS: &[&str] = &[
    "schema_version",
    "warn_limit",
    "fail_limit",
    "min_file_statements",
    "repeat_runs",
    "repeat",
    "max_slow_tests",
    "max_wheels",
    "max_wheel_members",
    "max_wheel_uncompressed_bytes",
    "warn_cc",
    "fail_cc",
    "warn_nesting",
    "min_window",
    "max_reported",
    "warn",
    "fail",
    "max_artifacts",
    "max_cases",
    "max_output_bytes",
    "jobs",
    "expected_exit",
    "min_size",
    "max_findings",
];
const NUMBERS: &[&str] = &[
    "min_tem_score",
    "min_line_cov",
    "min_file_cov",
    "min_branch_cov",
    "min_func_cov",
    "min_changed_line_cov",
    "max_coverage_regression",
    "timeout",
    "slow_test_threshold",
    "slow_threshold",
    "warn_pct",
    "fail_pct",
    "timeout_seconds",
];
const TABLES: &[&str] = &[
    "ici",
    "project",
    "engines",
    "build",
    "doctor",
    "quality",
    "workspace",
    "component",
    "checks",
    "tools",
    "builds",
    "python",
    "cpp",
    "publish",
    "display",
    "exemptions",
];

pub(crate) fn family(parsed: &Parsed) -> Result<SchemaFamily, ConfigError> {
    if !parsed.value.is_object() {
        return Err(parsed.error("", "configuration must be a table"));
    }
    match parsed.value.get("schema_version") {
        Some(v) if v.as_i64() == Some(2) => Ok(SchemaFamily::SparseV2),
        Some(v) if v.as_i64() == Some(1) => Ok(SchemaFamily::NextV1),
        Some(_) => Err(parsed.error(
            "schema_version",
            "unsupported schema_version; supported versions are 1 and 2",
        )),
        None if parsed.value.get("workspace").is_some()
            || parsed.value.get("components").is_some()
            || parsed.value.get("component").is_some() =>
        {
            Err(parsed.error(
                "schema_version",
                "workspace/component configuration requires schema_version",
            ))
        }
        None => Ok(SchemaFamily::Legacy),
    }
}

pub(crate) fn validate(parsed: &Parsed, family: SchemaFamily) -> Result<(), ConfigError> {
    let mut errors = vec![];
    walk(parsed, &parsed.value, &[], "", family, 0, &mut errors);
    ConfigError::collect(errors)?;
    if family == SchemaFamily::NextV1
        && parsed.value.get("workspace").is_none()
        && parsed.value.get("component").is_none()
    {
        return Err(parsed.error(
            "workspace",
            "schema_version=1 requires [workspace] or an explicit [component]",
        ));
    }
    if parsed.value.get("workspace").is_some() && parsed.value.get("component").is_some() {
        return Err(parsed.error(
            "component",
            "a component file must not also declare a workspace",
        ));
    }
    relationships(parsed, &parsed.value, "")
}

fn allowed(path: &[String], family: SchemaFamily) -> bool {
    let keys: Vec<&str> = match family {
        SchemaFamily::Legacy => LEGACY_KEYS.to_vec(),
        SchemaFamily::NextV1 => NEXT_KEYS.to_vec(),
        _ => NEXT_KEYS
            .iter()
            .chain(LEGACY_KEYS)
            .chain(V2_EXTRA)
            .copied()
            .collect(),
    };
    keys.iter().any(|pattern| {
        let parts: Vec<_> = pattern.split('.').collect();
        path.len() <= parts.len()
            && path.iter().zip(parts).all(|(actual, declared)| {
                declared == "*" || actual == declared || actual.trim_end_matches("[]") == declared
            })
    })
}

fn walk(
    parsed: &Parsed,
    value: &Value,
    path: &[String],
    display: &str,
    family: SchemaFamily,
    depth: usize,
    errors: &mut Vec<ConfigError>,
) {
    // Bound diagnostics independently of the document bound; no output flood for typo lists.
    if errors.len() >= 64 {
        return;
    }
    if depth > 64 {
        errors.push(parsed.error(display, "configuration nesting exceeds 64 levels"));
        return;
    }
    if let Some(object) = value.as_object() {
        let last = path.last().map(String::as_str).unwrap_or("");
        if matches!(last, "env" | "python_targets") {
            if let Err(error) = validate_string_map(parsed, object, display, last, family) {
                errors.push(error);
            }
            return;
        }
        for (key, child) in object {
            if errors.len() >= 64 {
                break;
            }
            let mut child_path = path.to_vec();
            child_path.push(key.clone());
            let child_display = join(display, key);
            if !allowed(&child_path, family) {
                errors.push(parsed.error(&child_display, "unknown configuration key"));
                continue;
            }
            if let Err(error) = validate_type(parsed, child, &child_path, &child_display) {
                errors.push(error);
                continue;
            }
            walk(
                parsed,
                child,
                &child_path,
                &child_display,
                family,
                depth + 1,
                errors,
            );
        }
    } else if let Some(array) = value.as_array() {
        if array.len() > 100_000 {
            errors.push(parsed.error(display, "array exceeds 100000 values"));
            return;
        }
        let mut array_path = path.to_vec();
        if let Some(last) = array_path.last_mut() {
            *last += "[]";
        }
        for (index, child) in array.iter().enumerate() {
            if errors.len() >= 64 {
                break;
            }
            if child.is_object() {
                walk(
                    parsed,
                    child,
                    &array_path,
                    &format!("{display}[{index}]"),
                    family,
                    depth + 1,
                    errors,
                );
            }
        }
    }
}

fn validate_type(
    parsed: &Parsed,
    value: &Value,
    path: &[String],
    display: &str,
) -> Result<(), ConfigError> {
    let last = path.last().map(String::as_str).unwrap_or("");
    let parent = path
        .get(path.len().saturating_sub(2))
        .map(String::as_str)
        .unwrap_or("");
    let table = TABLES.contains(&last)
        || matches!(
            parent,
            "engines" | "tools" | "checks" | "builds" | "exemptions"
        )
        || (last == "make" && parent == "build")
        || (last == "artifacts" && parent == "build");
    let string_slot =
        (last == "project" && path.len() == 3 && path.first().is_some_and(|p| p == "builds"))
            || (last == "python" && parent == "test")
            || (last == "build" && matches!(parent, "components[]" | "component"))
            || (last == "component" && parent == "suppressions[]");
    let valid = if string_slot {
        value.is_string()
    } else if table {
        value.is_object()
    } else if matches!(
        last,
        "components" | "integrations" | "cases" | "output_artifacts" | "suppressions"
    ) {
        value
            .as_array()
            .is_some_and(|v| v.iter().all(Value::is_object))
    } else if matches!(last, "env" | "python_targets") {
        value.is_object()
    } else if last == "mutation" {
        value.is_boolean() || value.is_object()
    } else if BOOLS.contains(&last) {
        value.is_boolean()
    } else if INTEGERS.contains(&last) {
        value.as_i64().is_some()
    } else if NUMBERS.contains(&last) {
        value.as_f64().is_some_and(f64::is_finite)
    } else if STRING_LISTS.contains(&last) {
        value
            .as_array()
            .is_some_and(|v| v.iter().all(Value::is_string))
    } else {
        value.is_string()
    };
    if !valid {
        return Err(parsed.error(display, "invalid value type for configuration key"));
    }
    if let Some(text) = value.as_str() {
        if text.len() > 8192 || text.contains('\0') {
            return Err(parsed.error(display, "string must be bounded and contain no NUL"));
        }
        // Frozen non-empty strings use `str.strip()`; whitespace-only values are empty.
        if !matches!(
            last,
            "target_version"
                | "expected_class"
                | "expected_machine"
                | "max_glibc"
                | "max_glibcxx"
                | "max_cxxabi"
        ) && text.trim().is_empty()
        {
            return Err(parsed.error(display, "string must not be empty"));
        }
        validate_text_bounds(parsed, display, last, text)?;
        let choices: &[&str] = match last {
            "profile" => &["fast", "standard", "deep"],
            "type_provider" => &["mypy", "ty"],
            "test_tools" => &["project", "bundle"],
            "cpp_boundaries" | "cpp_unused" | "cpp_linker" | "python_semantic" | "clang_tidy"
            | "clazy" => &["auto", "required", "off"],
            "clazy_profile" => &["level0", "level1"],
            "mypy_profile" => &["project", "ici"],
            "wheel_policy" => &["allow-native", "pure"],
            "out_of_tree" => &["allow", "required"],
            "prepare" => &["explicit"],
            "mode" if display.contains(".quality.") => &["report", "warn"],
            "mode" => &["pass_warn_fail", "pass_fail", "pass_warn"],
            "tool" if display.contains(".mutation.") => &["auto", "mutmut", "cosmic-ray", "mutpy"],
            "type" => &["python", "cpp", "hybrid"],
            "color" => &["auto", "always", "never"],
            "format" => &["console", "json", "markdown", "html", "sarif"],
            _ => &[],
        };
        if !choices.is_empty() && !choices.contains(&text) {
            return Err(parsed.error(display, format!("expected one of: {}", choices.join(", "))));
        }
        if matches!(last, "api_url" | "server_url") && !text.starts_with("https://") {
            return Err(parsed.error(display, "publication endpoint must use https://"));
        }
        if last == "token_env" && !valid_env_name(text) {
            return Err(parsed.error(display, "token_env must name an environment variable"));
        }
        if last == "repo" && (text.split('/').count() != 2 || text.split('/').any(str::is_empty)) {
            return Err(parsed.error(display, "repo must be owner/name"));
        }
        if last == "target_version" && !text.is_empty() {
            let minor = text.strip_prefix("3.").and_then(|s| s.parse::<u8>().ok());
            if minor.is_none_or(|m| m < 7) {
                return Err(parsed.error(
                    display,
                    "target_version must be a Python 3 minor, for example 3.10",
                ));
            }
        }
    }
    if let Some(number) = value.as_f64() {
        let (minimum, maximum) = match last {
            "min_tem_score" => (0.0, 5.0),
            "min_line_cov"
            | "min_file_cov"
            | "min_branch_cov"
            | "min_func_cov"
            | "min_changed_line_cov"
            | "max_coverage_regression"
            | "warn_pct"
            | "fail_pct" => (0.0, 100.0),
            "repeat" | "repeat_runs" => (1.0, 3.0),
            "max_slow_tests" => (1.0, 1000.0),
            "jobs" | "max_artifacts" => (1.0, 64.0),
            "max_wheels" | "max_cases" => (1.0, 32.0),
            "max_wheel_members" => (1.0, 8192.0),
            "max_wheel_uncompressed_bytes" => (1.0, 67108864.0),
            "min_size" => (0.0, 67108864.0),
            "max_output_bytes" => (1024.0, 8388608.0),
            "timeout" => (0.1, 3600.0),
            "timeout_seconds" => (0.1, 300.0),
            "slow_threshold" | "slow_test_threshold" => (0.0, 86400.0),
            "expected_exit" => (i32::MIN as f64, i32::MAX as f64),
            "max_findings" => (0.0, 100000.0),
            _ => (1.0, i64::MAX as f64),
        };
        if number < minimum || number > maximum {
            return Err(parsed.error(display, format!("must be between {minimum} and {maximum}")));
        }
    }
    if let Some(array) = value.as_array().filter(|_| STRING_LISTS.contains(&last)) {
        let maximum = match last {
            "interpreters" | "required_interpreters" | "wheel_globs" | "inherit_env" => 32,
            "imports" => 64,
            "clang_tidy_checks" | "clazy_checks" | "secret_name_allowlist" => 128,
            "forbidden_needed" | "allowed_needed" => 256,
            _ => 10000,
        };
        if array.len() > maximum {
            return Err(parsed.error(display, format!("must contain at most {maximum} items")));
        }
        for item in array {
            let text = item.as_str().expect("string list checked");
            if text.is_empty() || text.len() > 4096 || text.chars().any(|c| c < ' ') {
                return Err(parsed.error(
                    display,
                    "list items must be nonempty, bounded printable strings",
                ));
            }
        }
        if matches!(
            last,
            "languages"
                | "interpreters"
                | "required_interpreters"
                | "imports"
                | "wheel_globs"
                | "clang_tidy_checks"
                | "clazy_checks"
        ) {
            let values: BTreeSet<_> = array.iter().map(|v| v.as_str().unwrap()).collect();
            if values.len() != array.len() {
                return Err(parsed.error(display, "duplicate values are not allowed"));
            }
        }
        if last == "argv" || last == "command" || last.ends_with("_argv") {
            validate_argv(parsed, array, display, last)?;
        }
        validate_list_items(parsed, display, last, parent, array)?;
        if last == "languages"
            && (array.is_empty()
                || array.iter().any(|v| {
                    !["python", "cpp", "rust", "javascript", "typescript"]
                        .contains(&v.as_str().unwrap())
                }))
        {
            return Err(parsed.error(display, "languages must name supported languages"));
        }
    }
    Ok(())
}

fn validate_string_map(
    parsed: &Parsed,
    map: &serde_json::Map<String, Value>,
    display: &str,
    kind: &str,
    family: SchemaFamily,
) -> Result<(), ConfigError> {
    if map.len() > 32 {
        return Err(parsed.error(display, "mapping may contain at most 32 values"));
    }
    for (key, value) in map {
        let valid_key = if kind == "env" {
            valid_env_name(key) && (family == SchemaFamily::Legacy || !key.starts_with("ICI_"))
        } else {
            // Frozen `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`.
            key.len() <= 64
                && key
                    .chars()
                    .next()
                    .is_some_and(|c| c.is_ascii_alphanumeric())
                && key
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "_.-".contains(c))
        };
        let limit = if kind == "env" { 4096 } else { 1024 };
        if !valid_key
            || value.as_str().is_none_or(|s| {
                s.chars().count() > limit
                    || s.chars().any(|c| c < ' ')
                    || (kind == "python_targets" && s.is_empty())
            })
        {
            return Err(parsed.error(&join(display, key), "invalid environment/target mapping"));
        }
    }
    Ok(())
}

pub(crate) fn relationships(
    parsed: &Parsed,
    value: &Value,
    prefix: &str,
) -> Result<(), ConfigError> {
    fn visit(parsed: &Parsed, value: &Value, prefix: &str, errors: &mut Vec<ConfigError>) {
        if errors.len() >= 64 {
            return;
        }
        if let Err(error) = relationships_here(parsed, value, prefix) {
            errors.push(error);
        }
        if let Some(table) = value.as_object() {
            for (key, child) in table {
                visit(parsed, child, &join(prefix, key), errors);
            }
        } else if let Some(array) = value.as_array() {
            for (index, child) in array.iter().enumerate() {
                visit(parsed, child, &format!("{prefix}[{index}]"), errors);
            }
        }
    }
    let mut errors = vec![];
    visit(parsed, value, prefix, &mut errors);
    ConfigError::collect(errors)
}

fn relationships_here(parsed: &Parsed, value: &Value, prefix: &str) -> Result<(), ConfigError> {
    if let Some(table) = value.as_object() {
        for (warn, fail) in [
            ("warn_limit", "fail_limit"),
            ("warn_cc", "fail_cc"),
            ("warn_pct", "fail_pct"),
            ("warn", "fail"),
        ] {
            if let (Some(a), Some(b)) = (
                table.get(warn).and_then(Value::as_f64),
                table.get(fail).and_then(Value::as_f64),
            ) && a > b
            {
                return Err(parsed.error(&join(prefix, warn), format!("must not exceed {fail}")));
            }
        }
        for (left, right) in [
            ("repeat", "repeat_runs"),
            ("slow_threshold", "slow_test_threshold"),
        ] {
            if table.contains_key(left) && table.contains_key(right) {
                return Err(parsed.error(prefix, format!("declare only one of {left} and {right}")));
            }
        }
        if let Some(required) = table.get("required_interpreters").and_then(Value::as_array) {
            let interpreters = table.get("interpreters").and_then(Value::as_array);
            if required
                .iter()
                .any(|item| interpreters.is_none_or(|items| !items.contains(item)))
            {
                return Err(parsed.error(
                    &join(prefix, "required_interpreters"),
                    "must be a subset of interpreters",
                ));
            }
        }
        if table.get("config").is_some()
            && (prefix.starts_with("components[") || prefix == "component")
            && table.keys().any(|key| key != "id" && key != "config")
        {
            return Err(parsed.error(
                prefix,
                "component may be defined inline or referenced by config, not both",
            ));
        }
        if prefix.starts_with("suppressions[") {
            if table
                .get("reason")
                .and_then(Value::as_str)
                .is_none_or(|s| s.trim().is_empty())
            {
                return Err(parsed.error(prefix, "suppression must carry a reason"));
            }
            if !["fingerprint", "rule", "path", "component"]
                .iter()
                .any(|key| table.contains_key(*key))
            {
                return Err(parsed.error(
                    prefix,
                    "suppression must select a fingerprint, rule, path or component",
                ));
            }
        }
        let integration_case = prefix.ends_with(']')
            && (prefix.contains(".integrations[")
                || prefix.starts_with("engines.integration.cases["))
            && !prefix.contains("output_artifacts[");
        let missing_case_fields = table
            .get("name")
            .and_then(Value::as_str)
            .is_none_or(|s| s.trim().is_empty() || s.len() > 128)
            || table
                .get("argv")
                .and_then(Value::as_array)
                .is_none_or(Vec::is_empty);
        if integration_case && missing_case_fields {
            return Err(parsed.error(
                prefix,
                "integration case requires a bounded name and direct argv",
            ));
        }
        if prefix.contains("output_artifacts[") && prefix.ends_with(']') {
            let path = table
                .get("path")
                .and_then(Value::as_str)
                .ok_or_else(|| parsed.error(prefix, "output artifact requires path"))?;
            crate::paths::check_glob(path, &parsed.origin(&join(prefix, "path")))?;
            if path == "." || path.split('/').any(|part| part.is_empty() || part == ".") {
                return Err(parsed.error(
                    &join(prefix, "path"),
                    "output path must be canonical and not the project root",
                ));
            }
        }
    } else if let Some(array) = value.as_array() {
        if ["cases", "integrations", "output_artifacts"]
            .iter()
            .any(|key| prefix.ends_with(key))
            && array.len() > 32
        {
            return Err(parsed.error(
                prefix,
                "at most 32 integration cases/assertions are allowed",
            ));
        }
        let id_key = if prefix.ends_with("components") {
            Some("id")
        } else if prefix.ends_with("cases") || prefix.ends_with("integrations") {
            Some("name")
        } else if prefix.ends_with("output_artifacts") {
            Some("path")
        } else {
            None
        };
        if let Some(key) = id_key {
            let ids: Vec<_> = array
                .iter()
                .filter_map(|v| v.get(key).and_then(Value::as_str))
                .collect();
            if ids.len() != ids.iter().collect::<BTreeSet<_>>().len() {
                return Err(parsed.error(prefix, format!("duplicate {key} in {prefix}")));
            }
        }
    }
    Ok(())
}

/// Frozen per-key string bounds (`config_schema.py`, `_analysis_config.py`).
fn validate_text_bounds(
    parsed: &Parsed,
    display: &str,
    last: &str,
    text: &str,
) -> Result<(), ConfigError> {
    let characters = text.chars().count();
    let printable = !text.chars().any(|c| c < ' ');
    let valid = match last {
        "clang_tidy_config" => characters <= 4096,
        "expected_class" | "expected_machine" | "kind" => characters <= 128 && printable,
        "max_glibc" | "max_glibcxx" | "max_cxxabi" => text.is_empty() || abi_version(text),
        _ => true,
    };
    if valid {
        Ok(())
    } else {
        Err(parsed.error(
            display,
            "string exceeds the frozen bound or format for this key",
        ))
    }
}
fn abi_version(text: &str) -> bool {
    let mut parts = text.split('.');
    text.len() <= 32
        && parts.next().is_some_and(all_digits)
        && text.contains('.')
        && parts.all(all_digits)
}
fn all_digits(part: &str) -> bool {
    !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit())
}
/// Python `str.isidentifier()` for configuration names: ASCII is exact; non-ASCII uses the
/// Rust Unicode alphabetic/numeric properties (XID tables live in ici-analysis, above config).
fn python_identifier(text: &str) -> bool {
    let mut characters = text.chars();
    characters
        .next()
        .is_some_and(|c| c == '_' || c.is_alphabetic())
        && characters.all(|c| c == '_' || c.is_alphanumeric())
}
fn contained_glob(text: &str) -> bool {
    let bytes = text.as_bytes();
    let drive = bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':';
    text.chars().count() <= 256
        && !text.contains('\\')
        && !text.starts_with('/')
        && !drive
        && !text.split('/').any(|part| part == "..")
}
/// Frozen list-member rules that depend on the key, after generic type/bound checks.
fn validate_list_items(
    parsed: &Parsed,
    display: &str,
    last: &str,
    parent: &str,
    array: &[Value],
) -> Result<(), ConfigError> {
    let items: Vec<&str> = array.iter().filter_map(Value::as_str).collect();
    let unique = || items.iter().collect::<BTreeSet<_>>().len() == items.len();
    let problem = match last {
        "clang_tidy_checks" | "clazy_checks" => (items.is_empty()
            || items.len() > 128
            || items.iter().any(|item| {
                item.chars().count() > 128
                    || !item
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || "_.*-".contains(c))
            })
            || items.join(",").len() > 8192)
            .then_some("tool checks must be 1 to 128 unique names using letters, digits, _ . * -"),
        "imports" => items
            .iter()
            .any(|item| !item.split('.').all(python_identifier))
            .then_some("imports must be dotted Python module names"),
        "secret_name_allowlist" => {
            let folded: BTreeSet<_> = items.iter().map(|item| item.to_lowercase()).collect();
            (items
                .iter()
                .any(|item| item.chars().count() > 128 || !python_identifier(item))
                || folded.len() != items.len())
            .then_some(
                "names must be unique (ignoring case) Python identifiers of at most 128 characters",
            )
        }
        "wheel_globs" | "python_wheel" | "report" => (items
            .iter()
            .any(|item| !contained_glob(item))
            || !unique()
            || (last != "wheel_globs" && items.len() > 32))
            .then_some("globs must be unique contained POSIX patterns of at most 256 characters"),
        "artifacts" if parent == "binary_compat" => {
            (items.len() > 64 || !unique() || items.iter().any(|item| item.chars().count() > 1024))
                .then_some("artifacts must contain at most 64 unique bounded strings")
        }
        "forbidden_needed" | "allowed_needed" => (!unique()
            || items.iter().any(|item| item.chars().count() > 1024))
        .then_some("dependency names must be unique bounded strings"),
        "inherit_env" => (!unique() || items.iter().any(|item| !valid_env_name(item)))
            .then_some("inherit_env must name unique environment variables"),
        // Legacy `_validate_string_list_bounded(unique=True)`; next integrations allow repeats.
        "stdout_contains" | "stderr_contains" | "stdout_not_contains" | "stderr_not_contains"
            if display.starts_with("engines.integration.cases[") =>
        {
            (!unique() || items.iter().any(|item| item.chars().count() > 1024))
                .then_some("legacy integration assertions must be unique bounded strings")
        }
        _ => None,
    };
    match problem {
        Some(message) => Err(parsed.error(display, message)),
        None => Ok(()),
    }
}

pub(crate) fn valid_env_name(value: &str) -> bool {
    let mut chars = value.chars();
    chars
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
        && value.len() <= 128
}
pub(crate) fn join(parent: &str, key: &str) -> String {
    if parent.is_empty() {
        key.into()
    } else {
        format!("{parent}.{key}")
    }
}

fn validate_argv(
    parsed: &Parsed,
    argv: &[Value],
    display: &str,
    name: &str,
) -> Result<(), ConfigError> {
    let maximum = if name == "command" { 32 } else { 64 };
    if argv.len() > maximum || (!name.ends_with("_argv") && argv.is_empty()) {
        return Err(parsed.error(
            display,
            format!("command must contain 1 to {maximum} direct arguments"),
        ));
    }
    let words: Vec<_> = argv
        .iter()
        .map(|v| v.as_str().expect("validated argv strings"))
        .collect();
    if words.iter().any(|word| word.len() > 1024)
        || words.iter().map(|word| word.len()).sum::<usize>() > 32768
    {
        return Err(parsed.error(display, "command arguments exceed configured bounds"));
    }
    let shell = words.first().map(|word| {
        std::path::Path::new(word)
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_ascii_lowercase()
    });
    if shell.as_deref().is_some_and(|name| {
        [
            "sh",
            "bash",
            "dash",
            "zsh",
            "fish",
            "cmd",
            "powershell",
            "pwsh",
        ]
        .contains(&name)
    }) || words
        .iter()
        .skip(1)
        .any(|word| ["-c", "/c", "-command"].contains(&word.to_ascii_lowercase().as_str()))
    {
        return Err(parsed.error(
            display,
            "invoke a tool directly, not through a command shell",
        ));
    }
    if name == "argv" {
        if words.first().is_none_or(|word| !typed_placeholder(word)) {
            return Err(parsed.error(display, "integration argv[0] must be a typed {python:NAME} or {artifact:BUILD/PATH} placeholder"));
        }
        if words
            .iter()
            .any(|word| (word.contains('{') || word.contains('}')) && !typed_placeholder(word))
        {
            return Err(parsed.error(
                display,
                "integration placeholders must be known whole tokens",
            ));
        }
    } else if words.iter().any(|word| {
        word.contains('$')
            || word.contains('`')
            || ((word.contains('{') || word.contains('}')) && *word != "{jobs}")
    }) {
        return Err(parsed.error(
            display,
            "direct argv must not use shell expansion or unknown placeholders",
        ));
    }
    Ok(())
}
fn typed_placeholder(value: &str) -> bool {
    let inside = value.strip_prefix('{').and_then(|v| v.strip_suffix('}'));
    inside.is_some_and(|s| {
        (s.starts_with("python:") || s.starts_with("artifact:"))
            && s.split_once(':')
                .is_some_and(|(_, name)| !name.is_empty() && !name.contains(['{', '}']))
    })
}
