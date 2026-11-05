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
    if crate::cutover::applies(invocation, cwd) {
        return crate::cutover::verify(invocation, cwd, environment);
    }
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
    if invocation.command() == "verify"
        && let Some(reason) = crate::legacy::pipeline_refusal(&prepared)
    {
        return Ok(CommandOutput {
            stdout: String::new(),
            stderr: format!("{reason}\n"),
            exit_code: 1,
        });
    }
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
    // A legacy-schema `verify` against a baseline a native `--write-baseline` saved compares in
    // frozen legacy space (see `frozen_baseline`), and that comparison decides the gate.
    let frozen_space = legacy_schema
        && engine(&invocation.command()).is_none()
        && run.legacy_baseline_suite.is_some();
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
                if !frozen_space
                    && !run.baseline_gate_failures.is_empty()
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
    let mut stored = crate::report::load(&root, &run.result_path)?;
    let quality_profile = if engine(&invocation.command()).is_none() {
        crate::legacy_support::profile_name(prepared.project.profile)
    } else {
        String::new()
    };
    let shaping = Shaping {
        prepared: &prepared,
        root: &root,
        legacy_schema,
        coverage_required,
        warn_cc: &warn_cc,
        regressed,
        quality_profile: &quality_profile,
    };
    // `verify` shapes every engine of the suite with the same helper as the family commands, so a
    // missing Ruff/mypy reads the same in both (frozen verify runs the same engine bodies).
    if engine(&invocation.command()).is_none()
        && legacy_schema
        && let StoredResult::Legacy(legacy) = &stored
    {
        let mut document = legacy.as_value().clone();
        let mut replaced_any = false;
        let mut floor = 0;
        if let Some(results) = document["results"].as_array_mut() {
            for value in results {
                let family = value["engine_name"].as_str().unwrap_or_default().to_owned();
                let (replaced, engine_floor) = shape_engine(&family, value, &shaping)?;
                replaced_any |= replaced;
                floor = floor.max(engine_floor);
            }
        }
        if replaced_any {
            // The native run was incomplete only through checks these engines replaced when the
            // recomputed suite has no remaining required NOT_RUN engine: an ERROR suite from a
            // frozen verdict class (e.g. a required SKIP engine) then exits like frozen (1), and
            // only evidence still missing keeps the incomplete exit 3.
            let status = ici_reporting::legacy_refresh_suite(&mut document).map_err(error)?;
            let missing = document["results"].as_array().is_some_and(|engines| {
                engines
                    .iter()
                    .any(|engine| engine["required"] == true && engine["evidence"] == "NOT_RUN")
            });
            exit_code = if status == "ERROR" && missing {
                exit_code.max(1)
            } else {
                ici_reporting::legacy_exit_code(&status)
                    .max(floor)
                    .max(i32::from(regressed))
            };
        }
        // The shaped engines are the report even when no verdict was replaced (frozen engine
        // `extra` fields such as the C++ lint counters are shaping too).
        stored = StoredResult::from_value(document).map_err(error)?;
    }
    // Frozen `_write_baseline`: the written baseline has no comparison and the suite status before
    // the gate.
    let mut pre_gate_status = None;
    if frozen_space && let StoredResult::Legacy(legacy) = &stored {
        let mut document = legacy.as_value().clone();
        pre_gate_status = Some(document["suite_status"].clone());
        if frozen_baseline(&mut document, &run)? {
            exit_code = exit_code.max(1);
        }
        stored = StoredResult::from_value(document).map_err(error)?;
    }
    // Frozen `ici verify` exits 1 for a FAIL/ERROR suite. A legacy verdict the native gate keeps
    // advisory (e.g. a coverage minimum the frozen TestEngine turns into FAIL) never exits 0;
    // a higher native exit (incomplete 3) is kept. Only the legacy schema has that frozen suite
    // exit: frozen v0.13.0 rejects schema_version=2 and next [engines] keys (exit 2), so a
    // modern-schema verify keeps the native gate's exit.
    if legacy_schema
        && engine(&invocation.command()).is_none()
        && let StoredResult::Legacy(legacy) = &stored
        && matches!(
            legacy.as_value()["suite_status"].as_str(),
            Some("FAIL" | "ERROR")
        )
    {
        exit_code = exit_code.max(1);
    }
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
        let (replaced, floor) = shape_engine(family, &mut value, &shaping)?;
        exit_code = exit_code.max(floor);
        let status = value["status"].as_str().unwrap_or_default();
        exit_code = if replaced {
            // Both replacements are FAIL/ERROR (exit 1); a regression already means exit 1.
            ici_reporting::legacy_exit_code(status).max(i32::from(regressed))
        } else {
            // A legacy class never lowers the run's own exit (an incomplete run keeps 3).
            exit_code.max(ici_reporting::legacy_exit_code(status))
        };
        // Frozen `_run_engine_command`: this engine's rows under the configured profile.
        let inputs = crate::legacy_support::inputs(
            &prepared,
            &crate::legacy_support::profile_name(prepared.project.profile),
        );
        value["support_matrix"] = ici_reporting::legacy_support_matrix(
            &inputs,
            std::slice::from_ref(&value),
            Some(&[family]),
        );
        value["native_run"] = run.result.to_value().map_err(error)?;
        stored = StoredResult::from_value(value).map_err(error)?;
    } else if let StoredResult::Legacy(legacy) = &stored {
        // Frozen `engines/verify.py`: the suite matrix over the selected profile's results.
        let profile = invocation.value("profile").map_or_else(
            || crate::legacy_support::profile_name(prepared.project.profile),
            str::to_owned,
        );
        let mut document = legacy.as_value().clone();
        crate::legacy_support::attach_suite(
            &mut document,
            &crate::legacy_support::inputs(&prepared, &profile),
        );
        // Frozen `verify` serializes the analysis context it prepared; single engines have none.
        document["analysis_context"] =
            crate::legacy_context::analysis_context(&prepared, &document, &profile);
        // Frozen `VerificationSuiteResult`: the producer/policy identity and the capability
        // snapshot every v3 suite reader (the quality-zoo contract, `legacy_reader`) requires.
        document["analysis_metadata"] =
            crate::legacy_context::analysis_metadata(&prepared, &document, &profile);
        document["capability_inventory"] = crate::doctor::suite_capability_inventory(
            &document["support_matrix"],
            &prepared.project.effective,
            &root,
            environment,
            &cancellation,
        )?;
        crate::cpp::frozen_readelf_version(&mut document);
        stored = StoredResult::from_value(document).map_err(error)?;
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
            // Root decision E1: the historical suite keeps frozen reporters/sarif.py's shape
            // (`ici/v3` fingerprints, %SRCROOT%) so code-scanning alerts keep their identity.
            if legacy_schema && matches!(stored, StoredResult::Legacy(_)) {
                ici_reporting::render_legacy_sarif(&stored)
            } else {
                ici_reporting::render_sarif(&stored, &options)
            }
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
        match (&stored, pre_gate_status) {
            (StoredResult::Legacy(legacy), Some(status)) => {
                let mut document = legacy.as_value().clone();
                document["baseline_comparison"] = serde_json::Value::Null;
                document["suite_status"] = status;
                let written = StoredResult::from_value(document).map_err(error)?;
                write_result_export(&path, &written, &run.result_path)?;
            }
            _ => write_result_export(&path, &stored, &run.result_path)?,
        }
        stdout.push_str(&format!("wrote baseline {}\n", path.display()));
    }
    if invocation.flag("github_summary") {
        // Frozen write_github_step_summary: append the historical suite's bounded Markdown to
        // $GITHUB_STEP_SUMMARY when it is set; without it nothing is written or printed.
        if let Some(path) = summary_path {
            let markdown = match ici_reporting::legacy_summary_markdown(&stored).map_err(error)? {
                Some(markdown) => markdown,
                None => ici_reporting::render_markdown(&stored, &options).map_err(error)?,
            };
            let mut bytes = if path.exists() {
                crate::report::read_bounded(&path, 8 * 1024 * 1024)?
            } else {
                vec![]
            };
            let remaining = ici_reporting::MAX_SUMMARY_BYTES.saturating_sub(bytes.len());
            bytes.extend_from_slice(
                ici_reporting::bounded_summary_payload(&markdown, remaining).as_bytes(),
            );
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
        }
        // Frozen: workflow commands only inside GitHub Actions, built from the suite's targets.
        if environment.get("GITHUB_ACTIONS").map(String::as_str) == Some("true") {
            for annotation in
                ici_reporting::legacy_github_annotations(&stored, &options).map_err(error)?
            {
                stdout.push_str(&annotation);
                stdout.push('\n');
            }
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
    // Frozen engines/verify.py: `--publish` prints the publisher's `[publish]` lines (its own
    // Actions/target guards included) and never changes the verify exit.
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
        match crate::publish::command_named(&publish, &root, environment, &run.project_name) {
            Ok(published) => {
                stdout.push_str(&published.stdout);
                stderr.push_str(&published.stderr);
            }
            Err(reason) => stderr.push_str(&crate::publish::refusal_line(&reason)),
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
#[derive(Clone, Copy)]
struct Shaping<'a> {
    prepared: &'a crate::planning::PreparedPlan,
    root: &'a Path,
    legacy_schema: bool,
    coverage_required: bool,
    warn_cc: &'a str,
    regressed: bool,
    /// Frozen `TestEngine` quality profile: the suite's analysis-context profile under `verify`,
    /// empty for a single engine command (no analysis context).
    quality_profile: &'a str,
}
/// Frozen engine verdicts the native result cannot express by itself (missing Ruff/mypy
/// fallbacks, no sources, no tests, refused or optional coverage). Returns whether the native
/// verdict was replaced and the exit floor the legacy view adds.
fn shape_engine(
    family: &str,
    value: &mut serde_json::Value,
    shaping: &Shaping<'_>,
) -> Result<(bool, i32)> {
    let Shaping {
        prepared,
        root,
        legacy_schema,
        coverage_required,
        warn_cc,
        regressed,
        quality_profile,
    } = *shaping;
    let root = root.to_path_buf();
    let warn_cc = warn_cc.to_owned();
    let mut replaced = false;
    let mut exit_code = 0;
    if family == "test"
        && legacy_schema
        && let Some(languages) = no_tests_collected(&root, value, cpp_in_scope(prepared))
            .or_else(|| empty_pytest_suite(value))
            .or_else(|| hybrid_empty_pytest_suite(&root, value, cpp_in_scope(prepared)))
            .or_else(|| python_without_tests_dir(&root, value))
    {
        // Frozen `_mark_zero_tests`: no test file at all is a FAIL verdict, not missing evidence.
        ici_reporting::legacy_no_tests_collected(value, &languages).map_err(error)?;
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && value["score"].is_null()
        && value["extra"]["native_checks"]
            .as_array()
            .is_some_and(|checks| {
                checks
                    .iter()
                    .any(|check| check["extra"]["legacy_test_configuration_error"] == true)
            })
    {
        // Frozen TestEngine always reports its TEM; a coverage configure refusal ran no test,
        // so the engine carries TEM 0.0 / 5.0 next to its tool error.
        value["score"] = serde_json::json!(0.0);
        value["max_score"] = serde_json::json!(5.0);
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && let Some(total) = python_tests_not_executed(value)
    {
        // Frozen `TestEngine.run` `no_tests_executed`: every collected case skipped is ERROR
        // (NOT_RUN) for a required engine and SKIP (ESTIMATED) otherwise, never a pass.
        let required = value["required"].as_bool().unwrap_or(true);
        value["status"] = serde_json::json!(if required { "ERROR" } else { "SKIP" });
        value["evidence"] = serde_json::json!(if required { "NOT_RUN" } else { "ESTIMATED" });
        value["summary"] = serde_json::json!(format!(
            "0/{total} Tests Executed; every collected test was skipped"
        ));
        replaced = true;
    } else if family == "build" && legacy_schema && python_sources_unselected(value) {
        // Frozen `BuildEngine.run`: a Python project without a selected source creates no
        // library, launcher or binary, which is the measured `BuildArtifacts` FAIL (mode-aware).
        let mode = prepared.project.components.iter().find_map(|component| {
            component
                .settings
                .pointer("/engines/build/mode")
                .and_then(serde_json::Value::as_str)
        });
        let status = if mode == Some("pass_warn") {
            "WARN"
        } else {
            "FAIL"
        };
        let target = serde_json::json!({"file_path":".","start_line":1,"end_line":null,
            "start_column":null,"end_column":null,"target_name":"BuildArtifacts",
            "status":"FAIL","message":"No Python library, launcher, or C++ binary artifact was created",
            "snippet":"","metrics":{}});
        ici_reporting::legacy_verdict(
            value,
            status,
            "MEASURED",
            "Build did not produce a complete artifact set",
            vec![target],
        )
        .map_err(error)?;
        replaced = true;
    } else if legacy_schema
        && let Some((status, evidence, summary, target)) =
            no_python_sources(family, value, &warn_cc)
    {
        ici_reporting::legacy_no_sources(value, status, evidence, &summary, target)
            .map_err(error)?;
        replaced = true;
    } else if family == "python_compat" && legacy_schema {
        let settings = prepared
            .project
            .components
            .iter()
            .find_map(|component| {
                component
                    .settings
                    .pointer("/engines/python_compat")
                    .cloned()
            })
            .unwrap_or_default();
        let compile_targets = crate::legacy_python_compat::compile_target_count(&prepared.project);
        replaced =
            crate::legacy_python_compat::shape(value, &settings, compile_targets).map_err(error)?;
    } else if family == "lint" && legacy_schema && ruff_unavailable(value) {
        // Frozen `_lint_python` without Ruff: the AST syntax pass stands in (one
        // `ASTSyntaxFallback` inventory target) with a tool warning (ESTIMATED) or, under
        // `ruff_required`, a tool error (NOT_RUN).
        let setting = |key: &str| {
            prepared.project.components.iter().find_map(|component| {
                component
                    .settings
                    .pointer(&format!("/engines/lint/{key}"))
                    .cloned()
            })
        };
        let started = std::time::Instant::now();
        let (targets, fail, _) = python_fallback_targets(prepared, &root)?;
        add_duration(value, started.elapsed());
        // Frozen adapts every target with the engine's first tool evidence (the Ruff not-found
        // record), so it is in place before the verdict adapts the fallback targets.
        value["tool_evidence"] = missing_tool("ruff");
        if setting("ruff_required").and_then(|value| value.as_bool()) == Some(true) {
            ici_reporting::legacy_verdict(
                value,
                "ERROR",
                "NOT_RUN",
                "Ruff is required but was not found",
                targets,
            )
            .map_err(error)?;
        } else {
            let mode = setting("mode")
                .and_then(|value| value.as_str().map(str::to_owned))
                .unwrap_or_else(|| "pass_warn_fail".into());
            let status = match mode.as_str() {
                "pass_warn" => "WARN",
                "pass_fail" => "FAIL",
                _ if fail > 0 => "FAIL",
                _ => "WARN",
            };
            let summary = if fail == 0 {
                "Ruff is unavailable; AST syntax fallback is ESTIMATED".to_owned()
            } else {
                format!("{fail} Errors, 1 Warnings Found")
            };
            ici_reporting::legacy_verdict(value, status, "ESTIMATED", &summary, targets)
                .map_err(error)?;
            replaced = true;
        }
    } else if family == "lint" && legacy_schema && hybrid_ruff_unavailable(value) {
        // Frozen `LintEngine.run` on a hybrid project without Ruff: the Python AST fallback
        // targets come first, then the C++ targets; the tool warnings are Ruff's then the C++
        // ones (compile-database fallback, Qt, clang tooling), the errors Ruff's (under
        // `ruff_required`) then the C++ tool errors.
        let setting = |key: &str| {
            prepared.project.components.iter().find_map(|component| {
                component
                    .settings
                    .pointer(&format!("/engines/lint/{key}"))
                    .cloned()
            })
        };
        let started = std::time::Instant::now();
        let (mut targets, _, parsed) = python_fallback_targets(prepared, &root)?;
        add_duration(value, started.elapsed());
        targets.extend(value["targets"].as_array().cloned().unwrap_or_default());
        let checks = value["extra"]["native_checks"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let strings = |key: &str| -> Vec<String> {
            checks
                .iter()
                .flat_map(|check| check["extra"][key].as_array().cloned().unwrap_or_default())
                .filter_map(|item| item.as_str().map(str::to_owned))
                .collect()
        };
        let mut errors = vec![];
        let mut warnings = vec![];
        if setting("ruff_required").and_then(|value| value.as_bool()) == Some(true) {
            errors.push("Ruff is required but was not found".to_owned());
        } else {
            warnings.push("Ruff is unavailable; AST syntax fallback is ESTIMATED".to_owned());
        }
        if checks.iter().any(|check| {
            check["check_id"] == "cpp.diagnostics"
                && check["extra"]["cpp_analysis_mode"] == "heuristic"
        }) {
            warnings.push("No compilation database is available; C++ syntax analysis used the c++17 heuristic fallback".to_owned());
        }
        warnings.extend(strings("legacy_qt_warnings"));
        warnings.extend(strings("legacy_tool_warnings"));
        errors.extend(strings("legacy_tool_errors"));
        let count = |status: &str| {
            targets
                .iter()
                .filter(|target| target["status"] == status)
                .count()
        };
        let fail = count("FAIL");
        let messages: Vec<&str> = targets
            .iter()
            .filter(|target| target["status"] == "WARN")
            .filter_map(|target| target["message"].as_str())
            .collect();
        let warn = count("WARN")
            + warnings
                .iter()
                .filter(|warning| !messages.contains(&warning.as_str()))
                .count();
        let mode = setting("mode")
            .and_then(|value| value.as_str().map(str::to_owned))
            .unwrap_or_else(|| "pass_warn_fail".into());
        let status = if !errors.is_empty() {
            "ERROR"
        } else {
            match (mode.as_str(), fail > 0, warn > 0) {
                ("pass_fail", true, _) | ("pass_fail", _, true) => "FAIL",
                ("pass_warn", true, _) | ("pass_warn", _, true) => "WARN",
                (_, true, _) => "FAIL",
                (_, _, true) => "WARN",
                _ => "PASS",
            }
        };
        let summary = if !errors.is_empty() {
            errors
                .iter()
                .take(3)
                .cloned()
                .collect::<Vec<_>>()
                .join("; ")
        } else if status == "PASS" {
            "0 Violations Found".to_owned()
        } else if !warnings.is_empty() && fail == 0 {
            warnings
                .iter()
                .take(2)
                .cloned()
                .collect::<Vec<_>>()
                .join("; ")
        } else {
            format!("{fail} Errors, {warn} Warnings Found")
        };
        let evidence = if !errors.is_empty() {
            "NOT_RUN"
        } else if !warnings.is_empty() {
            "ESTIMATED"
        } else {
            "MEASURED"
        };
        // Frozen `_cpp_findings`: the C++ diagnostics stay their own findings (explanation,
        // category, tool rule); only the other targets are adapted by the new verdict.
        let explicit: Vec<serde_json::Value> = value["findings"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|finding| {
                finding["explanation"] != "Adapted from the legacy lint InspectionTarget contract."
            })
            .cloned()
            .collect();
        ici_reporting::legacy_verdict(value, status, evidence, &summary, targets).map_err(error)?;
        for finding in value["findings"].as_array_mut().into_iter().flatten() {
            // The adapted finding is labelled with its target name (`C++Syntax`); the frozen
            // compiler finding keeps the diagnostic's own (empty) label.
            let same = |item: &&serde_json::Value| {
                item["message"] == finding["message"]
                    && ["path", "start_line", "start_column"].iter().all(|key| {
                        item["primary_location"][key] == finding["primary_location"][key]
                    })
            };
            if let Some(original) = explicit.iter().find(same) {
                *finding = original.clone();
            }
        }
        value["extra"]["python_files_parsed"] = serde_json::json!(parsed);
        let mut evidence = missing_tool("ruff");
        if let (Some(list), Some(cpp)) =
            (evidence.as_array_mut(), value["tool_evidence"].as_array())
        {
            list.extend(cpp.iter().cloned());
        }
        value["tool_evidence"] = evidence;
        // Frozen adapts every target with the engine's first tool evidence (Ruff), the C++
        // tool-unavailable targets included; only its compiler diagnostics were native findings
        // with their compiler attribution (the C++ facet).
        for finding in value["findings"].as_array_mut().into_iter().flatten() {
            if finding["rule_id"] == "ici.legacy.lint.target"
                && finding["explanation"]
                    == "Adapted from the legacy lint InspectionTarget contract."
                && !finding["primary_location"]["label"]
                    .as_str()
                    .is_some_and(|label| label.starts_with("Compiler:"))
            {
                finding["tool_name"] = serde_json::json!("ruff");
                finding["tool_version"] = serde_json::json!("");
            }
        }
        replaced = true;
    } else if family == "type" && legacy_schema && mypy_unavailable(value) {
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
            // The fallback warning, then a hybrid project's C++ skip warning.
            let mode = setting("mode")
                .and_then(|value| value.as_str().map(str::to_owned))
                .unwrap_or_else(|| "pass_warn".into());
            replaced = crate::legacy_extra::type_verdict(value, &mode, true);
        }
        value["tool_evidence"] = missing_tool("mypy");
        // Frozen adapted the targets after its verdict: the first tool evidence (mypy) names
        // them and the engine evidence sets their confidence.
        let confidence = if value["evidence"] == "ESTIMATED" {
            "medium"
        } else {
            "low"
        };
        for finding in value["findings"].as_array_mut().into_iter().flatten() {
            if finding["rule_id"] == "ici.legacy.type.target" {
                finding["confidence"] = serde_json::json!(confidence);
                finding["tool_name"] = serde_json::json!("mypy");
                finding["tool_version"] = serde_json::json!("");
            }
        }
    } else if family == "type"
        && legacy_schema
        && crate::legacy_extra::type_verdict(
            value,
            prepared
                .project
                .components
                .iter()
                .find_map(|component| {
                    component
                        .settings
                        .pointer("/engines/type/mode")
                        .and_then(serde_json::Value::as_str)
                })
                .unwrap_or("pass_warn"),
            false,
        )
    {
        // Frozen `_mark_cpp_type_check_skipped` beside a Python mypy run (hybrid project).
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && let Some(reason) = refused_policy(value)
    {
        ici_reporting::legacy_policy_refused(value, &reason).map_err(error)?;
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && let Some(reason) = legacy_coverage_probe_error(value)
    {
        // Frozen `_find_coverage_cmd`: a failed probe is a tool error; the plain suite still ran
        // (its case targets stay) and `_result_status`/`_result_summary` report the error.
        value["status"] = serde_json::json!("ERROR");
        value["evidence"] = serde_json::json!("NOT_RUN");
        value["summary"] = serde_json::json!(reason);
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && !coverage_required
        && let Some(reason) = optional_coverage_missing(value)
    {
        // Frozen optional coverage: a missing coverage module is a WARN beside the measured
        // test verdict (FAIL still wins). Native never invents the frozen estimate.
        if reason.starts_with("coverage module is unavailable") {
            // Frozen `_find_coverage_cmd` adds no target for the missing module (decisions.md
            // "pytest는 있으나 coverage가 없는 legacy test"); the native result keeps its
            // `Coverage capability` SKIP target.
            crate::legacy_extra::drop_targets(value, |target| {
                target["target_name"] == "Coverage capability" && target["status"] == "SKIP"
            });
        }
        let (status, code) = optional_coverage_verdict(&value["targets"], regressed);
        value["status"] = serde_json::json!(status);
        // The engine aggregate stops at the unmeasured coverage half; frozen `_result_summary`
        // counts the plain suite that ran, which the python.test check measured.
        let (passed, total) = python_test_counts(value).unwrap_or((
            value["extra"]["passed_tests"].as_u64().unwrap_or(0),
            value["extra"]["total_tests"].as_u64().unwrap_or(0),
        ));
        value["summary"] = serde_json::json!(format!(
            "{passed}/{total} Tests Passed; coverage not measured (optional): {reason}"
        ));
        // Frozen `_result_evidence`: an optional coverage warning is ESTIMATED evidence (frozen
        // invents its estimate; native records none), never the required NOT_RUN that would
        // turn the suite into an incomplete ERROR. The measured test verdict stands.
        value["evidence"] = serde_json::json!("ESTIMATED");
        for finding in value["findings"].as_array_mut().into_iter().flatten() {
            if finding["rule_id"] == "ici.legacy.test.target" {
                finding["confidence"] = serde_json::json!("medium");
            }
        }
        exit_code = exit_code.max(code);
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && let Some(reason) = incomplete_cpp_text_coverage(value)
    {
        // Frozen `_consume_cpp_coverage`'s gcov text rows that miss a declared source: the
        // measured rows stay, the coverage error is a tool error under `coverage_required`
        // (`_apply_coverage_policy`) and otherwise the optional ESTIMATED warning
        // (`_result_status`/`_result_summary`/`_result_evidence`).
        if coverage_required {
            value["status"] = serde_json::json!("ERROR");
            value["evidence"] = serde_json::json!("NOT_RUN");
            value["summary"] = serde_json::json!(reason);
            // The adapted target findings follow the engine evidence (`NOT_RUN` -> `low`).
            for finding in value["findings"].as_array_mut().into_iter().flatten() {
                if finding["rule_id"] == "ici.legacy.test.target" {
                    finding["confidence"] = serde_json::json!("low");
                }
            }
        } else {
            let (status, code) = optional_coverage_verdict(&value["targets"], regressed);
            value["status"] = serde_json::json!(status);
            value["evidence"] = serde_json::json!("ESTIMATED");
            for finding in value["findings"].as_array_mut().into_iter().flatten() {
                if finding["rule_id"] == "ici.legacy.test.target" {
                    finding["confidence"] = serde_json::json!("medium");
                }
            }
            let summary = value["summary"].as_str().unwrap_or_default().to_owned();
            value["summary"] = serde_json::json!(format!(
                "{summary}; Coverage evidence ESTIMATED (optional; not threshold evidence)"
            ));
            exit_code = exit_code.max(code);
        }
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && !coverage_required
        && let Some(reason) = optional_cpp_coverage_missing(value)
    {
        // Frozen optional C++ coverage without a production source to measure: the test verdict
        // stands beside an ESTIMATED coverage evidence (frozen invents 85%/95%; native does not),
        // so the suite keeps the frozen WARN class instead of a NOT_RUN ERROR.
        let (status, code) = optional_coverage_verdict(&value["targets"], regressed);
        let cases: Vec<_> = value["targets"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
            .iter()
            .filter(|target| {
                target["target_name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("[C++] ") && name != "[C++] build")
            })
            .collect();
        let passed = cases
            .iter()
            .filter(|target| target["status"] == "PASS")
            .count();
        let total = cases.len();
        value["status"] = serde_json::json!(status);
        value["evidence"] = serde_json::json!("ESTIMATED");
        // The adapted target findings follow the engine evidence (`ESTIMATED` -> `medium`).
        for finding in value["findings"].as_array_mut().into_iter().flatten() {
            if finding["rule_id"] == "ici.legacy.test.target" {
                finding["confidence"] = serde_json::json!("medium");
            }
        }
        value["summary"] = serde_json::json!(format!(
            "{passed}/{total} Tests Passed; coverage not measured (optional): {reason}"
        ));
        exit_code = exit_code.max(code);
        replaced = true;
    }
    if family == "test"
        && legacy_schema
        && coverage_required
        && !replaced
        && let Some(summary) = required_cpp_coverage_missing(value, &root)
    {
        value["status"] = serde_json::json!("ERROR");
        value["evidence"] = serde_json::json!("NOT_RUN");
        value["summary"] = serde_json::json!(summary);
        replaced = true;
    } else if family == "test"
        && legacy_schema
        && coverage_required
        && !replaced
        && let Some(summary) = required_cpp_gcov_failed(value)
    {
        value["summary"] = serde_json::json!(summary);
    }
    if legacy_schema {
        frozen_python_finding_fields(family, value);
        crate::legacy_python_inventory::shape(family, value, &root)?;
        crate::cpp::legacy_engine_extra::shape(family, value);
        if family == "lint" {
            let config = prepared
                .project
                .components
                .iter()
                .find_map(|component| component.settings.pointer("/engines/lint"));
            let python: std::collections::BTreeSet<&String> = prepared
                .project
                .components
                .iter()
                .flat_map(|component| &component.files)
                .filter(|path| path.ends_with(".py"))
                .collect();
            crate::cpp::legacy_lint_extra::shape(value, config, python.len());
        }
        let settings = prepared
            .project
            .components
            .iter()
            .find_map(|component| component.settings.pointer(&format!("/engines/{family}")))
            .cloned()
            .unwrap_or(serde_json::Value::Null);
        let python_sources = prepared
            .project
            .components
            .iter()
            .flat_map(|component| &component.files)
            .filter(|path| path.ends_with(".py"))
            .count();
        // Frozen `project_cpp_sources` (sorted), for the C++ test engine's coverage scope.
        let mut cpp_sources: Vec<String> = prepared
            .project
            .components
            .iter()
            .flat_map(|component| &component.files)
            .filter(|path| {
                std::path::Path::new(path)
                    .extension()
                    .is_some_and(|extension| {
                        ["cpp", "cc", "cxx", "c"].contains(&extension.to_string_lossy().as_ref())
                    })
            })
            .cloned()
            .collect();
        cpp_sources.sort();
        cpp_sources.dedup();
        replaced |= crate::legacy_extra::complete(
            family,
            value,
            &crate::legacy_extra::Context {
                settings: &settings,
                profile: quality_profile,
                python_sources,
                root: &root,
                cpp_sources: &cpp_sources,
            },
        );
    }
    Ok((replaced, exit_code))
}

fn python_source(finding: &serde_json::Value) -> bool {
    finding["primary_location"]["path"]
        .as_str()
        .is_some_and(|path| path.ends_with(".py") || path.ends_with(".pyi"))
}

/// Finding fields frozen engines set themselves rather than through the generic target adapter.
/// `dead`: `DeadEngine._scoped_findings` gives each Python target its own explanation and
/// remediation, no tool, and the Python scope's confidence (medium when ESTIMATED, else low).
/// `sanitize`: `_missing_python_scope` records a `pytest resource warnings` tool evidence even
/// when no process ran, so the adapted targets name that tool (native ran nothing and names no
/// interpreter: path and argv stay empty).
fn frozen_python_finding_fields(family: &str, value: &mut serde_json::Value) {
    if family == "dead" {
        let python = value["extra"]["language_evidence"]["python"]
            .as_str()
            .map(str::to_owned)
            .or_else(|| {
                value["extra"]["native_checks"]
                    .as_array()?
                    .iter()
                    .find(|check| check["check_id"] == "python.dead")?["evidence"]
                    .as_str()
                    .map(str::to_owned)
            });
        let confidence = if python.as_deref() == Some("ESTIMATED") {
            "medium"
        } else {
            "low"
        };
        for finding in value["findings"].as_array_mut().into_iter().flatten() {
            if finding["rule_id"] == "ici.legacy.dead.target"
                && python_source(finding)
                && finding["explanation"]
                    .as_str()
                    .is_some_and(|text| text.starts_with("Adapted from the legacy"))
            {
                finding["explanation"] = serde_json::json!(
                    "Python dead-code evidence is derived from bounded AST reachability and name-reference analysis."
                );
                finding["remediation"] = serde_json::json!(
                    "Review the reported symbol or unreachable block and either remove it or make the intended reference explicit."
                );
                finding["confidence"] = serde_json::json!(confidence);
                finding["tool_name"] = serde_json::json!("");
                finding["tool_version"] = serde_json::json!("");
            }
        }
    }
    if family == "sanitize"
        && value["tool_evidence"].as_array().is_some_and(Vec::is_empty)
        && let Some(message) = value["targets"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|target| {
                target["target_name"] == "PythonResourceWarnings"
                    && target["message"].as_str().is_some_and(|text| {
                        text.starts_with("Python ResourceWarning check skipped:")
                    })
            })
            .and_then(|target| target["message"].as_str())
            .map(str::to_owned)
    {
        value["tool_evidence"] = serde_json::json!([{"name":"pytest resource warnings","path":"",
            "version":"","argv":[],"returncode":null,"timed_out":false,"truncated":false,
            "error":message}]);
        for finding in value["findings"].as_array_mut().into_iter().flatten() {
            if finding["rule_id"] == "ici.legacy.sanitize.target" && finding["tool_name"] == "" {
                finding["tool_name"] = serde_json::json!("pytest resource warnings");
            }
        }
    }
}

/// A measured legacy C++ test run whose optional coverage check had nothing to measure (frozen
/// falls back to its fixed estimate there).
fn optional_cpp_coverage_missing(engine: &serde_json::Value) -> Option<String> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let test = checks
        .iter()
        .find(|check| check["check_id"] == "cpp.test")?;
    let coverage = checks
        .iter()
        .find(|check| check["check_id"] == "cpp.coverage")?;
    let reason = coverage["summary"].as_str()?;
    // Frozen `_apply_coverage_policy` without `coverage_required`: whatever kept gcov evidence
    // from being measured (no production source, no gcov, a failed coverage build or test
    // compile) is an optional coverage warning beside the measured test verdict.
    // Frozen's gcov text reading without rows is a coverage error, not a tool error
    // (test_coverage.py:241-246, :181).
    let text_unmeasured = coverage["state"] == "FAILED" && legacy_text_coverage_error(reason);
    (checks.len() == 2
        && test["state"] == "SUCCEEDED"
        && (coverage["state"] == "BLOCKED" || text_unmeasured))
        .then(|| reason.to_owned())
}

/// A measured legacy gcov text reading whose rows miss a declared source (frozen
/// test_coverage.py:241-246).
fn incomplete_cpp_text_coverage(engine: &serde_json::Value) -> Option<String> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let coverage = checks
        .iter()
        .find(|check| check["check_id"] == "cpp.coverage")?;
    std::iter::once(&coverage["extra"])
        .chain(crate::legacy_extra::check_blocks(coverage))
        .find_map(|block| block.get("legacy_coverage_error")?.as_str())
        .map(str::to_owned)
}

/// Frozen's legacy gcov text coverage errors (test_coverage.py:245, :181 / test.py:689).
fn legacy_text_coverage_error(reason: &str) -> bool {
    reason.starts_with("legacy gcov text evidence is missing ")
        || reason == "C++ gcov coverage output was missing or malformed"
}

/// Frozen `_apply_coverage_policy` with `coverage_required`: the missing gcov evidence is a tool
/// error (`ERROR`, `NOT_RUN`) and the summary joins the first three tool errors; a missing gcov
/// is frozen's `gcov executable was unavailable`.
fn required_cpp_coverage_missing(engine: &serde_json::Value, root: &Path) -> Option<String> {
    let reason = optional_cpp_coverage_missing(engine)?;
    // A gcov text reading without rows keeps frozen's own coverage errors
    // (`required_cpp_gcov_failed`).
    if legacy_text_coverage_error(&reason) {
        return None;
    }
    // Frozen's direct g++ path compiles the sorted production sources first for every test
    // (`_compile_cpp_objects`); when the first one does not compile no notes exist, so
    // `_collect_cpp_coverage` adds `C++ gcov data files were unavailable` (test_coverage.py:177)
    // and `... output was missing or malformed` (:181) after the case's `C++ gcov coverage
    // compilation failed` (test.py:715).
    if reason.starts_with("explicit coverage preparation failed: compile:")
        && first_direct_source_failed(engine, root)
    {
        return Some(
            "C++ gcov coverage compilation failed; C++ gcov data files were unavailable; \
             C++ gcov coverage output was missing or malformed"
                .to_owned(),
        );
    }
    let first = if reason.starts_with("gcov: not found") {
        "gcov executable was unavailable".to_owned()
    } else {
        reason
    };
    Some(format!(
        "{first}; C++ gcov coverage evidence was unavailable or malformed"
    ))
}

/// Whether the sorted first production source of the direct g++ coverage compiles (sources
/// outside `tests/`) failed to compile.
fn first_direct_source_failed(engine: &serde_json::Value, root: &Path) -> bool {
    let tests = root.join("tests");
    engine["tool_evidence"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|record| record["name"] == "g++ coverage compile")
        .filter_map(|record| {
            let argv = record["argv"].as_array()?;
            let index = argv.iter().position(|argument| argument == "-c")?;
            let source = Path::new(argv.get(index.checked_sub(1)?)?.as_str()?);
            (!source.starts_with(&tests))
                .then(|| (source.to_path_buf(), record["returncode"].clone()))
        })
        .min_by(|left, right| left.0.cmp(&right.0))
        .is_some_and(|(_, code)| code.as_i64().is_some_and(|code| code != 0))
}

/// Frozen `_apply_coverage_policy` with `coverage_required` after a gcov tool error: the
/// summary joins the first three tool errors, which are the gcov error, then frozen's coverage
/// errors — `C++ gcov capability could not be determined` for the direct path's incomplete
/// `gcov --help` (test_coverage.py:156), else `C++ gcov coverage output was missing or
/// malformed` (:181, test.py:674) — and `C++ gcov coverage evidence was unavailable or
/// malformed` (test.py:369).
fn required_cpp_gcov_failed(engine: &serde_json::Value) -> Option<String> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let test = checks
        .iter()
        .find(|check| check["check_id"] == "cpp.test")?;
    let coverage = checks
        .iter()
        .find(|check| check["check_id"] == "cpp.coverage")?;
    let reason = coverage["summary"].as_str()?;
    let gcov_error = reason == "gcov JSON capability probe was incomplete"
        || reason == "gcov output was incomplete"
        || reason.starts_with("gcov failed with exit code ")
        || reason.starts_with("gcov produced no ")
        || reason.starts_with("gcov JSON evidence rejected (")
        || legacy_text_coverage_error(reason);
    if checks.len() != 2
        || test["state"] != "SUCCEEDED"
        || coverage["state"] != "FAILED"
        || !gcov_error
    {
        return None;
    }
    let direct = engine["tool_evidence"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|record| record["name"] == "g++ coverage compile");
    let second = if direct && reason == "gcov JSON capability probe was incomplete" {
        "C++ gcov capability could not be determined"
    } else {
        "C++ gcov coverage output was missing or malformed"
    };
    // A gcov text reading without rows and without a declared source (test_coverage.py:241)
    // only has frozen's two coverage errors.
    if reason == second {
        return Some(format!(
            "{reason}; C++ gcov coverage evidence was unavailable or malformed"
        ));
    }
    Some(format!(
        "{reason}; {second}; C++ gcov coverage evidence was unavailable or malformed"
    ))
}

pub(crate) fn open_page(
    path: &Path,
    root: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<()> {
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
/// Frozen `_run_project_tests` reports "[C++] Tests" only when the configured scope selected a
/// C++ source; legacy C++ runtime checks are bound even over an empty scope. The source alone
/// is not enough for a declared `type = "python"` project (test.py:268, `proj_type in ("cpp",
/// "hybrid") and cpp_sources`): only a `tests/**/*.cpp` suite names its C++ tests.
fn cpp_in_scope(prepared: &crate::planning::PreparedPlan) -> bool {
    if crate::legacy_support::declared_type(&prepared.project.effective) == Some("python") {
        return false;
    }
    prepared.project.components.iter().any(|component| {
        component
            .files
            .iter()
            .any(|file| ici_application::plan::file_language(file) == Some("cpp"))
    })
}

fn no_tests_collected(
    root: &Path,
    engine: &serde_json::Value,
    cpp_scope: bool,
) -> Option<Vec<&'static str>> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    // Only "no runnable suite exists" blocks qualify; any other block (interpreter, config,
    // tool) stays incomplete evidence. A CMake project without `enable_testing()` writes no
    // CTest inventory; with no `tests/**/*.cpp` frozen never reaches ctest either.
    let no_suite = |check: &serde_json::Value| {
        check["summary"].as_str().is_some_and(|summary| {
            summary.starts_with("no sources in scope")
                || summary == "C++ runtime checks require a declared component build"
                || summary.starts_with("CTest inventory is unavailable: ")
                || summary == crate::cpp_validation::NO_PRODUCTION_COVERAGE
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
        if (label != "C++" || cpp_scope)
            && checks.iter().any(|check| {
                check["check_id"]
                    .as_str()
                    .is_some_and(|id| id.starts_with(prefix))
                    && !check["summary"]
                        .as_str()
                        .is_some_and(|summary| summary.starts_with("no sources in scope"))
            })
        {
            languages.push(label);
        }
    }
    Some(languages)
}

/// Frozen `_record_missing_tool`: the evidence of a tool that was looked for and not run.
fn missing_tool(name: &str) -> serde_json::Value {
    serde_json::json!([{"name":name,"path":"","version":"","argv":[],"returncode":null,
        "timed_out":false,"truncated":false,"error":"tool not found; no command was executed"}])
}

/// Frozen `_lint_python` without Ruff: the AST syntax targets of every selected Python file and
/// the `ASTSyntaxFallback` inventory target; the syntax failure count and parsed file count.
/// The legacy view's own analysis (the AST fallback parse) is part of the engine's run time.
fn add_duration(value: &mut serde_json::Value, elapsed: std::time::Duration) {
    let seconds = value["duration"].as_f64().unwrap_or(0.0) + elapsed.as_secs_f64();
    value["duration"] = serde_json::json!(seconds);
}

fn python_fallback_targets(
    prepared: &crate::planning::PreparedPlan,
    root: &Path,
) -> Result<(Vec<serde_json::Value>, usize, usize)> {
    let mut targets = vec![];
    let mut parsed = 0;
    for component in &prepared.project.components {
        parsed += component
            .files
            .iter()
            .filter(|path| path.ends_with(".py"))
            .count();
        targets.extend(crate::legacy::python_syntax_targets(root, &component.files));
    }
    let fail = targets.len();
    let mut targets: Vec<_> = targets
        .into_iter()
        .map(|target| serde_json::to_value(target).map_err(error))
        .collect::<Result<_>>()?;
    targets.push(serde_json::json!({"file_path":".","start_line":1,"end_line":null,
            "start_column":null,"end_column":null,"target_name":"ASTSyntaxFallback",
            "status":"PASS","message":format!("Ruff was unavailable; parsed {parsed} Python file(s) for syntax errors only. Style and lint rules were not checked."),
            "snippet":"","metrics":{"files_parsed":parsed}}));
    Ok((targets, fail, parsed))
}

/// A check refused at planning because `tool` could not be resolved: found nowhere (frozen
/// `<tool> is not available`) or any other native resolution failure (`<tool>: …`).
fn tool_unresolved(summary: &str, tool: &str) -> bool {
    summary
        .strip_prefix(tool)
        .is_some_and(|rest| rest == " is not available" || rest.starts_with(": "))
}

/// A hybrid lint view whose Ruff checks were refused at planning (`ruff: …`) while its C++ lint
/// checks ran: the Python facet takes frozen's AST fallback beside the C++ targets.
fn hybrid_ruff_unavailable(engine: &serde_json::Value) -> bool {
    let Some(checks) = engine["extra"]["native_checks"].as_array() else {
        return false;
    };
    let (python, cpp): (Vec<_>, Vec<_>) = checks.iter().partition(|check| {
        matches!(
            check["check_id"].as_str(),
            Some("python.lint" | "python.format")
        )
    });
    !python.is_empty()
        && !cpp.is_empty()
        && python.iter().all(|check| {
            check["state"] == "BLOCKED"
                && check["summary"]
                    .as_str()
                    .is_some_and(|summary| tool_unresolved(summary, "ruff"))
        })
        && cpp.iter().all(|check| {
            check["check_id"]
                .as_str()
                .is_some_and(|id| id.starts_with("cpp."))
                && check["state"] != "BLOCKED"
        })
}

/// The historical Ruff could not be resolved for a Python-only lint view: both Ruff checks were
/// refused at planning (a hybrid view is `hybrid_ruff_unavailable`).
fn ruff_unavailable(engine: &serde_json::Value) -> bool {
    engine["extra"]["native_checks"]
        .as_array()
        .is_some_and(|checks| {
            !checks.is_empty()
                && checks.iter().all(|check| {
                    matches!(
                        check["check_id"].as_str(),
                        Some("python.lint" | "python.format")
                    ) && check["state"] == "BLOCKED"
                        && check["summary"]
                            .as_str()
                            .is_some_and(|summary| tool_unresolved(summary, "ruff"))
                })
        })
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
                        .is_some_and(|summary| tool_unresolved(summary, "mypy"))
            })
        })
}

/// Every native check of the engine is a Python check refused at planning because no Python
/// source was selected ("no sources in scope for python").
fn python_sources_unselected(engine: &serde_json::Value) -> bool {
    engine["extra"]["native_checks"]
        .as_array()
        .is_some_and(|checks| {
            !checks.is_empty()
                && checks.iter().all(|check| {
                    check["state"] == "BLOCKED"
                        && check["check_id"]
                            .as_str()
                            .is_some_and(|id| id.starts_with("python."))
                        && check["summary"] == "no sources in scope for python"
                })
        })
}

/// Every native check of the engine is a C++ check refused at planning because the configured
/// source directories selected no C/C++ source ("no sources in scope for cpp").
fn cpp_sources_unselected(engine: &serde_json::Value) -> bool {
    engine["extra"]["native_checks"]
        .as_array()
        .is_some_and(|checks| {
            !checks.is_empty()
                && checks.iter().all(|check| {
                    check["state"] == "BLOCKED"
                        && check["check_id"]
                            .as_str()
                            .is_some_and(|id| id.starts_with("cpp."))
                        && check["summary"] == "no sources in scope for cpp"
                })
        })
}

/// Frozen `LintEngine.run` with neither a Python nor a C++ source selected (LintScope): every
/// native lint check was refused at planning for its language's empty scope.
fn no_lint_sources(engine: &serde_json::Value) -> bool {
    engine["extra"]["native_checks"]
        .as_array()
        .is_some_and(|checks| {
            !checks.is_empty()
                && checks.iter().all(|check| {
                    check["state"] == "BLOCKED"
                        && matches!(
                            check["summary"].as_str(),
                            Some("no sources in scope for python" | "no sources in scope for cpp")
                        )
                })
        })
}

type NoSources = (
    &'static str,
    &'static str,
    String,
    Option<(&'static str, &'static str)>,
);
/// Frozen single-engine verdicts when the project selected no source of its language: every
/// native check of the engine was refused at planning ("no sources in scope for python|cpp").
/// A C++ project's engines run over the empty scope too (its build system's tests still run).
fn no_python_sources(family: &str, engine: &serde_json::Value, warn_cc: &str) -> Option<NoSources> {
    let cpp = cpp_sources_unselected(engine);
    if !python_sources_unselected(engine) && !cpp && !(family == "lint" && no_lint_sources(engine))
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
        "python_compat" => (
            "SKIP",
            "NOT_APPLICABLE",
            "Python compatibility analysis skipped: no Python source files".into(),
            Some(("PythonRuntime:NotApplicable", none)),
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
        // Frozen `CompileDbEngine` without a production translation unit: the same verdict when
        // the project has no C/C++ and the configured scope selected no Python either (the
        // `python.compile-db` applicability check was refused for that empty Python scope).
        "compile_db" => (
            "SKIP",
            "NOT_APPLICABLE",
            "No production C/C++ translation units are in scope.".into(),
            None,
        ),
        // Frozen `SanitizeEngine` / `ThreadSanitizeEngine` with neither a Python nor a C/C++
        // source selected: a not-applicable inventory target, never a required engine that did
        // not run.
        "sanitize" if !cpp => (
            "SKIP",
            "NOT_APPLICABLE",
            "Sanitize skipped: no applicable checks were executed".into(),
            Some((
                "Sanitize",
                "No applicable Python or C++ sources were selected; sanitize was not run",
            )),
        ),
        "thread_sanitize" if !cpp => (
            "SKIP",
            "NOT_APPLICABLE",
            "ThreadSanitizer skipped: no applicable C++ checks were executed".into(),
            Some((
                "ThreadSanitizer",
                "No applicable C++ sources were selected; ThreadSanitizer was not run",
            )),
        ),
        // Frozen `ComplexityEngine` over no function: a PASS naming the warning limit (the C++
        // brace scanner's boundary mode stays heuristic, so its evidence is ESTIMATED).
        "complexity" => (
            "PASS",
            if cpp { "ESTIMATED" } else { "MEASURED" },
            format!("Max Cyclomatic Complexity: 0 (limit {warn_cc}) across 0 functions (0 issues)"),
            None,
        ),
        "type" if cpp => (
            "SKIP",
            "NOT_APPLICABLE",
            "Type checking not applicable: no Python sources; C++ is unsupported".into(),
            Some((
                "C++TypeCheck",
                "No applicable C++ source files were selected; type checking was not run",
            )),
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
                summary == "pytest did not report a completed nonempty suite (exit 5)"
                    || summary == "pytest did not report a completed nonempty suite (exit 0)"
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

/// Frozen `TestEngine` on a hybrid project whose pytest collected nothing (or did not run: no
/// `tests` directory, `validation::LEGACY_NO_TESTS_DIRECTORY`) and whose C++ facet has no
/// runnable suite: `[Python] Tests` and, when C++ is in scope and `tests/` holds no `*.cpp`,
/// `[C++] Tests` are both "No tests collected" (a FAIL verdict, not missing evidence).
fn hybrid_empty_pytest_suite(
    root: &Path,
    engine: &serde_json::Value,
    cpp_scope: bool,
) -> Option<Vec<&'static str>> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let (python, cpp): (Vec<_>, Vec<_>) = checks.iter().partition(|check| {
        check["check_id"]
            .as_str()
            .is_some_and(|id| id.starts_with("python."))
    });
    if cpp.is_empty()
        || !cpp.iter().all(|check| {
            check["check_id"]
                .as_str()
                .is_some_and(|id| id.starts_with("cpp."))
                && check["state"] == "BLOCKED"
                && check["summary"].as_str().is_some_and(|summary| {
                    summary == "C++ runtime checks require a declared component build"
                        || summary.starts_with("CTest inventory is unavailable: ")
                        || summary == crate::cpp_validation::NO_PRODUCTION_COVERAGE
                })
        })
    {
        return None;
    }
    let mut alone = engine.clone();
    let unrun = !python.is_empty()
        && python.iter().all(|check| {
            check["state"] == "BLOCKED"
                && check["summary"] == crate::validation::LEGACY_NO_TESTS_DIRECTORY
        });
    alone["extra"]["native_checks"] = serde_json::json!(python);
    if !unrun {
        empty_pytest_suite(&alone)?;
    }
    let mut languages = vec!["Python"];
    if cpp_scope && !tests_have_cpp_sources(root)? {
        languages.push("C++");
    }
    Some(languages)
}

/// Whether `tests/` holds a `*.cpp` file (frozen C++ test discovery); `None` past the walk bound.
fn tests_have_cpp_sources(root: &Path) -> Option<bool> {
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
            let metadata = std::fs::symlink_metadata(entry.path()).ok()?;
            if metadata.is_dir() {
                pending.push(entry.path());
            } else if entry.file_name().to_string_lossy().ends_with(".cpp") {
                return Some(true);
            }
        }
    }
    Some(false)
}

/// Frozen `no_tests_executed` (`total_tests > 0`, no failure, every case skipped) over the
/// completed native Python test check; the count of collected cases. C++ suites keep their own
/// verdicts (cpp facet).
fn python_tests_not_executed(engine: &serde_json::Value) -> Option<u64> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let test = checks
        .iter()
        .find(|check| check["check_id"] == "python.test" && check["state"] == "SUCCEEDED")?;
    if checks.iter().any(|check| {
        check["check_id"]
            .as_str()
            .is_some_and(|id| id.starts_with("cpp."))
    }) {
        return None;
    }
    let counts = test["extra"].as_object()?.values().find(|value| {
        value
            .get("total_tests")
            .is_some_and(serde_json::Value::is_u64)
    })?;
    let total = counts["total_tests"].as_u64()?;
    let failed = engine["targets"].as_array()?.iter().any(|target| {
        target["target_name"]
            .as_str()
            .is_some_and(|name| name.contains("::"))
            && matches!(target["status"].as_str(), Some("FAIL" | "ERROR"))
    });
    (total > 0
        && counts["passed_tests"].as_u64() == Some(0)
        && counts["skipped_tests"].as_u64()? >= total
        && !failed)
        .then_some(total)
}

/// Frozen `TestEngine._run_project_tests`: a Python project without a `tests/` directory is
/// marked "No tests collected" without running pytest (so no project interpreter is needed).
/// Native keeps a completed pytest run's own evidence; only a test check that did not succeed
/// takes the frozen verdict.
fn python_without_tests_dir(root: &Path, engine: &serde_json::Value) -> Option<Vec<&'static str>> {
    let checks = engine["extra"]["native_checks"].as_array()?;
    let id = |check: &serde_json::Value| check["check_id"].as_str().unwrap_or("").to_owned();
    let python_only =
        !checks.is_empty() && checks.iter().all(|check| id(check).starts_with("python."));
    let test_not_run = checks
        .iter()
        .any(|check| id(check) == "python.test" && check["state"] != "SUCCEEDED");
    let no_sources = checks.iter().any(|check| {
        check["summary"]
            .as_str()
            .is_some_and(|summary| summary.starts_with("no sources in scope"))
    });
    (python_only && test_not_run && !no_sources && !root.join("tests").exists())
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

/// Passed/total cases the legacy python.test check measured (its own extra block).
fn python_test_counts(engine: &serde_json::Value) -> Option<(u64, u64)> {
    let test = engine["extra"]["native_checks"]
        .as_array()?
        .iter()
        .find(|check| check["check_id"] == "python.test" && check["state"] == "SUCCEEDED")?;
    test["extra"].as_object()?.values().find_map(|extra| {
        Some((
            extra["passed_tests"].as_u64()?,
            extra["total_tests"].as_u64()?,
        ))
    })
}

/// The frozen coverage-probe tool error a legacy python.coverage check failed with
/// (`ici_validation::python_coverage::LEGACY_PROBE_ERRORS`).
fn legacy_coverage_probe_error(engine: &serde_json::Value) -> Option<String> {
    let coverage = engine["extra"]["native_checks"]
        .as_array()?
        .iter()
        .find(|check| check["check_id"] == "python.coverage")?;
    let reason = coverage["summary"].as_str()?;
    let [timed_out, truncated, terminated, exit] =
        ici_validation::python_coverage::LEGACY_PROBE_ERRORS;
    (coverage["state"] == "FAILED"
        && ([timed_out, truncated, terminated].contains(&reason)
            || reason
                .strip_prefix(exit)
                .is_some_and(|code| code.parse::<i32>().is_ok())))
    .then(|| reason.to_owned())
}

/// A changed-line selection the native planner refused (frozen evaluates it at run time and
/// reports an ERROR policy target).
fn refused_policy(engine: &serde_json::Value) -> Option<String> {
    engine["extra"]["native_checks"]
        .as_array()?
        .iter()
        .filter_map(|check| {
            let summary = check["summary"].as_str()?;
            // Planning refuses changed lines; the measured inventory bound refuses after parse.
            ((check["state"] == "BLOCKED" && summary.starts_with("changed line"))
                || summary == ici_validation::policy::POLICY_ROWS_REFUSAL)
                .then_some(summary)
        })
        .next()
        .map(str::to_owned)
}

/// Frozen `VerifyEngine._apply_baseline` over the shaped legacy suite: the legacy findings of every
/// engine (PASS inventory included) and the run's coverage regressions, compared with the legacy
/// suite the baseline saved (`ici_reporting::frozen_baseline_comparison`). A failed
/// `--fail-on-new` gate turns a passing suite FAIL; returns whether the gate failed.
fn frozen_baseline(
    document: &mut serde_json::Value,
    run: &crate::execute::ExecutedRun,
) -> Result<bool> {
    let (Some(native), Some(saved)) = (&run.legacy_baseline, &run.legacy_baseline_suite) else {
        return Ok(false);
    };
    let mut current = ici_reporting::legacy_records(&document["results"]);
    for finding in &run.result.findings {
        let Some(kind) = finding
            .rule_id
            .strip_prefix(ici_reporting::COVERAGE_REGRESSION_RULE)
        else {
            continue;
        };
        let path = &finding.primary_location.path;
        let name = format!("Coverage regression:{kind}");
        let metrics = run
            .outputs
            .values()
            .flat_map(|output| &output.targets)
            .find(|target| target.target_name == name && &target.file_path == path)
            .map(|target| &target.metrics);
        let number = |key: &str| metrics.and_then(|metrics| metrics.get(key)?.as_f64());
        if let (Some(actual), Some(before), Some(drop)) =
            (number("actual"), number("baseline"), number("allowed_drop"))
        {
            current.push(
                ici_reporting::coverage_regression_record(kind, path, actual, before, drop)
                    .map_err(error)?,
            );
        }
    }
    let statuses = document["results"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|engine| {
            (
                engine["engine_name"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned(),
                engine["status"].as_str().unwrap_or_default().to_owned(),
            )
        })
        .collect();
    let comparison = ici_reporting::frozen_baseline_comparison(
        &current,
        &ici_reporting::legacy_records(&saved["results"]),
        &statuses,
        native["source_path"].as_str().unwrap_or_default(),
        native["warnings"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|warning| warning.as_str().map(str::to_owned))
            .collect(),
        saved["analysis_metadata"].clone(),
        native["fail_on_new"] == true,
    );
    let failed = comparison["gate_failed"] == true;
    document["baseline_comparison"] = comparison;
    if failed && !matches!(document["suite_status"].as_str(), Some("FAIL" | "ERROR")) {
        document["suite_status"] = serde_json::json!("FAIL");
    }
    Ok(failed)
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
    use super::{
        first_direct_source_failed, optional_coverage_verdict, optional_cpp_coverage_missing,
        required_cpp_coverage_missing, required_cpp_gcov_failed,
    };
    use serde_json::json;
    use std::path::Path;

    /// Frozen `_compile_cpp_objects` compiles the sorted production sources before the test, so
    /// only the first production source decides whether any notes exist.
    #[test]
    fn first_direct_source_decides_whether_notes_exist() {
        let engine = |records: &[(&str, i64)]| {
            json!({"tool_evidence": records.iter().map(|(source, code)| json!({
                "name": "g++ coverage compile", "returncode": code,
                "argv": ["/usr/bin/g++", "-std=c++17", source, "-c", "-o", "x.o"]}))
                .collect::<Vec<_>>()})
        };
        let root = Path::new("/p");
        assert!(first_direct_source_failed(
            &engine(&[
                ("/p/src/b.cpp", 0),
                ("/p/src/a.cpp", 1),
                ("/p/tests/t.cpp", 0)
            ]),
            root
        ));
        assert!(!first_direct_source_failed(
            &engine(&[
                ("/p/src/a.cpp", 0),
                ("/p/src/b.cpp", 1),
                ("/p/tests/t.cpp", 1)
            ]),
            root
        ));
        assert!(!first_direct_source_failed(
            &engine(&[("/p/tests/t.cpp", 1)]),
            root
        ));
    }

    /// Frozen `_apply_coverage_policy` for C++: unmeasured gcov evidence beside a completed
    /// test check is an optional warning (the native reason kept), or with `coverage_required`
    /// frozen's tool errors (`gcov executable was unavailable` for a missing gcov); a test check
    /// that did not complete, or measured coverage, is neither.
    #[test]
    fn blocked_cpp_coverage_is_optional_or_the_frozen_required_error() {
        let engine = |test: &str, coverage: &str, reason: &str| {
            json!({"extra": {"native_checks": [
                {"check_id": "cpp.test", "state": test},
                {"check_id": "cpp.coverage", "state": coverage, "summary": reason}]}})
        };
        let missing = engine("SUCCEEDED", "BLOCKED", "gcov: not found; prepare the tool");
        assert_eq!(
            optional_cpp_coverage_missing(&missing).as_deref(),
            Some("gcov: not found; prepare the tool")
        );
        assert_eq!(
            required_cpp_coverage_missing(&missing, Path::new("/p")).as_deref(),
            Some(
                "gcov executable was unavailable; C++ gcov coverage evidence was unavailable or malformed"
            )
        );
        let build = engine(
            "SUCCEEDED",
            "BLOCKED",
            "explicit coverage preparation failed",
        );
        assert_eq!(
            required_cpp_coverage_missing(&build, Path::new("/p")).as_deref(),
            Some(
                "explicit coverage preparation failed; C++ gcov coverage evidence was unavailable or malformed"
            )
        );
        assert_eq!(
            optional_cpp_coverage_missing(&engine("FAILED", "BLOCKED", "x")),
            None
        );
        assert_eq!(
            optional_cpp_coverage_missing(&engine("SUCCEEDED", "SUCCEEDED", "x")),
            None
        );
    }

    /// Frozen required C++ coverage after a gcov tool error (captures
    /// test:direct-gcov-required-*): the gcov error, then the frozen coverage errors.
    #[test]
    fn failed_gcov_under_coverage_required_joins_frozen_tool_errors() {
        let engine = |reason: &str, direct: bool| {
            json!({
                "tool_evidence": [{"name": if direct { "g++ coverage compile" } else { "ctest" }}],
                "extra": {"native_checks": [
                    {"check_id": "cpp.test", "state": "SUCCEEDED"},
                    {"check_id": "cpp.coverage", "state": "FAILED", "summary": reason}]}})
        };
        let probe = "gcov JSON capability probe was incomplete";
        assert_eq!(
            required_cpp_gcov_failed(&engine(probe, true)).as_deref(),
            Some(
                "gcov JSON capability probe was incomplete; C++ gcov capability could not be determined; C++ gcov coverage evidence was unavailable or malformed"
            )
        );
        // Build-session `collect_coverage` adds no capability error.
        assert_eq!(
            required_cpp_gcov_failed(&engine(probe, false)).as_deref(),
            Some(
                "gcov JSON capability probe was incomplete; C++ gcov coverage output was missing or malformed; C++ gcov coverage evidence was unavailable or malformed"
            )
        );
        assert_eq!(
            required_cpp_gcov_failed(&engine("gcov failed with exit code 3", true)).as_deref(),
            Some(
                "gcov failed with exit code 3; C++ gcov coverage output was missing or malformed; C++ gcov coverage evidence was unavailable or malformed"
            )
        );
        // A native refusal is not a frozen gcov tool error.
        assert_eq!(
            required_cpp_gcov_failed(&engine("gcov report ownership/capture failed", true)),
            None
        );
    }

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
