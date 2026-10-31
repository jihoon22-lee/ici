//! Source package and selected wheel contracts, over immutable inputs only.
use crate::{
    cancelled, failure,
    metadata::{self, ProjectMetadata},
    parser::ParsedFile,
    source::SourceError,
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, SourceSpan,
    TaskState,
};
use pep440_rs::Version;
use ruff_python_ast::{Expr, Stmt};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    str::FromStr,
    sync::{Arc, atomic::AtomicBool},
};

pub struct Policy {
    pub required: bool,
    pub pure: bool,
    pub entrypoints: bool,
    pub package_files: bool,
    pub max_wheels: usize,
    pub max_members: usize,
    pub max_bytes: u64,
    pub globs: Vec<String>,
}
fn positive(request: &AnalysisRequest, key: &str, maximum: u64) -> Result<u64, String> {
    let value = request
        .options
        .get(key)
        .map_or(Some(maximum), Value::as_u64)
        .ok_or_else(|| format!("{key} must be a positive integer"))?;
    if value == 0 || value > maximum {
        return Err(format!("{key} must be between 1 and {maximum}"));
    }
    Ok(value)
}
impl Policy {
    pub fn new(request: &AnalysisRequest) -> Result<Self, String> {
        let globs = match request.options.get("wheel_globs") {
            None => vec![],
            Some(Value::Array(values)) => values
                .iter()
                .map(|value| {
                    value
                        .as_str()
                        .map(str::to_owned)
                        .ok_or_else(|| "wheel_globs must contain strings".to_owned())
                })
                .collect::<Result<Vec<_>, _>>()?,
            _ => return Err("wheel_globs must be a list".into()),
        };
        if globs.len() > 32 {
            return Err("wheel_globs must contain at most 32 patterns".into());
        }
        for pattern in &globs {
            if pattern.is_empty()
                || pattern.starts_with('/')
                || pattern.contains(['\\', '\0'])
                || pattern.split('/').any(|part| part == "..")
            {
                return Err(format!(
                    "wheel glob must be project-relative and contained: {}",
                    crate::unicode::python_repr(pattern)
                ));
            }
        }
        let pure = match request
            .options
            .get("wheel_policy")
            .and_then(Value::as_str)
            .unwrap_or("allow-native")
        {
            "allow-native" => false,
            "pure" => true,
            _ => return Err("wheel_policy must be pure or allow-native".into()),
        };
        let flag = |key: &str, default: bool| {
            request.options.get(key).map_or(Ok(default), |value| {
                value
                    .as_bool()
                    .ok_or_else(|| format!("{key} must be a boolean"))
            })
        };
        Ok(Self {
            required: flag("wheel_required", false)?,
            pure,
            entrypoints: flag("check_entrypoints", true)?,
            package_files: flag("check_package_files", true)?,
            max_wheels: positive(request, "max_wheels", 32)? as usize,
            max_members: positive(request, "max_wheel_members", 8192)? as usize,
            max_bytes: positive(request, "max_wheel_uncompressed_bytes", 64 * 1024 * 1024)?,
            globs,
        })
    }
}
pub struct Package<'a> {
    pub name: String,
    pub version: String,
    pub modules: BTreeMap<String, &'a ParsedFile>,
    pub files: BTreeSet<String>,
    pub entrypoints: Vec<(String, String)>,
}
pub fn canonical_name(value: &str) -> String {
    let mut result = String::new();
    for character in crate::unicode::lower(value).chars() {
        if matches!(character, '-' | '_' | '.') {
            if !result.ends_with('-') {
                result.push('-');
            }
        } else {
            result.push(character);
        }
    }
    result
}
fn entries(metadata: &ProjectMetadata) -> Vec<(String, String)> {
    let mut result = vec![];
    let Some(project) = metadata.document.get("project") else {
        return result;
    };
    for (table, group) in [
        ("scripts", "console_scripts"),
        ("gui-scripts", "gui_scripts"),
    ] {
        if let Some(table) = project.get(table).and_then(toml_edit::Item::as_table_like) {
            for (name, value) in table.iter() {
                if let Some(value) = value.as_str() {
                    result.push((format!("{group}:{name}"), value.into()));
                }
            }
        }
    }
    if let Some(groups) = project
        .get("entry-points")
        .and_then(toml_edit::Item::as_table_like)
    {
        for (group, table) in groups.iter() {
            if let Some(table) = table.as_table_like() {
                for (name, value) in table.iter() {
                    if let Some(value) = value.as_str() {
                        result.push((format!("{group}:{name}"), value.into()));
                    }
                }
            }
        }
    }
    result.sort();
    result
}
fn project<'a>(
    request: &AnalysisRequest,
    metadata: &ProjectMetadata,
    files: &'a [Arc<ParsedFile>],
) -> Result<Package<'a>, String> {
    let project = metadata
        .document
        .get("project")
        .and_then(toml_edit::Item::as_table_like)
        .ok_or("pyproject.toml [project] must be a table")?;
    let name = project
        .get("name")
        .and_then(toml_edit::Item::as_str)
        .filter(|name| !name.trim().is_empty())
        .ok_or("project.name must be a non-empty string")?;
    let version = project
        .get("version")
        .map_or(Some(""), toml_edit::Item::as_str)
        .ok_or("project.version must be a string")?;
    if version.is_empty()
        && !metadata::strings(project.get("dynamic"))
            .iter()
            .any(|value| value == "version")
    {
        return Err("project.version must be declared or listed as dynamic".into());
    }
    if !version.is_empty() && Version::from_str(version).is_err() {
        return Err(format!(
            "project.version is invalid: {}",
            crate::unicode::python_repr(version)
        ));
    }
    let mut modules = BTreeMap::new();
    let mut package_files = BTreeSet::new();
    let mut ordered: Vec<_> = files.iter().collect();
    ordered.sort_by_key(|file| &file.source.path);
    for file in ordered {
        let path = metadata::component_relative(request, &file.source.path);
        let mut parts: Vec<_> = path.split('/').collect();
        if parts
            .first()
            .is_some_and(|part| matches!(*part, "src" | "lib" | "python" | "packages"))
        {
            parts.remove(0);
        }
        if parts.is_empty() {
            continue;
        }
        let archive = parts.join("/");
        package_files.insert(archive.clone());
        let stem = archive
            .strip_suffix(".pyi")
            .or_else(|| archive.strip_suffix(".py"))
            .unwrap_or(&archive);
        let mut parts: Vec<_> = stem.split('/').collect();
        if parts.last() == Some(&"__init__") {
            parts.pop();
        }
        if !parts.is_empty() && parts.iter().all(|part| crate::unicode::identifier(part)) {
            modules.insert(parts.join("."), file.as_ref());
        }
    }
    Ok(Package {
        name: name.into(),
        version: version.into(),
        modules,
        files: package_files,
        entrypoints: entries(metadata),
    })
}
pub fn issue(
    request: &AnalysisRequest,
    rule: &str,
    path: &str,
    line: u32,
    message: impl Into<String>,
    severity: &str,
    native: &str,
) -> Finding {
    let location = SourceSpan::line(path, line.max(1));
    let region = BTreeMap::from([
        ("end_column", Value::Null),
        ("end_line", Value::Null),
        ("start_column", Value::Null),
        ("start_line", json!(location.start_line)),
    ]);
    let payload = BTreeMap::from([
        ("path", json!(path)),
        ("region", json!(region)),
        ("rule_id", json!(rule)),
        ("symbol", json!("")),
        ("version", json!("ici-fingerprint/v1")),
    ]);
    let mut finding = Finding::new(rule, message, location, "ici.compat");
    finding.fingerprint = format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&payload).expect("package fingerprint JSON"))
    );
    finding.evidence = EvidenceLevel::Measured;
    finding.confidence = "exact".into();
    finding.category = "compatibility".into();
    finding.severity = severity.into();
    finding.native_rule_id = native.into();
    finding.component_id = Some(request.component_id.clone());
    finding.task_id = Some(request.task_id.clone());
    finding
}
fn line(text: &str, needle: &str) -> u32 {
    text.lines()
        .position(|line| line.contains(needle))
        .map_or(1, |position| position as u32 + 1)
}
fn supported_binding(statement: &Stmt, name: &str) -> bool {
    match statement {
        Stmt::FunctionDef(function) => function.name.as_str() == name,
        Stmt::ClassDef(class) => class.name.as_str() == name,
        Stmt::Assign(assign) => {
            matches!(assign.value.as_ref(), Expr::Lambda(_))
                && assign
                    .targets
                    .iter()
                    .any(|target| matches!(target,Expr::Name(target) if target.id.as_str()==name))
        }
        Stmt::AnnAssign(assign) => {
            matches!(assign.value.as_deref(), Some(Expr::Lambda(_)))
                && matches!(assign.target.as_ref(),Expr::Name(target) if target.id.as_str()==name)
        }
        Stmt::Import(import) => import.names.iter().any(|alias| {
            alias.asname.as_ref().map_or_else(
                || alias.name.split('.').next().unwrap_or(""),
                |alias| alias.as_str(),
            ) == name
        }),
        Stmt::ImportFrom(import) => import.names.iter().any(|alias| {
            alias.asname.as_ref().map_or_else(
                || alias.name.split('.').next().unwrap_or(""),
                |alias| alias.as_str(),
            ) == name
        }),
        _ => false,
    }
}
fn attribute_exists(file: &ParsedFile, attribute: &str) -> bool {
    if file.source.text.len() > 2 * 1024 * 1024
        || crate::ast_util::cpython_node_count(file) > 200_000
    {
        return false;
    }
    let mut body = file.body();
    let parts: Vec<_> = attribute.split('.').collect();
    for (index, name) in parts.iter().enumerate() {
        let Some(statement) = body
            .iter()
            .find(|statement| supported_binding(statement, name))
        else {
            return false;
        };
        if index + 1 == parts.len() {
            return true;
        }
        body = if let Stmt::ClassDef(class) = statement {
            &class.body
        } else {
            &[]
        };
    }
    false
}
fn entrypoint(value: &str) -> Option<(&str, &str)> {
    let (module, attribute) = value.trim().split_once(':')?;
    let valid = |value: &str| {
        value.split('.').all(|part| {
            part.chars()
                .next()
                .is_some_and(|character| character.is_ascii_alphabetic() || character == '_')
                && crate::unicode::word(part)
        })
    };
    if valid(module) && valid(attribute) {
        Some((module, attribute))
    } else {
        None
    }
}
pub fn requested(request: &AnalysisRequest) -> bool {
    ["legacy_projection", "package_inspection"]
        .iter()
        .any(|key| request.options.get(key).and_then(Value::as_bool) == Some(true))
        || request
            .options
            .get("wheel_globs")
            .and_then(Value::as_array)
            .is_some_and(|values| !values.is_empty())
        || request
            .inputs
            .iter()
            .any(|input| input.path.ends_with(".whl"))
}
fn summary(path: &str, label: &str, findings: &[Finding]) -> InspectionTarget {
    let relevant: Vec<_> = findings
        .iter()
        .filter(|finding| finding.primary_location.path == path)
        .collect();
    let mut target = InspectionTarget::at(path, 1);
    target.target_name = format!("Package:{label}");
    target.status = if relevant
        .iter()
        .any(|finding| matches!(finding.severity.as_str(), "high" | "critical"))
    {
        "FAIL"
    } else if relevant.is_empty() {
        "PASS"
    } else {
        "WARN"
    }
    .into();
    target.message = if relevant.is_empty() {
        "Package contract inspection completed".into()
    } else {
        format!("Package inspection found {} issue(s)", relevant.len())
    };
    target
}
pub fn analyze(
    request: &AnalysisRequest,
    metadata: &ProjectMetadata,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&AtomicBool>,
) {
    if output.observation.state == TaskState::Failed || cancelled(output, cancellation) {
        return;
    }
    let policy = match Policy::new(request) {
        Ok(policy) => policy,
        Err(message) => {
            failure(
                output,
                &metadata.path,
                &SourceError::new("package-policy", message),
            );
            return;
        }
    };
    let package = match project(request, metadata, files) {
        Ok(package) => package,
        Err(message) => {
            failure(
                output,
                &metadata.path,
                &SourceError::new("package-metadata", message),
            );
            return;
        }
    };
    let before = output.observation.findings.len();
    let top: BTreeSet<_> = package
        .modules
        .keys()
        .filter_map(|name| name.split('.').next())
        .collect();
    let expected = canonical_name(&package.name).replace('-', "_");
    if !package.modules.is_empty() && !top.contains(expected.as_str()) && top.len() == 1 {
        let actual = top.iter().next().unwrap();
        output.observation.findings.push(issue(
            request,
            "ici.package.import-distribution-mismatch",
            &metadata.path,
            line(&metadata.text, "name"),
            format!(
                "Distribution {} exposes import package {}",
                crate::unicode::python_repr(&package.name),
                crate::unicode::python_repr(actual)
            ),
            "medium",
            "import-distribution-name",
        ));
    }
    if policy.entrypoints {
        for (name, value) in &package.entrypoints {
            if cancelled(output, cancellation) {
                return;
            }
            if entrypoint(value).and_then(|(module, attribute)| {
                package
                    .modules
                    .get(module)
                    .map(|file| attribute_exists(file, attribute))
            }) == Some(true)
            {
                continue;
            }
            output.observation.findings.push(issue(
                request,
                "ici.package.entrypoint-missing",
                &metadata.path,
                line(&metadata.text, value),
                format!(
                    "Entry point {} target {} does not resolve to a callable or imported symbol",
                    crate::unicode::python_repr(name),
                    crate::unicode::python_repr(value)
                ),
                "high",
                "entrypoint-target",
            ));
        }
    }
    if package.modules.is_empty() {
        output.observation.findings.push(issue(
            request,
            "ici.package.discovery-empty",
            &metadata.path,
            1,
            "Project metadata is present but no Python package modules were discovered",
            "high",
            "package-discovery",
        ));
    }
    let mut wheels: Vec<_> = request
        .inputs
        .iter()
        .filter(|input| input.path.ends_with(".whl"))
        .collect();
    wheels.sort_by_key(|input| &input.path);
    if wheels.len() > policy.max_wheels {
        failure(
            output,
            &metadata.path,
            &SourceError::new(
                "wheel-count-limit",
                "wheel match count exceeds the configured bound",
            ),
        );
        return;
    }
    if wheels.is_empty() && !policy.globs.is_empty() {
        if policy.required {
            failure(
                output,
                &metadata.path,
                &SourceError::new(
                    "wheel-missing",
                    "configured wheel globs matched no wheel artifacts",
                ),
            );
            return;
        }
        output.observation.findings.push(issue(
            request,
            "ici.package.wheel-missing",
            &metadata.path,
            1,
            "Configured wheel globs matched no wheel artifacts",
            "medium",
            "wheel.discovery",
        ));
    }
    let mut evidence = vec![];
    output.targets.push(summary(
        &metadata.path,
        "SourceMetadata",
        &output.observation.findings[before..],
    ));
    for wheel in &wheels {
        if cancelled(output, cancellation) {
            return;
        }
        if wheel.path.starts_with('/')
            || wheel.path.contains(['\\', '\0'])
            || wheel
                .path
                .split('/')
                .any(|part| part.is_empty() || part == "..")
        {
            failure(
                output,
                ".",
                &SourceError::new(
                    "invalid-wheel-path",
                    "Wheel inputs must be project-relative contained paths",
                ),
            );
            return;
        }
        let previous = output.observation.findings.len();
        match crate::wheel::inspect(request, wheel, &package, &policy, cancellation) {
            Ok((findings, measured)) => {
                output.observation.findings.extend(findings);
                evidence.push(measured);
            }
            Err(message) => {
                if cancelled(output, cancellation) {
                    return;
                }
                output.observation.findings.push(issue(
                    request,
                    "ici.package.wheel-invalid",
                    &wheel.path,
                    1,
                    format!("Wheel could not be inspected safely: {message}"),
                    "high",
                    "wheel.structure",
                ));
            }
        }
        output.targets.push(summary(
            &wheel.path,
            "Wheel",
            &output.observation.findings[previous..],
        ));
    }
    let findings = &output.observation.findings[before..];
    output.extra["package_evidence"] = json!({"state":"MEASURED","policy":if policy.pure {"pure"}else{"allow-native"},"requested":policy.globs,"attempted":wheels.len(),"checked":evidence.len(),"invalid":wheels.len()-evidence.len(),"members":evidence.iter().map(|wheel|wheel.members).sum::<usize>(),"uncompressed_bytes":evidence.iter().map(|wheel|wheel.uncompressed_bytes).sum::<u64>(),"pure":!evidence.is_empty() && evidence.iter().all(|wheel|wheel.pure),"wheels":evidence,"source_modules":package.modules.len(),"source_package_files":package.files.len(),"entrypoints":package.entrypoints.len(),"findings":findings.iter().map(|finding|&finding.rule_id).collect::<Vec<_>>()});
    output.extra["wheel"] = output.extra["package_evidence"].clone();
}
