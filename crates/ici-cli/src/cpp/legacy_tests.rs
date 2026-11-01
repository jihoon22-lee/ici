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
        target.message = format!("test executable did not compile: {}", detail.trim_end());
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
        if legacy {
            // Finding text is trimmed; the frozen row keeps the target's untrimmed message.
            output.extra["finding_details"] = json!({finding.fingerprint.clone():
                {"legacy_path": target.file_path, "legacy_message": target.message}});
        }
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

/// The native `ctest.failed` finding of a case whose target the legacy view relocated names the
/// relocated target (`legacy_path`/`legacy_message`), so it becomes that frozen target-adapted
/// row instead of an extra native row at the owned CTestTestfile.
fn map_case_findings(
    output: &mut AnalysisOutput,
    native_path: &str,
    name: &str,
    verdict: &str,
    target: &InspectionTarget,
) {
    let message = format!("test {name}: {verdict}");
    let fingerprints: Vec<String> = output
        .observation
        .findings
        .iter()
        .filter(|finding| {
            finding.native_rule_id == "ctest.failed"
                && finding.primary_location.path == native_path
                && finding.message == message
        })
        .map(|finding| finding.fingerprint.clone())
        .collect();
    if fingerprints.is_empty() {
        return;
    }
    if !output.extra["finding_details"].is_object() {
        output.extra["finding_details"] = json!({});
    }
    for fingerprint in fingerprints {
        let details = &mut output.extra["finding_details"][&fingerprint];
        if !details.is_object() {
            *details = json!({});
        }
        details["legacy_path"] = json!(target.file_path);
        details["legacy_message"] = json!(target.message);
    }
}

/// Frozen `TestEngine._run_cpp_tests_via_adapter` targets for CTest cases: `[C++] <name>` at the
/// resolved test source, PASS "C++ Test Passed", FAIL "Execution Failed: <message>", and a case
/// that did not run SKIP "Execution skipped: <message>".
pub fn legacy_ctest_tests(
    output: &mut AnalysisOutput,
    root: &Path,
    descriptor: &str,
    testfile: &Path,
) {
    // Every target of a CTest suite check is one case (native locates a case at a declared
    // source whose stem contains its name, else at the CTestTestfile).
    let mut relocated = vec![];
    for target in &mut output.targets {
        if target.target_name.starts_with('[')
            || !matches!(target.status.as_str(), "PASS" | "FAIL" | "SKIP")
        {
            continue;
        }
        let name = target.target_name.clone();
        let native = (target.file_path.clone(), target.message.clone());
        if target.message == "Skipped"
            && let Some(code) = skip_return_code(testfile, &name)
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
        relocated.push((native, name, target.clone()));
    }
    for ((path, verdict), name, target) in relocated {
        map_case_findings(output, &path, &name, &verdict, &target);
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

/// Frozen SanitizeEngine/ThreadSanitizeEngine adapter (CMake/ctest) targets in CTest case order:
/// a case without a diagnostic is `[C++ <label>] <name>` at the descriptor ("Sanitizers reported
/// no diagnostics" / "Sanitizer run failed: <message>"), a failed case whose output printed a
/// sanitizer report is replaced by its located diagnostic targets, and a case that was not
/// executed (DISABLED, SKIP_RETURN_CODE) is "Sanitizer test was not executed: <reason>" — an
/// ERROR scope under `required = true` (the default; engine ERROR with the first three reasons),
/// otherwise a SKIP scope (partial or skipped verdict, ESTIMATED evidence).
pub fn legacy_ctest_sanitizer(
    output: &mut AnalysisOutput,
    check_id: &str,
    descriptor: &str,
    testfile: &Path,
    required: bool,
) {
    if !matches!(output.status.as_str(), "PASS" | "FAIL")
        || !output.observation.evidence_is_complete()
    {
        return;
    }
    let (label, clean, partial, skipped_summary, noun) = if check_id == "cpp.tsan" {
        (
            "TSan",
            "Thread Safety & TSan Clean (0 Defects)",
            "ThreadSanitizer partially executed: one or more applicable scopes were skipped",
            "ThreadSanitizer skipped: no applicable C++ checks were executed",
            "Thread Safety",
        )
    } else {
        (
            "ASan/UBSan",
            "Memory Safety & Sanitize Clean (0 Defects)",
            "Sanitize partially executed: one or more applicable scopes were skipped",
            "Sanitize skipped: no applicable checks were executed",
            "Memory / Resource",
        )
    };
    let case = |target: &InspectionTarget| {
        !matches!(
            target.target_name.as_str(),
            "ASan/UBSan Error" | "TSan Error" | "LSan Error" | "SanitizerDiagnosticLocation"
        )
    };
    // Diagnostic targets keep the order of their diagnostics (an unlocated report is the
    // `SanitizerDiagnosticLocation` ERROR scope); each names the CTest case that printed it.
    // Ordered validation keeps each plan's extra under its task id.
    let diagnostics = output
        .extra
        .get("sanitizer_diagnostics")
        .or_else(|| output.extra[&output.observation.task_id].get("sanitizer_diagnostics"));
    let owners: Vec<Option<String>> = diagnostics
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| item["test_name"].as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    let mut owners = owners.into_iter();
    let mut unattributed = vec![];
    let mut attributed: BTreeMap<String, Vec<InspectionTarget>> = BTreeMap::new();
    for target in output.targets.iter().filter(|target| !case(target)) {
        match owners.next().flatten() {
            Some(name) => attributed.entry(name).or_default().push(target.clone()),
            None => unattributed.push(target.clone()),
        }
    }
    let cases: Vec<_> = output
        .targets
        .iter()
        .filter(|target| case(target) && target.target_name != "Instrumented suite")
        .cloned()
        .collect();
    // Diagnostics no case claims stay first, as before attribution.
    let mut errors: Vec<String> = unattributed
        .iter()
        .filter(|target| target.status == "ERROR")
        .map(|target| target.message.clone())
        .collect();
    errors.dedup();
    let mut reshaped = unattributed;
    let unclaimed = !reshaped.is_empty();
    let mut mapped = vec![];
    let (mut measured, mut skipped, mut failed) = (0usize, 0usize, false);
    for target in &cases {
        let name = target.target_name.clone();
        if target.status == "FAIL"
            && let Some(diagnostics) = attributed.remove(&name)
        {
            measured += 1;
            failed = true;
            for diagnostic in &diagnostics {
                // Frozen records an unlocated report once among the engine's tool errors.
                if diagnostic.status == "ERROR" && !errors.contains(&diagnostic.message) {
                    errors.push(diagnostic.message.clone());
                }
            }
            // The case's own ctest.failed finding is that frozen diagnostic row.
            mapped.push((target.clone(), diagnostics[0].clone()));
            reshaped.extend(diagnostics);
            continue;
        }
        if unclaimed && target.status == "FAIL" {
            // A report this projection could not attribute replaces the failed cases.
            measured += 1;
            failed = true;
            continue;
        }
        let mut projected = InspectionTarget::at(descriptor, 1);
        projected.target_name = format!("[C++ {label}] {name}");
        projected.status = target.status.clone();
        projected.message = match target.status.as_str() {
            "PASS" => {
                measured += 1;
                "Sanitizers reported no diagnostics".into()
            }
            "FAIL" => {
                measured += 1;
                failed = true;
                let message = format!("Sanitizer run failed: {}", junit_message(&target.message));
                let mut relocated = projected.clone();
                relocated.message = message.clone();
                mapped.push((target.clone(), relocated));
                message
            }
            _ => {
                skipped += 1;
                let reason = match skip_return_code(testfile, &name) {
                    Some(code) if target.message == "Skipped" => format!("SKIP_RETURN_CODE={code}"),
                    _ => junit_message(&target.message),
                };
                let message = format!("Sanitizer test was not executed: {reason}");
                projected.status = if required { "ERROR" } else { "SKIP" }.into();
                if required {
                    errors.push(message.clone());
                }
                message
            }
        };
        reshaped.push(projected);
    }
    // Attributed diagnostics whose case did not fail are still reported.
    for diagnostic in attributed.into_values().flatten() {
        if diagnostic.status == "ERROR" && !errors.contains(&diagnostic.message) {
            errors.push(diagnostic.message.clone());
        }
        reshaped.push(diagnostic);
    }
    for (native, relocated) in mapped {
        map_case_findings(
            output,
            &native.file_path,
            &native.target_name,
            &native.message,
            &relocated,
        );
    }
    let issues = reshaped
        .iter()
        .filter(|target| matches!(target.status.as_str(), "WARN" | "FAIL"))
        .count();
    output.targets = reshaped;
    let estimated = |output: &mut AnalysisOutput| {
        output.observation.measurements.push(Measurement {
            name: "legacy.sanitizer.skipped_scopes".into(),
            value: skipped as f64,
            evidence: ici_domain::EvidenceLevel::Estimated,
            unit: "scopes".into(),
            numerator: None,
            denominator: None,
        });
    };
    if skipped > 0 {
        output.observation.limitations.push(format!(
            "{skipped} CTest case(s) were not executed under the sanitizers"
        ));
    }
    if !errors.is_empty() {
        output.status = "ERROR".into();
        output.summary = errors
            .iter()
            .take(3)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ");
    } else if measured > 0 && skipped > 0 {
        if !failed {
            output.status = "WARN".into();
        }
        output.summary = partial.into();
        estimated(output);
    } else if measured > 0 {
        output.summary = if output.status == "PASS" {
            clean.into()
        } else {
            format!("{issues} {noun} Defect(s) Detected")
        };
    } else if skipped > 0 {
        output.status = "SKIP".into();
        output.summary = skipped_summary.into();
        estimated(output);
    }
}
