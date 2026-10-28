use super::*;
use crate::cpp::package_flags::{FlagsFailure, observe_flags};
use ici_domain::{AnalysisOutput, Finding, InspectionTarget, SourceSpan, TaskState};
use ici_execution::{
    TaskOutcome,
    storage::{self, WriteMode},
};

fn directory(path: &Path) -> Result<(), String> {
    if !path.exists() {
        fs::create_dir(path).map_err(|error| error.to_string())?;
    }
    let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !metadata.is_dir()
        || metadata.file_type().is_symlink()
        || path.canonicalize().map_err(|error| error.to_string())? != path
    {
        return Err("direct compiler output directory is redirected or not a directory".into());
    }
    Ok(())
}
fn output_file(root: &Path, path: &str) -> Result<(), String> {
    let path = root.join(path);
    if let Ok(metadata) = fs::symlink_metadata(&path)
        && (!metadata.is_file()
            || metadata.nlink() != 1
            || path.canonicalize().map_err(|error| error.to_string())? != path)
    {
        return Err("direct compiler output is redirected or multiply linked".into());
    }
    Ok(())
}
fn result_record(outcome: &TaskOutcome) -> Value {
    json!({"task_id":outcome.task_id,"state":outcome.state,"exit_code":outcome.exit_code,"signal":outcome.signal,"timed_out":outcome.timed_out,"truncated":outcome.truncated,"duration_seconds":outcome.duration_seconds,"stdout_sha256":sha256(&outcome.stdout),"stderr_sha256":sha256(&outcome.stderr),"error":outcome.error})
}
fn attach_evidence(output: &mut AnalysisOutput, evidence: Value) {
    if let Some(fields) = evidence.as_object() {
        for (key, value) in fields {
            output.extra[key] = value.clone();
        }
    }
}
fn run_tasks(
    plan: &ContextPreparationPlan,
    tasks: &[TaskSpec],
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
) -> Result<Vec<TaskOutcome>, PreparationFailure> {
    let outcomes = scheduler
        .run_with_start(tasks, cancellation, |task| {
            events.emit(
                "task.started",
                Some(&task.id),
                Some(&plan.component_id),
                "explicit legacy direct compiler preparation",
            )
        })
        .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?
        .outcomes;
    for outcome in &outcomes {
        events.emit(
            "task.completed",
            Some(&outcome.task_id),
            Some(&plan.component_id),
            &format!("{:?}", outcome.state),
        );
    }
    Ok(outcomes)
}
fn failed_compiler(
    plan: &ContextPreparationPlan,
    phase: &str,
    provider: &ProviderPlan,
    outcome: &TaskOutcome,
    path: &str,
) -> AnalysisOutput {
    if !outcome.completed() || outcome.exit_code != Some(1) {
        return crate::cpp::failed_probe(
            &outcome.task_id,
            "g++",
            path,
            outcome,
            "direct compiler did not return a complete compilation verdict",
        );
    }
    let mut output = ici_providers::observe_full(provider, outcome);
    if output.observation.state != TaskState::Succeeded || output.observation.findings.is_empty() {
        output = AnalysisOutput::new(&outcome.task_id, "g++");
        output.observation.state = TaskState::Succeeded;
        output.observation.exit_code = outcome.exit_code;
        output.observation.duration_seconds = Some(outcome.duration_seconds);
        let detail = String::from_utf8_lossy(&outcome.stderr)
            .replace('\0', "�")
            .chars()
            .take(2048)
            .collect::<String>();
        let mut target = InspectionTarget::at(path, 1);
        target.target_name = if phase == "link" {
            "CppBinary"
        } else {
            "CppCompilation"
        }
        .into();
        target.status = "FAIL".into();
        target.message = format!("C++ {phase} failed with compiler exit 1: {detail}");
        let mut finding = Finding::new(
            if phase == "link" {
                "ici.build.cpp-link"
            } else {
                "ici.build.cpp-compile"
            },
            &target.message,
            SourceSpan::line(path, 1),
            "g++",
        );
        finding.severity = "high".into();
        finding.task_id = Some(outcome.task_id.clone());
        finding.component_id = Some(plan.component_id.clone());
        output.observation.findings.push(finding);
        output.targets.push(target);
    }
    output.status = "FAIL".into();
    output.summary = format!("Direct C++ {phase} reported a compilation failure");
    output
}
fn passed_source(provider: &ProviderPlan, outcome: &TaskOutcome) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(&outcome.task_id, "g++");
    output.observation.exit_code = outcome.exit_code;
    output.observation.duration_seconds = Some(outcome.duration_seconds);
    let mut target = InspectionTarget::at(&provider.context.targets[0], 1);
    target.target_name = "CppCompilation".into();
    target.message = "Source object compilation completed".into();
    output.targets.push(target);
    output
}

pub(in crate::export_context) fn execute(
    plan: &ContextPreparationPlan,
    scheduler: &Scheduler,
    cancellation: &Cancellation,
    events: &crate::events::EventLog,
) -> Result<PreparedContext, PreparationFailure> {
    let direct = plan.direct.as_ref().expect("direct plan");
    let current = InputSnapshot::take(
        &plan.root,
        &plan.selected_sources,
        &[],
        InputLimits {
            file_bytes: 16 * 1024 * 1024,
            total_bytes: 64 * 1024 * 1024,
        },
    )
    .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?;
    for source in direct.sources.iter() {
        if current.files[&source.path].input.bytes != source.bytes {
            return Err(PreparationFailure::Incomplete(format!(
                "direct compiler source changed after planning: {}",
                source.path
            )));
        }
    }
    let flags = match observe_flags(
        &direct.flags.probes,
        &direct.flags.limitations,
        scheduler,
        cancellation,
        events,
        &plan.component_id,
    ) {
        Ok(flags) => flags,
        Err(FlagsFailure::Invalid(reason)) => return Err(PreparationFailure::Incomplete(reason)),
        Err(FlagsFailure::Execution(outcome)) => {
            return Err(PreparationFailure::Observed {
                phase: "package flags".into(),
                output: Box::new(crate::cpp::failed_probe(
                    &outcome.task_id,
                    "pkg-config",
                    &direct.sources[0].path,
                    &outcome,
                    "package flag observation was incomplete",
                )),
            });
        }
    };
    let providers =
        compile_providers(plan, &flags.arguments).map_err(PreparationFailure::Incomplete)?;
    let link = link_task(plan, &providers);
    let lease = BuildLease::acquire(
        &plan.root,
        &plan.build,
        cancellation,
        Duration::from_secs(120),
    )
    .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?;
    let shadow = lease
        .prepare(&plan.build)
        .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?;
    directory(&shadow.join("objects")).map_err(PreparationFailure::Incomplete)?;
    directory(&shadow.join("bin")).map_err(PreparationFailure::Incomplete)?;
    for path in direct
        .objects
        .iter()
        .chain(std::iter::once(&direct.binary))
        .chain(std::iter::once(&plan.build.compilation_database))
    {
        output_file(&plan.root, path).map_err(PreparationFailure::Incomplete)?;
    }
    let mut records = vec![];
    let mut completed = 0usize;
    for chunk in providers.chunks(2) {
        let tasks = chunk
            .iter()
            .map(|provider| provider.task.clone())
            .collect::<Vec<_>>();
        let outcomes = run_tasks(plan, &tasks, scheduler, cancellation, events)?;
        let failed = outcomes
            .iter()
            .any(|outcome| !outcome.prerequisite_succeeded());
        completed += outcomes.len();
        records.extend(outcomes.iter().map(result_record));
        if failed {
            let outputs = outcomes
                .iter()
                .map(|outcome| {
                    let provider = chunk
                        .iter()
                        .find(|provider| provider.task.id == outcome.task_id)
                        .expect("declared direct compiler");
                    if outcome.prerequisite_succeeded() {
                        passed_source(provider, outcome)
                    } else {
                        failed_compiler(
                            plan,
                            "compile",
                            provider,
                            outcome,
                            &provider.context.targets[0],
                        )
                    }
                })
                .collect();
            let mut output = crate::cpp::merge("direct-build", "g++", outputs);
            attach_evidence(
                &mut output,
                json!({"phase":"compile","pkg_config":flags.records,"compiler_tasks":tasks,"outcomes":records,"unexecuted_sources":providers.len().saturating_sub(completed)}),
            );
            if completed < providers.len() {
                output.observation.limitations.push(format!(
                    "{} additional source compiles were not attempted after a failed compile phase",
                    providers.len() - completed
                ));
            }
            return Err(PreparationFailure::Observed {
                phase: "compile".into(),
                output: Box::new(output),
            });
        }
    }
    for path in &direct.objects {
        output_file(&plan.root, path).map_err(PreparationFailure::Incomplete)?;
        if !plan.root.join(path).is_file() {
            return Err(PreparationFailure::Incomplete(
                "compiler returned success without a declared object file".into(),
            ));
        }
    }
    let mut staged_link = link.clone();
    staged_link.depends_on.clear();
    let mut outcomes = run_tasks(
        plan,
        std::slice::from_ref(&staged_link),
        scheduler,
        cancellation,
        events,
    )?;
    let linked = outcomes.remove(0);
    records.push(result_record(&linked));
    if !linked.prerequisite_succeeded() {
        let mut provider = providers[0].clone();
        provider.task = staged_link;
        let mut output = failed_compiler(plan, "link", &provider, &linked, &direct.binary);
        attach_evidence(
            &mut output,
            json!({"phase":"link","pkg_config":flags.records,"compiler_tasks":providers.iter().map(|provider|&provider.task).collect::<Vec<_>>(),"link_task":link,"outcomes":records}),
        );
        return Err(PreparationFailure::Observed {
            phase: "link".into(),
            output: Box::new(output),
        });
    }
    output_file(&plan.root, &direct.binary).map_err(PreparationFailure::Incomplete)?;
    if super::super::compiled_artifact_kind(&plan.root.join(&direct.binary))
        .map_err(PreparationFailure::Incomplete)?
        != Some("executable")
    {
        return Err(PreparationFailure::Incomplete(
            "compiler returned success without a declared executable ELF artifact".into(),
        ));
    }
    let database=providers.iter().enumerate().map(|(index,provider)|json!({"directory":provider.task.cwd,"file":direct.sources[index].path,"arguments":provider.task.argv,"output":direct.objects[index]})).collect::<Vec<_>>();
    let bytes = serde_json::to_vec(&database)
        .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?;
    if bytes.len() > 32 * 1024 * 1024 {
        return Err(PreparationFailure::Incomplete(
            "actual direct compilation DB exceeds 32 MiB".into(),
        ));
    }
    storage::atomic_write(
        &plan.root.join(&plan.build.compilation_database),
        &bytes,
        WriteMode::ReplaceRegular,
    )
    .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?;
    let context = ici_cpp::load_compilation_context(
        &plan.root,
        &ContextRequest {
            database: Some(plan.build.compilation_database.clone()),
            variant: Some(plan.build.variant.clone()),
            selected_sources: plan.selected_sources.clone(),
            ..Default::default()
        },
    );
    if !context.complete() {
        return Err(PreparationFailure::Incomplete(format!(
            "actual direct compilation evidence is incomplete: {:?}",
            context.diagnostics
        )));
    }
    let evidence = json!({"system":"direct-gcc","pkg_config":flags.records,"limitations":flags.limitations,"compiler_tasks":providers.iter().map(|provider|&provider.task).collect::<Vec<_>>(),"link_task":link,"outcomes":records,"database":plan.build.compilation_database});
    Ok(PreparedContext {
        plan: plan.clone(),
        context,
        artifacts: vec![direct.binary.clone()],
        test_executable: None,
        evidence: Some(evidence),
        _lease: Arc::new(lease),
    })
}
