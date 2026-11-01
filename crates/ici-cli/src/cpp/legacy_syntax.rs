//! Frozen `_cpp_lint._run_exact` outcomes of the legacy lint projection that are decided before
//! or instead of a unit replay: context and unit diagnostics, missing production commands, no
//! replayable unit, the selected-unit budget and rejected replay commands.
use super::*;
use ici_cpp::{ContextDiagnostic, CppError};

/// Frozen `_cpp_lint._MAX_SELECTED_UNITS`.
pub(super) const MAX_SELECTED_UNITS: usize = 2_048;

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

/// A unit's diagnostic targets (frozen `unit.diagnostics`, plus `missing-include-dir` for an
/// absent `-I`/`-isystem`/`-iquote` directory, which the native context records as a search
/// path that is not present) and the tool error when one of them is an error.
pub(super) fn unit_targets(unit: &CompilationUnit) -> (Vec<InspectionTarget>, Option<String>) {
    let mut diagnostics = unit.diagnostics.clone();
    for path in &unit.include_paths {
        if matches!(path.kind.as_str(), "include" | "system" | "quote") && !path.present {
            let mut diagnostic = ContextDiagnostic::error(
                "missing-include-dir",
                "A configured compiler include directory does not exist.",
            );
            diagnostic.level = "warning".into();
            diagnostics.push(diagnostic);
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
fn frozen_replay(error: &CppError) -> (String, String) {
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
    let (code, message) = frozen_replay(error);
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
