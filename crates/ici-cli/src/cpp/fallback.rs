//! Explicit legacy syntax fallback. Recorded compilation commands never receive these flags.
use super::*;
use ici_domain::EvidenceLevel;
use ici_execution::{Cancellation, Scheduler};

const MARKER: &str = "{observed-pkg-config-cflags}";
#[derive(Clone)]
pub(super) struct FallbackPlan {
    pub providers: Vec<ProviderPlan>,
    units: Vec<CompilationUnit>,
    compiler: ResolvedTool,
    probes: Vec<TaskSpec>,
    packages: Vec<String>,
    limitations: Vec<String>,
    observed: Option<Value>,
}

fn provider(
    project: &ResolvedProject,
    component: &Component,
    unit: &CompilationUnit,
    compiler: &ResolvedTool,
    id: &str,
) -> Result<ProviderPlan, String> {
    let replay = ici_cpp::replay_command(
        &project.root,
        unit,
        &compiler.path,
        ReplayPurpose::Diagnostics,
    )
    .map_err(|error| error.to_string())?;
    let mut request = ProviderRequest::new(
        ProviderKind::Compiler,
        compiler.clone(),
        &project.root,
        &replay.cwd,
        vec![unit.source.clone()],
        id,
    );
    request.component_id = Some(component.id.clone());
    request.analysis_unit_ids = vec![unit_id(component, unit)];
    request.variant = Some("legacy-c++17-fallback".into());
    request.options.compiler_argv = replay.argv;
    request.options.source_token = Some(replay.source.to_string_lossy().into_owned());
    let mut planned = ici_providers::plan(&request).map_err(|error| error.to_string())?;
    planned.scope_complete = false;
    planned.limitations.push("Legacy c++17 syntax fallback has no recorded build configuration or compiler header/module closure; findings are estimated".into());
    Ok(planned)
}

impl CppPlans {
    pub fn observed_configuration_for(&self, id: &str) -> Option<Value> {
        self.fallbacks
            .get(id)
            .and_then(|plan| plan.observed.clone())
    }
    pub(super) fn bind_fallback(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<Work, String> {
        let sources: Vec<_> = component
            .files
            .iter()
            .filter(|path| ici_cpp::is_translation_unit(path))
            .collect();
        if sources.is_empty() || sources.len() > 2048 {
            return Err(
                "legacy syntax fallback requires 1..2048 selected translation units".into(),
            );
        }
        let super::package_flags::LegacyFlagsPlan {
            compiler,
            includes: flags,
            probes,
            packages,
            limitations,
            tools,
        } = super::package_flags::plan_flags(project, component, id, environment)?;
        self.tools.extend(tools);
        let mut providers = vec![];
        let mut units = vec![];
        for (index, source) in sources.iter().enumerate() {
            let argv = std::iter::once(compiler.path.display().to_string())
                .chain(std::iter::once("-std=c++17".into()))
                .chain(flags.iter().cloned())
                .chain(std::iter::once((*source).clone()))
                .collect::<Vec<_>>();
            let configuration = sha256(
                serde_json::to_vec(&ici_cpp::normalized_configuration_arguments(
                    &project.root,
                    &project.root,
                    &argv,
                ))
                .map_err(|error| error.to_string())?,
            );
            let unit = CompilationUnit {
                source: (*source).clone(),
                directory: ".".into(),
                compiler: "g++".into(),
                language: "c++".into(),
                standard: "c++17".into(),
                configuration,
                original_argv: argv.clone(),
                argv,
                output: String::new(),
                target: String::new(),
                variant: "legacy-c++17-fallback".into(),
                defines: vec![],
                include_paths: vec![],
                sysroot: String::new(),
                sysroot_scope: String::new(),
                response_files: vec![],
                diagnostics: vec![],
            };
            let mut planned = provider(
                project,
                component,
                &unit,
                &compiler,
                &format!("{id}.unit{index}"),
            )?;
            if !probes.is_empty() {
                planned.task.argv.push(MARKER.into());
            }
            providers.push(planned);
            units.push(unit);
        }
        let tasks = probes
            .iter()
            .cloned()
            .chain(providers.iter().map(|provider| provider.task.clone()))
            .collect();
        self.fallbacks.insert(
            id.into(),
            FallbackPlan {
                providers,
                units,
                compiler,
                probes,
                packages,
                limitations,
                observed: None,
            },
        );
        Ok(Work::steps(tasks, false))
    }

    pub(super) fn run_fallback(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        request: &AnalysisRequest,
        scheduler: &Scheduler,
        cancellation: &Cancellation,
        events: &crate::events::EventLog,
    ) -> AnalysisOutput {
        let plan = self.fallbacks[&request.task_id].clone();
        let observed_flags = match super::package_flags::observe_flags(
            &plan.probes,
            &plan.limitations,
            scheduler,
            cancellation,
            events,
            &component.id,
        ) {
            Ok(observed) => observed,
            Err(super::package_flags::FlagsFailure::Invalid(error)) => {
                return failed(AnalysisOutput::new(&request.task_id, "pkg-config"), &error);
            }
            Err(super::package_flags::FlagsFailure::Execution(outcome)) => {
                return failed_probe(
                    &request.task_id,
                    "pkg-config",
                    &plan.units[0].source,
                    &outcome,
                    "pkg-config observation did not finish completely",
                );
            }
        };
        let flags = &observed_flags.arguments;
        let mut providers = vec![];
        for (index, base) in plan.units.iter().enumerate() {
            let mut unit = base.clone();
            let source = unit.argv.pop().expect("declared source operand");
            unit.argv.extend(flags.iter().cloned());
            unit.argv.push(source);
            unit.configuration = sha256(
                serde_json::to_vec(&ici_cpp::normalized_configuration_arguments(
                    &project.root,
                    &project.root,
                    &unit.argv,
                ))
                .expect("configuration json"),
            );
            let mut planned = match provider(
                project,
                component,
                &unit,
                &plan.compiler,
                &plan.providers[index].task.id,
            ) {
                Ok(provider) => provider,
                Err(error) => {
                    return failed(AnalysisOutput::new(&request.task_id, "compiler"), &error);
                }
            };
            planned.context.source_coordinates =
                plan.providers[index].context.source_coordinates.clone();
            providers.push(planned);
        }
        let concrete: Vec<_> = providers
            .iter()
            .map(|provider| provider.task.clone())
            .collect();
        let observed = json!({"mode":"legacy-c++17-fallback","packages":plan.packages,"package_outcomes":observed_flags.outcomes,"compiler_argv":concrete.iter().map(|task|ici_cpp::normalized_configuration_arguments(&project.root,Path::new(&task.cwd),&task.argv)).collect::<Vec<_>>()});
        self.fallbacks
            .get_mut(&request.task_id)
            .expect("bound fallback")
            .observed = Some(observed);
        let results = match run_cpp_tasks(
            &concrete,
            scheduler,
            cancellation,
            events,
            &component.id,
            "legacy c++17 syntax fallback",
        ) {
            Ok(results) => results,
            Err(error) => return failed(AnalysisOutput::new(&request.task_id, "compiler"), &error),
        };
        let outputs = results
            .iter()
            .map(|outcome| {
                ici_providers::observe_full(
                    providers
                        .iter()
                        .find(|provider| provider.task.id == outcome.task_id)
                        .expect("declared fallback"),
                    outcome,
                )
            })
            .collect();
        let mut output = merge(&request.task_id, "compiler", outputs);
        for finding in &mut output.observation.findings {
            finding.evidence = EvidenceLevel::Estimated;
            finding.confidence = "medium".into();
            finding.limitations.push(
                "No recorded build configuration; legacy c++17 heuristic syntax fallback".into(),
            );
        }
        for metric in &mut output.observation.measurements {
            metric.evidence = EvidenceLevel::Estimated;
        }
        if output.observation.state == TaskState::Succeeded && output.status == "PASS" {
            output.status = "WARN".into();
        }
        output
            .observation
            .limitations
            .extend(observed_flags.limitations);
        output.observation.limitations.push("No compilation database was available; syntax analysis used the legacy c++17 heuristic fallback. Package metadata and compiler header/module closure have not been established reusable".into());
        output.extra["cpp_analysis_mode"] = json!("heuristic");
        output.extra["pkg_config"] = json!(observed_flags.records);
        output.extra["concrete_tasks"] = json!(concrete);
        output
    }
}
