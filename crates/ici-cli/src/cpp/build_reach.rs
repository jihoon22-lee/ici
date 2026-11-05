//! Frozen next `workspace/compile_units._build_targets` with the textual project readers it uses
//! (`workspace/cmake_project.resolve_cmake_project`, `workspace/qmake_project.
//! resolve_qmake_project`): the targets a linked build's project file reaches, the readers'
//! warnings, and whether the component root is reached at all. Nothing is evaluated or written.
use std::{collections::BTreeSet, path::Path};

/// One target a linked build reaches (frozen `QmakeTarget` / `CmakeTarget`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BuildTarget {
    pub name: String,
    /// The project file that builds it, workspace-relative; "" when a qmake entry is unresolved.
    pub project: String,
    pub depends: Vec<String>,
}

/// A warning-level reader or reach diagnostic (frozen `CompilationDiagnostic`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReachDiagnostic {
    pub code: String,
    pub message: String,
}

fn diagnostic(code: &str, message: String) -> ReachDiagnostic {
    ReachDiagnostic {
        code: code.into(),
        message,
    }
}

/// What the readers returned for one project file.
#[derive(Default)]
struct Resolved {
    targets: Vec<BuildTarget>,
    directories: Vec<String>,
    diagnostics: Vec<ReachDiagnostic>,
    /// Literal `add_test` declarations (cmake only).
    tests: usize,
}

/// Frozen `resolve_cmake_project(...).tests` is not empty: the cmake tree declares a suite.
pub fn cmake_declares_test(root: &Path, definition: &str) -> bool {
    resolve_cmake(root, definition).tests > 0
}

/// Frozen `_build_targets(root, component, linked)`: (targets, diagnostics) in frozen order.
pub fn build_targets(
    root: &Path,
    component_id: &str,
    component_root: &str,
    linked: &[&ici_config::Build],
) -> (Vec<BuildTarget>, Vec<ReachDiagnostic>) {
    let mut targets = vec![];
    let mut diagnostics = vec![];
    // Frozen keeps a dict keyed by system in first-seen order.
    let mut covered: Vec<(String, Vec<String>, Vec<String>)> = vec![];
    for build in linked {
        let system = build.system.as_deref().unwrap_or("explicit");
        let definition = build.project.as_deref().unwrap_or("");
        let resolved = match system {
            "qmake" if !definition.is_empty() => Some(resolve_qmake(root, definition)),
            "cmake" if !definition.is_empty() => Some(resolve_cmake(root, definition)),
            _ => {
                if !definition.is_empty() && !root.join(definition).is_file() {
                    diagnostics.push(diagnostic(
                        "build-definition-missing",
                        format!(
                            "build '{}' ({system}) declares {definition}, which is not on disk",
                            build.id
                        ),
                    ));
                }
                None
            }
        };
        if let Some(resolved) = resolved {
            targets.extend(resolved.targets);
            diagnostics.extend(resolved.diagnostics);
            let slot = match covered.iter().position(|(name, _, _)| name == system) {
                Some(index) => index,
                None => {
                    covered.push((system.to_owned(), vec![], vec![]));
                    covered.len() - 1
                }
            };
            covered[slot].1.extend(resolved.directories);
            covered[slot].2.push(build.id.clone());
        }
    }
    let component_root = match component_root.trim_end_matches('/') {
        "" => ".",
        value => value,
    };
    for (system, directories, build_ids) in covered {
        if directories.is_empty() {
            continue;
        }
        let reached = directories.iter().any(|directory| {
            component_root == directory || component_root.starts_with(&format!("{directory}/"))
        });
        if !reached {
            diagnostics.push(diagnostic(
                &format!("{system}-target-missing"),
                format!(
                    "{component_id}: its root is not reached by the declared root project's tree \
                     ({}) — {system} would not compile its sources",
                    build_ids.join(", ")
                ),
            ));
        }
    }
    (targets, diagnostics)
}

/// The file's first `limit + 1` bytes as text, or None when unreadable; the bool is "longer than
/// the bound" (frozen measured the decoded text). The read is bounded before it happens, and a
/// FIFO/device or a file whose resolved path leaves the project root is refused like an
/// unreadable one (frozen blocked on the first and followed the second), as the `add_test`
/// scan does (`next_plan::read_contained_lists`).
fn read_bounded(root: &Path, relative: &str, limit: usize) -> Option<(String, bool)> {
    let bytes = super::next_plan::read_contained_lists(root, &root.join(relative), limit + 1)?;
    let text = String::from_utf8_lossy(&bytes).into_owned();
    let large = text.len() > limit;
    Some((text, large))
}

/// Python `str.splitlines()` boundaries.
fn split_lines(text: &str) -> Vec<&str> {
    let mut lines = vec![];
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, character)) = chars.next() {
        let boundary = matches!(
            character,
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !boundary {
            continue;
        }
        lines.push(&text[start..index]);
        start = index + character.len_utf8();
        if character == '\r' && chars.peek().is_some_and(|(_, next)| *next == '\n') {
            chars.next();
            start += 1;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Lexical `..`/`.` collapse; the filesystem is not consulted (frozen `_norm`).
fn norm(path: &str) -> String {
    let mut parts: Vec<&str> = vec![];
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                parts.pop();
            }
            part => parts.push(part),
        }
    }
    if parts.is_empty() {
        ".".into()
    } else {
        parts.join("/")
    }
}

fn parent(path: &str) -> String {
    match path.rsplit_once('/') {
        Some((head, _)) if !head.is_empty() => head.to_owned(),
        _ => ".".into(),
    }
}

fn join(base: &str, child: &str) -> String {
    if base == "." || child.starts_with('/') {
        child.to_owned()
    } else {
        format!("{base}/{child}")
    }
}

fn canonical(root: &Path, relative: &str) -> String {
    let path = root.join(relative);
    std::fs::canonicalize(&path)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned()
}

// ---- cmake --------------------------------------------------------------------------------

const CMAKE_MAX_BYTES: usize = 512 * 1024;
const CMAKE_MAX_DEPTH: usize = 16;

fn cmake_literal(token: &str) -> bool {
    !token.is_empty() && !token.contains('$') && !token.contains('(') && !token.contains('"')
}

/// Frozen `_COMMENT_RE`: `#[[ ... ]]` blocks and `#` to end of line.
fn cmake_strip(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(index) = rest.find('#') {
        out.push_str(&rest[..index]);
        let tail = &rest[index..];
        rest = match tail
            .strip_prefix("#[[")
            .and_then(|block| block.find("]]").map(|end| (block, end)))
        {
            Some((block, end)) => &block[end + 2..],
            None => tail.find('\n').map_or("", |end| &tail[end..]),
        };
    }
    out.push_str(rest);
    out
}

/// Frozen `_commands`: `(name, args, conditional)` with at most one nested parenthesis level;
/// commands inside an if/foreach/while/function/macro block are conditional.
fn cmake_commands(text: &str) -> Vec<(String, Vec<String>, bool)> {
    let stripped = cmake_strip(text);
    let bytes = stripped.as_bytes();
    let mut found = vec![];
    let mut depth_blocks = 0usize;
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        let starts = (byte.is_ascii_alphabetic() || byte == b'_')
            && (index == 0
                || !(bytes[index - 1].is_ascii_alphanumeric() || bytes[index - 1] == b'_'));
        if !starts {
            index += 1;
            continue;
        }
        let start = index;
        while index < bytes.len() && (bytes[index].is_ascii_alphanumeric() || bytes[index] == b'_')
        {
            index += 1;
        }
        let name = stripped[start..index].to_ascii_lowercase();
        let mut cursor = index;
        while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
            cursor += 1;
        }
        if cursor >= bytes.len() || bytes[cursor] != b'(' {
            continue;
        }
        // `[^()]*(?:\([^()]*\)[^()]*)*\)`: one nesting level only.
        let mut nested = false;
        let mut end = None;
        for (offset, byte) in bytes[cursor + 1..].iter().enumerate() {
            match byte {
                b'(' if nested => break,
                b'(' => nested = true,
                b')' if nested => nested = false,
                b')' => {
                    end = Some(cursor + 1 + offset);
                    break;
                }
                _ => {}
            }
        }
        let Some(end) = end else {
            continue;
        };
        let args: Vec<String> = stripped[cursor + 1..end]
            .split_whitespace()
            .map(str::to_owned)
            .collect();
        index = end + 1;
        if ["endif", "endforeach", "endwhile", "endfunction", "endmacro"].contains(&name.as_str()) {
            depth_blocks = depth_blocks.saturating_sub(1);
            continue;
        }
        let conditional = depth_blocks > 0;
        let opens = ["if", "foreach", "while", "function", "macro"].contains(&name.as_str());
        found.push((name, args, conditional));
        if opens {
            depth_blocks += 1;
        }
    }
    found
}

fn resolve_cmake(root: &Path, definition: &str) -> Resolved {
    let mut resolved = Resolved::default();
    let mut dependencies: Vec<(String, Vec<String>)> = vec![];
    let mut seen = BTreeSet::new();
    cmake_visit(
        root,
        definition,
        0,
        &mut seen,
        &mut resolved,
        &mut dependencies,
    );
    for target in &mut resolved.targets {
        target.depends = dependencies
            .iter()
            .filter(|(name, _)| *name == target.name)
            .flat_map(|(_, items)| items.clone())
            .collect();
    }
    let mut unique = vec![];
    for directory in std::mem::take(&mut resolved.directories) {
        if !unique.contains(&directory) {
            unique.push(directory);
        }
    }
    resolved.directories = unique;
    resolved
}

fn cmake_visit(
    root: &Path,
    lists: &str,
    depth: usize,
    seen: &mut BTreeSet<String>,
    resolved: &mut Resolved,
    dependencies: &mut Vec<(String, Vec<String>)>,
) {
    if !seen.insert(canonical(root, lists)) {
        return;
    }
    if depth > CMAKE_MAX_DEPTH {
        resolved.diagnostics.push(diagnostic(
            "cmake-subdirs-depth",
            format!(
                "{lists}: add_subdirectory nesting deeper than {CMAKE_MAX_DEPTH} is not followed"
            ),
        ));
        return;
    }
    let Some((text, large)) = read_bounded(root, lists, CMAKE_MAX_BYTES) else {
        resolved.diagnostics.push(diagnostic(
            "cmake-project-missing",
            format!("{lists}: project file is not readable"),
        ));
        return;
    };
    if large {
        resolved.diagnostics.push(diagnostic(
            "cmake-project-large",
            format!(
                "{lists}: larger than {} KiB — read truncated",
                CMAKE_MAX_BYTES / 1024
            ),
        ));
    }
    let base = parent(&norm_lexical(lists));
    resolved.directories.push(base.clone());
    for (name, args, _conditional) in cmake_commands(&text) {
        match name.as_str() {
            "add_subdirectory" if !args.is_empty() => {
                let subdir = &args[0];
                if !cmake_literal(subdir) {
                    resolved.diagnostics.push(diagnostic(
                        "cmake-subdir-unresolved",
                        format!(
                            "{lists}: add_subdirectory({subdir}) is not a literal path — the \
                             expansion is not guessed"
                        ),
                    ));
                    continue;
                }
                let child = format!("{}/CMakeLists.txt", norm_lexical(&join(&base, subdir)));
                if !root.join(&child).is_file() {
                    resolved.diagnostics.push(diagnostic(
                        "cmake-subdir-unresolved",
                        format!(
                            "{lists}: add_subdirectory({subdir}) has no CMakeLists.txt to follow"
                        ),
                    ));
                    continue;
                }
                cmake_visit(root, &child, depth + 1, seen, resolved, dependencies);
            }
            "add_executable" | "add_library" if !args.is_empty() => {
                if !cmake_literal(&args[0]) {
                    resolved.diagnostics.push(diagnostic(
                        "cmake-target-unresolved",
                        format!("{lists}: target name {} is not literal", args[0]),
                    ));
                    continue;
                }
                resolved.targets.push(BuildTarget {
                    name: args[0].clone(),
                    project: lists.to_owned(),
                    depends: vec![],
                });
            }
            "add_test" if !args.is_empty() => {
                let test = args
                    .iter()
                    .position(|arg| arg == "NAME")
                    .and_then(|index| args.get(index + 1))
                    .unwrap_or(&args[0]);
                if cmake_literal(test) {
                    resolved.tests += 1;
                } else {
                    resolved.diagnostics.push(diagnostic(
                        "cmake-test-unresolved",
                        format!("{lists}: test name {test} is not literal"),
                    ));
                }
            }
            "add_dependencies" if args.len() > 1 && cmake_literal(&args[0]) => {
                let items: Vec<String> = args[1..]
                    .iter()
                    .filter(|item| cmake_literal(item))
                    .cloned()
                    .collect();
                match dependencies.iter_mut().find(|(name, _)| *name == args[0]) {
                    Some((_, existing)) => existing.extend(items),
                    None => dependencies.push((args[0].clone(), items)),
                }
            }
            "include" if !args.is_empty() => {
                resolved.diagnostics.push(diagnostic(
                    "cmake-include-unresolved",
                    format!(
                        "{lists}: include({}) is not followed — module contents are not evaluated",
                        args[0]
                    ),
                ));
            }
            _ => {}
        }
    }
}

/// `PurePosixPath(str(base / subdir))`: drops `.` parts and duplicate slashes but keeps `..`.
fn norm_lexical(path: &str) -> String {
    let parts: Vec<&str> = path
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    let joined = parts.join("/");
    if path.starts_with('/') {
        format!("/{joined}")
    } else if joined.is_empty() {
        ".".into()
    } else {
        joined
    }
}

// ---- qmake --------------------------------------------------------------------------------

const QMAKE_MAX_BYTES: usize = 512 * 1024;
const QMAKE_MAX_DEPTH: usize = 8;
const OPERATORS: [&str; 5] = ["~=", "-=", "+=", "*=", "="];

/// Frozen `_logical_lines`: `\` continues, `#` comments to end of line.
fn logical_lines(text: &str) -> Vec<String> {
    let mut lines = vec![];
    let mut current = String::new();
    for raw in split_lines(text) {
        let line = raw.split('#').next().unwrap_or("").trim_end();
        if let Some(head) = line.strip_suffix('\\') {
            current.push_str(head);
            current.push(' ');
            continue;
        }
        current.push_str(line);
        if !current.trim().is_empty() {
            lines.push(current.trim().to_owned());
        }
        current.clear();
    }
    if !current.trim().is_empty() {
        lines.push(current.trim().to_owned());
    }
    lines
}

/// Frozen `_split_statement`: `[scope:]VAR OP value` -> (var, op, value, conditional).
fn split_statement(line: &str) -> Option<(String, &'static str, String, bool)> {
    let mut conditional = false;
    let mut statement = line.to_owned();
    if let Some((scope, rest)) = line.split_once(':')
        && !scope.trim().is_empty()
        && rest.contains('=')
    {
        conditional = true;
        statement = rest.trim().to_owned();
    }
    for op in OPERATORS {
        if let Some((var, value)) = statement.split_once(op) {
            let var = var.trim();
            if !var.is_empty() && !var.contains([' ', '\t']) {
                return Some((var.to_owned(), op, value.trim().to_owned(), conditional));
            }
        }
    }
    None
}

fn resolve_qmake(root: &Path, definition: &str) -> Resolved {
    let mut resolved = Resolved::default();
    let mut seen = BTreeSet::new();
    qmake_visit(root, definition, 0, &mut seen, &mut resolved);
    let mut unique = vec![];
    for directory in std::mem::take(&mut resolved.directories) {
        if !unique.contains(&directory) {
            unique.push(directory);
        }
    }
    resolved.directories = unique;
    resolved
}

fn qmake_visit(
    root: &Path,
    project: &str,
    depth: usize,
    seen: &mut BTreeSet<String>,
    resolved: &mut Resolved,
) {
    if !seen.insert(canonical(root, project)) {
        return;
    }
    if depth > QMAKE_MAX_DEPTH {
        resolved.diagnostics.push(diagnostic(
            "qmake-subdirs-depth",
            format!("{project}: SUBDIRS nesting deeper than {QMAKE_MAX_DEPTH} is not followed"),
        ));
        return;
    }
    let Some((text, large)) = read_bounded(root, project, QMAKE_MAX_BYTES) else {
        resolved.diagnostics.push(diagnostic(
            "qmake-project-missing",
            format!("{project}: project file is not readable"),
        ));
        return;
    };
    if large {
        resolved.diagnostics.push(diagnostic(
            "qmake-project-large",
            format!(
                "{project}: larger than {} KiB — read truncated",
                QMAKE_MAX_BYTES / 1024
            ),
        ));
    }
    let base = parent(&norm_lexical(project));
    resolved.directories.push(base.clone());
    let lines = logical_lines(&text);
    let mut entries: Vec<(String, &str)> = vec![];
    for line in &lines {
        if let Some((var, op, value, _)) = split_statement(line)
            && var == "SUBDIRS"
        {
            for token in value.split_whitespace() {
                entries.push((token.to_owned(), op));
            }
        }
    }
    let names: BTreeSet<&str> = entries.iter().map(|(name, _)| name.as_str()).collect();
    let mut modifiers: Vec<(String, String, Vec<String>)> = vec![];
    for line in &lines {
        let Some((var, _, value, _)) = split_statement(line) else {
            continue;
        };
        let Some((name, modifier)) = var.rsplit_once('.') else {
            continue;
        };
        if !names.contains(name) || !["subdir", "file", "depends", "target"].contains(&modifier) {
            continue;
        }
        let values: Vec<String> = value.split_whitespace().map(str::to_owned).collect();
        match modifiers
            .iter_mut()
            .find(|(n, m, _)| n == name && m == modifier)
        {
            Some(slot) => slot.2 = values,
            None => modifiers.push((name.to_owned(), modifier.to_owned(), values)),
        }
    }
    let modifier = |name: &str, key: &str| -> Option<Vec<String>> {
        modifiers
            .iter()
            .find(|(n, m, _)| n == name && m == key)
            .map(|(_, _, values)| values.clone())
    };
    let removed: BTreeSet<String> = entries
        .iter()
        .filter(|(_, op)| *op == "-=")
        .map(|(name, _)| name.clone())
        .collect();
    for (name, op) in &entries {
        if *op == "-=" || removed.contains(name) {
            continue;
        }
        let depends: Vec<String> = modifier(name, "depends")
            .unwrap_or_default()
            .into_iter()
            .filter(|item| !OPERATORS.contains(&item.as_str()))
            .collect();
        if name.contains("$$") || name.contains("${") {
            resolved.diagnostics.push(diagnostic(
                "qmake-var-unresolved",
                format!(
                    "{name}: a variable reference is not resolved — qmake's own expansion \
                     decides this target"
                ),
            ));
            continue;
        }
        let file = modifier(name, "file").filter(|values| !values.is_empty());
        let target = if name.ends_with(".pro") || file.is_some() {
            let declared = file.map_or_else(|| name.clone(), |values| values[0].clone());
            BuildTarget {
                name: name.clone(),
                project: norm(&join(&base, &declared)),
                depends,
            }
        } else {
            let directory = modifier(name, "subdir")
                .and_then(|values| values.first().cloned())
                .unwrap_or_else(|| name.clone());
            let directory = norm(&join(&base, &directory));
            let project = project_in(root, &directory, name, &mut resolved.diagnostics);
            BuildTarget {
                name: name.clone(),
                project,
                depends,
            }
        };
        let follow = target.project.clone();
        resolved.targets.push(target);
        if !follow.is_empty() {
            qmake_visit(root, &follow, depth + 1, seen, resolved);
        }
    }
}

/// Frozen `_project_in`: `<dir>/<dirname>.pro`, else the single `.pro` of the directory.
fn project_in(
    root: &Path,
    directory: &str,
    name: &str,
    diagnostics: &mut Vec<ReachDiagnostic>,
) -> String {
    let dirname = directory.rsplit('/').next().unwrap_or(directory);
    let conventional = format!("{directory}/{dirname}.pro");
    if root.join(&conventional).is_file() {
        return conventional;
    }
    let mut projects: Vec<String> = std::fs::read_dir(root.join(directory))
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .map(|entry| entry.file_name().to_string_lossy().into_owned())
                .filter(|file| file.ends_with(".pro"))
                .collect()
        })
        .unwrap_or_default();
    projects.sort();
    match projects.len() {
        1 => format!("{directory}/{}", projects[0]),
        0 => {
            diagnostics.push(diagnostic(
                "qmake-subdir-missing",
                format!("{name}: {directory}/ holds no .pro — qmake would skip this target"),
            ));
            String::new()
        }
        count => {
            diagnostics.push(diagnostic(
                "qmake-subdir-ambiguous",
                format!(
                    "{name}: {directory}/ holds {count} .pro files — qmake's choice cannot be \
                     reproduced without evaluating it"
                ),
            ));
            String::new()
        }
    }
}

/// Frozen `workspace/test_suites._qmake_suites`: each SUBDIRS target whose `.pro` names
/// `QT += testlib` or `CONFIG += testcase|test` (first 256 KiB + 1 bytes, UTF-8 with
/// replacement) is one QTest suite; its binary is `TARGET=<name>` (frozen's exact pattern,
/// no space after `=`) or the `.pro` basename. Returns (target name, binary) pairs.
pub fn qmake_test_binaries(root: &Path, definition: &str) -> Vec<(String, String)> {
    use std::io::Read;
    use std::sync::LazyLock;
    static TARGET: LazyLock<regex::Regex> = LazyLock::new(|| {
        regex::Regex::new(r"(?m)^\s*TARGET\s*=(?P<name>[A-Za-z0-9_.-]+)\s*$").expect("pattern")
    });
    static TESTLIB: LazyLock<regex::Regex> =
        LazyLock::new(|| regex::Regex::new(r"(?m)^\s*QT\s*\+=?.*\btestlib\b").expect("pattern"));
    static TESTCASE: LazyLock<regex::Regex> = LazyLock::new(|| {
        regex::Regex::new(r"(?m)^\s*CONFIG\s*\+=?.*\b(?:testcase|test)\b").expect("pattern")
    });
    resolve_qmake(root, definition)
        .targets
        .into_iter()
        .filter(|target| !target.project.is_empty())
        .filter_map(|target| {
            let mut bytes = vec![];
            std::fs::File::open(root.join(&target.project))
                .ok()?
                .take(256 * 1024 + 1)
                .read_to_end(&mut bytes)
                .ok()?;
            let text = String::from_utf8_lossy(&bytes);
            if !TESTLIB.is_match(&text) && !TESTCASE.is_match(&text) {
                return None;
            }
            let binary = TARGET.captures(&text).map_or_else(
                || {
                    Path::new(&target.project)
                        .file_stem()
                        .map(|stem| stem.to_string_lossy().into_owned())
                        .unwrap_or_default()
                },
                |captures| captures["name"].to_owned(),
            );
            Some((target.name, binary))
        })
        .collect()
}
