//! Frozen `LintEngine.run` `extra` counters of a legacy lint report with C++ sources
//! (engines/lint.py): the C++ syntax, Qt generated-code and clang tool counters the producers
//! recorded as `legacy_lint_counters`, and the diagnostic summaries frozen derives from its
//! `CppDiagnostic` list, which the report's C++ findings are one to one with.
use serde_json::{Map, Value, json};

/// Frozen `FindingCategory` values in declaration order.
const CATEGORIES: [&str; 9] = [
    "correctness",
    "type",
    "security",
    "resource",
    "build",
    "test",
    "maintainability",
    "architecture",
    "compatibility",
];
const FAMILIES: [&str; 4] = ["compiler", "clang-tidy", "clang-analyzer", "clazy"];
const FALLBACK: &str =
    "No compilation database is available; C++ syntax analysis used the c++17 heuristic fallback";

/// The diagnostic family of a frozen `_cpp_findings` entry (its explanation names it); other
/// findings (Qt generated code, adapted targets) are not C++ diagnostics.
fn family(finding: &Value) -> Option<&'static str> {
    if finding["rule_id"] != "ici.legacy.lint.target" {
        return None;
    }
    match finding["explanation"].as_str()? {
        "Compiler diagnostic from a sanitized translation-unit replay." => Some("compiler"),
        "clang-tidy diagnostic." => Some("clang-tidy"),
        "Clang Static Analyzer diagnostic." => Some("clang-analyzer"),
        "Qt-aware clazy diagnostic." => Some("clazy"),
        _ => None,
    }
}

fn strings(value: &Value) -> impl Iterator<Item = &str> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
}

/// Fill the frozen lint `extra` keys of a legacy lint engine whose native checks include a C++
/// check; `config` is the component's `[engines.lint]` table. Python-only lint engines are left
/// to their own projection. `python_sources` is the selected Python sources frozen
/// `_check_python_syntax` parses beside Ruff.
pub(crate) fn shape(engine: &mut Value, config: Option<&Value>, python_sources: usize) {
    let checks = engine["extra"]["native_checks"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if !checks.iter().any(|check| {
        matches!(
            check["check_id"].as_str(),
            Some("cpp.diagnostics" | "cpp.tidy")
        )
    }) {
        return;
    }
    let mut counters = Map::new();
    for (key, value) in [
        ("cpp_analysis_mode", json!("not_applicable")),
        ("cpp_configurations_checked", json!(0)),
        ("cpp_sources_checked", json!(0)),
        ("cpp_context_missing", json!(0)),
        ("qt_codegen_mode", json!("not_applicable")),
        ("qt_codegen_inputs_checked", json!(0)),
        ("qt_codegen_ui_checked", json!(0)),
        ("qt_codegen_qrc_checked", json!(0)),
        ("qt_codegen_moc_checked", json!(0)),
        ("qt5_compile_units", json!(0)),
        ("qt6_compile_units", json!(0)),
    ] {
        counters.insert(key.into(), value);
    }
    // Clang tools without a `cpp.tidy` output (both disabled): their configured modes.
    if let Some(defaults) = super::legacy_tooling::unplanned_counters(&[], config).as_object() {
        counters.extend(defaults.clone());
    }
    let mut warnings: Vec<String> = vec![];
    for check in &checks {
        let extra = &check["extra"];
        if let Some(recorded) = extra["legacy_lint_counters"].as_object() {
            counters.extend(recorded.clone());
        }
        warnings.extend(strings(&extra["legacy_qt_warnings"]).map(str::to_owned));
        warnings.extend(strings(&extra["legacy_tool_warnings"]).map(str::to_owned));
        warnings.extend(strings(&extra["legacy_ruff"]["warnings"]).map(str::to_owned));
    }
    let tooling = checks.iter().any(|check| {
        check["extra"]["legacy_lint_counters"]
            .get("clang_tidy_mode")
            .is_some()
    });
    if !tooling && counters["cpp_analysis_mode"] == "not_applicable" {
        // Frozen `_lint_cpp` never ran without C++ sources: the outcome defaults stay.
        for (key, value) in [
            ("clang_tidy_mode", json!("not_applicable")),
            ("clazy_mode", json!("not_applicable")),
            ("clazy_provider", json!("none")),
            ("clazy_profile", json!("level0")),
        ] {
            counters.insert(key.into(), value);
        }
    }
    if checks
        .iter()
        .any(|check| check["extra"]["legacy_fallback_warned"] == true)
    {
        warnings.insert(0, FALLBACK.into());
    }
    let targets = engine["targets"].as_array().cloned().unwrap_or_default();
    let count = |status: &str| {
        targets
            .iter()
            .filter(|target| target["status"] == status)
            .count()
    };
    let fail = count("FAIL");
    let warn = count("WARN")
        + warnings
            .iter()
            .filter(|warning| {
                !targets
                    .iter()
                    .any(|target| target["status"] == "WARN" && target["message"] == **warning)
            })
            .count();
    let findings = engine["findings"].as_array().cloned().unwrap_or_default();
    let diagnostics: Vec<(&str, &Value)> = findings
        .iter()
        .filter_map(|finding| Some((family(finding)?, finding)))
        .collect();
    let mut fixits = vec![];
    for (family, finding) in &diagnostics {
        for fix in finding["fixes"].as_array().into_iter().flatten() {
            for replacement in fix["replacements"].as_array().into_iter().flatten() {
                fixits.push(json!({
                    "family": family, "rule": finding["tool_rule_id"],
                    "file_path": replacement["path"], "start_line": replacement["start_line"],
                    "start_column": replacement["start_column"], "end_line": replacement["end_line"],
                    "end_column": replacement["end_column"], "replacement": replacement["replacement"],
                }));
            }
        }
    }
    let extra = &mut engine["extra"];
    extra["violations_count"] = json!(
        targets
            .iter()
            .filter(|target| !matches!(target["status"].as_str(), Some("PASS" | "SKIP")))
            .count()
    );
    if extra.get("python_files_parsed").is_none() {
        // Frozen `_check_python_syntax` parses every selected Python source (none for C++).
        extra["python_files_parsed"] = json!(python_sources);
    }
    for (key, value) in counters {
        extra[key] = value;
    }
    extra["cpp_diagnostic_families"] = json!(
        FAMILIES
            .iter()
            .map(|name| {
                (
                    (*name).to_owned(),
                    json!(
                        diagnostics
                            .iter()
                            .filter(|(family, _)| family == name)
                            .count()
                    ),
                )
            })
            .collect::<Map<_, _>>()
    );
    extra["cpp_diagnostic_category_policy"] = json!("tool-rule-v1");
    extra["cpp_diagnostic_categories"] = json!(
        CATEGORIES
            .iter()
            .map(|name| {
                (
                    (*name).to_owned(),
                    json!(
                        diagnostics
                            .iter()
                            .filter(|(_, finding)| finding["category"] == *name)
                            .count()
                    ),
                )
            })
            .collect::<Map<_, _>>()
    );
    extra["cpp_fixits_total"] = json!(fixits.len());
    fixits.truncate(1_000);
    extra["cpp_fixits"] = json!(fixits);
    extra["cpp_related_notes"] = json!(
        diagnostics
            .iter()
            .map(|(_, finding)| finding["related_locations"].as_array().map_or(0, Vec::len))
            .sum::<usize>()
    );
    extra["metrics_summary"] = json!(format!("{} issues", fail + warn));
}
