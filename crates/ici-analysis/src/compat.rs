//! Static syntax/API floors over immutable native parser snapshots.
use crate::{
    ast_util, cancelled, failure, finding, finish_status, parser::ParsedFile, source::SourceError,
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
};
use ruff_python_ast::{
    Expr, ExprContext, PythonVersion, Stmt,
    visitor::{self, Visitor},
};
use ruff_python_parser::{Mode, ParseOptions, parse_unchecked};
use ruff_text_size::{Ranged, TextRange};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Arc, atomic::AtomicBool},
};

const INTRODUCED: &[(&str, (u8, u8))] = &[
    ("datetime.UTC", (3, 11)),
    ("enum.StrEnum", (3, 11)),
    ("itertools.batched", (3, 12)),
    ("pathlib.Path.walk", (3, 12)),
    ("sys.monitoring", (3, 12)),
    ("tomllib", (3, 11)),
    ("typing.Never", (3, 11)),
    ("typing.NotRequired", (3, 11)),
    ("typing.Required", (3, 11)),
    ("typing.Self", (3, 11)),
    ("typing.TypeAliasType", (3, 12)),
];
struct Candidates<'a> {
    file: &'a ParsedFile,
    imported: Vec<(String, TextRange)>,
    loaded: Vec<(String, TextRange)>,
    cancellation: Option<&'a AtomicBool>,
}
impl<'a> Visitor<'a> for Candidates<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        match statement {
            Stmt::Import(import) => {
                for alias in &import.names {
                    self.imported
                        .push((alias.name.to_string(), statement.range()));
                }
            }
            Stmt::ImportFrom(import) if import.level == 0 => {
                if let Some(module) = &import.module {
                    for alias in &import.names {
                        if alias.name.as_str() != "*" {
                            self.imported
                                .push((format!("{module}.{}", alias.name), statement.range()));
                        }
                    }
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }
    fn visit_expr(&mut self, expression: &'a Expr) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        if (matches!(expression,Expr::Name(name) if name.ctx==ExprContext::Load)
            || matches!(expression,Expr::Attribute(attribute) if attribute.ctx==ExprContext::Load))
            && let Some(qualified) = self.file.scopes.resolve(expression)
        {
            self.loaded.push((qualified, expression.range()));
        }
        visitor::walk_expr(self, expression);
    }
}
fn append(output: &mut AnalysisOutput, request: &AnalysisRequest, target: InspectionTarget) {
    let rule = target
        .target_name
        .strip_prefix("Compatibility:")
        .unwrap_or(&target.target_name);
    let slug = match rule {
        "SyntaxFloor" => "syntax-floor",
        _ => "standard-library-floor",
    };
    let mut result = Finding::new(
        format!("python.compat.{slug}"),
        &target.message,
        finding::span(&target),
        "ici.compat",
    );
    result.fingerprint = finding::sha1_short(
        "compat-",
        &format!(
            "{}:{}:{rule}:{}",
            target.file_path, target.start_line, target.message
        ),
    );
    result.native_rule_id = target.target_name.clone();
    result.severity = "medium".into();
    result.confidence = "high".into();
    result.category = "compatibility".into();
    result.evidence = EvidenceLevel::Measured;
    result.component_id = Some(request.component_id.clone());
    result.task_id = Some(request.task_id.clone());
    output.observation.findings.push(result);
    output.targets.push(target);
}
pub fn analyze(
    request: &AnalysisRequest,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&AtomicBool>,
) {
    let metadata = match crate::metadata::load(request) {
        Ok(metadata) => metadata,
        Err(error) => {
            failure(output, "pyproject.toml", &error);
            return;
        }
    };
    let floor = request
        .options
        .get("target_version")
        .or_else(|| request.options.get("target"))
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty());
    let inferred = if floor.is_none() {
        let requires_python = request
            .options
            .get("requires_python")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_else(|| {
                metadata
                    .as_ref()
                    .map_or("", |metadata| metadata.requires_python.as_str())
            });
        match crate::metadata::infer_floor(requires_python) {
            Ok(target) => target,
            Err(error) => {
                failure(output, "pyproject.toml", &error);
                return;
            }
        }
    } else {
        None
    };
    let target = match floor.map(str::parse::<PythonVersion>).transpose() {
        Ok(target) => target.or(inferred),
        Err(_) => {
            failure(
                output,
                "pyproject.toml",
                &SourceError::new(
                    "invalid-python-floor",
                    "Configured Python floor must be major.minor",
                ),
            );
            return;
        }
    };
    let introduced: BTreeMap<_, _> = INTRODUCED
        .iter()
        .map(|(name, version)| (*name, *version))
        .collect();
    let mut checked = 0;
    let mut nodes = 0;
    for file in files {
        if cancelled(output, cancellation) {
            break;
        }
        let count = ast_util::cpython_node_count(file);
        if count > 100_000 || nodes + count > 1_000_000 {
            failure(
                output,
                &file.source.path,
                &SourceError::new(
                    "compatibility-ast-limit",
                    "Python compatibility AST exceeds the bounded per-file or aggregate limit",
                ),
            );
            break;
        }
        nodes += count;
        if let Some(target) = target {
            let mut candidates = Candidates {
                file,
                imported: vec![],
                loaded: vec![],
                cancellation,
            };
            candidates.visit_body(file.body());
            let blocked: BTreeSet<_> = candidates
                .imported
                .iter()
                .filter(|(name, _)| {
                    introduced
                        .get(name.as_str())
                        .is_some_and(|version| *version > target.as_tuple())
                })
                .map(|(name, _)| name.clone())
                .collect();
            let mut emitted = BTreeSet::new();
            for (imported, items) in [(true, candidates.imported), (false, candidates.loaded)] {
                for (qualified, range) in items {
                    if !imported && blocked.contains(&qualified) {
                        continue;
                    }
                    if let Some(version) = introduced.get(qualified.as_str()) {
                        if *version <= target.as_tuple()
                            || !emitted.insert((range.start(), qualified.clone()))
                        {
                            continue;
                        }
                        let mut location = file
                            .source
                            .target(range, "Compatibility:StandardLibraryFloor");
                        location.status = "WARN".into();
                        location.message = format!(
                            "{qualified} requires Python {}.{} or newer",
                            version.0, version.1
                        );
                        append(output, request, location);
                    }
                }
            }
            if target <= PythonVersion::PY314 {
                let parsed = parse_unchecked(
                    &file.source.text,
                    ParseOptions::from(Mode::Module).with_target_version(target),
                );
                let offset = parsed
                    .errors()
                    .first()
                    .map(|error| error.range().start())
                    .or_else(|| {
                        parsed
                            .unsupported_syntax_errors()
                            .first()
                            .map(|error| error.range().start())
                    });
                if let Some(offset) = offset {
                    let (line, column) = file.source.location(offset.to_usize());
                    let mut location = InspectionTarget::at(&file.source.path, line);
                    location.start_column = Some(column);
                    location.target_name = "Compatibility:SyntaxFloor".into();
                    location.status = "WARN".into();
                    location.message =
                        format!("Syntax is not accepted by configured Python {target} floor");
                    append(output, request, location);
                }
            }
        }
        if cancelled(output, cancellation) {
            break;
        }
        let mut target = InspectionTarget::at(&file.source.path, 1);
        target.target_name = "Compatibility:StaticScan".into();
        target.message = "Python syntax-floor and standard-library API scan completed".into();
        output.targets.push(target);
        checked += 1;
    }
    if target.is_none() {
        output.observation.limitations.push("No declared Python floor was supplied; syntax and standard-library floors were not asserted".into());
    }
    output.observation.limitations.push("Static API rules cover the documented standard-library inventory; absence of findings is not proof of runtime compatibility".into());
    output.observation.measurements.push(Measurement {
        unit: "files".into(),
        ..Measurement::measured("files_checked", checked as f64)
    });
    output.extra = json!({"files_checked":checked,"python_ast_nodes":nodes,"target_version":target.map(|target|target.to_string()),"api_inventory":INTRODUCED.iter().map(|(name,_)|*name).collect::<Vec<_>>()});
    if let Some(metadata) = metadata {
        output.extra["project_metadata"] = json!({"path":metadata.path,"bytes":metadata.text.len(),"requires_python":metadata.requires_python,"import_names":crate::metadata::strings(metadata.document.get("project").and_then(|project|project.get("import-names")))});
    }
    output.summary = format!("Inspected static Python compatibility in {checked} file(s)");
    finish_status(output, request);
}
