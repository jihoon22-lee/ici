//! Historical single-engine target shapes for `legacy_projection` requests.
//!
//! The frozen v0.13.0 engines (`engines/{security,resource,dup,dead,cognitive,exception}.py`)
//! name their intake, syntax and scope failures with engine-specific targets and summaries, and
//! read sources with strict UTF-8 (no PEP 263 cookie). Targets are legacy-only data, so a legacy
//! request reshapes them here from the request bytes and the native failures; a modern request
//! never reaches this module and keeps the generic `PythonAnalysis:<code>` targets.
use crate::source::SourceDocument;
use ici_domain::SourceInput;
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, TaskState};
use ruff_python_ast::PythonVersion;
use ruff_python_ast::token::TokenKind;
use ruff_python_parser::{Mode, ParseOptions, parse_unchecked};
use ruff_text_size::Ranged;

pub(crate) fn requested(request: &AnalysisRequest) -> bool {
    request
        .options
        .get("legacy_projection")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false)
}

/// One strict-UTF-8 intake failure as the frozen reader saw it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Fault {
    /// `UnicodeDecodeError` byte range `[start, end)` and CPython reason.
    Utf8 {
        start: usize,
        end: usize,
        byte: u8,
        reason: &'static str,
    },
    Nul,
}

pub(crate) fn strict_fault(bytes: &[u8]) -> Option<Fault> {
    match std::str::from_utf8(bytes) {
        Ok(text) if text.contains('\0') => Some(Fault::Nul),
        Ok(_) => None,
        Err(error) => {
            let start = error.valid_up_to();
            let byte = bytes[start];
            Some(match error.error_len() {
                None => Fault::Utf8 {
                    start,
                    end: bytes.len(),
                    byte,
                    reason: "unexpected end of data",
                },
                Some(length) => Fault::Utf8 {
                    start,
                    end: start + length,
                    byte,
                    reason: if length == 1 && !(0xC2..=0xF4).contains(&byte) {
                        "invalid start byte"
                    } else {
                        "invalid continuation byte"
                    },
                },
            })
        }
    }
}

/// CPython `str(UnicodeDecodeError)` for the strict `utf-8` codec.
pub(crate) fn decode_error_text(fault: &Fault) -> Option<String> {
    let Fault::Utf8 {
        start,
        end,
        byte,
        reason,
    } = fault
    else {
        return None;
    };
    Some(if end - start == 1 {
        format!("'utf-8' codec can't decode byte 0x{byte:02x} in position {start}: {reason}")
    } else {
        format!(
            "'utf-8' codec can't decode bytes in position {start}-{}: {reason}",
            end - 1
        )
    })
}

fn intake_message(fault: &Fault) -> (&'static str, &'static str) {
    match fault {
        Fault::Utf8 { .. } => ("invalid-utf8", "Analysis source is not valid UTF-8"),
        Fault::Nul => ("invalid-text", "Analysis source contains a NUL character"),
    }
}

/// The native parser's first syntax message (fixed per pinned ruff revision; frozen embedded the
/// running CPython's `SyntaxError.msg`, which differs between interpreters) and, when the parse
/// failed at end of input with a bracket still open, CPython's location for that error: the
/// tokenizer reports the innermost unclosed bracket (`Parser/lexer/lexer.c`, `tok->parenstack`).
fn syntax_detail(input: &SourceInput) -> (String, Option<(u32, u32)>) {
    let Ok(source) = SourceDocument::decode(input) else {
        return ("invalid syntax".into(), None);
    };
    let parsed = parse_unchecked(
        &source.text,
        ParseOptions::from(Mode::Module).with_target_version(PythonVersion::PY314),
    );
    let Some(error) = parsed.errors().first() else {
        return ("invalid syntax".into(), None);
    };
    let mut open = Vec::new();
    let mut last = 0;
    for token in parsed.tokens() {
        match token.kind() {
            TokenKind::Lpar | TokenKind::Lsqb | TokenKind::Lbrace => open.push(token.start()),
            TokenKind::Rpar | TokenKind::Rsqb | TokenKind::Rbrace => {
                open.pop();
            }
            _ => {}
        }
        if !matches!(
            token.kind(),
            TokenKind::Newline
                | TokenKind::NonLogicalNewline
                | TokenKind::Indent
                | TokenKind::Dedent
                | TokenKind::EndOfFile
                | TokenKind::Comment
        ) {
            last = token.end().to_usize();
        }
    }
    let at_end = error.location.start().to_usize() >= last;
    let location = open
        .last()
        .filter(|_| at_end)
        .map(|offset| source.location(offset.to_usize()));
    (error.error.to_string(), location)
}

/// Frozen `LintEngine._check_python_syntax` for one source: the `SyntaxError` line and message
/// (`SyntaxError: <pinned parser wording>`), or None when it parses. Frozen read strict UTF-8 and
/// did not survive undecodable or NUL text, so those inputs yield no syntax target here.
pub fn lint_syntax_error(input: &SourceInput) -> Option<(u32, String)> {
    if strict_fault(&input.bytes).is_some() {
        return None;
    }
    let source = SourceDocument::decode(input).ok()?;
    let parsed = parse_unchecked(
        &source.text,
        ParseOptions::from(Mode::Module).with_target_version(PythonVersion::PY314),
    );
    let first = parsed.errors().first()?;
    let (message, location) = syntax_detail(input);
    let line = location.map_or_else(
        || source.location(first.location.start().to_usize()).0,
        |(line, _)| line,
    );
    Some((line, format!("SyntaxError: {message}")))
}

fn target(
    path: &str,
    line: u32,
    column: Option<u32>,
    name: &str,
    message: String,
) -> InspectionTarget {
    let mut target = InspectionTarget::at(path, line);
    target.start_column = column;
    target.target_name = name.into();
    target.status = "ERROR".into();
    target.message = message;
    target
}

fn fail(output: &mut AnalysisOutput, summary: String) {
    output.status = "ERROR".into();
    output.summary = summary;
    if output.observation.state == TaskState::Succeeded {
        output.observation.state = TaskState::Failed;
        output
            .observation
            .limitations
            .push("legacy strict source intake did not complete for every selected input".into());
    }
}

/// Replace every target and finding of `path` by `replacement`, kept in frozen per-file order.
fn replace_file(
    output: &mut AnalysisOutput,
    path: &str,
    replacement: InspectionTarget,
    scan: &[&str],
) {
    output.targets.retain(|target| target.file_path != path);
    output
        .observation
        .findings
        .retain(|finding| finding.primary_location.path != path);
    let index = if scan.is_empty() {
        output
            .targets
            .iter()
            .position(|target| target.file_path.as_str() > path)
            .unwrap_or(output.targets.len())
    } else {
        // Resource appends its per-file scan targets first and every finding after the loop.
        output
            .targets
            .iter()
            .position(|target| {
                !scan.contains(&target.target_name.as_str()) || target.file_path.as_str() > path
            })
            .unwrap_or(output.targets.len())
    };
    output.targets.insert(index, replacement);
}

fn abort(output: &mut AnalysisOutput, replacement: InspectionTarget, summary: String) {
    output.targets = vec![replacement];
    output.observation.findings.clear();
    fail(output, summary);
}

/// Owned Python inputs in the frozen reader's sorted order.
fn owned(request: &AnalysisRequest, excluding: bool) -> Vec<&SourceInput> {
    let mut inputs: Vec<_> = request
        .inputs
        .iter()
        .filter(|input| input.path.ends_with(".py") || input.path.ends_with(".pyi"))
        .filter(|input| {
            !excluding
                || crate::source::exclusion_reasons(&input.path)
                    .into_iter()
                    .all(|reason| {
                        request
                            .options
                            .get(format!("include_{reason}"))
                            .and_then(serde_json::Value::as_bool)
                            .unwrap_or(false)
                    })
        })
        .collect();
    inputs.sort_by(|left, right| left.path.cmp(&right.path));
    inputs
}

pub(crate) fn reshape(kind: &str, request: &AnalysisRequest, output: &mut AnalysisOutput) {
    if !matches!(
        kind,
        "security" | "resource" | "dup" | "dead" | "cognitive" | "exception"
    ) || output.observation.state == TaskState::Cancelled
    {
        return;
    }
    // Frozen engines never listed excluded inputs as targets.
    output
        .targets
        .retain(|target| target.target_name != "ExcludedAnalysisSource");
    let syntax: Vec<(String, u32, Option<u32>)> = {
        let mut failed: Vec<_> = output
            .targets
            .iter()
            .filter(|target| target.target_name == "PythonAnalysis:invalid-syntax")
            .map(|target| {
                (
                    target.file_path.clone(),
                    target.start_line,
                    target.start_column,
                )
            })
            .collect();
        for (path, line, column) in &mut failed {
            if let Some((cline, ccolumn)) = request
                .inputs
                .iter()
                .find(|input| input.path == *path)
                .and_then(|input| syntax_detail(input).1)
            {
                *line = cline;
                *column = Some(ccolumn);
            }
        }
        failed.sort();
        failed
    };
    let input = |path: &str| request.inputs.iter().find(|input| input.path == path);
    match kind {
        "security" | "resource" | "dup" | "dead" => {
            let inputs = owned(request, true);
            if let Some((input, fault)) = inputs
                .iter()
                .find_map(|input| strict_fault(&input.bytes).map(|fault| (input, fault)))
            {
                let (code, message) = intake_message(&fault);
                let (name, text, summary) = match kind {
                    "security" => (
                        "Security:SourceInput",
                        message.to_owned(),
                        format!("Security analysis incomplete: {message}"),
                    ),
                    "resource" => (
                        "Resource:SourceInput",
                        message.to_owned(),
                        format!("Resource analysis incomplete: {message}"),
                    ),
                    _ => (
                        "SourceInputError",
                        format!("{code}: {message}"),
                        message.to_owned(),
                    ),
                };
                abort(output, target(&input.path, 1, None, name, text), summary);
                return;
            }
            if output
                .targets
                .iter()
                .any(|target| target.target_name == "ExcludedAnalysisScope")
            {
                if kind == "dead" {
                    output.targets = vec![{
                        let mut skip = InspectionTarget::at(".", 1);
                        skip.target_name = "DeadCode".into();
                        skip.status = "SKIP".into();
                        skip.message = "All selected sources were excluded by the generated/vendor policy; dead-code analysis was not run".into();
                        skip
                    }];
                    output.summary = "Dead-code analysis skipped: no owned source files".into();
                    output
                        .observation
                        .measurements
                        .retain(|measurement| measurement.name == "applicability");
                }
                return;
            }
            if syntax.is_empty() {
                return;
            }
            match kind {
                "dup" => {
                    let path = &syntax[0].0;
                    abort(
                        output,
                        target(path, 1, None, "SourceTokenizationError", "Source lexical normalization failed: Python syntax could not be parsed for duplicate-region boundaries".into()),
                        format!("Duplicate analysis could not normalize {path}"),
                    );
                }
                "security" | "resource" => {
                    let (name, message, scan): (_, _, &[&str]) = if kind == "security" {
                        (
                            "Security:SyntaxUnavailable",
                            "Python syntax is invalid; AST security analysis was not run",
                            &[],
                        )
                    } else {
                        (
                            "Resource:AnalysisUnavailable",
                            "Python syntax is invalid; resource-flow analysis was not run",
                            &["Resource:ASTFlow", "Resource:AnalysisUnavailable"],
                        )
                    };
                    for (path, line, column) in &syntax {
                        replace_file(
                            output,
                            path,
                            target(path, *line, *column, name, message.into()),
                            scan,
                        );
                    }
                    let (path, line, _) = &syntax[0];
                    let detail = if kind == "security" {
                        "syntax prevents security analysis"
                    } else {
                        message
                    };
                    let prefix = if kind == "security" {
                        "Security"
                    } else {
                        "Resource"
                    };
                    fail(
                        output,
                        format!("{prefix} analysis incomplete: {path}:{line}: {detail}"),
                    );
                }
                _ => {
                    let mut first = None;
                    for (path, line, _) in &syntax {
                        let message = format!(
                            "SyntaxError: {}",
                            input(path)
                                .map(|input| syntax_detail(input).0)
                                .unwrap_or_default()
                        );
                        first.get_or_insert_with(|| message.clone());
                        replace_file(
                            output,
                            path,
                            target(path, *line, None, "SyntaxError", message),
                            &[],
                        );
                    }
                    fail(output, first.unwrap_or_default());
                }
            }
        }
        _ => {
            // Cognitive and exception read every source themselves (no generated/vendor policy).
            let mut errors = Vec::new();
            for input in owned(request, false) {
                let replacement = match strict_fault(&input.bytes) {
                    Some(fault @ Fault::Utf8 { .. }) => Some(if kind == "cognitive" {
                        target(
                            &input.path,
                            1,
                            None,
                            "PythonCognitiveAnalysisError",
                            "Python cognitive analysis failed: UnicodeDecodeError".into(),
                        )
                    } else {
                        let text = decode_error_text(&fault).unwrap_or_default();
                        target(
                            &input.path,
                            1,
                            None,
                            "ReadError",
                            format!("Could not read Python source: {text}"),
                        )
                    }),
                    // Frozen CPython 3.10 raised an uncaught ValueError for NUL: no frozen shape.
                    Some(Fault::Nul) => None,
                    None => syntax.iter().find(|(path, ..)| *path == input.path).map(
                        |(path, line, _)| {
                            if kind == "cognitive" {
                                target(
                                    path,
                                    *line,
                                    None,
                                    "PythonCognitiveAnalysisError",
                                    "Python cognitive analysis failed: SyntaxError".into(),
                                )
                            } else {
                                target(
                                    path,
                                    *line,
                                    None,
                                    "SyntaxError",
                                    format!("SyntaxError: {}", syntax_detail(input).0),
                                )
                            }
                        },
                    ),
                };
                if let Some(replacement) = replacement {
                    errors.push(replacement.message.clone());
                    replace_file(output, &input.path, replacement, &[]);
                }
            }
            if let Some(first) = errors.into_iter().next() {
                let summary = if kind == "cognitive" {
                    "Cognitive complexity analysis did not complete".into()
                } else {
                    first
                };
                fail(output, summary);
            }
        }
    }
}
