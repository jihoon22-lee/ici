//! A test driver is not itself the instrumented program. Bind every direct inventory executable.
use crate::{StepPrerequisite, ValidationError, ValidationKind, ValidationPlan, planning};
use ici_domain::{AnalysisOutput, TaskKind};
use ici_execution::TaskOutcome;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

pub(crate) fn plan(plan: &mut ValidationPlan) -> Result<(), ValidationError> {
    let request = &plan.context;
    let kind = request
        .options
        .sanitizer_suite_kind
        .expect("selected suite kind");
    if !matches!(kind, ValidationKind::Ctest | ValidationKind::Qtest) {
        return Err(ValidationError(
            "sanitizer suite kind must be CTest or QTest".into(),
        ));
    }
    if request.options.instrumented_artifacts.is_empty()
        || request.options.instrumented_artifacts.len() > 64
    {
        return Err(ValidationError(
            "sanitizer suite requires 1..64 declared instrumented artifacts".into(),
        ));
    }
    let mut unique = BTreeSet::new();
    for path in &request.options.instrumented_artifacts {
        ici_domain::validation::relative_path(path, false)
            .map_err(|error| ValidationError(error.to_string()))?;
        if !unique.insert(path) {
            return Err(ValidationError("duplicate instrumented artifact".into()));
        }
    }
    let readelf = request
        .options
        .auxiliary_tools
        .get("readelf")
        .ok_or_else(|| {
            ValidationError("sanitizer suite requires an explicit readelf identity".into())
        })?;
    let inventory_id = format!("{}.inventory", request.task_id);
    let mut tests = if kind == ValidationKind::Ctest {
        let directory = request
            .options
            .build_dir
            .as_ref()
            .filter(|directory| directory.is_absolute() && directory.starts_with(&request.root))
            .ok_or_else(|| {
                ValidationError(
                    "sanitizer CTest build directory must be declared inside the workspace".into(),
                )
            })?;
        let mut inventory = planning::task(
            request,
            "inventory",
            vec![
                "--test-dir".into(),
                directory.to_string_lossy().into_owned(),
                "--show-only=json-v1".into(),
            ],
        );
        inventory.kind = TaskKind::Probe;
        inventory.timeout_seconds = Some(30.0);
        inventory.output_limit_bytes = Some(8 * 1024 * 1024);
        plan.tasks.push(inventory);
        planning::task(
            request,
            "suite",
            vec![
                "--test-dir".into(),
                directory.to_string_lossy().into_owned(),
                "--output-on-failure".into(),
                "--parallel".into(),
                "2".into(),
            ],
        )
    } else {
        if request.options.instrumented_artifacts.len() != 1
            || request
                .root
                .join(&request.options.instrumented_artifacts[0])
                != request.tool.path
        {
            return Err(ValidationError(
                "QTest sanitizer must directly select its one declared instrumented executable"
                    .into(),
            ));
        }
        let args = if request.options.argv.is_empty() {
            vec![]
        } else {
            if Path::new(&request.options.argv[0]) != request.tool.path {
                return Err(ValidationError(
                    "QTest sanitizer argv must match selected executable identity".into(),
                ));
            }
            request.options.argv[1..].to_vec()
        };
        planning::task(request, "suite", args)
    };
    let mut prerequisites = vec![];
    if kind == ValidationKind::Ctest {
        prerequisites.push(requirement(&inventory_id));
    }
    for (index, path) in request.options.instrumented_artifacts.iter().enumerate() {
        let mut probe =
            planning::task(request, &format!("artifact{index}.instrumentation"), vec![]);
        probe.argv = vec![
            readelf.path.to_string_lossy().into_owned(),
            "--symbols".into(),
            "--wide".into(),
            request.root.join(path).to_string_lossy().into_owned(),
        ];
        probe.kind = TaskKind::Probe;
        probe.tool_digest = Some(readelf.digest.clone());
        probe.timeout_seconds = Some(30.0);
        probe.output_limit_bytes = Some(8 * 1024 * 1024);
        probe
            .input_refs
            .push(request.root.join(path).to_string_lossy().into_owned());
        if kind == ValidationKind::Ctest {
            plan.prerequisites
                .insert(probe.id.clone(), vec![requirement(&inventory_id)]);
        }
        prerequisites.push(requirement(&probe.id));
        plan.tasks.push(probe);
    }
    tests.kind = TaskKind::Test;
    tests.output_limit_bytes = Some(1024 * 1024);
    for (key, extra) in if request.kind == ValidationKind::ThreadSanitizer {
        vec![("TSAN_OPTIONS", "halt_on_error=0")]
    } else {
        vec![
            ("ASAN_OPTIONS", "detect_leaks=1"),
            ("UBSAN_OPTIONS", "halt_on_error=1"),
        ]
    } {
        let inherited = request
            .options
            .environment
            .get(key)
            .map(String::as_str)
            .unwrap_or("");
        tests.env_overlay.insert(
            key.into(),
            if inherited.is_empty() {
                extra.into()
            } else {
                format!("{inherited}:{extra}")
            },
        );
    }
    plan.prerequisites.insert(tests.id.clone(), prerequisites);
    plan.tasks.push(tests);
    plan.limitations.push("sanitizer runtime symbols are verified on declared test executables; this does not prove every translation unit or runtime dependency was instrumented".into());
    Ok(())
}
fn requirement(task: &str) -> StepPrerequisite {
    StepPrerequisite {
        task_id: task.into(),
        accepted_exits: vec![0],
        require_test_evidence: false,
    }
}
pub(crate) fn inventory(
    plan: &ValidationPlan,
    outcome: &TaskOutcome,
) -> Result<Value, ValidationError> {
    if outcome.task_id != format!("{}.inventory", plan.context.task_id)
        || !planning::completed(outcome)
        || outcome.exit_code != Some(0)
        || outcome.stdout.len() > 8 * 1024 * 1024
    {
        return Err(ValidationError(
            "sanitizer CTest inventory did not complete".into(),
        ));
    }
    let document: Value = serde_json::from_slice(&outcome.stdout)
        .map_err(|error| ValidationError(format!("invalid CTest inventory JSON: {error}")))?;
    if document["kind"] != "ctestInfo" || document["version"]["major"] != 1 {
        return Err(ValidationError(
            "unsupported CTest JSON inventory format".into(),
        ));
    }
    let tests = document["tests"]
        .as_array()
        .filter(|tests| !tests.is_empty() && tests.len() <= 2048)
        .ok_or_else(|| {
            ValidationError("sanitizer CTest inventory must contain 1..2048 tests".into())
        })?;
    let artifacts = plan
        .context
        .options
        .instrumented_artifacts
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut names = BTreeSet::new();
    let mut commands = vec![];
    for test in tests {
        let name = test["name"]
            .as_str()
            .filter(|name| !name.trim().is_empty() && name.len() <= 1024)
            .ok_or_else(|| {
                ValidationError("CTest inventory test name is missing or oversized".into())
            })?;
        if !names.insert(name) {
            return Err(ValidationError(
                "CTest inventory repeats a test name".into(),
            ));
        }
        if let Some(properties) = test.get("properties") {
            for property in properties
                .as_array()
                .ok_or_else(|| ValidationError("CTest properties are not a list".into()))?
            {
                if property["name"] == "DISABLED" {
                    let disabled = if let Some(value) = property["value"].as_bool() {
                        value
                    } else if let Some(value) = property["value"].as_str() {
                        match value.to_ascii_lowercase().as_str() {
                            "true" | "on" | "1" | "yes" => true,
                            "false" | "off" | "0" | "no" => false,
                            _ => {
                                return Err(ValidationError(
                                    "CTest disabled flag is not recognized".into(),
                                ));
                            }
                        }
                    } else {
                        return Err(ValidationError(
                            "CTest disabled flag has an unsupported type".into(),
                        ));
                    };
                    if disabled {
                        return Err(ValidationError(format!(
                            "sanitizer CTest inventory contains disabled test {name:?}"
                        )));
                    }
                }
            }
        }
        let command = test["command"]
            .as_array()
            .filter(|command| !command.is_empty() && command.len() <= 64)
            .ok_or_else(|| ValidationError("CTest test has no bounded direct command".into()))?;
        if command.iter().any(|word| {
            word.as_str()
                .is_none_or(|word| word.len() > 4096 || word.contains('\0'))
        }) {
            return Err(ValidationError(
                "CTest command contains an unsupported argument".into(),
            ));
        }
        let executable = command[0].as_str().expect("validated command");
        let base = plan
            .context
            .options
            .build_dir
            .as_deref()
            .unwrap_or(&plan.context.cwd);
        let absolute = if Path::new(executable).is_absolute() {
            Path::new(executable).to_path_buf()
        } else {
            base.join(executable)
        };
        if absolute
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
        {
            return Err(ValidationError(
                "CTest executable uses unproven parent traversal".into(),
            ));
        }
        let normalized = absolute.components().collect::<std::path::PathBuf>();
        let relative = planning::relative(&normalized.to_string_lossy(), &plan.context.root, base).map_err(|_|ValidationError("CTest inventory command is external; wrappers or external commands cannot prove sanitizer scope".into()))?;
        if !artifacts.contains(&relative) {
            return Err(ValidationError(format!(
                "CTest command {relative:?} is not one of the declared instrumented executables; wrappers or external commands cannot prove sanitizer scope"
            )));
        }
        commands.push(json!({"name":name,"executable":relative,"argv":command}));
    }
    Ok(json!({"kind":"ctest","tests":commands}))
}
pub(crate) fn proof(
    plan: &ValidationPlan,
    outcomes: &BTreeMap<String, TaskOutcome>,
) -> Result<Value, ValidationError> {
    if !(1..=64).contains(&plan.context.options.instrumented_artifacts.len()) {
        return Err(ValidationError(
            "sanitizer suite has no bounded instrumented artifact proof".into(),
        ));
    }
    let inventory = if plan.context.options.sanitizer_suite_kind == Some(ValidationKind::Ctest) {
        let id = format!("{}.inventory", plan.context.task_id);
        inventory(
            plan,
            outcomes.get(&id).ok_or_else(|| {
                ValidationError("sanitizer CTest inventory proof is absent".into())
            })?,
        )?
    } else {
        json!({"kind":"qtest","executable":plan.context.options.instrumented_artifacts[0]})
    };
    let mut artifacts = vec![];
    for (index, path) in plan
        .context
        .options
        .instrumented_artifacts
        .iter()
        .enumerate()
    {
        let id = format!("{}.artifact{index}.instrumentation", plan.context.task_id);
        let outcome = outcomes.get(&id).ok_or_else(|| {
            ValidationError(format!(
                "instrumentation proof is absent for artifact {index}"
            ))
        })?;
        if outcome.task_id != id {
            return Err(ValidationError(
                "instrumentation outcome identity mismatch".into(),
            ));
        }
        crate::cpp::instrumentation(plan, outcome)?;
        artifacts.push(json!({"path":path,"probe":id,"runtime_symbols_confirmed":true,"exit_code":outcome.exit_code}));
    }
    Ok(json!({"inventory":inventory,"instrumented_artifacts":artifacts}))
}
pub(crate) fn observe_framework(
    plan: &ValidationPlan,
    outcome: &TaskOutcome,
    output: &mut AnalysisOutput,
) -> Result<(), ValidationError> {
    let mut suite = plan.clone();
    let kind = plan
        .context
        .options
        .sanitizer_suite_kind
        .expect("selected suite kind");
    suite.kind = kind;
    suite.context.kind = kind;
    let outcomes = BTreeMap::from([(outcome.task_id.clone(), outcome.clone())]);
    let mut measured = AnalysisOutput::new(&plan.context.task_id, kind.provider());
    let result = match kind {
        ValidationKind::Ctest => crate::cpp::observe_ctest(&suite, &outcomes, &mut measured),
        ValidationKind::Qtest => crate::cpp::observe_qtest(&suite, &outcomes, &mut measured),
        _ => Err(ValidationError("unsupported sanitizer framework".into())),
    };
    output.extra["sanitizer_test_evidence"] = json!({"kind":kind,"complete":result.is_ok(),"targets":measured.targets,"findings":measured.observation.findings,"summary":measured.summary});
    output.targets.extend(measured.targets);
    output
        .observation
        .measurements
        .extend(measured.observation.measurements);
    if output.observation.findings.is_empty() {
        output
            .observation
            .findings
            .extend(measured.observation.findings);
    }
    result
}
