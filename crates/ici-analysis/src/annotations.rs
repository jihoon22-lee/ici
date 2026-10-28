//! Explicit legacy annotation coverage, never a replacement for a type checker.
use crate::{cancelled, finding, finish_status, parser::ParsedFile};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, EvidenceLevel, Finding, InspectionTarget, Measurement,
};
use ruff_python_ast::{
    Stmt,
    visitor::{self, Visitor},
};
use serde_json::json;
use std::sync::{Arc, atomic::AtomicBool};

struct Functions<'a> {
    file: &'a ParsedFile,
    targets: Vec<InspectionTarget>,
    warn: bool,
    cancellation: Option<&'a AtomicBool>,
}
impl<'a> Visitor<'a> for Functions<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if crate::cancellation_requested(self.cancellation) {
            return;
        }
        if let Stmt::FunctionDef(function) = statement {
            let name = function.name.as_str();
            if self.warn
                && function.returns.is_none()
                && !(name.starts_with("__") && name.ends_with("__"))
            {
                let line = self
                    .file
                    .source
                    .location(
                        crate::ast_util::function_range(self.file, function)
                            .start()
                            .to_usize(),
                    )
                    .0;
                let mut target = InspectionTarget::at(&self.file.source.path, line);
                target.target_name = format!("{name}()");
                target.message = format!("Function '{name}' is missing return type annotation");
                target.status = "WARN".into();
                self.targets.push(target);
            }
        }
        visitor::walk_stmt(self, statement);
    }
}
pub fn analyze(
    request: &AnalysisRequest,
    files: &[Arc<ParsedFile>],
    output: &mut AnalysisOutput,
    cancellation: Option<&AtomicBool>,
) {
    let warn = request
        .options
        .get("warn_on_missing_annotation")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let mut checked = 0;
    for file in files {
        if cancelled(output, cancellation) {
            break;
        }
        let mut functions = Functions {
            file,
            targets: vec![],
            warn,
            cancellation,
        };
        functions.visit_body(file.body());
        for target in functions.targets {
            let mut result = Finding::new(
                "type.missing-return-annotation",
                &target.message,
                finding::span(&target),
                "ici.type",
            );
            result.native_rule_id = "missing-return-annotation".into();
            result.evidence = EvidenceLevel::Estimated;
            result.severity = "low".into();
            result.confidence = "high".into();
            result.category = "maintainability".into();
            result.component_id = Some(request.component_id.clone());
            result.task_id = Some(request.task_id.clone());
            output.observation.findings.push(result);
            output.targets.push(target);
        }
        if cancelled(output, cancellation) {
            break;
        }
        let mut target = InspectionTarget::at(&file.source.path, 1);
        target.target_name = "AnnotationCoverage".into();
        target.message=if warn { "Python return annotation coverage was inspected" } else { "Python return annotation coverage was inspected; missing-annotation warnings are disabled" }.into();
        output.targets.push(target);
        checked += 1;
    }
    output.observation.limitations.push("Legacy annotation coverage is ESTIMATED and does not check type relations; it cannot replace a configured external type checker".into());
    output.observation.measurements.push(Measurement {
        unit: "files".into(),
        ..Measurement::measured("files_checked", checked as f64)
    });
    output.extra = json!({"annotation_only":true,"files_checked":checked,"warn_on_missing_annotation":warn,"language_evidence":{"python":"ESTIMATED"}});
    output.summary = format!("Inspected return annotations in {checked} Python source file(s)");
    finish_status(output, request);
}
