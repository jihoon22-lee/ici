//! Frozen Qt generated-code linkage and Qt-major evidence verification
//! (`analysis/_qt_codegen.py`). Pure inspection of project files and recorded compilation units;
//! nothing is built or executed.
use crate::model::CompilationUnit;
use ici_domain::InspectionTarget;
use regex::Regex;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
    sync::LazyLock,
};

const MAX_INPUTS: usize = 4_096;
const MAX_INCLUDE_FILES: usize = 4_096;
const MAX_FILE_BYTES: u64 = 2 * 1_048_576;
const BUDGET: &str = "Qt generated-code input count exceeds the bounded limit";

// Python `\s` also matches the information separators U+001C..U+001F.
static Q_OBJECT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\bQ_OBJECT\b").unwrap());
static DIRECTIVE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[\s\x1c-\x1f]*#[\s\x1c-\x1f]*(?P<name>[A-Za-z]+)(?P<value>.*)$").unwrap()
});
static INCLUDE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"^[\s\x1c-\x1f]*#[\s\x1c-\x1f]*include[\s\x1c-\x1f]*[<"](?P<name>[^>"\r\n]{1,1024})[>"]"#,
    )
    .unwrap()
});
static INCLUDE_DIRECTIVE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[\s\x1c-\x1f]*#[\s\x1c-\x1f]*include\b").unwrap());
static QT5: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:^|[/\\_.-])qt5(?:$|[/\\_.-])|\bqt5(?:core|gui|widgets)\b").unwrap()
});
static QT6: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:^|[/\\_.-])qt6(?:$|[/\\_.-])|\bqt6(?:core|gui|widgets)\b").unwrap()
});
static QT_LIB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\bQT_[A-Z0-9_]+_LIB\b").unwrap());

fn markers(kind: &str) -> &'static [&'static str] {
    match kind {
        "moc" => &[
            "Meta object code",
            "qt_meta_stringdata_",
            "qt_static_metacall",
        ],
        "qrc" => &[
            "QT_RCC_MANGLE_NAMESPACE",
            "qInitResources_",
            "qt_resource_data",
        ],
        _ => &["setupUi(", "retranslateUi(", "Ui_"],
    }
}

/// One frozen `Finding` of `_finding_for_target` (fingerprint computed by the reporter).
#[derive(Clone, Debug)]
pub struct QtFinding {
    pub rule_id: String,
    pub target: InspectionTarget,
}
impl QtFinding {
    /// The frozen finding as a historical report finding (label = target name).
    pub fn legacy_value(&self) -> Value {
        let compatibility = self.rule_id.ends_with("compatibility");
        let status = self.target.status.as_str();
        json!({
            "rule_id": self.rule_id,
            "category": if compatibility { "compatibility" } else { "build" },
            "severity": if matches!(status, "FAIL" | "ERROR") { "high" } else { "medium" },
            "confidence": if status == "FAIL" { "exact" } else { "high" },
            "primary_location": {
                "path": self.target.file_path,
                "start_line": self.target.start_line,
                "end_line": self.target.end_line,
                "start_column": self.target.start_column,
                "end_column": self.target.end_column,
                "label": self.target.target_name,
            },
            "related_locations": [],
            "message": self.target.message,
            "explanation": if compatibility {
                "Qt major compatibility could not be proved from a successful exact compile replay."
            } else {
                "A declared Qt generated-code input is not linked by the exact compilation context."
            },
            "remediation": "",
            "fixes": [],
            "tool_rule_id": "",
            "tool_name": "",
            "tool_version": "",
            "metrics": {},
            "snippet": "",
            "suppression": {"kind": "none", "reason": "", "suppressed": false},
        })
    }
}

/// Frozen `QtCodegenOutcome`.
#[derive(Clone, Debug)]
pub struct QtCodegenOutcome {
    pub targets: Vec<InspectionTarget>,
    pub findings: Vec<QtFinding>,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
    pub mode: String,
    pub inputs_checked: usize,
    pub ui_checked: usize,
    pub qrc_checked: usize,
    pub moc_checked: usize,
    pub qt5_units: usize,
    pub qt6_units: usize,
}
impl Default for QtCodegenOutcome {
    fn default() -> Self {
        Self {
            targets: vec![],
            findings: vec![],
            errors: vec![],
            warnings: vec![],
            mode: "not_applicable".into(),
            inputs_checked: 0,
            ui_checked: 0,
            qrc_checked: 0,
            moc_checked: 0,
            qt5_units: 0,
            qt6_units: 0,
        }
    }
}

/// The frozen `AnalysisContext` facets `verify_qt_codegen` reads.
pub struct QtContext<'a> {
    /// `context.compilation.database_path is not None`.
    pub database: bool,
    /// Any error-level `context.compilation.diagnostics` (database row errors).
    pub has_errors: bool,
    /// Every recorded unit, in frozen `(source, directory, configuration)` order.
    pub units: &'a [CompilationUnit],
}

/// Inputs of frozen `verify_qt_codegen`: project-relative POSIX paths.
pub struct QtCodegenRequest<'a> {
    pub root: &'a Path,
    /// Existing project source directories (frozen `get_source_dirs`).
    pub source_dirs: &'a [PathBuf],
    /// Production C/C++ sources (`project_cpp_sources`, also `context.project.cpp_sources`).
    pub cpp_files: &'a [String],
    /// `.h .hh .hpp .hxx .moc` headers (`project_cpp_headers`).
    pub cpp_headers: &'a [String],
    /// `None` for a run without an analysis context (standalone `ici lint`).
    pub context: Option<QtContext<'a>>,
    /// Sources whose syntax replay exited 0 untruncated (frozen `_successful_cpp_replays`).
    pub compiled_sources: &'a BTreeSet<String>,
}

#[derive(Clone, Debug)]
struct QtInput {
    kind: &'static str,
    path: String,
    line: u32,
    stem: String,
}

fn blank(ch: char) -> char {
    if ch == '\n' { '\n' } else { ' ' }
}

fn starts(chars: &[char], index: usize, pattern: &[char]) -> bool {
    chars.len() >= index + pattern.len() && chars[index..index + pattern.len()] == *pattern
}

fn py_isspace(ch: char) -> bool {
    ch.is_whitespace() || ('\x1c'..='\x1f').contains(&ch)
}

/// Frozen `_cpp_code`: blank comments and literals, keeping every `\n` and code position.
pub fn cpp_code(text: &str) -> String {
    #[derive(PartialEq)]
    enum State {
        Code,
        Line,
        Block,
        Literal,
        Raw,
    }
    let chars: Vec<char> = text.chars().collect();
    let mut output = String::with_capacity(text.len());
    let (mut index, mut state, mut quote) = (0, State::Code, '\0');
    let mut raw_end: Vec<char> = vec![];
    while index < chars.len() {
        let ch = chars[index];
        match state {
            State::Line => {
                output.push(blank(ch));
                index += 1;
                if ch == '\n' {
                    state = State::Code;
                }
            }
            State::Block => {
                if starts(&chars, index, &['*', '/']) {
                    output.push_str("  ");
                    index += 2;
                    state = State::Code;
                } else {
                    output.push(blank(ch));
                    index += 1;
                }
            }
            State::Literal => {
                if ch == '\\' && index + 1 < chars.len() {
                    output.push(' ');
                    output.push(blank(chars[index + 1]));
                    index += 2;
                    continue;
                }
                output.push(blank(ch));
                index += 1;
                if ch == quote || ch == '\n' {
                    state = State::Code;
                }
            }
            State::Raw => {
                if !raw_end.is_empty() && starts(&chars, index, &raw_end) {
                    output.extend(std::iter::repeat_n(' ', raw_end.len()));
                    index += raw_end.len();
                    raw_end.clear();
                    state = State::Code;
                } else {
                    output.push(blank(ch));
                    index += 1;
                }
            }
            State::Code => {
                if starts(&chars, index, &['/', '/']) {
                    output.push_str("  ");
                    index += 2;
                    state = State::Line;
                } else if starts(&chars, index, &['/', '*']) {
                    output.push_str("  ");
                    index += 2;
                    state = State::Block;
                } else if ch == '\'' || ch == '"' {
                    output.push(' ');
                    index += 1;
                    quote = ch;
                    state = State::Literal;
                } else if let Some((opening, delimiter)) = raw_delimiter(&chars, index) {
                    output.extend(std::iter::repeat_n(' ', opening - index + 1));
                    index = opening + 1;
                    raw_end = std::iter::once(')')
                        .chain(delimiter)
                        .chain(std::iter::once('"'))
                        .collect();
                    state = State::Raw;
                } else {
                    output.push(ch);
                    index += 1;
                }
            }
        }
    }
    output
}

/// Frozen `_raw_delimiter`: `R"<delim>(` with a 16-character delimiter window.
fn raw_delimiter(chars: &[char], index: usize) -> Option<(usize, Vec<char>)> {
    if !starts(chars, index, &['R', '"']) {
        return None;
    }
    let end = chars.len().min(index + 19);
    let opening = (index + 2..end).find(|position| chars[*position] == '(')?;
    let delimiter = chars[index + 2..opening].to_vec();
    if delimiter
        .iter()
        .any(|ch| py_isspace(*ch) || matches!(ch, '\\' | '(' | ')'))
    {
        return None;
    }
    Some((opening, delimiter))
}

/// Character spans of Python `str.splitlines()` lines (every Unicode line boundary, CRLF as
/// one, no trailing empty line).
fn line_spans(chars: &[char]) -> Vec<(usize, usize)> {
    let mut spans = vec![];
    let (mut start, mut index) = (0, 0);
    while index < chars.len() {
        if matches!(
            chars[index],
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        ) {
            spans.push((start, index));
            index += if chars[index] == '\r' && chars.get(index + 1) == Some(&'\n') {
                2
            } else {
                1
            };
            start = index;
        } else {
            index += 1;
        }
    }
    if start < chars.len() {
        spans.push((start, chars.len()));
    }
    spans
}

/// Python `str.splitlines()`.
pub fn py_splitlines(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    line_spans(&chars)
        .into_iter()
        .map(|(start, end)| chars[start..end].iter().collect())
        .collect()
}

/// Frozen `_q_object_line`: the first lexical `Q_OBJECT` line outside definitely disabled
/// `#if 0` / `#if false` branches (`#ifdef` / `#ifndef` are always taken).
pub fn q_object_line(text: &str) -> Option<u32> {
    let mut active = true;
    // parent-active, any-branch-taken, current-branch-active
    let mut conditions: Vec<(bool, bool, bool)> = vec![];
    let code = cpp_code(text);
    for (index, line) in py_splitlines(&code).iter().enumerate() {
        if let Some(directive) = DIRECTIVE.captures(line) {
            let name = directive["name"].to_lowercase();
            let value = directive["value"].trim_matches(py_isspace).to_lowercase();
            let condition = !matches!(value.as_str(), "0" | "false");
            match name.as_str() {
                "if" | "ifdef" | "ifndef" => {
                    let parent = active;
                    active = parent && (name != "if" || condition);
                    conditions.push((parent, active, active));
                }
                "elif" if !conditions.is_empty() => {
                    let (parent, taken, _) = *conditions.last().unwrap();
                    active = parent && !taken && condition;
                    *conditions.last_mut().unwrap() = (parent, taken || active, active);
                }
                "else" if !conditions.is_empty() => {
                    let (parent, taken, _) = *conditions.last().unwrap();
                    active = parent && !taken;
                    *conditions.last_mut().unwrap() = (parent, true, active);
                }
                "endif" if !conditions.is_empty() => {
                    active = conditions.pop().unwrap().0;
                }
                _ => {}
            }
            continue;
        }
        if active && Q_OBJECT.is_match(line) {
            return Some(index as u32 + 1);
        }
    }
    None
}

/// Frozen `_include_names`: `#include` names of active code lines, read from the original line.
///
/// Known frozen defect, corrected: frozen pairs the original and masked lines with
/// `zip(..., strict=True)`, so a lone CR / VT / FF / U+2028 inside a comment or literal (blanked
/// in the masked text) makes the line counts differ and crashes the whole lint engine
/// (`Engine crashed: ValueError`). The masker keeps every character position, so native cuts the
/// masked text at the original line boundaries; whenever frozen does not crash this is the
/// frozen pairing.
pub fn include_names(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let code: Vec<char> = cpp_code(text).chars().collect();
    line_spans(&chars)
        .into_iter()
        .filter(|(start, end)| {
            INCLUDE_DIRECTIVE.is_match(&code[*start..*end].iter().collect::<String>())
        })
        .filter_map(|(start, end)| {
            let line: String = chars[start..end].iter().collect();
            INCLUDE
                .captures(&line)
                .map(|found| found["name"].to_owned())
        })
        .collect()
}

/// Frozen `_is_qt_include`.
pub fn is_qt_include(name: &str) -> bool {
    if name.starts_with("Qt5") || name.starts_with("Qt6") {
        return true;
    }
    let chars: Vec<char> = name.chars().collect();
    if name.starts_with("Qt") && chars.len() > 2 {
        return chars[2] == '/' || chars[2].is_uppercase();
    }
    name.starts_with('Q') && chars.len() > 1 && chars[1].is_uppercase()
}

/// Frozen search paths (`-I` / `-isystem` / `-iquote`) and `-D` definitions of a unit.
fn search_paths(unit: &CompilationUnit) -> impl Iterator<Item = &crate::model::IncludePath> {
    unit.include_paths
        .iter()
        .filter(|path| matches!(path.kind.as_str(), "include" | "system" | "quote"))
}

/// Frozen `_qt_major`: Qt 5 / Qt 6 evidence in argv, search paths and definitions.
pub fn qt_major(unit: &CompilationUnit) -> BTreeSet<u8> {
    let values: Vec<String> = unit
        .argv
        .iter()
        .cloned()
        .chain(search_paths(unit).map(|path| path.path.clone()))
        .chain(
            unit.defines
                .iter()
                .filter(|define| !define.undefine)
                .map(|define| {
                    format!(
                        "{}={}",
                        define.name,
                        define.value.as_deref().unwrap_or_default()
                    )
                }),
        )
        .collect();
    let mut majors = BTreeSet::new();
    if values
        .iter()
        .any(|value| QT5.is_match(value) || value.contains("QT_VERSION_MAJOR=5"))
    {
        majors.insert(5);
    }
    if values
        .iter()
        .any(|value| QT6.is_match(value) || value.contains("QT_VERSION_MAJOR=6"))
    {
        majors.insert(6);
    }
    majors
}

fn inside(root: &Path, path: &Path) -> bool {
    path.starts_with(root)
}

/// Python `Path.resolve(strict=False)`: canonical existing prefix plus the lexical rest.
fn resolve_lenient(path: &Path) -> PathBuf {
    let lexical = crate::input::normalize(path);
    let mut existing = lexical.as_path();
    let mut suffix = vec![];
    loop {
        if let Ok(resolved) = existing.canonicalize() {
            let mut resolved = resolved;
            for part in suffix.into_iter().rev() {
                resolved.push(part);
            }
            return resolved;
        }
        match (existing.file_name(), existing.parent()) {
            (Some(name), Some(parent)) => {
                suffix.push(name.to_os_string());
                existing = parent;
            }
            _ => return lexical,
        }
    }
}

fn exception_name(error: &std::io::Error) -> &'static str {
    match error.kind() {
        ErrorKind::NotFound => "FileNotFoundError",
        ErrorKind::PermissionDenied => "PermissionError",
        ErrorKind::IsADirectory => "IsADirectoryError",
        ErrorKind::NotADirectory => "NotADirectoryError",
        _ if error.raw_os_error() == Some(40) => "RuntimeError",
        _ => "OSError",
    }
}

/// Frozen `_read_contained`: a contained, regular, at most 2 MiB file decoded with replacement.
fn read_contained(root: &Path, path: &Path) -> Result<String, String> {
    if fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("file is a symbolic link".into());
    }
    let resolved = path
        .canonicalize()
        .map_err(|error| format!("file could not be resolved: {}", exception_name(&error)))?;
    let metadata = fs::metadata(&resolved)
        .map_err(|error| format!("file could not be resolved: {}", exception_name(&error)))?;
    if !inside(root, &resolved) || !metadata.is_file() {
        return Err("file is not a contained regular file".into());
    }
    if metadata.len() > MAX_FILE_BYTES {
        return Err("file exceeds the 2 MiB inspection limit".into());
    }
    fs::read(&resolved)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .map_err(|error| format!("file could not be read: {}", exception_name(&error)))
}

/// Frozen `_should_ignore_path` over every part of the canonical path.
fn ignored(path: &Path) -> bool {
    path.components().any(|part| {
        let part = part.as_os_str().to_string_lossy();
        matches!(
            part.as_ref(),
            ".venv" | "venv" | "build" | "__pycache__" | ".git" | ".pytest_cache" | ".ruff_cache"
        ) || part.starts_with("v1.")
            || part.starts_with("v0.")
    })
}

/// Frozen `_iter_project_files`: contained regular files with one of `suffixes`, never
/// following a symlink and skipping ignored directories.
fn project_files(root: &Path, directory: &Path, suffixes: &[&str]) -> Vec<PathBuf> {
    let Ok(start) = directory.canonicalize() else {
        return vec![];
    };
    if !inside(root, &start) || !start.is_dir() {
        return vec![];
    }
    let mut files = vec![];
    let mut pending = vec![start];
    while let Some(current) = pending.pop() {
        let Ok(entries) = fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if kind.is_symlink() {
                continue;
            }
            if kind.is_dir() {
                if let Ok(safe) = path.canonicalize()
                    && inside(root, &safe)
                    && !ignored(&safe)
                {
                    pending.push(safe);
                }
                continue;
            }
            let matches = path
                .file_name()
                .and_then(|name| name.to_str())
                .and_then(|name| {
                    let dot = name.rfind('.').filter(|position| *position > 0)?;
                    Some(&name[dot..])
                })
                .is_some_and(|suffix| suffixes.contains(&suffix));
            if !matches {
                continue;
            }
            if let Ok(canonical) = path.canonicalize()
                && inside(root, &canonical)
                && canonical.is_file()
                && !ignored(&canonical)
            {
                files.push(canonical);
            }
        }
    }
    files
}

/// Frozen `get_all_cpp_sources` / `get_all_cpp_headers`: project-relative `.cpp .cc .cxx .c`
/// sources under the source directories and `.h .hh .hpp .hxx .moc` headers under them plus the
/// project's `include/` directory.
pub fn legacy_cpp_inputs(root: &Path, source_dirs: &[PathBuf]) -> (Vec<String>, Vec<String>) {
    let Ok(root) = root.canonicalize() else {
        return (vec![], vec![]);
    };
    let collect = |directories: &[PathBuf], suffixes: &[&str]| {
        let mut files: Vec<String> = directories
            .iter()
            .flat_map(|directory| project_files(&root, directory, suffixes))
            .filter_map(|path| relative(&root, &path))
            .collect();
        files.sort();
        files.dedup();
        files
    };
    let sources = collect(source_dirs, &[".cpp", ".cc", ".cxx", ".c"]);
    let mut header_roots: Vec<PathBuf> = source_dirs
        .iter()
        .filter_map(|directory| directory.canonicalize().ok())
        .collect();
    if let Ok(include) = root.join("include").canonicalize()
        && include.starts_with(&root)
        && include.is_dir()
        && !header_roots.contains(&include)
    {
        header_roots.push(include);
    }
    let headers = collect(&header_roots, &[".h", ".hh", ".hpp", ".hxx", ".moc"]);
    (sources, headers)
}

fn relative(root: &Path, path: &Path) -> Option<String> {
    Some(path.strip_prefix(root).ok()?.to_str()?.replace('\\', "/"))
}

fn stem(path: &str) -> String {
    Path::new(path)
        .file_stem()
        .map(|stem| stem.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn file_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

fn target(
    item: &QtInput,
    status: &str,
    name: impl Into<String>,
    message: impl Into<String>,
) -> InspectionTarget {
    let mut target = InspectionTarget::at(&item.path, item.line);
    target.target_name = name.into();
    target.status = status.into();
    target.message = message.into();
    target
}

fn root_error(outcome: &mut QtCodegenOutcome, name: &str, message: String) {
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = name.into();
    target.status = "ERROR".into();
    target.message = message.clone();
    outcome.errors.push(message);
    outcome.targets.push(target);
}

/// Frozen `_append`: the target, its finding unless PASS/SKIP, and its error or warning.
fn append(outcome: &mut QtCodegenOutcome, target: InspectionTarget, rule: &str) {
    if !matches!(target.status.as_str(), "PASS" | "SKIP") {
        outcome.findings.push(QtFinding {
            rule_id: rule.into(),
            target: target.clone(),
        });
    }
    match target.status.as_str() {
        "ERROR" => outcome.errors.push(target.message.clone()),
        "WARN" => outcome.warnings.push(target.message.clone()),
        _ => {}
    }
    outcome.targets.push(target);
}

fn discover_inputs(request: &QtCodegenRequest, outcome: &mut QtCodegenOutcome) -> Vec<QtInput> {
    let root = request.root;
    let mut resources = BTreeMap::<String, &'static str>::new();
    for directory in request.source_dirs {
        for path in project_files(root, directory, &[".ui", ".qrc"]) {
            let Some(key) = relative(root, &path) else {
                continue;
            };
            let kind = if key.ends_with(".ui") { "ui" } else { "qrc" };
            resources.insert(key, kind);
            if resources.len() > MAX_INPUTS {
                root_error(outcome, "QtCodegenBudgetError", BUDGET.into());
                return vec![];
            }
        }
    }
    let mut inputs: Vec<QtInput> = resources
        .into_iter()
        .map(|(path, kind)| QtInput {
            kind,
            stem: stem(&path),
            path,
            line: 1,
        })
        .collect();
    let mut objects = BTreeMap::<String, PathBuf>::new();
    for lexical in request.cpp_files.iter().chain(request.cpp_headers) {
        let lexical = root.join(lexical);
        if let Some(key) = relative(root, &resolve_lenient(&lexical)) {
            objects.entry(key).or_insert(lexical);
        }
    }
    for (key, path) in objects {
        let item = QtInput {
            kind: "moc",
            stem: path
                .file_stem()
                .map(|stem| stem.to_string_lossy().into_owned())
                .unwrap_or_default(),
            path: key,
            line: 1,
        };
        match read_contained(root, &path) {
            Err(error) => append(
                outcome,
                target(&item, "ERROR", "QtMocInspectionError", error),
                "ici.qt.codegen.moc",
            ),
            Ok(text) => {
                if let Some(line) = q_object_line(&text) {
                    inputs.push(QtInput { line, ..item });
                }
            }
        }
    }
    if inputs.len() > MAX_INPUTS {
        root_error(outcome, "QtCodegenBudgetError", BUDGET.into());
        return vec![];
    }
    inputs
}

/// Frozen `_unit_file`.
fn unit_file(root: &Path, unit: &CompilationUnit) -> Option<PathBuf> {
    let path = root.join(&unit.source).canonicalize().ok()?;
    let metadata = fs::metadata(&path).ok()?;
    (inside(root, &path) && metadata.is_file() && metadata.len() <= MAX_FILE_BYTES).then_some(path)
}

fn unit_includes(root: &Path, unit: &CompilationUnit) -> Vec<String> {
    unit_file(root, unit)
        .and_then(|path| fs::read(path).ok())
        .map(|bytes| include_names(&String::from_utf8_lossy(&bytes)))
        .unwrap_or_default()
}

/// Frozen `_include_roots`.
fn include_roots(root: &Path, unit: &CompilationUnit) -> Vec<PathBuf> {
    let source_parent = resolve_lenient(&root.join(&unit.source))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_default();
    let cwd = resolve_lenient(&root.join(&unit.directory));
    let mut roots = vec![source_parent, cwd.clone()];
    for entry in search_paths(unit) {
        if entry.scope != "project" || !entry.present {
            continue;
        }
        let candidate = resolve_lenient(&root.join(&entry.path));
        if inside(root, &candidate) {
            roots.push(candidate);
        }
    }
    let mut index = 1;
    while index < unit.argv.len() {
        let token = unit.argv[index].as_str();
        let value = if matches!(token, "-I" | "-iquote" | "-isystem") && index + 1 < unit.argv.len()
        {
            index += 2;
            unit.argv[index - 1].as_str()
        } else if let Some(value) = token.strip_prefix("-I").filter(|value| !value.is_empty()) {
            index += 1;
            value
        } else {
            index += 1;
            ""
        };
        if value.is_empty() || value.contains('\0') {
            continue;
        }
        let lexical = Path::new(value);
        let candidate = resolve_lenient(&if lexical.is_absolute() {
            lexical.to_path_buf()
        } else {
            cwd.join(lexical)
        });
        if inside(root, &candidate) {
            roots.push(candidate);
        }
    }
    let mut seen = BTreeSet::new();
    roots.retain(|path| seen.insert(path.clone()));
    roots.truncate(1_024);
    roots
}

/// Frozen `_resolved_include`.
fn resolved_include(
    root: &Path,
    unit: &CompilationUnit,
    include: &str,
    includer: Option<&Path>,
) -> Option<PathBuf> {
    let lexical = Path::new(include);
    if include.starts_with('/') || include.split('/').any(|part| part == "..") {
        return None;
    }
    let mut directories: Vec<PathBuf> = includer
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .into_iter()
        .collect();
    directories.extend(include_roots(root, unit));
    let mut seen = BTreeSet::new();
    for directory in directories {
        if !seen.insert(directory.clone()) {
            continue;
        }
        let candidate = directory.join(lexical);
        if fs::symlink_metadata(&candidate).is_ok_and(|metadata| metadata.file_type().is_symlink())
        {
            continue;
        }
        let Ok(resolved) = candidate.canonicalize() else {
            continue;
        };
        if inside(root, &resolved) && resolved.is_file() {
            return Some(resolved);
        }
    }
    None
}

fn is_generated(root: &Path, path: &Path, kind: &str) -> bool {
    read_contained(root, path)
        .is_ok_and(|text| markers(kind).iter().any(|marker| text.contains(marker)))
}

/// Frozen `_find_ui_link`: a uic header reached through the include closure of a unit.
fn find_ui_link<'a>(
    root: &Path,
    units: &[&'a CompilationUnit],
    expected: &str,
) -> Option<(&'a CompilationUnit, PathBuf)> {
    for unit in units {
        let Some(source) = unit_file(root, unit) else {
            continue;
        };
        let mut pending = vec![source];
        let mut visited = BTreeSet::new();
        while visited.len() < MAX_INCLUDE_FILES {
            let Some(includer) = pending.pop() else {
                break;
            };
            if !visited.insert(includer.clone()) {
                continue;
            }
            let includes = read_contained(root, &includer)
                .map(|text| include_names(&text))
                .unwrap_or_default();
            for include in includes {
                let Some(generated) = resolved_include(root, unit, &include, Some(&includer))
                else {
                    continue;
                };
                if file_name(&include) == expected && is_generated(root, &generated, "ui") {
                    return Some((unit, generated));
                }
                if !visited.contains(&generated) {
                    pending.push(generated);
                }
            }
        }
    }
    None
}

fn find_generated_unit<'a>(
    root: &Path,
    units: &[&'a CompilationUnit],
    expected: &str,
    kind: &str,
) -> Option<&'a CompilationUnit> {
    units.iter().copied().find(|unit| {
        file_name(&unit.source) == expected
            && unit_file(root, unit).is_some_and(|path| is_generated(root, &path, kind))
    })
}

/// Frozen `_find_moc_link`: a direct `moc_<stem>.cpp` unit, a `<stem>.moc` included by a unit,
/// or a `mocs_compilation.cpp` unit including `moc_<stem>.cpp`.
fn find_moc_link<'a>(
    root: &Path,
    units: &[&'a CompilationUnit],
    stem: &str,
) -> Option<(&'a CompilationUnit, String)> {
    let generated_cpp = format!("moc_{stem}.cpp");
    if let Some(unit) = find_generated_unit(root, units, &generated_cpp, "moc") {
        return Some((unit, generated_cpp));
    }
    let source_moc = format!("{stem}.moc");
    for unit in units {
        for include in unit_includes(root, unit) {
            if file_name(&include) == source_moc
                && resolved_include(root, unit, &include, None)
                    .is_some_and(|generated| is_generated(root, &generated, "moc"))
            {
                return Some((unit, source_moc));
            }
        }
    }
    for unit in units {
        if file_name(&unit.source) != "mocs_compilation.cpp" {
            continue;
        }
        for include in unit_includes(root, unit) {
            if file_name(&include) == generated_cpp
                && resolved_include(root, unit, &include, None)
                    .is_some_and(|generated| is_generated(root, &generated, "moc"))
            {
                return Some((unit, generated_cpp));
            }
        }
    }
    None
}

/// Frozen `_qt_relevant` without its include scan: Qt-major evidence or a `QT_*_LIB` token in
/// argv, search paths or definition names.
pub fn qt_metadata_relevant(unit: &CompilationUnit) -> bool {
    let metadata: Vec<&str> = unit
        .argv
        .iter()
        .map(String::as_str)
        .chain(search_paths(unit).map(|path| path.path.as_str()))
        .chain(
            unit.defines
                .iter()
                .filter(|define| !define.undefine)
                .map(|define| define.name.as_str()),
        )
        .collect();
    !qt_major(unit).is_empty() || QT_LIB.is_match(&metadata.join("\n"))
}

/// Frozen `_qt_relevant`.
fn qt_relevant(root: &Path, unit: &CompilationUnit) -> bool {
    qt_metadata_relevant(unit)
        || unit_includes(root, unit)
            .iter()
            .any(|name| is_qt_include(name))
}

fn verify_major_evidence(
    request: &QtCodegenRequest,
    units: &[CompilationUnit],
    outcome: &mut QtCodegenOutcome,
) {
    let production: BTreeSet<&String> = request.cpp_files.iter().collect();
    for unit in units
        .iter()
        .filter(|unit| production.contains(&unit.source) && qt_relevant(request.root, unit))
    {
        let majors = qt_major(unit);
        let item = QtInput {
            kind: "compatibility",
            path: unit.source.clone(),
            line: 1,
            stem: stem(&unit.source),
        };
        if majors.len() > 1 {
            append(
                outcome,
                target(
                    &item,
                    "FAIL",
                    "QtCompatibility:ConflictingMajor",
                    "The exact translation unit mixes Qt 5 and Qt 6 include or define evidence.",
                ),
                "ici.qt.compatibility",
            );
            continue;
        }
        let Some(major) = majors.first().copied() else {
            append(
                outcome,
                target(
                    &item,
                    "WARN",
                    "QtCompatibility:UnknownMajor",
                    "Qt code is present, but the exact translation unit does not identify Qt 5 or Qt 6.",
                ),
                "ici.qt.compatibility",
            );
            continue;
        };
        if major == 5 {
            outcome.qt5_units += 1;
        } else {
            outcome.qt6_units += 1;
        }
        let replayed = request.compiled_sources.contains(&unit.source);
        let mut item = target(
            &item,
            if replayed { "PASS" } else { "WARN" },
            format!("QtCompatibility:Qt{major}"),
            if replayed {
                format!("Qt {major} API compatibility passed an exact sanitized compile replay.")
            } else {
                format!("Qt {major} compilation context exists without successful replay evidence.")
            },
        );
        item.metrics = BTreeMap::from([
            ("qt_major".into(), json!(major)),
            ("compile_replay".into(), json!(i32::from(replayed))),
        ]);
        append(outcome, item, "ici.qt.compatibility");
    }
}

fn linkage(
    request: &QtCodegenRequest,
    item: &QtInput,
    units: &[&CompilationUnit],
    production: &[&CompilationUnit],
) -> InspectionTarget {
    let root = request.root;
    let successful = request.compiled_sources;
    let replay = |name: &str, unit: &CompilationUnit, pass: String, warn: String| {
        let replayed = successful.contains(&unit.source);
        let mut result = target(
            item,
            if replayed { "PASS" } else { "WARN" },
            name,
            if replayed { pass } else { warn },
        );
        result.metrics = BTreeMap::from([("compile_replay".into(), json!(i32::from(replayed)))]);
        result
    };
    match item.kind {
        "ui" => {
            let expected = format!("ui_{}.h", item.stem);
            let Some((unit, generated)) = find_ui_link(root, production, &expected) else {
                return target(
                    item,
                    "FAIL",
                    "QtUicLinkage",
                    format!(
                        "uic output {expected} is absent or not included by an exact translation unit."
                    ),
                );
            };
            let generated = relative(root, &generated).unwrap_or_default();
            replay(
                "QtUicLinkage",
                unit,
                format!(
                    "uic output {generated} is linked by successfully replayed unit {}.",
                    unit.source
                ),
                format!(
                    "uic output {generated} is linked by {}, but that unit has no successful compile replay.",
                    unit.source
                ),
            )
        }
        "qrc" => {
            let expected = format!("qrc_{}.cpp", item.stem);
            let Some(unit) = find_generated_unit(root, units, &expected, "qrc") else {
                return target(
                    item,
                    "FAIL",
                    "QtRccLinkage",
                    format!("rcc output {expected} is absent from the exact compilation database."),
                );
            };
            replay(
                "QtRccLinkage",
                unit,
                format!(
                    "rcc output {} is a successfully replayed generated compilation unit.",
                    unit.source
                ),
                format!(
                    "rcc output {} is in the compilation database without a successful replay.",
                    unit.source
                ),
            )
        }
        _ => {
            let Some((unit, generated)) = find_moc_link(root, units, &item.stem) else {
                return target(
                    item,
                    "FAIL",
                    "QtMocLinkage",
                    format!(
                        "No linked moc output was found for Q_OBJECT in {}.",
                        item.path
                    ),
                );
            };
            replay(
                "QtMocLinkage",
                unit,
                format!(
                    "moc output {generated} is linked by successfully replayed unit {}.",
                    unit.source
                ),
                format!(
                    "moc output {generated} is linked by {} without a successful replay.",
                    unit.source
                ),
            )
        }
    }
}

fn title(kind: &str) -> String {
    let mut chars = kind.chars();
    chars
        .next()
        .map(|first| first.to_uppercase().chain(chars).collect())
        .unwrap_or_default()
}

/// Frozen `verify_qt_codegen`: moc/uic/rcc linkage and Qt-major evidence, without a build.
pub fn verify_qt_codegen(request: &QtCodegenRequest) -> QtCodegenOutcome {
    let mut outcome = QtCodegenOutcome::default();
    let root = match request.root.canonicalize() {
        Ok(root) => root,
        Err(error) => {
            root_error(
                &mut outcome,
                "QtCodegenContextError",
                format!(
                    "Qt generated-code project root could not be resolved: {}",
                    exception_name(&error)
                ),
            );
            outcome.mode = "error".into();
            return outcome;
        }
    };
    let request = QtCodegenRequest {
        root: &root,
        source_dirs: request.source_dirs,
        cpp_files: request.cpp_files,
        cpp_headers: request.cpp_headers,
        context: request.context.as_ref().map(|context| QtContext {
            database: context.database,
            has_errors: context.has_errors,
            units: context.units,
        }),
        compiled_sources: request.compiled_sources,
    };
    let inputs = discover_inputs(&request, &mut outcome);
    if !outcome.errors.is_empty() {
        outcome.mode = "error".into();
        return outcome;
    }
    let Some(context) = request.context.as_ref() else {
        if !inputs.is_empty() {
            context_missing(&mut outcome, &inputs);
        }
        return outcome;
    };
    // Frozen `context.project.root != root` cannot occur: the native context is always
    // loaded for this project root.
    if inputs.is_empty() {
        if context.units.iter().any(|unit| qt_relevant(&root, unit)) {
            verify_major_evidence(&request, context.units, &mut outcome);
            outcome.mode = if outcome.errors.is_empty() {
                "exact"
            } else {
                "error"
            }
            .into();
        }
        return outcome;
    }
    if !context.database {
        context_missing(&mut outcome, &inputs);
        return outcome;
    }
    if context.has_errors {
        for item in &inputs {
            append(
                &mut outcome,
                target(
                    item,
                    "ERROR",
                    format!("Qt{}ContextError", title(item.kind)),
                    "Qt generated-code linkage was not checked because compilation context has errors.",
                ),
                &format!("ici.qt.codegen.{}", item.kind),
            );
        }
        outcome.inputs_checked = inputs.len();
        outcome.mode = "error".into();
        return outcome;
    }
    let units: Vec<&CompilationUnit> = context.units.iter().collect();
    let production: BTreeSet<&String> = request.cpp_files.iter().collect();
    let production_units: Vec<&CompilationUnit> = units
        .iter()
        .copied()
        .filter(|unit| production.contains(&unit.source))
        .collect();
    let mut counts = BTreeMap::<(&str, &str), usize>::new();
    for item in &inputs {
        *counts.entry((item.kind, item.stem.as_str())).or_default() += 1;
    }
    for item in &inputs {
        outcome.inputs_checked += 1;
        match item.kind {
            "ui" => outcome.ui_checked += 1,
            "qrc" => outcome.qrc_checked += 1,
            _ => outcome.moc_checked += 1,
        }
        let result = if counts[&(item.kind, item.stem.as_str())] > 1 {
            target(
                item,
                "WARN",
                format!("Qt{}AmbiguousStem", title(item.kind)),
                format!(
                    "Multiple Qt {} inputs share the generated basename stem {}; exact linkage is ambiguous.",
                    item.kind,
                    py_repr(&item.stem)
                ),
            )
        } else {
            linkage(&request, item, &units, &production_units)
        };
        append(
            &mut outcome,
            result,
            &format!("ici.qt.codegen.{}", item.kind),
        );
    }
    verify_major_evidence(&request, context.units, &mut outcome);
    outcome.mode = if outcome.errors.is_empty() {
        "exact"
    } else {
        "error"
    }
    .into();
    outcome
}

fn context_missing(outcome: &mut QtCodegenOutcome, inputs: &[QtInput]) {
    for item in inputs {
        append(
            outcome,
            target(
                item,
                "WARN",
                format!("Qt{}ContextMissing", title(item.kind)),
                "Qt generated-code linkage requires an exact compilation database.",
            ),
            &format!("ici.qt.codegen.{}", item.kind),
        );
    }
    outcome.inputs_checked = inputs.len();
    outcome.mode = "unavailable".into();
}

/// Python `repr()` of a str: single quotes unless the text holds a single quote only.
fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut output = String::from(quote);
    for ch in text.chars() {
        match ch {
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            _ if ch == quote => {
                output.push('\\');
                output.push(ch);
            }
            _ if (ch as u32) < 0x20 || ch as u32 == 0x7f => {
                output.push_str(&format!("\\x{:02x}", ch as u32));
            }
            _ => output.push(ch),
        }
    }
    output.push(quote);
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn masker_blanks_comments_literals_and_raw_strings_but_keeps_lines() {
        let text = "a // Q_OBJECT\nb /* x\ny */ \"Q_\\\"OBJECT\" R\"d(Q_OBJECT)d\" 'c'\n";
        let code = cpp_code(text);
        assert_eq!(code.chars().count(), text.chars().count());
        assert_eq!(py_splitlines(&code).len(), py_splitlines(text).len());
        assert!(!code.contains("Q_OBJECT"));
        assert!(code.starts_with("a "));
        assert!(code.contains("\n  "));
    }

    #[test]
    fn q_object_line_honours_disabled_branches_only() {
        assert_eq!(
            q_object_line("#if 0\nQ_OBJECT\n#else\nQ_OBJECT\n#endif\n"),
            Some(4)
        );
        assert_eq!(q_object_line("#if 1\n#elif 1\nQ_OBJECT\n#endif\n"), None);
        assert_eq!(q_object_line("#ifdef X\nQ_OBJECT\n#endif\n"), Some(2));
        assert_eq!(q_object_line("// Q_OBJECT\n\"Q_OBJECT\"\n"), None);
        assert_eq!(q_object_line("a\rb\r Q_OBJECT\r"), Some(3));
        assert_eq!(q_object_line("#if FALSE\nQ_OBJECT\n#endif\n"), None);
    }

    #[test]
    fn include_names_read_active_directives_and_survive_lone_carriage_returns() {
        assert_eq!(
            include_names("#include \"a.h\"\n// #include \"b.h\"\n  #  include <QObject>\n"),
            vec!["a.h", "QObject"]
        );
        // Frozen crashes here (zip strict); native cuts the masked text at the original lines.
        assert_eq!(include_names("/* a\rb */\n#include \"c.h\"\n"), vec!["c.h"]);
    }

    #[test]
    fn qt_include_names_follow_the_frozen_prefix_rules() {
        for (name, expected) in [
            ("QObject", true),
            ("QtCore/QObject", true),
            ("QtCore", true),
            ("Qt5Compat", true),
            ("Qt", false),
            ("Qtx", false),
            ("Q", false),
            ("Qobject", false),
            ("vector", false),
        ] {
            assert_eq!(is_qt_include(name), expected, "{name}");
        }
    }

    #[test]
    fn unresolvable_project_root_is_the_frozen_context_error_without_a_finding() {
        // Frozen (oracle 6fe3e40) `verify_qt_codegen(Path("/tmp/ici-qt-root-missing-x/proj"),
        // [], [], [], None)`: mode "error", one QtCodegenContextError at ".", no finding.
        let missing = std::env::temp_dir().join("ici-qt-root-missing-x/proj");
        let outcome = verify_qt_codegen(&QtCodegenRequest {
            root: &missing,
            source_dirs: &[],
            cpp_files: &[],
            cpp_headers: &[],
            context: None,
            compiled_sources: &BTreeSet::new(),
        });
        let message = "Qt generated-code project root could not be resolved: FileNotFoundError";
        assert_eq!(outcome.mode, "error");
        assert_eq!(outcome.errors, [message]);
        assert!(outcome.findings.is_empty());
        let [target] = outcome.targets.as_slice() else {
            panic!("one target")
        };
        assert_eq!(
            (
                target.file_path.as_str(),
                target.target_name.as_str(),
                target.status.as_str(),
                target.message.as_str(),
                target.start_line
            ),
            (".", "QtCodegenContextError", "ERROR", message, 1)
        );
    }

    #[test]
    fn python_repr_quotes_like_frozen() {
        assert_eq!(py_repr("form"), "'form'");
        assert_eq!(py_repr("it's"), "\"it's\"");
    }
}
