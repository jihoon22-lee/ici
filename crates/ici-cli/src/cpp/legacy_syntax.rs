//! Frozen `_cpp_lint._run_exact` outcomes of the legacy lint projection that are decided before
//! or instead of a unit replay: context and unit diagnostics, missing production commands, no
//! replayable unit, the selected-unit budget and rejected replay commands.
use super::*;
use ici_cpp::{ContextDiagnostic, CppError};
use ici_execution::TaskOutcome;
use std::{
    sync::atomic::{AtomicU64, Ordering},
    time::{Duration, Instant},
};

/// Frozen `_cpp_lint._MAX_SELECTED_UNITS`.
pub(super) const MAX_SELECTED_UNITS: usize = 2_048;
/// Frozen `_cpp_lint._GLOBAL_TIMEOUT_SECONDS`: one wall-clock budget for every selected unit.
pub(super) const GLOBAL_BUDGET: Duration = Duration::from_secs(600);
/// Frozen `_cpp_lint._TIMEOUT_SECONDS`: the per-unit process limit.
const UNIT_TIMEOUT: Duration = Duration::from_secs(120);
static BUDGET_OVERRIDE_MILLIS: AtomicU64 = AtomicU64::new(u64::MAX);

/// Test hook: replaces the 600 s legacy syntax budget for this process (`None` restores it).
/// The command line has no way to reach it.
#[doc(hidden)]
pub fn set_legacy_syntax_budget_for_tests(budget: Option<Duration>) {
    let millis = budget.map_or(u64::MAX, |budget| {
        u64::try_from(budget.as_millis()).unwrap_or(u64::MAX - 1)
    });
    BUDGET_OVERRIDE_MILLIS.store(millis, Ordering::SeqCst);
}

fn budget() -> Duration {
    match BUDGET_OVERRIDE_MILLIS.load(Ordering::SeqCst) {
        u64::MAX => GLOBAL_BUDGET,
        millis => Duration::from_millis(millis),
    }
}

/// The unit at which the global budget ran out: its error target and LintEngine tool error.
#[derive(Clone, Debug)]
pub(super) struct BudgetStop {
    pub index: usize,
    pub target: InspectionTarget,
    pub error: String,
}

/// Frozen `_run_exact` / `_run_fallback` loop: units run one at a time in frozen order under
/// one deadline. Before each unit (replayed or not) the remaining budget is checked; when it is
/// spent that unit is the `C++SyntaxBudgetError` target naming how many units are left and no
/// further unit runs. A replayed unit gets `min(120 s, remaining)`.
pub(super) fn run_budgeted(
    units: &[(String, Option<TaskSpec>)],
    mut run: impl FnMut(TaskSpec) -> std::result::Result<TaskOutcome, String>,
) -> std::result::Result<(Vec<TaskOutcome>, Option<BudgetStop>), String> {
    let deadline = Instant::now() + budget();
    let mut outcomes = vec![];
    for (index, (source, task)) in units.iter().enumerate() {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            let error = format!(
                "C++ syntax global time budget left {} unit(s) unexamined",
                units.len() - index
            );
            return Ok((
                outcomes,
                Some(BudgetStop {
                    index,
                    target: target(source, "C++SyntaxBudgetError", "ERROR", error.clone()),
                    error,
                }),
            ));
        }
        if let Some(task) = task {
            let mut task = task.clone();
            task.timeout_seconds = Some(remaining.min(UNIT_TIMEOUT).as_secs_f64());
            outcomes.push(run(task)?);
        }
    }
    Ok((outcomes, None))
}

/// The output that stands for the unit at which the budget ran out.
pub(super) fn budget_output(task: &str, stop: &BudgetStop) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(task, "compiler");
    output.observation.state = TaskState::Succeeded;
    output.targets.push(stop.target.clone());
    output.status = "ERROR".into();
    output
}

/// A legacy `cpp.diagnostics` run that stops before any replay: its targets in frozen order and
/// the LintEngine tool errors.
#[derive(Clone, Debug)]
pub(super) struct Terminal {
    pub targets: Vec<InspectionTarget>,
    pub errors: Vec<String>,
}

/// One selected unit in frozen order: its context diagnostic targets, then either the replay
/// task that runs it or the error target and tool error that replace the replay.
#[derive(Clone, Debug)]
pub(super) struct UnitNote {
    pub source: String,
    pub before: Vec<InspectionTarget>,
    pub replay: Result<String, (Option<InspectionTarget>, String)>,
}

fn target(path: &str, name: &str, status: &str, message: String) -> InspectionTarget {
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = name.into();
    target.status = status.into();
    target.message = message;
    target
}

/// Frozen `_context_diagnostic_target`: ERROR/WARN/PASS by level at the diagnostic source or
/// the fallback path.
pub(super) fn diagnostic_target(
    diagnostic: &ContextDiagnostic,
    fallback: &str,
) -> InspectionTarget {
    let status = match diagnostic.level.as_str() {
        "error" => "ERROR",
        "warning" => "WARN",
        _ => "PASS",
    };
    target(
        if diagnostic.source.is_empty() {
            fallback
        } else {
            &diagnostic.source
        },
        "C++SyntaxContextDiagnostic",
        status,
        legacy_context_message(&diagnostic.code, &diagnostic.message),
    )
}

/// The context-level diagnostic targets frozen emits before anything else.
pub(super) fn context_targets(context: &CompilationContext) -> Vec<InspectionTarget> {
    let fallback = context.database_path.as_deref().unwrap_or(".");
    context
        .diagnostics
        .iter()
        .map(|diagnostic| diagnostic_target(diagnostic, fallback))
        .collect()
}

/// Frozen `C++SyntaxContextMissing` WARN targets and their tool error.
fn missing(context: &CompilationContext) -> (Vec<InspectionTarget>, Option<String>) {
    let targets: Vec<_> = context
        .uncovered_sources
        .iter()
        .map(|path| {
            target(
                path,
                "C++SyntaxContextMissing",
                "WARN",
                "No exact translation-unit command covers this production source.".into(),
            )
        })
        .collect();
    let error = (!targets.is_empty()).then(|| {
        format!(
            "Compilation context is missing {} production source command(s)",
            targets.len()
        )
    });
    (targets, error)
}

/// Frozen `_run_exact` stops: error context diagnostics, no replayable production unit, or more
/// selected units than the budget. `None` when the selected units are replayed.
pub(super) fn terminal(context: &CompilationContext, selected: usize) -> Option<Terminal> {
    let mut targets = context_targets(context);
    if context
        .diagnostics
        .iter()
        .any(|diagnostic| diagnostic.level == "error")
    {
        return Some(Terminal {
            targets,
            errors: vec!["Compilation context contains error diagnostics".into()],
        });
    }
    let database = context.database_path.as_deref().unwrap_or(".");
    let (missing, missing_error) = missing(context);
    let stop = if selected == 0 {
        (
            "C++SyntaxContextError",
            "Compilation context contains no replayable production units",
        )
    } else if selected > MAX_SELECTED_UNITS {
        (
            "C++SyntaxBudgetError",
            "C++ syntax translation-unit count exceeds the bounded limit",
        )
    } else {
        return None;
    };
    targets.extend(missing);
    targets.push(target(database, stop.0, "ERROR", stop.1.into()));
    Some(Terminal {
        targets,
        errors: missing_error
            .into_iter()
            .chain([stop.1.to_owned()])
            .collect(),
    })
}

/// A unit's diagnostic targets (frozen `unit.diagnostics`) and the tool error when one of them
/// is an error.
pub(super) fn unit_targets(unit: &CompilationUnit) -> (Vec<InspectionTarget>, Option<String>) {
    let mut diagnostics = unit.diagnostics.clone();
    // Frozen `compile_db` records a gone working directory and a stale source as warnings and
    // still attempts the replay, which `build_replay_command` then rejects.
    for diagnostic in &mut diagnostics {
        if matches!(
            diagnostic.code.as_str(),
            "missing-directory" | "stale-source"
        ) {
            diagnostic.level = "warning".into();
        }
    }
    let error = diagnostics
        .iter()
        .any(|diagnostic| diagnostic.level == "error")
        .then(|| format!("Compilation unit has error diagnostics: {}", unit.source));
    (
        diagnostics
            .iter()
            .map(|diagnostic| diagnostic_target(diagnostic, &unit.source))
            .collect(),
        error,
    )
}

/// Frozen `ReplayCommandError` code and message for a native replay rejection with the same
/// meaning; other codes keep the native text.
fn frozen_replay(unit: &CompilationUnit, error: &CppError) -> (String, String) {
    let token = |prefix: &str, suffix: &str| {
        error
            .message
            .strip_prefix(prefix)
            .and_then(|rest| rest.strip_suffix(suffix))
            .map(str::to_owned)
    };
    let (code, message) = match error.code.as_str() {
        "extra-source-operand" => (
            "extra-compiler-operand",
            "The translation unit command contains an extra or mismatched input operand.".into(),
        ),
        "missing-source-operand" => (
            "invalid-source-operands",
            "The translation unit command must identify its canonical source exactly once.".into(),
        ),
        "unsupported-compiler-driver" => (
            "unsupported-compiler-driver",
            "The translation unit does not use a direct GCC/Clang compiler driver.".into(),
        ),
        "project-compiler-rejected" => (
            "project-compiler-rejected",
            "Project-contained compiler programs are not executed during analysis.".into(),
        ),
        "unsafe-tooling-warning-policy" => (
            "unsafe-tooling-warning-policy",
            "A warning-as-error option cannot be projected to a safe diagnostic flag.".into(),
        ),
        // The native replay refuses a unit with error diagnostics; frozen (which records a gone
        // directory or stale source as a warning) rejects the same replay as stale.
        "invalid-compilation-unit"
            if unit.diagnostics.iter().all(|diagnostic| {
                diagnostic.level != "error"
                    || matches!(
                        diagnostic.code.as_str(),
                        "missing-directory" | "stale-source"
                    )
            }) =>
        {
            (
                "stale-translation-unit",
                "The translation unit working directory or source no longer exists.".into(),
            )
        }
        "stale-compilation-unit" => (
            "stale-translation-unit",
            "The translation unit working directory or source no longer exists.".into(),
        ),
        "unsafe-compiler-option" => {
            match token("compiler option ", " is not allowed during replay") {
                Some(option) => (
                    "unsafe-compiler-option",
                    format!("The compiler option {option} is not allowed during analysis replay."),
                ),
                None => return (error.code.clone(), error.message.clone()),
            }
        }
        "unsupported-compiler-option" => {
            match token("unknown compiler option ", "; no unsafe guess") {
                Some(option) => (
                    "unsafe-compiler-option",
                    format!("The compiler option {option} is outside the replay allowlist."),
                ),
                None => return (error.code.clone(), error.message.clone()),
            }
        }
        "missing-option-value" => match token("", " has no safe value") {
            Some(option) => (
                "missing-option-value",
                format!("The compiler option {option} has no safe value."),
            ),
            None => return (error.code.clone(), error.message.clone()),
        },
        _ => return (error.code.clone(), error.message.clone()),
    };
    (code.into(), message)
}

/// Frozen `C++SyntaxReplayError` target and `C++ replay <code>: <source>: <message>` tool error.
pub(super) fn replay_error(unit: &CompilationUnit, error: &CppError) -> (InspectionTarget, String) {
    let (code, message) = frozen_replay(unit, error);
    (
        target(
            &unit.source,
            "C++SyntaxReplayError",
            "ERROR",
            format!("{code}: {message}"),
        ),
        format!("C++ replay {code}: {}: {message}", unit.source),
    )
}

/// The output of a terminal legacy syntax run: no process, the frozen targets and tool errors.
pub(super) fn terminal_output(request: &AnalysisRequest, terminal: &Terminal) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&request.task_id, "compiler");
    output.targets = terminal.targets.clone();
    output.observation.state = TaskState::Succeeded;
    output.extra["legacy_replayed_sources"] = json!([]);
    output.extra["concrete_tasks"] = json!([]);
    legacy_record_syntax_errors(&mut output, terminal.errors.clone());
    output
}

/// The synthetic output of a unit that is not replayed: its diagnostic targets and error target.
pub(super) fn skipped_output(task: &str, note: &UnitNote) -> Option<AnalysisOutput> {
    let (error_target, _) = note.replay.as_ref().err()?;
    let mut output = AnalysisOutput::new(task, "compiler");
    output.observation.state = TaskState::Succeeded;
    output.targets = note.before.clone();
    output.targets.extend(error_target.clone());
    output.status = "ERROR".into();
    Some(output)
}
