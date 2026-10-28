//! Python token normalization and scope barriers feeding the shared matcher.
use crate::{
    cancelled,
    duplicates::{self, DuplicateFileData},
    failure,
    parser::ParsedFile,
    source::SourceError,
};
use ici_domain::{AnalysisOutput, AnalysisRequest, TaskState};
use ruff_python_ast::{
    Expr, Stmt,
    token::{Token, TokenKind},
    visitor::{self, Visitor},
};
use ruff_text_size::{Ranged, TextSize};
use sha2::{Digest, Sha256};
use std::{
    cmp::Reverse,
    collections::{BTreeMap, BTreeSet, BinaryHeap},
    sync::{Arc, atomic::AtomicBool},
};

const BUILTINS: &str = "abs aiter all anext any ascii bin bool breakpoint bytearray bytes callable chr classmethod compile complex delattr dict dir divmod enumerate eval exec filter float format frozenset getattr globals hasattr hash hex id input int isinstance issubclass iter len list locals map max memoryview min next object oct open ord pow print property range repr reversed round set setattr slice sorted staticmethod str sum super tuple type vars zip __import__";
struct Context<'a> {
    file: &'a ParsedFile,
    soft: BTreeSet<TextSize>,
    imported: BTreeSet<String>,
    intervals: Vec<(u32, u32, u32)>,
    barriers: Vec<(u32, u32)>,
    cancellation: Option<&'a AtomicBool>,
}
impl<'a> Visitor<'a> for Context<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        match statement {
            Stmt::FunctionDef(function) => {
                let span = self
                    .file
                    .source
                    .span(crate::ast_util::function_range(self.file, function));
                let start = function
                    .decorator_list
                    .iter()
                    .map(|decorator| {
                        self.file
                            .source
                            .location(decorator.expression.range().start().to_usize())
                            .0
                    })
                    .fold(span.start_line, u32::min);
                self.intervals.push((
                    start,
                    span.end_line.unwrap_or(start),
                    self.intervals.len() as u32 + 1,
                ));
            }
            Stmt::ClassDef(class) => {
                let span = self
                    .file
                    .source
                    .span(crate::ast_util::statement_range(self.file, statement));
                let start = class
                    .decorator_list
                    .iter()
                    .map(|decorator| {
                        self.file
                            .source
                            .location(decorator.expression.range().start().to_usize())
                            .0
                    })
                    .fold(span.start_line, u32::min);
                self.intervals.push((
                    start,
                    span.end_line.unwrap_or(start),
                    self.intervals.len() as u32 + 1,
                ));
            }
            Stmt::Match(matching) => {
                self.soft.insert(matching.range().start());
                for branch in &matching.cases {
                    let tokens = self.file.parsed.tokens();
                    let before = tokens.partition_point(|token| {
                        token.range().start() < branch.pattern.range().start()
                    });
                    for token in tokens[..before].iter().rev() {
                        if token.kind() == TokenKind::Newline {
                            break;
                        }
                        if token.kind() == TokenKind::Case {
                            self.soft.insert(token.range().start());
                            break;
                        }
                    }
                }
            }
            Stmt::Import(import) => {
                for alias in &import.names {
                    self.imported.insert(alias.asname.as_ref().map_or_else(
                        || alias.name.split('.').next().unwrap_or("").into(),
                        ToString::to_string,
                    ));
                }
            }
            Stmt::ImportFrom(import) => {
                for alias in &import.names {
                    if alias.name.as_str() != "*" {
                        self.imported
                            .insert(alias.asname.as_ref().unwrap_or(&alias.name).to_string());
                    }
                }
            }
            _ => {}
        }
        if matches!(statement, Stmt::Import(_) | Stmt::ImportFrom(_)) {
            let span = self.file.source.span(statement.range());
            self.barriers
                .push((span.start_line, span.end_line.unwrap_or(span.start_line)));
        }
        visitor::walk_stmt(self, statement);
    }
    fn visit_expr(&mut self, expression: &'a Expr) {
        if !crate::cancellation_requested(self.cancellation) {
            visitor::walk_expr(self, expression);
        }
    }
}
fn trivia(kind: TokenKind) -> bool {
    matches!(
        kind,
        TokenKind::Indent | TokenKind::Dedent | TokenKind::NonLogicalNewline | TokenKind::Comment
    )
}
fn text<'a>(file: &'a ParsedFile, token: &Token) -> &'a str {
    &file.source.text[token.range().start().to_usize()..token.range().end().to_usize()]
}
fn neighbor<'a>(file: &'a ParsedFile, tokens: &[Token], index: usize, step: isize) -> &'a str {
    let mut cursor = index as isize + step;
    while cursor >= 0 && cursor < (tokens.len() as isize) {
        let token = &tokens[cursor as usize];
        if matches!(token.kind(), TokenKind::Newline | TokenKind::EndOfFile) {
            return "";
        }
        if !trivia(token.kind()) {
            return text(file, token);
        }
        cursor += step;
    }
    ""
}
fn previous(tokens: &[Token], index: usize) -> Option<usize> {
    (0..index)
        .rev()
        .find(|index| !trivia(tokens[*index].kind()))
}
fn qualified_root<'a>(file: &'a ParsedFile, tokens: &[Token], index: usize) -> &'a str {
    let mut cursor = index;
    while let Some(dot) = previous(tokens, cursor) {
        let Some(root) = previous(tokens, dot) else {
            break;
        };
        if tokens[dot].kind() != TokenKind::Dot
            || !(tokens[root].kind() == TokenKind::Name || tokens[root].kind().is_keyword())
        {
            break;
        }
        cursor = root;
    }
    text(file, &tokens[cursor])
}
fn imports(tokens: &[Token]) -> BTreeSet<usize> {
    let mut excluded = BTreeSet::new();
    let mut statement = vec![];
    let mut depth = 0usize;
    let finish = |statement: &mut Vec<usize>, excluded: &mut BTreeSet<usize>| {
        if statement
            .iter()
            .map(|index| tokens[*index].kind())
            .find(|kind| !trivia(*kind) && *kind != TokenKind::Newline)
            .is_some_and(|kind| matches!(kind, TokenKind::Import | TokenKind::From))
        {
            excluded.extend(statement.iter().copied());
        }
        statement.clear();
    };
    for (index, token) in tokens.iter().enumerate() {
        let kind = token.kind();
        if kind == TokenKind::EndOfFile {
            finish(&mut statement, &mut excluded);
            break;
        }
        if kind == TokenKind::Semi && depth == 0 {
            finish(&mut statement, &mut excluded);
            continue;
        }
        statement.push(index);
        match kind {
            TokenKind::Lpar | TokenKind::Lsqb | TokenKind::Lbrace => depth += 1,
            TokenKind::Rpar | TokenKind::Rsqb | TokenKind::Rbrace => {
                depth = depth.saturating_sub(1)
            }
            _ => {}
        }
        if kind == TokenKind::Newline && depth == 0 {
            finish(&mut statement, &mut excluded);
        }
    }
    finish(&mut statement, &mut excluded);
    excluded
}
fn string_kind(value: &str) -> String {
    let prefix = value
        .chars()
        .take_while(|character| "bfruBFRU".contains(*character))
        .collect::<String>()
        .to_ascii_lowercase();
    let kind = if prefix.contains('f') {
        "FSTRING_LIT"
    } else if prefix.contains('b') {
        "BYTES_LIT"
    } else {
        "STRING_LIT"
    };
    format!("{}{kind}", if prefix.contains('r') { "RAW_" } else { "" })
}
pub(crate) fn normalize(
    file: &ParsedFile,
    cancellation: Option<&AtomicBool>,
) -> Result<DuplicateFileData, SourceError> {
    let tokens = file.parsed.tokens();
    if tokens.len() > 500_000 {
        return Err(SourceError::new(
            "duplicate-token-limit",
            "Python duplicate tokenization exceeds the 500000-token bound",
        ));
    }
    let mut context = Context {
        file,
        soft: BTreeSet::new(),
        imported: BTreeSet::new(),
        intervals: vec![],
        barriers: vec![],
        cancellation,
    };
    context.visit_body(file.body());
    let excluded = imports(tokens);
    let mut lines: BTreeMap<u32, Vec<String>> = BTreeMap::new();
    let mut fstrings = 0usize;
    for (index, token) in tokens.iter().enumerate() {
        if crate::cancellation_requested(cancellation) {
            return Err(SourceError::new("cancelled", "analysis cancelled"));
        }
        let kind = token.kind();
        let value = text(file, token);
        let line = file.source.location(token.range().start().to_usize()).0;
        if excluded.contains(&index)
            || matches!(
                kind,
                TokenKind::EndOfFile
                    | TokenKind::Newline
                    | TokenKind::NonLogicalNewline
                    | TokenKind::Comment
            )
        {
            continue;
        }
        if kind == TokenKind::FStringStart {
            if fstrings == 0 {
                lines.entry(line).or_default().push(string_kind(value));
            }
            fstrings += 1;
            continue;
        }
        if fstrings > 0 {
            if kind == TokenKind::FStringEnd {
                fstrings -= 1;
            }
            continue;
        }
        let canonical = match kind {
            TokenKind::Name
            | TokenKind::Case
            | TokenKind::Match
            | TokenKind::Type
            | TokenKind::Lazy => {
                if context.soft.contains(&token.range().start()) {
                    value.to_owned()
                } else {
                    let before = neighbor(file, tokens, index, -1);
                    let after = neighbor(file, tokens, index, 1);
                    let anchor = context
                        .imported
                        .contains(qualified_root(file, tokens, index))
                        && (before == "." || matches!(after, "." | "("));
                    if anchor
                        || (after == "("
                            && BUILTINS.split_whitespace().any(|builtin| builtin == value))
                    {
                        if value.chars().count() <= 128 {
                            format!("NAME({value})")
                        } else {
                            format!("NAME_SHA256({:x})", Sha256::digest(value.as_bytes()))
                        }
                    } else {
                        "ID".into()
                    }
                }
            }
            TokenKind::Int | TokenKind::Float | TokenKind::Complex => {
                let lowered = value.to_ascii_lowercase().replace('_', "");
                if lowered.ends_with('j') {
                    "COMPLEX_LIT"
                } else if lowered.contains('.') || lowered.contains('e') {
                    "FLOAT_LIT"
                } else {
                    "INT_LIT"
                }
                .into()
            }
            TokenKind::String => string_kind(value),
            TokenKind::Indent => "INDENT".into(),
            TokenKind::Dedent => "DEDENT".into(),
            _ if kind.is_non_soft_keyword() || kind.is_operator() => value.to_owned(),
            TokenKind::TStringStart => "TOKEN_TYPE(62)".into(),
            TokenKind::TStringMiddle => "TOKEN_TYPE(63)".into(),
            TokenKind::TStringEnd => "TOKEN_TYPE(64)".into(),
            TokenKind::Exclamation => "!".into(),
            _ => {
                return Err(SourceError::new(
                    "unsupported-duplicate-token",
                    format!("Native Python duplicate token is not represented: {kind:?}"),
                ));
            }
        };
        lines.entry(line).or_default().push(canonical);
    }
    let indexed: Vec<_> = lines
        .into_iter()
        .filter(|(_, tokens)| tokens.iter().any(|token| token != "DEDENT"))
        .map(|(line, tokens)| (line, tokens.join("\x1f")))
        .collect();
    context.intervals.sort_unstable();
    context.barriers.sort_unstable();
    let mut barriers: Vec<(u32, u32)> = vec![];
    for (start, end) in context.barriers {
        if let Some(last) = barriers.last_mut()
            && start <= last.1.saturating_add(1)
        {
            last.1 = last.1.max(end);
            continue;
        }
        barriers.push((start, end));
    }
    let mut intervals = context.intervals.iter().peekable();
    let mut preceding = 0;
    let barriers: Vec<_> = barriers
        .into_iter()
        .map(|(start, end)| {
            let previous = preceding;
            preceding += end - start + 1;
            (start, end, previous)
        })
        .collect();
    let mut active = BinaryHeap::new();
    let mut regions = vec![];
    for (line, _) in &indexed {
        while let Some((start, end, scope)) = intervals.peek().copied() {
            if start > line {
                break;
            }
            active.push(Reverse((end - start, *end, *scope)));
            intervals.next();
        }
        while active.peek().is_some_and(|Reverse((_, end, _))| end < line) {
            active.pop();
        }
        let scope = active.peek().map_or(0, |Reverse((_, _, scope))| *scope);
        let before = barriers.partition_point(|(start, _, _)| start <= line);
        let segment = before.checked_sub(1).map_or(0, |index| {
            let (start, end, previous) = barriers[index];
            previous + end.min(*line) - start + 1
        });
        regions.push((scope, segment));
    }
    Ok(DuplicateFileData {
        file_path: file.source.path.clone(),
        language: "python".into(),
        raw_text: file.source.report_text().to_owned(),
        indexed,
        regions,
    })
}
pub(crate) fn analyze(
    request: &AnalysisRequest,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&AtomicBool>,
) {
    if output.observation.state == TaskState::Failed {
        return;
    }
    let mut normalized = vec![];
    for file in files {
        if cancelled(output, cancellation) {
            return;
        }
        match normalize(file, cancellation) {
            Ok(file) => normalized.push(file),
            Err(error) => {
                if !cancelled(output, cancellation) {
                    failure(output, &file.source.path, &error);
                }
                return;
            }
        }
    }
    let previous = std::mem::take(&mut output.targets);
    let limitations = std::mem::take(&mut output.observation.limitations);
    let policy = request
        .options
        .get("python_semantic")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("auto");
    if !matches!(policy, "auto" | "off" | "required") {
        failure(
            output,
            ".",
            &SourceError::new(
                "invalid-semantic-policy",
                "python_semantic must be auto, off or required",
            ),
        );
        return;
    }
    let semantic = if policy == "off" {
        None
    } else {
        Some(crate::semantic::analyze(
            files,
            crate::semantic::Limits::default(),
            cancellation,
        ))
    };
    if cancelled(output, cancellation) {
        return;
    }
    if policy == "required"
        && semantic
            .as_ref()
            .is_some_and(|outcome| !outcome.exclusions.is_empty())
    {
        output.observation.state = TaskState::Failed;
        output.status = "ERROR".into();
        for exclusion in &semantic.as_ref().unwrap().exclusions {
            let mut target = ici_domain::InspectionTarget::at(
                if exclusion.file_path == "<sources>" {
                    "."
                } else {
                    &exclusion.file_path
                },
                exclusion.start_line.unwrap_or(1),
            );
            target.end_line = exclusion.end_line;
            target.target_name = "PythonSemanticShapeError".into();
            target.status = "ERROR".into();
            target.message = format!("{}: {}", exclusion.reason, exclusion.message);
            output.targets.push(target);
        }
        crate::semantic::record_metadata(
            request,
            semantic.as_ref(),
            crate::semantic::Stats::default(),
            &mut output.extra,
        );
        output.summary = "Required Python AST-shape duplicate analysis was incomplete".into();
        return;
    }
    *output = duplicates::analyze_inner(request, &normalized, cancellation);
    if output.observation.state == TaskState::Succeeded
        && let Some(semantic) = semantic
    {
        crate::semantic::merge(request, &normalized, &semantic, output);
    }
    if policy == "off" {
        crate::semantic::record_metadata(
            request,
            None,
            crate::semantic::Stats::default(),
            &mut output.extra,
        );
    }
    output.targets.extend(previous);
    output.observation.limitations.extend(limitations);
}
