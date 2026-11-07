//! Frozen `_cpp_include_graph.build_compiler_cpp_graph` outcomes of the legacy cycle projection
//! (`ici verify` with a compilation context): context and unit diagnostics, production sources
//! without a command, no traceable unit, rejected replay commands and failed include traces
//! are per-unit targets and engine errors, while every other unit is still traced and its graph
//! analysed (frozen never stops the engine at the first bad unit).
use super::*;
use ici_execution::TaskOutcome;

/// One selected unit in frozen order: its context diagnostic targets, then either the include
/// trace task that runs it or the replay error target that replaces the trace.
#[derive(Clone, Debug)]
pub(super) struct IncludeUnit {
    pub source: String,
    pub configuration: String,
    pub before: Vec<InspectionTarget>,
    /// `Ok(task id)` when traced; `Err(None)` when an error diagnostic skipped the unit;
    /// `Err(Some(target))` for a rejected replay command.
    pub replay: Result<String, Option<InspectionTarget>>,
}

/// The frozen per-run targets and errors decided before any include trace runs.
#[derive(Clone, Debug, Default)]
pub(super) struct IncludePlan {
    pub prefix: Vec<InspectionTarget>,
    pub errors: Vec<String>,
    pub units: Vec<IncludeUnit>,
}

fn target(path: &str, name: &str, status: &str, message: String) -> InspectionTarget {
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = name.into();
    target.status = status.into();
    target.message = message;
    target
}

/// Frozen `compilation_context_present`: a database, recorded units or an error diagnostic.
pub(super) fn context_present(context: &CompilationContext) -> bool {
    !context.units.is_empty()
        || context.database_path.is_some()
        || context
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.level == "error")
}

/// Frozen `_context_target` carries the shared context-diagnostic target under its own name.
fn renamed(mut target: InspectionTarget) -> InspectionTarget {
    target.target_name = "CppIncludeContextDiagnostic".into();
    target
}

/// Frozen order before the per-unit loop: context diagnostics, `CppIncludeContextMissing` for
/// each production source without a command, and the stop when nothing is traceable.
/// Returns `false` when no unit is selected (frozen returns before the loop).
pub(super) fn begin(plan: &mut IncludePlan, context: &CompilationContext, selected: usize) -> bool {
    plan.prefix.extend(
        legacy_syntax::context_targets(context)
            .into_iter()
            .map(renamed),
    );
    if context
        .diagnostics
        .iter()
        .any(|diagnostic| diagnostic.level == "error")
    {
        plan.errors
            .push("Compilation context contains error diagnostics".into());
    }
    // Frozen `_selected_units`: every selected production source without a recorded unit, also
    // when no database was produced (a failed canonical preparation leaves only diagnostics).
    let missing: Vec<&String> = if context.database_path.is_none() && context.units.is_empty() {
        context
            .selected_sources
            .iter()
            .filter(|source| ici_cpp::is_translation_unit(source))
            .collect()
    } else {
        context.uncovered_sources.iter().collect()
    };
    for path in &missing {
        plan.prefix.push(target(
            path,
            "CppIncludeContextMissing",
            "WARN",
            "No exact translation-unit command covers this production source.".into(),
        ));
    }
    if !missing.is_empty() {
        plan.errors.push(format!(
            "Compilation context is missing {} production source command(s)",
            missing.len()
        ));
    }
    if selected == 0 {
        let message = "Compilation context contains no include-trace production units";
        plan.errors.push(message.into());
        plan.prefix.push(target(
            context.database_path.as_deref().unwrap_or("."),
            "CppIncludeContextError",
            "ERROR",
            message.into(),
        ));
        return false;
    }
    true
}

/// A unit's diagnostic targets and whether one is an error (frozen then skips its trace).
pub(super) fn unit_targets(unit: &CompilationUnit) -> (Vec<InspectionTarget>, bool) {
    let (targets, error) = legacy_syntax::unit_targets(unit);
    (targets.into_iter().map(renamed).collect(), error.is_some())
}

/// Frozen `CppIncludeReplayError` target (`<code>: <message>`).
pub(super) fn replay_error(unit: &CompilationUnit, error: &ici_cpp::CppError) -> InspectionTarget {
    let (code, message) = legacy_syntax::frozen_replay(unit, error);
    target(
        &unit.source,
        "CppIncludeReplayError",
        "ERROR",
        format!("{code}: {message}"),
    )
}

/// Frozen `build_replay_command` compiler resolution for a unit whose recorded compiler cannot
/// be found: a bare name missing from PATH is 'not available on PATH', a path that does not
/// resolve 'could not be resolved' (both `compiler-unavailable`). `None` for any other refusal.
pub(super) fn unresolved_compiler(
    root: &Path,
    unit: &CompilationUnit,
    environment: &BTreeMap<String, String>,
) -> Option<InspectionTarget> {
    let value = unit.argv.first()?;
    let lexical = Path::new(value);
    let message = if lexical.components().count() > 1 || lexical.is_absolute() {
        let candidate = if lexical.is_absolute() {
            lexical.to_path_buf()
        } else {
            root.join(&unit.directory).join(lexical)
        };
        candidate
            .canonicalize()
            .is_err()
            .then_some("The translation unit compiler could not be resolved.")?
    } else {
        let found = environment.get("PATH").is_some_and(|path| {
            std::env::split_paths(path).any(|directory| directory.join(value).is_file())
        });
        (!found).then_some("The translation unit compiler is not available on PATH.")?
    };
    Some(target(
        &unit.source,
        "CppIncludeReplayError",
        "ERROR",
        format!("compiler-unavailable: {message}"),
    ))
}

/// What one include trace established, after frozen `_evaluate_trace`.
pub(super) enum Trace {
    Observed(ici_cpp::CompilerIncludeObservation),
    Unresolved(ici_cpp::UnresolvedIncludeTrace),
    Failed(String),
}

/// Frozen `_cpp_include_trace.parse_include_trace` `ValueError` text of a native parse refusal:
/// the entry/depth limits are its bounded shape, a parent jump and a path that is not an existing
/// file keep frozen's sentences; other refusals keep the native message.
fn frozen_parse_error(error: &ici_cpp::CppError) -> &str {
    match error.code.as_str() {
        "include-trace-limit" => "compiler include trace exceeds its bounded shape",
        "include-trace-depth" => "compiler include trace jumps over a parent depth",
        "include-trace-stale" => "compiler include trace references a stale path",
        _ => &error.message,
    }
}

/// Frozen `_trace_process_error`, the located missing-header stop, the exit-code failure and
/// `_consume_successful_trace`'s parse refusals, in frozen order.
pub(super) fn evaluate(root: &Path, probe: &IncludeProbe, outcome: &TaskOutcome) -> Trace {
    let source = &probe.unit.source;
    if outcome.timed_out {
        return Trace::Failed(format!("Compiler include trace timed out: {source}"));
    }
    if outcome.truncated {
        return Trace::Failed(format!("Compiler include trace was truncated: {source}"));
    }
    let Some(code) = outcome
        .exit_code
        .filter(|code| *code >= 0 && outcome.signal.is_none())
    else {
        return Trace::Failed(format!(
            "Compiler include trace terminated unexpectedly: {source}"
        ));
    };
    if !String::from_utf8_lossy(&outcome.stdout).trim().is_empty() {
        return Trace::Failed(format!(
            "Compiler include trace emitted unexpected stdout: {source}"
        ));
    }
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    let cwd = Path::new(&probe.task.cwd);
    let input = root.join(source);
    if code != 0 {
        if let Ok(targets) = ici_cpp::parse_missing_include_targets(root, cwd, &input, &stderr)
            && !targets.is_empty()
        {
            return Trace::Unresolved(ici_cpp::UnresolvedIncludeTrace {
                source: source.clone(),
                configuration: probe.unit.configuration.clone(),
                targets,
            });
        }
        return Trace::Failed(format!(
            "Compiler include trace failed with exit code {code}: {source}"
        ));
    }
    match ici_cpp::parse_include_trace(root, cwd, &input, &stderr) {
        Err(error) => Trace::Failed(format!(
            "Compiler include trace was not parseable: {source}: {}",
            frozen_parse_error(&error)
        )),
        Ok(trace) if !trace.complete() => Trace::Failed(format!(
            "Compiler include trace contained unexpected diagnostics: {source}"
        )),
        Ok(trace) => Trace::Observed(ici_cpp::CompilerIncludeObservation {
            source: source.clone(),
            configuration: probe.unit.configuration.clone(),
            complete: true,
            trace,
        }),
    }
}

/// Frozen `_record_process`: one "compiler include trace" record per process run (frozen leaves
/// its `version` empty).
pub(super) fn evidence(probe: &IncludeProbe, outcome: &TaskOutcome, error: Option<&str>) -> Value {
    let error = error.map(str::to_owned).unwrap_or_else(|| {
        if outcome.timed_out {
            "timed out".into()
        } else if outcome.truncated {
            "output truncated".into()
        } else if outcome.exit_code.is_none_or(|code| code < 0) || outcome.signal.is_some() {
            "process failed to start or terminated by signal".into()
        } else {
            String::new()
        }
    });
    json!({"name":"compiler include trace","path":probe.task.argv.first(),"argv":probe.task.argv,
        "returncode":super::legacy_tooling::frozen_returncode(outcome),"timed_out":outcome.timed_out,"truncated":outcome.truncated,
        "error":error,"version":""})
}

/// The legacy cycle output: the traced units' graph analysed by the shared compiler-cycle
/// analysis, with frozen's per-unit targets, engine errors, summary and extra counters.
pub(super) fn assemble(
    request: &AnalysisRequest,
    context: &CompilationContext,
    plan: &IncludePlan,
    observations: &[ici_cpp::CompilerIncludeObservation],
    unresolved: &[ici_cpp::UnresolvedIncludeTrace],
    failures: &BTreeMap<String, String>,
    tool_evidence: Vec<Value>,
) -> AnalysisOutput {
    let traced: BTreeSet<_> = observations
        .iter()
        .map(|item| (item.source.clone(), item.configuration.clone()))
        .chain(
            unresolved
                .iter()
                .map(|item| (item.source.clone(), item.configuration.clone())),
        )
        .collect();
    let mut output = if traced.is_empty() {
        let mut output = AnalysisOutput::new(&request.task_id, "compiler.includes");
        output.observation.state = TaskState::Succeeded;
        output
    } else {
        // Only the traced units form the graph; everything else is a per-unit outcome below.
        let mut graph_context = context.clone();
        graph_context.diagnostics.clear();
        graph_context.uncovered_sources.clear();
        graph_context
            .units
            .retain(|unit| traced.contains(&(unit.source.clone(), unit.configuration.clone())));
        for unit in &mut graph_context.units {
            unit.diagnostics.clear();
        }
        ici_cpp::analyze_compiler_include_cycles_with_unresolved(
            request,
            &graph_context,
            observations,
            unresolved,
        )
    };
    let mut errors = plan.errors.clone();
    let mut traces: Vec<InspectionTarget> = vec![];
    let mut rest = vec![];
    for item in std::mem::take(&mut output.targets) {
        if item.target_name == "CppIncludeTrace" {
            traces.push(item);
        } else {
            rest.push(item);
        }
    }
    let mut targets = plan.prefix.clone();
    for unit in &plan.units {
        targets.extend(unit.before.iter().cloned());
        if unit.before.iter().any(|item| item.status == "ERROR") {
            errors.push(format!(
                "Compilation unit has error diagnostics: {}",
                unit.source
            ));
        }
        match &unit.replay {
            Err(None) => {}
            Err(Some(replay)) => {
                errors.push(replay_message(replay, &unit.source));
                targets.push(replay.clone());
            }
            Ok(task) => {
                if let Some(message) = failures.get(task) {
                    errors.push(message.clone());
                    targets.push(target(
                        &unit.source,
                        "CppIncludeTraceError",
                        "ERROR",
                        message.clone(),
                    ));
                } else if let Some(index) = traces.iter().position(|item| {
                    item.file_path == unit.source
                        && item.metrics.get("configuration") == Some(&json!(unit.configuration))
                }) {
                    targets.push(traces.remove(index));
                }
            }
        }
    }
    targets.extend(traces);
    targets.extend(rest);
    output.targets = targets;
    let cycles = output.extra["cycles_reported"].as_u64().unwrap_or(0)
        + output.extra["cycles_truncated"].as_u64().unwrap_or(0);
    let unresolved_count = output.extra["unresolved_cpp_includes"]
        .as_u64()
        .unwrap_or(0);
    let mut summary = if !errors.is_empty() {
        errors
            .iter()
            .take(3)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ")
    } else if cycles > 0 {
        format!("Dependency cycles: {cycles} found")
    } else {
        "No cyclic dependencies detected".into()
    };
    if unresolved_count > 0 {
        summary.push_str(&format!(
            "; C++ include graph incomplete: 0 ambiguous, {unresolved_count} unresolved"
        ));
    }
    output.summary = summary;
    if !errors.is_empty() {
        output.observation.state = TaskState::Failed;
        output.status = "ERROR".into();
        output.observation.limitations.extend(errors);
    } else if cycles > 0
        || unresolved_count > 0
        || output.targets.iter().any(|item| item.status == "WARN")
    {
        output.status = "WARN".into();
    } else {
        output.status = "PASS".into();
    }
    // Frozen per-unit targets name the unit by `_configuration_digest` (directory, argv, output).
    let identities: BTreeMap<&str, String> = context
        .units
        .iter()
        .map(|unit| {
            (
                unit.configuration.as_str(),
                super::legacy_configuration_key(unit),
            )
        })
        .collect();
    for target in &mut output.targets {
        if let Some(identity) = target
            .metrics
            .get("configuration")
            .and_then(Value::as_str)
            .and_then(|configuration| identities.get(configuration))
        {
            target
                .metrics
                .insert("configuration".into(), json!(identity));
        }
    }
    let scope_counts = output.extra["scope_counts"].clone();
    output.extra = json!({
        "cpp_include_resolution": "compiler_trace",
        "resolved_cpp_includes": output.extra["resolved_edges"].as_u64().unwrap_or(0),
        "ambiguous_cpp_includes": 0,
        "unresolved_cpp_includes": unresolved_count,
        "cpp_include_scope_counts": if scope_counts.is_object() { scope_counts } else { json!({}) },
        "cpp_configurations_checked": tool_evidence.len(),
        "cpp_include_diagnostics_truncated":
            output.extra["include_diagnostics_truncated"].as_u64().unwrap_or(0),
        "cpp_cycles": cycles,
        "tool_evidence": tool_evidence,
    });
    output
}

/// The engine error frozen records for a rejected replay (`C++ replay <code>: <src>: <msg>`),
/// from the target's `<code>: <msg>` message.
fn replay_message(replay: &InspectionTarget, source: &str) -> String {
    match replay.message.split_once(": ") {
        Some((code, message)) => format!("C++ replay {code}: {source}: {message}"),
        None => format!("C++ replay {}: {source}", replay.message),
    }
}
