//! Existing compilation evidence and artifact contracts. This facade never invents build commands.
use ici_application::plan::{Work, file_language};
use ici_config::{Component, ResolvedProject};
use ici_cpp::{CompilationContext, CompilationUnit, ContextRequest, ReplayPurpose};
use ici_domain::{
    AnalysisOutput, AnalysisRequest, CheckDefinition, Finding, InspectionTarget, Measurement,
    SourceSpan, TaskSpec, TaskState, sha256,
};
use ici_providers::{
    ProviderKind, ProviderPlan, ProviderRequest,
    resolver::{ResolvedTool, ToolRequest},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

#[derive(Clone, Debug)]
pub struct IncludeProbe {
    pub task: TaskSpec,
    pub unit: CompilationUnit,
}
#[derive(Default)]
pub struct CppPlans {
    pub contexts: BTreeMap<String, CompilationContext>,
    pub diagnostics: BTreeMap<String, Vec<ProviderPlan>>,
    pub includes: BTreeMap<String, Vec<IncludeProbe>>,
    pub tools: BTreeMap<String, ResolvedTool>,
    /// Before/after input metadata only, never a cache identity or a claim about artifact contents.
    pub artifacts: BTreeMap<String, Value>,
    resolved: BTreeMap<String, std::result::Result<ResolvedTool, String>>,
}
impl CppPlans {
    pub fn bind(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        check: &CheckDefinition,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Option<Work> {
        if check.language != "cpp" {
            return None;
        }
        if !self.contexts.contains_key(&component.id) {
            self.contexts
                .insert(component.id.clone(), context(project, component));
        }
        let context = self.contexts[&component.id].clone();
        match check.id.as_str() {
            "cpp.compile" => {
                // Unity sources are containers; observed includes must establish original membership.
                if (context.unity_build && !context.uncovered_sources.is_empty())
                    || context
                        .units
                        .iter()
                        .any(|unit| !component.files.contains(&unit.source))
                {
                    let mut probes = vec![];
                    for (index, unit) in context.units.iter().enumerate() {
                        let tool = match self.compiler(project, unit, environment) {
                            Ok(tool) => tool,
                            Err(reason) => return Some(blocked(reason)),
                        };
                        let mut task = match ici_cpp::compiler_probe_task(
                            &project.root,
                            unit,
                            &tool.path,
                            ReplayPurpose::IncludeTrace,
                            &format!("{id}.include{index}"),
                        ) {
                            Ok(task) => task,
                            Err(error) => return Some(blocked(error.to_string())),
                        };
                        task.analysis_unit_ids = vec![unit_id(component, unit)];
                        task.input_refs = task
                            .input_refs
                            .into_iter()
                            .map(|path| {
                                if Path::new(&path).is_absolute() {
                                    path
                                } else {
                                    project.root.join(path).to_string_lossy().into_owned()
                                }
                            })
                            .collect();
                        probes.push(IncludeProbe {
                            task,
                            unit: unit.clone(),
                        });
                    }
                    if probes.is_empty() {
                        return Some(Work::Internal);
                    }
                    let tasks = probes.iter().map(|probe| probe.task.clone()).collect();
                    self.includes.insert(id.into(), probes);
                    Some(Work::steps(tasks, false))
                } else {
                    Some(Work::Internal)
                }
            }
            "cpp.diagnostics" => {
                if context.units.is_empty() {
                    return Some(blocked(
                        "no usable recorded compilation units; provide the declared build's compilation database",
                    ));
                }
                let mut providers = vec![];
                for (index, unit) in context.units.iter().enumerate() {
                    let tool = match self.compiler(project, unit, environment) {
                        Ok(tool) => tool,
                        Err(reason) => return Some(blocked(reason)),
                    };
                    let replay = match ici_cpp::replay_command(
                        &project.root,
                        unit,
                        &tool.path,
                        ReplayPurpose::Diagnostics,
                    ) {
                        Ok(replay) => replay,
                        Err(error) => return Some(blocked(error.to_string())),
                    };
                    let mut request = ProviderRequest::new(
                        ProviderKind::Compiler,
                        tool,
                        &project.root,
                        &replay.cwd,
                        vec![unit.source.clone()],
                        format!("{id}.unit{index}"),
                    );
                    request.component_id = Some(component.id.clone());
                    request.analysis_unit_ids = vec![unit_id(component, unit)];
                    request.variant = Some(unit.variant.clone());
                    request.options.compiler_argv = replay.argv;
                    request.options.source_token =
                        Some(replay.source.to_string_lossy().into_owned());
                    request.input_refs = context
                        .database_path
                        .iter()
                        .map(|path| project.root.join(path).to_string_lossy().into_owned())
                        .chain(unit.response_files.iter().map(|input| {
                            project
                                .root
                                .join(&input.path)
                                .to_string_lossy()
                                .into_owned()
                        }))
                        .collect();
                    let mut provider = match ici_providers::plan(&request) {
                        Ok(provider) => provider,
                        Err(error) => return Some(blocked(error.to_string())),
                    };
                    provider.scope_complete = false;
                    provider.limitations.push(
                        "compiler header/module dependency closure has not been proven reusable"
                            .into(),
                    );
                    providers.push(provider);
                }
                let tasks = providers
                    .iter()
                    .map(|provider| provider.task.clone())
                    .collect();
                self.diagnostics.insert(id.into(), providers);
                Some(Work::steps(tasks, false))
            }
            "cpp.artifact" => {
                let Some(build) = linked_build(project, component) else {
                    return Some(blocked(
                        "no build linked; declare [builds.<id>] and components[].build",
                    ));
                };
                if build.artifacts.is_empty() {
                    return Some(blocked(format!(
                        "build {} declares no artifact contract",
                        build.id
                    )));
                }
                self.artifacts
                    .insert(component.id.clone(), artifact_identity(project, build));
                Some(Work::Internal)
            }
            _ => None,
        }
    }
    fn compiler(
        &mut self,
        project: &ResolvedProject,
        unit: &CompilationUnit,
        environment: &BTreeMap<String, String>,
    ) -> std::result::Result<ResolvedTool, String> {
        let executable = unit
            .argv
            .first()
            .map(String::as_str)
            .unwrap_or(&unit.compiler);
        let key = format!("{}:{executable}", unit.directory);
        if let Some(result) = self.resolved.get(&key) {
            return result.clone();
        }
        let name = Path::new(executable)
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| "recorded compiler has no valid executable name".to_string())?;
        let configured = project
            .tools
            .get(name)
            .and_then(|tool| tool.path.as_deref());
        let explicit = configured
            .map(|path| {
                if Path::new(path).is_absolute() {
                    PathBuf::from(path)
                } else {
                    project.root.join(path)
                }
            })
            .or_else(|| executable.contains('/').then(|| PathBuf::from(executable)));
        let request = ToolRequest {
            id: name.into(),
            explicit,
            project_root: project.root.join(&unit.directory),
            pack_root: None,
            path_env: environment.get("PATH").cloned(),
        };
        let result = request.resolve().map_err(|error| error.to_string());
        if let Ok(tool) = &result {
            self.tools.insert(format!("compiler:{key}"), tool.clone());
        }
        self.resolved.insert(key, result.clone());
        result
    }
    pub fn configuration_identity(&self) -> Value {
        json!({"contexts":self.contexts.iter().map(|(id,context)|(id,json!({"database":context.database_path,"digest":context.database_digest,"responses":context.response_files,"units":context.units,"selected":context.selected_sources}))).collect::<BTreeMap<_,_>>(),"artifacts":self.artifacts})
    }
}
fn blocked(reason: impl Into<String>) -> Work {
    Work::Blocked {
        reason: reason.into(),
    }
}
pub fn linked_build<'a>(
    project: &'a ResolvedProject,
    component: &Component,
) -> Option<&'a ici_config::Build> {
    component
        .build
        .as_ref()
        .and_then(|id| project.builds.iter().find(|build| &build.id == id))
}
fn context(project: &ResolvedProject, component: &Component) -> CompilationContext {
    let explicit = component
        .settings
        .pointer("/project/compile_database")
        .and_then(Value::as_str)
        .filter(|path| !path.is_empty())
        .map(|path| {
            if Path::new(path).is_absolute() {
                path.into()
            } else {
                let base = Path::new(&component.declared_in)
                    .parent()
                    .filter(|path| path.is_absolute())
                    .unwrap_or(&project.root);
                base.join(path).to_string_lossy().into_owned()
            }
        });
    let mut directories = BTreeSet::new();
    if component.root != "." {
        directories.insert(component.root.clone());
        directories.insert(format!("{}/build", component.root));
    }
    let build = linked_build(project, component);
    if let Some(directory) = build
        .and_then(|build| build.directory.as_deref())
        .filter(|directory| *directory != ".")
    {
        directories.insert(
            directory
                .trim_start_matches("./")
                .trim_end_matches('/')
                .to_owned(),
        );
    }
    let mut context = ici_cpp::load_compilation_context(
        &project.root,
        &ContextRequest {
            database: explicit,
            variant: build.map(|build| build.variant.clone()),
            build_directories: directories.into_iter().collect(),
            selected_sources: component
                .files
                .iter()
                .filter(|file| file_language(file) == Some("cpp"))
                .cloned()
                .collect(),
        },
    );
    // A shared database does not widen a component into unrelated production translation units.
    let selected: BTreeSet<_> = component.files.iter().collect();
    let build_directory = build.and_then(|build| build.directory.as_deref());
    context.units.retain(|unit| {
        selected.contains(&unit.source)
            || unit.source.contains("/Unity/")
            || unit.source.contains("unity_")
            || build_directory.is_some_and(|directory| {
                unit.source
                    .strip_prefix(directory)
                    .is_some_and(|tail| tail.starts_with('/'))
            })
    });
    context
        .source_coverage
        .retain(|source, _| context.units.iter().any(|unit| &unit.source == source));
    context
}
pub fn analyze_contract(
    project: &ResolvedProject,
    component: &Component,
    request: &AnalysisRequest,
) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&request.task_id, "ici.artifacts");
    let Some(build) = linked_build(project, component) else {
        return failed(output, "no declared build linked");
    };
    let directory = build.directory.as_deref().unwrap_or(".");
    let base = crate::files::under(&project.root, directory);
    let entries = if !base.exists() {
        vec![]
    } else {
        match crate::files::tree(&project.root, &base) {
            Ok(entries) => entries,
            Err(error) => return failed(output, &format!("artifact inventory: {error}")),
        }
    };
    let view = Component {
        root: directory.into(),
        ..component.clone()
    };
    let candidates: Vec<_> = entries.iter().map(|entry| entry.relative.clone()).collect();
    let mut satisfied = 0u64;
    let mut produced = 0u64;
    for pattern in &build.artifacts {
        let matches = match ici_config::matching_source_paths(
            &view,
            &candidates,
            std::slice::from_ref(pattern),
        ) {
            Ok(matches) => matches,
            Err(error) => return failed(output, &error.to_string()),
        };
        let mut count = 0u64;
        for path in matches {
            let entry = entries
                .iter()
                .find(|entry| entry.relative == path)
                .expect("matched bounded inventory");
            let mut target = InspectionTarget::at(&path, 1);
            target.target_name = "DeclaredArtifact".into();
            target.metrics.insert("build".into(), json!(build.id));
            if !entry.contained {
                target.status = "FAIL".into();
                target.message = format!(
                    "declared artifact {path} of build '{}' resolves outside the workspace",
                    build.id
                );
                add_finding(
                    &mut output,
                    &target,
                    "artifact.invalid",
                    &format!("invalid:{}:{path}", build.id),
                );
            } else if entry.file {
                count += 1;
                target.message = "Declared regular artifact exists inside the workspace".into();
            } else {
                continue;
            }
            output.targets.push(target);
        }
        if count > 0 {
            satisfied += 1;
            produced += count;
        } else {
            let location = if directory == "." {
                build
                    .origin
                    .file
                    .strip_prefix(&format!("{}/", project.root.display()))
                    .unwrap_or("ici.toml")
            } else {
                directory
            };
            let mut target = InspectionTarget::at(location, 1);
            target.status = "FAIL".into();
            target.target_name = "MissingDeclaredArtifact".into();
            target.message = format!(
                "declared artifact {pattern:?} of build '{}' produced nothing: no regular match under the build directory",
                build.id
            );
            add_finding(
                &mut output,
                &target,
                "artifact.missing",
                &format!("missing:{}:{pattern}", build.id),
            );
            output.targets.push(target);
        }
    }
    output.observation.measurements = vec![
        Measurement {
            name: "artifacts.satisfied".into(),
            value: satisfied as f64,
            evidence: ici_domain::EvidenceLevel::Measured,
            unit: "globs".into(),
            numerator: Some(satisfied),
            denominator: (!build.artifacts.is_empty()).then_some(build.artifacts.len() as u64),
        },
        Measurement {
            name: "artifacts.produced".into(),
            value: produced as f64,
            evidence: ici_domain::EvidenceLevel::Measured,
            unit: "files".into(),
            numerator: None,
            denominator: None,
        },
    ];
    output.status = if output.observation.findings.is_empty() {
        "PASS"
    } else {
        "FAIL"
    }
    .into();
    output.summary = format!(
        "{satisfied}/{} declared artifact patterns satisfied",
        build.artifacts.len()
    );
    output.extra = json!({"build":build.id,"directory":directory,"patterns":build.artifacts,"satisfied":satisfied,"produced":produced});
    output
}
fn add_finding(output: &mut AnalysisOutput, target: &InspectionTarget, rule: &str, identity: &str) {
    let mut finding = Finding::new(
        rule,
        &target.message,
        SourceSpan::line(&target.file_path, target.start_line),
        &output.observation.provider,
    );
    finding.fingerprint = sha256(identity);
    finding.severity = "high".into();
    finding.confidence = "high".into();
    output.observation.findings.push(finding);
}
fn failed(mut output: AnalysisOutput, reason: &str) -> AnalysisOutput {
    output.observation.state = TaskState::Failed;
    output.observation.limitations.push(reason.into());
    output.status = "ERROR".into();
    output.summary = reason.into();
    output
}
fn artifact_identity(project: &ResolvedProject, build: &ici_config::Build) -> Value {
    use std::os::unix::fs::MetadataExt;
    let directory = build.directory.as_deref().unwrap_or(".");
    let base = crate::files::under(&project.root, directory);
    if !base.exists() {
        return json!({"directory":directory,"missing":true});
    }
    match crate::files::tree(&project.root, &base) {
        Ok(entries) => {
            let view = Component {
                root: directory.into(),
                ..project.components[0].clone()
            };
            let candidates: Vec<_> = entries.iter().map(|entry| entry.relative.clone()).collect();
            let matched = ici_config::matching_source_paths(&view, &candidates, &build.artifacts)
                .unwrap_or_default();
            json!(matched.into_iter().map(|path|{let metadata=std::fs::metadata(project.root.join(&path)).ok();(path,metadata.map(|metadata|json!({"size":metadata.len(),"dev":metadata.dev(),"ino":metadata.ino(),"mtime":metadata.mtime(),"mtime_ns":metadata.mtime_nsec(),"ctime":metadata.ctime(),"ctime_ns":metadata.ctime_nsec()})))}).collect::<BTreeMap<_,_>>())
        }
        Err(error) => json!({"directory":directory,"error":error.to_string()}),
    }
}

impl CppPlans {
    pub fn attach_sources(&mut self, sources: std::sync::Arc<ici_providers::SourceCoordinates>) {
        for providers in self.diagnostics.values_mut() {
            for provider in providers {
                ici_providers::attach_sources(provider, sources.clone());
            }
        }
    }
    pub fn run(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        request: &AnalysisRequest,
        scheduler: &ici_execution::Scheduler,
        cancellation: &ici_execution::Cancellation,
        events: &crate::events::EventLog,
    ) -> AnalysisOutput {
        if request.check_id == "cpp.artifact" {
            return analyze_contract(project, component, request);
        }
        if request.check_id == "cpp.compile" {
            let mut context = self.contexts[&component.id].clone();
            if let Some(probes) = self.includes.get(&request.task_id) {
                let tasks: Vec<_> = probes.iter().map(|probe| probe.task.clone()).collect();
                let schedule = match scheduler.run_with_start(&tasks, cancellation, |task| {
                    events.emit(
                        "task.started",
                        Some(&task.id),
                        Some(&component.id),
                        "compiler include evidence",
                    )
                }) {
                    Ok(schedule) => schedule,
                    Err(error) => {
                        return failed(
                            AnalysisOutput::new(&request.task_id, "ici.compile"),
                            &error.to_string(),
                        );
                    }
                };
                let mut observations = vec![];
                for outcome in schedule.outcomes {
                    events.emit(
                        "task.completed",
                        Some(&outcome.task_id),
                        Some(&component.id),
                        &format!("{:?}", outcome.state),
                    );
                    let probe = probes
                        .iter()
                        .find(|probe| probe.task.id == outcome.task_id)
                        .expect("outcome from declared probe");
                    let trace = if outcome.completed() && outcome.exit_code == Some(0) {
                        std::str::from_utf8(&outcome.stderr)
                            .map_err(|error| error.to_string())
                            .and_then(|text| {
                                ici_cpp::parse_include_trace(
                                    &project.root,
                                    Path::new(&probe.task.cwd),
                                    &project.root.join(&probe.unit.source),
                                    text,
                                )
                                .map_err(|error| error.to_string())
                            })
                    } else {
                        Err(outcome.error.unwrap_or_else(|| {
                            format!("include probe exited {:?}", outcome.exit_code)
                        }))
                    };
                    match trace {
                        Ok(trace) => observations.push(ici_cpp::CompilerIncludeObservation {
                            source: probe.unit.source.clone(),
                            configuration: probe.unit.configuration.clone(),
                            complete: trace.complete(),
                            trace,
                        }),
                        Err(reason) => {
                            return failed(
                                AnalysisOutput::new(&request.task_id, "ici.compile"),
                                &reason,
                            );
                        }
                    }
                }
                match ici_cpp::confirm_compilation_coverage(&context, &observations) {
                    Ok(confirmed) => {
                        context = confirmed;
                        self.contexts.insert(component.id.clone(), context.clone());
                    }
                    Err(error) => {
                        return failed(
                            AnalysisOutput::new(&request.task_id, "ici.compile"),
                            &error.to_string(),
                        );
                    }
                }
            }
            return ici_cpp::analyze_compile_context(request, &context);
        }
        if let Some(providers) = self.diagnostics.get(&request.task_id) {
            let tasks: Vec<_> = providers
                .iter()
                .map(|provider| provider.task.clone())
                .collect();
            let schedule = match scheduler.run_with_start(&tasks, cancellation, |task| {
                events.emit(
                    "task.started",
                    Some(&task.id),
                    Some(&component.id),
                    "recorded compiler diagnostics",
                )
            }) {
                Ok(schedule) => schedule,
                Err(error) => {
                    return failed(
                        AnalysisOutput::new(&request.task_id, "compiler"),
                        &error.to_string(),
                    );
                }
            };
            let mut outputs = vec![];
            for outcome in schedule.outcomes {
                events.emit(
                    "task.completed",
                    Some(&outcome.task_id),
                    Some(&component.id),
                    &format!("{:?}", outcome.state),
                );
                let provider = providers
                    .iter()
                    .find(|provider| provider.task.id == outcome.task_id)
                    .expect("outcome from declared compiler");
                outputs.push(ici_providers::observe_full(provider, &outcome));
            }
            return merge(&request.task_id, "compiler", outputs);
        }
        failed(
            AnalysisOutput::new(&request.task_id, "ici.cpp"),
            "no C++ execution observer bound",
        )
    }
    pub fn partial_input_available(&self, dependency: &str, consumer: &str) -> bool {
        self.contexts.iter().any(|(component, context)| {
            dependency == format!("{component}.cpp.compile")
                && consumer.starts_with(&format!("{component}.cpp."))
                && !context.units.is_empty()
        })
    }
}
pub fn merge(id: &str, provider: &str, outputs: Vec<AnalysisOutput>) -> AnalysisOutput {
    let mut result = AnalysisOutput::new(id, provider);
    let mut details = serde_json::Map::new();
    let mut summaries = vec![];
    for output in outputs {
        if output.observation.state != TaskState::Succeeded {
            result.observation.state = output.observation.state;
        }
        if output.status == "FAIL" && result.status == "PASS" {
            result.status = "FAIL".into();
        }
        result.observation.timed_out |= output.observation.timed_out;
        result.observation.truncated |= output.observation.truncated;
        result.observation.duration_seconds = Some(
            result.observation.duration_seconds.unwrap_or(0.0)
                + output.observation.duration_seconds.unwrap_or(0.0),
        );
        result
            .observation
            .findings
            .extend(output.observation.findings);
        result
            .observation
            .measurements
            .extend(output.observation.measurements);
        result
            .observation
            .tool_versions
            .extend(output.observation.tool_versions);
        result
            .observation
            .limitations
            .extend(output.observation.limitations);
        result.targets.extend(output.targets);
        if let Some(rows) = output
            .extra
            .get("finding_details")
            .and_then(Value::as_object)
        {
            details.extend(rows.clone());
        }
        summaries.push(output.summary);
        result.extra[&output.observation.task_id] = output.extra;
    }
    if result.observation.state != TaskState::Succeeded {
        result.status = "ERROR".into();
    }
    result.extra["finding_details"] = Value::Object(details);
    result.summary = summaries.join("; ");
    result
}

fn unit_id(component: &Component, unit: &CompilationUnit) -> String {
    format!(
        "{}.cpp.{}",
        component.id,
        unit.configuration
            .strip_prefix("sha256:")
            .unwrap_or(&unit.configuration)
    )
}
