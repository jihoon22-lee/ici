//! Leased GNU section-GC observation. Ordinary analysis never creates this build.
use super::*;
use ici_cpp::{BuildPlan, LinkCommand, LinkerObjectEvidence, LinkerTargetEvidence};
use ici_execution::{Cancellation, Scheduler, TaskOutcome};

#[derive(Clone)]
pub(super) struct LinkerPlan {
    build: BuildPlan,
    context: CompilationContext,
    commands: Vec<LinkCommand>,
    tasks: Vec<TaskSpec>,
    locations: BTreeMap<String, TaskSpec>,
    objects: BTreeMap<String, String>,
    input_limits: BTreeMap<String, usize>,
}
impl CppPlans {
    /// The caller retains the same guards through final input verification.
    pub fn attach_prepared(&mut self, contexts: &[crate::export_context::PreparedContext]) {
        self.held_contexts = contexts.to_vec();
    }
    pub fn evidence_input_limits(&self) -> BTreeMap<String, usize> {
        self.linkers
            .values()
            .flat_map(|plan| plan.input_limits.clone())
            .collect()
    }
    pub(super) fn bind_linker(
        &mut self,
        project: &ResolvedProject,
        component: &Component,
        id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<Vec<TaskSpec>, String> {
        let prepared=component.settings.pointer("/native_build_variants/link-reachability").ok_or("GNU linker dead-function evidence requires an explicitly prepared owned link-reachability build")?;
        let build: BuildPlan = serde_json::from_value(
            prepared
                .get("plan")
                .cloned()
                .ok_or("link-reachability context has no build transaction")?,
        )
        .map_err(|error| error.to_string())?;
        if build.variant != "link-reachability" || build.system != "cmake" {
            return Err(
                "GNU linker evidence requires the isolated CMake link-reachability variant".into(),
            );
        }
        let context = ici_cpp::load_compilation_context(
            &project.root,
            &ContextRequest {
                database: Some(build.compilation_database.clone()),
                variant: Some(build.variant.clone()),
                selected_sources: component
                    .files
                    .iter()
                    .filter(|path| ici_cpp::is_translation_unit(path))
                    .cloned()
                    .collect(),
                ..Default::default()
            },
        );
        if !context.complete() || context.unity_build || context.units.len() > 2048 {
            return Err(
                "GNU linker evidence requires complete direct function-section compilation units"
                    .into(),
            );
        }
        let mut approved = vec![];
        for unit in &context.units {
            approved.push(self.compiler(project, unit, environment)?.path);
        }
        approved.sort();
        approved.dedup();
        let commands = ici_cpp::discover_link_commands(&project.root, &build, &approved, &context)
            .map_err(|error| error.to_string())?;
        if commands.is_empty() {
            return Err(
                "the isolated linker build has no supported direct executable targets".into(),
            );
        }
        let directory = project.root.join(&build.shadow);
        let readelf = resolve_binutil(project, environment, "readelf", &directory)?;
        let addr2line = resolve_binutil(project, environment, "addr2line", &directory)?;
        self.tools.insert(format!("{id}:readelf"), readelf.clone());
        self.tools
            .insert(format!("{id}:addr2line"), addr2line.clone());
        let mut tasks = vec![];
        let mut input_limits = BTreeMap::new();
        let mut objects = BTreeMap::new();
        let mut locations = BTreeMap::new();
        for unit in &context.units {
            input_limits.insert(unit.source.clone(), 16 * 1024 * 1024);
        }
        if let Some(path) = &context.database_path {
            input_limits.insert(path.clone(), 32 * 1024 * 1024);
        }
        for input in &context.response_files {
            input_limits.insert(input.path.clone(), 4 * 1024 * 1024);
        }
        for entry in
            crate::files::tree(&project.root, &directory).map_err(|error| error.to_string())?
        {
            if entry.file
                && entry.contained
                && Path::new(&entry.relative)
                    .file_name()
                    .is_some_and(|name| name == "link.txt")
            {
                input_limits.insert(entry.relative, 4 * 1024 * 1024);
            }
        }
        for (index, command) in commands.iter().enumerate() {
            let mut identity = ici_cpp::linker_identity_task(&project.root, command);
            identity.id = format!("{id}.target{index}.identity");
            let mut relink = ici_cpp::relink_task(&project.root, command);
            relink.id = format!("{id}.target{index}.relink");
            relink.depends_on = vec![identity.id.clone()];
            relink.work_dirs = vec![directory.to_string_lossy().into_owned()];
            relink.output_specs = vec![
                project
                    .root
                    .join(&command.output)
                    .to_string_lossy()
                    .into_owned(),
            ];
            let mut header = binutil(
                &readelf,
                &directory,
                format!("{id}.target{index}.elf"),
                vec![
                    "-Wh".into(),
                    project
                        .root
                        .join(&command.output)
                        .to_string_lossy()
                        .into_owned(),
                ],
            );
            header.depends_on = vec![relink.id.clone()];
            tasks.extend([identity, relink, header]);
            for path in &command.objects {
                input_limits.insert(path.clone(), 256 * 1024 * 1024);
                if !objects.contains_key(path) {
                    objects.insert(path.clone(), format!("{id}.object{}", objects.len()));
                }
            }
        }
        for (path, prefix) in &objects {
            for (suffix, flag) in [
                ("headers", "-WS"),
                ("symbols", "-Ws"),
                ("groups", "--section-groups"),
            ] {
                tasks.push(binutil(
                    &readelf,
                    &directory,
                    format!("{prefix}.{suffix}"),
                    vec![
                        flag.into(),
                        project.root.join(path).to_string_lossy().into_owned(),
                    ],
                ));
            }
            let mut location = binutil(
                &addr2line,
                &directory,
                format!("{prefix}.locations"),
                vec![
                    "-e".into(),
                    project.root.join(path).to_string_lossy().into_owned(),
                    "-j".into(),
                    "{observed-discarded-section}".into(),
                    "0".into(),
                ],
            );
            location.output_limit_bytes = Some(16 * 1024);
            locations.insert(path.clone(), location);
        }
        for task in tasks.iter_mut().chain(locations.values_mut()) {
            // Keep per-step references linear in its argv; the shared snapshot captures the union.
            let mut references = BTreeSet::new();
            if let Some(database) = &context.database_path {
                references.insert(project.root.join(database).to_string_lossy().into_owned());
            }
            for argument in &task.argv {
                let candidate = if Path::new(argument).is_absolute() {
                    PathBuf::from(argument)
                } else {
                    directory.join(argument)
                };
                if let Ok(candidate) = candidate.canonicalize()
                    && let Ok(relative) = candidate.strip_prefix(&project.root)
                    && input_limits.contains_key(relative.to_string_lossy().as_ref())
                {
                    references.insert(candidate.to_string_lossy().into_owned());
                }
            }
            task.input_refs = references.into_iter().collect();
            task.analysis_unit_ids = vec![format!("{}.cpp.link-reachability", component.id)];
            task.variant = Some("link-reachability".into());
        }
        let mut visible = tasks.clone();
        visible.extend(locations.values().cloned());
        self.linkers.insert(
            id.into(),
            LinkerPlan {
                build,
                context,
                commands,
                tasks,
                locations,
                objects,
                input_limits,
            },
        );
        Ok(visible)
    }
    pub(super) fn run_linker(
        &self,
        project: &ResolvedProject,
        component: &Component,
        request: &AnalysisRequest,
        scheduler: &Scheduler,
        cancellation: &Cancellation,
        events: &crate::events::EventLog,
    ) -> AnalysisOutput {
        let plan = &self.linkers[&request.task_id];
        let held = self.held_contexts.iter().any(|context| {
            context.plan.root == project.root
                && context.plan.component_id == component.id
                && serde_json::to_value(&context.plan.build).ok()
                    == serde_json::to_value(&plan.build).ok()
        });
        if !held {
            return failed(
                AnalysisOutput::new(&request.task_id, "gnu-linker.section-gc"),
                "GNU relink requires the matching held native build lease; explicit preparation did not authorize this context",
            );
        }
        let results = match run_cpp_tasks(
            &plan.tasks,
            scheduler,
            cancellation,
            events,
            &component.id,
            "owned GNU section-GC evidence",
        ) {
            Ok(results) => results,
            Err(reason) => {
                return failed(
                    AnalysisOutput::new(&request.task_id, "gnu-linker.section-gc"),
                    &reason,
                );
            }
        };
        let mut outcomes = BTreeMap::new();
        for outcome in results {
            if !outcome.prerequisite_succeeded() {
                return failed_probe(
                    &request.task_id,
                    "gnu-linker.section-gc",
                    ".",
                    &outcome,
                    "GNU linker or binutils evidence did not complete",
                );
            }
            outcomes.insert(outcome.task_id.clone(), outcome);
        }
        let collected = match collect_evidence(
            plan,
            request,
            project,
            component,
            scheduler,
            cancellation,
            events,
            &outcomes,
        ) {
            Ok(value) => value,
            Err(error) => {
                return if let Some(outcome) = error.outcome {
                    failed_probe(
                        &request.task_id,
                        "gnu-linker.section-gc",
                        ".",
                        &outcome,
                        &error.reason,
                    )
                } else {
                    failed(
                        AnalysisOutput::new(&request.task_id, "gnu-linker.section-gc"),
                        &error.reason,
                    )
                };
            }
        };
        let mut output = ici_cpp::analyze_linker_dead(
            request,
            &project.root,
            &plan.context,
            &plan.commands,
            &collected.targets,
            &collected.objects,
        );
        output.extra["linker_probes"] = json!({"steps":plan.tasks.iter().chain(collected.locations.iter()).collect::<Vec<_>>(),"commands":plan.commands,"variant":plan.build.variant,"shadow":plan.build.shadow,"source_mapping":"eligible symbols located by actual section-scoped addr2line; evidence intersects every target sharing each direct object"});
        output.observation.duration_seconds = Some(
            outcomes
                .values()
                .map(|outcome| outcome.duration_seconds)
                .sum::<f64>()
                + collected.duration,
        );
        output
    }
}

struct CollectedEvidence {
    targets: Vec<LinkerTargetEvidence>,
    objects: Vec<LinkerObjectEvidence>,
    locations: Vec<TaskSpec>,
    duration: f64,
}
struct CollectionError {
    reason: String,
    outcome: Option<Box<TaskOutcome>>,
}
impl From<String> for CollectionError {
    fn from(reason: String) -> Self {
        Self {
            reason,
            outcome: None,
        }
    }
}
#[allow(clippy::too_many_arguments)]
fn collect_evidence(
    plan: &LinkerPlan,
    request: &AnalysisRequest,
    project: &ResolvedProject,
    component: &Component,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<CollectedEvidence, CollectionError> {
    let mut targets = vec![];
    let mut removals = vec![];
    for (index, command) in plan.commands.iter().enumerate() {
        let prefix = format!("{}.target{index}", request.task_id);
        let banner = transcript(&outcomes[&format!("{prefix}.identity")])?;
        let discarded = transcript(&outcomes[&format!("{prefix}.relink")])?;
        removals.extend(
            ici_cpp::parse_discarded_sections(&project.root, command, &discarded)
                .map_err(|error| error.to_string())?,
        );
        targets.push(LinkerTargetEvidence {
            command_digest: command.digest.clone(),
            complete: true,
            linker_banner: banner,
            elf_header: utf8(&outcomes[&format!("{prefix}.elf")].stdout)?,
            discarded_output: discarded,
        });
    }
    let (retained, _) = ici_cpp::discarded_by_every_linking_target(&removals, &plan.commands);
    let mut objects = vec![];
    let mut dynamic = vec![];
    let mut ownership = vec![];
    for (path, prefix) in &plan.objects {
        let unit = plan
            .context
            .units
            .iter()
            .find(|unit| &unit.output == path)
            .ok_or_else(|| "link object no longer maps to its recorded source".to_string())?;
        let headers = utf8(&outcomes[&format!("{prefix}.headers")].stdout)?;
        let symbols = utf8(&outcomes[&format!("{prefix}.symbols")].stdout)?;
        let groups = utf8(&outcomes[&format!("{prefix}.groups")].stdout)?;
        let facts = ici_cpp::parse_object_facts(&headers, &symbols, &groups)
            .map_err(|error| error.to_string())?;
        let source = request
            .inputs
            .iter()
            .find(|input| input.path == unit.source);
        let object = LinkerObjectEvidence {
            object_path: path.clone(),
            source: unit.source.clone(),
            source_digest: source.map(|input| sha256(&input.bytes)).unwrap_or_default(),
            headers,
            symbols,
            groups,
            addr2line: BTreeMap::new(),
        };
        let sections = retained
            .iter()
            .filter(|removal| &removal.object_path == path)
            .map(|removal| removal.section.clone())
            .collect::<BTreeSet<_>>();
        for section in sections {
            if ici_cpp::eligible_discarded_symbol(&facts, &section).is_none() {
                continue;
            }
            if dynamic.len() >= 16384 {
                return Err("discarded-section locations exceed 16384 records"
                    .to_string()
                    .into());
            }
            let mut task = plan.locations[path].clone();
            task.id = format!("{}.locate{}", request.task_id, dynamic.len());
            for argument in &mut task.argv {
                if argument == "{observed-discarded-section}" {
                    *argument = section.clone();
                }
            }
            ownership.push((objects.len(), section, task.id.clone()));
            dynamic.push(task);
        }
        objects.push(object);
    }
    let results = run_cpp_tasks(
        &dynamic,
        scheduler,
        cancellation,
        events,
        &component.id,
        "discarded-section source locations",
    )?;
    let duration = results.iter().map(|outcome| outcome.duration_seconds).sum();
    let results = results
        .into_iter()
        .map(|outcome| (outcome.task_id.clone(), outcome))
        .collect::<BTreeMap<_, _>>();
    for (index, section, id) in ownership {
        let outcome = &results[&id];
        if !outcome.prerequisite_succeeded() {
            return Err(CollectionError {
                reason: "addr2line observation did not complete".into(),
                outcome: Some(Box::new(outcome.clone())),
            });
        }
        objects[index]
            .addr2line
            .insert(section, utf8(&outcome.stdout)?);
    }
    Ok(CollectedEvidence {
        targets,
        objects,
        locations: dynamic,
        duration,
    })
}
fn resolve_binutil(
    project: &ResolvedProject,
    environment: &BTreeMap<String, String>,
    id: &str,
    directory: &Path,
) -> Result<ResolvedTool, String> {
    ToolRequest {
        id: id.into(),
        explicit: project
            .tools
            .get(id)
            .and_then(|tool| tool.path.as_ref())
            .map(|path| crate::files::under(&project.root, path)),
        project_root: directory.into(),
        pack_root: None,
        path_env: environment.get("PATH").cloned(),
    }
    .resolve()
    .map_err(|error| error.to_string())
}
fn binutil(tool: &ResolvedTool, directory: &Path, id: String, arguments: Vec<String>) -> TaskSpec {
    let mut argv = vec![tool.path.to_string_lossy().into_owned()];
    argv.extend(arguments);
    let mut task = TaskSpec::command(id, &tool.id, argv, directory.to_string_lossy().into_owned());
    task.tool_digest = Some(tool.digest.clone());
    task.env_overlay = ici_cpp::replay_environment();
    task.timeout_seconds = Some(30.0);
    task.output_limit_bytes = Some(4 * 1024 * 1024);
    task.cacheable = false;
    task
}
fn utf8(bytes: &[u8]) -> Result<String, String> {
    String::from_utf8(bytes.to_vec()).map_err(|_| "GNU tool evidence must be UTF-8".into())
}
fn transcript(outcome: &TaskOutcome) -> Result<String, String> {
    Ok(format!(
        "{}\n{}",
        utf8(&outcome.stdout)?,
        utf8(&outcome.stderr)?
    ))
}
