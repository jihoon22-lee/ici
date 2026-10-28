//! Native CLI-to-application bridge. Preparing a plan reads inputs and executable bytes only.
use crate::{
    commands::{CliError, Result, under},
    registry::Invocation,
};
use ici_application::{
    ApplicationError,
    graph::TaskGraph,
    plan::{Plan, Work, file_language},
    request::RunRequest,
};
use ici_config::ResolvedProject;
use ici_domain::Profile;
use ici_providers::{
    ProviderKind, ProviderPlan, ProviderRequest,
    resolver::{ResolvedTool, ToolRequest},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub struct PreparedPlan {
    pub project: ResolvedProject,
    pub cpp: crate::cpp::CppPlans,
    pub request: RunRequest,
    pub plan: Plan,
    pub graph: TaskGraph,
    pub providers: BTreeMap<String, ProviderPlan>,
    pub probes: BTreeMap<String, ici_domain::TaskSpec>,
    pub validations: BTreeMap<String, Vec<ici_validation::ValidationPlan>>,
    pub tools: BTreeMap<String, ResolvedTool>,
    pub native_configuration: BTreeMap<String, ici_providers::native_config::ConfigEvidence>,
    pub legacy_projection: bool,
    pub artifact_builds: BTreeMap<String, crate::artifact_build::ArtifactBuildPlan>,
}
pub fn prepare(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
) -> Result<PreparedPlan> {
    prepare_for_run(invocation, cwd, environment, static_only, "${run}")
}
pub fn prepare_for_run(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    run_label: &str,
) -> Result<PreparedPlan> {
    prepare_for_run_omitting(
        invocation,
        cwd,
        environment,
        static_only,
        run_label,
        &BTreeSet::new(),
    )
}
pub fn prepare_for_run_omitting(
    invocation: &Invocation,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    static_only: bool,
    run_label: &str,
    generated_outputs: &BTreeSet<String>,
) -> Result<PreparedPlan> {
    let explicit = invocation.value("config_path").map(|path| under(cwd, path));
    let local = invocation
        .value("local_config")
        .or_else(|| invocation.value("local_path"))
        .map(|path| under(cwd, path));
    let mut project = ici_config::resolve_with_environment(
        cwd,
        explicit.as_deref(),
        local.as_deref(),
        environment,
    )
    .map_err(|e| CliError(e.to_string()))?;
    let owned_outputs = match crate::artifact_build::owned_outputs(&project.root) {
        Ok(paths) => paths,
        Err(reason) => {
            project.limitations.push(format!("generated artifact ownership could not be verified; files remain analysis inputs: {reason}"));
            BTreeSet::new()
        }
    };
    for component in &mut project.components {
        component
            .files
            .retain(|path| !generated_outputs.contains(path) && !owned_outputs.contains(path));
    }
    let mut languages = invocation.values("language").to_vec();
    if invocation.flag("python") {
        languages.push("python".into());
    }
    if invocation.flag("cpp") {
        languages.push("cpp".into());
    }
    languages.sort();
    languages.dedup();
    let profile = invocation
        .value("profile")
        .map(|value| {
            serde_json::from_value::<Profile>(Value::String(value.into()))
                .map_err(|e| CliError(e.to_string()))
        })
        .transpose()?
        .or_else(|| crate::legacy::individual_profile(invocation));
    if let Some(id) = match crate::legacy::engine(&invocation.command()) {
        Some("sanitize") => Some("python.sanitize"),
        Some("build") => Some("python.artifact"),
        _ => None,
    } {
        project
            .checks
            .entry(id.into())
            .or_insert(ici_config::CheckSetting {
                enabled: true,
                required: true,
                exemptions: BTreeMap::new(),
                settings: json!({"enabled":true}),
            });
    }
    if crate::legacy::engine(&invocation.command()) == Some("dead") {
        project
            .checks
            .entry("cpp.dead".into())
            .or_insert(ici_config::CheckSetting {
                enabled: true,
                required: true,
                exemptions: BTreeMap::new(),
                settings: json!({"enabled":true}),
            });
    }
    let mut supplemental = crate::validation::supplemental_checks(&project);
    supplemental.extend(crate::cpp::supplemental_checks(&project));
    let mut definitions = ici_domain::builtin_catalog().checks;
    definitions.extend(supplemental.iter().cloned());
    let selected_checks = crate::legacy::select(invocation, &mut project, &definitions);
    let request = RunRequest {
        components: invocation.values("component").to_vec(),
        languages,
        profile,
        require_full: invocation.flag("require_full"),
        static_only,
        checks: selected_checks,
    };
    let mut cpp = crate::cpp::CppPlans::default();
    let mut artifact_builds = BTreeMap::new();
    let mut validations = BTreeMap::new();
    let mut providers = BTreeMap::new();
    let mut tools = BTreeMap::new();
    let mut native_configuration = BTreeMap::new();
    let pack = tool_pack(environment);
    let mut plan = ici_application::plan::plan_with_checks(
        &project,
        &request,
        &supplemental,
        |component, check, task_id| {
            if check.id == "python.artifact" {
                return Ok(
                    match crate::artifact_build::describe(&project, component, task_id) {
                        Ok(build) => {
                            let work = Work::native_prepare(build.task.clone());
                            artifact_builds.insert(task_id.into(), build);
                            work
                        }
                        Err(reason) => Work::Blocked { reason },
                    },
                );
            }
            let component_root = if component.root == "." {
                project.root.clone()
            } else {
                project.root.join(&component.root)
            };
            if let Some(work) = cpp.bind(&project, component, check, task_id, environment) {
                return Ok(work);
            }
            if let Some(bound) = crate::cpp_validation::bind(
                &project,
                component,
                check,
                task_id,
                &request,
                environment,
                run_label,
            ) {
                tools.extend(bound.tools);
                if !bound.plans.is_empty() {
                    validations.insert(task_id.into(), bound.plans);
                }
                return Ok(bound.work);
            }
            if let Some(bound) = crate::validation::bind(
                &project,
                component,
                check,
                task_id,
                &request,
                environment,
                run_label,
            ) {
                if let Some(tool) = bound.tool {
                    tools.insert(format!("{}:python", component.id), tool);
                }
                if !bound.plans.is_empty() {
                    validations.insert(task_id.into(), bound.plans);
                }
                return Ok(bound.work);
            }
            let tool_id = match check.id.as_str() {
                "python.lint" | "python.format" => "ruff",
                "python.type" => match component.python_type_provider.as_deref() {
                    None | Some("ty") => "ty",
                    Some("mypy") => "mypy",
                    Some(other) => {
                        return Err(ApplicationError(format!(
                            "unknown Python type provider {other}"
                        )));
                    }
                },
                "rust.clippy" => "clippy",
                "javascript.lint" | "typescript.lint" => "eslint",
                "typescript.type" => "tsc",
                // Runtime/build/compile checks bind through their own context adapters, not a null
                // registry tool. They stay visibly blocked until those adapters supply real work.
                "python.test"
                | "python.coverage"
                | "python.compat-runtime"
                | "cpp.compile"
                | "cpp.diagnostics"
                | "cpp.tidy"
                | "cpp.test"
                | "cpp.coverage"
                | "cpp.artifact"
                | "cpp.sanitize"
                | "cpp.tsan"
                | "cpp.binary-compat"
                | "integration" => {
                    return Ok(Work::Blocked {
                        reason: format!(
                            "{} requires its native runtime/build context adapter",
                            check.id
                        ),
                    });
                }
                _ if matches!(check.language.as_str(), "python" | "cpp") => {
                    return Ok(Work::Internal);
                }
                _ => {
                    return Ok(Work::Blocked {
                        reason: format!("no native provider for {}", check.id),
                    });
                }
            };
            let kind = ProviderKind::for_check(&check.id, tool_id)
                .ok_or_else(|| ApplicationError(format!("no provider for {}", check.id)))?;
            let explicit = project
                .tools
                .get(tool_id)
                .and_then(|setting| setting.path.as_deref())
                .map(|path| under(&project.root, path));
            let tool = match (ToolRequest {
                id: tool_id.into(),
                explicit,
                project_root: component_root.clone(),
                pack_root: pack.clone(),
                path_env: environment.get("PATH").cloned(),
            })
            .resolve()
            {
                Ok(tool) => tool,
                Err(error) => {
                    return Ok(Work::Blocked {
                        reason: error.to_string(),
                    });
                }
            };
            let targets: Vec<_> = component
                .files
                .iter()
                .filter(|p| file_language(p) == Some(check.language.as_str()))
                .cloned()
                .collect();
            let evidence = ici_providers::native_config::discover_native_configuration(
                kind,
                &project.root,
                &component_root,
                &targets,
            )
            .map_err(|e| ApplicationError(e.to_string()))?;
            let mut provider_request = ProviderRequest::new(
                kind,
                tool.clone(),
                &project.root,
                &component_root,
                targets,
                task_id,
            );
            provider_request.component_id = Some(component.id.clone());
            provider_request.analysis_unit_ids =
                vec![format!("{}.{}", component.id, check.language)];
            provider_request.input_refs = evidence
                .files
                .keys()
                .map(|p| p.to_string_lossy().into_owned())
                .collect();
            provider_request.options.jobs = 2;
            provider_request.options.python_version = component
                .settings
                .pointer("/python/target_version")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if kind == ProviderKind::Tsc {
                provider_request.options.project_config = ["tsconfig.json", "jsconfig.json"]
                    .into_iter()
                    .find(|name| component_root.join(name).is_file())
                    .map(str::to_owned);
            }
            let decided = component
                .checks
                .get(&check.id)
                .or_else(|| project.checks.get(&check.id));
            provider_request.options.advisory = kind == ProviderKind::Ty
                && component.python_type_provider.is_none()
                && !decided.is_some_and(|setting| {
                    setting.settings.get("required").and_then(Value::as_bool) == Some(true)
                });
            let mut provider = ici_providers::plan(&provider_request)
                .map_err(|e| ApplicationError(e.to_string()))?;
            provider.scope_complete = evidence.complete;
            provider.limitations.extend(evidence.limitations.clone());
            provider.task.cacheable = provider.scope_complete && !provider.task.mutating();
            let work = if provider.advisory {
                Work::process_advisory(provider.task.clone())
            } else {
                Work::process(provider.task.clone())
            };
            tools.insert(format!("{}:{tool_id}", component.id), tool);
            providers.insert(task_id.to_owned(), provider);
            native_configuration.insert(task_id.to_owned(), evidence);
            Ok(work)
        },
    )
    .map_err(|e| CliError(e.to_string()))?;
    tools.extend(cpp.tools.clone());
    let probes = providers
        .iter()
        .filter(|(_, provider)| {
            matches!(
                provider.kind,
                ProviderKind::RuffLint | ProviderKind::RuffFormat
            )
        })
        .map(|(id, provider)| {
            ici_providers::plan::ruff_inventory_task(provider).map(|task| (id.clone(), task))
        })
        .collect::<std::result::Result<_, _>>()
        .map_err(|e| CliError(e.to_string()))?;
    cpp.finalize_plan(&mut plan);
    let graph = TaskGraph::build(&plan.checks).map_err(|e| CliError(e.to_string()))?;
    Ok(PreparedPlan {
        project,
        cpp,
        request,
        plan,
        graph,
        providers,
        probes,
        validations,
        tools,
        native_configuration,
        legacy_projection: crate::legacy::is_legacy(invocation),
        artifact_builds,
    })
}
fn tool_pack(environment: &BTreeMap<String, String>) -> Option<PathBuf> {
    if let Some(directory) = environment
        .get("ICI_NATIVE_TOOL_PACK")
        .filter(|p| !p.is_empty())
    {
        return Some(PathBuf::from(directory));
    }
    std::env::current_exe().ok().and_then(|path| {
        let parent = path.parent()?;
        std::iter::once(parent.join("tools"))
            .chain(parent.parent().map(|root| root.join("tools")))
            .find(|directory| directory.join("manifest.json").is_file())
    })
}
impl PreparedPlan {
    pub fn document(&self) -> Value {
        let plans:Vec<_>=self.plan.components.iter().filter_map(|component| {
            let checks:Vec<_>=self.plan.checks.iter().filter(|p| &p.component_id==component).map(|p| {
                let blocked=self.graph.blocked.get(&p.task_id).map(String::as_str).or_else(||p.blocked());
                let mut entry=json!({"task_id":p.task_id,"check":p.check.id,"kind":if blocked.is_some(){"blocked"}else if matches!(p.work,Work::NativePrepare{..}){"native_prepare"}else if p.task().is_some(){"process"}else{"internal"}});
                if let Some(reason)=blocked {entry["blocked"]=json!(reason);}
                else if let Some(task)=p.task() {
                    entry["argv"]=json!(task.argv);
                    let steps:Vec<_>=self.probes.get(&p.task_id).into_iter().chain(p.tasks().iter()).collect();
                    entry["steps"]=json!(steps);
                    if !task.requires.is_empty(){entry["requires"]=json!(task.requires);}
                    if !task.work_dirs.is_empty(){entry["work_dirs"]=json!(task.work_dirs);}
                    if p.advisory(){entry["advisory"]=json!(true);}
                }
                entry
            }).collect();
            (!checks.is_empty()).then(||json!({"component":component,"checks":checks}))
        }).collect();
        let mutating: BTreeMap<_, _> = self
            .plan
            .checks
            .iter()
            .filter(|p| !self.graph.blocked.contains_key(&p.task_id))
            .filter_map(|p| {
                let directories: BTreeSet<_> = p
                    .tasks()
                    .iter()
                    .filter(|t| t.mutating())
                    .flat_map(|t| t.work_dirs.iter())
                    .collect();
                (!directories.is_empty()).then_some((&p.task_id, directories))
            })
            .collect();
        let edges: BTreeMap<_, _> = self
            .graph
            .edges
            .iter()
            .filter(|(id, _)| !self.graph.blocked.contains_key(*id))
            .collect();
        let builds:Vec<_>=self.project.builds.iter().map(|build| {
            let linked:BTreeSet<_>=self.project.components.iter().filter(|c|self.plan.components.contains(&c.id) && c.build.as_deref()==Some(build.id.as_str())).map(|c|&c.id).collect();
            json!({"id":build.id,"system":build.system,"variant":build.variant,"directory":build.directory,"definition":build.project,"artifacts":build.artifacts,"linked_by":linked})
        }).collect();
        json!({"schema_id":"ici.next.plan","schema_version":1,"root":self.project.root,"scope":{"components":self.plan.components,"languages":self.plan.languages,"profile":self.plan.profile,"require_full":self.request.require_full},"plans":plans,"graph":{"edges":edges,"shared":{},"mutating":mutating},"builds":builds,"coverage_gaps":self.plan.coverage_gaps,"limitations":self.plan.limitations})
    }
    pub fn text(&self) -> String {
        let mut text = format!(
            "{}: {} check(s)\n",
            self.project.workspace_name,
            self.plan.checks.len()
        );
        for component in &self.plan.components {
            text.push_str(&format!("{component}:\n"));
            for check in self
                .plan
                .checks
                .iter()
                .filter(|p| &p.component_id == component)
            {
                let detail = if let Some(reason) = self.graph.blocked.get(&check.task_id) {
                    format!("blocked — {reason}")
                } else if !check.tasks().is_empty() {
                    format!(
                        "{}{}",
                        self.probes
                            .get(&check.task_id)
                            .into_iter()
                            .chain(check.tasks().iter())
                            .map(|task| task
                                .argv
                                .iter()
                                .map(|v| display_argument(v))
                                .collect::<Vec<_>>()
                                .join(" "))
                            .collect::<Vec<_>>()
                            .join(" → "),
                        if check.advisory() {
                            " [advisory findings; completion required]"
                        } else {
                            ""
                        }
                    )
                } else {
                    format!("{} (ici)", check.check.title)
                };
                text.push_str(&format!("  {}: {detail}\n", check.task_id));
            }
        }
        for gap in &self.plan.coverage_gaps {
            text.push_str(&format!("  coverage gap: {gap}\n"));
        }
        for limitation in &self.plan.limitations {
            text.push_str(&format!("  note: {limitation}\n"));
        }
        text
    }
}
fn display_argument(value: &str) -> String {
    if value.chars().any(|c| {
        c.is_whitespace() || matches!(c, '\'' | '"' | '$' | '`' | ';' | '&' | '|' | '<' | '>')
    }) {
        format!("'{}'", value.replace('\'', "'\\''"))
    } else {
        value.into()
    }
}
