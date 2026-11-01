//! Bounded concrete C++ syntax over snapshotted bytes. This never preprocesses or runs a compiler.
use crate::CppError;
use ici_domain::{InspectionTarget, SourceInput};
use std::{
    ops::ControlFlow,
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
use tree_sitter::{Node, ParseOptions, Parser, Tree};

pub(crate) const PARSER_ID: &str = "tree-sitter-0.27.1/cpp-0.23.4/source-v2-qt";
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
        mask_qt_extensions(&mut syntax_bytes);
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

/// Qt meta-object markers that moc reads and the C++ preprocessor expands to declarations,
/// annotations or nothing. They are written without a trailing semicolon, so the concrete
/// grammar alone would see recovery regions in every `Q_OBJECT` class; the frozen lexical
/// scanner reads them as ordinary text. Bare markers and their balanced argument lists are
/// blanked in the grammar's copy only.
const QT_MARKERS: &[&str] = &[
    "Q_OBJECT",
    "Q_GADGET",
    "Q_GADGET_EXPORT",
    "Q_NAMESPACE",
    "Q_NAMESPACE_EXPORT",
    "Q_INVOKABLE",
    "Q_SCRIPTABLE",
    "Q_SLOT",
    "Q_SIGNAL",
    "Q_REVISION",
    "Q_PROPERTY",
    "Q_ENUM",
    "Q_ENUMS",
    "Q_ENUM_NS",
    "Q_FLAG",
    "Q_FLAGS",
    "Q_FLAG_NS",
    "Q_CLASSINFO",
    "Q_INTERFACES",
    "Q_PLUGIN_METADATA",
    "Q_DECLARE_PRIVATE",
    "Q_DECLARE_PRIVATE_D",
    "Q_DECLARE_PUBLIC",
    "Q_DISABLE_COPY",
    "Q_DISABLE_COPY_MOVE",
    "Q_DISABLE_MOVE",
    "Q_DECLARE_FLAGS",
    "Q_DECLARE_TR_FUNCTIONS",
    "Q_DECLARE_METATYPE",
    "Q_DECLARE_OPERATORS_FOR_FLAGS",
    "Q_DECLARE_INTERFACE",
    "Q_DECLARE_TYPEINFO",
    "Q_IMPORT_PLUGIN",
    "QTEST_MAIN",
    "QTEST_APPLESS_MAIN",
    "QTEST_GUILESS_MAIN",
];

/// Rewrite the grammar's copy of the source so Qt's access-specifier extensions and
/// meta-object markers parse like the declarations they expand to. Every replacement keeps
/// the byte length and every line break, so node offsets still address the original text:
/// `signals:`/`Q_SIGNALS:` read as `public:`, the `slots`/`Q_SLOTS` of
/// `public|protected|private slots:` are blanked, a statement-leading `emit`/`Q_EMIT` is
/// blanked, and the markers in [`QT_MARKERS`] (with a
/// balanced argument list) are blanked. Comments, literals and preprocessor directives are
/// never matched; a look-alike identifier, `signals::x` or `case signals:` is left unchanged.
pub(crate) fn mask_qt_extensions(bytes: &mut [u8]) {
    let word = |byte: u8| byte == b'_' || byte.is_ascii_alphanumeric() || byte >= 0x80;
    let blank = |bytes: &mut [u8], start: usize, end: usize| {
        for byte in &mut bytes[start..end] {
            if *byte != b'\n' && *byte != b'\r' {
                *byte = b' ';
            }
        }
    };
    let mut significant: Option<u8> = None;
    let mut previous_word: Option<(usize, usize)> = None;
    let mut line_start = true;
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'\n' || byte == b'\r' {
            line_start = true;
            index += 1;
            continue;
        }
        if byte == b' ' || byte == b'\t' || byte == b'\x0c' || byte == b'\x0b' {
            index += 1;
            continue;
        }
        if line_start && byte == b'#' {
            index = directive_end(bytes, index);
            continue;
        }
        line_start = false;
        if bytes[index..].starts_with(b"//") {
            while index < bytes.len() && bytes[index] != b'\n' && bytes[index] != b'\r' {
                index += if bytes[index] == b'\\' { 2 } else { 1 };
            }
            continue;
        }
        if bytes[index..].starts_with(b"/*") {
            index = bytes[index + 2..]
                .windows(2)
                .position(|pair| pair == b"*/")
                .map_or(bytes.len(), |end| index + 2 + end + 2);
            continue;
        }
        if byte == b'"' || byte == b'\'' {
            index = literal_end(bytes, index, byte);
            significant = Some(byte);
            previous_word = None;
            continue;
        }
        if !word(byte) {
            significant = Some(byte);
            previous_word = None;
            index += 1;
            continue;
        }
        let start = index;
        while index < bytes.len() && word(bytes[index]) {
            index += 1;
        }
        let token = &bytes[start..index];
        if token[0].is_ascii_digit() {
            // A pp-number, including digit separators (`1'000`) and exponent signs.
            while index < bytes.len()
                && (word(bytes[index])
                    || matches!(bytes[index], b'.' | b'\'')
                    || (matches!(bytes[index], b'+' | b'-')
                        && matches!(bytes[index - 1], b'e' | b'E' | b'p' | b'P')))
            {
                index += 1;
            }
            significant = Some(b'0');
            previous_word = None;
            continue;
        }
        if index < bytes.len()
            && (bytes[index] == b'"' || bytes[index] == b'\'')
            && matches!(
                token,
                b"L" | b"u" | b"U" | b"u8" | b"R" | b"LR" | b"uR" | b"UR" | b"u8R"
            )
        {
            index = if token.ends_with(b"R") && bytes[index] == b'"' {
                raw_literal_end(bytes, index)
            } else {
                literal_end(bytes, index, bytes[index])
            };
            significant = Some(b'"');
            previous_word = None;
            continue;
        }
        let colon = next_colon(bytes, index);
        match token {
            b"signals" | b"Q_SIGNALS"
                if colon
                    && previous_word.is_none()
                    && matches!(significant, None | Some(b'{' | b'}' | b';' | b':')) =>
            {
                bytes[start..start + 6].copy_from_slice(b"public");
                blank(bytes, start + 6, index);
                significant = Some(b'c');
                previous_word = Some((start, start + 6));
            }
            b"slots" | b"Q_SLOTS"
                if colon
                    && previous_word.is_some_and(|(from, to)| {
                        matches!(&bytes[from..to], b"public" | b"protected" | b"private")
                    }) =>
            {
                blank(bytes, start, index);
            }
            // `emit`/`Q_EMIT` expand to nothing before a signal call statement.
            b"emit" | b"Q_EMIT"
                if previous_word.is_none()
                    && matches!(significant, None | Some(b'{' | b'}' | b';' | b':' | b')'))
                    && identifier_follows(bytes, index) =>
            {
                blank(bytes, start, index);
            }
            _ if QT_MARKERS.iter().any(|marker| marker.as_bytes() == token) => {
                let end = arguments_end(bytes, index).unwrap_or(index);
                blank(bytes, start, end);
                index = end;
            }
            _ => {
                significant = Some(b'a');
                previous_word = Some((start, index));
            }
        }
    }
}

fn directive_end(bytes: &[u8], mut index: usize) -> usize {
    while index < bytes.len() && bytes[index] != b'\n' && bytes[index] != b'\r' {
        if bytes[index] == b'\\' {
            index += 1;
            if bytes[index..].starts_with(b"\r\n") {
                index += 1;
            }
        }
        index += 1;
    }
    index.min(bytes.len())
}

fn literal_end(bytes: &[u8], mut index: usize, quote: u8) -> usize {
    index += 1;
    while index < bytes.len() && bytes[index] != quote {
        if bytes[index] == b'\n' {
            return index;
        }
        index += if bytes[index] == b'\\' { 2 } else { 1 };
    }
    (index + 1).min(bytes.len())
}

fn raw_literal_end(bytes: &[u8], index: usize) -> usize {
    let Some(open) = bytes[index + 1..]
        .iter()
        .take(17)
        .position(|byte| *byte == b'(')
    else {
        return literal_end(bytes, index, b'"');
    };
    let delimiter = &bytes[index + 1..index + 1 + open];
    let mut close = Vec::with_capacity(delimiter.len() + 2);
    close.push(b')');
    close.extend_from_slice(delimiter);
    close.push(b'"');
    let body = index + 2 + open;
    bytes[body..]
        .windows(close.len())
        .position(|window| window == close.as_slice())
        .map_or(bytes.len(), |end| body + end + close.len())
}

/// The next significant byte is a single `:` (not `::`), with only blanks before it.
fn next_colon(bytes: &[u8], mut index: usize) -> bool {
    while index < bytes.len() && matches!(bytes[index], b' ' | b'\t' | b'\n' | b'\r') {
        index += 1;
    }
    bytes.get(index) == Some(&b':') && bytes.get(index + 1) != Some(&b':')
}

/// The next significant byte starts an identifier.
fn identifier_follows(bytes: &[u8], mut index: usize) -> bool {
    while index < bytes.len() && matches!(bytes[index], b' ' | b'\t' | b'\n' | b'\r') {
        index += 1;
    }
    bytes
        .get(index)
        .is_some_and(|byte| *byte == b'_' || byte.is_ascii_alphabetic() || *byte >= 0x80)
}

/// The end of a balanced argument list directly after a marker, or `None` when the marker
/// has no argument list. An unbalanced list (or one longer than 64 KiB) blanks only the name.
fn arguments_end(bytes: &[u8], mut index: usize) -> Option<usize> {
    while index < bytes.len() && matches!(bytes[index], b' ' | b'\t') {
        index += 1;
    }
    if bytes.get(index) != Some(&b'(') {
        return None;
    }
    let limit = bytes.len().min(index + 64 * 1024);
    let mut depth = 0usize;
    while index < limit {
        match bytes[index] {
            b'(' => depth += 1,
            b')' => {
                depth -= 1;
                if depth == 0 {
                    return Some(index + 1);
                }
            }
            b'"' | b'\'' => {
                index = literal_end(bytes, index, bytes[index]);
                continue;
            }
            b';' | b'{' | b'}' => return None,
            _ => (),
        }
        index += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::mask_qt_extensions;
    fn masked(source: &str) -> String {
        let mut bytes = source.as_bytes().to_vec();
        mask_qt_extensions(&mut bytes);
        assert_eq!(bytes.len(), source.len());
        String::from_utf8(bytes).unwrap()
    }
    #[test]
    fn qt_access_specifiers_and_markers_keep_offsets_and_parse_like_their_expansion() {
        assert_eq!(
            masked(
                "class A {\n  Q_OBJECT\n  Q_PROPERTY(int x READ x NOTIFY changed)\nprivate slots:\n  void a();\nsignals:\n  void changed();\npublic Q_SLOTS :\n  Q_INVOKABLE void b();\nQ_SIGNALS:\n  void c();\n};\nQTEST_APPLESS_MAIN(A)\n#include \"a.moc\"\nvoid f() { emit a.c(); if (x) Q_EMIT c(); }\n"
            ),
            "class A {\n          \n                                         \nprivate      :\n  void a();\npublic :\n  void changed();\npublic         :\n              void b();\npublic   :\n  void c();\n};\n                     \n#include \"a.moc\"\nvoid f() {      a.c(); if (x)        c(); }\n"
        );
    }
    #[test]
    fn look_alike_identifiers_scopes_literals_and_directives_are_not_rewritten() {
        for source in [
            "int signals = 1; int f() { return a ? signals : b; }\n",
            "void f() { switch (x) { case signals: break; } }\n",
            "auto v = signals::value; class B { myslots: int slots_; };\n",
            "const char* s = \"Q_OBJECT signals:\"; // private slots:\n/* Q_SIGNALS: */ char c = 's';\n",
            "#define Q_OBJECT\n#define emit\nauto r = R\"x(private slots: Q_OBJECT)x\"; int n = 1'000;\n",
            "class C { public: int slots; };\n",
            "void emit(int); int f() { emit(1); return x.emit + emit; }\n",
        ] {
            assert_eq!(masked(source), source, "{source}");
        }
        // An unbalanced marker argument list blanks only the marker name.
        assert_eq!(masked("Q_PROPERTY(int x\n;"), "          (int x\n;");
    }
}
