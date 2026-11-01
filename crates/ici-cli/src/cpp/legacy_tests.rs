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

/// A failed build of the test/coverage variant as the test check's verdict: one FAIL target at
/// the build descriptor with the adapter's failure message (`[C++] build` in the legacy view).
pub fn test_build_failure(
    id: &str,
    observed: &AnalysisOutput,
    legacy: bool,
) -> Option<AnalysisOutput> {
    let failed = observed
        .targets
        .iter()
        .find(|target| target.status == "FAIL" && target.target_name == "BuildAdapter")?;
    let mut output = AnalysisOutput::new(id, &observed.observation.provider);
    output.observation.state = TaskState::Succeeded;
    output.observation.limitations = observed.observation.limitations.clone();
    output
        .observation
        .limitations
        .push("the test build failed; no test was executed".into());
    output.status = "FAIL".into();
    output.summary = "C++ build failed before tests could run".into();
    let mut target = InspectionTarget::at(&failed.file_path, 1);
    target.target_name = if legacy {
        "[C++] build"
    } else {
        "C++ test build"
    }
    .into();
    target.status = "FAIL".into();
    target.message = failed.message.clone();
    let mut located = target.clone();
    located.message = failed.message.trim().into();
    add_finding(
        &mut output,
        &located,
        "ici.cpp.test-build.failed",
        &format!("cpp-test-build:{}:{}", failed.file_path, located.message),
    );
    if let Some(finding) = output.observation.findings.last_mut() {
        finding.category = "build".into();
    }
    output.targets.push(target);
    Some(output)
}

/// Frozen `TestEngine._resolve_test_source`: the first `tests/**/*.cpp` (sorted) whose stem is
/// the test name before `::`; otherwise the build descriptor.
fn legacy_test_source(root: &Path, name: &str, descriptor: &str) -> String {
    let stem = name.split("::").next().unwrap_or(name);
    let mut candidates: Vec<String> = crate::files::tree(root, &root.join("tests"))
        .unwrap_or_default()
        .into_iter()
        .filter(|entry| entry.file && entry.relative.ends_with(".cpp"))
        .map(|entry| entry.relative)
        .collect();
    candidates.sort();
    candidates
        .into_iter()
        .find(|path| Path::new(path).file_stem().and_then(|stem| stem.to_str()) == Some(stem))
        .unwrap_or_else(|| descriptor.to_owned())
}

/// The ctest JUnit message frozen reads for a terminal verdict (ctest 4.2.3 `--output-junit`:
/// Failed and WILL_FAIL "Failed", "Timeout", segmentation fault "SEGFAULT", a missing executable
/// "Unable to find executable", a DISABLED test `status="disabled"`).
fn junit_message(verdict: &str) -> String {
    match verdict {
        "Exception: SegFault" => "SEGFAULT".into(),
        "Not Run (Disabled)" => "ctest reported status 'disabled'".into(),
        "Not Run" => "Unable to find executable".into(),
        other => other.into(),
    }
}

/// The `SKIP_RETURN_CODE` property CMake recorded for one test in its generated CTestTestfile.
fn skip_return_code(testfile: &Path, name: &str) -> Option<u32> {
    let text = std::fs::read(testfile)
        .ok()
        .filter(|bytes| bytes.len() <= 4 * 1024 * 1024)?;
    let text = String::from_utf8_lossy(&text);
    let prefix = format!("set_tests_properties({name} PROPERTIES ");
    let line = text.lines().find(|line| line.starts_with(&prefix))?;
    let value = line.split_once("SKIP_RETURN_CODE \"")?.1.split_once('"')?.0;
    value.parse().ok()
}

/// Frozen `TestEngine._run_cpp_tests_via_adapter` targets for CTest cases: `[C++] <name>` at the
/// resolved test source, PASS "C++ Test Passed", FAIL "Execution Failed: <message>", and a case
/// that did not run SKIP "Execution skipped: <message>".
pub fn legacy_ctest_tests(output: &mut AnalysisOutput, root: &Path, descriptor: &str) {
    for target in &mut output.targets {
        if target.target_name.starts_with('[')
            || !target.file_path.ends_with("CTestTestfile.cmake")
            || !matches!(target.status.as_str(), "PASS" | "FAIL" | "SKIP")
        {
            continue;
        }
        let name = target.target_name.clone();
        if target.message == "Skipped"
            && let Some(code) = skip_return_code(&root.join(&target.file_path), &name)
        {
            // ctest's JUnit `<skipped message="SKIP_RETURN_CODE=<n>"/>`, from the same property.
            target.message = format!("SKIP_RETURN_CODE={code}");
        }
        target.file_path = legacy_test_source(root, &name, descriptor);
        target.start_line = 1;
        target.target_name = format!("[C++] {name}");
        target.message = match target.status.as_str() {
            "PASS" => "C++ Test Passed".into(),
            "FAIL" => format!("Execution Failed: {}", junit_message(&target.message)),
            _ => format!("Execution skipped: {}", junit_message(&target.message)),
        };
    }
}

/// Frozen SanitizeEngine/ThreadSanitizeEngine `_analysis_scopes`: without a selected C++
/// source (`.cpp/.cc/.cxx/.c`) or C++ test, and (for `sanitize`) without a Python source or
/// `tests/**/*.py`, the engine has no scope at all.
pub fn legacy_sanitizer_without_scope(root: &Path, component: &Component, check_id: &str) -> bool {
    let has = |extensions: &[&str]| {
        component.files.iter().any(|path| {
            Path::new(path)
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| extensions.contains(&extension))
        })
    };
    if has(&["cpp", "cc", "cxx", "c"]) {
        return false;
    }
    if check_id == "cpp.tsan" {
        return true;
    }
    let python_tests = root.join("tests").is_dir()
        && crate::files::tree(root, &root.join("tests"))
            .map(|entries| {
                entries
                    .iter()
                    .any(|entry| entry.file && entry.relative.ends_with(".py"))
            })
            .unwrap_or(true);
    !has(&["py"]) && !python_tests
}

/// The frozen no-scope verdict: one SKIP target at "." and NOT_APPLICABLE evidence.
pub fn legacy_sanitizer_no_scope(id: &str, check_id: &str) -> AnalysisOutput {
    let (name, message, summary) = if check_id == "cpp.tsan" {
        (
            "ThreadSanitizer",
            "No applicable C++ sources were selected; ThreadSanitizer was not run",
            "ThreadSanitizer skipped: no applicable C++ checks were executed",
        )
    } else {
        (
            "Sanitize",
            "No applicable Python or C++ sources were selected; sanitize was not run",
            "Sanitize skipped: no applicable checks were executed",
        )
    };
    let mut output = AnalysisOutput::new(id, "ici.cpp");
    output.status = "SKIP".into();
    output.summary = summary.into();
    output.observation.measurements.push(Measurement {
        name: "applicability".into(),
        value: 0.0,
        evidence: ici_domain::EvidenceLevel::NotApplicable,
        unit: "checks".into(),
        numerator: None,
        denominator: None,
    });
    output.observation.limitations.push(message.into());
    let mut target = InspectionTarget::at(".", 1);
    target.target_name = name.into();
    target.status = "SKIP".into();
    target.message = message.into();
    output.targets.push(target);
    output
}

/// Frozen SanitizeEngine/ThreadSanitizeEngine adapter (CMake/ctest) targets: a case without a
/// diagnostic is `[C++ <label>] <name>` at the descriptor ("Sanitizers reported no
/// diagnostics" / "Sanitizer run failed: <message>"), a case whose run printed a sanitizer report
/// is replaced by the located diagnostic targets, and the summary is the frozen clean or
/// "<n> <noun> Defect(s) Detected" text.
pub fn legacy_ctest_sanitizer(output: &mut AnalysisOutput, check_id: &str, descriptor: &str) {
    if !matches!(output.status.as_str(), "PASS" | "FAIL")
        || !output.observation.evidence_is_complete()
    {
        return;
    }
    let (label, summary, noun) = if check_id == "cpp.tsan" {
        (
            "TSan",
            "Thread Safety & TSan Clean (0 Defects)",
            "Thread Safety",
        )
    } else {
        (
            "ASan/UBSan",
            "Memory Safety & Sanitize Clean (0 Defects)",
            "Memory / Resource",
        )
    };
    let case = |target: &InspectionTarget| target.file_path.ends_with("CTestTestfile.cmake");
    let diagnostics = output.targets.iter().any(|target| !case(target));
    let mut reshaped = vec![];
    for target in &output.targets {
        if !case(target) {
            reshaped.push(target.clone());
            continue;
        }
        if target.target_name == "Instrumented suite" || (diagnostics && target.status == "FAIL") {
            continue;
        }
        let mut projected = InspectionTarget::at(descriptor, 1);
        projected.target_name = format!("[C++ {label}] {}", target.target_name);
        projected.status = target.status.clone();
        projected.message = match target.status.as_str() {
            "PASS" => "Sanitizers reported no diagnostics".into(),
            "FAIL" => format!("Sanitizer run failed: {}", junit_message(&target.message)),
            _ => format!(
                "Sanitizer test was not executed: {}",
                junit_message(&target.message)
            ),
        };
        reshaped.push(projected);
    }
    let issues = reshaped
        .iter()
        .filter(|target| matches!(target.status.as_str(), "WARN" | "FAIL"))
        .count();
    output.targets = reshaped;
    output.summary = if output.status == "PASS" {
        summary.into()
    } else {
        format!("{issues} {noun} Defect(s) Detected")
    };
}
