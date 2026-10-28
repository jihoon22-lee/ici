//! Bounded concrete C++ syntax over snapshotted bytes. This never preprocesses or runs a compiler.
use crate::CppError;
use ici_domain::{InspectionTarget, SourceInput};
use std::{
    ops::ControlFlow,
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
use tree_sitter::{Node, ParseOptions, Parser, Tree};

pub(crate) const PARSER_ID: &str = "tree-sitter-0.27.1/cpp-0.23.4/source-v1";
pub(crate) struct ParsedSource {
    pub path: String,
    pub text: String,
    pub tree: Tree,
    pub weight: usize,
    line_starts: Vec<usize>,
}
impl ParsedSource {
    pub fn parse(input: &SourceInput, cancellation: Option<&AtomicBool>) -> Result<Self, CppError> {
        if input.bytes.len() > 16 * 1024 * 1024 {
            return Err(CppError::new(
                "source-too-large",
                "C++ source exceeds 16 MiB",
            ));
        }
        let text = std::str::from_utf8(&input.bytes)
            .map_err(|_| CppError::new("source-encoding", "C++ source is not UTF-8"))?
            .to_owned();
        if text.contains('\0') {
            return Err(CppError::new(
                "source-null",
                "C++ source contains a null byte",
            ));
        }
        // C++ translation phase 1 recognizes CR-only source line endings too. Replace each
        // standalone CR with LF for the grammar while keeping every byte offset unchanged.
        let mut syntax_bytes = text.as_bytes().to_vec();
        for index in 0..syntax_bytes.len() {
            if syntax_bytes[index] == b'\r' && syntax_bytes.get(index + 1) != Some(&b'\n') {
                syntax_bytes[index] = b'\n';
            }
        }
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_cpp::LANGUAGE.into())
            .expect("pinned C++ grammar");
        let started = Instant::now();
        let mut progress = |_: &tree_sitter::ParseState| {
            if cancellation.is_some_and(|flag| flag.load(Ordering::Relaxed))
                || started.elapsed() > Duration::from_secs(5)
            {
                ControlFlow::Break(())
            } else {
                ControlFlow::Continue(())
            }
        };
        let tree = parser
            .parse_with_options(
                &mut |offset, _| &syntax_bytes[offset..],
                None,
                Some(ParseOptions::new().progress_callback(&mut progress)),
            )
            .ok_or_else(|| {
                CppError::new(
                    "parse-interrupted",
                    "C++ concrete syntax parse was cancelled or exceeded 5 seconds",
                )
            })?;
        let mut line_starts = vec![0];
        line_starts.extend(text.bytes().enumerate().filter_map(|(index, byte)| {
            (byte == b'\n' || (byte == b'\r' && text.as_bytes().get(index + 1) != Some(&b'\n')))
                .then_some(index + 1)
        }));
        let weight = text.len()
            + line_starts.len() * std::mem::size_of::<usize>()
            + nodes(tree.root_node())?.len().saturating_mul(96);
        Ok(Self {
            weight,
            path: input.path.clone(),
            text,
            tree,
            line_starts,
        })
    }
    pub fn byte_at(&self, line: u32, column: u32) -> Option<usize> {
        let line = line.checked_sub(1)? as usize;
        let start = *self.line_starts.get(line)?;
        let offset = start.checked_add(column.checked_sub(1)? as usize)?;
        let end = self
            .line_starts
            .get(line + 1)
            .copied()
            .unwrap_or(self.text.len());
        (offset < end && self.text.is_char_boundary(offset)).then_some(offset)
    }
    pub fn location(&self, offset: usize) -> (u32, u32) {
        let offset = offset.min(self.text.len());
        let line = self
            .line_starts
            .partition_point(|start| *start <= offset)
            .saturating_sub(1);
        let mut byte = offset;
        while !self.text.is_char_boundary(byte) {
            byte -= 1;
        }
        (
            line as u32 + 1,
            self.text[self.line_starts[line]..byte].chars().count() as u32 + 1,
        )
    }
    pub fn target(&self, node: Node<'_>, name: impl Into<String>) -> InspectionTarget {
        let (line, column) = self.location(node.start_byte());
        let mut end = node.end_byte().saturating_sub(1).max(node.start_byte());
        while !self.text.is_char_boundary(end) {
            end -= 1;
        }
        let (end_line, end_column) = self.location(end);
        let mut target = InspectionTarget::at(&self.path, line);
        target.end_line = Some(end_line);
        target.start_column = Some(column);
        target.end_column = Some(end_column);
        target.target_name = name.into();
        target
    }
    pub fn text(&self, node: Node<'_>) -> &str {
        &self.text[node.byte_range()]
    }
}
/// Explicit stack bounds traversal independently of source nesting. No recursive Rust AST walk.
pub(crate) fn nodes(root: Node<'_>) -> Result<Vec<Node<'_>>, CppError> {
    let mut pending = vec![(root, 0u32)];
    let mut result = vec![];
    while let Some((node, depth)) = pending.pop() {
        if result.len() >= 1_000_000 || depth > 512 {
            return Err(CppError::new(
                "syntax-limit",
                "C++ syntax exceeds one million nodes or 512 nesting levels",
            ));
        }
        result.push(node);
        for i in (0..node.named_child_count()).rev() {
            pending.push((node.named_child(i as u32).expect("child index"), depth + 1));
        }
    }
    Ok(result)
}
pub(crate) fn function_name(file: &ParsedSource, node: Node<'_>) -> String {
    let Some(mut declarator) = node.child_by_field_name("declarator") else {
        return "<anonymous>".into();
    };
    for _ in 0..64 {
        if let Some(next) = declarator.child_by_field_name("declarator") {
            declarator = next;
        } else {
            break;
        }
    }
    file.text(declarator).to_owned()
}
pub(crate) fn is_excluded(path: &str) -> Option<&'static str> {
    if path.split('/').any(|part| {
        matches!(
            part,
            "vendor"
                | "vendors"
                | "third_party"
                | "third-party"
                | "external"
                | "node_modules"
                | ".venv"
                | "build"
                | "dist"
                | "generated"
        )
    }) {
        return Some("generated or third-party directory");
    }
    let name = path.rsplit('/').next().unwrap_or(path);
    if name.starts_with("moc_")
        || name.starts_with("qrc_")
        || name.starts_with("ui_")
        || name.ends_with(".pb.cc")
        || name.ends_with(".pb.h")
    {
        return Some("generated source filename");
    }
    None
}

pub(crate) fn policy_exclusions(
    request: &ici_domain::AnalysisRequest,
    path: &str,
) -> Vec<&'static str> {
    ici_analysis::source_exclusion_reasons(path)
        .into_iter()
        .filter(|reason| {
            !request
                .options
                .get(format!("include_{reason}"))
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false)
        })
        .collect()
}

pub(crate) fn record_policy_exclusions(
    request: &ici_domain::AnalysisRequest,
    output: &mut ici_domain::AnalysisOutput,
) -> std::collections::BTreeMap<String, usize> {
    let mut counts = std::collections::BTreeMap::new();
    for input in &request.inputs {
        let reasons = policy_exclusions(request, &input.path);
        if reasons.is_empty() {
            continue;
        }
        for reason in &reasons {
            *counts.entry((*reason).to_owned()).or_default() += 1;
        }
        let mut target = InspectionTarget::at(&input.path, 1);
        target.target_name = "ExcludedAnalysisSource".into();
        target.status = "SKIP".into();
        target.message = format!("excluded: {}", reasons.join(", "));
        output
            .observation
            .limitations
            .push(format!("{}: {}", input.path, target.message));
        output.targets.push(target);
    }
    counts
}

/// Borrowed logical source lines for C++'s LF, CRLF and CR physical input spellings.
pub(crate) fn physical_lines(mut text: &str) -> impl Iterator<Item = &str> {
    std::iter::from_fn(move || {
        if text.is_empty() {
            return None;
        }
        let Some(index) = text.find(['\r', '\n']) else {
            let line = text;
            text = "";
            return Some(line);
        };
        let line = &text[..index];
        let width = if text[index..].starts_with("\r\n") {
            2
        } else {
            1
        };
        text = &text[index + width..];
        Some(line)
    })
}

pub(crate) fn unqualified_name(name: &str) -> &str {
    let name = name.rsplit("::").next().unwrap_or(name);
    if name.starts_with("operator") {
        name
    } else {
        name.split('<').next().unwrap_or(name)
    }
}
