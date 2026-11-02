//! Frozen legacy `PythonCompatibilityEngine` verdict (`engines/python_compat.py`) over the native
//! static (`python.compat`) and runtime (`python.compat-runtime`) checks. Native facts are only
//! re-expressed: one `PythonRuntime:<state>` target per configured interpreter at `.`, the frozen
//! syntax/analysis error targets, the frozen counters and summary, and the frozen `extra` keys.
use serde_json::{Value, json};

const LIMITATIONS: [&str; 3] = [
    "Import smoke executes project module top-level code in a contained subprocess, not a sandbox",
    "Static API rules cover a documented standard-library compatibility inventory",
    "C-extension ABI compatibility belongs to package and binary artifact analysis",
];

fn target(
    path: &str,
    line: Value,
    column: Value,
    name: &str,
    status: &str,
    message: &str,
    metrics: Value,
) -> Value {
    json!({"file_path":path,"start_line":line,"end_line":null,"start_column":column,
        "end_column":null,"target_name":name,"status":status,"message":message,"snippet":"",
        "metrics":metrics})
}

fn strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}

/// Frozen `_process_problem` over a native step record.
fn problem(step: Option<&Value>) -> Option<String> {
    let step = step?;
    if step["timed_out"] == true {
        return Some("timed out".into());
    }
    if step["truncated"] == true {
        return Some("output exceeded the bounded limit".into());
    }
    match step["exit_code"].as_i64() {
        Some(0) => None,
        Some(code) => Some(format!("exit code {code}")),
        None => Some(format!(
            "process did not report an exit status ({})",
            step["state"].as_str().unwrap_or("not run")
        )),
    }
}

struct Runtime {
    configured: String,
    required: bool,
    /// `None` when the interpreter could not be resolved.
    index: Option<usize>,
    version: Option<String>,
}

/// Steps of runtime `index` (`None`: the single launcher/project interpreter) ending in `suffix`.
fn step<'a>(steps: &'a [Value], index: Option<usize>, suffix: &str) -> Option<&'a Value> {
    steps.iter().find(|step| {
        let id = step["task_id"].as_str().unwrap_or_default();
        id.ends_with(suffix)
            && match index {
                Some(index) => id.contains(&format!(".runtime{index}.")),
                None => !id.contains(".runtime"),
            }
    })
}

fn version(value: &Value) -> Option<String> {
    let parts = value.as_array()?;
    Some(
        parts
            .iter()
            .map(|part| part.as_u64().map(|part| part.to_string()))
            .collect::<Option<Vec<_>>>()?
            .join("."),
    )
}

/// Rebuilds the frozen engine verdict. `settings` is the effective `engines.python_compat`.
/// Returns `false` (engine untouched) when no native Python compatibility check ran, e.g. the
/// C++ `cpp.python-compat` applicability check of a C++ project.
/// Frozen `_compile_targets` bound: more than this many compileall targets is an analysis error.
const MAX_COMPILE_TARGETS: usize = 128;
const COMPILE_TARGETS_EXCEEDED: &str = "Python compileall target count exceeds 128";

/// Frozen `_compile_targets` count: each selected Python source contributes its first configured
/// existing source directory, else the file itself; duplicates collapse.
pub(crate) fn compile_target_count(project: &ici_config::ResolvedProject) -> usize {
    let mut targets = std::collections::BTreeSet::new();
    for component in &project.components {
        let directories: Vec<_> = component
            .source_dirs
            .iter()
            .filter(|directory| crate::files::under(&project.root, directory).is_dir())
            .collect();
        for file in component.files.iter().filter(|file| file.ends_with(".py")) {
            let parent = directories.iter().find(|directory| {
                directory.as_str() == "."
                    || file.starts_with(&format!("{}/", directory.trim_end_matches('/')))
            });
            targets.insert(match parent {
                Some(directory) => format!("dir:{directory}"),
                None => format!("file:{file}"),
            });
        }
    }
    targets.len()
}

pub(crate) fn shape(
    engine: &mut Value,
    settings: &Value,
    compile_targets: usize,
) -> ici_reporting::Result<bool> {
    let checks = engine["extra"]["native_checks"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let check = |id: &str| checks.iter().find(|check| check["check_id"] == id).cloned();
    let static_check = check("python.compat");
    let runtime_check = check("python.compat-runtime");
    if static_check.is_none() && runtime_check.is_none() {
        return Ok(false);
    }
    let static_extra = static_check
        .as_ref()
        .map(|check| check["extra"].clone())
        .unwrap_or(Value::Null);
    let original = engine["targets"].as_array().cloned().unwrap_or_default();
    let mut targets = vec![];
    let mut errors = vec![];
    let mut static_warnings = 0;
    for item in &original {
        let name = item["target_name"].as_str().unwrap_or_default();
        let path = item["file_path"].as_str().unwrap_or_default();
        if let Some(rule) = name.strip_prefix("PythonAnalysis:") {
            // Frozen `SyntaxError` per file, or the first analysis exception (metadata, source
            // input or bound) located at its file, line 1.
            if rule == "invalid-syntax" {
                let line = item["start_line"].as_u64().unwrap_or(1).max(1);
                errors.push(format!("{path}:{line}: Python syntax could not be parsed"));
                targets.push(target(
                    path,
                    json!(line),
                    item["start_column"].clone(),
                    "Compatibility:SyntaxUnavailable",
                    "ERROR",
                    "Current ici runtime cannot parse this Python source",
                    json!({}),
                ));
            } else {
                let message = item["message"].as_str().unwrap_or_default();
                errors.push(message.to_owned());
                // Frozen raises its AST bounds as PythonMetadataError, located at pyproject.toml.
                let path = if rule == "compatibility-ast-limit" {
                    "pyproject.toml"
                } else {
                    path
                };
                targets.push(target(
                    path,
                    json!(1),
                    Value::Null,
                    "Compatibility:AnalysisUnavailable",
                    "ERROR",
                    message,
                    json!({}),
                ));
            }
        } else if name.starts_with("Compatibility:") || name.starts_with("Package:") {
            if name.starts_with("Compatibility:") && name != "Compatibility:StaticScan" {
                static_warnings += 1;
            }
            targets.push(item.clone());
        }
    }
    // Frozen order: static targets file by file (inventory order), then the package summaries,
    // then the runtimes. The sort is stable, so each file keeps its native target order.
    targets.sort_by_key(|item| {
        let package = item["target_name"]
            .as_str()
            .is_some_and(|name| name.starts_with("Package:"));
        let path = if package {
            String::new()
        } else {
            item["file_path"].as_str().unwrap_or_default().to_owned()
        };
        (package, path)
    });
    let syntax_errors = targets
        .iter()
        .filter(|item| item["target_name"] == "Compatibility:SyntaxUnavailable")
        .count() as u64;
    let package: Vec<Value> = engine["findings"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|finding| {
            finding["rule_id"]
                .as_str()
                .is_some_and(|rule| rule.starts_with("ici.package."))
        })
        .cloned()
        .map(|mut finding| {
            // Frozen `_python_packaging._finding` / `_python_wheel._finding` constants.
            finding["explanation"] = json!(
                "The declared package contract does not match the inspected source or wheel."
            );
            finding["remediation"] = json!(
                "Correct the package metadata or rebuild the wheel from the intended source tree."
            );
            finding["tool_name"] = json!("ici packaging inspector");
            finding
        })
        .collect();
    let package_failures = package
        .iter()
        .filter(|finding| matches!(finding["severity"].as_str(), Some("high" | "critical")))
        .count();
    let mut failures = package_failures;
    let mut warnings = static_warnings + package.len() - package_failures;
    let imports = strings(settings.get("imports"));
    let mut runtime_count = 0;
    if errors.is_empty()
        && let Some(runtime) = &runtime_check
    {
        let configured = strings(settings.get("interpreters"));
        let required = strings(settings.get("required_interpreters"));
        let mut steps = vec![];
        let mut matrix = None;
        for (key, value) in runtime["extra"].as_object().into_iter().flatten() {
            steps.extend(value["steps"].as_array().cloned().unwrap_or_default());
            if key == runtime["task_id"].as_str().unwrap_or_default() {
                matrix = value["runtime_matrix"].as_array().cloned();
            }
        }
        // A runtime matrix that ran (its steps can still leave the check BLOCKED, e.g. a timed
        // out version probe) is judged per interpreter like frozen `_check_runtime`.
        let runtimes: Vec<Runtime> = if let Some(matrix) = matrix {
            matrix
                .iter()
                .enumerate()
                .map(|(index, entry)| Runtime {
                    configured: entry["configured"].as_str().unwrap_or_default().into(),
                    required: entry["required"] == true,
                    index: (entry["state"] != "unavailable").then_some(index),
                    version: version(&entry["runtime"]["version"]),
                })
                .collect()
        } else if runtime["state"] == "BLOCKED" {
            let summary = runtime["summary"].as_str().unwrap_or_default();
            if configured.is_empty()
                || !summary.starts_with("no configured project runtime is available")
            {
                targets.push(target(
                    ".",
                    json!(1),
                    Value::Null,
                    "PythonRuntime:Unavailable",
                    "ERROR",
                    summary,
                    json!({"required":1}),
                ));
                failures += 1;
                vec![]
            } else {
                configured
                    .iter()
                    .map(|name| Runtime {
                        configured: name.clone(),
                        required: required.contains(name),
                        index: None,
                        version: None,
                    })
                    .collect()
            }
        } else {
            let configured = step(&steps, None, ".metadata")
                .and_then(|step| step["argv"][0].as_str())
                .unwrap_or_default()
                .to_owned();
            let version = runtime["observation"]["tool_versions"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|pair| pair[0] == "project-python")
                .and_then(|pair| pair[1].as_str())
                .map(str::to_owned);
            vec![Runtime {
                configured,
                required: true,
                index: Some(usize::MAX),
                version,
            }]
        };
        // Frozen `tool_evidence`: the version probe, compileall and import smoke of every
        // checked interpreter, in that order (native labels its allowlisted metadata probe).
        let mut evidence = vec![];
        for runtime in &runtimes {
            let Some(index) = runtime.index else { continue };
            let index = (index != usize::MAX).then_some(index);
            // A version problem stops frozen `_check_runtime` before compileall.
            let probes = if runtime.version.is_some() { 3 } else { 1 };
            for (suffix, name, version) in [
                (
                    ".metadata",
                    "python runtime metadata",
                    runtime.version.clone().unwrap_or_default(),
                ),
                (".compile", "python -m compileall", String::new()),
                (".imports", "python import smoke", String::new()),
            ]
            .into_iter()
            .take(probes)
            {
                let Some(step) = step(&steps, index, suffix) else {
                    continue;
                };
                evidence.push(json!({"name":name,"path":step["argv"][0],"version":version,
                    "argv":step["argv"],"returncode":step["exit_code"],
                    "timed_out":step["timed_out"] == true,"truncated":step["truncated"] == true,
                    "error":""}));
            }
        }
        engine["tool_evidence"] = json!(evidence);
        for runtime in runtimes {
            let required = i64::from(runtime.required);
            let (failing, advisory) = if runtime.required {
                ("ERROR", "FAIL")
            } else {
                ("WARN", "WARN")
            };
            let Some(index) = runtime.index else {
                targets.push(target(
                    ".",
                    json!(1),
                    Value::Null,
                    "PythonRuntime:Unavailable",
                    failing,
                    &format!(
                        "Configured interpreter is unavailable: {}",
                        runtime.configured
                    ),
                    json!({"required":required}),
                ));
                if runtime.required {
                    failures += 1
                } else {
                    warnings += 1
                }
                continue;
            };
            let index = (index != usize::MAX).then_some(index);
            if runtime.version.is_some() && compile_targets > MAX_COMPILE_TARGETS {
                // Frozen `_run_compileall` raises before compiling for the first interpreter
                // whose version was read; the engine stops checking runtimes there.
                errors.push(COMPILE_TARGETS_EXCEEDED.into());
                targets.push(target(
                    "pyproject.toml",
                    json!(1),
                    Value::Null,
                    "Compatibility:AnalysisUnavailable",
                    "ERROR",
                    COMPILE_TARGETS_EXCEEDED,
                    json!({}),
                ));
                break;
            }
            let Some(version) = runtime.version else {
                let reason = problem(step(&steps, index, ".metadata"))
                    .unwrap_or_else(|| "unparseable version".into());
                targets.push(target(
                    ".",
                    json!(1),
                    Value::Null,
                    "PythonRuntime:VersionUnavailable",
                    failing,
                    &format!("{}: {reason}", runtime.configured),
                    json!({}),
                ));
                if runtime.required {
                    failures += 1
                } else {
                    warnings += 1
                }
                continue;
            };
            let mut problems = vec![];
            let label = match index {
                Some(_) => format!("[{}] Project Python runtime", runtime.configured),
                None => "Project Python runtime".into(),
            };
            if original
                .iter()
                .any(|item| item["target_name"] == label.as_str() && item["status"] == "FAIL")
            {
                problems.push(format!(
                    "does not satisfy requires-python {}",
                    static_extra["requires_python"].as_str().unwrap_or_default()
                ));
            }
            if let Some(reason) = problem(step(&steps, index, ".compile")) {
                problems.push(format!("compileall: {reason}"));
            }
            if !imports.is_empty()
                && let Some(reason) = problem(step(&steps, index, ".imports"))
            {
                problems.push(format!("import smoke: {reason}"));
            }
            if problems.is_empty() {
                runtime_count += 1;
                let completed = if imports.is_empty() {
                    "version and compileall"
                } else {
                    "version, compileall, and import smoke"
                };
                targets.push(target(
                    ".",
                    json!(1),
                    Value::Null,
                    "PythonRuntime:Verified",
                    "PASS",
                    &format!(
                        "{} ({version}) passed {completed} checks",
                        runtime.configured
                    ),
                    json!({"required":required,"imports":imports.len()}),
                ));
            } else {
                targets.push(target(
                    ".",
                    json!(1),
                    Value::Null,
                    "PythonRuntime:Incompatible",
                    advisory,
                    &format!(
                        "{} ({version}): {}",
                        runtime.configured,
                        problems.join("; ")
                    ),
                    json!({"required":required}),
                ));
                if runtime.required {
                    failures += 1
                } else {
                    warnings += 1
                }
            }
        }
    }
    let runtime_error = targets
        .iter()
        .find(|item| item["status"] == "ERROR")
        .and_then(|item| item["message"].as_str())
        .map(str::to_owned);
    let (status, evidence, summary) = if let Some(first) = errors.first() {
        (
            "ERROR",
            "NOT_RUN",
            format!("Python compatibility analysis incomplete: {first}"),
        )
    } else if let Some(message) = runtime_error {
        ("ERROR", "NOT_RUN", message)
    } else {
        let mode = settings
            .get("mode")
            .and_then(Value::as_str)
            .unwrap_or("pass_warn_fail");
        let (fail, warn) = (failures > 0, warnings > 0);
        let status = match mode {
            "pass_fail" if fail || warn => "FAIL",
            "pass_warn" if fail || warn => "WARN",
            "pass_fail" | "pass_warn" => "PASS",
            _ if fail => "FAIL",
            _ if warn => "WARN",
            _ => "PASS",
        };
        let summary = if fail || warn {
            format!("Python compatibility: {failures} failure(s), {warnings} warning(s)")
        } else {
            "Python runtime and source compatibility verified".into()
        };
        (status, "MEASURED", summary)
    };
    ici_reporting::legacy_verdict(engine, status, evidence, &summary, targets)?;
    // Frozen `result.findings = package_findings`: packaging rule findings stay beside the
    // adapted target findings, all sorted like frozen `normalize_result_findings`.
    let findings = engine["findings"].as_array_mut().ok_or_else(|| {
        ici_reporting::ReportError("legacy engine findings must be an array".into())
    })?;
    findings.extend(package);
    findings.sort_by(|left, right| {
        let key = |finding: &Value| {
            (
                finding["fingerprint"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned(),
                finding["primary_location"]["path"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned(),
                finding["primary_location"]["start_line"]
                    .as_u64()
                    .unwrap_or(0),
                finding["message"].as_str().unwrap_or_default().to_owned(),
            )
        };
        key(left).cmp(&key(right))
    });
    let wheel = static_extra.get("wheel").cloned().unwrap_or_else(|| {
        json!({"state":"NOT_APPLICABLE",
            "policy":settings.get("wheel_policy").and_then(Value::as_str).unwrap_or("allow-native"),
            "requested":strings(settings.get("wheel_globs")),"checked":0,"findings":[]})
    });
    let extra = &mut engine["extra"];
    extra["requires_python"] = json!(static_extra["requires_python"].as_str().unwrap_or_default());
    extra["target_version"] = json!(static_extra["target_version"].as_str().unwrap_or_default());
    // Frozen counts every selected Python source, including one its parser rejected.
    extra["files_checked"] =
        json!(static_extra["files_checked"].as_u64().unwrap_or(0) + syntax_errors);
    extra["ast_nodes"] = static_extra
        .get("python_ast_nodes")
        .cloned()
        .unwrap_or(json!(0));
    extra["runtime_count"] = json!(runtime_count);
    extra["discovered_imports"] = static_extra
        .get("discovered_imports")
        .cloned()
        .unwrap_or(json!([]));
    extra["configured_imports"] = json!(imports);
    extra["wheel"] = wheel;
    extra["limitations"] = json!(LIMITATIONS);
    Ok(true)
}
