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
    configurations: BTreeSet<String>,
    configuration_digests: BTreeMap<String, String>,
}
impl CppPlans {
    /// The caller retains the same guards through final input verification.
    pub fn attach_prepared(&mut self, contexts: &[crate::export_context::PreparedContext]) {
        self.held_contexts = contexts.to_vec();
    }
    fn recorded_configuration_consumer(&self, id: &str) -> bool {
        !self.inactive.contains_key(id)
            && !self.compile_scope_is_empty(id)
            && (id.ends_with(".cpp.compile")
                || self.diagnostics.contains_key(id)
                || self.includes.contains_key(id)
                || self.boundaries.contains_key(id))
    }
    pub fn evidence_input_limits(&self) -> BTreeMap<String, usize> {
        self.linkers
            .values()
            .flat_map(|plan| plan.input_limits.clone())
            .chain(self.abi_inputs.values().flat_map(|inputs| inputs.clone()))
            .chain(
                self.compiler_configurations
                    .iter()
                    .filter(|(id, _)| self.recorded_configuration_consumer(id))
                    .flat_map(|(_, inputs)| inputs.clone()),
            )
            .collect()
    }
    pub fn evidence_configurations_for(&self, id: &str) -> BTreeSet<String> {
        let mut paths = self
            .linkers
            .get(id)
            .map(|plan| plan.configurations.clone())
            .unwrap_or_default();
        if self.recorded_configuration_consumer(id)
            && let Some(configurations) = self.compiler_configurations.get(id)
        {
            paths.extend(configurations.keys().cloned());
        }
        paths
    }
    /// Semantic metadata hashes use parsed configuration identities; raw bytes remain in snapshot.
    pub fn evidence_configuration_digests_for(&self, id: &str) -> BTreeMap<String, String> {
        let mut digests = self
            .linkers
            .get(id)
            .map(|plan| {
                let mut values = normalized_context_digests(&plan.context);
                values.extend(plan.configuration_digests.clone());
                values
            })
            .unwrap_or_default();
        if self.recorded_configuration_consumer(id)
            && let Some(context) = self
                .compiler_components
                .get(id)
                .and_then(|component| self.contexts.get(component))
        {
            digests.extend(normalized_context_digests(context));
        }
        digests
    }
    pub fn evidence_code_inputs_for(&self, id: &str) -> BTreeSet<String> {
        self.linkers
            .get(id)
            .into_iter()
            .flat_map(|plan| {
                plan.objects
                    .keys()
                    .cloned()
                    .chain(plan.context.units.iter().map(|unit| unit.source.clone()))
            })
            .chain(
                self.abi_inputs
                    .get(id)
                    .into_iter()
                    .flat_map(|inputs| inputs.keys().cloned()),
            )
            .collect()
    }
    pub fn evidence_input_limits_for(&self, id: &str) -> BTreeMap<String, usize> {
        let mut inputs = self
            .linkers
            .get(id)
            .map(|plan| plan.input_limits.clone())
            .unwrap_or_default();
        inputs.extend(self.abi_inputs.get(id).cloned().unwrap_or_default());
        if self.recorded_configuration_consumer(id) {
            inputs.extend(
                self.compiler_configurations
                    .get(id)
                    .cloned()
                    .unwrap_or_default(),
            );
        }
        inputs
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
        let mut configurations = BTreeSet::new();
        let mut configuration_digests = BTreeMap::new();
        let mut objects = BTreeMap::new();
        let mut locations = BTreeMap::new();
        for unit in &context.units {
            input_limits.insert(unit.source.clone(), 16 * 1024 * 1024);
        }
        if let Some(path) = &context.database_path {
            input_limits.insert(path.clone(), 32 * 1024 * 1024);
            configurations.insert(path.clone());
        }
        for input in &context.response_files {
            input_limits.insert(input.path.clone(), 4 * 1024 * 1024);
            configurations.insert(input.path.clone());
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
                let target = Path::new(&entry.relative)
                    .parent()
                    .and_then(Path::file_name)
                    .and_then(|name| name.to_str())
                    .and_then(|name| name.strip_suffix(".dir"));
                let command = commands
                    .iter()
                    .find(|command| Some(command.target.as_str()) == target)
                    .ok_or("parsed CMake link metadata has no selected command")?;
                let argv = ici_cpp::normalized_configuration_arguments(
                    &project.root,
                    &directory,
                    &command.argv,
                );
                configuration_digests.insert(entry.relative.clone(),scope_digest(&json!({"schema":"ici.cpp.link-scope/v1","target":command.target,"argv":argv,"objects":command.objects,"output":command.output,"shadow":command.shadow,"driver_digest":command.driver_digest})));
                configurations.insert(entry.relative.clone());
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
        if self.legacy_projection {
            // Frozen linker evidence names each tool's capability version: the GCC link
            // driver's (the PATH `g++` capability of the same physical driver), readelf's,
            // addr2line's and the CMake adapter's.
            let mut probes: Vec<(String, PathBuf, &str)> = vec![];
            for command in &commands {
                if let Some(driver) = command.argv.first() {
                    probes.push((
                        driver.clone(),
                        legacy_driver_capability(Path::new(driver), environment),
                        "g++",
                    ));
                }
            }
            probes.push((
                readelf.path.to_string_lossy().into_owned(),
                readelf.path.clone(),
                "readelf",
            ));
            probes.push((
                addr2line.path.to_string_lossy().into_owned(),
                addr2line.path.clone(),
                "addr2line",
            ));
            if let Some(cmake) = build.tasks.first().and_then(|task| task.argv.first()) {
                probes.push((cmake.clone(), PathBuf::from(cmake), "cmake"));
            }
            for (key, executable, name) in probes {
                self.compiler_versions
                    .entry(format!("linker:{key}"))
                    .or_insert_with(|| {
                        legacy_tooling::legacy_version_banner(
                            &executable,
                            &project.root,
                            environment,
                            name,
                        )
                        .unwrap_or_default()
                    });
            }
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
                configurations,
                configuration_digests,
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
        let held = self.held_contexts.iter().find(|context| {
            context.plan.root == project.root
                && context.plan.component_id == component.id
                && serde_json::to_value(&context.plan.build).ok()
                    == serde_json::to_value(&plan.build).ok()
        });
        let Some(held) = held else {
            return failed(
                AnalysisOutput::new(&request.task_id, "gnu-linker.section-gc"),
                "GNU relink requires the matching held native build lease; explicit preparation did not authorize this context",
            );
        };
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
        if self.legacy_projection {
            let evidence = self.legacy_linker_evidence(
                plan,
                request,
                &held.process_evidence,
                &outcomes,
                &collected,
            );
            legacy_linker_projection(&mut output, plan, &evidence);
        }
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
    /// The section-scoped addr2line outcomes, in `locations` order.
    location_outcomes: Vec<TaskOutcome>,
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
    let location_outcomes = dynamic
        .iter()
        .filter_map(|task| results.iter().find(|outcome| outcome.task_id == task.id))
        .cloned()
        .collect();
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
        location_outcomes,
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

fn scope_digest(value: &Value) -> String {
    sha256(ici_domain::canonical_json(value).expect("bounded configuration metadata serializes"))
}
fn normalized_context_digests(context: &CompilationContext) -> BTreeMap<String, String> {
    let units=context.units.iter().map(|unit|json!({"source":unit.source,"directory":unit.directory,"output":unit.output,"target":unit.target,"variant":unit.variant,"language":unit.language,"standard":unit.standard,"configuration":unit.configuration})).collect::<Vec<_>>();
    let mut result = BTreeMap::new();
    if let Some(path) = &context.database_path {
        result.insert(path.clone(),scope_digest(&json!({"schema":"ici.cpp.database-scope/v1","units":units,"variant":context.variant,"unity_build":context.unity_build,"selected_sources":context.selected_sources,"uncovered_sources":context.uncovered_sources,"source_coverage":context.source_coverage})));
    }
    for response in &context.response_files {
        let users=context.units.iter().filter(|unit|unit.response_files.iter().any(|input|input.path==response.path)).map(|unit|json!({"source":unit.source,"configuration":unit.configuration,"variant":unit.variant,"directory":unit.directory,"output":unit.output})).collect::<Vec<_>>();
        result.insert(
            response.path.clone(),
            scope_digest(
                &json!({"schema":"ici.cpp.response-scope/v1","path":response.path,"units":users}),
            ),
        );
    }
    result
}

impl CppPlans {
    /// Frozen `CppLinkerDeadOutcome.evidence`, recorded from the processes this run actually
    /// executed (decisions.md "C++ 도구 증거는 실제 실행 기록"): the CMake adapter of the held
    /// preparation, then per link target the driver identity, the relink and the ELF header
    /// read, then per direct object the three readelf tables, then every section-scoped
    /// addr2line location.
    fn legacy_linker_evidence(
        &self,
        plan: &LinkerPlan,
        request: &AnalysisRequest,
        preparation: &[Value],
        outcomes: &BTreeMap<String, TaskOutcome>,
        collected: &CollectedEvidence,
    ) -> Vec<Value> {
        let version = |path: &str| {
            self.compiler_versions
                .get(&format!("linker:{path}"))
                .cloned()
                .unwrap_or_default()
        };
        let mut records: Vec<Value> = preparation
            .iter()
            .filter_map(|record| {
                let name = match record["name"].as_str()? {
                    "cmake configure" => "cmake linker-reachability configure",
                    "cmake build" => "cmake linker-reachability build",
                    _ => return None,
                };
                let mut record = record.clone();
                record["version"] = json!(version(record["path"].as_str().unwrap_or("")));
                record["name"] = json!(name);
                Some(record)
            })
            .collect();
        let mut push = |name: &str, task: &TaskSpec, outcome: Option<&TaskOutcome>| {
            if let Some(outcome) = outcome {
                let path = task.argv.first().map_or("", String::as_str);
                records.push(legacy_tooling::process_evidence(
                    name,
                    &version(path),
                    &task.argv,
                    outcome,
                    "",
                ));
            }
        };
        for (index, _) in plan.commands.iter().enumerate() {
            let prefix = format!("{}.target{index}", request.task_id);
            for (suffix, name) in [
                ("identity", "g++ linker identity"),
                ("relink", "g++ GNU ELF relink"),
                ("elf", "readelf linked executable"),
            ] {
                let id = format!("{prefix}.{suffix}");
                if let Some(task) = plan.tasks.iter().find(|task| task.id == id) {
                    push(name, task, outcomes.get(&id));
                }
            }
        }
        for prefix in plan.objects.values() {
            for (suffix, name) in [
                ("headers", "readelf -WS"),
                ("symbols", "readelf -Ws"),
                ("groups", "readelf --section-groups"),
            ] {
                let id = format!("{prefix}.{suffix}");
                if let Some(task) = plan.tasks.iter().find(|task| task.id == id) {
                    push(name, task, outcomes.get(&id));
                }
            }
        }
        for (task, outcome) in collected.locations.iter().zip(&collected.location_outcomes) {
            push("addr2line discarded function", task, Some(outcome));
        }
        records
    }
}

/// The PATH `g++` capability frozen `compiler_capability` matches to a link driver of the same
/// physical executable (its banner names `g++`); otherwise the driver itself.
fn legacy_driver_capability(driver: &Path, environment: &BTreeMap<String, String>) -> PathBuf {
    let physical = driver.canonicalize().ok();
    environment
        .get("PATH")
        .into_iter()
        .flat_map(|path| path.split(':'))
        .filter(|directory| !directory.is_empty())
        .map(|directory| Path::new(directory).join("g++"))
        .find(|candidate| candidate.is_file())
        .filter(|candidate| physical.is_some() && candidate.canonicalize().ok() == physical)
        .unwrap_or_else(|| driver.to_path_buf())
}

/// Frozen `DeadCodeEngine` projection of the GNU ELF section-GC result: `GNU ld reachability
/// scan` PASS rows ("Source participated in <n> supported target-local Release link(s)") with
/// their exact scope findings, the `ici.dead.gnu-elf-discarded-function` finding of each
/// discarded function, the frozen dead summary, the `cpp_linker_*` counters and details and
/// the recorded tool evidence.
fn legacy_linker_projection(output: &mut AnalysisOutput, plan: &LinkerPlan, evidence: &[Value]) {
    if output.observation.state != ici_domain::TaskState::Succeeded {
        return;
    }
    let link_targets = plan.commands.len();
    let shadow = plan.build.shadow.as_str();
    let legacy_digest = |digest: &Value| {
        plan.commands
            .iter()
            .find(|command| digest == command.digest.as_str())
            .map_or(digest.clone(), |command| json!(command.legacy_digest))
    };
    let names: BTreeSet<&str> = evidence
        .iter()
        .filter_map(|record| record["name"].as_str())
        .filter(|name| !name.is_empty())
        .collect();
    let versions: BTreeSet<&str> = evidence
        .iter()
        .filter_map(|record| record["version"].as_str())
        .filter(|version| !version.is_empty())
        .collect();
    let scope_tool = names.into_iter().collect::<Vec<_>>().join("+");
    let scope_version = versions.into_iter().collect::<Vec<_>>().join(", ");
    let driver_version = evidence
        .iter()
        .find(|record| record["name"] == "g++ GNU ELF relink")
        .and_then(|record| record["version"].as_str())
        .unwrap_or("")
        .to_owned();
    let mut key_counts = BTreeMap::<(String, String), usize>::new();
    for target in &output.targets {
        *key_counts
            .entry((
                target.file_path.clone(),
                target.target_name.trim().to_owned(),
            ))
            .or_default() += 1;
    }
    let mut details = vec![];
    let mut sources = BTreeSet::new();
    let mut scope_findings = vec![];
    let mut finding_details = serde_json::Map::new();
    for target in &mut output.targets {
        match target.target_name.as_str() {
            "LinkerReachability" => {
                target.target_name = "GNU ld reachability scan".into();
                target.status = "PASS".into();
                target.message = format!(
                    "Source participated in {link_targets} supported target-local Release link(s)"
                );
                target.metrics.clear();
                sources.insert(target.file_path.clone());
                scope_findings.push(json!({"rule_id":"ici.legacy.dead.target","category":"maintainability",
                    "severity":"info","confidence":"exact","primary_location":{"path":target.file_path,
                    "start_line":target.start_line,"end_line":target.end_line,"start_column":target.start_column,
                    "end_column":target.end_column,"label":target.target_name},"related_locations":[],
                    "message":target.message,
                    "explanation":"This target records the bounded target-local GNU ELF section-GC scope.",
                    "remediation":"","fixes":[],"tool_rule_id":"","tool_name":scope_tool,
                    "tool_version":scope_version,"metrics":{},"snippet":"",
                    "suppression":{"kind":"none","reason":"","suppressed":false}}));
            }
            "GNU ld discarded function" => {
                sources.insert(target.file_path.clone());
                let digest = legacy_digest(
                    &target
                        .metrics
                        .get("link_command_digest")
                        .cloned()
                        .unwrap_or_default(),
                );
                target.metrics.insert("link_command_digest".into(), digest);
                let metric = |key: &str| target.metrics.get(key).cloned().unwrap_or(Value::Null);
                // Frozen names the object relative to the isolated Release shadow.
                let object = metric("object_path");
                let object = object.as_str().map_or(object.clone(), |path| {
                    json!(
                        path.strip_prefix(shadow)
                            .map_or(path, |rest| rest.trim_start_matches('/'))
                    )
                });
                details.push(
                    json!({"file_path":target.file_path,"start_line":target.start_line,
                    "link_target":metric("link_target"),"symbol":metric("symbol"),
                    "section":metric("section"),"object_path":object,
                    "tool_name":"g++","tool_version":driver_version,
                    "link_command_digest":metric("link_command_digest")}),
                );
                // Frozen target metrics carry no object path (it is a detail field).
                target.metrics.remove("object_path");
                let unique = key_counts.get(&(
                    target.file_path.clone(),
                    target.target_name.trim().to_owned(),
                )) == Some(&1);
                for finding in output.observation.findings.iter().filter(|finding| {
                    finding.primary_location.path == target.file_path
                        && finding.primary_location.start_line == target.start_line
                        && finding.message == target.message
                }) {
                    finding_details.insert(finding.fingerprint.clone(), json!({
                        "legacy_rule_id":"ici.dead.gnu-elf-discarded-function",
                        "legacy_message":target.message,
                        "legacy_label":if unique { target.target_name.as_str() } else { "" },
                        "legacy_category":"maintainability","legacy_confidence":"exact",
                        "legacy_severity":"medium","tool_name":"g++",
                        "tool_rule_id":"--gc-sections/--print-gc-sections",
                        "tool_version":driver_version,
                        "explanation":"GNU ld explicitly discarded this uniquely mapped local or hidden function section while relinking the named CMake executable in the isolated Release reachability shadow. The claim is target-local, not project-wide behavioral unreachability.",
                        "remediation":"Review the function and its target-specific call path. Remove it when the target no longer needs it, or retain/reference it explicitly when the discarded section is intentional.",
                        "metrics":{"link_targets":{"value":1,"unit":"targets"}},
                        "related_locations":[]}));
                }
            }
            _ => {}
        }
    }
    let measured = |name: &str| {
        output
            .observation
            .measurements
            .iter()
            .find(|measurement| measurement.name == name)
            .map_or(0, |measurement| measurement.value as u64)
    };
    let symbols = details.len();
    let discarded = measured("discarded_sections_observed");
    let extra = &mut output.extra;
    extra["cpp_linker_mode"] = json!("exact");
    extra["cpp_linker_symbols_count"] = json!(symbols);
    extra["cpp_linker_targets_checked"] = json!(link_targets);
    extra["cpp_linker_sources_checked"] = json!(sources.len());
    extra["cpp_linker_discarded_sections_observed"] = json!(discarded);
    extra["cpp_linker_ambiguous_sections_excluded"] = extra["ambiguous_sections_excluded"].clone();
    extra["cpp_linker_sections_kept_by_another_target"] =
        extra["sections_kept_by_another_target"].clone();
    extra["cpp_linker_warnings"] = json!([]);
    extra["cpp_linker_details"] = json!(details);
    extra["legacy_findings"] = json!(scope_findings);
    extra["finding_details"] = Value::Object(finding_details);
    extra["tool_evidence"] = json!(evidence);
    output.summary = if symbols == 0 {
        "No dead-code findings detected in the analyzed scopes".into()
    } else {
        format!(
            "{symbols} dead-code finding(s): 0 heuristic Python, 0 compiler-local C/C++, {symbols} target-local GNU ELF"
        )
    };
}
