//! C++ test executables: per-test compile failures of descriptor-free builds, and the frozen
//! legacy `TestEngine` view of C++ test targets (`[C++] <name>` PASS/FAIL/SKIP/ERROR).
use super::*;

/// Test sources of a component's prepared direct builds whose own translation unit failed to
/// compile, with the bounded compiler stderr (`direct_test_compile_failures`).
pub fn direct_test_compile_failures(
    project: &ResolvedProject,
    component_id: &str,
) -> BTreeMap<String, String> {
    let Some(component) = project
        .components
        .iter()
        .find(|component| component.id == component_id)
    else {
        return BTreeMap::new();
    };
    let mut builds: BTreeSet<&str> = component.build.as_deref().into_iter().collect();
    if let Some(variants) = component
        .settings
        .get("native_build_variants")
        .and_then(Value::as_object)
    {
        builds.extend(
            variants
                .values()
                .filter_map(|variant| variant.get("build_id").and_then(Value::as_str)),
        );
    }
    project
        .builds
        .iter()
        .filter(|build| {
            builds.contains(build.id.as_str()) && build.system.as_deref() == Some("direct-gcc")
        })
        .filter_map(|build| {
            build
                .settings
                .get("direct_test_compile_failures")
                .and_then(Value::as_object)
        })
        .flatten()
        .filter_map(|(source, stderr)| Some((source.clone(), stderr.as_str()?.to_owned())))
        .collect()
}

/// One FAIL target and finding per test source that did not compile. The test counts as run
/// and failed, as the frozen engine counted it; nothing about its behaviour was observed.
pub fn append_test_compile_failures(
    output: &mut AnalysisOutput,
    failures: &BTreeMap<String, String>,
) {
    if failures.is_empty() {
        return;
    }
    for (source, stderr) in failures {
        let mut target = InspectionTarget::at(source, 1);
        target.target_name = source.clone();
        target.status = "FAIL".into();
        let detail: String = stderr.chars().take(2048).collect();
        target.message = format!("test source did not compile: {}", detail.trim_end());
        add_finding(
            output,
            &target,
            "ici.cpp.executable-test.compile-failed",
            &format!("cpp-test-compile:{source}"),
        );
        if let Some(finding) = output.observation.findings.last_mut() {
            finding.category = "test".into();
        }
        output.targets.push(target);
        output.observation.measurements.push(Measurement {
            name: "cpp-executable.cases".into(),
            value: 1.0,
            evidence: ici_domain::EvidenceLevel::Measured,
            unit: "cases".into(),
            numerator: Some(0),
            denominator: Some(1),
        });
    }
    let count = failures.len();
    // One physical suite per uncompiled test: counted as a run test that failed (0/1), so the
    // aggregate test count matches the sources the engine attempted.
    let task = output.observation.task_id.clone();
    let suites: Vec<_> = failures
        .keys()
        .map(|source| {
            json!({"canonical_suite_id":format!("{task}.compile:{source}.suite"),"complete":true,
                "framework":"cpp-executable","passed_tests":0,"skipped_tests":0,"total_tests":1})
        })
        .collect();
    output.extra[format!("{task}.compile")] = json!({
        "test_execution":{"canonical_suite_id":format!("{task}.compile.suite"),"complete":true,
            "framework":"cpp-executable","phase":"test","passed_tests":0,"skipped_tests":0,
            "total_tests":count,"pass_rate":0.0},
        "physical_test_suites":suites,"passed_tests":0,"skipped_tests":0,"total_tests":count,
        "test_outcomes":failures.keys().map(|source|(format!("cpp-executable:{source}"),"FAILED")).collect::<BTreeMap<_,_>>(),
    });
    let note = format!("{count} C++ test source(s) did not compile");
    output.summary = if output.summary.is_empty() {
        note
    } else {
        format!("{}; {note}", output.summary)
    };
    if output.status == "PASS" {
        output.status = "FAIL".into();
    }
}

/// The frozen output of a test command whose every test source failed to compile.
pub fn test_compile_failures_only(id: &str, failures: &BTreeMap<String, String>) -> AnalysisOutput {
    let mut output = AnalysisOutput::new(id, "cpp-executable");
    output.observation.state = TaskState::Succeeded;
    output.status = "FAIL".into();
    append_test_compile_failures(&mut output, failures);
    output
}

/// Frozen `TestEngine._run_cpp_test_case` targets for descriptor-free (direct g++) tests: one
/// `[C++] <file name>` target per `tests/**/*.cpp` at the source, line 1.
pub fn legacy_direct_tests(output: &mut AnalysisOutput, failures: &BTreeMap<String, String>) {
    let incomplete = output.observation.timed_out || output.observation.truncated;
    let runs: BTreeMap<String, Value> = output
        .extra
        .as_object()
        .into_iter()
        .flat_map(|parts| parts.values())
        .filter_map(|part| {
            let run = part.get("executable_test")?;
            Some((run["source"].as_str()?.to_owned(), run.clone()))
        })
        .collect();
    for target in &mut output.targets {
        if target.target_name != target.file_path {
            continue;
        }
        let name = Path::new(&target.file_path).file_name().map_or_else(
            || target.file_path.clone(),
            |name| name.to_string_lossy().into_owned(),
        );
        let message = if let Some(stderr) = failures.get(&target.file_path) {
            // Frozen `f"Compilation Error: {stderr[:200]}"`: the first 200 characters.
            format!(
                "Compilation Error: {}",
                stderr.chars().take(200).collect::<String>()
            )
        } else {
            match target.status.as_str() {
                "PASS" => "C++ Test Passed".into(),
                "FAIL" => {
                    let run = runs.get(&target.file_path);
                    let text = |key: &str| {
                        run.and_then(|run| run[key].as_str())
                            .unwrap_or_default()
                            .to_owned()
                    };
                    let stdout = text("stdout");
                    // Frozen `run_result.stdout or run_result.stderr`.
                    format!(
                        "Execution Failed: {}",
                        if stdout.is_empty() {
                            text("stderr")
                        } else {
                            stdout
                        }
                    )
                }
                "ERROR" if incomplete => "Execution output was incomplete".into(),
                "ERROR" => "Test process terminated before reporting results".into(),
                _ => continue,
            }
        };
        target.target_name = format!("[C++] {name}");
        target.message = message;
    }
}
