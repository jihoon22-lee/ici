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
            Err(error) => context.diagnostics.push(ContextDiagnostic {
                code: error.code,
                message: error.message,
                level: "error".into(),
                entry_index: Some(index),
                source: row.get("file").and_then(Value::as_str).unwrap_or("").into(),
            }),
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
    context.units.dedup();
    context.unity_build = context
        .units
        .iter()
        .any(|u| u.source.contains("/Unity/") || u.source.contains("unity_"));
    if !context.unity_build {
        let found: BTreeSet<_> = context.units.iter().map(|u| u.source.as_str()).collect();
        context.uncovered_sources = request
            .selected_sources
            .iter()
            .filter(|source| is_translation_unit(source) && !found.contains(source.as_str()))
            .cloned()
            .collect();
    }
    if context.units.is_empty() {
        context.diagnostics.push(ContextDiagnostic::error(
            "database-empty",
            "no usable compilation units were found",
        ));
    }
    context
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
    let directory_value = required(row, "directory")?;
    let file = required(row, "file")?;
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
        arguments::split_posix(required(row, "command")?)?
    };
    arguments::validate(&original_argv)?;
    let mut diagnostics = vec![];
    let (argv, response_files) = match responses.expand(root, &cwd, &original_argv) {
        Ok(value) => value,
        Err(error) => {
            diagnostics.push(ContextDiagnostic::error(error.code, error.message));
            (vec![], vec![])
        }
    };
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
    metadata(root, &cwd, row, &mut unit)?;
    for diagnostic in &mut unit.diagnostics {
        diagnostic.entry_index = Some(index);
        diagnostic.source = unit.source.clone();
    }
    unit.configuration = ici_domain::sha256(
        ici_domain::canonical_json(
            &json!({"directory":unit.directory,"argv":unit.argv,"output":unit.output}),
        )
        .map_err(|e| CppError::new("identity", e.to_string()))?,
    );
    Ok(unit)
}

pub fn is_translation_unit(path: &str) -> bool {
    matches!(
        Path::new(path).extension().and_then(|s| s.to_str()),
        Some("c" | "cc" | "cpp" | "cxx" | "m" | "mm")
    )
}
fn language(path: &str) -> &'static str {
    match Path::new(path).extension().and_then(|s| s.to_str()) {
        Some("c") => "c",
        Some("m") => "objective-c",
        Some("mm") => "objective-c++",
        Some("cc" | "cpp" | "cxx" | "h" | "hpp") => "c++",
        _ => "",
    }
}
fn metadata(
    root: &Path,
    cwd: &Path,
    row: &Value,
    unit: &mut CompilationUnit,
) -> Result<(), CppError> {
    let argv = unit.argv.clone();
    unit.compiler = argv
        .first()
        .or(unit.original_argv.first())
        .map(|s| s.rsplit(['/', '\\']).next().unwrap_or(s).to_string())
        .unwrap_or_default();
    unit.language = language(&unit.source).into();
    let msvc = ["cl", "cl.exe", "clang-cl", "clang-cl.exe"]
        .contains(&unit.compiler.to_ascii_lowercase().as_str());
    let mut output = None;
    let mut operands = vec![];
    let mut i = 1;
    while i < argv.len() {
        let token = &argv[i];
        if token == "--" {
            operands.extend(argv[i + 1..].iter().cloned());
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
                if argv.get(i + 1).is_none_or(|v| v.starts_with('-')) {
                    unit.diagnostics.push(ContextDiagnostic::error(
                        "missing-flag-value",
                        format!("{flag} has no value"),
                    ));
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
                        unit.diagnostics.push(ContextDiagnostic::error(
                            "invalid-define",
                            "compiler macro has invalid name",
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
                    unit.language = match value.as_str() {
                        "c" | "c-header" => "c".into(),
                        "c++" | "c++-header" => "c++".into(),
                        "objective-c" | "objective-c++" => value,
                        _ => {
                            unit.diagnostics.push(ContextDiagnostic::error(
                                "unknown-language",
                                "unsupported compiler language",
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
        if !token.starts_with('-') && !msvc {
            operands.push(token.clone());
        }
        i += 1;
    }
    if let Some(raw) = row
        .get("output")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or(output)
    {
        let (path, scope, _) = scoped(root, cwd, &raw)?;
        if scope != "project" {
            unit.diagnostics.push(ContextDiagnostic::error(
                "output-outside-project",
                "compile output escapes workspace",
            ));
        } else {
            unit.output = path;
        }
    }
    let parts: Vec<_> = unit.output.split('/').collect();
    for pair in parts.windows(2) {
        if pair[0] == "CMakeFiles"
            && let Some(name) = pair[1].strip_suffix(".dir")
        {
            unit.target = name.into();
            break;
        }
    }
    let sources: Vec<_> = operands
        .iter()
        .filter(|p| is_translation_unit(p))
        .filter_map(|p| scoped(root, cwd, p).ok())
        .map(|v| v.0)
        .collect();
    if !sources.contains(&unit.source) {
        unit.diagnostics.push(ContextDiagnostic::error(
            "source-operand-mismatch",
            "argv does not name the declared translation unit",
        ));
    }
    if sources.iter().any(|s| s != &unit.source) {
        unit.diagnostics.push(ContextDiagnostic::error(
            "multiple-source-operands",
            "compile argv contains another translation unit",
        ));
    }
    Ok(())
}
