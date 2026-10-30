//! Historical output options over the common native run.
use crate::{
    commands::{CliError, CommandOutput, Result, under},
    legacy::engine,
    registry::{Argument, Invocation},
};
use ici_domain::GateVerdict;
use ici_reporting::{RenderOptions, StoredResult};
use std::{collections::BTreeMap, path::Path};

fn error(value: impl std::fmt::Display) -> CliError {
    CliError(value.to_string())
}
pub fn command(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<CommandOutput> {
    if invocation.flag("fail_on_new") && invocation.value("baseline").is_none() {
        return Err(CliError("--fail-on-new requires --baseline".into()));
    }
    let mut internal = invocation.clone();
    // Native evidence always has its own namespace. Historical exports are presentation artifacts.
    internal.arguments.insert(
        "result".into(),
        Argument::Values(vec![crate::DEFAULT_RESULT.into()]),
    );
    let prepared = crate::planning::prepare(&internal, cwd, environment, false)?;
    let root = prepared.project.root.clone();
    let legacy_schema = prepared.project.schema_family == ici_config::SchemaFamily::Legacy;
    let coverage_required = prepared.project.components.iter().any(|component| {
        component
            .settings
            .pointer("/engines/test/coverage_required")
            .and_then(serde_json::Value::as_bool)
            == Some(true)
    });
    // Frozen complexity summary names `engines.complexity.warn_cc` (legacy default 15).
    let warn_cc = prepared
        .project
        .components
        .iter()
        .find_map(|component| {
            component
                .settings
                .pointer("/engines/complexity/warn_cc")
                .filter(|limit| limit.is_number())
                .map(serde_json::Value::to_string)
        })
        .unwrap_or_else(|| "15".into());
    let result_path = root.join(crate::DEFAULT_RESULT);
    let report_path = invocation.flag("report").then(|| {
        root.join(engine(&invocation.command()).map_or_else(
            || "verify_report.json".into(),
            |family| format!("{family}_report.json"),
        ))
    });
    let page_path = invocation
        .value("html")
        .map(|path| under(&root, path))
        .or_else(|| {
            (invocation.flag("open_browser") || invocation.flag("publish"))
                .then(|| root.join("verify_report.html"))
        });
    if let Some(path) = &page_path {
        internal.arguments.insert(
            "html".into(),
            Argument::Values(vec![path.to_string_lossy().into_owned()]),
        );
    }
    let sarif_path = invocation.value("sarif").map(|path| under(&root, path));
    let baseline_path = invocation
        .value("baseline")
        .map(|path| contained(&root, path))
        .transpose()?;
    let baseline_output = invocation
        .value("write_baseline")
        .map(|path| contained(&root, path))
        .transpose()?;
    let summary_path = if invocation.flag("github_summary") {
        environment
            .get("GITHUB_STEP_SUMMARY")
            .filter(|path| !path.is_empty())
            .map(|path| under(&root, path))
    } else {
        None
    };
    let outputs: Vec<_> = [
        report_path
            .as_ref()
            .map(|path| (path, crate::report::ArtifactKind::Result)),
        page_path
            .as_ref()
            .map(|path| (path, crate::report::ArtifactKind::Html)),
        sarif_path
            .as_ref()
            .map(|path| (path, crate::report::ArtifactKind::Sarif)),
        baseline_output
            .as_ref()
            .map(|path| (path, crate::report::ArtifactKind::Result)),
    ]
    .into_iter()
    .flatten()
    .collect();
    let all_paths: Vec<_> = outputs
        .iter()
        .map(|(path, _)| *path)
        .chain(summary_path.as_ref())
        .collect();
    for (index, path) in all_paths.iter().enumerate() {
        if crate::report::same_file(path, &result_path)
            || baseline_path
                .as_ref()
                .is_some_and(|baseline| crate::report::same_file(path, baseline))
            || all_paths[..index]
                .iter()
                .any(|other| crate::report::same_file(path, other))
            || prepared
                .project
                .configuration_files
                .iter()
                .any(|input| crate::report::same_file(path, input))
            || prepared
                .project
                .components
                .iter()
                .flat_map(|component| &component.files)
                .any(|input| crate::report::same_file(path, &root.join(input)))
        {
            return Err(CliError(format!(
                "{} aliases an input or another output",
                path.display()
            )));
        }
    }
    for (path, kind) in &outputs {
        crate::report::artifact_mode(path, &result_path, *kind)?;
        crate::execute::validate_destination(path, false)?;
    }
    if let Some(path) = &summary_path {
        crate::execute::validate_destination(path, false)?;
        if path.exists() {
            crate::report::read_bounded(path, 8 * 1024 * 1024)?;
        }
    }
    let cancellation = ici_execution::Cancellation::default();
    let _signals = crate::events::SignalGuard::install(&cancellation).map_err(error)?;
    let run = crate::execute::execute(&internal, cwd, environment, false, &cancellation)?;
    // Validate comparison before any requested export, especially --write-baseline.
    let mut exit_code = run.result.exit_code();
    // A --fail-on-new failure (regression or incompatible baseline) is a gate decision; later
    // legacy verdict projections never lower it.
    let mut regressed = false;
    // An incompatible baseline under --fail-on-new fails like the frozen gate (exit 1, never a
    // silent pass) with the exact command that regenerates it; nothing is reported as new.
    let mut incompatible_baseline = None;
    if invocation.flag("fail_on_new") {
        match &run.result.baseline {
            Some(delta) if delta.state == ici_domain::BaselineState::Comparable => {
                if crate::coverage_regression::incomplete(&run.outputs) {
                    return Err(CliError(format!(
                        "coverage regression comparison is incomplete; result saved at {}",
                        run.result_path.display()
                    )));
                }
                // Gated new findings and gated severity regressions (frozen FindingDelta.gated).
                if !run.baseline_gate_failures.is_empty()
                    && run.result.gate.selected != GateVerdict::Incomplete
                {
                    regressed = true;
                    exit_code = 1;
                }
            }
            Some(delta) => {
                incompatible_baseline = Some(delta.reason.clone());
                regressed = true;
                exit_code = exit_code.max(1);
            }
            None => {
                incompatible_baseline = Some("no baseline comparison was recorded".into());
                regressed = true;
                exit_code = exit_code.max(1);
            }
        }
    }
    let mut stored = crate::report::load(&run.result_path)?;
    if let Some(family) = engine(&invocation.command())
        && let StoredResult::Legacy(legacy) = &stored
    {
        let mut value = legacy
            .engines()
            .into_iter()
            .find(|result| result["engine_name"] == family)
            .cloned()
            .ok_or_else(|| CliError(format!("{family}: no applicable language/check selected")))?;
        // Frozen measured verdicts that replace the native one (no test file, refused coverage
        // policy) carry the frozen exit code; every other legacy view only adds its own class.
        let mut replaced = false;
        if family == "test"
            && legacy_schema
            && let Some(languages) =
                no_tests_collected(&root, &value).or_else(|| empty_pytest_suite(&value))
        {
            // Frozen `_mark_zero_tests`: no test file at all is a FAIL verdict, not missing evidence.
            ici_reporting::legacy_no_tests_collected(&mut value, &languages).map_err(error)?;
            replaced = true;
        } else if legacy_schema
            && let Some((status, evidence, summary, target)) =
                no_python_sources(family, &value, &warn_cc)
        {
            ici_reporting::legacy_no_sources(&mut value, status, evidence, &summary, target)
                .map_err(error)?;
            replaced = true;
        } else if family == "type" && legacy_schema && mypy_unavailable(&value) {
            // Frozen `_check_python_types` without mypy: the AST annotation pass stands in, with
            // a tool warning (ESTIMATED) or, under `mypy_required`, a tool error (NOT_RUN).
            let setting = |key: &str| {
                prepared.project.components.iter().find_map(|component| {
                    component
                        .settings
                        .pointer(&format!("/engines/type/{key}"))
                        .cloned()
                })
            };
            if setting("mypy_required").and_then(|value| value.as_bool()) == Some(true) {
                value["status"] = serde_json::json!("ERROR");
                value["evidence"] = serde_json::json!("NOT_RUN");
                value["summary"] = serde_json::json!("Mypy is required but was not found");
            } else {
                let mode = setting("mode")
                    .and_then(|value| value.as_str().map(str::to_owned))
                    .unwrap_or_else(|| "pass_warn".into());
                let targets = value["targets"].as_array().cloned().unwrap_or_default();
                let count = |status: &str| {
                    targets
                        .iter()
                        .filter(|target| target["status"] == status)
                        .count()
                };
                let (fail, warn) = (count("FAIL"), count("WARN") + 1);
                let status = match mode.as_str() {
                    "pass_warn" => "WARN",
                    "pass_fail" => "FAIL",
                    _ if fail > 0 => "FAIL",
                    _ => "WARN",
                };
                value["status"] = serde_json::json!(status);
                value["evidence"] = serde_json::json!("ESTIMATED");
                value["summary"] = serde_json::json!(format!(
                    "{fail} Type Findings, {warn} Warnings; Mypy is unavailable; AST fallback is ESTIMATED"
                ));
                replaced = true;
            }
        } else if family == "test"
            && legacy_schema
            && let Some(reason) = refused_policy(&value)
        {
            ici_reporting::legacy_policy_refused(&mut value, &reason).map_err(error)?;
            replaced = true;
        } else if family == "test"
            && legacy_schema
            && !coverage_required
            && let Some(reason) = optional_coverage_missing(&value)
        {
            // Frozen optional coverage: a missing coverage module is a WARN beside the measured
            // test verdict (FAIL still wins). Native never invents the frozen estimate.
            let (status, code) = optional_coverage_verdict(&value["targets"], regressed);
            value["status"] = serde_json::json!(status);
            let passed = value["extra"]["passed_tests"].as_u64().unwrap_or(0);
            let total = value["extra"]["total_tests"].as_u64().unwrap_or(0);
            value["summary"] = serde_json::json!(format!(
                "{passed}/{total} Tests Passed; coverage not measured (optional): {reason}"
            ));
            exit_code = exit_code.max(code);
        }
        let status = value["status"].as_str().unwrap_or_default();
        exit_code = if replaced {
            // Both replacements are FAIL/ERROR (exit 1); a regression already means exit 1.
            ici_reporting::legacy_exit_code(status).max(i32::from(regressed))
        } else {
            // A legacy class never lowers the run's own exit (an incomplete run keeps 3).
            exit_code.max(ici_reporting::legacy_exit_code(status))
        };
        value["native_run"] = run.result.to_value().map_err(error)?;
        stored = StoredResult::from_value(value).map_err(error)?;
    }
    let options = RenderOptions {
        verbose: invocation.flag("verbose"),
        max_findings: invocation
            .value("max_findings")
            .unwrap_or("5")
            .parse()
            .map_err(error)?,
        group_by: serde_json::from_value(serde_json::json!(
            invocation.value("group_by").unwrap_or("engine")
        ))
        .map_err(error)?,
        source_root: Some(root.to_string_lossy().into_owned()),
        ..Default::default()
    };
    // Frozen single-engine commands print only the engine line (plus the line chart and the
    // report notice); the saved result stays complete. `verify` keeps the full console.
    let single = match (engine(&invocation.command()), &stored) {
        (Some(family), StoredResult::Legacy(legacy)) => Some((family, legacy.as_value().clone())),
        _ => None,
    };
    let mut stdout = match &single {
        Some(_) => String::new(),
        None => ici_reporting::render_console(&stored, &options).map_err(error)?,
    };
    if let Some(path) = &report_path {
        write_result_export(path, &stored, &run.result_path)?;
    }
    if let Some((family, engine)) = &single {
        let report = report_path
            .as_ref()
            .map(|_| format!("{family}_report.json"));
        stdout.push_str(&ici_reporting::legacy_engine_console(
            engine,
            report.as_deref(),
        ));
    } else if let Some(path) = &report_path {
        stdout.push_str(&format!("wrote {}\n", path.display()));
    }
    if let Some(path) = sarif_path {
        crate::report::write_artifact(
            &path,
            ici_reporting::render_sarif(&stored, &options)
                .map_err(error)?
                .as_bytes(),
            &run.result_path,
            crate::report::ArtifactKind::Sarif,
        )?;
        stdout.push_str(&format!("wrote {}\n", path.display()));
    }
    if let Some(path) = &page_path {
        crate::report::write_artifact(
            path,
            ici_reporting::render_html(&stored, &options)
                .map_err(error)?
                .as_bytes(),
            &run.result_path,
            crate::report::ArtifactKind::Html,
        )?;
        stdout.push_str(&format!("wrote {}\n", path.display()));
    }
    if let Some(reason) = &incompatible_baseline {
        // Validated before any requested export: the requested replacement is not written.
        stdout.push_str(&format!(
            "Baseline gate: FAILED — --fail-on-new cannot compare against {}: {reason}\n",
            invocation.value("baseline").unwrap_or_default()
        ));
    } else if let Some(path) = baseline_output {
        write_result_export(&path, &stored, &run.result_path)?;
        stdout.push_str(&format!("wrote baseline {}\n", path.display()));
    }
    if invocation.flag("github_summary") {
        let markdown = ici_reporting::render_markdown(&stored, &options).map_err(error)?;
        if let Some(path) = summary_path {
            let mut bytes = if path.exists() {
                crate::report::read_bounded(&path, 8 * 1024 * 1024)?
            } else {
                vec![]
            };
            bytes.extend_from_slice(markdown.as_bytes());
            if bytes.len() > 8 * 1024 * 1024 {
                return Err(CliError("GitHub summary exceeds 8 MiB".into()));
            }
            ici_execution::storage::atomic_write(
                &path,
                &bytes,
                if path.exists() {
                    ici_execution::storage::WriteMode::ReplaceRegular
                } else {
                    ici_execution::storage::WriteMode::CreateNew
                },
            )
            .map_err(error)?;
        } else {
            stdout.push_str(&markdown);
        }
        for annotation in ici_reporting::github_annotations(&stored, &options).map_err(error)? {
            stdout.push_str(&annotation);
            stdout.push('\n');
        }
    }
    let mut stderr = String::new();
    if invocation.flag("open_browser")
        && let Some(path) = &page_path
        && let Err(reason) = open_page(path, &root, environment)
    {
        stderr.push_str(&format!("browser: {reason}\n"));
        if exit_code == 0 {
            exit_code = 2;
        }
    }
    if invocation.flag("publish")
        && let Some(path) = &page_path
    {
        let publish = crate::registry::parse([
            "ici",
            "publish",
            "--json",
            run.result_path
                .to_str()
                .ok_or_else(|| CliError("result path is not UTF-8".into()))?,
            "--html",
            path.to_str()
                .ok_or_else(|| CliError("page path is not UTF-8".into()))?,
        ])
        .map_err(error)?;
        match crate::publish::command(&publish, &root, environment) {
            Ok(published) => {
                stdout.push_str(&published.stdout);
                stderr.push_str(&published.stderr);
                if exit_code == 0 {
                    exit_code = published.exit_code;
                }
            }
            Err(reason) => {
                stderr.push_str(&format!("publish: {reason}\n"));
                if exit_code == 0 {
                    exit_code = 2;
                }
            }
        }
    }
    if single.is_none() {
        stdout.push_str(&format!("Result: {}\n", run.result_path.display()));
    }
    Ok(CommandOutput {
        stdout,
        stderr,
        exit_code,
    })
}
fn open_page(path: &Path, root: &Path, environment: &BTreeMap<String, String>) -> Result<()> {
    let tool = ici_providers::resolver::ToolRequest {
        id: "xdg-open".into(),
        explicit: None,
        project_root: root.into(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .map_err(error)?;
    let mut task = ici_domain::TaskSpec::command(
        "report.open",
        "ici.browser",
        vec![
            tool.path.to_string_lossy().into_owned(),
            path.to_string_lossy().into_owned(),
        ],
        root.to_string_lossy(),
    );
    task.tool_digest = Some(tool.digest);
    for name in [
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "XDG_CURRENT_DESKTOP",
        "BROWSER",
    ] {
        if let Some(value) = environment.get(name) {
            task.env_overlay.insert(name.into(), value.clone());
        }
    }
    let digest = ici_execution::process::executable_digest(&tool.path).map_err(error)?;
    if task.tool_digest.as_ref() != Some(&digest) {
        return Err(CliError("browser opener changed after resolution".into()));
    }
    // Opening a viewer is an explicit user action, not a verification task. Its window must
    // outlive the CLI, so it must not enter the analysis runner's descendant cleanup group.
    let mut child = std::process::Command::new(&tool.path)
        .arg(path)
        .current_dir(root)
        .env_clear()
        .envs(ici_execution::Runner::with_environment(environment.clone()).environment(&task))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|error| CliError(format!("cannot launch the browser opener: {error}")))?;
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}
pub fn contained(root: &Path, value: &str) -> Result<std::path::PathBuf> {
    let path = under(root, value);
    if !path.starts_with(root)
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        return Err(CliError(
            "baseline path must be contained inside the workspace".into(),
        ));
    }
    Ok(path)
}
pub fn write_result_export(path: &Path, stored: &StoredResult, source: &Path) -> Result<()> {
    if crate::report::same_file(path, source) {
        return Err(CliError(
            "legacy export would replace native result evidence".into(),
        ));
    }
    let mode = if path.try_exists().map_err(error)? {
        let _existing =
            StoredResult::from_bytes(&crate::report::read_bounded(path, 64 * 1024 * 1024)?)
                .map_err(|_| {
                    CliError(format!(
                        "{} is not an existing result artifact; choose a new output",
                        path.display()
                    ))
                })?;
        ici_execution::storage::WriteMode::ReplaceRegular
    } else {
        ici_execution::storage::WriteMode::CreateNew
    };
    ici_execution::storage::atomic_write(
        path,
        (ici_reporting::render_json(stored).map_err(error)? + "\n").as_bytes(),
        mode,
    )
    .map_err(error)
}

/// The frozen legacy test engine attempts a suite only for `tests/**/test_*.py` or
/// `tests/**/*.cpp`. When no such file exists and every native test/coverage check was blocked
/// before running, return the languages the frozen engine would report as having no tests.
fn no_tests_collected(root: &Path, engine: &serde_json::Value) -> Option<Vec<&'static str>> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    // Only "no runnable suite exists" blocks qualify; any other block (interpreter, config,
    // tool) stays incomplete evidence.
    let no_suite = |check: &serde_json::Value| {
        check["summary"].as_str().is_some_and(|summary| {
            summary.starts_with("no sources in scope")
                || summary == "C++ runtime checks require a declared component build"
        })
    };
    if checks.is_empty()
        || checks
            .iter()
            .any(|check| check["state"] != "BLOCKED" || !no_suite(check))
    {
        return None;
    }
    let mut pending = vec![root.join("tests")];
    let mut visited = 0usize;
    while let Some(directory) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            visited += 1;
            if visited > 100_000 {
                return None;
            }
            let Ok(metadata) = std::fs::symlink_metadata(entry.path()) else {
                return None;
            };
            let name = entry.file_name().to_string_lossy().into_owned();
            if metadata.is_dir() {
                pending.push(entry.path());
            } else if (name.starts_with("test_") && name.ends_with(".py")) || name.ends_with(".cpp")
            {
                return None;
            }
        }
    }
    let mut languages = vec![];
    for (prefix, label) in [("python.", "Python"), ("cpp.", "C++")] {
        if checks.iter().any(|check| {
            check["check_id"]
                .as_str()
                .is_some_and(|id| id.starts_with(prefix))
                && !check["summary"]
                    .as_str()
                    .is_some_and(|summary| summary.starts_with("no sources in scope"))
        }) {
            languages.push(label);
        }
    }
    Some(languages)
}

/// The historical mypy could not be resolved: the type check was refused at planning.
fn mypy_unavailable(engine: &serde_json::Value) -> bool {
    engine["extra"]["native_checks"]
        .as_array()
        .is_some_and(|checks| {
            checks.iter().any(|check| {
                check["check_id"] == "python.type"
                    && check["state"] == "BLOCKED"
                    && check["summary"]
                        .as_str()
                        .is_some_and(|summary| summary.starts_with("mypy: "))
            })
        })
}

type NoSources = (
    &'static str,
    &'static str,
    String,
    Option<(&'static str, &'static str)>,
);
/// Frozen single-engine verdicts when the Python project selected no Python source: every
/// native check of the engine was refused at planning ("no sources in scope for python").
fn no_python_sources(family: &str, engine: &serde_json::Value, warn_cc: &str) -> Option<NoSources> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    if checks.is_empty()
        || !checks.iter().all(|check| {
            check["state"] == "BLOCKED"
                && check["check_id"]
                    .as_str()
                    .is_some_and(|id| id.starts_with("python."))
                && check["summary"] == "no sources in scope for python"
        })
    {
        return None;
    }
    let none = "No applicable Python source files were selected";
    Some(match family {
        "security" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Security analysis skipped: no Python source files".into(),
            Some(("Security:NotApplicable", none)),
        ),
        "resource" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Resource analysis skipped: no Python source files".into(),
            Some(("Resource:NotApplicable", none)),
        ),
        "lint" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Lint analysis skipped: no applicable source files".into(),
            Some((
                "LintScope",
                "No applicable source files were selected; lint analysis was not run",
            )),
        ),
        "exception" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Exception safety analysis skipped: no applicable source files".into(),
            Some((
                "ExceptionSafety",
                "No applicable Python or C++ source files were selected; analysis was not run",
            )),
        ),
        "dead" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Dead-code analysis skipped: no owned source files".into(),
            Some((
                "DeadCode",
                "No applicable source files were selected; dead-code analysis was not run",
            )),
        ),
        "dup" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Duplicate analysis skipped: no owned source files".into(),
            Some(("DuplicateCode", "No applicable source files were selected")),
        ),
        "cognitive" => (
            "PASS",
            "MEASURED",
            "Max cognitive complexity: 0 across 0 functions".into(),
            None,
        ),
        "cycle" => (
            "PASS",
            "MEASURED",
            "No cyclic dependencies detected".into(),
            None,
        ),
        // Frozen `ComplexityEngine` over no function: a measured PASS naming the warning limit.
        "complexity" => (
            "PASS",
            "MEASURED",
            format!("Max Cyclomatic Complexity: 0 (limit {warn_cc}) across 0 functions (0 issues)"),
            None,
        ),
        // Frozen `_mark_python_type_check_skipped`: Mypy was not run, nothing is type-checkable.
        "type" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Type checking not applicable: no Python sources; C++ is unsupported".into(),
            Some((
                "Mypy",
                "No applicable Python source files were selected; Mypy was not run",
            )),
        ),
        _ => return None,
    })
}

/// Frozen `_parse_pytest_result`: pytest exit 5 (or exit 0 without a case) is the same
/// "No tests collected" FAIL in the legacy view. The modern check stays incomplete.
fn empty_pytest_suite(engine: &serde_json::Value) -> Option<Vec<&'static str>> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let empty = |check: &serde_json::Value| {
        check["check_id"] == "python.test"
            && check["state"] == "FAILED"
            && check["summary"].as_str().is_some_and(|summary| {
                summary == "pytest did not report a completed nonempty suite (exit Some(5))"
                    || summary == "pytest did not report a completed nonempty suite (exit Some(0))"
            })
    };
    let dependent = |check: &serde_json::Value| {
        check["check_id"] == "python.coverage"
            && check["state"] == "BLOCKED"
            && check["summary"]
                .as_str()
                .is_some_and(|summary| summary.ends_with("has no complete evidence"))
    };
    let located = engine["targets"].as_array()?.iter().any(|target| {
        target["target_name"] == "[Python] Tests" && target["message"] == "No tests collected"
    });
    (located
        && checks.iter().any(empty)
        && checks.iter().all(|check| empty(check) || dependent(check)))
    .then(|| vec!["Python"])
}

/// The legacy coverage half failed only because the project interpreter has no coverage module,
/// while the test half completed.
fn optional_coverage_missing(engine: &serde_json::Value) -> Option<String> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let test = checks
        .iter()
        .find(|check| check["check_id"] == "python.test")?;
    let coverage = checks
        .iter()
        .find(|check| check["check_id"] == "python.coverage")?;
    let reason = coverage["summary"].as_str()?;
    (checks.len() == 2
        && test["state"] == "SUCCEEDED"
        && coverage["state"] == "FAILED"
        && (reason.starts_with("coverage module is unavailable")
            || reason.starts_with("pytest module is unavailable; fallback unittest")))
    .then(|| reason.to_owned())
}

/// A changed-line selection the native planner refused (frozen evaluates it at run time and
/// reports an ERROR policy target).
fn refused_policy(engine: &serde_json::Value) -> Option<String> {
    engine["extra"]["native_checks"]
        .as_array()?
        .iter()
        .filter(|check| check["state"] == "BLOCKED")
        .filter_map(|check| check["summary"].as_str())
        .find(|summary| summary.starts_with("changed line"))
        .map(str::to_owned)
}

/// Frozen optional-coverage verdict: a measured FAIL/ERROR target wins over the coverage WARN,
/// and a `--fail-on-new` regression keeps exit 1 even when every target passed.
fn optional_coverage_verdict(targets: &serde_json::Value, regressed: bool) -> (&'static str, i32) {
    let statuses = targets
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter_map(|target| match target["status"].as_str() {
            Some("ERROR") => Some("ERROR"),
            Some("FAIL") => Some("FAIL"),
            _ => None,
        });
    let status = ici_reporting::legacy_worst_status(statuses, "WARN");
    (
        status,
        ici_reporting::legacy_exit_code(status).max(i32::from(regressed)),
    )
}

#[cfg(test)]
mod tests {
    use super::optional_coverage_verdict;
    use serde_json::json;

    #[test]
    fn optional_coverage_verdict_never_lowers_failures_or_regressions() {
        let targets = |statuses: &[&str]| {
            json!(
                statuses
                    .iter()
                    .map(|status| json!({"status": status}))
                    .collect::<Vec<_>>()
            )
        };
        assert_eq!(
            optional_coverage_verdict(&targets(&["PASS"]), false),
            ("WARN", 0)
        );
        assert_eq!(
            optional_coverage_verdict(&targets(&["PASS"]), true),
            ("WARN", 1)
        );
        assert_eq!(
            optional_coverage_verdict(&targets(&["PASS", "FAIL"]), false),
            ("FAIL", 1)
        );
        assert_eq!(
            optional_coverage_verdict(&targets(&["FAIL", "ERROR"]), false),
            ("ERROR", 1)
        );
        assert_eq!(optional_coverage_verdict(&json!(null), true), ("WARN", 1));
    }
}
