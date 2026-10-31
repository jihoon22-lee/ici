//! Legacy binary production scope stays separate from source-reading and recorded diagnostics.
use super::*;

pub fn external_build_directories(
    project: &ResolvedProject,
    component: &Component,
    environment: &BTreeMap<String, String>,
) -> Result<Vec<String>, String> {
    let values = string_list(
        component
            .settings
            .pointer("/project/cpp_external_build_dirs")
            .or_else(|| {
                project
                    .effective
                    .pointer("/project/cpp_external_build_dirs")
            }),
    );
    let mut directories = BTreeSet::new();
    for value in values {
        let resolved = project
            .resolve_path("project.cpp_external_build_dirs", &value, environment)
            .map_err(|error| error.to_string())?;
        let path = project.root.join(&resolved);
        if path.is_dir() {
            let canonical = path.canonicalize().map_err(|error| error.to_string())?;
            let relative = canonical
                .strip_prefix(&project.root)
                .map_err(|_| "external build directory leaves workspace")?;
            directories.insert(if relative.as_os_str().is_empty() {
                ".".into()
            } else {
                relative.to_string_lossy().into_owned()
            });
        }
    }
    Ok(directories.into_iter().collect())
}
fn excluded(path: &str, directories: &[String]) -> bool {
    directories.iter().any(|directory| {
        directory == "."
            || path == directory
            || path
                .strip_prefix(directory)
                .is_some_and(|tail| tail.starts_with('/'))
    })
}
impl CppPlans {
    pub(super) fn legacy_compile_context(
        &self,
        id: &str,
        context: &CompilationContext,
    ) -> CompilationContext {
        let mut scoped = context.clone();
        if let Some(directories) = self.compile_externals.get(id) {
            scoped
                .selected_sources
                .retain(|path| !excluded(path, directories));
            scoped
                .uncovered_sources
                .retain(|path| !excluded(path, directories));
            scoped
                .source_coverage
                .retain(|path, _| !excluded(path, directories));
        }
        scoped
    }
    pub(super) fn compile_scope_is_empty(&self, id: &str) -> bool {
        self.compile_externals.contains_key(id)
            && self
                .compiler_components
                .get(id)
                .and_then(|component| self.contexts.get(component))
                .is_some_and(|context| {
                    !self
                        .legacy_compile_context(id, context)
                        .selected_sources
                        .iter()
                        .any(|path| ici_cpp::is_translation_unit(path))
                })
    }
    pub(super) fn preserve_diagnostic_scope(&self, component: &str, output: &mut AnalysisOutput) {
        let Some(context) = self.contexts.get(component) else {
            return;
        };
        if context.complete() {
            return;
        }
        for path in &context.uncovered_sources {
            let mut target = InspectionTarget::at(path, 1);
            target.target_name = "C++SyntaxContextMissing".into();
            target.status = "ERROR".into();
            target.message =
                "No recorded translation-unit configuration covers this selected source".into();
            output.targets.push(target);
        }
        for diagnostic in context
            .diagnostics
            .iter()
            .chain(context.units.iter().flat_map(|unit| &unit.diagnostics))
        {
            if diagnostic.level == "error" {
                let path = if diagnostic.source.is_empty() {
                    context.database_path.as_deref().unwrap_or(".")
                } else {
                    &diagnostic.source
                };
                let mut target = InspectionTarget::at(path, 1);
                target.target_name = "C++SyntaxContextDiagnostic".into();
                target.status = "ERROR".into();
                target.message = format!("{}: {}", diagnostic.code, diagnostic.message);
                output.targets.push(target);
            }
        }
        if output.observation.state != TaskState::Cancelled {
            output.observation.state = TaskState::Failed;
        }
        output.status = "ERROR".into();
        output.observation.limitations.push("Compiler diagnostics cover only the recorded subset; selected compilation context is incomplete".into());
    }
}
