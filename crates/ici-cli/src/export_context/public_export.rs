//! Public v1 compilation observations. No process execution, filesystem discovery or raw argv.
use ici_cpp::{CompilationContext, CompilationUnit, ContextDiagnostic};
use ici_domain::{canonical_json, sha256};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path, PathBuf},
};

const REDACTED: &str = "***REDACTED***";
pub const MAX_EXPORT_BYTES: usize = 32 * 1024 * 1024;
#[derive(Clone, Debug)]
pub struct ExportMetadata {
    pub backend: Option<String>,
    pub name: String,
    pub version: String,
    pub project_type: String,
    pub generator: Option<String>,
}
fn digest(value: &Value) -> Result<String, String> {
    canonical_json(value)
        .map(|text| sha256(text.as_bytes()))
        .map_err(|error| error.to_string())
}
fn text(value: &str) -> String {
    ici_execution::redaction::redact_text(value)
}
fn lexical(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for item in path.components() {
        match item {
            Component::CurDir => (),
            Component::ParentDir => {
                result.pop();
            }
            _ => result.push(item.as_os_str()),
        }
    }
    result
}
fn path(value: &str, root: &Path) -> String {
    if value.is_empty() {
        return String::new();
    }
    if value.contains('\\') || value.as_bytes().get(1) == Some(&b':') {
        return "[external]".into();
    }
    let absolute = lexical(&root.join(value));
    match absolute.strip_prefix(root) {
        Ok(relative) if relative.as_os_str().is_empty() => ".".into(),
        Ok(relative) => text(&relative.to_string_lossy()),
        Err(_) => "[external]".into(),
    }
}
fn safe_value(value: &str, root: &Path, directory: &str) -> String {
    if !value.contains('/') && !value.contains('\\') {
        return text(value);
    }
    let (prefix, candidate) = value
        .split_once('=')
        .filter(|(_, right)| right.contains('/') || right.contains('\\'))
        .map_or((None, value), |(left, right)| (Some(left), right));
    let quote = candidate.as_bytes().first().copied().filter(|quote| {
        matches!(quote, b'\'' | b'"') && candidate.as_bytes().last() == Some(quote)
    });
    let raw = if quote.is_some() {
        &candidate[1..candidate.len() - 1]
    } else {
        candidate
    };
    let safe = path(&root.join(directory).join(raw).to_string_lossy(), root);
    let safe = if let Some(quote) = quote {
        format!("{}{safe}{}", quote as char, quote as char)
    } else {
        safe
    };
    prefix.map_or_else(|| safe.clone(), |prefix| format!("{}={safe}", text(prefix)))
}
fn secret(name: &str) -> bool {
    let name = name.to_ascii_lowercase().replace(['-', '_'], "");
    [
        "password",
        "passwd",
        "secret",
        "apikey",
        "accesskey",
        "authtoken",
        "token",
    ]
    .iter()
    .any(|needle| name.contains(needle))
}
fn scalar(value: &str, extra: &str, limit: usize) -> String {
    if value.len() <= limit
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || extra.as_bytes().contains(&byte))
    {
        text(value)
    } else {
        REDACTED.into()
    }
}
fn diagnostic(value: &ContextDiagnostic, root: &Path) -> Value {
    // Diagnostic prose can contain arbitrary compiler host paths; publish locations separately.
    let message = text(&value.message)
        .split_whitespace()
        .map(|word| {
            if word.contains('/') || word.contains('\\') {
                "[path]".to_string()
            } else {
                word.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join(" ");
    json!({"code":scalar(&value.code,"+_.-",128),"entry_index":value.entry_index,"level":value.level,"message":message,"source":path(&value.source,root)})
}
fn name(value: &str) -> &str {
    value.rsplit(['/', '\\']).next().unwrap_or(value)
}
fn stem(value: &str) -> String {
    let mut value = name(value).to_ascii_lowercase();
    if value.ends_with(".exe") {
        value.truncate(value.len() - 4);
    }
    if let Some((base, version)) = value.rsplit_once('-')
        && !version.is_empty()
        && version
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte == b'.')
    {
        return base.into();
    }
    value
}
fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().enumerate().all(|(index, byte)| {
            byte == b'_' || byte.is_ascii_alphabetic() || index > 0 && byte.is_ascii_digit()
        })
}
fn compiler(unit: &CompilationUnit, root: &Path) -> (Value, usize, bool) {
    let argv = &unit.argv;
    let mut index = 0;
    let mut wrappers = vec![];
    if argv.first().is_some_and(|arg| stem(arg) == "env") {
        wrappers.push("env".to_string());
        index += 1;
        while argv
            .get(index)
            .and_then(|arg| arg.split_once('='))
            .is_some_and(|(key, _)| identifier(key))
        {
            index += 1;
        }
    }
    while argv.get(index).is_some_and(|arg| {
        matches!(
            stem(arg).as_str(),
            "ccache" | "distcc" | "icecc" | "sccache"
        )
    }) {
        wrappers.push(stem(&argv[index]));
        index += 1;
    }
    let raw = argv
        .get(index)
        .map(String::as_str)
        .unwrap_or(&unit.compiler);
    let family = match stem(raw).as_str() {
        "c++" | "c89" | "c99" | "cc" | "gcc" | "g++" => "gcc",
        "clang" | "clang++" => "clang",
        "clang-cl" => "clang-cl",
        "cl" => "msvc",
        _ => "unknown",
    };
    let compiler_path = if raw.contains('/') || raw.contains('\\') {
        safe_value(raw, root, &unit.directory)
    } else {
        scalar(raw, "+_.-", 128)
    };
    (
        json!({"family":family,"name":scalar(name(raw),"+_.-",128),"path":compiler_path,"wrappers":wrappers}),
        index,
        matches!(family, "msvc" | "clang-cl"),
    )
}
const VALUE_OPTIONS: &[&str] = &[
    "--sysroot",
    "--target",
    "-D",
    "-I",
    "-U",
    "-isystem",
    "-isysroot",
    "-iquote",
    "-std",
    "-target",
    "-x",
    "/D",
    "/I",
    "/U",
    "/external:I",
    "/imsvc",
];
const JOINED: &[&str] = &[
    "/external:I",
    "-isystem",
    "-isysroot",
    "-iquote",
    "/imsvc",
    "--sysroot=",
    "--target=",
    "-target=",
    "/clang:--target=",
    "/clang:-target=",
    "/std:",
    "-std=",
    "-D",
    "-I",
    "-U",
    "/D",
    "/I",
    "/U",
    "-x",
];
const OP_VALUE: &[&str] = &["-MF", "-MQ", "-MT", "-o", "/Fo"];
const OP_FLAGS: &[&str] = &[
    "-c",
    "-M",
    "-MD",
    "-MM",
    "-MMD",
    "-MP",
    "-MG",
    "/c",
    "/showIncludes",
];
fn joined<'a>(token: &str, prefixes: &'a [&str]) -> Option<&'a str> {
    prefixes
        .iter()
        .copied()
        .find(|prefix| token.starts_with(prefix) && token.len() > prefix.len())
}
fn option_name(token: &str) -> String {
    let candidate = token
        .find(['=', ':'])
        .filter(|index| *index > 0)
        .map(|index| &token[..=index])
        .or_else(|| joined(token, JOINED))
        .or_else(|| joined(token, OP_VALUE))
        .unwrap_or(token);
    let valid = (candidate.starts_with('-') || candidate.starts_with('/'))
        && candidate
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-+_.=:/".contains(&byte))
        && !candidate[1..].contains('/')
        && candidate.len() <= 128;
    if valid {
        candidate.into()
    } else {
        REDACTED.into()
    }
}
// Invocation hashes retain the v1 normalization contract. Unknown values are never emitted.
fn argv_path(value: &str, root: &Path) -> String {
    let quote = value.as_bytes().first().copied().filter(|quote| {
        matches!(quote, b'\'' | b'"') && value.len() >= 2 && value.as_bytes().last() == Some(quote)
    });
    let raw = if quote.is_some() {
        &value[1..value.len() - 1]
    } else {
        value
    };
    let absolute = Path::new(raw).is_absolute()
        || raw.as_bytes().get(1) == Some(&b':')
            && raw
                .as_bytes()
                .get(2)
                .is_some_and(|byte| matches!(byte, b'/' | b'\\'))
        || raw.starts_with("\\\\");
    if !absolute {
        return text(value);
    }
    let safe = path(raw, root);
    if let Some(quote) = quote {
        format!("{}{safe}{}", quote as char, quote as char)
    } else {
        safe
    }
}
fn argv_assignment(value: &str, root: &Path) -> String {
    let safe = argv_path(value, root);
    if safe != text(value) {
        return safe;
    }
    if let Some((left, right)) = value.split_once('=') {
        return format!("{}={}", argv_path(left, root), argv_path(right, root));
    }
    text(value)
}
fn safe_argv(unit: &CompilationUnit, root: &Path) -> Vec<String> {
    const PATH_FLAGS: &[&str] = &[
        "-I",
        "-B",
        "-L",
        "-include",
        "-include-pch",
        "-imacros",
        "-isystem",
        "-iquote",
        "-isysroot",
        "--sysroot",
        "-o",
        "-fmodule-file",
        "-resource-dir",
    ];
    const PATH_PREFIXES: &[&str] = &[
        "-fmodule-file=",
        "-fdebug-compilation-dir=",
        "-fdebug-prefix-map=",
        "-ffile-prefix-map=",
        "-fmacro-prefix-map=",
        "-include-pch=",
        "-include-pch",
        "-resource-dir=",
        "-resource-dir",
        "-imacros=",
        "-imacros",
        "-include=",
        "-include",
        "--sysroot=",
        "-isystem",
        "-iquote",
        "-isysroot",
        "-fmodule-file",
        "-B",
        "-L",
        "-I",
        "-o",
    ];
    let mut result = vec![];
    let mut hide_next = false;
    let mut path_next = false;
    for token in &unit.argv {
        let safe = if hide_next {
            hide_next = false;
            REDACTED.into()
        } else if path_next {
            path_next = false;
            argv_path(token, root)
        } else if [
            "--api-key",
            "--api_key",
            "--password",
            "--passwd",
            "--secret",
            "--token",
        ]
        .contains(&token.to_ascii_lowercase().as_str())
        {
            hide_next = true;
            text(token)
        } else if PATH_FLAGS.contains(&token.as_str()) {
            path_next = true;
            text(token)
        } else if let Some(body) = token.strip_prefix("-D").filter(|body| !body.is_empty()) {
            if let Some((key, value)) = body.split_once('=') {
                format!(
                    "-D{}={}",
                    text(key),
                    if secret(key) {
                        REDACTED.into()
                    } else {
                        argv_assignment(value, root)
                    }
                )
            } else {
                text(token)
            }
        } else if token.starts_with("-Wl,") {
            token
                .split(',')
                .map(|item| argv_assignment(item, root))
                .collect::<Vec<_>>()
                .join(",")
        } else if let Some(value) = token.strip_prefix('@').filter(|value| !value.is_empty()) {
            format!("@{}", argv_path(value, root))
        } else if let Some(prefix) = joined(token, PATH_PREFIXES) {
            format!("{prefix}{}", argv_assignment(&token[prefix.len()..], root))
        } else {
            argv_path(&text(token), root)
        };
        result.push(safe);
    }
    result
}
fn invocation(
    unit: &CompilationUnit,
    root: &Path,
    start: usize,
    msvc: bool,
) -> Result<Value, String> {
    let safe = safe_argv(unit, root);
    let mut unmodeled = vec![];
    let mut index = start + 1;
    let mut triple = None;
    for (position, token) in unit.argv.iter().enumerate() {
        if let Some(value) = [
            "--target=",
            "-target=",
            "/clang:--target=",
            "/clang:-target=",
        ]
        .iter()
        .find_map(|prefix| token.strip_prefix(prefix))
        .or_else(|| {
            matches!(token.as_str(), "--target" | "-target")
                .then(|| unit.argv.get(position + 1).map(String::as_str))
                .flatten()
        }) {
            triple = Some(scalar(value, "+_.:-", 128));
            break;
        }
    }
    while index < unit.argv.len() {
        let token = &unit.argv[index];
        if token == "--" {
            break;
        }
        if VALUE_OPTIONS.contains(&token.as_str()) || OP_VALUE.contains(&token.as_str()) {
            index += 2;
            continue;
        }
        if joined(token, JOINED).is_some()
            || joined(token, OP_VALUE).is_some()
            || OP_FLAGS.contains(&token.as_str())
            || token.starts_with("-Wl,")
        {
            index += 1;
            continue;
        }
        if token.starts_with('-') || msvc && token.starts_with('/') {
            unmodeled.push(json!({"name":option_name(token),"order":unmodeled.len(),"token_digest":digest(&json!(safe[index]))?}));
        }
        index += 1;
    }
    Ok(
        json!({"command_style":if msvc {"windows"} else {"posix"},"digest":digest(&json!(safe))?,"source":"normalized","target_triple":triple,"unmodeled_options":unmodeled}),
    )
}
fn unit(value: &CompilationUnit, root: &Path) -> Result<Value, String> {
    let (compiler, index, msvc) = compiler(value, root);
    let invocation = invocation(value, root, index, msvc)?;
    let definitions = value
        .defines
        .iter()
        .filter(|define| !define.undefine)
        .map(|define| {
            let safe = define.value.as_ref().map(|raw| {
                if secret(&define.name) {
                    REDACTED.into()
                } else {
                    safe_value(raw, root, &value.directory)
                }
            });
            let state = match (&define.value, &safe) {
                (None, _) => "absent",
                (Some(raw), Some(safe)) if raw == safe => "measured",
                _ => "redacted",
            };
            json!({"name":text(&define.name),"value":safe,"value_state":state})
        })
        .collect::<Vec<_>>();
    let includes=value.include_paths.iter().filter(|include|matches!(include.kind.as_str(),"include"|"quote"|"system")).enumerate().map(|(index,include)|json!({"exists":if include.scope=="project" {Some(include.present)} else {None},"kind":include.kind,"order":index,"path":path(&include.path,root),"scope":include.scope})).collect::<Vec<_>>();
    let sysroot = (!value.sysroot.is_empty())
        .then(|| json!({"path":path(&value.sysroot,root),"scope":value.sysroot_scope}));
    let standard = scalar(&value.standard, "+_.-", 64);
    let mut undefines = vec![];
    let mut i = 1;
    let option = if msvc { "/U" } else { "-U" };
    while i < value.argv.len() {
        let token = &value.argv[i];
        let name = if token == option {
            i += 1;
            value.argv.get(i).map(String::as_str)
        } else {
            token.strip_prefix(option).filter(|value| !value.is_empty())
        };
        if let Some(name) = name.filter(|name| identifier(name)) {
            undefines.push(name.to_string());
        }
        i += 1;
    }
    let inconclusive = compiler["family"] == "unknown"
        || !invocation["unmodeled_options"]
            .as_array()
            .unwrap()
            .is_empty()
        || !value.diagnostics.is_empty()
        || standard == REDACTED
        || definitions
            .iter()
            .any(|define| define["value_state"] == "redacted")
        || includes
            .iter()
            .any(|include| include["scope"] == "external")
        || value.sysroot_scope == "external" && !value.sysroot.is_empty();
    let mut result = json!({"compiler":compiler,"defines":definitions,"directory":path(&value.directory,root),"include_paths":includes,"invocation":invocation,"language":value.language,"output":path(&value.output,root),"source":path(&value.source,root),"standard":standard,"sysroot":sysroot,"target":if value.target.is_empty(){None}else{Some(safe_value(&value.target,root,&value.directory))},"undefines":undefines});
    let hash = digest(&result)?;
    result["configuration_digest"] = json!(hash);
    result["comparison_state"] = json!(if inconclusive {
        "inconclusive"
    } else {
        "comparable"
    });
    result["diagnostics"] = json!(
        value
            .diagnostics
            .iter()
            .map(|item| diagnostic(item, root))
            .collect::<Vec<_>>()
    );
    Ok(result)
}
/// Fail closed before publishing a measured, bounded standalone v1 context.
pub fn compilation_export_payload(
    root: &Path,
    metadata: &ExportMetadata,
    context: &CompilationContext,
) -> Result<Value, String> {
    if context.database_path.is_none() || context.database_digest.is_none() {
        return Err(
            "no measured compilation database is available; use --database or --prepare".into(),
        );
    }
    if !matches!(
        context.origin.as_str(),
        "cmake" | "configured" | "discovered" | "qmake"
    ) {
        return Err("compilation context has no trusted database origin".into());
    }
    let fatal = context
        .diagnostics
        .iter()
        .chain(context.units.iter().flat_map(|unit| &unit.diagnostics))
        .filter(|item| item.level == "error")
        .map(|item| scalar(&item.code, "+_.-", 128))
        .collect::<BTreeSet<_>>();
    if !fatal.is_empty() {
        return Err(format!(
            "compilation context contains fatal diagnostics: {}",
            fatal.into_iter().collect::<Vec<_>>().join(", ")
        ));
    }
    if context.units.is_empty() {
        return Err("the measured compilation database contains no usable units".into());
    }
    if context
        .units
        .iter()
        .any(|unit| unit.compiler.is_empty() || unit.argv.is_empty())
    {
        return Err("compilation context contains an unidentified compiler".into());
    }
    let mut units = context
        .units
        .iter()
        .map(|item| unit(item, root))
        .collect::<Result<Vec<_>, _>>()?;
    units.sort_by_key(|unit| {
        [
            "source",
            "target",
            "configuration_digest",
            "directory",
            "output",
        ]
        .map(|key| unit[key].as_str().unwrap_or("").to_string())
    });
    let mut counts = BTreeMap::<(String, String), usize>::new();
    let mut configurations = BTreeMap::<String, BTreeSet<String>>::new();
    for unit in &units {
        let source = unit["source"].as_str().unwrap().to_string();
        let config = unit["configuration_digest"].as_str().unwrap().to_string();
        *counts.entry((source.clone(), config.clone())).or_default() += 1;
        configurations.entry(source).or_default().insert(config);
    }
    for (index, unit) in units.iter_mut().enumerate() {
        let source = unit["source"].as_str().unwrap().to_string();
        let config = unit["configuration_digest"].as_str().unwrap().to_string();
        unit["state"] = json!({"duplicate":counts[&(source.clone(),config)]>1,"source_configuration_count":configurations[&source].len(),"unit_index":index});
    }
    let generator = metadata
        .generator
        .as_deref()
        .filter(|value| !value.is_empty())
        .map(|value| scalar(value, "+_. ()-", 128));
    let inconclusive = !context.diagnostics.is_empty()
        || context.unity_build
        || !context.uncovered_sources.is_empty()
        || generator.as_deref() == Some(REDACTED)
        || units
            .iter()
            .any(|unit| unit["comparison_state"] == "inconclusive");
    let semantic = digest(
        &json!({"generator":generator,"origin":context.origin,"units":units.iter().map(|unit|json!({"configuration_digest":unit["configuration_digest"],"source":unit["source"],"target":unit["target"]})).collect::<Vec<_>>(),"unity_build":context.unity_build}),
    )?;
    Ok(
        json!({"compilation":{"comparison_state":if inconclusive {"inconclusive"}else{"comparable"},"database_path":path(context.database_path.as_deref().unwrap(),root),"diagnostics":context.diagnostics.iter().map(|item|diagnostic(item,root)).collect::<Vec<_>>(),"generator":generator,"origin":context.origin,"semantic_digest":semantic,"source_bytes_digest":context.database_digest,"units":units,"unity_build":context.unity_build},"evidence":"MEASURED","producer":{"name":"ici","version":env!("CARGO_PKG_VERSION")},"project":{"backend":metadata.backend,"name":text(&metadata.name),"type":metadata.project_type,"version":text(&metadata.version)},"schema_version":"ici.compilation-export/v1"}),
    )
}
pub fn render_compilation_export(payload: &Value, pretty: bool) -> Result<Vec<u8>, String> {
    let mut bytes = if pretty {
        serde_json::to_vec_pretty(payload)
    } else {
        serde_json::to_vec(payload)
    }
    .map_err(|error| error.to_string())?;
    bytes.push(b'\n');
    if bytes.len() > MAX_EXPORT_BYTES {
        return Err(format!(
            "compilation export exceeds the {MAX_EXPORT_BYTES} byte output limit"
        ));
    }
    Ok(bytes)
}
