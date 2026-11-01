//! Frozen `core/support.py` capability matrix for the historical report views.
//!
//! The declaration table is frozen v0.13.0 `_DECLARATIONS` verbatim; `legacy_support_matrix` is
//! frozen `evaluate_support_matrix` over the already-discovered project languages/frameworks, the
//! effective `[engines]` policy after the analysis profile and the shaped legacy engine results.
use ici_domain::builtin_catalog;
use serde_json::{Value, json};

struct Declaration {
    engine: &'static str,
    language: &'static str,
    mode: &'static str,
    confidence: &'static str,
    frameworks: &'static [&'static str],
    required: &'static [&'static str],
    optional: &'static [&'static str],
    fallback: Option<&'static str>,
    limitations: &'static [&'static str],
}

const QT: &[&str] = &["qt"];
const NONE: &[&str] = &[];
const BUILD_TOOLS: &[&str] = &["cmake", "qmake", "make", "pkg-config"];

macro_rules! declare {
    ($engine:literal, $language:literal, $mode:literal, $confidence:literal,
     frameworks: $frameworks:expr, required: $required:expr, optional: $optional:expr,
     fallback: $fallback:expr, limitations: [$($limitation:expr),* $(,)?]) => {
        Declaration {
            engine: $engine,
            language: $language,
            mode: $mode,
            confidence: $confidence,
            frameworks: $frameworks,
            required: $required,
            optional: $optional,
            fallback: $fallback,
            limitations: &[$($limitation),*],
        }
    };
}

const DECLARATIONS: &[Declaration] = &[
    declare!("line", "python", "exact", "exact", frameworks: NONE, required: NONE, optional: NONE, fallback: None,
        limitations: ["Counts physical source lines; it does not measure semantic size."]),
    declare!("line", "cpp", "exact", "exact", frameworks: QT, required: NONE, optional: NONE, fallback: None,
        limitations: ["Counts physical source lines; generated files outside source scope are excluded."]),
    declare!("lint", "python", "tool-backed", "high", frameworks: NONE, required: NONE, optional: &["ruff"],
        fallback: Some("heuristic"),
        limitations: ["Without Ruff, fallback validates AST syntax only, not style or lint rules."]),
    declare!("lint", "cpp", "tool-backed", "high", frameworks: QT, required: NONE,
        optional: &["gcc", "g++", "clang", "clang++", "clang-tidy", "clazy", "pkg-config"],
        fallback: Some("heuristic"),
        limitations: ["Replays sanitized GCC/Clang translation-unit commands and optional clang-tidy/Clazy checks when compilation context exists; without it, a fixed c++17 syntax fallback is estimated."]),
    declare!("compile_db", "python", "unsupported", "low", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None, limitations: ["Compilation databases apply only to C/C++ translation units."]),
    declare!("compile_db", "cpp", "exact", "exact", frameworks: QT, required: NONE, optional: NONE,
        fallback: Some("heuristic"),
        limitations: ["Exact flag and coverage evidence requires a valid compile_commands.json; absence is reported explicitly."]),
    declare!("test", "python", "tool-backed", "high", frameworks: NONE, required: &["python3"],
        optional: &["coverage", "pytest"], fallback: Some("heuristic"),
        limitations: ["Falls back from pytest to unittest only when pytest is unavailable; coverage is estimated when optional evidence is absent."]),
    declare!("test", "cpp", "tool-backed", "high", frameworks: QT, required: &["g++"],
        optional: &["cmake", "qmake", "make", "gcov", "pkg-config"], fallback: Some("heuristic"),
        limitations: ["Uses CMake/CTest or qmake/QtTest when declared, otherwise a generic g++ harness; coverage is estimated without valid gcov evidence."]),
    declare!("type", "python", "tool-backed", "high", frameworks: NONE, required: NONE, optional: &["mypy"],
        fallback: Some("heuristic"),
        limitations: ["Without mypy, fallback checks annotations structurally and cannot prove type safety."]),
    declare!("type", "cpp", "unsupported", "low", frameworks: NONE, required: NONE, optional: NONE, fallback: None,
        limitations: ["C++ semantic type analysis is not implemented; compiler syntax checks belong to lint."]),
    declare!("python_compat", "python", "tool-backed", "high", frameworks: NONE, required: &["python3"],
    optional: NONE, fallback: None,
    limitations: [
        "Configured interpreters compile and import selected project modules in contained subprocesses; this is not a sandbox.",
        "Static standard-library API compatibility uses a bounded documented inventory.",
    ]),
    declare!("python_compat", "cpp", "unsupported", "low", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None, limitations: ["Python runtime compatibility does not apply to C++ source."]),
    declare!("cognitive", "python", "heuristic", "medium", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["AST-based cognitive score is an ici policy metric, not a compiler proof."]),
    declare!("cognitive", "cpp", "unsupported", "low", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None, limitations: ["C++ cognitive complexity is not implemented."]),
    declare!("resource", "python", "heuristic", "medium", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["AST patterns cover known resource and mutable-default risks, not runtime ownership."]),
    declare!("resource", "cpp", "unsupported", "low", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["C++ ownership/resource analysis is not implemented; runtime leaks belong to sanitize."]),
    declare!("security", "python", "heuristic", "medium", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["Offline source patterns cover selected secret and unsafe-API rules, not dependency CVEs."]),
    declare!("security", "cpp", "unsupported", "low", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None, limitations: ["C++ security rule coverage is not implemented."]),
    declare!("cycle", "python", "heuristic", "high", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["Import-to-module resolution is best effort for dynamic and ambiguous imports."]),
    declare!("cycle", "cpp", "tool-backed", "high", frameworks: QT, required: NONE,
        optional: &["gcc", "g++", "clang", "clang++", "cmake", "readelf", "addr2line"],
        fallback: Some("heuristic"),
        limitations: ["Compiler traces measure active include edges for covered translation units; without compilation context, project-path suffix resolution is estimated."]),
    declare!("complexity", "python", "heuristic", "high", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["Cyclomatic and nesting metrics approximate maintainability rather than correctness."]),
    declare!("complexity", "cpp", "tool-backed", "medium", frameworks: QT, required: NONE,
    optional: &["clang-tidy"], fallback: Some("heuristic"),
    limitations: [
        "Clang AST-backed function boundaries require an exact compilation database; empty, unreported, or macro-generated definitions may use the lower-confidence source scanner.",
        "Suppressed or malformed diagnostics and attempted-tool failures fail closed.",
        "Cyclomatic and nesting counts within a confirmed boundary remain token-based.",
    ]),
    declare!("sanitize", "python", "tool-backed", "high", frameworks: NONE, required: &["python3", "pytest"],
        optional: NONE, fallback: None,
        limitations: ["Promotes ResourceWarning during pytest; it does not instrument Python memory safety."]),
    declare!("sanitize", "cpp", "tool-backed", "high", frameworks: QT, required: &["g++"],
        optional: BUILD_TOOLS, fallback: None,
        limitations: ["Runs ASan/UBSan tests; unexecuted code paths remain unobserved."]),
    declare!("thread_sanitize", "python", "unsupported", "low", frameworks: NONE, required: NONE,
        optional: NONE, fallback: None,
        limitations: ["ThreadSanitizer applies only to compiled C/C++ tests."]),
    declare!("thread_sanitize", "cpp", "tool-backed", "high", frameworks: QT, required: &["g++"],
        optional: BUILD_TOOLS, fallback: None,
        limitations: ["Runs only in the deep profile and observes only thread interleavings reached by tests."]),
    declare!("dead", "python", "heuristic", "medium", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["AST reachability and reference rules cannot resolve all dynamic Python uses."]),
    declare!("dead", "cpp", "tool-backed", "exact", frameworks: QT, required: NONE,
    optional: &["gcc", "g++", "clang", "clang++"], fallback: None,
    limitations: [
        "Exact findings cover only compiler-diagnosed -Wunused-function definitions whose primary location is attributed to a selected owned translation unit and agreed across all of that source's known configurations.",
        "GNU ELF linker evidence is target-local and only accepts uniquely mapped local or hidden function sections explicitly discarded from a supported direct-object CMake Release link.",
        "Dynamic lookup, exported symbols, archives, shared objects, LTO, linker scripts, COMDAT groups, plugins and Qt meta-object reachability remain excluded. Compiler diagnostics retain their compiler-attributed expansion location.",
    ]),
    declare!("dup", "python", "heuristic", "medium", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["Token-window similarity can miss semantic clones and report intentional repetition."]),
    declare!("dup", "cpp", "heuristic", "medium", frameworks: QT, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["Token-window similarity does not expand macros or identify semantic clones."]),
    declare!("exception", "python", "heuristic", "medium", frameworks: NONE, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["AST patterns detect selected exception hazards but do not model runtime call graphs."]),
    declare!("exception", "cpp", "heuristic", "medium", frameworks: QT, required: NONE, optional: NONE,
        fallback: None,
        limitations: ["Lightweight parsing checks selected exception-safety patterns without full semantic analysis."]),
    declare!("build", "python", "tool-backed", "high", frameworks: NONE, required: &["python3"],
    optional: NONE, fallback: None,
    limitations: [
        "Packages configured Python sources and entry points into a contained release tree; it does not build or publish a wheel.",
        "Generated launchers use the selected runtime at execution time, so deployment environment dependencies remain outside this check.",
    ]),
    declare!("build", "cpp", "tool-backed", "high", frameworks: QT, required: &["g++"],
    optional: BUILD_TOOLS, fallback: None,
    limitations: [
        "Builds a contained Release artifact with the declared CMake, qmake, Make, or direct g++ path; unsupported build descriptions fail closed.",
        "The manifest records produced files and build evidence, not deployment host libraries or runtime behavior.",
    ]),
    declare!("binary_compat", "python", "unsupported", "low", frameworks: NONE, required: NONE,
        optional: NONE, fallback: None,
        limitations: ["ELF dependency and ABI checks apply to compiled artifacts, not Python source or pure-Python launchers."]),
    declare!("binary_compat", "cpp", "tool-backed", "exact", frameworks: QT, required: &["readelf"],
    optional: NONE, fallback: None,
    limitations: [
        "Inspects only executable and shared-library artifacts published by the Release build manifest; it never executes or loads them.",
        "ABI floors and loader/dependency policy cover the parsed ELF metadata and do not prove behavior on every deployment host.",
    ]),
    declare!("integration", "python", "tool-backed", "high", frameworks: NONE, required: &["python3"],
    optional: NONE, fallback: None,
    limitations: [
        "Runs explicitly declared, shell-free process contracts with bounded output and timeout; undeclared runtime behavior is out of scope.",
        "Python and artifact placeholders resolve only to validated targets published by the current analysis context.",
    ]),
    declare!("integration", "cpp", "tool-backed", "high", frameworks: QT, required: NONE, optional: NONE,
    fallback: None,
    limitations: [
        "Runs explicitly declared, shell-free contracts against validated C++ artifacts; it does not replace unit, GUI, or protocol test coverage.",
        "The process boundary is verified for declared exit, output, environment, and artifact assertions only.",
    ]),
];

/// Project facts frozen `evaluate_support_matrix` discovers before evaluating declarations.
#[derive(Clone, Debug, Default)]
pub struct LegacySupportInputs {
    /// Ordered subset of `["python", "cpp"]` (frozen `_project_languages`).
    pub languages: Vec<String>,
    /// Frozen `_project_frameworks`: `["qt"]` or empty.
    pub frameworks: Vec<String>,
    /// Effective analysis profile (`fast`, `standard` or `deep`).
    pub profile: String,
    /// The effective `[engines]` table (frozen `config["engines"]`) before the profile is applied.
    pub engines: Value,
}

/// Python truthiness of a configuration value (frozen `bool(...)`/`if value`).
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(items) => !items.is_empty(),
    }
}

fn engine_config<'a>(inputs: &'a LegacySupportInputs, engine: &str) -> &'a Value {
    &inputs.engines[engine]
}

fn text_setting<'a>(config: &'a Value, key: &str, default: &'a str) -> &'a str {
    config.get(key).and_then(Value::as_str).unwrap_or(default)
}

/// Frozen `apply_analysis_profile` then `_language_enabled`.
fn language_enabled(declaration: &Declaration, inputs: &LegacySupportInputs) -> bool {
    let in_profile = builtin_catalog()
        .legacy
        .iter()
        .find(|descriptor| descriptor.name == declaration.engine)
        .is_none_or(|descriptor| {
            descriptor.profiles.iter().any(|profile| {
                serde_json::to_value(profile)
                    .ok()
                    .as_ref()
                    .and_then(Value::as_str)
                    == Some(inputs.profile.as_str())
            })
        });
    let config = engine_config(inputs, declaration.engine);
    let enabled = in_profile && config.get("enabled").is_none_or(truthy);
    if enabled && declaration.engine == "dead" && declaration.language == "cpp" {
        return text_setting(config, "cpp_unused", "auto") != "off"
            || text_setting(config, "cpp_linker", "off") != "off";
    }
    enabled
}

fn promote(required: &mut Vec<&'static str>, optional: &mut Vec<&'static str>, tool: &'static str) {
    optional.retain(|item| *item != tool);
    if !required.contains(&tool) {
        required.push(tool);
    }
}

/// Frozen `_tool_policy`.
fn tool_policy(
    declaration: &Declaration,
    inputs: &LegacySupportInputs,
) -> (Vec<&'static str>, Vec<&'static str>) {
    let mut required = declaration.required.to_vec();
    let mut optional = declaration.optional.to_vec();
    let config = engine_config(inputs, declaration.engine);
    let flag = |key: &str| config.get(key).is_some_and(truthy);
    let promoted = match (declaration.engine, declaration.language) {
        ("lint", "python") => flag("ruff_required").then_some("ruff"),
        ("lint", "cpp") => {
            for (tool, key) in [("clang-tidy", "clang_tidy"), ("clazy", "clazy")] {
                match config.get(key).unwrap_or(&json!("auto")) {
                    Value::String(mode) if mode == "required" => {
                        promote(&mut required, &mut optional, tool);
                    }
                    Value::String(mode) if mode == "off" => optional.retain(|item| *item != tool),
                    _ => {}
                }
            }
            None
        }
        ("type", "python") => flag("mypy_required").then_some("mypy"),
        ("test", language) if flag("coverage_required") => Some(if language == "python" {
            "coverage"
        } else {
            "gcov"
        }),
        ("dead", "cpp") => {
            if text_setting(config, "cpp_unused", "auto") == "off" {
                optional.retain(|item| !matches!(*item, "clang" | "clang++"));
            }
            match text_setting(config, "cpp_linker", "off") {
                "off" => {
                    optional.retain(|item| !matches!(*item, "cmake" | "readelf" | "addr2line"))
                }
                "required" => {
                    for tool in ["cmake", "readelf", "addr2line"] {
                        promote(&mut required, &mut optional, tool);
                    }
                }
                _ => {}
            }
            None
        }
        _ => None,
    };
    if let Some(tool) = promoted
        && optional.contains(&tool)
    {
        promote(&mut required, &mut optional, tool);
    }
    (required, optional)
}

fn confidence(declared: &'static str, evidence: &str) -> &'static str {
    match evidence {
        "NOT_APPLICABLE" | "NOT_RUN" => "low",
        "ESTIMATED" if matches!(declared, "exact" | "high") => "medium",
        _ => declared,
    }
}

const EVIDENCE: [&str; 4] = ["MEASURED", "ESTIMATED", "NOT_RUN", "NOT_APPLICABLE"];

/// Frozen `_language_evidence`: an engine's per-language evidence overrides its own.
fn language_evidence<'a>(result: &'a Value, language: &str) -> &'a str {
    let own = result["evidence"].as_str().unwrap_or("NOT_RUN");
    match result["extra"]["language_evidence"].get(language) {
        Some(Value::String(value)) if EVIDENCE.contains(&value.as_str()) => value,
        _ => own,
    }
}

/// Frozen `evaluate_support_matrix(root, config, results, engine_names=...)`.
pub fn legacy_support_matrix(
    inputs: &LegacySupportInputs,
    results: &[Value],
    engine_names: Option<&[&str]>,
) -> Value {
    let mut entries = vec![];
    for declaration in DECLARATIONS {
        if engine_names.is_some_and(|names| !names.contains(&declaration.engine)) {
            continue;
        }
        let enabled = language_enabled(declaration, inputs);
        let present = inputs
            .languages
            .iter()
            .any(|language| language == declaration.language);
        let supported = declaration.mode != "unsupported";
        let applicable = present && supported;
        // Frozen `{result.engine_name: result}`: a later result of the same engine wins.
        let result = results
            .iter()
            .rev()
            .find(|result| result["engine_name"] == declaration.engine);
        let (evidence, reason) = if !present {
            (
                "NOT_APPLICABLE",
                format!(
                    "project has no discovered {} source scope",
                    declaration.language
                ),
            )
        } else if !supported {
            (
                "NOT_APPLICABLE",
                format!(
                    "{} does not support {}",
                    declaration.engine, declaration.language
                ),
            )
        } else if !enabled {
            ("NOT_RUN", "engine is disabled by effective policy".into())
        } else if let Some(result) = result {
            let evidence = language_evidence(result, declaration.language);
            (
                evidence,
                format!("observed engine result reported {evidence} evidence"),
            )
        } else {
            ("NOT_RUN", "applicable engine has not been run".into())
        };
        let active_mode = match evidence {
            "MEASURED" if applicable && enabled => Some(declaration.mode),
            "ESTIMATED" if applicable && enabled => {
                Some(declaration.fallback.unwrap_or(declaration.mode))
            }
            _ => None,
        };
        let (required, optional) = tool_policy(declaration, inputs);
        entries.push(json!({
            "engine_name": declaration.engine,
            "language": declaration.language,
            "mode": declaration.mode,
            "active_mode": active_mode,
            "applicable": applicable,
            "enabled": enabled,
            "evidence": evidence,
            "confidence": confidence(declaration.confidence, evidence),
            "frameworks": declaration.frameworks,
            "required_tools": required,
            "optional_tools": optional,
            "fallback_mode": declaration.fallback,
            "limitations": declaration.limitations,
            "reason": reason,
        }));
    }
    json!({
        "project_languages": inputs.languages,
        "project_frameworks": inputs.frameworks,
        "entries": entries,
    })
}

/// The frozen declaration registry (`support_declarations()`), for parity tests.
pub fn legacy_support_declarations() -> Value {
    Value::Array(
        DECLARATIONS
            .iter()
            .map(|declaration| {
                json!({
                    "engine_name": declaration.engine,
                    "language": declaration.language,
                    "mode": declaration.mode,
                    "confidence": declaration.confidence,
                    "frameworks": declaration.frameworks,
                    "required_tools": declaration.required,
                    "optional_tools": declaration.optional,
                    "fallback_mode": declaration.fallback,
                    "limitations": declaration.limitations,
                })
            })
            .collect(),
    )
}
