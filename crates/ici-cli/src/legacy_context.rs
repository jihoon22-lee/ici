//! Frozen `reporters/json_rep.py::_serialize_analysis_context` for the historical `verify` report:
//! the `ici.analysis-context/v1` project model, identity, compilation context, requested build
//! variants and artifact manifests, built from facts the native run already recorded (no process
//! runs here). Host paths are reported project-relative or `[external]` like frozen.
use crate::planning::PreparedPlan;
use serde_json::{Value, json};
use std::path::{Component, Path, PathBuf};

const REDACTED: &str = "***REDACTED***";
const SECRET_FLAGS: [&str; 6] = [
    "--api-key",
    "--api_key",
    "--password",
    "--passwd",
    "--secret",
    "--token",
];
const PATH_FLAGS: [&str; 13] = [
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
/// Longest first, as frozen `_COMPILE_PATH_PREFIXES`.
const PATH_PREFIXES: [&str; 22] = [
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

fn text(value: &str) -> String {
    ici_execution::redaction::redact_text(value)
}
fn secret_name(name: &str) -> bool {
    let folded = name.to_ascii_lowercase();
    [
        "password",
        "passwd",
        "secret",
        "api_key",
        "api-key",
        "apikey",
        "access_key",
        "access-key",
        "accesskey",
        "auth_token",
        "auth-token",
        "authtoken",
        "token",
    ]
    .iter()
    .any(|needle| folded.contains(needle))
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
fn absolute(value: &str) -> bool {
    let raw = unquote(value.trim()).1;
    raw.starts_with('/')
        || raw.starts_with("\\\\")
        || raw.as_bytes().get(1) == Some(&b':')
            && raw
                .as_bytes()
                .get(2)
                .is_some_and(|byte| matches!(byte, b'/' | b'\\'))
}
fn unquote(value: &str) -> (&str, &str) {
    let bytes = value.as_bytes();
    if bytes.len() >= 2 && matches!(bytes[0], b'\'' | b'"') && bytes[bytes.len() - 1] == bytes[0] {
        (&value[..1], &value[1..value.len() - 1])
    } else {
        ("", value)
    }
}
/// Frozen `_redact_compilation_path`: project-relative (`.` for the root) or `[external]`.
fn path(value: &str, root: &Path) -> String {
    if value.is_empty() {
        return String::new();
    }
    let (quote, raw) = unquote(value);
    if !absolute(value) {
        return text(value);
    }
    if !raw.starts_with('/') {
        return format!("{quote}[external]{quote}");
    }
    let resolved = lexical(Path::new(raw));
    let relative = match resolved.strip_prefix(root) {
        Ok(relative) if relative.as_os_str().is_empty() => ".".to_owned(),
        Ok(relative) => relative.to_string_lossy().into_owned(),
        Err(_) => "[external]".to_owned(),
    };
    format!("{quote}{relative}{quote}")
}
fn assignment(value: &str, root: &Path) -> String {
    if absolute(value) {
        return path(value, root);
    }
    let Some((left, right)) = value.split_once('=') else {
        return text(value);
    };
    if absolute(left) {
        format!("{}={}", path(left, root), text(right))
    } else if absolute(right) {
        format!("{}={}", text(left), path(right, root))
    } else {
        text(value)
    }
}
fn define(value: &str, root: &Path) -> String {
    let Some((name, body)) = value[2..].split_once('=') else {
        return text(value);
    };
    if secret_name(name) {
        format!("-D{}={REDACTED}", text(name))
    } else {
        format!("-D{}={}", text(name), assignment(body, root))
    }
}
fn linker(value: &str, root: &Path) -> String {
    value
        .split(',')
        .map(|part| {
            if absolute(part) {
                path(part, root)
            } else if let Some((left, right)) = part.split_once('=')
                && absolute(right)
            {
                format!("{}={}", text(left), path(right, root))
            } else {
                text(part)
            }
        })
        .collect::<Vec<_>>()
        .join(",")
}
/// Frozen `_redact_compilation_argv`.
fn argv(values: &[String], root: &Path) -> Vec<String> {
    let (mut hide_next, mut path_next) = (false, false);
    let mut out = Vec::with_capacity(values.len());
    for value in values {
        if hide_next {
            out.push(REDACTED.to_owned());
            hide_next = false;
            continue;
        }
        if path_next {
            out.push(path(value, root));
            path_next = false;
            continue;
        }
        let safe = text(value);
        if SECRET_FLAGS.contains(&value.to_lowercase().as_str()) {
            out.push(safe);
            hide_next = true;
        } else if PATH_FLAGS.contains(&value.as_str()) {
            out.push(safe);
            path_next = true;
        } else if value.starts_with("-D") && value.len() > 2 {
            out.push(define(value, root));
        } else if let Some(rest) = value.strip_prefix("-Wl,").filter(|rest| !rest.is_empty()) {
            out.push(format!("-Wl,{}", linker(rest, root)));
        } else if let Some(rest) = value.strip_prefix('@').filter(|rest| !rest.is_empty()) {
            out.push(format!("@{}", path(rest, root)));
        } else if let Some(prefix) = PATH_PREFIXES
            .iter()
            .find(|prefix| value.starts_with(**prefix) && value.len() > prefix.len())
        {
            let rest = &value[prefix.len()..];
            out.push(if prefix.ends_with('=') {
                format!("{prefix}{}", assignment(rest, root))
            } else if let Some(rest) = rest.strip_prefix('=') {
                format!("{prefix}={}", assignment(rest, root))
            } else {
                format!("{prefix}{}", assignment(rest, root))
            });
        } else {
            out.push(path(&safe, root));
        }
    }
    out
}
fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}
fn diagnostic(value: &Value, root: &Path) -> Value {
    let field = |key: &str| text(value[key].as_str().unwrap_or_default());
    json!({
        "code": field("code"),
        "message": text(ici_cpp::legacy_diagnostic_message(
            value["code"].as_str().unwrap_or_default(),
            value["message"].as_str().unwrap_or_default(),
        )),
        "level": value["level"],
        "entry_index": value["entry_index"],
        // Frozen records an unparseable command before it knows the entry's source.
        "source": if value["code"] == "invalid-command" {
            String::new()
        } else {
            path(value["source"].as_str().unwrap_or_default(), root)
        },
    })
}
fn diagnostics(value: &Value, root: &Path) -> Vec<Value> {
    // A database without any unit is no diagnostic of frozen's context.
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter(|item| item["code"] != "database-empty")
        .map(|item| diagnostic(item, root))
        .collect()
}

/// Frozen `unity_build` of the context: the CMake cache beside a database CMake prepared. Native's
/// `CompilationContext::unity_build` also reads unit names for its own refusal.
pub(crate) fn frozen_unity_build(root: &Path, context: &ici_cpp::CompilationContext) -> bool {
    context
        .database_path
        .as_deref()
        .and_then(|database| Path::new(database).parent().map(Path::to_path_buf))
        .filter(|_| {
            context
                .database_path
                .as_deref()
                .is_some_and(|database| database.starts_with(".ici/"))
        })
        .and_then(|directory| {
            std::fs::read_to_string(root.join(directory).join("CMakeCache.txt")).ok()
        })
        .as_deref()
        .and_then(crate::export_context::cmake_unity_build)
        .unwrap_or(false)
}
/// Frozen `_serialize_compilation_unit` over one native compilation unit.
fn unit(value: &Value, root: &Path) -> Value {
    let field = |key: &str| value[key].as_str().unwrap_or_default();
    let defines: Vec<Value> = value["defines"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|item| item["undefine"] != true)
        .map(|item| {
            let name = item["name"].as_str().unwrap_or_default();
            let value = match item["value"].as_str() {
                Some(_) if secret_name(name) => json!(REDACTED),
                Some(value) => json!(text(value)),
                None => Value::Null,
            };
            json!({"name": text(name), "value": value})
        })
        .collect();
    // Frozen `_record_include_value` records only -I/-isystem/-iquote (and MSVC /I) search
    // directories, each with whether it is a directory; native also keeps -idirafter and
    // forced includes, which frozen never serialized.
    let include_paths: Vec<Value> = value["include_paths"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|item| matches!(item["kind"].as_str(), Some("include" | "system" | "quote")))
        .map(|item| {
            json!({
                "path": path(item["path"].as_str().unwrap_or_default(), root),
                "kind": item["kind"],
                "scope": item["scope"],
                "exists": item["present"],
            })
        })
        .collect();
    json!({
        "source": path(field("source"), root),
        "directory": path(field("directory"), root),
        "argv": argv(&strings(&value["argv"]), root),
        "output": path(field("output"), root),
        "compiler": text(field("compiler")),
        "language": field("language"),
        "standard": text(field("standard")),
        "target": text(field("target")),
        "defines": defines,
        "include_paths": include_paths,
        "sysroot": path(field("sysroot"), root),
        "sysroot_scope": field("sysroot_scope"),
        // Frozen `_configuration_digest`: the recorded directory, argv and output.
        "configuration": ici_domain::canonical_json(
            &json!({"directory": value["directory"], "argv": value["argv"], "output": value["output"]}),
        )
        .map(ici_domain::sha256)
        .unwrap_or_default(),
        "diagnostics": diagnostics(&value["diagnostics"], root),
    })
}

/// Frozen `select_backend`: the build descriptor at the project root and why it was chosen.
fn backend(root: &Path, effective: &Value) -> (Value, String, String) {
    let file = |name: &str| {
        let path = root.join(name);
        path.is_file() && !path.is_symlink()
    };
    let mut projects: Vec<String> = std::fs::read_dir(root)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|entry| {
            entry.file_name().to_string_lossy().ends_with(".pro")
                && entry.file_type().is_ok_and(|kind| kind.is_file())
        })
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    projects.sort();
    if file("CMakeLists.txt") {
        let mut reason = "CMakeLists.txt at the project root selected the CMake backend".to_owned();
        if let Some(project) = projects.first() {
            reason.push_str(&format!("; {project} was present and passed over"));
        }
        return (json!("cmake"), "CMakeLists.txt".into(), reason);
    }
    if let Some(project) = projects.first() {
        return (
            json!("qmake"),
            project.clone(),
            format!("{project} at the project root selected the qmake backend"),
        );
    }
    if let Some(makefile) = ["Makefile", "makefile", "GNUmakefile"]
        .into_iter()
        .find(|name| file(name))
    {
        if effective.pointer("/build/make/enabled") == Some(&json!(true)) {
            return (
                json!("make"),
                makefile.into(),
                format!("{makefile} at the project root selected the configured Make backend"),
            );
        }
        return (
            Value::Null,
            makefile.into(),
            format!(
                "{makefile} at the project root requires build.make.enabled=true and explicit shell-free command argv"
            ),
        );
    }
    (
        Value::Null,
        String::new(),
        "No build descriptor at the project root".into(),
    )
}

fn under(file: &str, directory: &str) -> bool {
    directory == "."
        || file
            .strip_prefix(directory)
            .is_some_and(|rest| rest.starts_with('/'))
}
fn ignored(path: &str) -> bool {
    path.split('/').any(|part| {
        matches!(
            part,
            ".venv" | "venv" | "build" | "__pycache__" | ".git" | ".pytest_cache" | ".ruff_cache"
        ) || part.starts_with("v1.")
            || part.starts_with("v0.")
    })
}

/// Frozen `ProjectModel` fields: sources under the configured source directories, headers under
/// them and `include/`, and the include flags of `include/` (and its subdirectories) and
/// `<source dir>/include`.
fn project(prepared: &PreparedPlan, identity: &ProjectIdentity) -> Value {
    let project = &prepared.project;
    let root = &project.root;
    let effective = &project.effective;
    let mut source_dirs: Vec<String> = project
        .components
        .iter()
        .flat_map(|component| component.source_dirs.iter().cloned())
        // Frozen `get_source_dirs` keeps only configured directories that exist.
        .filter(|directory| crate::files::under(root, directory).is_dir())
        .collect();
    source_dirs.sort();
    source_dirs.dedup();
    let mut files: Vec<String> = project
        .components
        .iter()
        .flat_map(|component| component.files.iter().cloned())
        .collect();
    files.sort();
    files.dedup();
    let extension = |file: &str| file.rsplit_once('.').map(|(_, ext)| ext.to_owned());
    let in_sources = |file: &str| source_dirs.iter().any(|directory| under(file, directory));
    let pick = |extensions: &[&str], header_roots: bool| -> Vec<String> {
        files
            .iter()
            .filter(|file| {
                extension(file).is_some_and(|ext| extensions.contains(&ext.as_str()))
                    && (in_sources(file) || header_roots && under(file, "include"))
            })
            .cloned()
            .collect()
    };
    let python_sources = pick(&["py"], false);
    let cpp_sources = pick(&["cpp", "cc", "cxx", "c"], false);
    let cpp_headers = pick(&["h", "hh", "hpp", "hxx", "moc"], true);
    let external: Vec<String> = effective
        .pointer("/project/cpp_external_build_dirs")
        .map(strings)
        .unwrap_or_default()
        .into_iter()
        .filter(|directory| root.join(directory).is_dir())
        .map(|directory| directory.trim_end_matches('/').to_owned())
        .collect();
    let compilable: Vec<String> = cpp_sources
        .iter()
        .filter(|file| !external.iter().any(|directory| under(file, directory)))
        .cloned()
        .collect();
    let mut includes = std::collections::BTreeSet::new();
    let include = root.join("include");
    if include.is_dir() && !include.is_symlink() {
        includes.insert(format!("-I{}", include.display()));
        for file in &files {
            let mut parent = Path::new(file).parent();
            while let Some(directory) = parent.filter(|path| !path.as_os_str().is_empty()) {
                let text = directory.to_string_lossy();
                if under(&text, "include") && !ignored(&text) {
                    includes.insert(format!("-I{}", root.join(directory).display()));
                }
                parent = directory.parent();
            }
        }
    }
    for directory in &source_dirs {
        let nested = root.join(directory).join("include");
        if nested.is_dir() && !nested.is_symlink() {
            includes.insert(format!("-I{}", lexical(&nested).display()));
        }
    }
    let include_flags: Vec<String> = includes.into_iter().collect();
    let configured_type = effective
        .get("type")
        .filter(|value| value.is_string())
        .or_else(|| effective.pointer("/project/type"))
        .and_then(Value::as_str)
        .filter(|kind| matches!(*kind, "python" | "cpp" | "hybrid"));
    let kind =
        configured_type.unwrap_or(match (python_sources.is_empty(), cpp_sources.is_empty()) {
            (false, false) => "hybrid",
            (true, false) => "cpp",
            _ => "python",
        });
    let (backend, descriptor, reason) = backend(root, effective);
    json!({
        "name": identity.name,
        "version": identity.version,
        "type": kind,
        "source_dirs": source_dirs,
        "python_sources": python_sources,
        "cpp_sources": cpp_sources,
        "cpp_headers": cpp_headers,
        "compilable_cpp_sources": compilable,
        "external_cpp_dirs": external,
        "cpp_include_flags": argv(&include_flags, root),
        "backend": backend,
        "backend_descriptor": descriptor,
        "backend_reason": reason,
    })
}

/// Frozen `prepare_analysis_context` compilation: the compile_db engine's measured database.
fn compilation(document: &Value, root: &Path, backend: &Value, planned: Option<Value>) -> Value {
    let measured = || {
        document["results"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|result| matches!(result["engine_name"].as_str(), Some("compile_db" | "lint")))
            .map(|result| &result["extra"]["context"])
    };
    // Frozen prepares the context whichever engines the profile selected; the plan's context
    // stands in when neither engine measured one.
    let planned = planned.filter(|_| !measured().any(Value::is_object));
    let contexts = || measured().chain(planned.iter());
    let Some(context) = contexts().find(|context| context["database_path"].is_string()) else {
        // Frozen keeps the diagnostics of a canonical preparation that produced no database.
        let unprepared = contexts()
            .map(|context| diagnostics(&context["diagnostics"], root))
            .find(|diagnostics| !diagnostics.is_empty())
            .unwrap_or_default();
        return json!({
            "database_path": null,
            "origin": "",
            "generator": "",
            "unity_build": null,
            "units": [],
            "diagnostics": unprepared,
        });
    };
    let database = context["database_path"].as_str().unwrap_or_default();
    // A database ici prepared from the root descriptor is frozen's `cmake`/`qmake` origin.
    let prepared = database.starts_with(".ici/");
    let origin = match (prepared, backend.as_str()) {
        (true, Some(kind @ ("cmake" | "qmake"))) => kind.to_owned(),
        _ => context["origin"].as_str().unwrap_or_default().to_owned(),
    };
    let cache = Path::new(database)
        .parent()
        .map(|directory| root.join(directory).join("CMakeCache.txt"))
        .and_then(|path| std::fs::read_to_string(path).ok());
    // Frozen `qmake_context` names its captured database's generator `qmake`; a CMake
    // database reads the generator from its cache.
    let generator = if origin == "qmake" {
        "qmake".to_owned()
    } else {
        cache
            .as_deref()
            .and_then(|cache| {
                cache.lines().find_map(|line| {
                    line.strip_prefix("CMAKE_GENERATOR:INTERNAL=")
                        .map(str::to_owned)
                })
            })
            .unwrap_or_default()
    };
    let unity = cache
        .as_deref()
        .and_then(crate::export_context::cmake_unity_build);
    let units: Vec<Value> = context["units"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|item| {
            let mut projected = unit(item, root);
            // Frozen `_qmake_compilers` records the qmake compiler by its resolved realpath
            // (`_resolve_compiler`), so the unit's compiler is that file's name; native keeps
            // the invocation alias in the replayed argv (a multicall driver reads argv[0]).
            if origin == "qmake"
                && let Some(resolved) = item["argv"][0]
                    .as_str()
                    .filter(|program| program.starts_with('/'))
                    .and_then(|program| std::fs::canonicalize(program).ok())
                    .and_then(|path| {
                        path.file_name()
                            .map(|name| name.to_string_lossy().into_owned())
                    })
            {
                projected["compiler"] = json!(text(&resolved));
            }
            projected
        })
        .collect();
    // Frozen `load_compilation_context` order (cpp.rs orders by native identity).
    let mut units = units;
    units.sort_by_cached_key(|unit| {
        ["source", "directory", "configuration"]
            .map(|key| unit[key].as_str().unwrap_or_default().to_owned())
    });
    let mut value = json!({
        "database_path": path(database, root),
        "origin": origin,
        "generator": text(&generator),
        "unity_build": if origin == "cmake" { json!(unity) } else { Value::Null },
        "units": units,
        "diagnostics": diagnostics(&context["diagnostics"], root),
    });
    // Frozen keeps no digest of a database it could not read as a JSON array.
    let unread = context["diagnostics"].as_array().is_some_and(|items| {
        items.iter().any(|item| {
            item["entry_index"].is_null()
                && matches!(
                    item["code"].as_str(),
                    Some("database-malformed" | "database-not-array" | "database-too-many-entries")
                )
        })
    });
    if let Some(digest) = context["database_digest"]
        .as_str()
        .filter(|digest| digest.starts_with("sha256:") && !unread)
    {
        value["database_digest"] = json!(digest);
    }
    value
}

/// Frozen `descriptors_for_profile` build variants, in `BuildVariant` order.
fn requested_variants(effective: &Value, profile: &str) -> Vec<&'static str> {
    let enabled =
        |name: &str| effective.pointer(&format!("/engines/{name}/enabled")) != Some(&json!(false));
    let standard = matches!(profile, "standard" | "deep");
    let deep = profile == "deep";
    let mut variants = vec![];
    if deep && (enabled("build") || enabled("integration")) {
        variants.push("release");
    }
    if standard && enabled("test") {
        variants.push("coverage");
    }
    if standard && enabled("sanitize") {
        variants.push("sanitize");
    }
    if deep && enabled("thread_sanitize") {
        variants.push("thread-sanitize");
    }
    variants
}

/// The name and version a historical suite records for its project.
#[derive(Clone, Debug)]
pub struct ProjectIdentity {
    pub name: String,
    pub version: String,
}

impl ProjectIdentity {
    /// Frozen `read_project_metadata` with its Git fallback, and `v1.0.0` when Git has no answer.
    /// The refusal is frozen's for the first value it refuses, a tag `git describe` printed
    /// included. A schema frozen never ran keeps its own workspace name without that rule.
    pub fn read(
        project: &ici_config::ResolvedProject,
        environment: &std::collections::BTreeMap<String, String>,
    ) -> std::result::Result<Self, ici_config::MetadataRefusal> {
        let metadata = ici_config::read_project_metadata(&project.root)?;
        let name = if project.schema_family == ici_config::SchemaFamily::Legacy {
            metadata.legacy_name(&project.root)?
        } else {
            metadata
                .name
                .clone()
                .unwrap_or_else(|| project.workspace_name.clone())
        };
        let version = match metadata.version {
            Some(version) => version,
            None => match crate::vcs::describe(project, environment) {
                Some(tag) => ici_config::validate_project_version(&tag)?,
                None => "v1.0.0".to_owned(),
            },
        };
        Ok(Self { name, version })
    }

    /// For a project whose schema frozen never ran: the directory name and the default version.
    pub fn unvalidated(root: &std::path::Path) -> Self {
        Self {
            name: root
                .file_name()
                .map_or_else(String::new, |name| name.to_string_lossy().into_owned()),
            version: "v1.0.0".into(),
        }
    }
}

/// The frozen top-level `analysis_context` of a historical `verify` report.
pub fn analysis_context(
    prepared: &PreparedPlan,
    document: &Value,
    profile: &str,
    identity: &ProjectIdentity,
) -> Value {
    let run_identity = &document["native_run"]["identity"];
    let commit = run_identity["source"]["commit"]
        .as_str()
        .filter(|commit| commit.len() >= 40 && commit.bytes().all(|byte| byte.is_ascii_hexdigit()));
    let project = project(prepared, identity);
    let compilation = compilation(
        document,
        &prepared.project.root,
        &project["backend"],
        prepared.cpp.planned_context(),
    );
    let mut manifests: Vec<Value> = vec![];
    for result in document["results"].as_array().into_iter().flatten() {
        for manifest in result["artifact_manifests"]
            .as_array()
            .into_iter()
            .flatten()
        {
            if !manifests.contains(manifest) {
                manifests.push(manifest.clone());
            }
        }
    }
    json!({
        "schema_version": "ici.analysis-context/v1",
        "project": project,
        "identity": {
            "source_commit": commit.unwrap_or("unavailable"),
            "config_digest": run_identity["policy_digest"],
            "toolchain_digest": run_identity["toolchain_digest"],
        },
        "profile": profile,
        "compilation": compilation,
        "requested_variants": requested_variants(&prepared.project.effective, profile),
        "artifact_manifests": manifests,
    })
}

/// Frozen `core/baseline.py` `build_analysis_metadata` over the effective policy of the
/// selected profile (`apply_analysis_profile`: a profile-excluded engine is `enabled = false`):
/// the producer, the frozen v3 finding identity the legacy view keeps (`ici-fingerprint/v1`),
/// and `sha256:` of the frozen compact sorted JSON of the `engines` policy and of the support
/// matrix's tool policy.
pub fn analysis_metadata(prepared: &PreparedPlan, document: &Value, profile: &str) -> Value {
    let digest = |value: &Value| {
        ici_domain::canonical_json(value)
            .map(|text| ici_domain::sha256(text.as_bytes()))
            .unwrap_or_default()
    };
    let mut engines = prepared
        .project
        .effective
        .get("engines")
        .filter(|engines| engines.is_object())
        .cloned()
        .unwrap_or_else(|| json!({}));
    let selected: Option<ici_domain::Profile> = serde_json::from_value(json!(profile)).ok();
    for descriptor in ici_domain::builtin_catalog().legacy {
        let entry = &mut engines[descriptor.name.as_str()];
        if !entry.is_object() {
            *entry = json!({});
        }
        if selected.is_some_and(|profile| !descriptor.profiles.contains(&profile)) {
            entry["enabled"] = json!(false);
        }
    }
    let matrix = &document["support_matrix"];
    let sorted = |value: &Value| {
        let mut items: Vec<Value> = value.as_array().cloned().unwrap_or_default();
        items.sort_by_key(|item| item.as_str().unwrap_or_default().to_owned());
        Value::Array(items)
    };
    let mut entries: Vec<Value> = matrix["entries"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|entry| {
            json!({
                "engine_name": entry["engine_name"],
                "language": entry["language"],
                "mode": entry["mode"],
                "active_mode": entry["active_mode"],
                "applicable": entry["applicable"],
                "enabled": entry["enabled"],
                "evidence": entry["evidence"],
                "confidence": entry["confidence"],
                "frameworks": sorted(&entry["frameworks"]),
                "required_tools": sorted(&entry["required_tools"]),
                "optional_tools": sorted(&entry["optional_tools"]),
                "fallback_mode": entry["fallback_mode"],
            })
        })
        .collect();
    entries.sort_by_key(|entry| {
        (
            entry["engine_name"].as_str().unwrap_or_default().to_owned(),
            entry["language"].as_str().unwrap_or_default().to_owned(),
        )
    });
    let tool_policy = json!({
        "project_languages": sorted(&matrix["project_languages"]),
        "project_frameworks": sorted(&matrix["project_frameworks"]),
        "entries": entries,
    });
    json!({
        "producer_version": crate::VERSION,
        "fingerprint_version": "ici-fingerprint/v1",
        "policy_digest": digest(&engines),
        "tool_policy_digest": digest(&tool_policy),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compilation_argv_reports_host_paths_relative_or_external() {
        let root = Path::new("/work/project");
        let values: Vec<String> = [
            "/usr/bin/c++",
            "-I",
            "/work/project/include",
            "-I/opt/sdk/include",
            "-DROOT=/work/project/data",
            "-DAPI_TOKEN=abc",
            "--token",
            "abc",
            "-Wl,-rpath,/opt/lib",
            "@/work/project/args.rsp",
            "-c",
            "/work/project/src/a.cpp",
        ]
        .map(str::to_owned)
        .to_vec();
        assert_eq!(
            argv(&values, root),
            [
                "[external]",
                "-I",
                "include",
                "-I[external]",
                "-DROOT=data",
                "-DAPI_TOKEN=***REDACTED***",
                "--token",
                "***REDACTED***",
                "-Wl,-rpath,[external]",
                "@args.rsp",
                "-c",
                "src/a.cpp",
            ]
        );
        assert_eq!(path("/work/project", root), ".");
        assert_eq!(path("'C:\\x'", root), "'[external]'");
    }
}
