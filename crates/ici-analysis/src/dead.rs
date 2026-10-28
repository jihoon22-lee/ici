//! Bounded static private-definition/import correlation and first unreachable statements.
//! This is a heuristic, not runtime reachability, and its findings remain ESTIMATED.
use crate::{
    cancelled, failure, finding, finish_status,
    modules::{ModuleIndex, relative_import},
    parser::ParsedFile,
    source::SourceError,
};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
    TaskState,
};
use ruff_python_ast::{
    Expr, ExprContext, Stmt,
    visitor::{self, Visitor},
};
use ruff_text_size::Ranged;
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Arc, atomic::AtomicBool},
};

#[derive(Clone)]
struct ImportBinding {
    module: String,
    member: Option<String>,
    prefix: Vec<String>,
}
struct Imports<'a> {
    file: &'a ParsedFile,
    module: &'a str,
    bindings: BTreeMap<(usize, String), Vec<ImportBinding>>,
    errors: Vec<(u32, String)>,
    cancellation: Option<&'a AtomicBool>,
}
impl<'a> Visitor<'a> for Imports<'a> {
    fn visit_stmt(&mut self, node: &'a Stmt) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        let scope = self.file.scopes.scope_of_statement(node);
        match node {
            Stmt::Import(import) => {
                for alias in &import.names {
                    let module = alias.name.to_string();
                    let local = alias.asname.as_ref().map_or_else(
                        || module.split('.').next().unwrap_or("").to_owned(),
                        ToString::to_string,
                    );
                    let prefix = if alias.asname.is_some() {
                        vec![local.clone()]
                    } else {
                        module.split('.').map(str::to_owned).collect()
                    };
                    self.bindings
                        .entry((scope, local))
                        .or_default()
                        .push(ImportBinding {
                            module,
                            member: None,
                            prefix,
                        });
                }
            }
            Stmt::ImportFrom(import) => {
                let package = self.file.source.path.ends_with("/__init__.py")
                    || self.file.source.path == "__init__.py";
                if let Some(module) = relative_import(
                    self.module,
                    import.module.as_ref().map(|module| module.as_str()),
                    import.level,
                    package,
                ) {
                    for alias in &import.names {
                        if alias.name.as_str() == "*" {
                            continue;
                        }
                        let local = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                        self.bindings
                            .entry((scope, local.clone()))
                            .or_default()
                            .push(ImportBinding {
                                module: module.clone(),
                                member: Some(alias.name.to_string()),
                                prefix: vec![local],
                            });
                    }
                } else {
                    self.errors.push((
                        self.file.source.location(node.range().start().to_usize()).0,
                        "Relative import leaves the selected package scope".into(),
                    ));
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, node);
    }
    fn visit_expr(&mut self, _: &'a Expr) {}
}
struct References<'a> {
    file: &'a ParsedFile,
    imports: &'a BTreeMap<(usize, String), Vec<ImportBinding>>,
    modules: &'a ModuleIndex,
    files: &'a BTreeMap<String, &'a ParsedFile>,
    local: BTreeSet<String>,
    imported: BTreeSet<(String, String)>,
    cancellation: Option<&'a AtomicBool>,
}
fn attribute_chain(expression: &Expr) -> Option<(Vec<String>, &Expr)> {
    match expression {
        Expr::Name(name) => Some((vec![name.id.to_string()], expression)),
        Expr::Attribute(attribute) => {
            let (mut parts, root) = attribute_chain(&attribute.value)?;
            parts.push(attribute.attr.to_string());
            Some((parts, root))
        }
        _ => None,
    }
}
fn declared_attribute(file: &ParsedFile, name: &str) -> bool {
    file.body().iter().any(|statement| match statement {
        Stmt::FunctionDef(function) => function.name.as_str() == name,
        Stmt::ClassDef(class) => class.name.as_str() == name,
        Stmt::Assign(assign) => assign.targets.iter().any(|target| {
            crate::ast_util::bound_names(target)
                .iter()
                .any(|bound| bound == name)
        }),
        Stmt::AnnAssign(assign) => {
            assign.value.is_some()
                && crate::ast_util::bound_names(&assign.target)
                    .iter()
                    .any(|bound| bound == name)
        }
        _ => false,
    })
}
impl References<'_> {
    fn imported_reference(&mut self, parts: &[String], root: &Expr) {
        let Some(first) = parts.first() else {
            return;
        };
        let Some(scope) = self
            .file
            .scopes
            .binding_scope(self.file.scopes.scope_of(root), first)
        else {
            return;
        };
        let Some(bindings) = self.imports.get(&(scope, first.clone())) else {
            return;
        };
        for binding in bindings {
            if let Some(member) = &binding.member {
                let candidate = if binding.module.is_empty() {
                    member.clone()
                } else {
                    format!("{}.{member}", binding.module)
                };
                let owner = self
                    .modules
                    .by_module
                    .get(&binding.module)
                    .and_then(|path| self.files.get(path));
                let is_attribute = owner.is_some_and(|owner| declared_attribute(owner, member));
                if !is_attribute && let Some(path) = self.modules.by_module.get(&candidate) {
                    if let Some(attribute) = parts.get(1) {
                        self.imported.insert((path.clone(), attribute.clone()));
                    }
                } else if let Some(path) = self.modules.by_module.get(&binding.module) {
                    self.imported.insert((path.clone(), member.clone()));
                }
            } else if parts.starts_with(&binding.prefix)
                && let Some(member) = parts.get(binding.prefix.len())
                && let Some(path) = self.modules.by_module.get(&binding.module)
            {
                self.imported.insert((path.clone(), member.clone()));
            }
        }
    }
}
impl<'a> Visitor<'a> for References<'a> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        if let Expr::Name(name) = expression
            && name.ctx == ExprContext::Load
            && self
                .file
                .scopes
                .binding_scope(self.file.scopes.scope_of(expression), name.id.as_str())
                == Some(0)
        {
            self.local.insert(name.id.to_string());
        }
        if let Some((parts, root)) = attribute_chain(expression) {
            self.imported_reference(&parts, root);
        }
        visitor::walk_expr(self, expression);
    }
}
fn exported(file: &ParsedFile) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    for statement in file.body() {
        let value = match statement {
            Stmt::Assign(assign)
                if assign.targets.iter().any(
                    |target| matches!(target,Expr::Name(name) if name.id.as_str()=="__all__"),
                ) =>
            {
                Some(assign.value.as_ref())
            }
            Stmt::AnnAssign(assign) if matches!(assign.target.as_ref(),Expr::Name(name) if name.id.as_str()=="__all__") => {
                assign.value.as_deref()
            }
            _ => None,
        };
        let elements = match value {
            Some(Expr::List(list)) => &list.elts,
            Some(Expr::Tuple(tuple)) => &tuple.elts,
            Some(Expr::Set(set)) => &set.elts,
            _ => continue,
        };
        for item in elements {
            if let Expr::StringLiteral(value) = item {
                names.insert(value.value.to_str().to_owned());
            }
        }
    }
    names
}
struct Unreachable<'a> {
    file: &'a ParsedFile,
    targets: Vec<InspectionTarget>,
    cancellation: Option<&'a AtomicBool>,
}
impl<'a> Visitor<'a> for Unreachable<'a> {
    fn visit_body(&mut self, body: &'a [Stmt]) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        let mut terminal = false;
        for statement in body {
            if terminal {
                let mut target = self
                    .file
                    .source
                    .target(statement.range(), "UnreachableCode");
                target.status = "WARN".into();
                target.message =
                    "Unreachable code statement detected after terminal return/raise".into();
                self.targets.push(target);
                break;
            }
            terminal = matches!(
                statement,
                Stmt::Return(_) | Stmt::Raise(_) | Stmt::Break(_) | Stmt::Continue(_)
            );
        }
        visitor::walk_body(self, body);
    }
}
fn append_finding(
    output: &mut AnalysisOutput,
    request: &AnalysisRequest,
    target: &InspectionTarget,
) {
    let name = &target.target_name;
    let mut finding = Finding::new(
        format!("dead.{}", name.to_lowercase().replace('_', "-")),
        &target.message,
        finding::span(target),
        "ici.deadcode",
    );
    finding.fingerprint = finding::sha1_short(
        "dead-",
        &format!(
            "{}:{}:{name}:{}",
            target.file_path, target.start_line, target.message
        ),
    );
    finding.native_rule_id = name.clone();
    finding.severity = "medium".into();
    finding.confidence = "medium".into();
    finding.category = "maintainability".into();
    finding.component_id = Some(request.component_id.clone());
    finding.task_id = Some(request.task_id.clone());
    finding.evidence = EvidenceLevel::Estimated;
    output.observation.findings.push(finding);
}
pub fn analyze(
    request: &AnalysisRequest,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&AtomicBool>,
) {
    if output.observation.state == TaskState::Failed {
        for file in files {
            let mut target = InspectionTarget::at(&file.source.path, 1);
            target.target_name = "DeadCodeIncompleteScope".into();
            target.status = "SKIP".into();
            target.message="Cross-file dead-code correlation was not run because selected source parsing was incomplete".into();
            output.targets.push(target);
        }
        finish_status(output, request);
        return;
    }
    let index = ModuleIndex::new(request, files);
    let by_file: BTreeMap<_, _> = files
        .iter()
        .map(|file| (file.source.path.clone(), file.as_ref()))
        .collect();
    let mut local_references = BTreeMap::new();
    let mut imported_references = BTreeSet::new();
    for file in files {
        if cancelled(output, cancellation) {
            return;
        }
        let module = &index.by_file[&file.source.path];
        let mut imports = Imports {
            file,
            module,
            bindings: BTreeMap::new(),
            errors: vec![],
            cancellation,
        };
        imports.visit_body(file.body());
        for (line, message) in imports.errors {
            failure(
                output,
                &file.source.path,
                &SourceError {
                    code: "relative-import-context",
                    message,
                    line,
                    column: 1,
                },
            );
        }
        let mut references = References {
            file,
            imports: &imports.bindings,
            modules: &index,
            files: &by_file,
            local: BTreeSet::new(),
            imported: BTreeSet::new(),
            cancellation,
        };
        references.visit_body(file.body());
        local_references.insert(file.source.path.clone(), references.local);
        imported_references.extend(references.imported);
    }
    if output.observation.state == TaskState::Failed || cancelled(output, cancellation) {
        finish_status(output, request);
        return;
    }
    let mut checked = 0;
    for file in files {
        if cancelled(output, cancellation) {
            break;
        }
        let before = output.targets.len();
        let exports = exported(file);
        let definitions: BTreeMap<_, _> = file
            .body()
            .iter()
            .filter_map(|statement| {
                if let Stmt::FunctionDef(function) = statement {
                    Some((function.name.as_str(), function.range()))
                } else {
                    None
                }
            })
            .collect();
        for statement in file.body() {
            let Stmt::FunctionDef(function) = statement else {
                continue;
            };
            let name = function.name.as_str();
            if definitions.get(name) != Some(&function.range())
                || !name.starts_with('_')
                || name.starts_with("__")
                || !function.decorator_list.is_empty()
                || exports.contains(name)
            {
                continue;
            }
            let used = local_references[&file.source.path].contains(name)
                || imported_references.contains(&(file.source.path.clone(), name.into()));
            let span = file.source.span(function.range());
            let mut target = InspectionTarget::at(&file.source.path, span.start_line);
            target.end_line = span.end_line;
            target.target_name = format!("{name}()");
            target.status = if used { "PASS" } else { "WARN" }.into();
            target.message = if used {
                format!("Private function '{name}' is referenced")
            } else {
                format!("Private module-level function '{name}' is never referenced")
            };
            target.snippet = file
                .source
                .text
                .get(function.range().start().to_usize()..function.range().end().to_usize())
                .unwrap_or("")
                .into();
            if !used {
                append_finding(output, request, &target);
            }
            output.targets.push(target);
        }
        let mut unreachable = Unreachable {
            file,
            targets: vec![],
            cancellation,
        };
        unreachable.visit_body(file.body());
        unreachable
            .targets
            .sort_by_key(|target| (target.start_line, target.start_column));
        for target in unreachable.targets {
            append_finding(output, request, &target);
            output.targets.push(target);
        }
        if cancelled(output, cancellation) {
            break;
        }
        if output.targets.len() == before {
            let mut target = InspectionTarget::at(&file.source.path, 1);
            target.target_name = "DeadCode".into();
            target.message =
                "Python source was parsed and no dead-code findings were identified".into();
            output.targets.push(target);
        }
        checked += 1;
    }
    let issues = output.observation.findings.len();
    output.observation.measurements.extend([
        Measurement {
            unit: "files".into(),
            ..Measurement::measured("files_checked", checked as f64)
        },
        Measurement {
            unit: "issues".into(),
            evidence: EvidenceLevel::Estimated,
            ..Measurement::measured("issues_found", issues as f64)
        },
    ]);
    output.observation.limitations.push("Private-symbol references and local unreachable statements are bounded static heuristics; reflective exports and runtime reachability are not proven".into());
    output.extra = json!({"dead_symbols_count":issues,"python_issues":issues,"files_checked":checked,"module_index":index.by_module,"language_evidence":{"python":"ESTIMATED"},"analysis_provenance":["python-ast-heuristic"]});
    output.summary = if issues == 0 {
        "No dead-code findings detected in the analyzed scopes".into()
    } else {
        format!("{issues} dead-code finding(s) in selected Python sources")
    };
    finish_status(output, request);
}
