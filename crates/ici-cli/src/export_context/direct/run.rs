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
            &outcome.state_label(),
        );
    }
    Ok(outcomes)
}
/// Where a failed legacy release compile or link records what frozen `_compile_cpp` classifies.
pub(crate) const LEGACY_DIRECT_FAILURE: &str = "legacy_direct_failure";
pub(crate) const NO_DECLARED_OBJECT: &str =
    "compiler returned success without a declared object file";
/// How a legacy direct release refuses a regular output it cannot confirm as an ELF executable.
pub(crate) const UNVERIFIED_RELEASE_BINARY: &str = "g++ reported success but ";
pub(crate) const NO_DECLARED_EXECUTABLE: &str =
    "compiler returned success without a declared executable ELF artifact";
/// Frozen keeps `result.stderr[:200]` of a failed g++.
const FROZEN_MESSAGE_STDERR: usize = 200;

/// The exit-1 stderr of every failed compile, in source order, as one g++ would have printed it.
fn failure_stderr(failures: &[TaskOutcome]) -> String {
    failures
        .iter()
        .filter(|outcome| outcome.completed() && outcome.exit_code == Some(1))
        .map(|outcome| String::from_utf8_lossy(&outcome.stderr).replace('\0', "\u{fffd}"))
        .collect()
}

/// Decisions.md "직접 g++ 비-ELF 산출물": a legacy direct release's contract is an ELF executable,
/// so a compiler exit 0 is not enough. A missing or non-regular output stays frozen's refusal; a
/// regular file names what it is, and unsupported formats are not reported as corrupt files.
fn release_binary_verdict(root: &Path, path: &str) -> Result<(), PreparationFailure> {
    use std::io::Read;
    let artifact = root.join(path);
    if !fs::symlink_metadata(&artifact).is_ok_and(|metadata| metadata.is_file()) {
        return Err(PreparationFailure::Incomplete(
            NO_DECLARED_EXECUTABLE.into(),
        ));
    }
    let detail = match super::super::compiled_artifact_kind(&artifact) {
        Ok(Some("executable")) => return Ok(()),
        Ok(Some(kind)) => format!("is a {kind}, not an executable"),
        Ok(None) => {
            let mut magic = [0u8; 4];
            let elf = fs::File::open(&artifact)
                .and_then(|mut file| file.read_exact(&mut magic))
                .is_ok()
                && &magic == b"\x7fELF";
            format!(
                "is not a supported ELF executable: {}",
                if elf {
                    "its ELF type is not an executable image"
                } else {
                    "the file has no ELF header"
                }
            )
        }
        Err(reason) => format!("could not be verified as an ELF executable: {reason}"),
    };
    Err(PreparationFailure::Incomplete(format!(
        "{UNVERIFIED_RELEASE_BINARY}{path} {detail}"
    )))
}

/// Replaces `path` where it is a whole path token; a longer name it prefixes is left alone.
fn replace_path_token(text: &str, path: &str, placeholder: &str) -> String {
    let continues = |character: char| character.is_alphanumeric() || "._-/+~".contains(character);
    let mut output = String::with_capacity(text.len());
    let mut kept = 0;
    for (index, _) in text.match_indices(path) {
        let end = index + path.len();
        if text[..index].chars().next_back().is_some_and(continues)
            || text[end..].chars().next().is_some_and(continues)
        {
            continue;
        }
        output.push_str(&text[kept..index]);
        output.push_str(placeholder);
        kept = end;
    }
    output.push_str(&text[kept..]);
    output
}

/// The stderr the legacy message displays (decisions.md "직접 g++ 링크 실패 문구의 object 경로"):
/// each object this build owns is written as `<object>`, then frozen's cut applies, counted in
/// characters. Frozen's linker named a random temporary object there instead, and native's longer
/// paths would otherwise push the diagnostic past the cut. The tool's own stderr, the parser and
/// the native findings keep the real paths.
fn display_stderr(stderr: &str, objects: &[String]) -> String {
    let substituted = objects.iter().fold(stderr.to_owned(), |text, object| {
        replace_path_token(&text, object, "<object>")
    });
    substituted.chars().take(FROZEN_MESSAGE_STDERR).collect()
}

/// Frozen has one process, so one result: the first failure decides it.
fn failure_record(plan: &ContextPreparationPlan, failures: &[TaskOutcome]) -> Value {
    let direct = plan.direct.as_ref().expect("direct plan");
    let objects = direct
        .objects
        .iter()
        .map(|object| plan.root.join(object).display().to_string())
        .collect::<Vec<_>>();
    let stderr = display_stderr(&failure_stderr(failures), &objects);
    let first = failures.first();
    json!({
        "path": direct.flags.compiler.path,
        "exit_code": first.and_then(|outcome| outcome.exit_code),
        "signal": first.and_then(|outcome| outcome.signal),
        "timed_out": first.is_some_and(|outcome| outcome.timed_out),
        "truncated": first.is_some_and(|outcome| outcome.truncated),
        "stderr": stderr,
    })
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
/// A finished compiler's stderr, as a test's failure text.
fn bounded_stderr(outcome: &TaskOutcome) -> String {
    String::from_utf8_lossy(&outcome.stderr)
        .replace('\0', "\u{fffd}")
        .chars()
        .take(4096)
        .collect()
}
fn linked(binary: &DirectBinary, failures: &BTreeMap<String, String>) -> bool {
    binary
        .source
        .as_ref()
        .is_none_or(|source| !failures.contains_key(source))
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
    let links = (0..direct.binaries.len())
        .map(|index| link_task(plan, &providers, index))
        .collect::<Vec<_>>();
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
    // Reset only this leased variant's regular coverage counters before its new execution.
    for entry in crate::files::tree(&plan.root, &shadow)
        .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?
    {
        if entry.relative.ends_with(".gcda") {
            output_file(&plan.root, &entry.relative).map_err(PreparationFailure::Incomplete)?;
            fs::remove_file(plan.root.join(&entry.relative))
                .map_err(|error| PreparationFailure::Incomplete(error.to_string()))?;
        }
    }

    for path in direct
        .objects
        .iter()
        .chain(direct.binaries.iter().map(|binary| &binary.path))
        .chain(std::iter::once(&plan.build.compilation_database))
    {
        output_file(&plan.root, path).map_err(PreparationFailure::Incomplete)?;
    }
    let mut records = vec![];
    let mut completed = 0usize;
    // Frozen TestEngine compiles each `tests/**/*.cpp` on its own: a test whose own translation
    // unit does not compile is that test's failure, and the other tests still build and run.
    // Only test/coverage variants tolerate it, and only for a complete exit-1 compiler verdict
    // on a source that no other executable links.
    let tolerant = matches!(plan.build.variant.as_str(), "test" | "coverage");
    let mut failed_tests: BTreeMap<usize, String> = BTreeMap::new();
    // Frozen `_run_cpp_test_case`/`_compile_cpp_objects`: a test compile that did not finish
    // (timed out or past the output bound: "incomplete"; killed by a signal: "terminated") is
    // that test's ERROR, never a verdict; the other tests still build and run.
    let mut unfinished_tests: BTreeMap<usize, Value> = BTreeMap::new();
    // Frozen compiles a test in coverage mode only beside production sources it links
    // (`use_coverage = gcov_bin is not None and bool(src_files)`; gcov is checked by the
    // legacy projection, `direct_test_compile_failures`).
    let links_production = direct.binaries.iter().any(|binary| {
        binary
            .object_indices
            .iter()
            .any(|index| binary.source.as_deref() != Some(direct.sources[*index].path.as_str()))
    });
    for chunk in providers.chunks(2) {
        let tasks = chunk
            .iter()
            .map(|provider| provider.task.clone())
            .collect::<Vec<_>>();
        let outcomes = run_tasks(plan, &tasks, scheduler, cancellation, events)?;
        completed += outcomes.len();
        records.extend(outcomes.iter().map(result_record));
        let mut failed = false;
        for outcome in outcomes
            .iter()
            .filter(|outcome| !outcome.prerequisite_succeeded())
        {
            let index = providers
                .iter()
                .position(|provider| provider.task.id == outcome.task_id)
                .expect("declared direct compiler");
            let owners: Vec<_> = direct
                .binaries
                .iter()
                .filter(|binary| binary.object_indices.contains(&index))
                .collect();
            let test_only = tolerant
                && owners.len() == 1
                && owners[0].source.as_deref() == Some(direct.sources[index].path.as_str());
            let incomplete = outcome.timed_out || outcome.truncated;
            if test_only && outcome.completed() && outcome.exit_code == Some(1) {
                failed_tests.insert(index, bounded_stderr(outcome));
            } else if test_only && !outcome.cancelled && (incomplete || outcome.signal.is_some()) {
                failed_tests.insert(index, String::new());
                unfinished_tests.insert(
                    index,
                    json!({"incomplete": incomplete,
                        "coverage": plan.build.variant == "coverage" && links_production && direct.instrumented,
                        "path": plan.root.join(&direct.sources[index].path)}),
                );
            } else {
                failed = true;
            }
        }
        if failed {
            let mut outcomes = outcomes;
            if plan.build.variant == "release" {
                // Frozen `_compile_cpp` passes every source to one g++, which compiles the rest
                // after one fails: their diagnostics belong in the message and in the findings.
                for rest in providers[completed..].chunks(2) {
                    let tasks = rest
                        .iter()
                        .map(|provider| provider.task.clone())
                        .collect::<Vec<_>>();
                    let later = run_tasks(plan, &tasks, scheduler, cancellation, events)?;
                    completed += later.len();
                    records.extend(later.iter().map(result_record));
                    outcomes.extend(later);
                }
            }
            let outputs = outcomes
                .iter()
                .map(|outcome| {
                    let provider = providers
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
            if plan.build.variant == "release" {
                let failures = outcomes
                    .iter()
                    .filter(|outcome| !outcome.prerequisite_succeeded())
                    .cloned()
                    .collect::<Vec<_>>();
                output.extra[LEGACY_DIRECT_FAILURE] = failure_record(plan, &failures);
            }
            // The first complete exit-1 compiler stderr, for a test command that reports it per
            // test (frozen TestEngine compiles the library with every test).
            if let Some(stderr) = outcomes
                .iter()
                .find(|outcome| {
                    !outcome.prerequisite_succeeded()
                        && outcome.completed()
                        && outcome.exit_code == Some(1)
                })
                .map(|outcome| {
                    String::from_utf8_lossy(&outcome.stderr)
                        .replace('\0', "\u{fffd}")
                        .chars()
                        .take(4096)
                        .collect::<String>()
                })
            {
                output.extra["compile_failure_stderr"] = json!(stderr);
            }
            // How the first compile that gave no object ended, for a command that words each
            // ending differently (frozen `SanitizeEngine` per test).
            if let Some(outcome) = outcomes
                .iter()
                .find(|outcome| !outcome.prerequisite_succeeded() && !outcome.cancelled)
            {
                let stream = if outcome.stderr.is_empty() {
                    &outcome.stdout
                } else {
                    &outcome.stderr
                };
                output.extra["compile_failure_process"] = json!({
                    "exit_code": outcome.exit_code,
                    "signal": outcome.signal,
                    "timed_out": outcome.timed_out,
                    "truncated": outcome.truncated,
                    "coverage": plan.build.variant == "coverage" && direct.instrumented,
                    "output": String::from_utf8_lossy(&stream[..stream.len().min(4096)]),
                });
            }
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
    for (index, path) in direct.objects.iter().enumerate() {
        if failed_tests.contains_key(&index) {
            continue;
        }
        output_file(&plan.root, path).map_err(PreparationFailure::Incomplete)?;
        if !plan.root.join(path).is_file() {
            return Err(PreparationFailure::Incomplete(NO_DECLARED_OBJECT.into()));
        }
    }
    for (link, binary) in links.iter().zip(&direct.binaries) {
        if binary
            .object_indices
            .iter()
            .any(|index| failed_tests.contains_key(index))
        {
            continue;
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
            // Frozen `_run_cpp_test_case` (test.py:753-782): a test that does not link is that
            // test's failure, under the same tolerance as its compile; the production objects and
            // the other tests stay. A link that did not finish is that test's ERROR.
            let test = binary
                .source
                .as_deref()
                .filter(|_| tolerant)
                .and_then(|source| direct.sources.iter().position(|item| item.path == source));
            let incomplete = linked.timed_out || linked.truncated;
            if let Some(index) = test {
                if linked.completed() && linked.exit_code == Some(1) {
                    failed_tests.insert(index, bounded_stderr(&linked));
                    continue;
                }
                if !linked.cancelled && (incomplete || linked.signal.is_some()) {
                    failed_tests.insert(index, String::new());
                    // Frozen names a link by the test (`C++ test compilation ...`), also in
                    // coverage mode, unlike a coverage compile of one object.
                    unfinished_tests.insert(
                        index,
                        json!({"incomplete": incomplete, "coverage": false,
                            "path": plan.root.join(&direct.sources[index].path)}),
                    );
                    continue;
                }
            }
            let mut provider = providers[0].clone();
            provider.task = staged_link;
            let mut output = failed_compiler(plan, "link", &provider, &linked, &binary.path);
            if plan.build.variant == "release" {
                output.extra[LEGACY_DIRECT_FAILURE] =
                    failure_record(plan, std::slice::from_ref(&linked));
            }
            attach_evidence(
                &mut output,
                json!({"phase":"link","pkg_config":flags.records,"compiler_tasks":providers.iter().map(|provider|&provider.task).collect::<Vec<_>>(),"link_task":link,"outcomes":records}),
            );
            return Err(PreparationFailure::Observed {
                phase: "link".into(),
                output: Box::new(output),
            });
        }
        output_file(&plan.root, &binary.path).map_err(PreparationFailure::Incomplete)?;
        if plan.build.variant == "release" {
            release_binary_verdict(&plan.root, &binary.path)?;
        }
        if super::super::compiled_artifact_kind(&plan.root.join(&binary.path))
            .map_err(PreparationFailure::Incomplete)?
            != Some("executable")
        {
            return Err(PreparationFailure::Incomplete(
                NO_DECLARED_EXECUTABLE.into(),
            ));
        }
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
    let test_compile_failures: BTreeMap<String, String> = failed_tests
        .into_iter()
        .map(|(index, stderr)| (direct.sources[index].path.clone(), stderr))
        .collect();
    let test_compile_unfinished: BTreeMap<String, Value> = unfinished_tests
        .into_iter()
        .map(|(index, kind)| (direct.sources[index].path.clone(), kind))
        .collect();
    let evidence = json!({"system":"direct-gcc","pkg_config":flags.records,"limitations":flags.limitations,"compiler_tasks":providers.iter().map(|provider|&provider.task).collect::<Vec<_>>(),"link_tasks":links,"outcomes":records,"database":plan.build.compilation_database,"test_compile_failures":test_compile_failures.keys().collect::<Vec<_>>()});
    Ok(PreparedContext {
        plan: plan.clone(),
        context,
        non_elf: vec![],
        artifacts: direct
            .binaries
            .iter()
            .filter(|binary| linked(binary, &test_compile_failures))
            .map(|binary| binary.path.clone())
            .collect(),
        test_executables: direct
            .binaries
            .iter()
            .filter(|binary| linked(binary, &test_compile_failures))
            .filter_map(|binary| {
                binary
                    .source
                    .clone()
                    .map(|source| (source, binary.path.clone()))
            })
            .collect(),
        test_executable: None,
        test_compile_failures,
        test_compile_unfinished,
        evidence: Some(evidence),
        build_failure: None,
        build_failure_output: None,
        process_evidence: vec![],
        cmake_version: None,
        analysis: None,
        _lease: Arc::new(lease),
    })
}

#[cfg(test)]
mod tests {
    use super::display_stderr;

    const FIRST: &str = "/w/.ici/rust-v1/build/x/release/objects/aa.o";
    const SECOND: &str = "/w/.ici/rust-v1/build/x/release/objects/bb.o";

    fn objects() -> Vec<String> {
        vec![FIRST.to_owned(), SECOND.to_owned()]
    }

    #[test]
    fn only_whole_owned_object_tokens_are_displayed_as_the_placeholder() {
        let short = |text: &str| text.chars().take(200).collect::<String>();
        let replaced = format!("ld: {FIRST}: in function\nld: {SECOND}: in function\n");
        assert_eq!(
            display_stderr(&replaced, &objects()),
            "ld: <object>: in function\nld: <object>: in function\n"
        );
        for kept in [
            format!("{FIRST}.tmp: a longer name"),
            format!("{FIRST}x: a longer name"),
            format!("/x{FIRST}: under another directory"),
            format!("{FIRST}/d: a directory below"),
            "/tmp/ccAbCdEf.o: not an object this build owns".to_owned(),
        ] {
            assert_eq!(display_stderr(&kept, &objects()), short(&kept));
        }
    }

    #[test]
    fn the_cut_is_two_hundred_characters_after_the_substitution() {
        let filler = |count: usize| "y".repeat(count);
        // Longer than the cut before the substitution, exactly at it after.
        let at_the_cut = format!("{FIRST} {}", filler(200 - "<object> ".len()));
        assert!(at_the_cut.chars().count() > 200);
        let displayed = display_stderr(&at_the_cut, &objects());
        assert_eq!(displayed.chars().count(), 200);
        assert!(displayed.starts_with("<object> y") && displayed.ends_with('y'));
        // One character more is cut; two objects count once each after the substitution.
        assert_eq!(
            display_stderr(&format!("{at_the_cut}z"), &objects()),
            displayed
        );
        let two = format!(
            "{FIRST} {SECOND} {}",
            filler(200 - "<object> <object> ".len())
        );
        let displayed = display_stderr(&two, &objects());
        assert_eq!(displayed.chars().count(), 200);
        assert!(displayed.starts_with("<object> <object> y"));
    }

    #[test]
    fn the_cut_counts_characters_not_bytes_and_applies_without_objects() {
        let text = "\u{2018}".repeat(300);
        assert_eq!(display_stderr(&text, &[]), "\u{2018}".repeat(200));
        assert_eq!(display_stderr("short\n", &[]), "short\n");
    }
}
