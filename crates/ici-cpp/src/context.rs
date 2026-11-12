use crate::{
    arguments::{self, ResponseCache},
    input::{read_contained, scoped, strict_json},
    *,
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

pub const MAX_DATABASE_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_DATABASE_ENTRIES: usize = 200_000;

/// Read-only selection. Ambiguous databases and explicit failures remain evidence, never fallback.
pub fn load_compilation_context(root: &Path, request: &ContextRequest) -> CompilationContext {
    load(root, request, false)
}

/// Frozen's legacy context, which keeps a repeated database entry as its own configuration.
pub fn load_legacy_compilation_context(
    root: &Path,
    request: &ContextRequest,
) -> CompilationContext {
    load(root, request, true)
}

fn load(root: &Path, request: &ContextRequest, keep_repeated_entries: bool) -> CompilationContext {
    let mut context = CompilationContext {
        variant: request.variant.clone().unwrap_or_else(|| "default".into()),
        selected_sources: request.selected_sources.clone(),
        ..Default::default()
    };
    let root = match root.canonicalize() {
        Ok(root) => root,
        Err(error) => {
            context
                .diagnostics
                .push(ContextDiagnostic::error("invalid-root", error.to_string()));
            return context;
        }
    };
    let selected = if let Some(database) = &request.database {
        context.origin = "configured".into();
        Some(database.clone())
    } else if let Some(first) = ["compile_commands.json", "build/compile_commands.json"]
        .into_iter()
        .find(|path| keep_repeated_entries && root.join(path).exists())
    {
        // Frozen `_select_database`: the first existing conventional location.
        context.origin = "discovered".into();
        Some(first.to_owned())
    } else {
        let candidates: BTreeSet<_> = std::iter::once("compile_commands.json".to_string())
            .chain(std::iter::once("build/compile_commands.json".into()))
            .chain(request.build_directories.iter().map(|directory| {
                format!("{}/compile_commands.json", directory.trim_end_matches('/'))
            }))
            .filter(|path| root.join(path).exists())
            .collect();
        if candidates.len() > 1 {
            context.origin = "ambiguous".into();
            context.diagnostics.push(ContextDiagnostic::error(
                "ambiguous-database",
                format!(
                    "select a build variant/database explicitly: {}",
                    candidates.into_iter().collect::<Vec<_>>().join(", ")
                ),
            ));
            return context;
        }
        candidates.into_iter().next()
    };
    let Some(selected) = selected else {
        return context;
    };
    if context.origin.is_empty() {
        context.origin = "discovered".into();
    }
    context.database_path = Some(selected.clone());
    let database = match scoped(&root, &root, &selected) {
        Ok((relative, scope, path)) if scope == "project" => {
            context.database_path = Some(relative);
            path
        }
        Ok(_) => {
            context.diagnostics.push(ContextDiagnostic::error(
                "database-outside-project",
                "compile database leaves workspace",
            ));
            return context;
        }
        Err(error) => {
            context
                .diagnostics
                .push(ContextDiagnostic::error(error.code, error.message));
            return context;
        }
    };
    let bytes = match read_contained(&root, &database, MAX_DATABASE_BYTES) {
        Ok(bytes) => bytes,
        Err(error) => {
            context.diagnostics.push(ContextDiagnostic::error(
                format!("database-{}", error.code),
                error.message,
            ));
            return context;
        }
    };
    context.database_digest = Some(ici_domain::sha256(&bytes));
    let json = match strict_json(&bytes) {
        Ok(json) => json,
        Err(error) => {
            context.diagnostics.push(ContextDiagnostic::error(
                "database-malformed",
                error.message,
            ));
            return context;
        }
    };
    let Some(rows) = json.as_array() else {
        context.diagnostics.push(ContextDiagnostic::error(
            "database-not-array",
            "compile database must be a JSON array",
        ));
        return context;
    };
    if rows.len() > MAX_DATABASE_ENTRIES {
        context.diagnostics.push(ContextDiagnostic::error(
            "database-too-many-entries",
            "compile database exceeds 200000 rows",
        ));
        return context;
    }
    context.clang_database = Some(clang_database_view(rows));
    let mut responses = ResponseCache::default();
    let mut argument_count = 0;
    let mut argument_chars = 0;
    for (index, row) in rows.iter().enumerate() {
        match parse_row(
            &root,
            database.parent().unwrap(),
            row,
            index,
            &context.variant,
            &mut responses,
        ) {
            Ok(unit) => {
                argument_count += unit.argv.len();
                argument_chars += unit.argv.iter().map(|s| s.chars().count()).sum::<usize>();
                if argument_count > 1_000_000 || argument_chars > MAX_DATABASE_BYTES {
                    context.diagnostics.push(ContextDiagnostic::error(
                        "database-arguments-too-large",
                        "retained expanded argv exceeds database-wide limit",
                    ));
                    break;
                }
                context.units.push(unit);
            }
            Err(error) => {
                // Frozen `_RowError` carries a source only for the database-wide argument limit;
                // every other refused row is located at the database itself.
                let source = if error.code == "database-arguments-too-large" {
                    row.get("file").and_then(Value::as_str).unwrap_or("")
                } else {
                    ""
                };
                context.diagnostics.push(ContextDiagnostic {
                    source: source.into(),
                    code: error.code,
                    message: error.message,
                    level: "error".into(),
                    entry_index: Some(index),
                });
            }
        }
    }
    let mut response_files = BTreeMap::new();
    for unit in &context.units {
        for input in &unit.response_files {
            response_files.insert(input.path.clone(), input.clone());
        }
    }
    context.response_files = response_files.into_values().collect();
    context
        .units
        .sort_by(|a, b| (&a.source, &a.configuration).cmp(&(&b.source, &b.configuration)));
    if !keep_repeated_entries {
        // The shared context keeps one unit per identical entry; the removed copies stay
        // visible to the one consumer that replays the database row by row (next cpp.tidy).
        let mut kept: Vec<CompilationUnit> = Vec::with_capacity(context.units.len());
        for unit in std::mem::take(&mut context.units) {
            if kept.last() == Some(&unit) {
                context.repeated_units.push(unit);
            } else {
                kept.push(unit);
            }
        }
        context.units = kept;
    }
    context.unity_build = context
        .units
        .iter()
        .any(|u| u.source.contains("/Unity/") || u.source.contains("unity_"));
    let found: BTreeSet<_> = context
        .units
        .iter()
        .map(|unit| unit.source.as_str())
        .collect();
    context.uncovered_sources = request
        .selected_sources
        .iter()
        .filter(|source| is_translation_unit(source) && !found.contains(source.as_str()))
        .cloned()
        .collect();
    for unit in &context.units {
        context
            .source_coverage
            .entry(unit.source.clone())
            .or_default()
            .push(unit.configuration.clone());
    }
    for configurations in context.source_coverage.values_mut() {
        configurations.sort();
        configurations.dedup();
    }
    if context.units.is_empty() {
        context.diagnostics.push(ContextDiagnostic::error(
            "database-empty",
            "no usable compilation units were found",
        ));
    }
    context
}

/// LLVM `JSONCompilationDatabase::parse` (clang 21): every row must be an object whose keys
/// are `directory`, `file`, `command`, `output` (scalar values) or `arguments` (a sequence of
/// scalars), with `file`, `directory` and `command` or `arguments` present; any other row makes
/// clang-tidy refuse the whole database. A JSON number, boolean or null is a YAML scalar too.
pub fn clang_database_view(rows: &[Value]) -> crate::ClangDatabaseView {
    let scalar = |value: &Value| !value.is_array() && !value.is_object();
    let mut view = crate::ClangDatabaseView::default();
    for (index, row) in rows.iter().enumerate() {
        let refuse = |reason: String| Some(format!("entry {index}: {reason}"));
        let Some(object) = row.as_object() else {
            view.unloadable = view
                .unloadable
                .or_else(|| refuse("Expected object.".into()));
            view.keys.push(None);
            continue;
        };
        for (key, value) in object {
            let error = match key.as_str() {
                "arguments"
                    if !value
                        .as_array()
                        .is_some_and(|items| items.iter().all(scalar)) =>
                {
                    Some("arguments is not a sequence of strings")
                }
                "arguments" => None,
                "directory" | "file" | "command" | "output" if !scalar(value) => {
                    Some("Expected string as value.")
                }
                "directory" | "file" | "command" | "output" => None,
                _ => Some("Unknown key"),
            };
            if let Some(error) = error {
                view.unloadable = view
                    .unloadable
                    .or_else(|| refuse(format!("{error} ({key})")));
            }
        }
        for (missing, present) in [
            ("file", object.contains_key("file")),
            (
                "command\" or \"arguments",
                object.contains_key("command") || object.contains_key("arguments"),
            ),
            ("directory", object.contains_key("directory")),
        ] {
            if !present {
                view.unloadable = view
                    .unloadable
                    .or_else(|| refuse(format!("Missing key: \"{missing}\".")));
            }
        }
        view.keys.push(
            match (
                object.get("directory").and_then(Value::as_str),
                object.get("file").and_then(Value::as_str),
            ) {
                (Some(directory), Some(file)) => {
                    Some(clang_remove_dots(&if file.starts_with('/') {
                        file.to_owned()
                    } else {
                        format!("{directory}/{file}")
                    }))
                }
                _ => None,
            },
        );
    }
    view
}

/// LLVM `sys::path::remove_dots(path, /*remove_dot_dot=*/true)` for a POSIX path: drop empty and
/// `.` components, a `..` removes the component before it (dropped at an absolute root, kept
/// at the start of a relative path).
pub fn clang_remove_dots(path: &str) -> String {
    let mut parts: Vec<&str> = vec![];
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." if parts.last().is_some_and(|last| *last != "..") => {
                parts.pop();
            }
            ".." if path.starts_with('/') => {}
            part => parts.push(part),
        }
    }
    let joined = parts.join("/");
    if path.starts_with('/') {
        format!("/{joined}")
    } else {
        joined
    }
}

fn required<'a>(row: &'a Value, key: &str) -> Result<&'a str, CppError> {
    row.get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            CppError::new(
                format!("invalid-{key}"),
                format!("compile entry requires a nonempty {key}"),
            )
        })
}
fn parse_row(
    root: &Path,
    database_parent: &Path,
    row: &Value,
    index: usize,
    variant: &str,
    responses: &mut ResponseCache,
) -> Result<CompilationUnit, CppError> {
    // Frozen row order: a non-object entry, an absent file, an absent directory, then an
    // invalid file/directory.
    if !row.is_object() {
        return Err(CppError::new(
            "invalid-entry",
            "compile entry is not an object",
        ));
    }
    for key in ["file", "directory"] {
        if row.get(key).is_none() {
            return Err(CppError::new(
                format!("missing-{key}"),
                format!("compile entry has no {key} field"),
            ));
        }
    }
    let file = required(row, "file")?;
    let directory_value = required(row, "directory")?;
    let (directory, directory_scope, cwd) = scoped(root, database_parent, directory_value)?;
    if directory_scope != "project" {
        return Err(CppError::new(
            "directory-outside-project",
            "compile cwd leaves workspace",
        ));
    }
    let (source, source_scope, source_path) = scoped(root, &cwd, file)?;
    if source_scope != "project" {
        return Err(CppError::new(
            "source-outside-project",
            "translation unit leaves workspace",
        ));
    }
    if source == "." || source_path.is_dir() {
        return Err(CppError::new(
            "invalid-source-path",
            "translation unit must identify a file",
        ));
    }
    let original_argv = if let Some(arguments) = row.get("arguments") {
        let array = arguments
            .as_array()
            .ok_or_else(|| CppError::new("invalid-arguments", "arguments must be an array"))?;
        array
            .iter()
            .map(|v| {
                v.as_str().map(str::to_string).ok_or_else(|| {
                    CppError::new("invalid-arguments", "arguments must contain strings")
                })
            })
            .collect::<Result<Vec<_>, _>>()?
    } else {
        // Frozen `_parse_command`.
        let command = row
            .get("command")
            .and_then(Value::as_str)
            .filter(|command| !command.is_empty() && !command.contains('\0'))
            .ok_or_else(|| {
                CppError::new(
                    "missing-command",
                    "The compilation entry has neither valid arguments nor a command.",
                )
            })?;
        let parsed = arguments::split_posix(command)?;
        if parsed.is_empty() || parsed.iter().any(String::is_empty) {
            return Err(CppError::new(
                "invalid-command",
                "The compilation command produced an invalid argv.",
            ));
        }
        parsed
    };
    arguments::validate(&original_argv)?;
    let mut diagnostics = vec![];
    let expansion = responses.expand(root, &cwd, &original_argv)?;
    for error in expansion.diagnostics {
        diagnostics.push(ContextDiagnostic::error(error.code, error.message));
    }
    let (argv, response_files) = (expansion.argv, expansion.response_files);
    if !cwd.is_dir() {
        diagnostics.push(ContextDiagnostic::error(
            "missing-directory",
            "compile cwd no longer exists",
        ));
    }
    if !source_path.is_file() {
        diagnostics.push(ContextDiagnostic::error(
            "stale-source",
            "translation unit no longer exists",
        ));
    }
    let mut unit = CompilationUnit {
        source,
        directory,
        original_argv,
        argv,
        compiler: String::new(),
        language: String::new(),
        standard: String::new(),
        output: String::new(),
        target: String::new(),
        variant: variant.into(),
        defines: vec![],
        include_paths: vec![],
        sysroot: String::new(),
        sysroot_scope: String::new(),
        configuration: String::new(),
        response_files,
        diagnostics,
    };
    metadata(root, &cwd, database_parent, row, &mut unit)?;
    // Frozen `_record_include_value`: a configured -I/-isystem/-iquote directory that does not
    // exist is a warning-level unit diagnostic (one per path, in argv order).
    let missing_includes = unit
        .include_paths
        .iter()
        .filter(|path| {
            matches!(path.kind.as_str(), "include" | "system" | "quote") && !path.present
        })
        .count();
    for _ in 0..missing_includes {
        unit.diagnostics.push(ContextDiagnostic {
            level: "warning".into(),
            ..ContextDiagnostic::error(
                "missing-include-dir",
                "A configured compiler include directory does not exist.",
            )
        });
    }
    // Frozen `_parse_row` order: response and directory/source facts, then the source operand,
    // language, standard, definition, include, sysroot and output families.
    unit.diagnostics.sort_by_key(diagnostic_family);
    for diagnostic in &mut unit.diagnostics {
        diagnostic.entry_index = Some(index);
        diagnostic.source = unit.source.clone();
    }
    unit.configuration = ici_domain::sha256(
        ici_domain::canonical_json(
            &json!({"source":unit.source,"directory":unit.directory,"argv":normalized_configuration_arguments(root,&cwd,&unit.argv),"output":unit.output,"variant":unit.variant}),
        )
        .map_err(|e| CppError::new("identity", e.to_string()))?,
    );
    Ok(unit)
}

/// Normalize only path operands and workspace absolute values. External roots, option ordering,
/// compiler choices, literal macro values and the configured variant remain distinct identities.
pub fn normalized_configuration_arguments(root: &Path, cwd: &Path, argv: &[String]) -> Vec<String> {
    let mut result = vec![];
    let mut index = 0;
    const PATH_VALUES: &[&str] = &[
        "-I",
        "-isystem",
        "-idirafter",
        "-iquote",
        "-include-pch",
        "-include",
        "-imacros",
        "-isysroot",
        "--sysroot",
        "-o",
        "-MF",
        "-MJ",
        "-L",
        "-F",
        "-B",
        "-iframework",
        "-resource-dir",
    ];
    const JOINED: &[&str] = &[
        "-include-pch=",
        "-include-pch",
        "-resource-dir=",
        "-resource-dir",
        "--sysroot=",
        "-isystem",
        "-idirafter",
        "-iquote",
        "-isysroot",
        "-iframework",
        "-imacros=",
        "-imacros",
        "-include=",
        "-include",
        "-MF",
        "-MJ",
        "-I",
        "-L",
        "-F",
        "-B",
        "-o",
    ];
    while index < argv.len() {
        let argument = &argv[index];
        if PATH_VALUES.contains(&argument.as_str())
            && let Some(value) = argv.get(index + 1)
        {
            result.extend([argument.clone(), configuration_path(root, cwd, value)]);
            index += 2;
            continue;
        }
        if let Some((prefix, value)) = JOINED.iter().find_map(|prefix| {
            argument
                .strip_prefix(prefix)
                .filter(|value| !value.is_empty())
                .map(|value| (*prefix, value))
        }) {
            result.push(format!("{prefix}{}", configuration_path(root, cwd, value)));
            index += 1;
            continue;
        }
        let normalized = if let Some((prefix, value)) = [
            "-ffile-prefix-map=",
            "-fdebug-prefix-map=",
            "-fmacro-prefix-map=",
        ]
        .iter()
        .find_map(|prefix| argument.strip_prefix(prefix).map(|value| (*prefix, value)))
        {
            let value = value
                .split_once('=')
                .map(|(from, to)| {
                    format!(
                        "{}={}",
                        configuration_absolute(root, cwd, from),
                        configuration_absolute(root, cwd, to)
                    )
                })
                .unwrap_or_else(|| value.into());
            format!("{prefix}{value}")
        } else if argument.starts_with("-Wl,") || argument.starts_with("-Wp,") {
            argument
                .split(',')
                .map(|part| configuration_absolute(root, cwd, part))
                .collect::<Vec<_>>()
                .join(",")
        } else if argument.starts_with('-')
            && let Some((key, value)) = argument.split_once('=')
        {
            format!("{key}={}", configuration_absolute(root, cwd, value))
        } else if index == 0 && argument.contains('/')
            || !argument.starts_with('-')
                && (argument.contains('/') || Path::new(argument).is_absolute())
        {
            configuration_path(root, cwd, argument)
        } else {
            argument.clone()
        };
        result.push(normalized);
        index += 1;
    }
    result
}
fn configuration_absolute(root: &Path, cwd: &Path, value: &str) -> String {
    let (quote, raw) = if value.len() > 1
        && value.starts_with(['\'', '"'])
        && value.as_bytes().first() == value.as_bytes().last()
    {
        (&value[..1], &value[1..value.len() - 1])
    } else {
        ("", value)
    };
    if Path::new(raw).is_absolute() {
        format!("{quote}{}{quote}", configuration_path(root, cwd, raw))
    } else {
        value.into()
    }
}
fn configuration_path(root: &Path, cwd: &Path, value: &str) -> String {
    match scoped(root, cwd, value) {
        Ok((path, scope, _)) if scope == "project" => {
            if path == "." {
                "${workspace}".into()
            } else {
                format!("${{workspace}}/{path}")
            }
        }
        Ok((path, _, _)) => path,
        Err(_) => value.into(),
    }
}

pub fn is_translation_unit(path: &str) -> bool {
    matches!(
        Path::new(path).extension().and_then(|s| s.to_str()),
        Some("c" | "C" | "cc" | "cpp" | "cxx" | "c++" | "m" | "mm")
    )
}
fn language(path: &str) -> &'static str {
    match Path::new(path).extension().and_then(|s| s.to_str()) {
        Some("c") => "c",
        Some("m") => "objective-c",
        Some("mm") => "objective-c++",
        Some("C" | "cc" | "cpp" | "cxx" | "c++" | "h" | "hpp") => "c++",
        _ => "",
    }
}
fn diagnostic_family(diagnostic: &ContextDiagnostic) -> u8 {
    let flag = diagnostic
        .message
        .strip_prefix("The ")
        .and_then(|rest| rest.split_once(' '))
        .map(|(flag, _)| flag);
    match (diagnostic.code.as_str(), flag) {
        ("source-mismatch", _) => 1,
        ("unknown-language", _) | ("missing-flag-value", Some("-x")) => 2,
        ("missing-flag-value", Some("-std" | "/std")) => 3,
        ("invalid-define", _) | ("missing-flag-value", Some("-D" | "/D")) => 4,
        ("missing-include-dir", _)
        | ("missing-flag-value", Some("-I" | "-isystem" | "-iquote" | "/I" | "/external:I")) => 5,
        ("missing-flag-value", Some("--sysroot" | "-isysroot")) => 6,
        ("invalid-output" | "output-outside-project" | "output-mismatch", _)
        | ("missing-flag-value", Some("-o" | "/Fo")) => 7,
        _ => 0,
    }
}

/// A warning-level unit diagnostic (frozen `_compile_db_metadata._diagnostic` default level):
/// recorded and reported, never making the context incomplete.
fn warning(code: &str, message: impl Into<String>) -> ContextDiagnostic {
    ContextDiagnostic {
        level: "warning".into(),
        ..ContextDiagnostic::error(code, message)
    }
}

/// Frozen `_normalize_output_value`: a workspace-relative file path, or "" with a warning for an
/// output outside the project or one naming the project root or a directory.
fn output_path(
    root: &Path,
    base: &Path,
    value: &str,
    diagnostics: &mut Vec<ContextDiagnostic>,
) -> Result<String, CppError> {
    let (path, scope, resolved) = scoped(root, base, value)?;
    if scope != "project" {
        diagnostics.push(warning(
            "output-outside-project",
            "The compilation output is outside the project.",
        ));
        return Ok(String::new());
    }
    if path == "." || resolved.is_dir() {
        diagnostics.push(warning(
            "invalid-output",
            "The compilation output does not identify a file path.",
        ));
        return Ok(String::new());
    }
    Ok(path)
}

/// Frozen `_normalize_output`: the `output` field (refused with a warning unless a nonempty
/// string) and the last `-o` value are normalized against the compile directory; a field that
/// only agrees with `-o` when read from the database directory is taken from there; two
/// different paths are an `output-mismatch` warning and the field wins.
fn normalize_output(
    root: &Path,
    cwd: &Path,
    database_parent: &Path,
    row: &Value,
    argv_output: Option<String>,
    unit: &mut CompilationUnit,
) -> Result<String, CppError> {
    let mut diagnostics = vec![];
    let declared = match row.get("output") {
        None | Some(Value::Null) => None,
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        Some(_) => {
            diagnostics.push(warning(
                "invalid-output",
                "The compilation output field is not a string.",
            ));
            None
        }
    };
    let from_argv = match &argv_output {
        Some(value) if !value.is_empty() => output_path(root, cwd, value, &mut diagnostics)?,
        _ => String::new(),
    };
    let mut declared_diagnostics = vec![];
    let mut from_field = match &declared {
        Some(value) => output_path(root, cwd, value, &mut declared_diagnostics)?,
        None => String::new(),
    };
    if let Some(value) = &declared
        && from_field != from_argv
        && database_parent != cwd
    {
        let mut database_diagnostics = vec![];
        let relative = output_path(root, database_parent, value, &mut database_diagnostics)?;
        if relative == from_argv {
            from_field = relative;
            declared_diagnostics = database_diagnostics;
        }
    }
    diagnostics.extend(declared_diagnostics);
    if !from_field.is_empty() && !from_argv.is_empty() && from_field != from_argv {
        diagnostics.push(warning(
            "output-mismatch",
            "The output field and compiler -o argument identify different paths.",
        ));
    }
    unit.diagnostics.extend(diagnostics);
    Ok(if from_field.is_empty() {
        from_argv
    } else {
        from_field
    })
}

fn metadata(
    root: &Path,
    cwd: &Path,
    database_parent: &Path,
    row: &Value,
    unit: &mut CompilationUnit,
) -> Result<(), CppError> {
    let argv = unit.argv.clone();
    unit.compiler = argv
        .first()
        .or(unit.original_argv.first())
        .map(|s| s.rsplit(['/', '\\']).next().unwrap_or(s).to_string())
        .unwrap_or_default();
    unit.language = if unit.compiler == "c++"
        || unit.compiler.contains("g++")
        || unit.compiler.contains("clang++")
    {
        "c++"
    } else {
        language(&unit.source)
    }
    .into();
    let mut source_language = None;
    let msvc = ["cl", "cl.exe", "clang-cl", "clang-cl.exe"]
        .contains(&unit.compiler.to_ascii_lowercase().as_str());
    let mut output = None;
    let mut i = 1;
    while i < argv.len() {
        let token = &argv[i];
        if token == "--" {
            source_language = Some(unit.language.clone());
            break;
        }
        let mut matched = false;
        for (flag, kind, joined) in [
            ("--sysroot", "sysroot", true),
            ("-isysroot", "sysroot", true),
            ("-isystem", "system", true),
            ("-iquote", "quote", true),
            ("-idirafter", "after", true),
            ("-include", "forced", false),
            ("-imacros", "forced", false),
            ("-std", "standard", true),
            ("-x", "language", true),
            ("-D", "define", true),
            ("-U", "undefine", true),
            ("-I", "include", true),
            ("-o", "output", true),
        ] {
            let value = if token == flag {
                // Frozen `_looks_like_option`: "--" or a dash-led token other than "-".
                if argv
                    .get(i + 1)
                    .is_none_or(|v| v.starts_with('-') && v != "-")
                {
                    // Frozen `_compile_db_metadata` reports a missing value only for the flags
                    // it parses, as a warning (the legacy compile_db engine still fails on it).
                    if !matches!(flag, "-idirafter" | "-include" | "-imacros" | "-U") {
                        unit.diagnostics.push(warning(
                            "missing-flag-value",
                            format!("The {flag} flag has no value."),
                        ));
                    }
                    i += 1;
                    matched = true;
                    break;
                }
                i += 1;
                Some(argv[i].clone())
            } else if joined && token.starts_with(flag) && token.len() > flag.len() {
                if (matches!(flag, "-std" | "--sysroot") && !token.starts_with(&format!("{flag}=")))
                    || (flag == "-o" && token.starts_with("-output"))
                {
                    None
                } else {
                    Some(token[flag.len()..].trim_start_matches('=').to_string())
                }
            } else {
                None
            };
            let Some(value) = value else {
                continue;
            };
            matched = true;
            i += 1;
            match kind {
                "define" | "undefine" => {
                    let (name, value) = value
                        .split_once('=')
                        .map_or((value.as_str(), None), |(a, b)| (a, Some(b.to_string())));
                    if name.is_empty()
                        || !name.chars().enumerate().all(|(i, c)| {
                            c == '_' || c.is_ascii_alphabetic() || (i > 0 && c.is_ascii_digit())
                        })
                    {
                        unit.diagnostics.push(warning(
                            "invalid-define",
                            "A compiler definition has an invalid name.",
                        ));
                    } else {
                        unit.defines.push(Define {
                            name: name.into(),
                            value,
                            undefine: kind == "undefine",
                        });
                    }
                }
                "standard" => unit.standard = value,
                "language" => {
                    // Frozen `_LANGUAGE_ALIASES` (case-folded); anything else is a warning and
                    // keeps the current language.
                    unit.language = match value.to_lowercase().as_str() {
                        "" => unit.language.clone(),
                        "c" | "c-header" => "c".into(),
                        "c++" | "c++-header" => "c++".into(),
                        "objective-c" => "objective-c".into(),
                        "objective-c++" => "objective-c++".into(),
                        _ => {
                            unit.diagnostics.push(warning(
                                "unknown-language",
                                "The -x language value is not recognized.",
                            ));
                            unit.language.clone()
                        }
                    }
                }
                "output" => output = Some(value),
                "sysroot" => {
                    let (path, scope, _) = scoped(root, cwd, &value)?;
                    unit.sysroot = path;
                    unit.sysroot_scope = scope;
                }
                _ => {
                    let (path, scope, resolved) = scoped(root, cwd, &value)?;
                    let present = if kind == "forced" {
                        resolved.is_file()
                    } else {
                        resolved.is_dir()
                    };
                    unit.include_paths.push(IncludePath {
                        path,
                        scope,
                        kind: kind.into(),
                        present,
                    });
                }
            }
            break;
        }
        if matched {
            continue;
        }
        if msvc {
            if let Some(value) = token.strip_prefix("/std:") {
                unit.standard = value.into();
                i += 1;
                continue;
            }
            if token == "/TC" {
                unit.language = "c".into();
                i += 1;
                continue;
            }
            if token == "/TP" {
                unit.language = "c++".into();
                i += 1;
                continue;
            }
            if let Some(value) = token.strip_prefix("/D").filter(|s| !s.is_empty()) {
                let (name, value) = value
                    .split_once('=')
                    .map_or((value, None), |(a, b)| (a, Some(b.into())));
                unit.defines.push(Define {
                    name: name.into(),
                    value,
                    undefine: false,
                });
                i += 1;
                continue;
            }
            if let Some(value) = token.strip_prefix("/I").filter(|s| !s.is_empty()) {
                let (path, scope, resolved) = scoped(root, cwd, value)?;
                unit.include_paths.push(IncludePath {
                    path,
                    scope,
                    kind: "include".into(),
                    present: resolved.is_dir(),
                });
                i += 1;
                continue;
            }
        }
        if [
            "-MF",
            "-MT",
            "-MQ",
            "-MJ",
            "-isysroot",
            "-target",
            "--target",
            "-arch",
            "-resource-dir",
        ]
        .contains(&token.as_str())
        {
            i += 2;
            continue;
        }
        if !token.starts_with('-')
            && !msvc
            && scoped(root, cwd, token).is_ok_and(|(path, _, _)| path == unit.source)
        {
            source_language = Some(unit.language.clone());
        }
        i += 1;
    }
    unit.output = normalize_output(root, cwd, database_parent, row, output, unit)?;
    let parts: Vec<_> = unit.output.split('/').collect();
    for pair in parts.windows(2) {
        if pair[0] == "CMakeFiles"
            && let Some(name) = pair[1].strip_suffix(".dir")
        {
            unit.target = name.into();
            break;
        }
    }
    if !msvc && let Some(language) = source_language {
        unit.language = language;
    }
    unit.diagnostics
        .extend(source_operand_diagnostics(root, cwd, &argv, &unit.source));
    Ok(())
}

/// Frozen `_source_operand_diagnostics`: the operand after the first `-c`/`/c` that is not
/// itself an option must name the unit's `file` field, else one warning-level `source-mismatch`
/// (an unscopable operand counts as a mismatch). Other operands are judged only by the replay,
/// which refuses an extra or missing source operand.
fn source_operand_diagnostics(
    root: &Path,
    cwd: &Path,
    argv: &[String],
    source: &str,
) -> Option<ContextDiagnostic> {
    let candidate = argv.windows(2).find_map(|pair| {
        (matches!(pair[0].as_str(), "-c" | "/c")
            && !(pair[1] == "--" || (pair[1].starts_with('-') && pair[1] != "-")))
            .then_some(pair[1].as_str())
    })?;
    match scoped(root, cwd, candidate) {
        Ok((path, scope, _)) if scope == "project" && path == source => None,
        _ => Some(warning(
            "source-mismatch",
            "The compiler source operand does not match the compilation file field.",
        )),
    }
}
